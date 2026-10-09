"""Run with:  python3 -m unittest -v test_ost2_completion_nudge   (from host-scripts/)"""
import contextlib
import datetime as dt
import io
import json
import os
import smtplib
import sys
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
SETTINGS = "https://p.ost2.fyi/dashboard"
SENTINEL = nudge.UNSUBSCRIBE_SENTINEL
IMAGE = "https://p.ost2.fyi/media/lil-stranger/hello.png"
URLS = {"home": HOME, "progress": PROGRESS, "accomplishments": ACCOMPLISHMENTS, "leaderboard": LEADERBOARD,
        "settings": SETTINGS, "unsubscribe": SENTINEL}
CFG = {"lms_base": "https://p.ost2.fyi", "mfe_base": "https://apps.p.ost2.fyi", "image_url": IMAGE,
       "sender": "info@ost2.fyi", "sender_name": "OpenSecurityTraining2", "lms_host": "p.ost2.fyi"}


def cand(user_id=1, course=ARCH1005, name=ARCH1005_NAME, percent=0.92, days=30, email=None, username=None):
    return nudge.Candidate(user_id, username or "learner%d" % user_id, email or "learner%d@example.com" % user_id,
                           course, name, percent, NOW - dt.timedelta(days=days))


def db_row(user_id=1, course=ARCH1005, name=ARCH1005_NAME, percent=0.92, days=30):
    when = NOW - dt.timedelta(days=days)
    return {"user_id": user_id, "username": "learner%d" % user_id, "email": "learner%d@example.com" % user_id,
            "course_id": course, "display_name": name, "percent": percent,
            "last_activity": when.strftime("%Y-%m-%d %H:%M:%S.000000")}


class RenderTests(unittest.TestCase):
    def html(self, name=ARCH1005_NAME, recipient="someone@example.com", urls=URLS):
        return nudge.render_html(name, urls, IMAGE, "OpenSecurityTraining2", recipient)

    def text(self, name=ARCH1005_NAME, recipient="someone@example.com"):
        return nudge.render_text(name, URLS, "OpenSecurityTraining2", recipient)

    def test_html_carries_the_requested_copy_and_both_links(self):
        page = self.html()
        self.assertIn("Hi! We see that you're &gt; 90% done with the class "
                      + '<a href="%s">%s</a>! That\'s pretty awesome!' % (HOME, ARCH1005_NAME), page)
        self.assertIn('go check out your <a href="%s">Progress</a> page, to see which units you missed, '
                      "and to mark them as done." % PROGRESS, page)
        self.assertIn('a couple of "Mark as complete" buttons', page)
        self.assertIn("Thanks<br>Li'l Stranger", page)

    def test_accomplishment_and_leaderboard_are_links_and_leaderboard_is_capitalized(self):
        page = self.html()
        self.assertIn("get their completion certificates (and new "
                      + '<a href="%s">Accomplishment</a> badges and points towards the ' % ACCOMPLISHMENTS
                      + '<a href="%s">Leaderboard</a>!), but we can\'t give you your well-deserved kudos ' % LEADERBOARD
                      + "until you confirm you're really done with the class.", page)
        self.assertNotIn("the leaderboard", page)

    def test_the_footer_is_the_standard_course_email_footer(self):
        page = self.html(recipient="xeno@ost2.fyi")
        self.assertIn("This email was automatically sent from OpenSecurityTraining2.<br>", page)
        self.assertIn("You are receiving this email at address xeno@ost2.fyi because you are enrolled in "
                      '<a href="%s">%s</a>.<br>' % (HOME, ARCH1005_NAME), page)
        self.assertIn('To stop receiving email like this, update your course email settings '
                      '<a href="%s">here</a>.' % SETTINGS, page)
        self.assertIn('<a href="%s">unsubscribe</a></p>' % SENTINEL, page)
        self.assertLess(page.index("Li'l Stranger</p>"), page.index("This email was automatically sent"),
                        "the footer comes after the sign-off")

    def test_every_link_in_the_html_is_one_of_the_expected_seven(self):
        hrefs = [chunk.split('"')[0] for chunk in self.html().split('<a href="')[1:]]
        self.assertEqual(hrefs, [HOME, PROGRESS, ACCOMPLISHMENTS, LEADERBOARD, HOME, SETTINGS, SENTINEL])

    def test_mascot_is_the_first_thing_in_the_body_and_300_wide(self):
        page = self.html()
        self.assertLess(page.index("<img "), page.index("Hi! We see"))
        self.assertIn('<img src="%s" width="300"' % IMAGE, page)
        self.assertIn("width:300px", page)
        self.assertEqual(page.count("<img "), 1)

    def test_class_names_and_recipients_are_html_escaped(self):
        page = self.html("Attack & Defense <b>", recipient="a&b@example.com")
        self.assertIn(">Attack &amp; Defense &lt;b&gt;</a>", page)
        self.assertIn("a&amp;b@example.com", page)
        self.assertNotIn("<b>", page)

    def test_text_part_has_every_url_the_copy_and_the_footer(self):
        text = self.text(recipient="xeno@ost2.fyi")
        flat = text.replace("\n", " ")
        self.assertIn("%s <%s>! That's pretty awesome!" % (ARCH1005_NAME, HOME), flat)
        self.assertIn("Progress page <%s>, to see which units you missed" % PROGRESS, flat)
        self.assertIn("(and new Accomplishment <%s> badges and points towards the Leaderboard <%s>!), but "
                      "we can't give you your well-deserved kudos until you confirm you're really done "
                      "with the class." % (ACCOMPLISHMENTS, LEADERBOARD), flat)
        self.assertIn("> 90% done", flat)
        self.assertIn("<%s>!" % HOME, text.splitlines(), "a long URL must stay unbroken on its own line")
        footer = text.split("\n----\n")[1].splitlines()
        self.assertEqual(footer, [
            "This email was automatically sent from OpenSecurityTraining2.",
            "You are receiving this email at address xeno@ost2.fyi because you are enrolled in %s" % ARCH1005_NAME,
            "(URL: %s)." % HOME,
            "To stop receiving email like this, update your course email settings at %s." % SETTINGS,
            "Unsubscribe: %s" % SENTINEL])
        self.assertLess(text.index("Li'l Stranger\n\n----"), text.index("This email was automatically"))

    def test_message_parts_for_the_agent(self):
        item = nudge.render_message(CFG, cand(), "someone@example.com")
        self.assertEqual(item["to"], "someone@example.com")
        self.assertEqual(item["from"], "OpenSecurityTraining2 <info@ost2.fyi>")
        self.assertEqual(item["reply_to"], "info@ost2.fyi")
        self.assertEqual(item["subject"], "You're so close to finishing %s!" % ARCH1005_NAME)
        self.assertTrue(item["headers"]["Message-ID"].endswith("@ost2.fyi>"))
        self.assertEqual(item["headers"]["Auto-Submitted"], "auto-generated")
        for url in (HOME, PROGRESS, ACCOMPLISHMENTS, LEADERBOARD, SETTINGS):
            self.assertIn(url, item["html"])
            self.assertIn(url, item["text"])
        self.assertIn("someone@example.com", item["html"])
        self.assertIn(SENTINEL, item["html"])
        self.assertIn(SENTINEL, item["text"])

    def test_test_mode_tags_the_subject_and_shows_the_test_address_not_the_learners(self):
        live = nudge.render_message(CFG, cand(1), "learner1@example.com")
        test = nudge.render_message(CFG, cand(1), "xeno@ost2.fyi", test=True)
        self.assertEqual(test["subject"], "[TEST] " + live["subject"])
        self.assertIn("xeno@ost2.fyi", test["html"])
        self.assertNotIn("learner1@example.com", test["html"] + test["text"])

    def test_links_are_built_from_each_boxs_own_hosts(self):
        dev = dict(CFG, lms_base="https://dev.ost2.fyi/", mfe_base="https://apps.dev.ost2.fyi",
                   image_url="https://dev.ost2.fyi/media/lil-stranger/hello.png")
        self.assertEqual(nudge.email_urls(dev, ARCH1005), {
            "home": "https://apps.dev.ost2.fyi/learning/course/%s/home" % ARCH1005,
            "progress": "https://apps.dev.ost2.fyi/learning/course/%s/progress" % ARCH1005,
            "accomplishments": "https://dev.ost2.fyi/gamma_dashboard/dashboard/",
            "leaderboard": "https://dev.ost2.fyi/gamma_dashboard/leaderboard/",
            "settings": "https://dev.ost2.fyi/dashboard", "unsubscribe": SENTINEL})
        self.assertEqual(nudge.email_urls(CFG, ARCH1005), URLS)
        item = nudge.render_message(dev, cand(), "x@example.com")
        self.assertNotIn("p.ost2.fyi", item["html"] + item["text"], "a dev email must not point at p")

    def test_urls_keep_colon_and_plus_but_quote_the_rest(self):
        self.assertEqual(nudge.course_urls("https://apps.p.ost2.fyi/", ARCH1005), (HOME, PROGRESS))
        home, _ = nudge.course_urls("https://apps.p.ost2.fyi", "course-v1:Org+C d+Run/1")
        self.assertIn("course-v1:Org+C%20d+Run%2F1/home", home)

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
            self.assertNotIn("example.com", json.dumps(entries[0]), "ids only, never an address")


