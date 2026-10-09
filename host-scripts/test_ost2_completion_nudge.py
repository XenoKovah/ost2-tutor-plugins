"""Run with:  python3 -m unittest -v test_ost2_completion_nudge   (from host-scripts/)"""
import contextlib
import datetime as dt
import io
import json
import os
import smtplib
import tempfile
import unittest
from unittest import mock

import ost2_completion_nudge as nudge

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
ARCH1005 = "course-v1:OpenSecurityTraining2+Arch1005_IntroRISCV+2024_v1"
ARCH1005_NAME = "Architecture 1005: RISC-V Assembly"
HOME = "https://apps.p.ost2.fyi/learning/course/%s/home" % ARCH1005
PROGRESS = "https://apps.p.ost2.fyi/learning/course/%s/progress" % ARCH1005
ACCOMPLISHMENTS = "https://p.ost2.fyi/gamma_dashboard/dashboard/"
LEADERBOARD = "https://p.ost2.fyi/gamma_dashboard/leaderboard/"
IMAGE = "https://p.ost2.fyi/media/lil-stranger/hello.png"
URLS = {"home": HOME, "progress": PROGRESS, "accomplishments": ACCOMPLISHMENTS, "leaderboard": LEADERBOARD}
CFG = {"lms_base": "https://p.ost2.fyi", "mfe_base": "https://apps.p.ost2.fyi", "image_url": IMAGE,
       "sender": "info@ost2.fyi", "sender_name": "OpenSecurityTraining2", "lms_host": "p.ost2.fyi"}


def cand(user_id=1, course=ARCH1005, name=ARCH1005_NAME, percent=0.92, days=30, email=None):
    return nudge.Candidate(user_id, email or "learner%d@example.com" % user_id, course, name, percent,
                           NOW - dt.timedelta(days=days))


def db_row(user_id=1, course=ARCH1005, name=ARCH1005_NAME, percent=0.92, days=30):
    when = NOW - dt.timedelta(days=days)
    return {"user_id": user_id, "email": "learner%d@example.com" % user_id, "course_id": course,
            "display_name": name, "percent": percent,
            "last_activity": when.strftime("%Y-%m-%d %H:%M:%S.000000")}


