#!/usr/bin/env python3
"""Weekly email of learner-dashboard unenrollment-survey answers.

The learner-dashboard MFE (fork branch teak3_2_unenroll-survey-multiselect) POSTs every
submitted "Why are you unenrolling?" survey to the LMS /event endpoint, which writes one
`unenrollment_reason.selected` record per submission to tracking.log.  This script reads
those records for the period since the last report, formats them as a plain-text email and
sends it through the box's own Tutor SMTP settings (SMTP_*, CONTACT_EMAIL).

Standard library only; run it from cron on the Tutor host (NOT inside a container):

    0 8 * * 0  python3 ~/ost2-host-scripts/ost2_unenroll_report.py --to xeno@ost2.fyi \
               >> ~/.local/share/ost2-unenroll-report/report.log 2>&1

Period: from the end of the previous successful report (kept in <state-dir>/last_report_end)
to now, so a missed week is caught up by the next run instead of leaving a gap; the very first
run covers the previous 7 days.  The marker only advances after the email was accepted by the
SMTP server.  `--dry-run` prints the email and touches nothing.  An email is sent even when
there are no answers, so a silent inbox means the job is broken, not that nobody unenrolled.

The email names the learner (username) on each response, but carries no email address, user id
or IP.  BY COURSE lists, under each course, how many times each reason was given for it.

BY COURSE (ALL TIME) at the end is the same breakdown over every answer ever logged.  The running
totals live in <state-dir>/all_time.json and each run only adds the period since the file's
"through" time, so tracking.log is never re-read in full.  The first run (no file yet) seeds it
from every tracking.log still on disk -- a one-off full scan that can take minutes on a big
prod log; if that history is later rotated away the JSON is the only copy, so keep it.
`--rebuild-all-time` discards the file and re-seeds from the logs.  Like the weekly marker, the
file only advances after the email was accepted by the SMTP server.
"""
import argparse
import collections
import datetime as dt
import gzip
import json
import os
import smtplib
import ssl
import subprocess
import sys
import traceback
from email.message import EmailMessage

EVENT_NAMES = ("unenrollment_reason.selected", "entitlement_unenrollment_reason.selected")
DEFAULT_WINDOW = dt.timedelta(days=7)
TUTOR_ROOT = os.path.expanduser("~/.local/share/tutor")
DEFAULT_LOG_DIR = os.path.join(TUTOR_ROOT, "data", "lms", "logs")
DEFAULT_STATE_DIR = os.path.expanduser("~/.local/share/ost2-unenroll-report")
DEFAULT_TUTOR = os.path.expanduser("~/tutor-venv/bin/tutor")
MARKER = "last_report_end"
ALL_TIME = "all_time.json"
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
FAIL_MARKER = "last_failure"


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def parse_time(value):
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))  # 3.10 rejects "Z"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


# --------------------------------------------------------------------------- reading the log


def tracking_logs(log_dir, since):
    """tracking.log plus any rotated copies that could hold events at or after `since`."""
    cutoff = since.timestamp() - 86400
    paths = []
    for name in sorted(os.listdir(log_dir)):
        if not name.startswith("tracking.log") or name.endswith(".swp"):
            continue
        path = os.path.join(log_dir, name)
        if os.path.isfile(path) and os.path.getmtime(path) >= cutoff:
            paths.append(path)
    return paths


def candidate_lines(path):
    """Cheap substring pre-filter: tracking.log on prod is tens of GB, so never parse it all."""
    needles = [piece for name in EVENT_NAMES for piece in ("-e", '"name": "%s"' % name)]
    if path.endswith(".gz"):
        with gzip.open(path, "rt", errors="replace") as handle:
            return [line for line in handle if any(n in line for n in EVENT_NAMES)]
    out = subprocess.run(
        ["grep", "-a", "-h", "-F"] + needles + [path],
        capture_output=True, text=True, errors="replace",
    )
    if out.returncode not in (0, 1):  # 1 = no match
        raise RuntimeError("grep failed on %s: %s" % (path, out.stderr.strip()))
    return out.stdout.splitlines()