class SqlTests(unittest.TestCase):
    def test_thresholds_are_strict_and_in_utc(self):
        sql = nudge.candidates_sql(90, 14)
        self.assertIn("g.percent_grade > 0.9", sql)
        self.assertIn("t.last_activity < UTC_TIMESTAMP() - INTERVAL 14 DAY", sql)
        self.assertIn("g.passed_timestamp IS NULL", sql)
        self.assertIn("bulk_email_optout", sql, "the standard course-email opt-out is honoured")
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
        self.assertEqual((c.user_id, c.username, c.course_id, c.class_name), (5, "learner5", ARCH1005, ARCH1005_NAME))
        self.assertEqual(c.last_activity, NOW - dt.timedelta(days=20))
        self.assertEqual(nudge.to_candidate(dict(db_row(), display_name="")).class_name, ARCH1005)


# ------------------------------------------------------------------------------------------------
# The delivery agent: the source text that runs inside the LMS container, exec'd here with stand-ins.


def reply_error(kind, code, text, *recipients):
    text = text.encode()
    if kind == "data":
        return smtplib.SMTPDataError(code, text)
    if kind == "sender":
        return smtplib.SMTPSenderRefused(code, text, "info@ost2.fyi")
    if kind == "connect":
        return smtplib.SMTPConnectError(code, text)
    return smtplib.SMTPRecipientsRefused({(recipients or ("a@b.c",))[0]: (code, text)})


LIMITER_SLOT = lambda: smtplib.SMTPSenderRefused(  # noqa: E731 - exactly what RateLimitedEmailBackend raises
    451, b"4.7.0 OST2 sending budget: rate limit 30/min, next slot in 20.0s", "")
LIMITER_DAILY = lambda: smtplib.SMTPSenderRefused(  # noqa: E731
    451, b"4.7.0 OST2 sending budget: daily cap of 1800 reached", "")


class FakeRedis:
    def __init__(self, used=0, down=False):
        self.used, self.down, self.keys = used, down, []

    def get(self, key):
        if self.down:
            raise ConnectionError("redis is down")
        self.keys.append(key)
        return str(self.used).encode()


