"""Run with:  python3 -m unittest -v test_ost2_bounce_delete   (from host-scripts/)"""
import datetime as dt
import unittest

import ost2_bounce_delete as bd

SENDER = "noreply@ost2.fyi"
TO = "nobody.here@gmail.com"
MSGID = "<170000000000.100.12345678901234567890@0123456789ab>"

GMAIL_TOP = """Delivered-To: noreply@ost2.fyi
Received: by 2002:a05:6000:1:b0:1:2:3 with SMTP id o1csp1000000aaa;
        Mon, 26 Oct 2026 00:42:40 -0700 (PDT)
X-Received: by 2002:a05:6000:2:b0:1:2:4 with SMTP id aa00000000000-000000000000mr000000000a.1.1793000560000;
        Mon, 26 Oct 2026 00:42:40 -0700 (PDT)
ARC-Seal: i=1; a=rsa-sha256; t=1793000560; cv=none; d=google.com; s=arc-20240605; b=AAAA
ARC-Message-Signature: i=1; a=rsa-sha256; c=relaxed/relaxed; d=google.com; s=arc-20240605; h=from; bh=x; b=BBBB
ARC-Authentication-Results: i=1; mx.google.com;
       dkim=pass header.i=@googlemail.com header.s=20251104 header.b=AbCdEfGh;
       spf=none (google.com: postmaster@mail-sor-f69.google.com does not designate permitted sender hosts) smtp.helo=mail-sor-f69.google.com;
       dmarc=pass (p=QUARANTINE sp=QUARANTINE dis=NONE) header.from=googlemail.com;
       dara=neutral header.i=@ost2.fyi
Return-Path: <{return_path}>
Received: from mail-sor-f69.google.com (mail-sor-f69.google.com. [209.85.220.69])
        by mx.google.com with SMTPS id aa00000000000-000000000000sor000000000a.1.2026.10.26.00.42.40
        for <noreply@ost2.fyi>
        (Google Transport Security);
        Mon, 26 Oct 2026 00:42:40 -0700 (PDT)
Received-SPF: none (google.com: postmaster@mail-sor-f69.google.com does not designate permitted sender hosts) client-ip=209.85.220.69;
{extra_above}Authentication-Results: mx.google.com;
       dkim={dkim} header.i=@{dkim_domain} header.s=20251104 header.b=AbCdEfGh;
       spf=none (google.com: postmaster@mail-sor-f69.google.com does not designate permitted sender hosts) smtp.helo=mail-sor-f69.google.com;
       dmarc={dmarc} (p=QUARANTINE sp=QUARANTINE dis=NONE) header.from={dmarc_from};
       dara=neutral header.i=@ost2.fyi
"""

