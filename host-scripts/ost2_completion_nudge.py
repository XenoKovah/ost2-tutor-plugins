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

The email is the "Li'l Stranger nudge": the platform logo in the same light-gray header band as
normal course emails, then the mascot image, links to the class and its Progress page, and to this
server's Accomplishments and Leaderboard pages, then a course-email footer
(why they got it, and a one-click per-class "unsubscribe").
Each learner is nudged about a class at most once, ever (ledger below), and at most once every
--min-days-between days across classes.

HOW IT SENDS.  Learners are selected on the Tutor host (read-only SQL through
`docker exec <mysql container> mysql`), but the mail is NOT sent from here.  The host script hands
the rendered messages to a small delivery agent that runs inside the LMS container
(`manage.py lms shell`) and sends them through Django's configured EMAIL_BACKEND -- the same route
as every other system email (password resets, forum notifications, instructor bulk email).  On
OST2 boxes that backend is `RateLimitedEmailBackend` (plugin ost2_email_ratelimit), which paces ALL
server mail through one Redis budget: messages at least 2 s apart (30/min) and a hard daily cap,
because everything is relayed through one Google Workspace account with Gmail's own limits.
So this job automatically shares the global budget with whatever else the server is sending, and
the agent also:
  * yields: it stops once the server-wide count for today passes --yield-above (default 50%) of
    the daily cap, so bulk or transactional mail is never starved by nudges;
  * backs off and retries when the limiter says "next slot too far" (5/15/45/90 s) or Gmail
    answers 421/4xx (60 s, 5 min, 15 min), and stops the run for the day on the daily cap or a
    Gmail quota reply -- nothing is dropped or double-sent, the next run resumes;
  * keeps at least --delay seconds (default 2) between this job's own messages;
  * refuses to send at all if the box's mail route is a real SMTP backend WITHOUT the limiter, or
    if the limiter's Redis is unreachable (its fail-open mode would send unpaced).
The unsubscribe link is built by the platform itself (bulk_email.api.get_unsubscribed_link), so
clicking it writes the same bulk_email_optout row that the selection already honours.

NOTHING IS SENT UNLESS YOU ASK:

  (no flag)            dry run: counts, who would be nudged (learner ids), a pre-flight of the
                       mail route (backend, limiter settings, today's server-wide usage, an
                       unsubscribe-link check) and a preview of the first email.
  --only-to ADDR       TEST: composes the real email for the top candidate(s) and delivers it to
                       ADDR only (subject gets "[TEST] "); sends at most 1 unless --max-send says
                       otherwise; ignores and never writes the ledger.  The unsubscribe link
                       belongs to the account owning ADDR, or to --unsub-as USERNAME, never to the
                       learner whose data was used.
  --send               LIVE: emails the learners.

    17 15 * * *  /usr/bin/python3 ~/ost2-host-scripts/ost2_completion_nudge.py --send \
                 >> ~/.local/share/ost2-completion-nudge/nudge.log 2>&1

The dev box writes all of its mail to files (plugin ost2_dev_mail_to_files, so a copy of p's
learners can never be mailed from dev): there `--send` and `--only-to` write files instead of
delivering, and the ledger stays untouched.  To really deliver one test email from dev, add
`--mail-backend openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend` to an
`--only-to` run (the override is refused with --send).