class RenderTests(unittest.TestCase):
    def test_html_carries_the_requested_copy_and_both_links(self):
        page = nudge.render_html(ARCH1005_NAME, URLS, IMAGE)
        self.assertIn("Hi! We see that you're &gt; 90% done with the class "
                      + '<a href="%s">%s</a>! That\'s pretty awesome!' % (HOME, ARCH1005_NAME), page)
        self.assertIn('go check out your <a href="%s">Progress</a> page, to see which units you missed, '
                      "and to mark them as done." % PROGRESS, page)
        self.assertIn('a couple of "Mark as complete" buttons', page)
        self.assertIn("Thanks<br>Li'l Stranger", page)

    def test_accomplishment_and_leaderboard_are_links_and_leaderboard_is_capitalized(self):
        page = nudge.render_html(ARCH1005_NAME, URLS, IMAGE)
        self.assertIn("get their completion certificates (and new "
                      + '<a href="%s">Accomplishment</a> badges and points towards the ' % ACCOMPLISHMENTS
                      + '<a href="%s">Leaderboard</a>!), but we can\'t give you your well-deserved kudos '
                      % LEADERBOARD
                      + "until you confirm you're really done with the class.", page)
        self.assertNotIn("the leaderboard", page)
        self.assertEqual(page.count("<a href="), 4)

    def test_every_link_in_the_html_is_one_of_the_four_expected(self):
        page = nudge.render_html(ARCH1005_NAME, URLS, IMAGE)
        hrefs = [chunk.split('"')[0] for chunk in page.split('<a href="')[1:]]
        self.assertEqual(hrefs, [HOME, PROGRESS, ACCOMPLISHMENTS, LEADERBOARD])

    def test_mascot_is_the_first_thing_in_the_body_and_300_wide(self):
        page = nudge.render_html(ARCH1005_NAME, URLS, IMAGE)
        self.assertLess(page.index("<img "), page.index("Hi! We see"))
        self.assertIn('<img src="%s" width="300"' % IMAGE, page)
        self.assertIn("width:300px", page)
        self.assertEqual(page.count("<img "), 1)

    def test_class_names_are_html_escaped(self):
        page = nudge.render_html("Attack & Defense <b>", URLS, IMAGE)
        self.assertIn(">Attack &amp; Defense &lt;b&gt;</a>", page)
        self.assertNotIn("<b>", page)

    def test_text_part_has_both_urls_and_every_paragraph(self):
        text = nudge.render_text(ARCH1005_NAME, URLS)
        flat = text.replace("\n", " ")
        self.assertIn("%s <%s>! That's pretty awesome!" % (ARCH1005_NAME, HOME), flat)
        self.assertIn("Progress page <%s>, to see which units you missed" % PROGRESS, flat)
        self.assertIn("> 90% done", flat)
        self.assertTrue(text.endswith("Thanks\nLi'l Stranger\n"))
        self.assertIn("<%s>!" % HOME, text.splitlines(), "a long URL must stay unbroken on its own line")
        self.assertIn("<%s>," % PROGRESS, text.splitlines())
        self.assertIn("(and new Accomplishment <%s> badges and points towards the Leaderboard <%s>!), but "
                      "we can't give you your well-deserved kudos until you confirm you're really done "
                      "with the class." % (ACCOMPLISHMENTS, LEADERBOARD), flat)

    def test_message_is_multipart_alternative_with_headers(self):
        msg = nudge.build_message(CFG, cand(), "someone@example.com")
        self.assertEqual(msg["To"], "someone@example.com")
        self.assertEqual(msg["From"], "OpenSecurityTraining2 <info@ost2.fyi>")
        self.assertEqual(msg["Reply-To"], "info@ost2.fyi")
        self.assertEqual(msg["Subject"], "You're so close to finishing %s!" % ARCH1005_NAME)
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertEqual([p.get_content_type() for p in msg.iter_parts()], ["text/plain", "text/html"])
        html_part = msg.get_body(("html",)).get_content()
        for url in (HOME, PROGRESS, ACCOMPLISHMENTS, LEADERBOARD):
            self.assertIn(url, html_part)
        self.assertTrue(msg["Message-ID"].endswith("@ost2.fyi>"))

    def test_test_mode_tags_the_subject_only(self):
        live = nudge.build_message(CFG, cand(), "x@example.com")
        test = nudge.build_message(CFG, cand(), "x@example.com", test=True)
        self.assertEqual(test["Subject"], "[TEST] " + live["Subject"])
        self.assertEqual(test.get_body(("html",)).get_content(), live.get_body(("html",)).get_content())

    def test_urls_keep_colon_and_plus_but_quote_the_rest(self):
        self.assertEqual(nudge.course_urls("https://apps.p.ost2.fyi/", ARCH1005), (HOME, PROGRESS))
        home, _ = nudge.course_urls("https://apps.p.ost2.fyi", "course-v1:Org+C d+Run/1")
        self.assertIn("course-v1:Org+C%20d+Run%2F1/home", home)

    def test_links_are_built_from_each_boxs_own_hosts(self):
        dev = dict(CFG, lms_base="https://dev.ost2.fyi/", mfe_base="https://apps.dev.ost2.fyi",
                   image_url="https://dev.ost2.fyi/media/lil-stranger/hello.png")
        self.assertEqual(nudge.email_urls(dev, ARCH1005), {
            "home": "https://apps.dev.ost2.fyi/learning/course/%s/home" % ARCH1005,
            "progress": "https://apps.dev.ost2.fyi/learning/course/%s/progress" % ARCH1005,
            "accomplishments": "https://dev.ost2.fyi/gamma_dashboard/dashboard/",
            "leaderboard": "https://dev.ost2.fyi/gamma_dashboard/leaderboard/"})
        self.assertEqual(nudge.email_urls(CFG, ARCH1005), URLS)
        page = nudge.build_message(dev, cand(), "x@example.com").get_body(("html",)).get_content()
        self.assertNotIn("p.ost2.fyi", page, "a dev email must not point at p")

    def test_mask_email(self):
        self.assertEqual(nudge.mask_email("alice@example.com"), "a***@example.com")