NOTICE = """DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed; d=googlemail.com; s=20251104; h=from; bh=x; b=CCCC
{forged_auth}Content-Type: multipart/report; boundary="0000000000001111aaaa0000bbbb"; report-type="delivery-status"
To: noreply@ost2.fyi
Return-Path: <>
Auto-Submitted: auto-replied
Message-ID: <00000000.00000000.000000.0000.GMR@mx.google.com>
Date: Mon, 26 Oct 2026 00:42:40 -0700
From: {from_header}
Subject: Delivery Status Notification (Failure)
References: {msgid}
In-Reply-To: {msgid}
X-Failed-Recipients: {to}

--0000000000001111aaaa0000bbbb
Content-Type: multipart/related; boundary="0000000000002222aaaa0000cccc"

--0000000000002222aaaa0000cccc
Content-Type: multipart/alternative; boundary="0000000000003333aaaa0000dddd"

--0000000000003333aaaa0000dddd
Content-Type: text/plain; charset="UTF-8"


** {heading} **

Your message wasn't delivered to {text_to} because {text_reason}

Learn more here: https://support.google.com/mail/?p=NoSuchUser

The response was:

550 5.1.1 The email account that you tried to reach does not exist.

--0000000000003333aaaa0000dddd
Content-Type: text/html; charset="UTF-8"

<html><body>Address not found</body></html>

--0000000000003333aaaa0000dddd--

--0000000000002222aaaa0000cccc--

--0000000000001111aaaa0000bbbb
Content-Type: message/delivery-status

Reporting-MTA: dns; {reporting}
Received-From-MTA: dns; noreply@ost2.fyi
Arrival-Date: Mon, 26 Oct 2026 00:42:38 -0700 (PDT)
X-Original-Message-ID: {msgid}

Final-Recipient: rfc822; {to}
Action: {action}
Status: {status}
Diagnostic-Code: smtp; 550-5.1.1 The email account that you tried to reach
 does not exist.
Last-Attempt-Date: Mon, 26 Oct 2026 00:42:40 -0700 (PDT)
{second_recipient}
--0000000000001111aaaa0000bbbb
Content-Type: text/rfc822-headers

Return-Path: <noreply@ost2.fyi>
Received: from 0123456789ab ([192.0.2.10]) by smtp.gmail.com with ESMTPSA id aa00000000000; Mon, 26 Oct 2026 00:42:38 -0700 (PDT)
From: {orig_from}
X-Google-Original-From: info@ost2.fyi
Content-Type: multipart/alternative; boundary="===============1111111111111111111=="
MIME-Version: 1.0
Subject: {orig_subject}
To: {orig_to}
Date: Mon, 26 Oct 2026 07:42:38 -0000
Message-ID: {orig_msgid}

--0000000000001111aaaa0000bbbb--
"""


def notice(**over):
    v = dict(return_path="", extra_above="", dkim="pass", dkim_domain="googlemail.com", dmarc="pass",
             dmarc_from="googlemail.com", forged_auth="", from_header="Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
             msgid=MSGID, to=TO, heading="Address not found", text_to=TO,
             text_reason="the address couldn't be found, or is unable to receive mail.", reporting="googlemail.com",
             action="failed", status="5.1.1", second_recipient="", orig_from=SENDER,
             orig_subject=bd.ACTIVATION_SUBJECT, orig_to=TO, orig_msgid=MSGID)
    v.update(over)
    top = GMAIL_TOP.format(**v)
    return (top + NOTICE.format(**v)).replace("\n", "\r\n").encode()


KEY = "0123456789abcdef0123456789abcdef"


def sent_copy(msgid=MSGID, to=TO, subject=bd.ACTIVATION_SUBJECT, sender=SENDER, body=None):
    if body is None:
        body = "You're almost there!\r\n\r\nhttps://p.ost2.fyi/activate/%s?next=%%2Fdashboard\r\n" % KEY
    return ("Return-Path: <noreply@ost2.fyi>\r\nFrom: %s\r\nX-Google-Original-From: info@ost2.fyi\r\nSubject: %s\r\n"
            "To: %s\r\nDate: Mon, 26 Oct 2026 07:42:38 -0000\r\nMessage-ID: %s\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n%s"
            % (sender, subject, to, msgid, body)).encode()