Other rails: --max-send caps one run (default 100), closest-to-done and most-recently-active
learners go first so a big backlog drains over several days, and the mascot image must be
reachable (HEAD check, mascot and logo) before anything is sent.  The ledger (<state-dir>/sent.jsonl, one JSON
line per delivered message, learner ids only) is what makes reruns and missed days harmless.
Running out of budget (daily cap, yield, Gmail quota/throttle) is a normal early stop (exit 0);
authentication/network/route problems leave <state-dir>/last_failure and exit 1.
"""
import argparse
import base64
import collections
import contextlib
import datetime as dt
import fcntl
import html
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formataddr, formatdate, make_msgid

DEFAULT_MIN_PERCENT = 90.0
DEFAULT_INACTIVE_DAYS = 14
DEFAULT_MAX_SEND = 100
DEFAULT_DELAY = 2.0
DEFAULT_YIELD_ABOVE = 0.5
DEFAULT_MAX_RUNTIME = 1800
DEFAULT_MIN_DAYS_BETWEEN = 7
IMAGE_WIDTH = 300  # CSS pixels; the PNG itself is 2x for high-DPI screens
IMAGE_PATH = "/media/lil-stranger/hello.png"  # served by the LMS media volume, no restart needed
LOGO_PATH = "/theming/asset/images/logo.png"  # the logo course emails use (LMS default for emails)
LOGO_WIDTH, LOGO_HEIGHT = 162, 65  # the 250x100 logo at the same 65 px height course emails give it
ACCOMPLISHMENTS_PATH = "/gamma_dashboard/dashboard/"  # LMS routes from edx-gamma-dashboard
LEADERBOARD_PATH = "/gamma_dashboard/leaderboard/"
UNSUBSCRIBE_SENTINEL = "@@OST2-UNSUBSCRIBE-URL@@"  # swapped for the per-recipient link by the agent
TUTOR_ROOT = os.path.expanduser("~/.local/share/tutor")
DEFAULT_STATE_DIR = os.path.expanduser("~/.local/share/ost2-completion-nudge")
DEFAULT_TUTOR = os.path.expanduser("~/tutor-venv/bin/tutor")
LEDGER = "sent.jsonl"
FAIL_MARKER = "last_failure"
LOCK = "lock"
COURSE_ID_RE = re.compile(r"^[A-Za-z0-9_.:+\-]+$")
# Why the agent stopped early.  These are the budget doing its job (exit 0, the rest resumes at the
# next run); any other reason is a problem somebody should look at (exit 1).
EXPECTED_STOPS = {"daily_cap", "yield_budget", "rate_defer", "gmail_throttle", "gmail_quota", "time_limit"}


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
         'user_id', t.user_id, 'username', t.username, 'email', t.email, 'course_id', t.course_id,
         'display_name', t.display_name, 'percent', t.percent_grade,
         'last_activity', t.last_activity)
FROM (
  SELECT g.user_id, u.username, u.email, g.course_id, c.display_name, g.percent_grade,
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
    "Candidate", "user_id username email course_id class_name percent last_activity")


def class_key(name):
    return " ".join(name.split()).casefold()


def to_candidate(row):
    return Candidate(
        user_id=int(row["user_id"]),
        username=row["username"],
        email=row["email"].strip(),
        course_id=row["course_id"],
        class_name=" ".join((row.get("display_name") or "").split()) or row["course_id"],
        percent=float(row["percent"]),
        last_activity=parse_db_time(row["last_activity"]),
    )


# --------------------------------------------------------------------------- ledger + plan


class Ledger:
    """Append-only record of every delivered/refused message: one JSON line each, ids only."""

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


def paragraphs(class_name, urls):
    """The nudge, as paragraphs of plain strings and Links, so text and HTML can't drift apart."""
    return [
        ["Hi! We see that you're > 90% done with the class ", Link(class_name, urls["home"]),
         "! That's pretty awesome! But we noticed you haven't been active in the class for more "
         "than 2 weeks."],
        ["While it's possible you just got busy and haven't finished the class, the most common "
         "reason for this is that students miss a couple of \"Mark as complete\" buttons while going "
         "through the class, and then the percentage is just not 100% complete. If that's you, then "
         "go check out your ", Link("Progress", urls["progress"], after=" page"),
         ", to see which units you missed, and to mark them as done."],
        ["We love to see students complete the classes and get their completion certificates (and "
         "new ", Link("Accomplishment", urls["accomplishments"]), " badges and points towards the ",
         Link("Leaderboard", urls["leaderboard"]), "!), but we can't give you your well-deserved "
         "kudos until you confirm you're really done with the class."],
    ]


SIGNOFF = ("Thanks", "Li'l Stranger")


def footer_lines(platform_name, recipient, class_name, urls):
    """The course-email footer: the platform's wording minus its "course email settings" link.

    Stock course emails say "update your course email settings here" and link to /dashboard, but
    the only such setting there is an "Email settings" item inside each class card's menu, which
    nobody finds from a link.  The unsubscribe page does the same per-class opt-out directly, needs
    no login, and says up front that other classes' emails are unaffected.
    """
    return [
        "This email was automatically sent from %s." % platform_name,
        "You are receiving this email at address %s because you are enrolled in %s" % (recipient, class_name),
        "(URL: %s)." % urls["home"],
        "To stop receiving email like this about this class, unsubscribe here: %s" % urls["unsubscribe"],
    ]


def render_text(class_name, urls, platform_name, recipient):
    blocks = []
    for paragraph in paragraphs(class_name, urls):
        text = "".join(
            "%s%s <%s>" % (p.text, p.after, p.url) if isinstance(p, Link) else p for p in paragraph)
        blocks.append(textwrap.fill(text, 74, break_long_words=False, break_on_hyphens=False))
    blocks.append("\n".join(SIGNOFF))
    blocks.append("----\n" + "\n".join(footer_lines(platform_name, recipient, class_name, urls)))
    return "\n\n".join(blocks) + "\n"