class PlanTests(unittest.TestCase):
    def plan(self, candidates, ledger=(), cap=100, **kw):
        return nudge.build_plan(list(candidates), list(ledger), NOW, cap, **kw)

    def test_closest_to_done_first_then_most_recently_active(self):
        plan = self.plan([cand(1, percent=0.91, days=20), cand(2, percent=0.97, days=500),
                          cand(3, percent=0.91, days=15), cand(4, percent=0.95, days=40)])
        self.assertEqual([c.user_id for c in plan.selected], [2, 4, 3, 1])

    def test_cap_limits_the_run_but_not_the_backlog_count(self):
        plan = self.plan([cand(i, percent=0.9 + i / 1000) for i in range(1, 6)], cap=2)
        self.assertEqual(len(plan.selected), 2)
        self.assertEqual(len(plan.eligible), 5)
        self.assertEqual(plan.stats["eligible"], 5)

    def test_two_runs_of_one_class_keep_the_better_run(self):
        other = "course-v1:OpenSecurityTraining2+Arch1005_IntroRISCV+2025_v2"
        plan = self.plan([cand(1, percent=0.93, course=ARCH1005), cand(1, percent=0.96, course=other)])
        self.assertEqual([c.course_id for c in plan.selected], [other])
        self.assertEqual(plan.stats["other_run_same_class"], 1)

    def test_class_names_compare_ignoring_case_and_spacing(self):
        plan = self.plan([cand(1, percent=0.93), cand(1, course="x", name="  architecture 1005:  RISC-V assembly ")])
        self.assertEqual(len(plan.selected), 1)

    def test_a_learner_is_nudged_about_a_class_only_once_ever(self):
        old = {"ts": (NOW - dt.timedelta(days=400)).isoformat(), "user_id": 1, "course_id": ARCH1005,
               "class": ARCH1005_NAME, "status": "sent"}
        plan = self.plan([cand(1)], [old])
        self.assertEqual(plan.selected, [])
        self.assertEqual(plan.stats["already_nudged"], 1)

    def test_another_run_of_a_nudged_class_is_covered_by_the_class_name(self):
        old = {"ts": NOW.isoformat(), "user_id": 1, "course_id": "some-other-run", "class": ARCH1005_NAME,
               "status": "sent"}
        self.assertEqual(self.plan([cand(1)], [old], min_days_between=0).selected, [])

    def test_refused_addresses_are_not_retried(self):
        gone = {"ts": NOW.isoformat(), "user_id": 1, "course_id": ARCH1005, "class": ARCH1005_NAME,
                "status": "refused"}
        self.assertEqual(self.plan([cand(1)], [gone]).selected, [])

    def test_a_learner_is_not_mailed_again_within_the_spacing_window(self):
        other = cand(1, course="c2", name="Debuggers 1011: Introductory WinDbg")
        recent = {"ts": (NOW - dt.timedelta(days=3)).isoformat(), "user_id": 1, "course_id": "c1",
                  "class": "Some other class", "status": "sent"}
        plan = self.plan([other], [recent])
        self.assertEqual(plan.selected, [])
        self.assertEqual(plan.stats["learner_nudged_recently"], 1)
        recent["ts"] = (NOW - dt.timedelta(days=8)).isoformat()
        self.assertEqual(len(self.plan([other], [recent]).selected), 1)

    def test_one_email_per_learner_per_run_best_class_first(self):
        a = cand(1, percent=0.92)
        b = cand(1, percent=0.96, course="c2", name="Debuggers 1011: Introductory WinDbg")
        plan = self.plan([a, b])
        self.assertEqual([c.course_id for c in plan.selected], ["c2"])
        self.assertEqual(plan.stats["another_class_goes_first"], 1)

    def test_max_inactive_days_drops_the_long_gone(self):
        plan = self.plan([cand(1, days=100), cand(2, days=800)], max_inactive_days=365)
        self.assertEqual([c.user_id for c in plan.selected], [1])
        self.assertEqual(plan.stats["too_long_inactive"], 1)

    def test_test_mode_ignores_the_ledger(self):
        old = {"ts": NOW.isoformat(), "user_id": 1, "course_id": ARCH1005, "class": ARCH1005_NAME,
               "status": "sent"}
        self.assertEqual(len(self.plan([cand(1)], [old], use_ledger=False).selected), 1)


class LedgerTests(unittest.TestCase):
    def test_append_then_load_and_tolerate_a_torn_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = nudge.Ledger(tmp)
            self.assertEqual(ledger.load(), [])
            ledger.append(cand(7), "sent", NOW)
            with open(ledger.path, "a") as handle:
                handle.write('{"user_id": 9, "course_')  # torn write from a crash
            entries = ledger.load()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["user_id"], 7)
            self.assertEqual(entries[0]["status"], "sent")
            self.assertNotIn("email", json.dumps(entries[0]))


