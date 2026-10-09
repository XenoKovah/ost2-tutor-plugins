#!/usr/bin/env python3
"""Email learners who are almost done with a class but have gone quiet.

A learner is nudged about a class when ALL of these hold:

  * their current grade in the class -- the number the learner dashboard shows as "Current
    grade: N%", read from grades_persistentcoursegrade.percent_grade -- is above --min-percent
    (default 90) and they have not passed yet;
  * their newest activity in the class (max courseware_studentmodule.modified) is more than
    --inactive-days (default 14) ago;
  * they are an enrolled, active, non-staff learner in a course that has started, has not ended
    and is visible to students, and they are not on that course's email opt-out list or on its
    course team;
  * they hold no certificate (downloadable/generating) and no certificate allowlist entry for it;
  * if the class has other runs (same display name), they have no certificate, no pass and no
    recent activity in any of them either.

The email is the "Li'l Stranger nudge": mascot image on top, a link to the class and a link to
its Progress page.  Each learner is nudged about a class at most once, ever (ledger below), and
at most once every --min-days-between days across classes.

Standard library only; run it from cron on the Tutor host (NOT inside a container).  It reads
the database through `docker exec <mysql container> mysql` (SELECT only, session set READ ONLY)
and sends through the box's own Tutor SMTP settings (SMTP_*, CONTACT_EMAIL), like
ost2_unenroll_report.py.

NOTHING IS SENT UNLESS YOU ASK:

  (no flag)            dry run: prints what it would do plus a preview of the first email.
  --only-to ADDR       TEST: composes the real emails for the selected learners but delivers
                       them to ADDR only (subject gets "[TEST] "); sends at most 1 unless
                       --max-send says otherwise; ignores and never writes the ledger.
  --send               LIVE: emails the learners.

    17 15 * * *  /usr/bin/python3 ~/ost2-host-scripts/ost2_completion_nudge.py --send \
                 >> ~/.local/share/ost2-completion-nudge/nudge.log 2>&1

Safety rails: --max-send caps one run (default 100) and --delay paces the messages, because all
OST2 mail shares one Gmail account with a ~2,000/day budget; the newest-and-closest learners go
first, so a big backlog drains over several days.  The ledger (<state-dir>/sent.jsonl, one JSON
line per accepted message, learner ids only) is what makes reruns and missed days harmless.  The
mascot image must be reachable (HEAD check) before anything is sent.  A rate-limit/quota reply
or an authentication/network failure stops the run, leaves <state-dir>/last_failure and exits 1;
the next run resumes where this one stopped.
"""
import argparse
import collections
import contextlib
import datetime as dt
import fcntl
import html
import json
import os
import re
import shutil
import smtplib
import ssl
import subprocess
import sys
import textwrap
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

DEFAULT_MIN_PERCENT = 90.0
DEFAULT_INACTIVE_DAYS = 14
DEFAULT_MAX_SEND = 100
DEFAULT_DELAY = 2.5
DEFAULT_MIN_DAYS_BETWEEN = 7
IMAGE_WIDTH = 300  # CSS pixels; the PNG itself is 2x for high-DPI screens
IMAGE_PATH = "/media/lil-stranger/hello.png"  # served by the LMS media volume, no restart needed
TUTOR_ROOT = os.path.expanduser("~/.local/share/tutor")
DEFAULT_STATE_DIR = os.path.expanduser("~/.local/share/ost2-completion-nudge")
DEFAULT_TUTOR = os.path.expanduser("~/tutor-venv/bin/tutor")
LEDGER = "sent.jsonl"
FAIL_MARKER = "last_failure"
LOCK = "lock"
MAX_CONSECUTIVE_REFUSALS = 5
COURSE_ID_RE = re.compile(r"^[A-Za-z0-9_.:+\-]+$")
QUOTA_RE = re.compile(
    r"5\.4\.5|4\.7\.|daily|quota|limit|too many|rate[- ]?limit|try again|temporar|throttl", re.I)


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def parse_db_time(value):
    """MySQL DATETIME text (UTC), with or without microseconds -> aware datetime."""
    return dt.datetime.fromisoformat(value.strip()).replace(tzinfo=dt.timezone.utc)