def parse_record(line):
    """One tracking.log line -> normalised response dict, or None if it is not ours."""
    try:
        record = json.loads(line[line.index("{"):])
    except ValueError:
        return None
    if record.get("name") not in EVENT_NAMES:
        return None
    event = record.get("event")
    if isinstance(event, str):  # defensive: /event keeps unparseable payloads as a string
        try:
            event = json.loads(event)
        except ValueError:
            return None
    if not isinstance(event, dict):
        return None
    reasons, details = [], {}
    for item in event.get("reasons") or []:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or item.get("key") or "").strip()
        if not label:
            continue
        reasons.append(label)
        text = " ".join(str(item.get("details") or "").split())
        if text:  # a reason that carries the learner's own words, e.g. "Something was broken"
            details[label] = text
    other = (event.get("other") or "").strip()
    if not reasons and not other:
        return None
    return {
        "time": parse_time(record["time"]),
        "course_id": event.get("course_id") or record.get("context", {}).get("course_id") or "",
        "reasons": reasons,
        "details": details,
        "other": other,
        "learner": record.get("context", {}).get("user_id") or record.get("username"),
        "username": (record.get("username") or record.get("context", {}).get("username") or "").strip(),
    }


def collect(log_dir, since, until):
    responses = []
    for path in tracking_logs(log_dir, since):
        for line in candidate_lines(path):
            response = parse_record(line)
            if response and since <= response["time"] < until:
                responses.append(response)
    responses.sort(key=lambda r: r["time"])
    return responses


# --------------------------------------------------------------------------- rendering


def course_label(course_id):
    """course-v1:OpenSecurityTraining2+Arch1001_x86-64_Asm+2021_v1 -> Arch1001_x86-64_Asm (2021_v1)"""
    parts = course_id.split(":", 1)[-1].split("+")
    if course_id and len(parts) == 3:
        return "%s (%s)" % (parts[1], parts[2])
    return course_id or "(unknown course)"


def who(response):
    """The username that took the action; falls back to the user id if the record had none."""
    if response["username"]:
        return response["username"]
    if response["learner"] is not None:
        return "user id %s" % response["learner"]
    return "(unknown user)"


def reason_texts(response):
    """Each reason as shown to the reader; ones carrying the learner's own words include them."""
    items = ["%s: %s" % (label, response["details"][label]) if label in response["details"] else label
             for label in response["reasons"]]
    if response["other"]:
        items.append("Other: " + " ".join(response["other"].split()))
    return items


def tally(responses, courses=None):
    """Add responses to {course_id: {"responses": n, "reasons": {text: n}}} (JSON-friendly)."""
    courses = {} if courses is None else courses
    for r in responses:
        entry = courses.setdefault(r["course_id"], {"responses": 0, "reasons": {}})
        entry["responses"] += 1
        for text in reason_texts(r):
            entry["reasons"][text] = entry["reasons"].get(text, 0) + 1
    return courses


def by_course_lines(courses):
    """Per course: its response count, then each reason given for it, indented underneath."""
    merged = {}
    for course_id, entry in courses.items():
        slot = merged.setdefault(course_label(course_id),
                                 {"responses": 0, "reasons": collections.Counter()})
        slot["responses"] += entry["responses"]
        slot["reasons"].update(entry["reasons"])
    width = len(str(max(slot["responses"] for slot in merged.values())))
    lines = []
    for label, slot in sorted(merged.items(), key=lambda item: (-item[1]["responses"], item[0])):
        lines.append("  %*d  %s" % (width, slot["responses"], label))
        reasons = slot["reasons"]
        sub_width = len(str(max(reasons.values())))
        for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0])):
            lines.append("  %s    %*d  %s" % (" " * width, sub_width, count, reason))
    return lines


def all_time_lines(all_time):
    lines = ["", "BY COURSE (ALL TIME)", "--------------------"]
    if not all_time["responses"]:
        return lines + ["No unenrollment survey answers have been logged yet."]
    since = all_time["first_response"][:10]
    lines.append("%d response%s since %s, this period included.  Same layout as BY COURSE above." % (
        all_time["responses"], "" if all_time["responses"] == 1 else "s", since))
    return lines + by_course_lines(all_time["courses"])


def fmt_day(moment):
    return moment.strftime("%Y-%m-%d")