class SqlTests(unittest.TestCase):
    def test_thresholds_are_strict_and_in_utc(self):
        sql = nudge.candidates_sql(90, 14)
        self.assertIn("g.percent_grade > 0.9", sql)
        self.assertIn("t.last_activity < UTC_TIMESTAMP() - INTERVAL 14 DAY", sql)
        self.assertIn("g.passed_timestamp IS NULL", sql)
        self.assertNotIn("NOW()", sql)

    def test_filters_are_added_and_unsafe_ids_rejected(self):
        sql = nudge.candidates_sql(92.5, 30, [ARCH1005], [5, "7"])
        self.assertIn("g.percent_grade > 0.925", sql)
        self.assertIn("g.course_id IN ('%s')" % ARCH1005, sql)
        self.assertIn("g.user_id IN (5, 7)", sql)
        with self.assertRaises(ValueError):
            nudge.candidates_sql(90, 14, ["x' OR '1'='1"])

    def test_it_only_reads(self):
        sql = nudge.candidates_sql(90, 14).upper()
        for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE ", "CREATE "):
            self.assertNotIn(word, sql)

    def test_row_parsing(self):
        c = nudge.to_candidate(db_row(5, percent=0.9231, days=20))
        self.assertEqual((c.user_id, c.course_id, c.class_name), (5, ARCH1005, ARCH1005_NAME))
        self.assertEqual(c.last_activity, NOW - dt.timedelta(days=20))
        self.assertEqual(nudge.to_candidate(dict(db_row(), display_name=""))
                         .class_name, ARCH1005)


class ClassifyTests(unittest.TestCase):
    def test_permanent_recipient_errors_only_skip_that_learner(self):
        exc = smtplib.SMTPRecipientsRefused({"a@b.c": (550, b"5.1.1 The email account does not exist")})
        self.assertEqual(nudge.classify_smtp_error(exc), "refused")

    def test_quota_and_rate_limit_replies_stop_the_run(self):
        for code, text in ((550, b"5.4.5 Daily user sending limit exceeded"), (421, b"4.7.0 Try again later"),
                           (452, b"4.5.3 Too many recipients"), (550, b"5.4.5 daily quota")):
            exc = smtplib.SMTPRecipientsRefused({"a@b.c": (code, text)})
            self.assertEqual(nudge.classify_smtp_error(exc), "abort", (code, text))
            self.assertEqual(nudge.classify_smtp_error(smtplib.SMTPDataError(code, text)), "abort")

    def test_systemic_failures_stop_the_run(self):
        for exc in (smtplib.SMTPAuthenticationError(535, b"bad credentials"),
                    smtplib.SMTPSenderRefused(550, b"x", "info@ost2.fyi"), ConnectionResetError(),
                    smtplib.SMTPServerDisconnected()):
            self.assertEqual(nudge.classify_smtp_error(exc), "abort", repr(exc))

    def test_a_5xx_data_rejection_is_the_learners_problem_not_the_runs(self):
        self.assertEqual(nudge.classify_smtp_error(smtplib.SMTPDataError(554, b"5.7.1 rejected")), "refused")