def mask_email(address):
    local, _, domain = address.partition("@")
    return "%s***@%s" % (local[:1], domain)


# --------------------------------------------------------------------------- selecting learners

# Everything the eligibility rules say, in one read-only query.  Notes:
#  * last_activity is a correlated subquery so only the few hundred near-complete (user, course)
#    pairs touch courseware_studentmodule (unique index leads with student_id).
#  * "same class" = same course display name, which is how OST2 tells its runs apart
#    (e.g. Arch2001 2021_v1 / 2024_v1, Dbg1102 2024_v1 / 2024_v2).
#  * Datetimes in Open edX are UTC, so UTC_TIMESTAMP() rather than NOW().
CANDIDATES_SQL = """
SELECT JSON_OBJECT(
         'user_id', t.user_id, 'email', t.email, 'course_id', t.course_id,
         'display_name', t.display_name, 'percent', t.percent_grade,
         'last_activity', t.last_activity)
FROM (
  SELECT g.user_id, u.email, g.course_id, c.display_name, g.percent_grade,
         (SELECT MAX(m.modified) FROM courseware_studentmodule m
           WHERE m.student_id = g.user_id AND m.course_id = g.course_id) AS last_activity
  FROM grades_persistentcoursegrade g
  JOIN auth_user u ON u.id = g.user_id
  JOIN student_courseenrollment e ON e.user_id = g.user_id AND e.course_id = g.course_id
  JOIN course_overviews_courseoverview c ON c.id = g.course_id
  WHERE g.percent_grade > {min_fraction}
    AND g.passed_timestamp IS NULL
    AND u.is_active = 1 AND u.is_staff = 0 AND u.is_superuser = 0
    AND u.email LIKE '%@%' AND u.email NOT LIKE '%@retired.invalid'
    AND e.is_active = 1
    AND (c.start IS NULL OR c.start <= UTC_TIMESTAMP())
    AND (c.end IS NULL OR c.end > UTC_TIMESTAMP())
    AND c.visible_to_staff_only = 0
    AND NOT EXISTS (SELECT 1 FROM certificates_generatedcertificate gc
                     WHERE gc.user_id = g.user_id AND gc.course_id = g.course_id
                       AND gc.status IN ('downloadable', 'generating'))
    AND NOT EXISTS (SELECT 1 FROM certificates_certificateallowlist al
                     WHERE al.user_id = g.user_id AND al.course_id = g.course_id AND al.allowlist = 1)
    AND NOT EXISTS (SELECT 1 FROM bulk_email_optout o
                     WHERE o.user_id = g.user_id AND o.course_id = g.course_id)
    AND NOT EXISTS (SELECT 1 FROM student_courseaccessrole r
                     WHERE r.user_id = g.user_id AND (r.course_id = g.course_id OR r.course_id = ''))
    AND NOT EXISTS (SELECT 1 FROM course_overviews_courseoverview c2
                      JOIN certificates_generatedcertificate gc2
                        ON gc2.course_id = c2.id AND gc2.user_id = g.user_id
                       AND gc2.status IN ('downloadable', 'generating')
                     WHERE c2.display_name = c.display_name AND c2.id <> c.id)
    AND NOT EXISTS (SELECT 1 FROM course_overviews_courseoverview c3
                      JOIN grades_persistentcoursegrade g3
                        ON g3.course_id = c3.id AND g3.user_id = g.user_id
                       AND g3.passed_timestamp IS NOT NULL
                     WHERE c3.display_name = c.display_name AND c3.id <> c.id)
    AND NOT EXISTS (SELECT 1 FROM course_overviews_courseoverview c4
                      JOIN courseware_studentmodule m4
                        ON m4.course_id = c4.id AND m4.student_id = g.user_id
                     WHERE c4.display_name = c.display_name AND c4.id <> c.id
                       AND m4.modified >= UTC_TIMESTAMP() - INTERVAL {inactive_days} DAY)
    {filters}
) t
WHERE t.last_activity IS NOT NULL
  AND t.last_activity < UTC_TIMESTAMP() - INTERVAL {inactive_days} DAY
ORDER BY t.percent_grade DESC, t.last_activity DESC, t.user_id
"""