class FakeConn:
    """Stands in for RateLimitedEmailBackend; `script` holds what each send does (exception or None)."""
    daily_cap, rate_per_min, max_block, fail_open, key_prefix = 1800, 30, 15, True, "ost2:email"

    def __init__(self, script=(), used=0, redis_down=False):
        self._redis = FakeRedis(used, redis_down)
        self.script, self.sent, self.opened, self.closed = list(script), [], 0, 0

    @property
    def redis(self):
        return self._redis

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1

    def send_messages(self, messages):
        action = self.script.pop(0) if self.script else None
        if action is not None:
            raise action
        self.sent.extend(messages)
        self._redis.used += len(messages)
        return len(messages)


def route_class(module, name="EmailBackend"):
    """A mail backend that has no limiter, living in `module` (e.g. the plain SMTP or file backend)."""
    return type(name, (), {"__module__": module, "open": lambda self: None, "close": lambda self: None,
                           "send_messages": lambda self, messages: len(messages)})


class FakeMessage:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def message(self):
        return self

    def as_bytes(self):
        return (self.subject + self.text).encode()


class FakeDeps:
    def __init__(self, conn, users=("learner1", "learner2", "learner3", "Xeno"), email_map=None):
        self.conn, self.users, self.email_map = conn, set(users), email_map or {}
        self.requested_backend = "unset"

    def get_connection(self, backend):
        self.requested_backend = backend
        return self.conn

    def user_exists(self, username):
        return username in self.users

    def username_for_email(self, email):
        return self.email_map.get(email.lower())

    def unsubscribe_link(self, username, course_id):
        return "https://p.ost2.fyi/bulk_email/email/optout/TOKEN-%s/%s/" % (username, course_id)

    def new_message(self, **fields):
        return FakeMessage(**fields)


def agent_item(idx=0, username="learner1", to="learner1@example.com", **extra):
    item = {"idx": idx, "username": username, "course_id": ARCH1005, "to": to,
            "from": "OpenSecurityTraining2 <info@ost2.fyi>", "reply_to": "info@ost2.fyi",
            "subject": "Subject %d" % idx, "text": "text for %s\nUnsubscribe: %s\n" % (to, SENTINEL),
            "html": '<a href="%s">unsubscribe</a> %s' % (SENTINEL, to),
            "headers": {"Message-ID": "<%d@ost2.fyi>" % idx}}
    item.update(extra)
    return item


def agent_payload(items, action="send", **options):
    opts = {"backend": None, "delay": 2.0, "yield_above": 0.5, "max_runtime": 1800, "sentinel": SENTINEL}
    opts.update(options)
    return {"action": action, "options": opts, "items": items}