class MailerTests(unittest.TestCase):
    """The real Mailer against a fake smtplib, so wiring mistakes (settings keys, call order) show up here."""

    SETTINGS = {"host": "smtp.example.test", "port": 587, "tls": True, "ssl": False, "user": "u",
                "password": "p", "sender": "info@ost2.fyi"}

    def test_connects_with_starttls_logs_in_and_sends_with_an_explicit_envelope(self):
        client = mock.MagicMock()
        with mock.patch.object(nudge.smtplib, "SMTP", return_value=client) as smtp:
            mailer = nudge.Mailer(dict(self.SETTINGS))
            message = nudge.build_message(CFG, cand(), "xeno@ost2.fyi")
            mailer.send(message, "xeno@ost2.fyi")
            mailer.send(message, "xeno@ost2.fyi")
            mailer.close()
        smtp.assert_called_once_with("smtp.example.test", 587, timeout=60)
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("u", "p")
        self.assertEqual(client.send_message.call_count, 2)
        client.send_message.assert_called_with(message, from_addr="info@ost2.fyi", to_addrs=["xeno@ost2.fyi"])
        client.quit.assert_called_once()

    def test_reconnects_once_when_the_connection_dropped(self):
        dead, fresh = mock.MagicMock(), mock.MagicMock()
        dead.send_message.side_effect = smtplib.SMTPServerDisconnected()
        with mock.patch.object(nudge.smtplib, "SMTP", side_effect=[dead, fresh]):
            nudge.Mailer(dict(self.SETTINGS)).send("msg", "a@b.c")
        fresh.send_message.assert_called_once()

    def test_gives_up_after_the_second_dropped_connection(self):
        clients = [mock.MagicMock(), mock.MagicMock()]
        for client in clients:
            client.send_message.side_effect = ConnectionResetError()
        with mock.patch.object(nudge.smtplib, "SMTP", side_effect=clients):
            with self.assertRaises(ConnectionResetError):
                nudge.Mailer(dict(self.SETTINGS)).send("msg", "a@b.c")

    def test_load_config_hands_the_sender_to_the_smtp_settings(self):
        args = nudge.parser().parse_args([])
        with mock.patch.object(nudge, "tutor_value", lambda tutor, key: CONFIG[key]):
            cfg = nudge.load_config(args, "send")
            dry = nudge.load_config(args, "dry-run")
        self.assertEqual(cfg["smtp"]["sender"], "info@ost2.fyi")
        self.assertEqual((cfg["smtp"]["host"], cfg["smtp"]["port"], cfg["smtp"]["tls"]), ("smtp.example.test", 587, True))
        self.assertEqual(cfg["lms_base"], "https://p.ost2.fyi")
        self.assertEqual(cfg["mfe_base"], "https://apps.p.ost2.fyi")
        self.assertEqual(cfg["image_url"], "https://p.ost2.fyi/media/lil-stranger/hello.png")
        self.assertNotIn("smtp", dry, "a dry run must not even read the SMTP password")

    def test_overrides_win_over_the_boxs_own_settings(self):
        args = nudge.parser().parse_args(["--lms-url", "https://x.test", "--mfe-url", "https://apps.x.test",
                                          "--image-url", "https://x.test/a.png", "--sender", "me@x.test"])
        with mock.patch.object(nudge, "tutor_value", lambda tutor, key: CONFIG[key]):
            cfg = nudge.load_config(args, "dry-run")
        self.assertEqual((cfg["lms_base"], cfg["mfe_base"], cfg["image_url"], cfg["sender"]),
                         ("https://x.test", "https://apps.x.test", "https://x.test/a.png", "me@x.test"))


class FakeMailer:
    instances = []
    behavior = staticmethod(lambda recipient: None)

    def __init__(self, settings):
        self.sent, self.closed = [], False
        FakeMailer.instances.append(self)

    def send(self, message, recipient):
        type(self).behavior(recipient)
        self.sent.append((message, recipient))

    def close(self):
        self.closed = True