def candidates_sql(min_percent, inactive_days, course_ids=(), user_ids=()):
    filters = []
    if course_ids:
        bad = [c for c in course_ids if not COURSE_ID_RE.match(c)]
        if bad:
            raise ValueError("unsafe course id(s): %r" % (bad,))
        filters.append("AND g.course_id IN (%s)" % ", ".join("'%s'" % c for c in course_ids))
    if user_ids:
        filters.append("AND g.user_id IN (%s)" % ", ".join(str(int(u)) for u in user_ids))
    return CANDIDATES_SQL.format(
        min_fraction=repr(float(min_percent) / 100.0),
        inactive_days=int(inactive_days),
        filters="\n    ".join(filters),
    )


def tutor_value(tutor, key):
    out = subprocess.run([tutor, "config", "printvalue", key], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("tutor config printvalue %s failed: %s" % (key, out.stderr.strip()))
    value = out.stdout.strip()
    return "" if value in ("None", "null") else value


def mysql_json_rows(args, password, sql):
    """Run read-only SQL in the mysql container; every output line is one JSON document."""
    prelude = "SET SESSION TRANSACTION READ ONLY;\nSET SESSION MAX_EXECUTION_TIME = 300000;\n"
    docker = shutil.which("docker") or "/usr/bin/docker"
    cmd = [docker, "exec", "-i", "-e", "MYSQL_PWD", args.mysql_container, "mysql",
           "-u" + args.mysql_user, "-N", "-B", "-r", "--default-character-set=utf8mb4", args.db]
    env = dict(os.environ, MYSQL_PWD=password)  # by name only, so it never shows up in `ps`
    out = subprocess.run(cmd, input=prelude + sql, capture_output=True, text=True,
                         encoding="utf-8", env=env, timeout=900)
    if out.returncode != 0:
        raise RuntimeError("mysql query failed (exit %d): %s" % (out.returncode, out.stderr.strip()))
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


Candidate = collections.namedtuple(
    "Candidate", "user_id email course_id class_name percent last_activity")


def class_key(name):
    return " ".join(name.split()).casefold()


def to_candidate(row):
    return Candidate(
        user_id=int(row["user_id"]),
        email=row["email"].strip(),
        course_id=row["course_id"],
        class_name=" ".join((row.get("display_name") or "").split()) or row["course_id"],
        percent=float(row["percent"]),
        last_activity=parse_db_time(row["last_activity"]),
    )


# --------------------------------------------------------------------------- ledger + plan


class Ledger:
    """Append-only record of every accepted/refused message: one JSON line each, ids only."""

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, LEDGER)

    def load(self):
        entries = []
        try:
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except ValueError:  # a torn last line must not stop the job
                        continue
                    if isinstance(entry, dict) and "user_id" in entry:
                        entries.append(entry)
        except FileNotFoundError:
            pass
        return entries

    def append(self, candidate, status, now):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        entry = {
            "ts": now.isoformat(), "user_id": candidate.user_id, "course_id": candidate.course_id,
            "class": candidate.class_name, "percent": round(candidate.percent, 4), "status": status,
        }
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


Plan = collections.namedtuple("Plan", "selected eligible stats")