def render_html(class_name, urls, image_url, platform_name, recipient):
    esc = lambda s: html.escape(s, quote=False)  # noqa: E731 - keeps ' and " readable
    link = lambda url, text: '<a href="%s">%s</a>' % (html.escape(url, quote=True), esc(text))  # noqa: E731
    body = []
    for paragraph in paragraphs(class_name, urls):
        inner = "".join(link(p.url, p.text) + esc(p.after) if isinstance(p, Link) else esc(p)
                        for p in paragraph)
        body.append('<p style="margin:0 0 16px 0;">%s</p>' % inner)
    body.append('<p style="margin:0;">%s<br>%s</p>' % tuple(esc(s) for s in SIGNOFF))
    body.append(
        '<p style="margin:24px 0 0 0;font-size:12px;line-height:1.5;color:#6b7280;">'
        "%s<br>\n"
        "You are receiving this email at address %s because you are enrolled in %s.<br>\n"
        "To stop receiving email like this about this class, %s.</p>" % (
            esc("This email was automatically sent from %s." % platform_name), esc(recipient),
            link(urls["home"], class_name), link(urls["unsubscribe"], "unsubscribe here")))
    image = (
        '<div style="text-align:center;margin:0 0 16px 0;">'
        '<img src="%s" width="%d" alt="Li\'l Stranger waving hello" '
        'style="display:block;margin:0 auto;width:%dpx;max-width:100%%;height:auto;border:0;">'
        "</div>" % (html.escape(image_url, quote=True), IMAGE_WIDTH, IMAGE_WIDTH))
    # the header band normal course emails have (ace_common base_body.html): logo on #f5f5f5, linked home
    header = (
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" '
        'bgcolor="#f5f5f5" style="background-color:#f5f5f5;"><tr><td align="left" style="padding:10px 20px;">'
        '<a href="%s"><img src="%s" width="%d" height="%d" alt="%s" '
        'style="display:block;border:0;width:%dpx;height:%dpx;max-height:%dpx;"></a></td></tr></table>' % (
            html.escape(urls["site"], quote=True), html.escape(urls["logo"], quote=True), LOGO_WIDTH, LOGO_HEIGHT,
            html.escape("Go to %s Home Page" % platform_name, quote=True), LOGO_WIDTH, LOGO_HEIGHT, LOGO_HEIGHT))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        "<title>%s</title></head>\n"
        '<body style="margin:0;padding:0;">\n'
        '<div style="max-width:600px;margin:0 auto;">\n%s\n'
        '<div style="padding:16px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        'font-size:16px;line-height:1.5;">\n%s\n%s\n</div>\n</div>\n</body></html>\n'
        % (esc(SUBJECT.format(class_name=class_name)), header, image, "\n".join(body)))


def course_urls(mfe_base, course_id):
    key = urllib.parse.quote(course_id, safe=":+")
    base = "%s/learning/course/%s" % (mfe_base.rstrip("/"), key)
    return base + "/home", base + "/progress"


def email_urls(cfg, course_id):
    """Every link in the email, built from THIS box's hosts so p's email points at p.

    The unsubscribe link is per recipient and per course, so it is a placeholder here: the
    delivery agent swaps in the platform's own link (an encrypted-username token).
    """
    home, progress = course_urls(cfg["mfe_base"], course_id)
    lms = cfg["lms_base"].rstrip("/")
    return {"home": home, "progress": progress, "accomplishments": lms + ACCOMPLISHMENTS_PATH,
            "leaderboard": lms + LEADERBOARD_PATH, "site": lms + "/", "logo": cfg["logo_url"],
            "unsubscribe": UNSUBSCRIBE_SENTINEL}


def render_message(cfg, candidate, recipient, test=False):
    """Everything the delivery agent needs for one email (the agent builds the Django message)."""
    urls = email_urls(cfg, candidate.course_id)
    domain = cfg["sender"].rpartition("@")[2] or None
    return {
        "from": formataddr((cfg["sender_name"], cfg["sender"])),
        "to": recipient,
        "reply_to": cfg["sender"],
        "subject": ("[TEST] " if test else "") + SUBJECT.format(class_name=candidate.class_name),
        "text": render_text(candidate.class_name, urls, cfg["sender_name"], recipient),
        "html": render_html(candidate.class_name, urls, cfg["image_url"], cfg["sender_name"], recipient),
        "headers": {
            "Date": formatdate(usegmt=True),
            "Message-ID": make_msgid(domain=domain),
            "Auto-Submitted": "auto-generated",
            "X-OST2-Mailer": "completion-nudge",
        },
    }


# --------------------------------------------------------------------------- the delivery agent