CONFIG = {"LMS_HOST": "p.ost2.fyi", "ENABLE_HTTPS": "true", "MFE_HOST": "apps.p.ost2.fyi",
          "CONTACT_EMAIL": "info@ost2.fyi", "PLATFORM_NAME": "OpenSecurityTraining2",
          "MYSQL_ROOT_PASSWORD": "pw", "SMTP_HOST": "smtp.example.test", "SMTP_PORT": "587",
          "SMTP_USE_TLS": "true", "SMTP_USE_SSL": "false", "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p"}


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        FakeMailer.instances = []
        FakeMailer.behavior = staticmethod(lambda recipient: None)
        self.rows = [db_row(1, percent=0.96), db_row(2, percent=0.94), db_row(3, percent=0.92)]
        patches = [
            mock.patch.object(nudge, "tutor_value", lambda tutor, key: CONFIG[key]),
            mock.patch.object(nudge, "mysql_json_rows", lambda args, password, sql: list(self.rows)),
            mock.patch.object(nudge, "Mailer", FakeMailer),
            mock.patch.object(nudge, "check_image", mock.Mock()),
            mock.patch.object(nudge, "utcnow", lambda: NOW),
            mock.patch.object(nudge.time, "sleep", mock.Mock()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.check_image = nudge.check_image

    def go(self, *argv):
        args = nudge.parser().parse_args(["--state-dir", self.tmp.name] + list(argv))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = nudge.run(args)
        return code, out.getvalue()

    def ledger(self):
        return nudge.Ledger(self.tmp.name).load()

    def test_default_is_a_dry_run_that_sends_and_saves_nothing(self):
        code, out = self.go()
        self.assertEqual(code, 0)
        self.assertEqual(FakeMailer.instances, [])
        self.assertEqual(self.ledger(), [])
        self.assertIn("mode=dry-run", out)
        self.assertIn("would nudge user_id=1", out)
        self.assertIn("preview of the first email", out)
        self.assertNotIn("learner1@example.com", out)

    def test_test_mode_sends_one_email_to_the_override_address_only(self):
        code, _ = self.go("--only-to", "xeno@ost2.fyi")
        self.assertEqual(code, 0)
        (mailer,) = FakeMailer.instances
        self.assertEqual([r for _, r in mailer.sent], ["xeno@ost2.fyi"])
        message = mailer.sent[0][0]
        self.assertEqual(message["To"], "xeno@ost2.fyi")
        self.assertTrue(message["Subject"].startswith("[TEST] "))
        self.assertNotIn("learner1@example.com", bytes(message).decode())
        self.assertEqual(self.ledger(), [], "a test must not mark anyone as nudged")
        self.assertTrue(mailer.closed)

    def test_test_mode_can_be_asked_for_more_and_can_target_a_pair(self):
        self.go("--only-to", "xeno@ost2.fyi", "--max-send", "2")
        self.assertEqual([r for _, r in FakeMailer.instances[0].sent], ["xeno@ost2.fyi"] * 2)

    def test_live_run_mails_each_learner_and_records_it(self):
        code, out = self.go("--send")
        self.assertEqual(code, 0)
        mailer = FakeMailer.instances[0]
        self.assertEqual([r for _, r in mailer.sent],
                         ["learner1@example.com", "learner2@example.com", "learner3@example.com"])
        self.assertEqual([(e["user_id"], e["status"]) for e in self.ledger()],
                         [(1, "sent"), (2, "sent"), (3, "sent")])
        self.assertNotIn("[TEST]", mailer.sent[0][0]["Subject"])
        self.assertIn("done: sent=3 refused=0 backlog_left=0", out)

    def test_a_second_live_run_sends_nothing_new(self):
        self.go("--send")
        FakeMailer.instances = []
        code, out = self.go("--send")
        self.assertEqual(code, 0)
        self.assertEqual(FakeMailer.instances, [])
        self.assertIn("already_nudged=3", out)
        self.assertIn("nothing to send", out)

    def test_the_cap_drains_a_backlog_over_several_runs(self):
        self.go("--send", "--max-send", "2")
        self.assertEqual(len(self.ledger()), 2)
        code, out = self.go("--send", "--max-send", "2")
        self.assertEqual(len(self.ledger()), 3)
        self.assertIn("done: sent=1", out)

    def test_quota_reply_stops_the_run_and_keeps_what_was_sent(self):
        def behavior(recipient):
            if recipient == "learner2@example.com":
                raise smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded")
        FakeMailer.behavior = staticmethod(behavior)
        with self.assertRaises(nudge.SendAbort):
            self.go("--send")
        self.assertEqual([e["user_id"] for e in self.ledger()], [1])
        self.assertTrue(FakeMailer.instances[0].closed)

    def test_a_refused_recipient_is_recorded_and_the_run_continues(self):
        def behavior(recipient):
            if recipient == "learner2@example.com":
                raise smtplib.SMTPRecipientsRefused({recipient: (550, b"5.1.1 no such user")})
        FakeMailer.behavior = staticmethod(behavior)
        code, out = self.go("--send")
        self.assertEqual(code, 0)
        self.assertEqual([(e["user_id"], e["status"]) for e in self.ledger()],
                         [(1, "sent"), (2, "refused"), (3, "sent")])
        self.assertIn("sent=2 refused=1", out)

    def test_a_missing_mascot_image_blocks_every_send(self):
        self.check_image.side_effect = RuntimeError("mascot image is not reachable")
        with self.assertRaises(RuntimeError):
            self.go("--send")
        self.assertEqual(FakeMailer.instances, [])
        self.assertEqual(self.ledger(), [])

    def test_send_and_only_to_cannot_be_combined(self):
        with self.assertRaises(SystemExit):
            self.go("--send", "--only-to", "xeno@ost2.fyi")

    def test_main_leaves_a_failure_marker_and_exit_code_1(self):
        FakeMailer.behavior = staticmethod(lambda recipient: (_ for _ in ()).throw(ConnectionResetError()))
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(nudge.main(["--state-dir", self.tmp.name, "--send"]), 1)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, nudge.FAIL_MARKER)))
        FakeMailer.behavior = staticmethod(lambda recipient: None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(nudge.main(["--state-dir", self.tmp.name, "--send"]), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, nudge.FAIL_MARKER)))

    def test_eml_files_can_be_saved_for_inspection(self):
        target = os.path.join(self.tmp.name, "eml")
        self.go("--only-to", "xeno@ost2.fyi", "--save-eml", target)
        self.assertEqual(os.listdir(target), ["nudge-01.eml"])


if __name__ == "__main__":
    unittest.main()