def build_plan(candidates, ledger_entries, now, max_send, max_inactive_days=None,
               min_days_between=DEFAULT_MIN_DAYS_BETWEEN, use_ledger=True):
    """Pick who gets an email in this run.

    `candidates` arrive best-first (closest to done, then most recently active).  One learner
    can qualify in several runs of one class -> keep the best run.  Returns the capped selection,
    everyone eligible (the backlog), and counters for the log.
    """
    stats = collections.Counter(rows=len(candidates))
    if max_inactive_days is not None:
        cutoff = now - dt.timedelta(days=max_inactive_days)
        kept = [c for c in candidates if c.last_activity >= cutoff]
        stats["too_long_inactive"] = len(candidates) - len(kept)
        candidates = kept

    ordered = sorted(candidates, key=lambda c: (-c.percent, -c.last_activity.timestamp(), c.user_id))
    seen, unique = set(), []
    for c in ordered:
        key = (c.user_id, class_key(c.class_name))
        if key in seen:
            stats["other_run_same_class"] += 1
            continue
        seen.add(key)
        unique.append(c)

    nudged_courses, nudged_classes, last_nudge = set(), set(), {}
    if use_ledger:
        for entry in ledger_entries:
            if entry.get("status") not in ("sent", "refused"):
                continue
            nudged_courses.add((entry["user_id"], entry.get("course_id")))
            nudged_classes.add((entry["user_id"], class_key(entry.get("class") or "")))
            if entry.get("status") == "sent":
                try:
                    when = dt.datetime.fromisoformat(entry["ts"])
                except (KeyError, ValueError):
                    continue
                last_nudge[entry["user_id"]] = max(when, last_nudge.get(entry["user_id"], when))

    eligible, picked_users = [], set()
    for c in unique:
        if (c.user_id, c.course_id) in nudged_courses or \
                (c.user_id, class_key(c.class_name)) in nudged_classes:
            stats["already_nudged"] += 1
        elif c.user_id in last_nudge and now - last_nudge[c.user_id] < dt.timedelta(days=min_days_between):
            stats["learner_nudged_recently"] += 1
        elif c.user_id in picked_users:
            stats["another_class_goes_first"] += 1
        else:
            picked_users.add(c.user_id)
            eligible.append(c)
    stats["eligible"] = len(eligible)
    return Plan(eligible[:max_send], eligible, stats)


# --------------------------------------------------------------------------- the email

Link = collections.namedtuple("Link", "text url after", defaults=("",))

SUBJECT = "You're so close to finishing {class_name}!"


def paragraphs(class_name, home_url, progress_url):
    """The nudge, as paragraphs of plain strings and Links, so text and HTML can't drift apart."""
    return [
        ["Hi! We see that you're > 90% done with the class ", Link(class_name, home_url),
         "! That's pretty awesome! But we noticed you haven't been active in the class for more "
         "than 2 weeks."],
        ["While it's possible you just got busy and haven't finished the class, the most common "
         "reason for this is that students miss a couple of \"Mark as complete\" buttons while going "
         "through the class, and then the percentage is just not 100% complete. If that's you, then "
         "go check out your ", Link("Progress", progress_url, after=" page"),
         ", to see which units you missed, and to mark them as done."],
        ["We love to see students complete the classes and get their completion certificates (and "
         "new Accomplishment badges and points towards the leaderboard!), but we can't give you your "
         "well-deserved kudos until you confirm you're really done with the class."],
    ]


SIGNOFF = ("Thanks", "Li'l Stranger")


def render_text(class_name, home_url, progress_url):
    blocks = []
    for paragraph in paragraphs(class_name, home_url, progress_url):
        text = "".join(
            "%s%s <%s>" % (p.text, p.after, p.url) if isinstance(p, Link) else p for p in paragraph)
        blocks.append(textwrap.fill(text, 74, break_long_words=False, break_on_hyphens=False))
    blocks.append("\n".join(SIGNOFF))
    return "\n\n".join(blocks) + "\n"


def render_html(class_name, home_url, progress_url, image_url):
    esc = lambda s: html.escape(s, quote=False)  # noqa: E731 - keeps ' and " readable
    body = []
    for paragraph in paragraphs(class_name, home_url, progress_url):
        inner = "".join(
            '<a href="%s">%s</a>%s' % (html.escape(p.url, quote=True), esc(p.text), esc(p.after))
            if isinstance(p, Link) else esc(p) for p in paragraph)
        body.append('<p style="margin:0 0 16px 0;">%s</p>' % inner)
    body.append('<p style="margin:0;">%s<br>%s</p>' % tuple(esc(s) for s in SIGNOFF))
    image = (
        '<div style="text-align:center;margin:0 0 16px 0;">'
        '<img src="%s" width="%d" alt="Li\'l Stranger waving hello" '
        'style="display:block;margin:0 auto;width:%dpx;max-width:100%%;height:auto;border:0;">'
        "</div>" % (html.escape(image_url, quote=True), IMAGE_WIDTH, IMAGE_WIDTH))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        "<title>%s</title></head>\n"
        '<body style="margin:0;padding:0;">\n'
        '<div style="max-width:600px;margin:0 auto;padding:16px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        'font-size:16px;line-height:1.5;">\n%s\n%s\n</div>\n</body></html>\n'
        % (esc(SUBJECT.format(class_name=class_name)), image, "\n".join(body)))