class GenuineNotice(unittest.TestCase):
    def test_real_shape_is_an_activation_bounce(self):
        kind, info = bd.verify_notice(notice(), SENDER)
        self.assertEqual(kind, "activation", info)
        self.assertEqual(info["recipient"], TO)
        self.assertEqual(info["message_id"], MSGID)
        self.assertEqual(info["status"], "5.1.1")

    def test_recipient_case_is_folded(self):
        kind, info = bd.verify_notice(notice(to="Nobody.Here@gmail.com", text_to="Nobody.Here@gmail.com", orig_to="Nobody.Here@gmail.com"), SENDER)
        self.assertEqual((kind, info["recipient"]), ("activation", TO))

    def test_curly_apostrophes_in_the_notice_text(self):
        reason = "the address couldn" + chr(0x2019) + "t be found, or is unable to receive mail."
        self.assertEqual(bd.verify_notice(notice(text_reason=reason), SENDER)[0], "activation")

    def test_sent_mail_copy_gives_the_send_time_and_key(self):
        info = bd.verify_notice(notice(), SENDER)[1]
        record = bd.sent_record([sent_copy()], info, SENDER)
        self.assertEqual(record, {"sent_at": dt.datetime(2026, 10, 26, 7, 42, 38, tzinfo=dt.timezone.utc).isoformat(),
                                  "activation_key": KEY})

    def test_no_matching_sent_mail_copy(self):
        info = bd.verify_notice(notice(), SENDER)[1]
        self.assertIsNone(bd.sent_record([], info, SENDER))
        self.assertIsNone(bd.sent_record([sent_copy(msgid="<other@host>")], info, SENDER))
        self.assertIsNone(bd.sent_record([sent_copy(to="someone.else@gmail.com")], info, SENDER))
        self.assertIsNone(bd.sent_record([sent_copy(subject="Reset your password")], info, SENDER))
        self.assertIsNone(bd.sent_record([sent_copy(sender="attacker@example.com")], info, SENDER))

    def test_sent_copy_needs_exactly_one_activation_key(self):
        info = bd.verify_notice(notice(), SENDER)[1]
        self.assertIsNone(bd.sent_record([sent_copy(body="no link here")], info, SENDER))
        two = "https://p.ost2.fyi/activate/%s https://p.ost2.fyi/activate/%s" % (KEY, "f" * 32)
        self.assertIsNone(bd.sent_record([sent_copy(body=two)], info, SENDER))
        same_twice = "https://p.ost2.fyi/activate/%s and again /activate/%s" % (KEY, KEY)
        self.assertEqual(bd.sent_record([sent_copy(body=same_twice)], info, SENDER)["activation_key"], KEY)


class Forgeries(unittest.TestCase):
    """Anything that does not provably come from Google is 'forged', whatever its body says."""

    def assertForged(self, raw, why):
        kind, info = bd.verify_notice(raw, SENDER)
        self.assertEqual(kind, "forged", info)
        self.assertIn(why, info["reason"])

    def test_dkim_fail(self):
        self.assertForged(notice(dkim="fail"), "dkim")

    def test_dkim_for_another_domain(self):
        self.assertForged(notice(dkim_domain="attacker.example"), "dkim")

    def test_dmarc_fail(self):
        self.assertForged(notice(dmarc="fail"), "dmarc")

    def test_dmarc_for_another_domain(self):
        self.assertForged(notice(dmarc_from="attacker.example"), "dmarc")

    def test_a_gmail_user_is_not_the_mailer_daemon(self):
        self.assertForged(notice(from_header="Mail Delivery Subsystem <someone@googlemail.com>"), "From")

    def test_two_from_addresses(self):
        self.assertForged(notice(from_header="mailer-daemon@googlemail.com, x@example.com"), "From")

    def test_non_null_return_path(self):
        self.assertForged(notice(return_path="attacker@example.com"), "Return-Path")

    def test_a_sender_header_above_gmails_results(self):
        self.assertForged(notice(extra_above="X-Attacker: 1\n"), "no Gmail Authentication-Results")

    def test_fake_results_below_do_not_count(self):
        fake = ("Authentication-Results: mx.google.com; dkim=pass header.i=@googlemail.com; "
                "dmarc=pass header.from=googlemail.com\n")
        self.assertForged(notice(dkim="fail", dmarc="fail", forged_auth=fake), "dkim")

    def test_message_without_any_results(self):
        raw = notice().split(b"Authentication-Results: mx.google.com;", 1)[0] + b"Subject: x\r\n\r\nbody\r\n"
        self.assertForged(raw, "no Gmail Authentication-Results")


