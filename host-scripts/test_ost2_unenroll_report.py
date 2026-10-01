"""Run with:  python3 -m unittest -v test_ost2_unenroll_report   (from host-scripts/)"""
import datetime as dt
import gzip
import json
import os
import tempfile
import unittest
from unittest import mock

import ost2_unenroll_report as report

UTC = dt.timezone.utc
COURSE = "course-v1:OpenSecurityTraining2+Arch1001_x86-64_Asm+2021_v1"


def log_line(when, reasons=(), other="", course=COURSE, name="unenrollment_reason.selected",
             user_id=5, event=None):
    """A tracking.log line shaped exactly like the real /event records on dev."""
    if event is None:
        event = {"course_id": course, "reasons": [{"key": k, "label": l} for k, l in reasons],
                 "other": other, "is_entitlement": False}
    record = {
        "name": name,
        "context": {"user_id": user_id, "path": "/event", "course_id": course,
                    "org_id": "OpenSecurityTraining2", "enterprise_uuid": ""},
        "username": "someone", "session": "abc", "ip": "203.0.113.9", "agent": "x", "host": "dev.ost2.fyi",
        "referer": "https://apps.dev.ost2.fyi/", "accept_language": "en", "event": event,
        "time": when.isoformat(), "event_type": name, "event_source": "browser",
        "page": "https://apps.dev.ost2.fyi/learner-dashboard/",
    }
    stamp = when.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return "%s INFO 22 [tracking] [user %s] [ip 203.0.113.9] logger.py:41 - %s\n" % (
        stamp, user_id, json.dumps(record))


def at(day, hour=12, minute=0):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=UTC)


TIME = ("time", "I don't have the time")
EASY = ("easy", "The course material was too easy")
BROKEN = ("broken", "Something was broken")
ZERO = ("zeroCompletion", "I needed to unenroll from a 0%-completion class to register for new classes")


class ParseTests(unittest.TestCase):
    def test_parses_a_real_shaped_record(self):
        r = report.parse_record(log_line(at(1), [TIME, EASY], other=" more "))
        self.assertEqual(r["reasons"], [TIME[1], EASY[1]])
        self.assertEqual(r["other"], "more")
        self.assertEqual(r["course_id"], COURSE)
        self.assertEqual(r["time"], at(1))

    def test_reason_details_are_kept_per_label(self):
        event = {"course_id": COURSE, "other": "", "reasons": [
            {"key": "broken", "label": BROKEN[1], "details": "  video  would not\nplay "},
            {"key": "time", "label": TIME[1]}]}
        r = report.parse_record(log_line(at(1), event=event))
        self.assertEqual(r["reasons"], [BROKEN[1], TIME[1]])
        self.assertEqual(r["details"], {BROKEN[1]: "video would not play"})
        self.assertEqual(report.parse_record(log_line(at(1), [TIME]))["details"], {})

    def test_other_names_and_junk_are_ignored(self):
        self.assertIsNone(report.parse_record(log_line(at(1), [TIME], name="edx.profile.viewed")))
        self.assertIsNone(report.parse_record("not json at all"))
        self.assertIsNone(report.parse_record(log_line(at(1), event="garbage")))
        self.assertIsNone(report.parse_record(log_line(at(1), [], other="")))

    def test_event_may_arrive_as_a_json_string(self):
        event = json.dumps({"course_id": COURSE, "reasons": [{"key": "time", "label": TIME[1]}], "other": ""})
        self.assertEqual(report.parse_record(log_line(at(1), event=event))["reasons"], [TIME[1]])

    def test_falls_back_to_key_when_label_missing(self):
        event = {"course_id": COURSE, "reasons": [{"key": "time"}], "other": ""}
        self.assertEqual(report.parse_record(log_line(at(1), event=event))["reasons"], ["time"])

    def test_entitlement_event_is_included(self):
        line = log_line(at(1), [TIME], name="entitlement_unenrollment_reason.selected")
        self.assertIsNotNone(report.parse_record(line))

    def test_z_suffix_times_parse(self):
        self.assertEqual(report.parse_time("2026-10-04T08:00:00Z"), at(4, 8))


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, text, gz=False):
        path = os.path.join(self.dir.name, name)
        if gz:
            with gzip.open(path, "wt") as handle:
                handle.write(text)
        else:
            with open(path, "w") as handle:
                handle.write(text)
        os.utime(path, (at(30).timestamp(),) * 2)  # written after the events, like a live log
        return path

    def test_window_is_half_open_and_other_events_are_skipped(self):
        self.write("tracking.log", "".join([
            log_line(at(1, 7, 59), [TIME]),                       # before the window
            log_line(at(1, 8, 0), [TIME]),                        # == since: in
            log_line(at(3, 12), [EASY]),                          # in
            log_line(at(3, 12), [TIME], name="edx.profile.viewed"),
            log_line(at(8, 8, 0), [ZERO]),                        # == until: out
        ]))
        got = report.collect(self.dir.name, at(1, 8), at(8, 8))
        self.assertEqual([r["reasons"] for r in got], [[TIME[1]], [EASY[1]]])

    def test_reads_rotated_and_gzipped_logs_in_time_order(self):
        self.write("tracking.log.1.gz", log_line(at(2), [TIME]), gz=True)
        self.write("tracking.log", log_line(at(3), [EASY]))
        got = report.collect(self.dir.name, at(1), at(8))
        self.assertEqual([r["time"] for r in got], [at(2), at(3)])

    def test_ignores_unrelated_files_and_old_rotations(self):
        self.write("all.log", log_line(at(2), [TIME]))
        old = self.write("tracking.log.9.gz", log_line(at(2), [TIME]), gz=True)
        os.utime(old, (at(1).timestamp() - 10 * 86400,) * 2)
        self.assertEqual(report.collect(self.dir.name, at(1), at(8)), [])

    def test_missing_events_is_not_an_error(self):
        self.write("tracking.log", log_line(at(2), [], name="edx.profile.viewed", event={}))
        self.assertEqual(report.collect(self.dir.name, at(1), at(8)), [])