def course_urls(mfe_base, course_id):
    key = urllib.parse.quote(course_id, safe=":+")
    base = "%s/learning/course/%s" % (mfe_base.rstrip("/"), key)
    return base + "/home", base + "/progress"


def build_message(cfg, candidate, recipient, test=False):
    home_url, progress_url = course_urls(cfg["mfe_base"], candidate.course_id)
    subject = SUBJECT.format(class_name=candidate.class_name)
    message = EmailMessage()
    message["From"] = formataddr((cfg["sender_name"], cfg["sender"]))
    message["To"] = recipient
    message["Reply-To"] = cfg["sender"]
    message["Subject"] = ("[TEST] " if test else "") + subject
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = make_msgid(domain=cfg["sender"].rpartition("@")[2] or None)
    message["Auto-Submitted"] = "auto-generated"
    message["X-OST2-Mailer"] = "completion-nudge"
    message.set_content(render_text(candidate.class_name, home_url, progress_url))
    message.add_alternative(
        render_html(candidate.class_name, home_url, progress_url, cfg["image_url"]), subtype="html")
    return message


# --------------------------------------------------------------------------- sending


class SendAbort(Exception):
    """Stop the whole run: rate limit / daily quota, bad credentials, network down."""


def classify_smtp_error(exc):
    """'refused' = this recipient only (permanent, 5xx); 'abort' = everything else."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        code, reply = next(iter(exc.recipients.values()))
    elif isinstance(exc, (smtplib.SMTPSenderRefused, smtplib.SMTPAuthenticationError,
                          smtplib.SMTPConnectError, smtplib.SMTPHeloError)):
        return "abort"
    elif isinstance(exc, smtplib.SMTPResponseException):
        code, reply = exc.smtp_code, exc.smtp_error
    else:
        return "abort"
    text = reply.decode("utf-8", "replace") if isinstance(reply, bytes) else str(reply)
    return "refused" if code >= 500 and not QUOTA_RE.search(text) else "abort"


class Mailer:
    def __init__(self, settings):
        self.settings = settings
        self.client = None

    def _connect(self):
        s = self.settings
        if s["ssl"]:
            client = smtplib.SMTP_SSL(s["host"], s["port"], timeout=60, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(s["host"], s["port"], timeout=60)
            if s["tls"]:
                client.starttls(context=ssl.create_default_context())
        if s["user"]:
            client.login(s["user"], s["password"])
        self.client = client

    def close(self):
        if self.client is not None:
            with contextlib.suppress(Exception):
                self.client.quit()
            self.client = None

    def send(self, message, recipient):
        for attempt in (1, 2):
            if self.client is None:
                self._connect()
            try:
                self.client.send_message(message, from_addr=self.settings["sender"], to_addrs=[recipient])
                return
            except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError, ssl.SSLError):
                self.client = None  # dead connection: reconnect once, then give up
                if attempt == 2:
                    raise


def smtp_settings(args, get):
    if args.smtp_host:  # local testing against a sink
        return {"host": args.smtp_host, "port": args.smtp_port or 25, "tls": False, "ssl": False,
                "user": "", "password": ""}
    return {
        "host": get("SMTP_HOST"),
        "port": int(get("SMTP_PORT") or 587),
        "tls": get("SMTP_USE_TLS").lower() == "true",
        "ssl": get("SMTP_USE_SSL").lower() == "true",
        "user": get("SMTP_USERNAME"),
        "password": get("SMTP_PASSWORD"),
    }


def check_image(url, timeout=20):
    """Never mail hundreds of learners a broken picture."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ost2-completion-nudge"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            kind = response.headers.get("Content-Type", "")
            if response.status != 200 or not kind.startswith("image/"):
                raise RuntimeError("mascot image check: %s -> HTTP %s, %r" % (url, response.status, kind))
    except urllib.error.URLError as exc:
        raise RuntimeError("mascot image %s is not reachable (%s); install hello.png first" % (url, exc))


# --------------------------------------------------------------------------- state