# Runs INSIDE the LMS container (`manage.py lms shell`, production settings), so it can use Django's
# configured mail route and the platform's own unsubscribe-link helper.  It is shipped to the
# container on stdin as source text (nothing is installed there) and talks back with one
# "NUDGE_<TAG> <json>" line per event; every other line is Django start-up noise and is ignored.
# The source needs no Django at import time, so the tests exec it with stand-ins.
AGENT_SOURCE = r'''
import base64
import json
import re
import smtplib
import ssl
import time

NON_DELIVERING = (
    "django.core.mail.backends.filebased", "django.core.mail.backends.locmem",
    "django.core.mail.backends.console", "django.core.mail.backends.dummy",
)
SLOT_BACKOFF = (5, 15, 45, 90)    # seconds; the limiter said the next send slot is too far away
GMAIL_BACKOFF = (60, 300, 900)    # seconds; Gmail answered 421/4xx (burst throttle)
MAX_CONSECUTIVE_REFUSALS = 5
QUOTA_RE = re.compile(r"5\.4\.5|daily|quota|sending limit", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class Stop(Exception):
    """End the run early; `reason` is one of the short codes the host script understands."""

    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def emit(tag, **fields):
    try:
        print("NUDGE_%s %s" % (tag, json.dumps(fields, ensure_ascii=False)), flush=True)
    except (BrokenPipeError, OSError):
        raise Stop("host_gone")  # nobody is recording results any more: send nothing else


def short(exc):
    """One line for the log, with e-mail addresses scrubbed (recipient errors quote them)."""
    return EMAIL_RE.sub("<email>", ("%s: %s" % (type(exc).__name__, exc)).replace("\n", " "))[:300]


def reply_text(reply):
    return reply.decode("utf-8", "replace") if isinstance(reply, (bytes, bytearray)) else str(reply)


def classify(exc):
    """What an SMTP/limiter error means for the run.

    smtplib reports the SAME Gmail reply under different exception types depending on which SMTP
    command it answered (421 at MAIL FROM is SMTPSenderRefused, at RCPT SMTPRecipientsRefused, at
    DATA SMTPDataError), so the code and text decide, not the type -- except for the types that
    can only mean our own side is rejected.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "fatal"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        code, reply = next(iter(exc.recipients.values()))
    elif isinstance(exc, smtplib.SMTPResponseException):
        code, reply = exc.smtp_code, exc.smtp_error
    elif isinstance(exc, (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError, ssl.SSLError)):
        return "disconnected"
    else:
        return "fatal"
    text = reply_text(reply).lower()
    if code == 451 and "ost2 sending budget" in text:  # RateLimitedEmailBackend deferring
        return "limiter_daily" if "daily cap" in text else "limiter_slot"
    if code >= 500 and QUOTA_RE.search(text):
        return "gmail_quota"
    if 400 <= code < 500:
        return "gmail_throttle"
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError)):
        return "refused"  # this message/recipient only (repeated refusals still stop the run)
    return "fatal"  # a 5xx for the connection, HELO or sender: our side is being rejected


def describe_route(conn):
    cls = type(conn)
    limited = all(hasattr(conn, name) for name in ("daily_cap", "rate_per_min", "key_prefix")) \
        and hasattr(cls, "redis")
    if limited:
        kind = "limited"
    elif cls.__module__ in NON_DELIVERING:
        kind = "files"        # mail never leaves the box (dev's ost2_dev_mail_to_files)
    else:
        kind = "unlimited"    # a real SMTP backend with no shared budget: never use it
    info = {"kind": kind, "backend": "%s.%s" % (cls.__module__, cls.__name__)}
    if limited:
        info.update(rate_per_min=conn.rate_per_min, daily_cap=conn.daily_cap,
                    max_block=conn.max_block, fail_open=conn.fail_open)
    return info


def usage(conn, clock):
    """Messages the whole server has sent today, from the limiter's own Redis counter."""
    day = int(clock() // 86400)
    return int(conn.redis.get("%s:daily:%d" % (conn.key_prefix, day)) or 0)


def mask(url):
    return re.sub(r"/optout/[^/]+/", "/optout/<token>/", url)


def real_deps():
    from django.contrib.auth import get_user_model
    from django.core.mail import EmailMultiAlternatives, get_connection
    from lms.djangoapps.bulk_email.api import get_unsubscribed_link

    user_model = get_user_model()

    class Deps:
        get_connection = staticmethod(lambda backend: get_connection(backend=backend))
        user_exists = staticmethod(lambda username: user_model.objects.filter(username=username).exists())
        username_for_email = staticmethod(lambda email: user_model.objects.filter(
            email__iexact=email).values_list("username", flat=True).first())
        unsubscribe_link = staticmethod(get_unsubscribed_link)

        @staticmethod
        def new_message(subject, text, html, from_email, to, reply_to, headers, connection):
            message = EmailMultiAlternatives(
                subject=subject, body=text, from_email=from_email, to=[to],
                reply_to=[reply_to] if reply_to else None, headers=headers, connection=connection)
            message.attach_alternative(html, "text/html")
            return message

    return Deps


def nudge_agent(payload, deps=None, sleep=time.sleep, clock=time.time):
    deps = deps or real_deps()
    opts = payload["options"]
    items = payload.get("items", [])
    sentinel = opts["sentinel"]
    started = clock()
    stats = {"sent": 0, "refused": 0, "skipped": 0}
    stopped = None
    route = {}
    conn = None

    def username_of(item):
        if item.get("username"):
            return item["username"]
        if item.get("lookup_email"):
            return deps.username_for_email(item["lookup_email"])
        return None

    def reconnect():
        try:
            conn.close()
        except Exception:  # a dead connection may not close cleanly; opening a fresh one is the point
            pass
        conn.open()

    def pause(seconds):
        if clock() - started + seconds > opts["max_runtime"]:
            raise Stop("time_limit")
        sleep(seconds)

    def build(item, link):
        return deps.new_message(
            subject=item["subject"], text=item["text"].replace(sentinel, link),
            html=item["html"].replace(sentinel, link), from_email=item["from"], to=item["to"],
            reply_to=item.get("reply_to"), headers=item.get("headers") or {}, connection=conn)

    def send_one(message):
        slot_tries = gmail_tries = dropped = 0
        while True:
            try:
                count = conn.send_messages([message])
            except Exception as exc:  # classified below; anything unknown stops the run
                kind = classify(exc)
                if kind == "refused":
                    return "refused", short(exc)
                if kind == "limiter_daily":
                    raise Stop("daily_cap", short(exc))
                if kind == "gmail_quota":
                    raise Stop("gmail_quota", short(exc))
                if kind == "limiter_slot":
                    if slot_tries >= len(SLOT_BACKOFF):
                        raise Stop("rate_defer", short(exc))
                    wait, slot_tries = SLOT_BACKOFF[slot_tries], slot_tries + 1
                    emit("BACKOFF", why="the server's mail budget has no slot free", seconds=wait)
                    pause(wait)
                    continue
                if kind == "gmail_throttle":
                    if gmail_tries >= len(GMAIL_BACKOFF):
                        raise Stop("gmail_throttle", short(exc))
                    wait, gmail_tries = GMAIL_BACKOFF[gmail_tries], gmail_tries + 1
                    emit("BACKOFF", why="Gmail asked us to slow down", seconds=wait)
                    reconnect()
                    pause(wait)
                    continue
                if kind == "disconnected" and dropped < 2:
                    dropped += 1
                    reconnect()
                    pause(2)
                    continue
                raise Stop("error", short(exc))
            return ("sent", "") if count == 1 else ("refused", "the mail backend accepted 0 messages")

    try:
        conn = deps.get_connection(opts.get("backend") or None)
        route = describe_route(conn)
        if route["kind"] == "limited":
            try:
                route["used_today"] = usage(conn, clock)
            except Exception as exc:
                raise Stop("limiter_unreachable", short(exc))
        emit("ROUTE", **route)
        if route["kind"] == "unlimited":
            raise Stop("route_unlimited", "%s has no shared rate limit" % route["backend"])

        if payload["action"] == "preflight":
            for item in items[:1]:
                username = username_of(item)
                if not username or not deps.user_exists(username):
                    raise Stop("error", "no account to build an unsubscribe link for")
                link = deps.unsubscribe_link(username, item["course_id"])
                size = len(build(item, link).message().as_bytes())  # builds the MIME exactly as a send would
                emit("UNSUB_CHECK", ok=True, example=mask(link), message_bytes=size)
        else:
            conn.open()  # one connection for the whole run (the backends reuse an open one)
            streak = 0
            for position, item in enumerate(items):
                if clock() - started > opts["max_runtime"]:
                    raise Stop("time_limit")
                if route["kind"] == "limited":
                    try:
                        used = usage(conn, clock)
                    except Exception as exc:
                        raise Stop("limiter_unreachable", short(exc))
                    if conn.daily_cap and used >= conn.daily_cap * opts["yield_above"]:
                        raise Stop("yield_budget", "%d of %d sent today by the whole server" % (used, conn.daily_cap))
                username = username_of(item)
                if not username or not deps.user_exists(username):
                    stats["skipped"] += 1
                    emit("RESULT", idx=item["idx"], status="skipped", detail="no account to unsubscribe")
                    continue
                link = deps.unsubscribe_link(username, item["course_id"])
                begun = clock()
                status, detail = send_one(build(item, link))
                if status == "sent":
                    stats["sent"] += 1
                    streak = 0
                else:
                    stats["refused"] += 1
                    streak += 1
                emit("RESULT", idx=item["idx"], status=status, detail=detail)
                if streak >= MAX_CONSECUTIVE_REFUSALS:
                    raise Stop("refusals", "%d recipients in a row were refused" % streak)
                if position < len(items) - 1 and clock() - begun < opts["delay"]:
                    sleep(opts["delay"] - (clock() - begun))
    except Stop as stop:
        stopped = (stop.reason, stop.detail)
    except Exception as exc:  # last resort: still report, so the host script can fail loudly
        stopped = ("error", short(exc))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    summary = dict(stats, stopped=stopped[0] if stopped else None, detail=stopped[1] if stopped else "",
                   kind=route.get("kind"))
    if route.get("kind") == "limited":
        try:
            summary["used_end"] = usage(conn, clock)
            summary["daily_cap"] = conn.daily_cap
        except Exception:
            pass
    try:
        emit("SUMMARY", **summary)
    except Stop:
        pass


if "PAYLOAD_B64" in globals():
    nudge_agent(json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8")))
'''