class OtherGenuineNotices(unittest.TestCase):
    """Genuine Google notices that must not lead to a deletion."""

    def assertOther(self, raw, why):
        kind, info = bd.verify_notice(raw, SENDER)
        self.assertEqual(kind, "other", info)
        self.assertIn(why, info["reason"])

    def test_mailbox_full(self):
        self.assertOther(notice(heading="Message not delivered", status="5.2.2",
                                text_reason="the recipient's inbox is out of storage space."), "Address not found")

    def test_temporary_failure(self):
        self.assertOther(notice(action="delayed", status="4.4.1"), "not a permanent failure")

    def test_reminder_or_other_email_bounces(self):
        self.assertOther(notice(orig_subject="Your OST2 account still needs to be activated"), "subject")

    def test_not_our_sender(self):
        self.assertOther(notice(orig_from="someone@ost2.fyi"), "not sent by")

    def test_text_names_a_different_address(self):
        self.assertOther(notice(text_to="other@gmail.com"), "notice text names")

    def test_returned_to_differs(self):
        self.assertOther(notice(orig_to="other@gmail.com"), "To does not match")

    def test_returned_message_id_differs(self):
        self.assertOther(notice(orig_msgid="<other@host>"), "Message-ID")

    def test_two_recipients(self):
        extra = "\nFinal-Recipient: rfc822; second@gmail.com\nAction: failed\nStatus: 5.1.1\n"
        self.assertOther(notice(second_recipient=extra), "2 recipients")

    def test_other_reporting_mta(self):
        self.assertOther(notice(reporting="mx.example.com"), "Reporting-MTA")


class AuthResults(unittest.TestCase):
    def test_parse(self):
        serv, res = bd.parse_auth_results("mx.google.com; dkim=pass (comment; with semicolon) header.i=@googlemail.com header.s=x;"
                                          " dmarc=pass (p=QUARANTINE) header.from=googlemail.com")
        self.assertEqual(serv, "mx.google.com")
        self.assertIn(("dkim", "pass", {"header.i": "@googlemail.com", "header.s": "x"}), res)
        self.assertIn(("dmarc", "pass", {"header.from": "googlemail.com"}), res)


class Render(unittest.TestCase):
    def row(self, **over):
        r = {"user_id": 40143, "username": "someone", "email": TO, "date_joined": "2026-10-26T07:42:37",
             "sent_at": "2026-10-26T07:42:38+00:00", "noticed_at": "2026-10-26T07:42:40+00:00", "status": "5.1.1"}
        r.update(over)
        return r

    def test_deleted_and_held(self):
        subject, body = bd.render("p.ost2.fyi", {"deleted": [self.row()], "held": [self.row(user_id=1, reason="has 1 certificates")],
                                                 "suspicious": [], "run_dir": "/x/runs/1", "checked": 3, "other": 1})
        self.assertEqual(subject, "[p.ost2.fyi] Bounced-activation cleanup: deleted 1, held 1")
        self.assertIn("DELETED", body)
        self.assertIn("held because: has 1 certificates", body)
        self.assertIn("restore /x/runs/1", body)

    def test_too_many_says_nothing_was_deleted(self):
        subject, body = bd.render("p.ost2.fyi", {"deleted": [self.row()] * 12, "too_many": 12, "max_deletes": 10})
        self.assertIn("TOO MANY (12), nothing deleted", subject)
        self.assertIn("WOULD DELETE", body)
        self.assertIn("NOTHING was deleted", body)

    def test_waiting_shows_the_deletion_date(self):
        _, body = bd.render("p.ost2.fyi", {"waiting": [self.row(outcome="wait", reason="registered or active in the last 7 days",
                                                                 delete_after="2026-11-02T07:45:00+00:00")]})
        self.assertIn("WAITING (7-day grace period", body)
        self.assertIn("deleted on or after 2026-11-02T07:45", body)

    def test_dry_run_says_would_delete(self):
        _, body = bd.render("p.ost2.fyi", {"deleted": [self.row()], "dry": True})
        self.assertIn("WOULD DELETE", body)
        self.assertNotIn("\nDELETED", body)


class MongoScript(unittest.TestCase):
    def test_ids_and_backup_are_embedded_as_literals(self):
        js = bd.mongo_script("apply", [{"id": 5, "username": "o'brien"}], '{"users": [], "subscriptions": []}')
        self.assertIn('const ids = ["5"]', js)
        self.assertIn('"5": "o\'brien"', js)
        self.assertIn('EJSON.parse("{\\"users\\": [], \\"subscriptions\\": []}")', js)
        self.assertIn("'apply' === 'test-apply'", js)


if __name__ == "__main__":
    unittest.main()