class RenderTests(unittest.TestCase):
    def responses(self):
        mk = lambda day, reasons, other="", user=5: report.parse_record(  # noqa: E731
            log_line(at(day), reasons, other, user_id=user))
        return [mk(2, [TIME, EASY], user=5), mk(3, [TIME], other="needed the slot", user=6),
                mk(4, [ZERO], user=5)]

    def test_empty_report_still_says_so(self):
        subject, body = report.render([], at(1, 8), at(8, 8), "dev.ost2.fyi")
        self.assertIn("0 responses", subject)
        self.assertIn("No unenrollment survey answers", body)

    def test_summary_sections_and_counts(self):
        subject, body = report.render(self.responses(), at(1, 8), at(8, 8), "dev.ost2.fyi")
        self.assertEqual(
            subject, "[OST2 dev.ost2.fyi] Weekly unenrollment reasons: 2026-10-01 to 2026-10-08 (3 responses)")
        self.assertIn("3 responses from 2 learners, 5 reason selections in total.", body)
        self.assertIn("  2  I don't have the time", body)
        self.assertIn("  1  Other (free text)", body)
        self.assertIn("  3  Arch1001_x86-64_Asm (2021_v1)", body)
        self.assertIn('"needed the slot"', body)
        self.assertIn("      - Other: needed the slot", body)

    def test_free_text_section_covers_broken_details_and_other(self):
        event = {"course_id": COURSE, "other": "needed the slot", "reasons": [
            {"key": "broken", "label": BROKEN[1], "details": "video would not play"}]}
        r = report.parse_record(log_line(at(2), event=event))
        subject, body = report.render([r], at(1, 8), at(8, 8), "dev.ost2.fyi")
        self.assertIn("FREE-TEXT ANSWERS", body)
        self.assertIn('    Something was broken: "video would not play"', body)
        self.assertIn('    Other: "needed the slot"', body)
        self.assertIn("      - Something was broken: video would not play", body)
        self.assertIn("  1  Something was broken", body)  # still counted by plain label
        self.assertIn("1 response from 1 learner, 2 reason selections in total.", body)

    def test_broken_without_details_is_just_a_label(self):
        r = report.parse_record(log_line(at(2), [BROKEN]))
        _, body = report.render([r], at(1, 8), at(8, 8), "h")
        self.assertNotIn("FREE-TEXT ANSWERS", body)
        self.assertIn("      - Something was broken\n", body)

    def test_singular_wording(self):
        subject, body = report.render(self.responses()[:1], at(1, 8), at(8, 8), "h")
        self.assertIn("(1 response)", subject)
        self.assertIn("1 response from 1 learner, 2 reason selections in total.", body)

    def test_no_learner_identity_leaks_into_the_email(self):
        _, body = report.render(self.responses(), at(1, 8), at(8, 8), "dev.ost2.fyi")
        for secret in ("someone", "203.0.113.9"):
            self.assertNotIn(secret, body)

    def test_course_label(self):
        self.assertEqual(report.course_label(COURSE), "Arch1001_x86-64_Asm (2021_v1)")
        self.assertEqual(report.course_label(""), "(unknown course)")
        self.assertEqual(report.course_label("weird"), "weird")


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = os.path.join(self.tmp.name, "logs")
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.logs)
        live = os.path.join(self.logs, "tracking.log")
        with open(live, "w") as handle:
            handle.write(log_line(at(2), [TIME]))
            handle.write(log_line(at(9), [EASY]))
        os.utime(live, (at(30).timestamp(),) * 2)
        self.base = ["--to", "xeno@ost2.fyi", "--log-dir", self.logs, "--state-dir", self.state,
                     "--host-label", "dev.ost2.fyi", "--smtp-host", "localhost", "--smtp-port", "1025"]

    def run_main(self, *extra):
        return report.main(self.base + list(extra))

    def test_send_advances_marker_and_next_run_starts_there(self):
        with mock.patch.object(report, "send") as send, mock.patch.object(report, "utcnow", return_value=at(8, 8)):
            self.assertEqual(self.run_main(), 0)
        self.assertEqual(send.call_count, 1)
        _, recipient, subject, body = send.call_args[0]
        self.assertEqual(recipient, "xeno@ost2.fyi")
        self.assertIn("(1 response)", subject)  # only the Oct 2 answer; Oct 9 is after "now"
        self.assertEqual(report.read_marker(self.state), at(8, 8))
        with mock.patch.object(report, "send") as send, mock.patch.object(report, "utcnow", return_value=at(15, 8)):
            self.assertEqual(self.run_main(), 0)
        self.assertIn("(1 response)", send.call_args[0][2])  # now it is the Oct 9 answer
        self.assertIn(EASY[1], send.call_args[0][3])
        self.assertNotIn(TIME[1], send.call_args[0][3])

    def test_dry_run_prints_and_saves_nothing(self):
        with mock.patch.object(report, "send") as send, mock.patch.object(report, "utcnow", return_value=at(8, 8)):
            self.assertEqual(self.run_main("--dry-run"), 0)
        send.assert_not_called()
        self.assertIsNone(report.read_marker(self.state))
        self.assertFalse(os.path.exists(os.path.join(self.state, report.FAIL_MARKER)))

    def test_send_failure_keeps_marker_and_leaves_fail_marker(self):
        with mock.patch.object(report, "send", side_effect=OSError("smtp down")), \
                mock.patch.object(report, "utcnow", return_value=at(8, 8)), \
                mock.patch.object(report.traceback, "print_exc"):
            self.assertEqual(self.run_main(), 1)
        self.assertIsNone(report.read_marker(self.state))
        self.assertTrue(os.path.exists(os.path.join(self.state, report.FAIL_MARKER)))
        with mock.patch.object(report, "send"), mock.patch.object(report, "utcnow", return_value=at(8, 9)):
            self.assertEqual(self.run_main(), 0)
        self.assertFalse(os.path.exists(os.path.join(self.state, report.FAIL_MARKER)))

    def test_explicit_since_replays_without_moving_the_marker(self):
        with mock.patch.object(report, "send") as send, mock.patch.object(report, "utcnow", return_value=at(20)):
            self.assertEqual(self.run_main("--since", "2026-10-01T00:00:00Z"), 0)
        self.assertIn("(2 responses)", send.call_args[0][2])
        self.assertIsNone(report.read_marker(self.state))

    def test_first_run_covers_previous_seven_days(self):
        with mock.patch.object(report, "send") as send, mock.patch.object(report, "utcnow", return_value=at(10, 0)):
            self.assertEqual(self.run_main(), 0)
        # window Oct 3 00:00 -> Oct 10 00:00: only the Oct 9 answer
        self.assertIn(EASY[1], send.call_args[0][3])
        self.assertNotIn(TIME[1], send.call_args[0][3])