class AgentTests(unittest.TestCase):
    def run_agent(self, payload, conn, deps=None, namespace=None):
        """Exec the agent source and run it; returns (events, sleeps, deps)."""
        deps = deps or FakeDeps(conn)
        ns = dict(namespace or {})
        exec(compile(nudge.AGENT_SOURCE, "<agent>", "exec"), ns)
        clock = [1_000_000.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ns["nudge_agent"](payload, deps=deps, sleep=sleep, clock=lambda: clock[0])
        events = []
        for line in out.getvalue().splitlines():
            tag, _, data = line[len("NUDGE_"):].partition(" ")
            events.append((tag, json.loads(data)))
        return events, sleeps, deps

    def summary(self, events):
        return [data for tag, data in events if tag == "SUMMARY"][-1]

    def results(self, events):
        return [(data["idx"], data["status"]) for tag, data in events if tag == "RESULT"]

    def test_sends_each_item_through_the_route_with_the_per_recipient_unsubscribe_link(self):
        conn = FakeConn()
        items = [agent_item(0), agent_item(1, "learner2", "learner2@example.com")]
        events, sleeps, _ = self.run_agent(agent_payload(items), conn)
        self.assertEqual(self.results(events), [(0, "sent"), (1, "sent")])
        self.assertEqual([m.to for m in conn.sent], ["learner1@example.com", "learner2@example.com"])
        first = conn.sent[0]
        link = "https://p.ost2.fyi/bulk_email/email/optout/TOKEN-learner1/%s/" % ARCH1005
        self.assertIn("Unsubscribe: " + link, first.text)
        self.assertIn('<a href="%s">unsubscribe</a>' % link, first.html)
        self.assertNotIn(SENTINEL, first.text + first.html)
        self.assertIn("TOKEN-learner2", conn.sent[1].text)
        self.assertEqual(first.headers, {"Message-ID": "<0@ost2.fyi>"})
        self.assertEqual(first.reply_to, "info@ost2.fyi")
        self.assertIs(first.connection, conn)
        route = dict(events)["ROUTE"]
        self.assertEqual((route["kind"], route["rate_per_min"], route["daily_cap"], route["used_today"]),
                         ("limited", 30, 1800, 0))
        summary = self.summary(events)
        self.assertEqual((summary["sent"], summary["stopped"], summary["used_end"], summary["daily_cap"]),
                         (2, None, 2, 1800))
        self.assertEqual((conn.opened, conn.closed), (1, 1), "one connection for the whole run")

    def test_the_server_wide_counter_key_is_todays_utc_bucket(self):
        conn = FakeConn()
        self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual(conn.redis.keys[0], "ost2:email:daily:%d" % (1_000_000 // 86400))

    def test_keeps_the_minimum_gap_between_its_own_messages_but_not_after_the_last(self):
        events, sleeps, _ = self.run_agent(agent_payload([agent_item(0), agent_item(1), agent_item(2)]), FakeConn())
        self.assertEqual(sleeps, [2.0, 2.0])
        self.assertEqual(self.summary(events)["sent"], 3)

    def test_yields_to_other_mail_when_the_server_is_past_its_share_of_the_daily_cap(self):
        conn = FakeConn(used=900)
        events, _, _ = self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual(conn.sent, [])
        summary = self.summary(events)
        self.assertEqual(summary["stopped"], "yield_budget")
        self.assertIn("900 of 1800", summary["detail"])
        self.assertIn("yield_budget", nudge.EXPECTED_STOPS)

    def test_the_yield_check_runs_before_every_message_so_other_senders_count(self):
        conn = FakeConn(used=898)
        events, _, _ = self.run_agent(agent_payload([agent_item(i) for i in range(3)]), conn)
        self.assertEqual(len(conn.sent), 2)
        self.assertEqual(self.summary(events)["stopped"], "yield_budget")

    def test_a_slot_defer_from_the_limiter_is_waited_out_then_retried(self):
        conn = FakeConn([LIMITER_SLOT(), None])
        events, sleeps, _ = self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual(self.results(events), [(0, "sent")])
        self.assertEqual(sleeps, [5])
        self.assertIn(("BACKOFF", {"why": "the server's mail budget has no slot free", "seconds": 5}), events)

    def test_a_server_busy_with_other_mail_ends_the_run_after_the_backoffs_are_used_up(self):
        conn = FakeConn([LIMITER_SLOT()] * 5)
        events, sleeps, _ = self.run_agent(agent_payload([agent_item(0), agent_item(1)]), conn)
        self.assertEqual(sleeps, [5, 15, 45, 90])
        self.assertEqual(conn.sent, [])
        self.assertEqual(self.summary(events)["stopped"], "rate_defer")

    def test_the_daily_cap_stops_the_run_without_hammering_the_limiter(self):
        conn = FakeConn([LIMITER_DAILY()])
        events, sleeps, _ = self.run_agent(agent_payload([agent_item(0), agent_item(1)]), conn)
        self.assertEqual((sleeps, conn.sent), ([], []))
        self.assertEqual(self.summary(events)["stopped"], "daily_cap")

    def test_a_gmail_421_backs_off_reconnects_and_retries_whichever_smtp_stage_it_came_at(self):
        for kind in ("data", "sender", "rcpt", "connect"):
            conn = FakeConn([reply_error(kind, 421, "4.7.0 Try again later, closing connection. (MAIL)"), None])
            events, sleeps, _ = self.run_agent(agent_payload([agent_item()]), conn)
            self.assertEqual(self.results(events), [(0, "sent")], kind)
            self.assertEqual(sleeps, [60], kind)
            self.assertEqual((conn.closed, conn.opened), (2, 2), "%s: reconnected, then closed at the end" % kind)

    def test_gmail_throttling_that_does_not_clear_ends_the_run_not_the_job(self):
        conn = FakeConn([reply_error("data", 421, "4.7.0 Try again later")] * 4)
        events, sleeps, _ = self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual(sleeps, [60, 300, 900])
        self.assertEqual(self.summary(events)["stopped"], "gmail_throttle")
        self.assertIn("gmail_throttle", nudge.EXPECTED_STOPS)

    def test_a_gmail_daily_quota_reply_stops_the_run_immediately(self):
        for kind in ("data", "sender", "rcpt"):
            conn = FakeConn([reply_error(kind, 550, "5.4.5 Daily user sending quota exceeded.")])
            events, sleeps, _ = self.run_agent(agent_payload([agent_item(0), agent_item(1)]), conn)
            self.assertEqual((sleeps, conn.sent), ([], []), kind)
            self.assertEqual(self.summary(events)["stopped"], "gmail_quota", kind)

    def test_a_dropped_connection_is_reopened_twice_then_reported(self):
        conn = FakeConn([smtplib.SMTPServerDisconnected("gone"), None])
        events, sleeps, _ = self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual((self.results(events), sleeps), ([(0, "sent")], [2]))
        conn = FakeConn([smtplib.SMTPServerDisconnected("gone")] * 3)
        events, _, _ = self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual(self.summary(events)["stopped"], "error")

    def test_a_permanent_recipient_error_refuses_that_learner_and_scrubs_the_address(self):
        conn = FakeConn([reply_error("rcpt", 550, "5.1.1 The email account that you tried to reach does not exist",
                                     "learner1@example.com"), None])
        items = [agent_item(0), agent_item(1, "learner2", "learner2@example.com")]
        events, _, _ = self.run_agent(agent_payload(items), conn)
        self.assertEqual(self.results(events), [(0, "refused"), (1, "sent")])
        detail = [d for t, d in events if t == "RESULT"][0]["detail"]
        self.assertNotIn("learner1@example.com", detail)
        self.assertIn("<email>", detail)
        self.assertEqual(self.summary(events)["stopped"], None)

    def test_five_refusals_in_a_row_mean_something_is_wrong(self):
        conn = FakeConn([reply_error("data", 554, "5.7.1 rejected")] * 6)
        events, _, _ = self.run_agent(agent_payload([agent_item(i) for i in range(6)]), conn)
        self.assertEqual(self.summary(events)["stopped"], "refusals")
        self.assertEqual(self.summary(events)["refused"], 5)

    def test_bad_credentials_or_a_rejected_sender_are_fatal_not_retried(self):
        for exc in (smtplib.SMTPAuthenticationError(535, b"bad credentials"),
                    reply_error("sender", 550, "5.7.1 sender not allowed")):
            conn = FakeConn([exc])
            events, sleeps, _ = self.run_agent(agent_payload([agent_item()]), conn)
            self.assertEqual((self.summary(events)["stopped"], sleeps), ("error", []))
            self.assertNotIn("error", nudge.EXPECTED_STOPS)

    def test_a_real_smtp_route_without_the_limiter_is_never_used(self):
        conn = route_class("django.core.mail.backends.smtp")()
        events, _, _ = self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual(dict(events)["ROUTE"]["kind"], "unlimited")
        self.assertEqual(self.summary(events)["stopped"], "route_unlimited")
        self.assertEqual(self.results(events), [])

    def test_a_file_route_still_runs_but_says_nothing_is_delivered(self):
        conn = route_class("django.core.mail.backends.filebased")()
        events, _, _ = self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual(dict(events)["ROUTE"]["kind"], "files")
        self.assertEqual(self.summary(events)["kind"], "files")
        self.assertEqual(self.results(events), [(0, "sent")])

    def test_if_the_limiters_redis_is_down_nothing_is_sent_unpaced(self):
        conn = FakeConn(redis_down=True)
        events, _, _ = self.run_agent(agent_payload([agent_item()]), conn)
        self.assertEqual(conn.sent, [])
        self.assertEqual(self.summary(events)["stopped"], "limiter_unreachable")

    def test_redis_going_down_mid_run_also_stops_before_the_next_message(self):
        conn = FakeConn()
        original = conn.send_messages

        def send_then_break(messages):
            count = original(messages)
            conn._redis.down = True
            return count
        conn.send_messages = send_then_break
        events, _, _ = self.run_agent(agent_payload([agent_item(0), agent_item(1)]), conn)
        self.assertEqual(len(conn.sent), 1)
        self.assertEqual(self.summary(events)["stopped"], "limiter_unreachable")

    def test_an_item_without_an_account_is_skipped_not_sent(self):
        conn = FakeConn()
        events, _, _ = self.run_agent(agent_payload([agent_item(0, "ghost"), agent_item(1)]), conn)
        self.assertEqual(self.results(events), [(0, "skipped"), (1, "sent")])
        self.assertEqual(self.summary(events)["skipped"], 1)

    def test_test_mode_builds_the_unsubscribe_link_for_the_account_that_owns_the_test_address(self):
        conn = FakeConn()
        deps = FakeDeps(conn, email_map={"xeno@ost2.fyi": "Xeno"})
        item = agent_item(0, username="", to="xeno@ost2.fyi", lookup_email="xeno@ost2.fyi")
        events, _, _ = self.run_agent(agent_payload([item]), conn, deps)
        self.assertEqual(self.results(events), [(0, "sent")])
        self.assertIn("TOKEN-Xeno", conn.sent[0].text)
        self.assertNotIn("learner1", conn.sent[0].text)
        conn2 = FakeConn()
        events, _, _ = self.run_agent(agent_payload([item]), conn2, FakeDeps(conn2))
        self.assertEqual((self.results(events), conn2.sent), ([(0, "skipped")], []))

    def test_a_backend_override_is_handed_to_django(self):
        _, _, deps = self.run_agent(agent_payload([agent_item()], backend="openedx.core.lib.X"), FakeConn())
        self.assertEqual(deps.requested_backend, "openedx.core.lib.X")
        _, _, deps = self.run_agent(agent_payload([agent_item()]), FakeConn())
        self.assertIsNone(deps.requested_backend, "default: the box's own EMAIL_BACKEND")

    def test_preflight_sends_nothing_but_builds_the_real_message_and_masks_the_token(self):
        conn = FakeConn(used=12)
        events, _, _ = self.run_agent(agent_payload([agent_item()], action="preflight"), conn)
        self.assertEqual((conn.sent, conn.opened), ([], 0))
        check = dict(events)["UNSUB_CHECK"]
        self.assertTrue(check["ok"])
        self.assertIn("/optout/<token>/", check["example"])
        self.assertNotIn("TOKEN-learner1", check["example"])
        self.assertGreater(check["message_bytes"], 10)
        self.assertEqual(dict(events)["ROUTE"]["used_today"], 12)
        self.assertEqual(self.summary(events)["stopped"], None)

    def test_preflight_without_an_account_is_a_problem(self):
        events, _, _ = self.run_agent(agent_payload([agent_item(username="ghost")], action="preflight"), FakeConn())
        self.assertEqual(self.summary(events)["stopped"], "error")

    def test_the_run_stops_by_itself_at_the_time_limit(self):
        events, _, _ = self.run_agent(agent_payload([agent_item(i) for i in range(5)], max_runtime=5), FakeConn())
        self.assertEqual(self.summary(events)["stopped"], "time_limit")
        self.assertEqual(self.summary(events)["sent"], 3)  # sends at t=0, 2, 4; at t=6 it is over

    def test_a_backoff_that_would_overrun_the_time_limit_is_not_started(self):
        conn = FakeConn([reply_error("data", 421, "4.7.0 Try again later")])
        events, sleeps, _ = self.run_agent(agent_payload([agent_item()], max_runtime=30), conn)
        self.assertEqual((sleeps, self.summary(events)["stopped"]), ([], "time_limit"))

    def test_if_the_host_stops_listening_nothing_more_is_sent(self):
        def broken_print(*args, **kwargs):
            raise BrokenPipeError()
        conn = FakeConn()
        events, _, _ = self.run_agent(agent_payload([agent_item()]), conn, namespace={"print": broken_print})
        self.assertEqual((conn.sent, events), ([], []))

    def test_classification(self):
        agent = {}
        exec(compile(nudge.AGENT_SOURCE, "<agent>", "exec"), agent)
        classify = agent["classify"]
        cases = [
            (LIMITER_SLOT(), "limiter_slot"), (LIMITER_DAILY(), "limiter_daily"),
            (reply_error("data", 421, "4.7.0 Try again later"), "gmail_throttle"),
            (reply_error("sender", 421, "4.7.28 rate limited"), "gmail_throttle"),
            (reply_error("rcpt", 452, "4.5.3 Too many recipients"), "gmail_throttle"),
            (reply_error("connect", 421, "4.7.0 Try again later"), "gmail_throttle"),
            (reply_error("data", 550, "5.4.5 Daily user sending quota exceeded."), "gmail_quota"),
            (reply_error("sender", 550, "5.4.5 Daily user sending limit exceeded"), "gmail_quota"),
            (reply_error("rcpt", 550, "5.1.1 no such user"), "refused"),
            (reply_error("data", 554, "5.7.1 spam"), "refused"),
            (reply_error("sender", 550, "5.7.1 sender not allowed"), "fatal"),
            (smtplib.SMTPAuthenticationError(535, b"x"), "fatal"),
            (smtplib.SMTPServerDisconnected(), "disconnected"), (ConnectionResetError(), "disconnected"),
            (TimeoutError(), "disconnected"), (ValueError("boom"), "fatal"),
        ]
        for exc, expected in cases:
            self.assertEqual(classify(exc), expected, repr(exc))


# A stand-in for the LMS container: stub Django, print some start-up noise, then run stdin like
# `manage.py lms shell -c "import sys; exec(sys.stdin.read())"` does.
FAKE_CONTAINER = r'''
import os, sys, types
print("2026-10-09 11:15:40,320 WARNING 120 [py.warnings] DeprecationWarning: pkg_resources is deprecated")
sent_log = os.environ["NUDGE_TEST_SENT"]

class Redis:
    def get(self, key):
        return b"7"

class Conn:
    daily_cap, rate_per_min, max_block, fail_open, key_prefix = 1800, 30, 15, True, "ost2:email"
    redis = Redis()
    def open(self): pass
    def close(self): pass
    def send_messages(self, messages):
        with open(sent_log, "a") as handle:
            for message in messages:
                handle.write(message.to[0] + "\n")
        return len(messages)

def module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod

class Message:
    def __init__(self, subject, body, from_email, to, reply_to, headers, connection):
        self.to = to
    def attach_alternative(self, html, kind): pass
    def message(self):
        return types.SimpleNamespace(as_bytes=lambda: b"mime")

class Users:
    def filter(self, **kw): return self
    def exists(self): return True
    def values_list(self, *a, **k): return self
    def first(self): return "Xeno"

module("django"); module("django.contrib"); module("django.core")
module("django.contrib.auth", get_user_model=lambda: types.SimpleNamespace(objects=Users()))
module("django.core.mail", get_connection=lambda backend=None: Conn(), EmailMultiAlternatives=Message)
module("lms"); module("lms.djangoapps"); module("lms.djangoapps.bulk_email")
module("lms.djangoapps.bulk_email.api", get_unsubscribed_link=lambda username, course: "https://x/optout/TOK/%s/" % course)
exec(sys.stdin.read())
'''


class AgentProcessTests(unittest.TestCase):
    """The real run_agent (stdin feed, line streaming, noise, failure) against a local fake container."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sent_log = os.path.join(self.tmp.name, "sent.txt")
        self.args = nudge.parser().parse_args(["--max-runtime", "60"])

    def start(self, script=FAKE_CONTAINER):
        patches = [mock.patch.object(nudge, "agent_command", lambda args: [sys.executable, "-c", script]),
                   mock.patch.dict(os.environ, {"NUDGE_TEST_SENT": self.sent_log})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_events_stream_back_through_a_real_pipe_and_startup_noise_is_ignored(self):
        self.start()
        items = [agent_item(0), agent_item(1, "learner2", "learner2@example.com")]
        events = list(nudge.run_agent(self.args, agent_payload(items, delay=0)))
        self.assertEqual([tag for tag, _ in events], ["ROUTE", "RESULT", "RESULT", "SUMMARY"])
        self.assertEqual(events[0][1]["kind"], "limited")
        self.assertEqual(events[-1][1]["sent"], 2)
        with open(self.sent_log) as handle:
            self.assertEqual(handle.read().split(), ["learner1@example.com", "learner2@example.com"])

    def test_the_generated_source_really_runs_the_payload_on_its_own(self):
        self.start()
        events = list(nudge.run_agent(self.args, agent_payload([agent_item()], action="preflight")))
        self.assertEqual([tag for tag, _ in events], ["ROUTE", "UNSUB_CHECK", "SUMMARY"])
        self.assertIn("/optout/<token>/", events[1][1]["example"])

    def test_an_agent_that_dies_without_a_summary_is_an_error_showing_its_last_output(self):
        self.start("print('Traceback: the lms container is not healthy'); raise SystemExit(3)")
        with self.assertRaises(RuntimeError) as caught:
            list(nudge.run_agent(self.args, agent_payload([agent_item()])))
        self.assertIn("without a summary", str(caught.exception))
        self.assertIn("lms container is not healthy", str(caught.exception))
        self.assertIn("exit 3", str(caught.exception))

    def test_a_big_payload_does_not_deadlock_the_pipe(self):
        self.start()
        items = [agent_item(i, text="x" * 20000 + SENTINEL, html="y" * 20000 + SENTINEL) for i in range(30)]
        events = list(nudge.run_agent(self.args, agent_payload(items, delay=0)))
        self.assertEqual(events[-1][1]["sent"], 30)

    def test_the_command_runs_manage_py_in_the_lms_container_reading_stdin_explicitly(self):
        args = nudge.parser().parse_args(["--lms-container", "my-lms-1"])
        command = nudge.agent_command(args)
        self.assertEqual(command[1:5], ["exec", "-i", "my-lms-1", "./manage.py"])
        self.assertEqual(command[5:7], ["lms", "shell"])
        self.assertEqual(command[-2:], ["-c", "import sys; exec(sys.stdin.read())"])


CONFIG = {"LMS_HOST": "p.ost2.fyi", "ENABLE_HTTPS": "true", "MFE_HOST": "apps.p.ost2.fyi",
          "CONTACT_EMAIL": "info@ost2.fyi", "PLATFORM_NAME": "OpenSecurityTraining2",
          "MYSQL_ROOT_PASSWORD": "pw"}

LIMITED = {"kind": "limited", "backend": "openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend",
           "rate_per_min": 30, "daily_cap": 1800, "max_block": 15, "fail_open": True, "used_today": 10}


def scripted_agent(results=None, route=LIMITED, stopped=None, detail="", stop_after=None, used_end=14):
    """A stand-in for run_agent: yields what the real agent would for the payload it is given."""
    def fake_agent(args, payload):
        fake_agent.payloads.append(payload)
        yield "ROUTE", dict(route)
        sent = 0
        for item in payload["items"]:
            if stop_after is not None and sent >= stop_after:
                break
            status = (results or {}).get(item["idx"], "sent")
            yield "RESULT", {"idx": item["idx"], "status": status, "detail": "d" if status != "sent" else ""}
            sent += 1
        yield "SUMMARY", {"sent": sent, "refused": 0, "skipped": 0, "stopped": stopped, "detail": detail,
                          "kind": route["kind"], "used_end": used_end, "daily_cap": 1800}
    fake_agent.payloads = []
    return fake_agent


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rows = [db_row(1, percent=0.96), db_row(2, percent=0.94), db_row(3, percent=0.92)]
        self.agent = scripted_agent()
        self.check_image = mock.Mock()
        patches = [
            mock.patch.object(nudge, "tutor_value", lambda tutor, key: CONFIG[key]),
            mock.patch.object(nudge, "mysql_json_rows", lambda args, password, sql: list(self.rows)),
            mock.patch.object(nudge, "run_agent", lambda args, payload: self.agent(args, payload)),
            mock.patch.object(nudge, "check_image", self.check_image),
            mock.patch.object(nudge, "utcnow", lambda: NOW),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def go(self, *argv):
        args = nudge.parser().parse_args(["--state-dir", self.tmp.name] + list(argv))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = nudge.run(args)
        return code, out.getvalue()

    def ledger(self):
        return nudge.Ledger(self.tmp.name).load()

    def reset_ledger(self):
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(self.tmp.name, nudge.LEDGER))

    def test_default_is_a_dry_run_that_preflights_the_route_and_sends_and_saves_nothing(self):
        code, out = self.go()
        self.assertEqual(code, 0)
        (payload,) = self.agent.payloads
        self.assertEqual(payload["action"], "preflight")
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(self.ledger(), [])
        self.assertIn("mode=dry-run", out)
        self.assertIn("would nudge user_id=1", out)
        self.assertIn("mail route: openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend", out)
        self.assertIn("shared limiter 30/min, daily cap 1800, nudges yield above 900 sent/day", out)
        self.assertIn("the whole server has sent 10 today", out)
        self.assertIn("Unsubscribe: https://p.ost2.fyi/bulk_email/email/optout/<token>/%s/" % ARCH1005, out)
        self.assertNotIn(SENTINEL, out)
        self.assertNotIn("learner1@example.com", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, nudge.LOCK)), "a dry run takes no lock")

    def test_dry_run_can_write_the_html_preview_and_reports_a_preflight_problem_with_exit_1(self):
        target = os.path.join(self.tmp.name, "preview.html")
        self.agent = scripted_agent(stopped="route_unlimited", detail="no limiter")
        code, out = self.go("--preview-html", target)
        self.assertEqual(code, 1)
        self.assertIn("PRE-FLIGHT PROBLEM: route_unlimited", out)
        with open(target) as handle:
            page = handle.read()
        self.assertIn("/optout/<token>/", page)
        self.assertNotIn(SENTINEL, page)

    def test_test_mode_delivers_one_email_to_the_override_address_and_leaves_no_trace(self):
        backend = "openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend"
        code, out = self.go("--only-to", "xeno@ost2.fyi", "--unsub-as", "Xeno", "--mail-backend", backend)
        self.assertEqual(code, 0)
        (payload,) = self.agent.payloads
        self.assertEqual(payload["action"], "send")
        self.assertEqual(payload["options"]["backend"], backend)
        (item,) = payload["items"]
        self.assertEqual((item["to"], item["username"], item["lookup_email"]),
                         ("xeno@ost2.fyi", "Xeno", "xeno@ost2.fyi"))
        self.assertTrue(item["subject"].startswith("[TEST] "))
        self.assertNotIn("learner1@example.com", json.dumps(item))
        self.assertIn("xeno@ost2.fyi", item["html"])
        self.assertIn(SENTINEL, item["html"])
        self.assertEqual(self.ledger(), [], "a test must not mark anyone as nudged")
        self.assertIn("TEST-sent to xeno@ost2.fyi (not recorded)", out)

    def test_test_mode_without_unsub_as_looks_the_account_up_by_the_address(self):
        self.go("--only-to", "xeno@ost2.fyi")
        (item,) = self.agent.payloads[0]["items"]
        self.assertEqual((item["username"], item["lookup_email"]), ("", "xeno@ost2.fyi"))
        self.assertIsNone(self.agent.payloads[0]["options"]["backend"])

    def test_the_mail_backend_override_and_unsub_as_are_test_only(self):
        for argv in (["--send", "--mail-backend", "x.Y"], ["--mail-backend", "x.Y"], ["--unsub-as", "Xeno"]):
            with self.assertRaises(SystemExit):
                self.go(*argv)
        self.assertEqual(self.agent.payloads, [])

    def test_live_run_mails_each_learner_with_their_own_unsubscribe_account_and_records_it(self):
        code, out = self.go("--send")
        self.assertEqual(code, 0)
        (payload,) = self.agent.payloads
        self.assertEqual([(i["to"], i["username"]) for i in payload["items"]],
                         [("learner1@example.com", "learner1"), ("learner2@example.com", "learner2"),
                          ("learner3@example.com", "learner3")])
        self.assertNotIn("lookup_email", payload["items"][0])
        self.assertFalse(payload["items"][0]["subject"].startswith("[TEST]"))
        self.assertIn("learner1@example.com", payload["items"][0]["html"], "the footer shows where it was sent")
        self.assertEqual([(e["user_id"], e["status"]) for e in self.ledger()], [(1, "sent"), (2, "sent"), (3, "sent")])
        self.assertIn("done: sent=3 refused=0 skipped=0 backlog_left=0 server_sent_today=14/1800", out)
        self.check_image.assert_called_once()

    def test_the_run_options_reach_the_agent(self):
        self.go("--send", "--delay", "3.5", "--yield-above", "0.25", "--max-runtime", "600")
        options = self.agent.payloads[0]["options"]
        self.assertEqual((options["delay"], options["yield_above"], options["max_runtime"], options["sentinel"]),
                         (3.5, 0.25, 600, SENTINEL))

    def test_defaults_are_one_mail_per_two_seconds_and_a_half_share_of_the_cap(self):
        self.go("--send")
        options = self.agent.payloads[0]["options"]
        self.assertEqual((options["delay"], options["yield_above"]), (2.0, 0.5))

    def test_a_second_live_run_sends_nothing_new(self):
        self.go("--send")
        self.agent.payloads.clear()
        code, out = self.go("--send")
        self.assertEqual(code, 0)
        self.assertEqual(self.agent.payloads, [])
        self.assertIn("already_nudged=3", out)
        self.assertIn("nothing to send", out)

    def test_the_cap_drains_a_backlog_over_several_runs(self):
        self.go("--send", "--max-send", "2")
        self.assertEqual(len(self.agent.payloads[0]["items"]), 2)
        self.assertEqual(len(self.ledger()), 2)
        code, out = self.go("--send", "--max-send", "2")
        self.assertEqual(len(self.ledger()), 3)
        self.assertIn("done: sent=1", out)

    def test_a_box_whose_mail_goes_to_files_delivers_nothing_and_records_nothing(self):
        self.agent = scripted_agent(route={"kind": "files", "backend": "django.core.mail.backends.filebased.EmailBackend"})
        code, out = self.go("--send")
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger(), [], "dev's rehearsal must not mark learners as nudged")
        self.assertIn("NOTHING IS DELIVERED from here", out)
        self.assertIn("WRITTEN TO A FILE, not delivered (not recorded)", out)
        self.assertIn("backlog_left=3", out)

    def test_running_out_of_budget_is_a_normal_early_stop_that_keeps_what_was_sent(self):
        for reason in sorted(nudge.EXPECTED_STOPS):
            self.agent = scripted_agent(stopped=reason, detail="the budget", stop_after=1)
            self.reset_ledger()
            code, out = self.go("--send")
            self.assertEqual(code, 0, reason)
            self.assertIn("stopped early (%s: the budget): the rest resumes at the next run" % reason, out)
            self.assertEqual([e["user_id"] for e in self.ledger()], [1], reason)
            self.assertFalse(os.path.exists(os.path.join(self.tmp.name, nudge.FAIL_MARKER)), reason)

    def test_a_problem_stop_fails_the_job_loudly(self):
        for reason in ("route_unlimited", "limiter_unreachable", "error", "refusals", "host_gone"):
            self.agent = scripted_agent(stopped=reason, detail="trouble", stop_after=0)
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                code = nudge.main(["--state-dir", self.tmp.name, "--send"])
            self.assertEqual(code, 1, reason)
            marker = os.path.join(self.tmp.name, nudge.FAIL_MARKER)
            self.assertTrue(os.path.exists(marker), reason)
            with open(marker) as handle:
                self.assertIn(reason, handle.read())
        self.agent = scripted_agent()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(nudge.main(["--state-dir", self.tmp.name, "--send"]), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, nudge.FAIL_MARKER)))

    def test_refused_learners_are_recorded_so_they_are_not_retried_and_skipped_ones_are_not(self):
        self.agent = scripted_agent(results={1: "refused", 2: "skipped"})
        code, out = self.go("--send")
        self.assertEqual(code, 0)
        self.assertEqual([(e["user_id"], e["status"]) for e in self.ledger()], [(1, "sent"), (2, "refused")])
        self.assertIn("skipped user_id=3", out)

    def test_a_missing_mascot_image_blocks_every_send(self):
        self.check_image.side_effect = RuntimeError("mascot image is not reachable")
        with self.assertRaises(RuntimeError):
            self.go("--send")
        self.assertEqual((self.agent.payloads, self.ledger()), ([], []))

    def test_send_and_only_to_cannot_be_combined(self):
        with self.assertRaises(SystemExit):
            self.go("--send", "--only-to", "xeno@ost2.fyi")

    def test_every_email_reaches_the_agent_with_exactly_one_unsubscribe_placeholder(self):
        self.go("--send")
        for item in self.agent.payloads[0]["items"]:
            self.assertEqual(item["text"].count(SENTINEL), 1)
            self.assertEqual(item["html"].count(SENTINEL), 1)

    def test_config_comes_from_the_boxs_own_settings_and_overrides_win(self):
        cfg = nudge.load_config(nudge.parser().parse_args([]))
        self.assertEqual((cfg["lms_base"], cfg["mfe_base"], cfg["image_url"], cfg["sender"]),
                         ("https://p.ost2.fyi", "https://apps.p.ost2.fyi",
                          "https://p.ost2.fyi/media/lil-stranger/hello.png", "info@ost2.fyi"))
        self.assertNotIn("smtp", cfg, "the host script never touches SMTP or its password any more")
        args = nudge.parser().parse_args(["--lms-url", "https://x.test", "--mfe-url", "https://apps.x.test",
                                          "--image-url", "https://x.test/a.png", "--sender", "me@x.test"])
        cfg = nudge.load_config(args)
        self.assertEqual((cfg["lms_base"], cfg["mfe_base"], cfg["image_url"], cfg["sender"]),
                         ("https://x.test", "https://apps.x.test", "https://x.test/a.png", "me@x.test"))


if __name__ == "__main__":
    unittest.main()