def fmt_moment(moment):
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def render(responses, since, until, host, all_time=None):
    """Returns (subject, body).  `all_time` (see update_all_time) adds the ALL TIME section."""
    count = len(responses)
    noun = "response" if count == 1 else "responses"
    subject = "[OST2 %s] Weekly unenrollment reasons: %s to %s (%d %s)" % (
        host, fmt_day(since), fmt_day(until), count, noun,
    )
    lines = [
        "Unenrollment survey answers from %s" % host,
        "Period: %s -> %s" % (fmt_moment(since), fmt_moment(until)),
        "",
    ]
    if not responses:
        lines.append("No unenrollment survey answers were submitted in this period.")
        lines.append("(Learners who press 'Skip survey' are not counted here.)")
        if all_time:
            lines += all_time_lines(all_time)
        return subject, "\n".join(lines) + "\n"

    learners = {r["learner"] for r in responses if r["learner"] is not None}
    selections = sum(len(r["reasons"]) + (1 if r["other"] else 0) for r in responses)
    lines.append("%d %s from %d learner%s, %d reason selection%s in total." % (
        count, noun, len(learners), "" if len(learners) == 1 else "s",
        selections, "" if selections == 1 else "s",
    ))
    lines.append("(Learners who press 'Skip survey' are not counted here.)")

    by_reason = collections.Counter()
    for r in responses:
        by_reason.update(r["reasons"])
        if r["other"]:
            by_reason["Other (free text)"] += 1
    lines += ["", "BY REASON", "---------"]
    width = len(str(max(by_reason.values())))
    for reason, n in sorted(by_reason.items(), key=lambda item: (-item[1], item[0])):
        lines.append("  %*d  %s" % (width, n, reason))

    lines += ["", "BY COURSE", "---------",
              "(a response can give several reasons, so the reasons listed under a course can",
              " add up to more than its response count)"]
    lines += by_course_lines(tally(responses))

    written = [r for r in responses if r["details"] or r["other"]]
    if written:
        lines += ["", "FREE-TEXT ANSWERS", "-----------------"]
        for r in written:
            lines.append("  [%s] %s %s" % (fmt_moment(r["time"]), course_label(r["course_id"]), who(r)))
            for label, text in r["details"].items():
                lines.append('    %s: "%s"' % (label, text))
            if r["other"]:
                lines.append('    Other: "%s"' % " ".join(r["other"].split()))

    lines += ["", "ALL RESPONSES (oldest first)", "----------------------------"]
    for r in responses:
        lines.append("  %s  %s %s" % (fmt_moment(r["time"]), course_label(r["course_id"]), who(r)))
        lines.extend("      - " + item for item in reason_texts(r))
    if all_time:
        lines += all_time_lines(all_time)
    return subject, "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- sending