def agent_command(args):
    docker = shutil.which("docker") or "/usr/bin/docker"
    # -c with an explicit stdin read: no reliance on Django noticing piped input
    return [docker, "exec", "-i", args.lms_container, "./manage.py", "lms", "shell",
            "-c", "import sys; exec(sys.stdin.read())"]


def run_agent(args, payload):
    """Run the delivery agent in the LMS container; yield its events as (tag, dict) as they happen."""
    code = "PAYLOAD_B64 = %r\n%s" % (base64.b64encode(json.dumps(payload).encode("utf-8")).decode(), AGENT_SOURCE)
    proc = subprocess.Popen(agent_command(args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    killer = threading.Timer(args.max_runtime + 300, proc.kill)
    killer.daemon = True
    killer.start()

    def feed():  # a thread, so a big payload can never deadlock against the child's output
        try:
            proc.stdin.write(code)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threading.Thread(target=feed, daemon=True).start()
    noise = collections.deque(maxlen=12)
    got_summary = False
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("NUDGE_"):
                tag, _, data = line[len("NUDGE_"):].partition(" ")
                try:
                    event = json.loads(data)
                except ValueError:
                    noise.append(line)
                    continue
                got_summary = got_summary or tag == "SUMMARY"
                yield tag, event
            elif line.strip():
                noise.append(line)
    finally:
        killer.cancel()
        with contextlib.suppress(Exception):
            proc.stdout.close()
        exit_code = proc.wait()
    if not got_summary:
        raise RuntimeError("the delivery agent in %s ended (exit %s) without a summary; last output: %s" % (
            args.lms_container, exit_code, " | ".join(noise)[-600:] or "(none)"))


# --------------------------------------------------------------------------- state


def check_image(url, timeout=20):
    """Never mail hundreds of learners a broken picture (follows redirects: the logo URL is one)."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ost2-completion-nudge"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            kind = response.headers.get("Content-Type", "")
            if response.status != 200 or not kind.startswith("image/"):
                raise RuntimeError("image check: %s -> HTTP %s, %r" % (url, response.status, kind))
    except urllib.error.URLError as exc:
        raise RuntimeError("image %s is not reachable (%s); the mascot needs hello.png installed" % (url, exc))


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


def load_config(args):
    get = lambda key: tutor_value(args.tutor, key)  # noqa: E731
    lms_host = get("LMS_HOST")
    https = (get("ENABLE_HTTPS") or "true").lower() == "true"
    scheme = "https" if https else "http"
    try:
        mfe_host = get("MFE_HOST")
    except RuntimeError:  # not set on boxes that never customised it
        mfe_host = ""
    mfe_host = mfe_host or "apps." + lms_host
    return {
        "lms_host": lms_host,
        "lms_base": args.lms_url or "%s://%s" % (scheme, lms_host),
        "mfe_base": args.mfe_url or "%s://%s" % (scheme, mfe_host),
        "image_url": args.image_url or "%s://%s%s" % (scheme, lms_host, IMAGE_PATH),
        "logo_url": args.logo_url or "%s://%s%s" % (scheme, lms_host, LOGO_PATH),
        "sender": args.sender or get("CONTACT_EMAIL"),
        "sender_name": get("PLATFORM_NAME") or "OpenSecurityTraining2",
        "mysql_password": get("MYSQL_ROOT_PASSWORD"),
    }


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


def agent_options(args):
    return {"backend": args.mail_backend, "delay": args.delay, "yield_above": args.yield_above,
            "max_runtime": args.max_runtime, "sentinel": UNSUBSCRIBE_SENTINEL}


def build_items(cfg, selected, args, mode):
    items = []
    for index, candidate in enumerate(selected):
        recipient = args.only_to or candidate.email
        item = render_message(cfg, candidate, recipient, test=(mode == "test"))
        item.update(idx=index, course_id=candidate.course_id)
        if mode == "test":  # never the learner's own link: the account that owns the test address
            item.update(username=args.unsub_as or "", lookup_email=recipient)
        else:
            item.update(username=candidate.username)
        items.append(item)
    return items


def route_line(route, yield_above):
    if route["kind"] == "limited":
        cap = route.get("daily_cap") or 0
        return ("mail route: %s -- shared limiter %s/min, daily cap %s, nudges yield above %d sent/day; "
                "the whole server has sent %s today" % (
                    route["backend"], route.get("rate_per_min"), cap, int(cap * yield_above), route.get("used_today")))
    if route["kind"] == "files":
        return ("mail route: %s -- this box writes mail to files, NOTHING IS DELIVERED from here "
                "(dev's ost2_dev_mail_to_files)" % route["backend"])
    return "mail route: %s -- NOT rate limited, refusing to send" % route["backend"]


def run_preflight(args, cfg, plan, mode):
    """Dry run: ask the agent about the mail route and the unsubscribe link; send nothing."""
    payload = {"action": "preflight", "options": agent_options(args),
               "items": build_items(cfg, plan.selected[:1], args, mode)}
    ok = False
    for tag, data in run_agent(args, payload):
        if tag == "ROUTE":
            print("  " + route_line(data, args.yield_above))
        elif tag == "UNSUB_CHECK":
            print("  unsubscribe link: built by the platform, e.g. %s; the first message builds fine (%s bytes)"
                  % (data.get("example"), data.get("message_bytes")))
        elif tag == "SUMMARY":
            ok = data.get("stopped") is None
            if not ok:
                print("  PRE-FLIGHT PROBLEM: %s %s" % (data.get("stopped"), data.get("detail", "")))
    return ok


def send_batch(args, cfg, plan, mode, ledger, now):
    """Hand the planned emails to the delivery agent; record each result as it streams back."""
    payload = {"action": "send", "options": agent_options(args), "items": build_items(cfg, plan.selected, args, mode)}
    result = {"sent": 0, "refused": 0, "skipped": 0, "summary": {}, "delivered": True}
    for tag, data in run_agent(args, payload):
        if tag == "ROUTE":
            result["delivered"] = data["kind"] == "limited"
            print(route_line(data, args.yield_above))
        elif tag == "BACKOFF":
            print("%s backing off %ss: %s" % (utcnow().isoformat(), data["seconds"], data["why"]))
        elif tag == "RESULT":
            candidate = plan.selected[data["idx"]]
            record = mode == "send" and result["delivered"]  # tests and file-only routes leave no trace
            if data["status"] == "sent":
                result["sent"] += 1
                if record:
                    ledger.append(candidate, "sent", utcnow())
                label = ("sent" if record else "TEST-sent to %s (not recorded)" % args.only_to) \
                    if result["delivered"] else "WRITTEN TO A FILE, not delivered (not recorded)"
                print("%s %s %s" % (utcnow().isoformat(), label, describe(candidate, now)))
            elif data["status"] == "refused":
                result["refused"] += 1
                if record:
                    ledger.append(candidate, "refused", utcnow())
                print("%s refused %s (%s)" % (utcnow().isoformat(), describe(candidate, now), data.get("detail")))
            else:
                result["skipped"] += 1
                print("%s skipped %s (%s)" % (utcnow().isoformat(), describe(candidate, now), data.get("detail")))
        elif tag == "SUMMARY":
            result["summary"] = data
    return result


def run(args):
    mode = "test" if args.only_to else ("send" if args.send else "dry-run")
    if args.only_to and args.send:
        raise SystemExit("--only-to (test) and --send (live) are mutually exclusive")
    if (args.mail_backend or args.unsub_as) and mode != "test":
        raise SystemExit("--mail-backend and --unsub-as are only for --only-to tests")
    max_send = args.max_send if args.max_send is not None else (1 if mode == "test" else DEFAULT_MAX_SEND)
    now = utcnow()
    guard = exclusive_lock(args.state_dir) if mode != "dry-run" else contextlib.nullcontext()
    with guard:
        cfg = load_config(args)
        sql = candidates_sql(args.min_percent, args.inactive_days, args.course_id, args.user_id)
        candidates = [to_candidate(row) for row in mysql_json_rows(args, cfg["mysql_password"], sql)]
        ledger = Ledger(args.state_dir)
        plan = build_plan(
            candidates, ledger.load() if mode != "test" else [], now, max_send,
            max_inactive_days=args.max_inactive_days, min_days_between=args.min_days_between,
            use_ledger=(mode != "test"))
        print_plan(plan, now, mode)

        if mode == "dry-run":
            ready = run_preflight(args, cfg, plan, mode)
            if plan.selected:
                first = plan.selected[0]
                preview = render_message(cfg, first, "preview@example.invalid")
                sample = "https://%s/bulk_email/email/optout/<token>/%s/" % (cfg["lms_host"], first.course_id)
                print("\n--- preview of the first email (%s) ---\nSubject: %s\nImage: %s\n\n%s" % (
                    describe(first, now), preview["subject"], cfg["image_url"],
                    preview["text"].replace(UNSUBSCRIBE_SENTINEL, sample)))
                if args.preview_html:
                    with open(args.preview_html, "w", encoding="utf-8") as handle:
                        handle.write(preview["html"].replace(UNSUBSCRIBE_SENTINEL, sample))
            print("  (dry run: nothing sent, nothing saved; --send emails learners, --only-to ADDR tests)")
            return 0 if ready else 1

        if not plan.selected:
            print("%s nothing to send" % now.isoformat())
            clear_failure(args.state_dir)
            return 0
        if not args.skip_image_check:
            check_image(cfg["image_url"])
            check_image(cfg["logo_url"])

        result = send_batch(args, cfg, plan, mode, ledger, now)
        summary = result["summary"]
        stopped = summary.get("stopped")
        if stopped and stopped not in EXPECTED_STOPS:
            raise RuntimeError("delivery stopped (%s) after %d sent: %s" % (stopped, result["sent"], summary.get("detail")))
        clear_failure(args.state_dir)
        if stopped:
            print("%s stopped early (%s%s): the rest resumes at the next run" % (
                utcnow().isoformat(), stopped, ": " + summary["detail"] if summary.get("detail") else ""))
        handled = result["sent"] + result["refused"] + result["skipped"]
        backlog = max(len(plan.eligible) - handled, 0) if mode == "send" and result["delivered"] else len(plan.eligible)
        used = ""
        if summary.get("used_end") is not None:
            used = " server_sent_today=%s/%s" % (summary["used_end"], summary.get("daily_cap"))
        print("%s done: sent=%d refused=%d skipped=%d backlog_left=%d%s" % (
            utcnow().isoformat(), result["sent"], result["refused"], result["skipped"], backlog, used))
        return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--send", action="store_true", help="LIVE: email the learners (default is a dry run)")
    ap.add_argument("--only-to", metavar="ADDR", help="TEST: deliver every selected email to ADDR only; "
                    "default max 1; the ledger is neither read nor written")
    ap.add_argument("--max-send", type=int, help="most emails in one run (default %d, 1 with --only-to)"
                    % DEFAULT_MAX_SEND)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help="least seconds between two of this job's emails; the server-wide limiter "
                    "separately keeps ALL mail 2 s apart (default %(default)s)")
    ap.add_argument("--yield-above", type=float, default=DEFAULT_YIELD_ABOVE,
                    help="stop once the server has sent this fraction of the daily cap today, "
                    "leaving the rest to other mail (default %(default)s)")
    ap.add_argument("--max-runtime", type=int, default=DEFAULT_MAX_RUNTIME,
                    help="seconds before the run stops by itself (default %(default)s)")
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
    ap.add_argument("--tutor", default=DEFAULT_TUTOR, help="tutor binary used to read the box's settings")
    ap.add_argument("--mysql-container", default="tutor_local-mysql-1")
    ap.add_argument("--mysql-user", default="root")
    ap.add_argument("--db", default="openedx")
    ap.add_argument("--lms-container", default="tutor_local-lms-1",
                    help="container the delivery agent runs in (default %(default)s)")
    ap.add_argument("--lms-url", help="override the LMS base used for the Accomplishments and Leaderboard "
                    "links, e.g. https://p.ost2.fyi")
    ap.add_argument("--mfe-url", help="override the MFE base, e.g. https://apps.p.ost2.fyi")
    ap.add_argument("--image-url", help="override the mascot PNG URL (default <LMS_HOST>%s)" % IMAGE_PATH)
    ap.add_argument("--logo-url", help="override the header logo URL (default <LMS_HOST>%s, the logo "
                    "course emails use)" % LOGO_PATH)
    ap.add_argument("--skip-image-check", action="store_true", help="do not HEAD-check the mascot image and "
                    "logo first")
    ap.add_argument("--sender", help="From address (default: tutor CONTACT_EMAIL)")
    ap.add_argument("--mail-backend", metavar="DOTTED.PATH",
                    help="TEST only: use this Django mail backend instead of the box's own route")
    ap.add_argument("--unsub-as", metavar="USERNAME",
                    help="TEST only: build the unsubscribe link for this account (default: the account "
                    "whose email is the --only-to address)")
    ap.add_argument("--preview-html", metavar="FILE", help="dry run: also write the first email's HTML here")
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