class SendTests(unittest.TestCase):
    def settings(self, **over):
        base = {"host": "smtp.example", "port": 587, "tls": True, "ssl": False,
                "user": "u@x", "password": "pw", "sender": "info@ost2.fyi"}
        base.update(over)
        return base

    def test_starttls_login_and_message_headers(self):
        with mock.patch.object(report.smtplib, "SMTP") as smtp:
            client = smtp.return_value
            client.__enter__.return_value = client
            client.send_message.return_value = {}
            report.send(self.settings(), "xeno@ost2.fyi", "subj", "body")
        smtp.assert_called_once_with("smtp.example", 587, timeout=60)
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("u@x", "pw")
        message = client.send_message.call_args[0][0]
        self.assertEqual((message["From"], message["To"], message["Subject"]),
                         ("info@ost2.fyi", "xeno@ost2.fyi", "subj"))
        self.assertEqual(message.get_content().strip(), "body")

    def test_refused_recipient_is_an_error(self):
        with mock.patch.object(report.smtplib, "SMTP") as smtp:
            client = smtp.return_value
            client.__enter__.return_value = client
            client.send_message.return_value = {"x@y": (550, b"no")}
            with self.assertRaises(RuntimeError):
                report.send(self.settings(), "x@y", "s", "b")

    def test_tutor_none_values_become_empty(self):
        with mock.patch.object(report.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="None\n", stderr="")
            self.assertEqual(report.tutor_value("tutor", "SMTP_USERNAME"), "")


if __name__ == "__main__":
    unittest.main()