def tutor_value(tutor, key):
    out = subprocess.run([tutor, "config", "printvalue", key], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("tutor config printvalue %s failed: %s" % (key, out.stderr.strip()))
    value = out.stdout.strip()
    return "" if value in ("None", "null") else value


def smtp_settings(args):
    """CLI overrides win (used for local testing); otherwise the box's own Tutor config."""
    if args.smtp_host:
        return {
            "host": args.smtp_host, "port": args.smtp_port or 25, "tls": False, "ssl": False,
            "user": "", "password": "", "sender": args.sender or "ost2-report@localhost",
        }
    get = lambda key: tutor_value(args.tutor, key)  # noqa: E731
    return {
        "host": get("SMTP_HOST"),
        "port": int(get("SMTP_PORT") or 587),
        "tls": get("SMTP_USE_TLS").lower() == "true",
        "ssl": get("SMTP_USE_SSL").lower() == "true",
        "user": get("SMTP_USERNAME"),
        "password": get("SMTP_PASSWORD"),
        "sender": args.sender or get("CONTACT_EMAIL"),
    }


def send(settings, recipient, subject, body):
    message = EmailMessage()
    message["From"] = settings["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if settings["ssl"]:
        client = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=60,
                                  context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(settings["host"], settings["port"], timeout=60)
    with client:
        if settings["tls"] and not settings["ssl"]:
            client.starttls(context=ssl.create_default_context())
        if settings["user"]:
            client.login(settings["user"], settings["password"])
        refused = client.send_message(message)
    if refused:
        raise RuntimeError("SMTP server refused recipients: %r" % (refused,))


# --------------------------------------------------------------------------- state


def read_marker(state_dir):
    try:
        with open(os.path.join(state_dir, MARKER)) as handle:
            return parse_time(handle.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_marker(state_dir, moment):
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, MARKER + ".tmp")
    with open(tmp, "w") as handle:
        handle.write(moment.isoformat() + "\n")
    os.replace(tmp, os.path.join(state_dir, MARKER))


def read_all_time(state_dir):
    """The saved running totals, or None if there are none yet."""
    path = os.path.join(state_dir, ALL_TIME)
    try:
        with open(path) as handle:
            data = json.load(handle)
        data["through"] = parse_time(data["through"])
        return data
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("%s is unreadable (%s): restore it, or run with --rebuild-all-time "
                           "to re-seed it from the tracking logs" % (path, exc))


def write_all_time(state_dir, data):
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, ALL_TIME + ".tmp")
    with open(tmp, "w") as handle:
        json.dump(dict(data, through=data["through"].isoformat()), handle,
                  indent=1, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, os.path.join(state_dir, ALL_TIME))


def update_all_time(args, responses, since, until):
    """Running totals brought up to `until`: the saved ones plus only what is new since them.

    Returns (totals, changed); nothing is saved here.  `responses` are those of [since, until),
    reused when that is exactly the new stretch (the normal weekly run) so the log is read once.
    """
    state = None if args.rebuild_all_time else read_all_time(args.state_dir)
    if state is None:
        state = {"through": EPOCH, "first_response": None, "responses": 0, "courses": {}}
    if state["through"] >= until:
        return state, False
    new = responses if state["through"] == since else collect(args.log_dir, state["through"], until)
    tally(new, state["courses"])
    state["responses"] += len(new)
    if new and not state["first_response"]:
        state["first_response"] = new[0]["time"].isoformat()
    state["through"] = until
    return state, True


def record_failure(state_dir, text):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, FAIL_MARKER), "w") as handle:
        handle.write("%s\n%s\n" % (utcnow().isoformat(), text))


def clear_failure(state_dir):
    try:
        os.remove(os.path.join(state_dir, FAIL_MARKER))
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- main


def run(args):
    until = parse_time(args.until) if args.until else utcnow()
    if args.since:
        since = parse_time(args.since)
    else:
        since = read_marker(args.state_dir) or until - DEFAULT_WINDOW
    if since >= until:
        print("Nothing to do: period start %s is not before end %s" % (since, until))
        return 0

    responses = collect(args.log_dir, since, until)
    host = args.host_label or tutor_value(args.tutor, "LMS_HOST")
    all_time, all_time_changed = update_all_time(args, responses, since, until)
    subject, body = render(responses, since, until, host, all_time)

    if args.dry_run:
        print("To: %s\nSubject: %s\n\n%s" % (args.to, subject, body))
        return 0

    send(smtp_settings(args), args.to, subject, body)
    if all_time_changed:
        write_all_time(args.state_dir, all_time)
    if not args.since:  # a manual --since replay must not move the weekly window
        write_marker(args.state_dir, until)
    clear_failure(args.state_dir)
    print("%s sent %r to %s (%d responses, %s -> %s)" % (
        utcnow().isoformat(), subject, args.to, len(responses), since.isoformat(), until.isoformat(),
    ))
    return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--to", required=True, help="recipient address")
    ap.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help="dir holding tracking.log")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--tutor", default=DEFAULT_TUTOR, help="tutor binary used to read SMTP settings")
    ap.add_argument("--since", help="ISO time; overrides the marker and never advances it")
    ap.add_argument("--until", help="ISO time; default now")
    ap.add_argument("--dry-run", action="store_true", help="print the email; send nothing, save nothing")
    ap.add_argument("--rebuild-all-time", action="store_true",
                    help="discard the saved ALL TIME totals and re-seed them from the tracking logs")
    ap.add_argument("--host-label", help="name shown in the subject (default: tutor LMS_HOST)")
    ap.add_argument("--sender", help="From address (default: tutor CONTACT_EMAIL)")
    ap.add_argument("--smtp-host", help="override Tutor's SMTP settings (testing)")
    ap.add_argument("--smtp-port", type=int)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except Exception:  # noqa: BLE001 - cron has nobody watching; leave a marker and fail loudly
        if not args.dry_run:
            record_failure(args.state_dir, traceback.format_exc())
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