def record_failure(state_dir, text):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, FAIL_MARKER), "w") as handle:
        handle.write("%s\n%s\n" % (utcnow().isoformat(), text))


def clear_failure(state_dir):
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(state_dir, FAIL_MARKER))


@contextlib.contextmanager
def exclusive_lock(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, LOCK), "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("another ost2_completion_nudge run holds %s/%s" % (state_dir, LOCK))
        yield


# --------------------------------------------------------------------------- main


def load_config(args, mode):
    get = lambda key: tutor_value(args.tutor, key)  # noqa: E731
    lms_host = get("LMS_HOST")
    https = (get("ENABLE_HTTPS") or "true").lower() == "true"
    scheme = "https" if https else "http"
    try:
        mfe_host = get("MFE_HOST")
    except RuntimeError:  # not set on boxes that never customised it
        mfe_host = ""
    mfe_host = mfe_host or "apps." + lms_host
    cfg = {
        "lms_host": lms_host,
        "mfe_base": args.mfe_url or "%s://%s" % (scheme, mfe_host),
        "image_url": args.image_url or "%s://%s%s" % (scheme, lms_host, IMAGE_PATH),
        "sender": args.sender or get("CONTACT_EMAIL"),
        "sender_name": get("PLATFORM_NAME") or "OpenSecurityTraining2",
        "mysql_password": get("MYSQL_ROOT_PASSWORD"),
    }
    if mode != "dry-run":
        cfg["smtp"] = dict(smtp_settings(args, get), sender=cfg["sender"])
    return cfg


def describe(candidate, now):
    return "user_id=%d percent=%.1f%% inactive_days=%d course=%s" % (
        candidate.user_id, candidate.percent * 100, (now - candidate.last_activity).days,
        candidate.course_id)


def print_plan(plan, now, mode, limit=25):
    stats = plan.stats
    print("%s mode=%s rows=%d too_long_inactive=%d other_run_same_class=%d already_nudged=%d "
          "learner_nudged_recently=%d another_class_goes_first=%d eligible=%d planned=%d" % (
              now.isoformat(), mode, stats["rows"], stats["too_long_inactive"],
              stats["other_run_same_class"], stats["already_nudged"], stats["learner_nudged_recently"],
              stats["another_class_goes_first"], stats["eligible"], len(plan.selected)))
    if mode == "dry-run":
        for candidate in plan.selected[:limit]:
            print("  would nudge " + describe(candidate, now))
        if len(plan.selected) > limit:
            print("  ... and %d more" % (len(plan.selected) - limit))
        print("  (dry run: nothing sent, nothing saved; --send emails learners, --only-to ADDR tests)")


def save_eml(directory, message, index):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "nudge-%02d.eml" % index)
    with open(path, "wb") as handle:
        handle.write(bytes(message))
    return path


def run(args):
    mode = "test" if args.only_to else ("send" if args.send else "dry-run")
    if args.only_to and args.send:
        raise SystemExit("--only-to (test) and --send (live) are mutually exclusive")
    max_send = args.max_send if args.max_send is not None else (1 if mode == "test" else DEFAULT_MAX_SEND)
    now = utcnow()
    guard = exclusive_lock(args.state_dir) if mode != "dry-run" else contextlib.nullcontext()
    with guard:
        cfg = load_config(args, mode)
        sql = candidates_sql(args.min_percent, args.inactive_days, args.course_id, args.user_id)
        candidates = [to_candidate(row) for row in mysql_json_rows(args, cfg["mysql_password"], sql)]
        ledger = Ledger(args.state_dir)
        plan = build_plan(
            candidates, ledger.load() if mode != "test" else [], now, max_send,
            max_inactive_days=args.max_inactive_days, min_days_between=args.min_days_between,
            use_ledger=(mode != "test"))
        print_plan(plan, now, mode)

        if mode == "dry-run":
            if plan.selected:
                preview = build_message(cfg, plan.selected[0], "preview@example.invalid")
                print("\n--- preview of the first email (%s) ---\nSubject: %s\nImage: %s\n\n%s" % (
                    describe(plan.selected[0], now), preview["Subject"], cfg["image_url"],
                    preview.get_body(("plain",)).get_content()))
            return 0

        if not plan.selected:
            print("%s nothing to send" % now.isoformat())
            clear_failure(args.state_dir)
            return 0
        if not args.skip_image_check:
            check_image(cfg["image_url"])

        mailer = Mailer(cfg["smtp"])
        sent = refused = streak = 0
        try:
            for index, candidate in enumerate(plan.selected, 1):
                recipient = args.only_to or candidate.email
                message = build_message(cfg, candidate, recipient, test=(mode == "test"))
                if args.save_eml:
                    save_eml(args.save_eml, message, index)
                try:
                    mailer.send(message, recipient)
                except Exception as exc:  # noqa: BLE001 - classified below
                    if classify_smtp_error(exc) == "abort":
                        raise SendAbort("stopping after %d sent: %s: %s" % (sent, type(exc).__name__, exc)) from exc
                    refused += 1
                    streak += 1
                    if mode == "send":
                        ledger.append(candidate, "refused", utcnow())
                    print("%s refused %s (%s)" % (utcnow().isoformat(), describe(candidate, now), exc))
                    if streak >= MAX_CONSECUTIVE_REFUSALS:
                        raise SendAbort("%d recipients in a row were refused; something is wrong" % streak)
                    continue
                streak = 0
                sent += 1
                if mode == "send":
                    ledger.append(candidate, "sent", utcnow())
                print("%s %s %s%s" % (utcnow().isoformat(), "sent" if mode == "send" else "TEST-sent to " + args.only_to,
                                      describe(candidate, now), "" if mode == "send" else " (not recorded)"))
                if index < len(plan.selected):
                    time.sleep(args.delay)
        finally:
            mailer.close()
        clear_failure(args.state_dir)
        print("%s done: sent=%d refused=%d backlog_left=%d" % (
            utcnow().isoformat(), sent, refused, max(len(plan.eligible) - sent - refused, 0)))
        return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--send", action="store_true", help="LIVE: email the learners (default is a dry run)")
    ap.add_argument("--only-to", metavar="ADDR", help="TEST: deliver every selected email to ADDR only; "
                    "default max 1; the ledger is neither read nor written")
    ap.add_argument("--max-send", type=int, help="most emails in one run (default %d, 1 with --only-to)"
                    % DEFAULT_MAX_SEND)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="seconds between emails")
    ap.add_argument("--min-percent", type=float, default=DEFAULT_MIN_PERCENT,
                    help="grade must be ABOVE this percent (default %(default)s)")
    ap.add_argument("--inactive-days", type=int, default=DEFAULT_INACTIVE_DAYS,
                    help="no activity for MORE than this many days (default %(default)s)")
    ap.add_argument("--max-inactive-days", type=int, help="skip learners gone longer than this (default: no limit)")
    ap.add_argument("--min-days-between", type=int, default=DEFAULT_MIN_DAYS_BETWEEN,
                    help="days between nudges to one learner about different classes (default %(default)s)")
    ap.add_argument("--course-id", action="append", default=[], help="only this course (repeatable)")
    ap.add_argument("--user-id", action="append", type=int, default=[], help="only this user id (repeatable)")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--save-eml", metavar="DIR", help="also write each composed message here as .eml")
    ap.add_argument("--tutor", default=DEFAULT_TUTOR, help="tutor binary used to read the box's settings")
    ap.add_argument("--mysql-container", default="tutor_local-mysql-1")
    ap.add_argument("--mysql-user", default="root")
    ap.add_argument("--db", default="openedx")
    ap.add_argument("--mfe-url", help="override the MFE base, e.g. https://apps.p.ost2.fyi")
    ap.add_argument("--image-url", help="override the mascot PNG URL (default <LMS_HOST>%s)" % IMAGE_PATH)
    ap.add_argument("--skip-image-check", action="store_true", help="do not HEAD-check the mascot image first")
    ap.add_argument("--sender", help="From address (default: tutor CONTACT_EMAIL)")
    ap.add_argument("--smtp-host", help="override Tutor's SMTP settings (testing)")
    ap.add_argument("--smtp-port", type=int)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except Exception:  # noqa: BLE001 - cron has nobody watching; leave a marker and fail loudly
        if args.send or args.only_to:
            record_failure(args.state_dir, traceback.format_exc())
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
