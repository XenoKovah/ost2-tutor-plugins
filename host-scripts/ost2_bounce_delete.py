#!/usr/bin/env python3
"""Delete never-activated accounts whose sign-up activation email hard-bounced.

Xeno 2026-10-26: when the "Action Required: Activate your OpenSecurityTraining2 account" email that
registration sends comes back with Gmail's "Address not found" notice ("... because the address
couldn't be found, or is unable to receive mail"), delete that account.  The address can never get
the activation link (or a password reset), so the account can never sign in again; deleting it also
frees the username and the email for a corrected sign-up.

Standard library only; run it from cron on the Tutor host (NOT inside a container):

    30 6 * * *  python3 ~/ost2-host-scripts/ost2_bounce_delete.py run --to xeno@ost2.fyi \
                >> ~/.local/share/ost2-bounce-delete/cron.log 2>&1

WHERE THE BOUNCES ARE.  The LMS sends as SMTP_USERNAME (noreply@ost2.fyi) through smtp.gmail.com, so
Gmail itself writes every failure ("Delivery Status Notification (Failure)" from
mailer-daemon@googlemail.com) into that mailbox.  This script reads the mailbox over IMAP with the
same App Password the LMS sends with (tutor config SMTP_USERNAME / SMTP_PASSWORD).  It never changes
the mailbox: folders are opened read-only and messages fetched with BODY.PEEK.

WHY A FORGED BOUNCE CANNOT DELETE AN ACCOUNT.  Anyone can email noreply@ something that looks like a
bounce, so the Gmail search is only a pre-filter.  A notice counts only when ALL of these hold
(verify_notice() and sent_time()):

  1. Google wrote it.  The message's first Authentication-Results header is the one Gmail's own
     mx.google.com adds on delivery (only Gmail's delivery headers may sit above it; a sender's
     headers always come below), and it says dkim=pass for @googlemail.com AND dmarc=pass for
     header.from=googlemail.com.  The From is exactly mailer-daemon@googlemail.com and Gmail recorded
     a null Return-Path (<>).  Only Google can sign for googlemail.com and no Gmail user can send as
     its mailer-daemon.
  2. It is a hard "address not found": a multipart/report whose delivery-status comes from
     Reporting-MTA googlemail.com, names exactly one recipient with Action: failed and a 5.x.x
     Status, and whose text is Google's "Address not found" notice for that same address.
  3. It is about OUR activation email: the returned headers carry the activation subject, our
     sender, that one recipient, and the Message-ID the notice reports -- and a message with that
     Message-ID, subject, sender and recipient is in this mailbox's Sent Mail.
  4. The account fits (checked inside the LMS): exactly one account uses the address, it never
     activated (is_active=0 and an activation key exists), it is not staff, and the bounced email
     was sent between 10 minutes before and 30 days after the account registered.
  5. The person is gone: the account registered over 24 hours ago and has had no sign-in and no
     courseware activity for 24 hours.  Registration signs the new account in, so someone who
     mistyped their address can still be learning (or fixing the address on the Account page) in
     that first session; such an account WAITS and is checked again on the next run.

An account that passes all of that but has something worth a human look is HELD (listed in the
email, never deleted): a certificate, block completions, a course role, an SSO link, forum posts
(the MySQL forum tables or the Mongo forum store), or a sign-in after its registration day.
Notices that claim to be from Google but fail check 1, and genuine activation bounces that are not
in Sent Mail, are listed too (possible forgeries).  Other genuine bounces (forum mail, reminders,
mailbox full, temporary failures) are ignored.

WHAT IS DELETED -- the same full-deletion recipe as the manual 2026-10-16 and 2026-10-25 deletions:
LMS auth_user and every row Django's delete cascades to, plus the non-FK grade, enrollment-history
and schedule-history rows and custom_reg_form_extrainfo (a DB-level RESTRICT the ORM does not see);
gamma's GammaUser (and its achievements) and events_event rows for the username; the Mongo forum
`users` doc and `subscriptions` for the user id.  Every row is saved before anything is deleted:
each store is dumped first and written to <state-dir>/runs/<UTC time>/ (0600), and each delete
re-dumps inside its own transaction and refuses to run if the rows differ from the saved backup.
Undo a run with:  ost2_bounce_delete.py restore <run dir>            (dry: insert, compare, roll back)
                  ost2_bounce_delete.py restore <run dir> --apply

SAFETY VALVES.  More than --max-deletes (default 10) eligible accounts in one run deletes nothing and
emails an alert (re-run by hand with a higher --max-deletes after looking).  A failure part-way
through the deletes writes <state-dir>/STOP; while STOP exists every run only emails a reminder, so
nothing more is deleted until a human has checked the run directory and removed STOP.
`run --dry-run` checks the mailbox and the accounts, prints what it would do, and changes nothing.
`run --test-apply` also runs the LMS and gamma deletes inside transactions that are rolled back
(the LMS one restores its backup first, as a round-trip check), and only counts the Mongo docs;
note gamma's GammaUser delete handlers remove the user from the Redis leaderboard even in a
rolled-back transaction, until the next 60-second leaderboard rebuild.

Each notice is handled once (<state-dir>/processed.json, keyed by Gmail message id); the search looks
back --lookback-days (default 7), so a missed day is caught up and a waiting account is re-checked.  The email goes out only when
something happened (deleted, held, a suspicious notice, too many, an error).
"""
import argparse
import base64
import datetime as dt
import email
import email.errors
import email.header
import email.utils
import imaplib
import json
import os
import re
import shutil
import smtplib
import ssl
import subprocess
import sys
import traceback
from email import policy
from email.message import EmailMessage

ACTIVATION_SUBJECT = "Action Required: Activate your OpenSecurityTraining2 account"
GOOGLE_DSN_FROM = "mailer-daemon@googlemail.com"
GOOGLE_DSN_DOMAIN = "googlemail.com"
GMAIL_AUTHSERV = "mx.google.com"
# Headers Gmail itself prepends on delivery, above its own Authentication-Results.
GMAIL_DELIVERY_HEADERS = {
    "delivered-to", "received", "x-received", "arc-seal", "arc-message-signature",
    "arc-authentication-results", "return-path", "received-spf", "x-google-smtp-source",
}
NOT_FOUND_RE = re.compile(r"wasn't delivered to\s+(\S+?)\s+because the address couldn't be found,"
                          r"\s+or is unable to receive mail", re.I)
MESSAGE_ID_RE = re.compile(r"^<[^<>\"\s\\]{3,250}>$")
STATUS_5XX_RE = re.compile(r"^5\.\d{1,3}\.\d{1,3}$")
APOSTROPHES = {0x2019: "'", 0x2018: "'"}

DEFAULT_STATE_DIR = os.path.expanduser("~/.local/share/ost2-bounce-delete")
DEFAULT_TUTOR = os.path.expanduser("~/tutor-venv/bin/tutor")
PROCESSED = "processed.json"
FAIL_MARKER = "last_failure"
STOP = "STOP"
KEEP_PROCESSED = dt.timedelta(days=90)
AGENT_TIMEOUT = 1800


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def squash(value):
    return " ".join(str(value or "").split())


def addresses(values):
    return [addr.strip().lower() for _, addr in email.utils.getaddresses([str(v) for v in values]) if addr.strip()]


def decoded(value):
    try:
        return squash(str(email.header.make_header(email.header.decode_header(str(value or "")))))
    except (ValueError, LookupError, email.errors.HeaderParseError):
        return squash(value)


def parse_date(value):
    try:
        moment = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:  # "-0000" (what Django's mail Date uses) means UTC with no stated zone
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


# --------------------------------------------------------------------------- verifying a notice


def parse_auth_results(value):
    """("mx.google.com", [(method, result, {prop: value}), ...]) from one Authentication-Results value."""
    value = re.sub(r"\([^()]*\)", " ", squash(value))
    parts = [p.strip() for p in value.split(";")]
    authserv = parts[0].split()[0].lower() if parts and parts[0].split() else ""
    results = []
    for part in parts[1:]:
        tokens = part.split()
        if not tokens or "=" not in tokens[0]:
            continue
        method, _, result = tokens[0].partition("=")
        props = {}
        for token in tokens[1:]:
            key, eq, val = token.partition("=")
            if eq:
                props[key.lower()] = val.lower()
        results.append((method.lower(), result.lower(), props))
    return authserv, results


def gmail_delivery_block(msg):
    """Gmail's own delivery headers (top of the message, down to and including its
    Authentication-Results), or None if anything else comes before an Authentication-Results."""
    block = []
    for name, value in msg.items():
        name = name.lower()
        block.append((name, squash(value)))
        if name == "authentication-results":
            return block
        if name not in GMAIL_DELIVERY_HEADERS:
            return None
    return None


def google_wrote_it(msg):
    """Check 1.  Returns None when the notice provably comes from Google, else the reason."""
    block = gmail_delivery_block(msg)
    if block is None:
        return "no Gmail Authentication-Results at the top of the message"
    authserv, results = parse_auth_results(block[-1][1])
    if authserv != GMAIL_AUTHSERV:
        return "top Authentication-Results is from %r, not %s" % (authserv, GMAIL_AUTHSERV)
    dkim = any(m == "dkim" and r == "pass" and (p.get("header.d") == GOOGLE_DSN_DOMAIN
                                                 or p.get("header.i", "").endswith("@" + GOOGLE_DSN_DOMAIN))
               for m, r, p in results)
    if not dkim:
        return "no dkim=pass for %s" % GOOGLE_DSN_DOMAIN
    dmarc = any(m == "dmarc" and r == "pass" and p.get("header.from") == GOOGLE_DSN_DOMAIN for m, r, p in results)
    if not dmarc:
        return "no dmarc=pass for header.from=%s" % GOOGLE_DSN_DOMAIN
    froms = msg.get_all("From", [])
    if len(froms) != 1 or addresses(froms) != [GOOGLE_DSN_FROM]:
        return "From is %r, not %s" % (squash(" | ".join(map(str, froms)))[:120], GOOGLE_DSN_FROM)
    paths = [v for n, v in block if n == "return-path"]
    if not paths or paths[0] != "<>":
        return "Gmail's Return-Path is %r, not <>" % (paths[0] if paths else None)
    return None


def parts_of(msg, content_type):
    return [part for part in msg.walk() if part.get_content_type() == content_type]


def human_text(msg):
    texts = []
    for part in msg.walk():
        if part.get_content_type() in ("message/delivery-status", "message/rfc822", "text/rfc822-headers"):
            break  # the machine-readable report and the returned original follow the notice text
        if part.get_content_type() == "text/plain" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            texts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
    return squash(" ".join(texts)).translate(APOSTROPHES)


def returned_headers(msg):
    """The original message's headers, as Google returned them (text/rfc822-headers or message/rfc822)."""
    found = []
    for part in parts_of(msg, "text/rfc822-headers"):
        payload = part.get_payload(decode=True) or b""
        found.append(email.message_from_bytes(payload, policy=policy.compat32))
    for part in parts_of(msg, "message/rfc822"):
        inner = part.get_payload()
        if isinstance(inner, list) and inner:
            found.append(inner[0])
    return found


def verify_notice(raw, sender, subject=ACTIVATION_SUBJECT):
    """Checks 1-3 (without the Sent Mail lookup) on one raw message.

    Returns (kind, info).  kind is "activation" (a genuine hard bounce of our activation email;
    info has recipient, message_id, status, diagnostic, noticed_at), "forged" (claims to be from
    Google but check 1 fails; info has the reason) or "other" (a genuine Google notice about
    something else; info has the reason).
    """
    msg = email.message_from_bytes(raw, policy=policy.compat32)
    reason = google_wrote_it(msg)
    if reason:
        return "forged", {"reason": reason, "from": squash(msg.get("From"))[:120], "date": squash(msg.get("Date"))}

    def other(why):
        return "other", {"reason": why}

    if msg.get_content_type() != "multipart/report" or (msg.get_param("report-type") or "").lower() != "delivery-status":
        return other("not a delivery-status report")
    reports = parts_of(msg, "message/delivery-status")
    if len(reports) != 1:
        return other("%d delivery-status parts" % len(reports))
    blocks = reports[0].get_payload()
    if not isinstance(blocks, list) or len(blocks) < 2:
        return other("delivery-status has no recipient block")
    per_message, recipients = blocks[0], blocks[1:]
    if squash(per_message.get("Reporting-MTA")).lower() != "dns; " + GOOGLE_DSN_DOMAIN:
        return other("Reporting-MTA is %r" % squash(per_message.get("Reporting-MTA")))
    if len(recipients) != 1:
        return other("%d recipients in one notice" % len(recipients))
    block = recipients[0]
    final = squash(block.get("Final-Recipient"))
    kind, _, recipient = final.partition(";")
    recipient = recipient.strip().lower()
    if kind.strip().lower() != "rfc822" or "@" not in recipient:
        return other("Final-Recipient %r" % final)
    action, status = squash(block.get("Action")).lower(), squash(block.get("Status"))
    if action != "failed" or not STATUS_5XX_RE.match(status):
        return other("Action %r Status %r (not a permanent failure)" % (action, status))
    text = human_text(msg)
    found = NOT_FOUND_RE.search(text)
    if "address not found" not in text.lower() or not found:
        return other("not Google's 'Address not found' notice (status %s)" % status)
    if found.group(1).strip(".,;<>").lower() != recipient:
        return other("notice text names %r, Final-Recipient %r" % (found.group(1), recipient))
    message_id = squash(per_message.get("X-Original-Message-ID"))
    if not MESSAGE_ID_RE.match(message_id):
        return other("no usable X-Original-Message-ID")
    originals = returned_headers(msg)
    if len(originals) != 1:
        return other("%d returned-header parts" % len(originals))
    original = originals[0]
    if decoded(original.get("Subject")) != subject:
        return other("bounced email's subject is %r" % decoded(original.get("Subject"))[:80])
    if addresses(original.get_all("From", [])) != [sender.lower()]:
        return other("bounced email was not sent by %s" % sender)
    if addresses(original.get_all("To", [])) != [recipient]:
        return other("bounced email's To does not match the failed recipient")
    if squash(original.get("Message-ID")) != message_id:
        return other("returned Message-ID differs from X-Original-Message-ID")
    in_reply_to = squash(msg.get("In-Reply-To"))
    if in_reply_to and in_reply_to != message_id:
        return other("In-Reply-To differs from X-Original-Message-ID")
    diagnostic = squash(block.get("Diagnostic-Code"))
    return "activation", {
        "recipient": recipient, "message_id": message_id, "status": status,
        "diagnostic": diagnostic[:300], "noticed_at": (parse_date(msg.get("Date")) or utcnow()).isoformat(),
    }


def sent_time(sent_headers, info, sender, subject=ACTIVATION_SUBJECT):
    """Check 3, second half: when we sent the bounced email, from its copy in Sent Mail (or None)."""
    for raw in sent_headers:
        m = email.message_from_bytes(raw, policy=policy.compat32)
        if squash(m.get("Message-ID")) != info["message_id"]:
            continue
        if (addresses(m.get_all("From", [])) == [sender.lower()] and addresses(m.get_all("To", [])) == [info["recipient"]]
                and decoded(m.get("Subject")) == subject):
            moment = parse_date(m.get("Date"))
            if moment:
                return moment
    return None


# --------------------------------------------------------------------------- the mailbox


class Mailbox:
    """Read-only IMAP access to the sending account (Gmail)."""

    def __init__(self, user, password, host="imap.gmail.com"):
        self.imap = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=120)
        self.imap.login(user, password)
        self.folders = self._special_use()

    def _special_use(self):
        typ, lines = self.imap.list()
        if typ != "OK":
            raise RuntimeError("IMAP LIST failed")
        found = {}
        for line in lines or []:
            m = re.match(rb'\((?P<flags>[^)]*)\) "[^"]*" "?(?P<name>[^"]*)"?$', line or b"")
            if not m:
                continue
            for flag in (b"\\All", b"\\Junk", b"\\Sent"):
                if flag in m.group("flags").split():
                    found[flag.decode()[1:]] = m.group("name").decode()
        missing = {"All", "Junk", "Sent"} - set(found)
        if missing:
            raise RuntimeError("IMAP folders not found: %s" % sorted(missing))
        return found

    def _select(self, folder):
        typ, data = self.imap.select('"%s"' % self.folders[folder], readonly=True)
        if typ != "OK":
            raise RuntimeError("cannot open %s: %r" % (self.folders[folder], data))

    def search(self, folder, gmail_query):
        """[(gmail message id, raw bytes)] for a Gmail search in All Mail / Junk."""
        self._select(folder)
        typ, data = self.imap.search(None, "X-GM-RAW", '"%s"' % gmail_query.replace('"', ""))
        if typ != "OK":
            raise RuntimeError("IMAP search failed in %s" % folder)
        found = []
        for num in (data[0] or b"").split():
            typ, resp = self.imap.fetch(num, "(X-GM-MSGID BODY.PEEK[])")
            if typ != "OK":
                raise RuntimeError("IMAP fetch %s failed" % num)
            meta = b" ".join(p[0] if isinstance(p, tuple) else p for p in resp if p)
            raw = next((p[1] for p in resp if isinstance(p, tuple)), None)
            gm = re.search(rb"X-GM-MSGID (\d+)", meta)
            if raw is None or not gm:
                raise RuntimeError("IMAP fetch %s returned no message" % num)
            found.append((gm.group(1).decode(), raw))
        return found

    def sent_headers(self, message_id):
        """Headers of every Sent Mail message whose Message-ID header contains message_id."""
        if not MESSAGE_ID_RE.match(message_id):
            return []
        self._select("Sent")
        typ, data = self.imap.search(None, "HEADER", "Message-ID", '"%s"' % message_id)
        if typ != "OK":
            raise RuntimeError("IMAP search of Sent Mail failed")
        found = []
        for num in (data[0] or b"").split():
            typ, resp = self.imap.fetch(num, "(BODY.PEEK[HEADER])")
            if typ == "OK":
                found += [p[1] for p in resp if isinstance(p, tuple)]
        return found

    def close(self):
        try:
            self.imap.logout()
        except Exception:  # noqa: BLE001 - closing a finished session must not fail the run
            pass


def collect_notices(mailbox, sender, lookback_days, processed):
    """Every not-yet-processed notice from Google's mailer-daemon, checked: [(gm_id, kind, info)]."""
    query = "from:%s newer_than:%dd" % (GOOGLE_DSN_FROM, lookback_days)
    seen, notices = set(), []
    for folder in ("All", "Junk"):
        for gm_id, raw in mailbox.search(folder, query):
            if gm_id in processed or gm_id in seen:
                continue
            seen.add(gm_id)
            kind, info = verify_notice(raw, sender)
            if kind == "activation":
                sent_at = sent_time(mailbox.sent_headers(info["message_id"]), info, sender)
                if sent_at is None:
                    kind, info = "unmatched", dict(info, reason="no matching activation email in Sent Mail")
                else:
                    info["sent_at"] = sent_at.isoformat()
            notices.append((gm_id, kind, info))
    return notices


# --------------------------------------------------------------------------- the agents

# Shared by the LMS and gamma agents (both Django on MySQL).  Every agent is shipped to its
# container on stdin as source text (nothing is installed there) and talks back with one
# "BD_<TAG> <json>" line per result; every other line is start-up noise and is ignored.
ROW_HELPERS = r'''
import base64, datetime, decimal, hashlib, json, uuid
from django.db import connection, transaction
from django.db.models.deletion import Collector

P = json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8"))


def out(tag, obj):
    print("BD_" + tag + " " + json.dumps(obj, default=str), flush=True)


class Abort(Exception):
    pass


class Rollback(Exception):
    pass


def enc(v):
    if isinstance(v, datetime.datetime): return {"__dt": v.isoformat()}
    if isinstance(v, datetime.date): return {"__d": v.isoformat()}
    if isinstance(v, datetime.timedelta): return {"__td": v.total_seconds()}
    if isinstance(v, decimal.Decimal): return {"__dec": str(v)}
    if isinstance(v, (bytes, bytearray, memoryview)): return {"__b": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, uuid.UUID): return {"__u": str(v)}
    return v


def dec(v):
    if isinstance(v, dict):
        if "__dt" in v: return datetime.datetime.fromisoformat(v["__dt"])
        if "__d" in v: return datetime.date.fromisoformat(v["__d"])
        if "__td" in v: return datetime.timedelta(seconds=v["__td"])
        if "__dec" in v: return decimal.Decimal(v["__dec"])
        if "__b" in v: return base64.b64decode(v["__b"])
        if "__u" in v: return v["__u"]
    return v


def q(name):
    return "`" + name.replace("`", "") + "`"


def dump(table, pkcol, pks):
    cols, rows, pks = None, [], sorted(pks)
    with connection.cursor() as c:
        for i in range(0, len(pks), 500):
            ch = pks[i:i + 500]
            c.execute("SELECT * FROM " + q(table) + " WHERE " + q(pkcol) + " IN (" + ",".join(["%s"] * len(ch)) + ")", ch)
            cols = [d[0] for d in c.description]
            rows += [[enc(v) for v in r] for r in c.fetchall()]
    return {"pk": pkcol, "cols": cols or [], "rows": rows}


def snapshot(roots, extra=()):
    """Every row deleting `roots` would remove ("del") or set-null ("upd"), dumped."""
    touch = {}

    def add(model, pks, how):
        e = touch.setdefault(model._meta.db_table, {"pk": model._meta.pk.column, "del": set(), "upd": set()})
        e[how].update(p.hex if isinstance(p, uuid.UUID) else p for p in pks)

    for qs in roots:
        col = Collector(using="default")
        col.collect(qs)
        for m, objs in col.data.items():
            add(m, [o.pk for o in objs], "del")
        for fq in col.fast_deletes:
            add(fq.model, list(fq.values_list("pk", flat=True)), "del")
        for (f, val), lst in col.field_updates.items():
            for objs in lst:
                objs = list(objs)
                if objs:
                    add(type(objs[0]), [o.pk for o in objs], "upd")
    for table, pkcol, pks in extra:
        touch.setdefault(table, {"pk": pkcol, "del": set(), "upd": set()})["del"].update(pks)
    tables = {}
    for t, e in sorted(touch.items()):
        e["upd"] -= e["del"]
        pks = e["del"] | e["upd"]
        if not pks:
            continue
        d = dump(t, e["pk"], pks)
        if len(d["rows"]) != len(pks):
            raise Abort("backup row count mismatch for " + t)
        d["upd"] = sorted(e["upd"], key=str)
        tables[t] = d
    return tables


def digest(tables):
    canon = {t: sorted(json.dumps(r, sort_keys=True) for r in d["rows"]) for t, d in tables.items()}
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def insert_rows(tables):
    """Put dumped rows back: plain INSERT for deleted rows (fails if one exists again), upsert for set-null rows."""
    n = 0
    with connection.cursor() as c:
        c.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            for t, d in tables.items():
                cols, upd = d["cols"], set(map(str, d.get("upd", [])))
                pki = cols.index(d["pk"])
                ins = "INSERT INTO " + q(t) + " (" + ",".join(q(x) for x in cols) + ") VALUES (" + ",".join(["%s"] * len(cols)) + ")"
                ups = ins + " AS _n ON DUPLICATE KEY UPDATE " + ",".join(q(x) + "=_n." + q(x) for x in cols)
                plain = [[dec(v) for v in r] for r in d["rows"] if str(r[pki]) not in upd]
                setnull = [[dec(v) for v in r] for r in d["rows"] if str(r[pki]) in upd]
                if plain:
                    c.executemany(ins, plain)
                if setnull:
                    c.executemany(ups, setnull)
                n += len(plain) + len(setnull)
        finally:
            c.execute("SET FOREIGN_KEY_CHECKS=1")
    return n


def differences(tables):
    diffs = []
    for t, d in tables.items():
        now = dump(t, d["pk"], [r[d["cols"].index(d["pk"])] for r in d["rows"]])
        if sorted(json.dumps(x, sort_keys=True) for x in d["rows"]) != sorted(json.dumps(x, sort_keys=True) for x in now["rows"]):
            diffs.append(t)
    return diffs


def still_present(tables):
    left = {}
    for t, d in tables.items():
        pkset = set(map(str, d.get("upd", [])))
        pks = [r[d["cols"].index(d["pk"])] for r in d["rows"] if str(r[d["cols"].index(d["pk"])]) not in pkset]
        if not pks:
            continue
        with connection.cursor() as c:
            c.execute("SELECT COUNT(*) FROM " + q(t) + " WHERE " + q(d["pk"]) + " IN (" + ",".join(["%s"] * len(pks)) + ")", pks)
            k = c.fetchone()[0]
        if k:
            left[t] = k
    return left


def restore_mode(tables, mode, conflicts=lambda: []):
    try:
        with transaction.atomic():
            clash = conflicts()
            if clash:
                raise Abort("cannot restore, these now belong to someone else: %s" % clash[:20])
            n = insert_rows(tables)
            diffs = differences(tables)
            out("RESTORE", {"rows": n, "tables_differing": diffs, "mode": mode})
            if diffs:
                raise Abort("restored rows differ from the backup in %s" % diffs)
            if mode != "restore":
                raise Rollback()
    except Rollback:
        pass
'''

LMS_AGENT = ROW_HELPERS + r'''
from django.contrib.auth.models import User
from simple_history.signals import post_create_historical_record
from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment, Registration
from completion.models import BlockCompletion
from forum.models import Comment, CommentThread
from django.db.models import Max
from django.utils import timezone
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.grades.models import PersistentCourseGrade, PersistentSubsectionGrade
from openedx.core.djangoapps.schedules.models import Schedule
from social_django.models import UserSocialAuth

JOIN_SLACK = datetime.timedelta(minutes=10)
RESEND_WINDOW = datetime.timedelta(days=30)
QUIET = datetime.timedelta(hours=24)


def assess(address, sent_at):
    users = list(User.objects.filter(email__iexact=address))
    if not users:
        return {"outcome": "gone"}
    if len(users) > 1:
        return {"outcome": "hold", "reason": "%d accounts use this address" % len(users)}
    u = users[0]
    base = {"user_id": u.id, "username": u.username, "date_joined": u.date_joined.isoformat(),
            "last_login": u.last_login.isoformat() if u.last_login else None}

    def hold(reason):
        return dict(base, outcome="hold", reason=reason)

    if u.is_active:
        return hold("the account is active")
    if u.is_staff or u.is_superuser:
        return hold("staff or superuser account")
    if not Registration.objects.filter(user=u).exists():
        return hold("no activation key")
    sent = datetime.datetime.fromisoformat(sent_at)
    if sent < u.date_joined - JOIN_SLACK or sent > u.date_joined + RESEND_WINDOW:
        return hold("the bounced activation email (sent %s) was not for this account (registered %s)" % (sent_at, base["date_joined"]))
    if u.last_login and u.last_login > u.date_joined + datetime.timedelta(days=1):
        return hold("signed in after its registration day")
    for name, qs in (("certificates", GeneratedCertificate.objects.filter(user_id=u.id)),
                     ("block completions", BlockCompletion.objects.filter(user_id=u.id)),
                     ("course roles", CourseAccessRole.objects.filter(user_id=u.id)),
                     ("SSO links", UserSocialAuth.objects.filter(user_id=u.id)),
                     ("forum threads", CommentThread.objects.filter(author_id=u.id)),
                     ("forum comments", Comment.objects.filter(author_id=u.id))):
        n = qs.count()
        if n:
            return hold("has %d %s" % (n, name))
    last = max(t for t in (u.date_joined, u.last_login, StudentModule.objects.filter(student_id=u.id).aggregate(m=Max("modified"))["m"]) if t)
    if timezone.now() - last < QUIET:
        return dict(base, outcome="wait", reason="still active (last activity %s)" % last.isoformat()[:16])
    return dict(base, outcome="eligible")


def roots_for(ids):
    sched_ids = list(Schedule.objects.filter(enrollment__user_id__in=ids).values_list("id", flat=True))
    roots = [PersistentSubsectionGrade.objects.filter(user_id__in=ids), PersistentCourseGrade.objects.filter(user_id__in=ids),
             CourseEnrollment.history.model.objects.filter(user_id__in=ids), Schedule.history.model.objects.filter(id__in=sched_ids),
             User.objects.filter(id__in=ids)]
    with connection.cursor() as c:
        c.execute("SELECT id FROM custom_reg_form_extrainfo WHERE user_id IN (" + ",".join(["%s"] * len(ids)) + ")", ids)
        extra = [("custom_reg_form_extrainfo", "id", [r[0] for r in c.fetchall()])]
    return sched_ids, roots, extra


def delete_users(ids, sched_ids, roots):
    tomb = []

    def rec(sender, history_instance, **kw):
        tomb.append(history_instance)

    post_create_historical_record.connect(rec, weak=False, dispatch_uid="ost2_bounce_delete_tomb")
    try:
        n = {"subsection_grades": roots[0].delete()[0], "course_grades": roots[1].delete()[0],
             "enroll_history": roots[2].delete()[0], "schedule_history": roots[3].delete()[0]}
        with connection.cursor() as c:
            c.execute("DELETE FROM custom_reg_form_extrainfo WHERE user_id IN (" + ",".join(["%s"] * len(ids)) + ")", ids)
            n["customreg_extrainfo"] = c.rowcount
        d = User.objects.filter(id__in=ids).delete()
        n["cascade_total"], n["users"] = d[0], d[1].get("auth.User", 0)
    finally:
        post_create_historical_record.disconnect(dispatch_uid="ost2_bounce_delete_tomb")
    n["tombstones"] = len(tomb)
    for h in tomb:
        type(h).objects.filter(pk=h.pk).delete()
    n["enroll_history_after"] = CourseEnrollment.history.model.objects.filter(user_id__in=ids).delete()[0]
    n["schedule_history_after"] = Schedule.history.model.objects.filter(id__in=sched_ids).delete()[0]
    return n


def checked(users):
    """Re-check every target inside the transaction; any change aborts the whole delete."""
    for x in users:
        a = assess(x["email"], x["sent_at"])
        if a["outcome"] != "eligible" or a.get("user_id") != x["id"] or a.get("username") != x["username"]:
            raise Abort("%s (%s) is no longer eligible: %s" % (x["id"], x["username"], a.get("reason", a["outcome"])))
    return [x["id"] for x in users]


mode = P["mode"]
try:
    if mode == "assess":
        for c in P["candidates"]:
            out("ASSESS", dict(assess(c["email"], c["sent_at"]), email=c["email"]))
    elif mode == "dump":
        ids = checked(P["users"])
        sched_ids, roots, extra = roots_for(ids)
        tables = snapshot(roots, extra)
        out("DUMP", {"digest": digest(tables), "rows": sum(len(d["rows"]) for d in tables.values()),
                     "tables_b64": base64.b64encode(json.dumps(tables).encode()).decode()})
    elif mode in ("apply", "test-apply"):
        try:
            with transaction.atomic():
                list(User.objects.select_for_update().filter(id__in=[x["id"] for x in P["users"]]))
                ids = checked(P["users"])
                sched_ids, roots, extra = roots_for(ids)
                tables = snapshot(roots, extra)
                if digest(tables) != P["digest"]:
                    raise Abort("the rows changed after the backup was saved")
                n = delete_users(ids, sched_ids, roots)
                left = still_present(tables)
                left_users = User.objects.filter(id__in=ids).count()
                if left or left_users:
                    raise Abort("rows left after the delete: users=%d %s" % (left_users, left))
                if mode == "test-apply":
                    r = insert_rows(tables)
                    out("TEST", {"deleted": n, "reinserted": r, "tables_differing": differences(tables)})
                    raise Rollback()
                out("DELETED", n)
        except Rollback:
            pass
    elif mode in ("restore", "restore-dry"):
        tables = json.loads(base64.b64decode(P["tables_b64"]).decode())

        def conflicts():
            d = tables.get("auth_user")
            if not d:
                return []
            cols, clash = d["cols"], []
            for r in d["rows"]:
                row = dict(zip(cols, r))
                if User.objects.filter(username=row["username"]).exclude(id=row["id"]).exists():
                    clash.append("username " + row["username"])
                if User.objects.filter(email__iexact=row["email"]).exclude(id=row["id"]).exists():
                    clash.append("email of " + row["username"])
            return clash

        restore_mode(tables, mode, conflicts)
    else:
        raise Abort("unknown mode " + mode)
except Abort as e:
    out("ABORT", {"reason": str(e)})
out("END", {"mode": mode})
'''

GAMMA_AGENT = ROW_HELPERS + r'''
from users.models import GammaUser
from events.models import Event

names = set(P["usernames"])


def roots_for():
    # exact match: the MySQL collation is case-insensitive
    gids = [g.id for g in GammaUser.objects.filter(user_uid__in=list(names)) if g.user_uid in names]
    eids = [i for i, un in Event.objects.filter(username__in=list(names)).values_list("id", "username") if un in names]
    return gids, eids, [Event.objects.filter(id__in=eids), GammaUser.objects.filter(id__in=gids)]


mode = P["mode"]
try:
    if mode == "dump":
        gids, eids, roots = roots_for()
        tables = snapshot(roots)
        out("DUMP", {"digest": digest(tables), "rows": sum(len(d["rows"]) for d in tables.values()),
                     "gamma_users": len(gids), "events": len(eids),
                     "tables_b64": base64.b64encode(json.dumps(tables).encode()).decode()})
    elif mode in ("apply", "test-apply"):
        try:
            with transaction.atomic():
                gids, eids, roots = roots_for()
                list(GammaUser.objects.select_for_update().filter(id__in=gids))
                tables = snapshot(roots)
                if digest(tables) != P["digest"]:
                    raise Abort("the rows changed after the backup was saved")
                n = {"events": roots[0].delete()[0]}
                d = GammaUser.objects.filter(id__in=gids).delete()
                n["cascade_total"], n["detail"] = d[0], d[1]
                left = still_present(tables)
                if left:
                    raise Abort("rows left after the delete: %s" % left)
                if mode == "test-apply":
                    out("TEST", {"deleted": n})
                    raise Rollback()
                out("DELETED", n)
        except Rollback:
            pass
    elif mode in ("restore", "restore-dry"):
        tables = json.loads(base64.b64decode(P["tables_b64"]).decode())
        restore_mode(tables, mode)
    else:
        raise Abort("unknown mode " + mode)
except Abort as e:
    out("ABORT", {"reason": str(e)})
out("END", {"mode": mode})
'''


def mongo_script(mode, users=None, backup=None):
    """mongosh source for the forum store (cs_comments_service).  users = [{id, username}]."""
    ids = json.dumps([str(u["id"]) for u in users or []])
    names = json.dumps({str(u["id"]): u["username"] for u in users or []})
    head = "const ids = %s; const names = %s; function out(t, o) { print('BD_' + t + ' ' + EJSON.stringify(o)); }\n" % (ids, names)
    if mode == "dump":
        return head + r"""
const users = db.users.find({external_id: {$in: ids}}).toArray();
const wrong = users.filter(u => names[u.external_id] !== u.username).map(u => u.external_id);
const posts = {};
db.contents.aggregate([{$match: {author_id: {$in: ids}}}, {$group: {_id: '$author_id', n: {$sum: 1}}}]).forEach(r => { posts[r._id] = r.n; });
out('DUMP', {users: users, subscriptions: db.subscriptions.find({subscriber_id: {$in: ids}}).toArray(), posts: posts, wrong_username: wrong});
out('END', {});
"""
    if mode in ("apply", "test-apply"):
        return head + "const B = EJSON.parse(%s);\n" % json.dumps(backup) + r"""
const uid = B.users.map(d => d._id), sid = B.subscriptions.map(d => d._id);
const now = {users: db.users.countDocuments({_id: {$in: uid}}), subs: db.subscriptions.countDocuments({_id: {$in: sid}}),
             all_users: db.users.countDocuments({external_id: {$in: ids}}), all_subs: db.subscriptions.countDocuments({subscriber_id: {$in: ids}}),
             posts: db.contents.countDocuments({author_id: {$in: ids}})};
if (now.users !== uid.length || now.subs !== sid.length || now.all_users !== uid.length || now.all_subs !== sid.length || now.posts !== 0) {
  out('ABORT', {reason: 'forum docs changed after the backup was saved', now: now});
} else if ('""" + mode + r"""' === 'test-apply') {
  out('TEST', now);
} else {
  const r = {users: db.users.deleteMany({_id: {$in: uid}}).deletedCount, subscriptions: db.subscriptions.deleteMany({_id: {$in: sid}}).deletedCount};
  if (r.users !== uid.length || r.subscriptions !== sid.length) { out('ABORT', {reason: 'delete counts differ', r: r}); } else { out('DELETED', r); }
}
out('END', {});
"""
    if mode in ("restore", "restore-dry"):
        return head + "const B = EJSON.parse(%s);\n" % json.dumps(backup) + r"""
const uid = B.users.map(d => d._id), sid = B.subscriptions.map(d => d._id);
const clash = {users: db.users.countDocuments({_id: {$in: uid}}), subs: db.subscriptions.countDocuments({_id: {$in: sid}})};
if (clash.users || clash.subs) {
  out('ABORT', {reason: 'some forum docs exist again', clash: clash});
} else if ('""" + mode + r"""' === 'restore') {
  const r = {users: uid.length ? db.users.insertMany(B.users).insertedIds : {}, subs: sid.length ? db.subscriptions.insertMany(B.subscriptions).insertedIds : {}};
  out('RESTORE', {users: Object.keys(r.users).length, subscriptions: Object.keys(r.subs).length, mode: 'restore'});
} else {
  out('RESTORE', {users: uid.length, subscriptions: sid.length, mode: 'restore-dry'});
}
out('END', {});
"""
    raise ValueError(mode)


def run_lines(cmd, stdin=None):
    """Run a container command; return its BD_ results as [(tag, obj)], requiring a BD_END."""
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=AGENT_TIMEOUT)
    results, noise = [], []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("BD_"):
            tag, _, data = line[3:].partition(" ")
            results.append((tag, json.loads(data)))
        elif line.strip():
            noise.append(line)
    if not any(tag == "END" for tag, _ in results):
        tail = " | ".join(noise[-8:] + (proc.stderr or "").strip().splitlines()[-8:])
        raise RuntimeError("%s ended (exit %s) without BD_END: %s" % (cmd[2] if len(cmd) > 2 else cmd, proc.returncode, tail[-900:]))
    for tag, obj in results:
        if tag == "ABORT":
            raise AgentAbort(obj.get("reason") or json.dumps(obj))
    return results


class AgentAbort(RuntimeError):
    pass


def django_agent(args, container, manage, source, payload):
    docker = shutil.which("docker") or "/usr/bin/docker"
    code = "PAYLOAD_B64 = %r\n%s" % (base64.b64encode(json.dumps(payload).encode()).decode(), source)
    cmd = [docker, "exec", "-i", container] + manage + ["-c", "import sys; exec(sys.stdin.read())"]
    return run_lines(cmd, code)


def lms(args, payload):
    return django_agent(args, args.lms_container, ["./manage.py", "lms", "shell"], LMS_AGENT, payload)


def gamma(args, payload):
    return django_agent(args, args.gamma_container, ["./manage.py", "shell"], GAMMA_AGENT, payload)


def mongo(args, script):
    docker = shutil.which("docker") or "/usr/bin/docker"
    return run_lines([docker, "exec", "-i", args.mongo_container, "mongosh", "--quiet", args.forum_db, "--eval", script])


def only(results, tag):
    found = [obj for t, obj in results if t == tag]
    if len(found) != 1:
        raise RuntimeError("expected one BD_%s, got %d" % (tag, len(found)))
    return found[0]


# --------------------------------------------------------------------------- state


def load_processed(state_dir):
    path = os.path.join(state_dir, PROCESSED)
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise RuntimeError("%s is unreadable (%s): fix or remove it" % (path, exc))


def save_json(path, data):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def save_processed(state_dir, processed, now):
    cutoff = (now - KEEP_PROCESSED).isoformat()
    kept = {k: v for k, v in processed.items() if v.get("at", "") >= cutoff}
    save_json(os.path.join(state_dir, PROCESSED), kept)


def record_failure(state_dir, text):
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    with open(os.path.join(state_dir, FAIL_MARKER), "w") as handle:
        handle.write("%s\n%s\n" % (utcnow().isoformat(), text))


def clear_failure(state_dir):
    try:
        os.remove(os.path.join(state_dir, FAIL_MARKER))
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- sending


def tutor_value(tutor, key):
    out = subprocess.run([tutor, "config", "printvalue", key], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("tutor config printvalue %s failed" % key)
    value = out.stdout.strip()
    return "" if value in ("None", "null") else value


def smtp_settings(args):
    get = lambda key: tutor_value(args.tutor, key)  # noqa: E731
    return {
        "host": get("SMTP_HOST"), "port": int(get("SMTP_PORT") or 587),
        "tls": get("SMTP_USE_TLS").lower() == "true", "ssl": get("SMTP_USE_SSL").lower() == "true",
        "user": get("SMTP_USERNAME"), "password": get("SMTP_PASSWORD"),
        "sender": args.sender or get("CONTACT_EMAIL"),
    }


def send(settings, recipient, subject, body):
    message = EmailMessage()
    message["From"] = settings["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if settings["ssl"]:
        client = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=60, context=ssl.create_default_context())
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


# --------------------------------------------------------------------------- the run


def render(host, report):
    deleted, held = report.get("deleted", []), report.get("held", [])
    suspicious, too_many = report.get("suspicious", []), report.get("too_many")
    bits = []
    if deleted:
        bits.append("deleted %d" % len(deleted))
    if held:
        bits.append("held %d" % len(held))
    if suspicious:
        bits.append("%d suspicious notice(s)" % len(suspicious))
    if too_many:
        bits.append("TOO MANY (%d), nothing deleted" % too_many)
    if report.get("error"):
        bits.append("ERROR")
    subject = "[%s] Bounced-activation cleanup: %s" % (host, ", ".join(bits) or "nothing to do")
    lines = ["Accounts whose sign-up activation email bounced as 'Address not found' (ost2_bounce_delete.py).", ""]
    if report.get("error"):
        lines += ["ERROR", "-----", report["error"], ""]
    if too_many:
        lines += ["%d accounts qualified, more than --max-deletes %d, so NOTHING was deleted. After checking the list"
                  " below, run by hand:  python3 ~/ost2-host-scripts/ost2_bounce_delete.py run --to <you> --max-deletes %d"
                  % (too_many, report["max_deletes"], too_many), ""]
    title = "WOULD DELETE" if too_many or report.get("dry") else "DELETED"
    for heading, rows in ((title, deleted), ("HELD (not deleted, worth a look)", held),
                          ("WAITING (still active; checked again next run)", report.get("waiting", []))):
        if not rows:
            continue
        lines += [heading, "-" * len(heading)]
        for r in rows:
            lines.append("  user %s %s <%s>  registered %s" % (r.get("user_id"), r.get("username"), r["email"], (r.get("date_joined") or "")[:16]))
            lines.append("      activation email sent %s, bounced %s: %s" % (r["sent_at"][:16], r["noticed_at"][:16], r["status"]))
            if r.get("reason"):
                lines.append("      %s: %s" % ("waiting" if r.get("outcome") == "wait" else "held because", r["reason"]))
        lines.append("")
    if suspicious:
        lines += ["SUSPICIOUS NOTICES (claim to be Google bounces but failed the checks; nothing done)", "-" * 40]
        for s in suspicious:
            lines.append("  %s  %s  %s: %s" % (s.get("date", ""), s.get("from", ""), s.get("recipient", ""), s["reason"]))
        lines.append("")
    if report.get("run_dir"):
        lines += ["Backups: %s" % report["run_dir"],
                  "Undo:    python3 ~/ost2-host-scripts/ost2_bounce_delete.py restore %s   (dry run; add --apply)" % report["run_dir"], ""]
    lines.append("Notices checked this run: %d (other genuine bounces ignored: %d; activation bounces whose account is already gone: %d)."
                 % (report.get("checked", 0), report.get("other", 0), report.get("gone", 0)))
    return subject, "\n".join(lines) + "\n"


def delete_accounts(args, eligible, run_dir, report):
    """Dump all three stores to run_dir, then delete LMS, gamma, Mongo (each verified against its backup).

    The LMS goes first: if its re-check fails nothing has changed yet.  A failure after that leaves a
    partial delete, so STOP is written and later runs only send reminders until a human has looked.
    """
    users = [{"id": e["user_id"], "username": e["username"], "email": e["email"], "sent_at": e["sent_at"]} for e in eligible]
    os.makedirs(run_dir, mode=0o700)
    lms_dump = only(lms(args, {"mode": "dump", "users": users}), "DUMP")
    gamma_dump = only(gamma(args, {"mode": "dump", "usernames": [u["username"] for u in users]}), "DUMP")
    mongo_dump = only(mongo(args, mongo_script("dump", users)), "DUMP")
    mongo_backup = {"users": mongo_dump["users"], "subscriptions": mongo_dump["subscriptions"]}
    save_json(os.path.join(run_dir, "lms_backup.json"), lms_dump)
    save_json(os.path.join(run_dir, "gamma_backup.json"), gamma_dump)
    save_json(os.path.join(run_dir, "mongo_backup.json"), mongo_backup)
    save_json(os.path.join(run_dir, "users.json"), users)
    report["run_dir"] = run_dir
    mode = "test-apply" if args.test_apply else "apply"
    done = {}
    try:
        done["lms"] = only(lms(args, {"mode": mode, "users": users, "digest": lms_dump["digest"]}), "TEST" if args.test_apply else "DELETED")
        if gamma_dump["rows"]:
            done["gamma"] = only(gamma(args, {"mode": mode, "usernames": [u["username"] for u in users], "digest": gamma_dump["digest"]}),
                                 "TEST" if args.test_apply else "DELETED")
        if mongo_backup["users"] or mongo_backup["subscriptions"]:
            done["mongo"] = only(mongo(args, mongo_script(mode, users, json.dumps(mongo_backup))), "TEST" if args.test_apply else "DELETED")
    except Exception as exc:
        if done and not args.test_apply:
            with open(os.path.join(args.state_dir, STOP), "w") as handle:
                handle.write("%s partial delete in %s: %s\nfinished: %s\n" % (utcnow().isoformat(), run_dir, exc, sorted(done)))
        raise
    finally:
        save_json(os.path.join(run_dir, "result.json"), {"mode": mode, "done": done})
    return done


def run(args):
    now = utcnow()
    os.makedirs(args.state_dir, mode=0o700, exist_ok=True)
    host = tutor_value(args.tutor, "LMS_HOST")
    stop = os.path.join(args.state_dir, STOP)
    if os.path.exists(stop):
        with open(stop) as handle:
            why = handle.read().strip()
        raise RuntimeError("%s exists, so nothing was checked or deleted. Check that run, then remove %s.\n%s" % (stop, stop, why))
    sender, password = tutor_value(args.tutor, "SMTP_USERNAME"), tutor_value(args.tutor, "SMTP_PASSWORD")
    processed = load_processed(args.state_dir)
    mailbox = Mailbox(sender, password)
    try:
        notices = collect_notices(mailbox, sender, args.lookback_days, processed)
    finally:
        mailbox.close()

    report = {"checked": len(notices), "other": 0, "gone": 0, "deleted": [], "held": [], "waiting": [], "suspicious": [], "max_deletes": args.max_deletes,
              "dry": args.dry_run or args.test_apply}
    marks, bounces = {}, {}
    for gm_id, kind, info in notices:
        if kind == "other":
            report["other"] += 1
            marks[gm_id] = {"outcome": "other", "reason": info["reason"]}
        elif kind in ("forged", "unmatched"):
            report["suspicious"].append(info)
            marks[gm_id] = {"outcome": kind, "reason": info["reason"]}
        else:
            bounces.setdefault(info["recipient"], []).append((gm_id, info))

    eligible = []
    if bounces:
        firsts = {addr: min(found, key=lambda x: x[1]["sent_at"])[1] for addr, found in bounces.items()}
        assessed = lms(args, {"mode": "assess", "candidates": [{"email": a, "sent_at": i["sent_at"]} for a, i in firsts.items()]})
        for tag, a in assessed:
            if tag != "ASSESS":
                continue
            row = dict(firsts[a["email"]], **a)
            if a["outcome"] == "eligible":
                eligible.append(row)
            elif a["outcome"] == "hold":
                report["held"].append(row)
            elif a["outcome"] == "wait":
                report["waiting"].append(row)
                continue  # not processed: checked again next run
            else:
                report["gone"] += 1
            for gm_id, _ in bounces[a["email"]]:
                marks[gm_id] = {"outcome": a["outcome"], "recipient": a["email"], "user_id": a.get("user_id")}
        if eligible:
            forum = only(mongo(args, mongo_script("dump", [{"id": e["user_id"], "username": e["username"]} for e in eligible])), "DUMP")
            keep = []
            for e in eligible:
                uid = str(e["user_id"])
                if forum["posts"].get(uid):
                    e["reason"] = "has %d posts in the Mongo forum store" % forum["posts"][uid]
                elif uid in forum["wrong_username"]:
                    e["reason"] = "Mongo forum user doc has a different username"
                else:
                    keep.append(e)
                    continue
                report["held"].append(e)
                for gm_id, _ in bounces[e["email"]]:
                    marks[gm_id]["outcome"] = "hold"
            eligible = keep

    if len(eligible) > args.max_deletes:
        report["too_many"] = len(eligible)
        report["deleted"] = eligible
        for e in eligible:  # leave them unprocessed so the next run reports them again
            for gm_id, _ in bounces[e["email"]]:
                marks.pop(gm_id, None)
        eligible = []
    elif eligible and not args.dry_run:
        run_dir = os.path.join(args.state_dir, "tests" if args.test_apply else "runs", now.strftime("%Y%m%dT%H%M%SZ"))
        done = delete_accounts(args, eligible, run_dir, report)
        report["deleted"] = eligible
        report["done"] = done
        if args.test_apply:
            report["test_apply"] = True
    elif eligible:
        report["deleted"] = eligible

    subject, body = render(host, report)
    if args.dry_run or args.test_apply:
        print("DRY RUN%s: nothing deleted, no state saved, no email.\nSubject: %s\n\n%s" % (
            " (test-apply: the deletes ran and were rolled back; backups in %s)" % report.get("run_dir") if args.test_apply else "",
            subject, body))
        if args.test_apply:
            print(json.dumps(report.get("done", {}), indent=1))
        return 0
    for gm_id, mark in marks.items():
        processed[gm_id] = dict(mark, at=now.isoformat())
    save_processed(args.state_dir, processed, now)
    if report["deleted"] or report["held"] or report["suspicious"] or report.get("too_many"):
        send(smtp_settings(args), args.to, subject, body)
    clear_failure(args.state_dir)
    print("%s checked=%d deleted=%d held=%d waiting=%d suspicious=%d other=%d gone=%d%s" % (
        now.isoformat(), report["checked"], 0 if report.get("too_many") else len(report["deleted"]), len(report["held"]),
        len(report["waiting"]), len(report["suspicious"]), report["other"], report["gone"], " TOO-MANY=%d" % report["too_many"] if report.get("too_many") else ""))
    return 0


def restore(args):
    run_dir = os.path.abspath(args.run_dir)
    with open(os.path.join(run_dir, "users.json")) as handle:
        users = json.load(handle)
    mode = "restore" if args.apply else "restore-dry"
    with open(os.path.join(run_dir, "lms_backup.json")) as handle:
        lms_backup = json.load(handle)
    with open(os.path.join(run_dir, "gamma_backup.json")) as handle:
        gamma_backup = json.load(handle)
    with open(os.path.join(run_dir, "mongo_backup.json")) as handle:
        mongo_backup = handle.read()
    print("LMS  ", only(lms(args, {"mode": mode, "tables_b64": lms_backup["tables_b64"]}), "RESTORE"))
    if gamma_backup["rows"]:
        print("gamma", only(gamma(args, {"mode": mode, "usernames": [u["username"] for u in users], "tables_b64": gamma_backup["tables_b64"]}), "RESTORE"))
    if json.loads(mongo_backup)["users"] or json.loads(mongo_backup)["subscriptions"]:
        print("mongo", only(mongo(args, mongo_script(mode, users, mongo_backup)), "RESTORE"))
    print("restored" if args.apply else "dry run: everything rolled back; add --apply to restore")
    return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="check the mailbox and delete qualifying accounts")
    r.add_argument("--to", required=True, help="who gets the report email")
    r.add_argument("--lookback-days", type=int, default=7)
    r.add_argument("--max-deletes", type=int, default=10)
    r.add_argument("--dry-run", action="store_true", help="check and print; delete, save and send nothing")
    r.add_argument("--test-apply", action="store_true", help="like --dry-run, plus run the deletes and roll them back")
    r.add_argument("--sender", help="report From address (default: tutor CONTACT_EMAIL)")
    s = sub.add_parser("restore", help="put a run's deleted accounts back")
    s.add_argument("run_dir")
    s.add_argument("--apply", action="store_true", help="really restore (default: dry run, rolled back)")
    for p in (r, s):
        p.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
        p.add_argument("--tutor", default=DEFAULT_TUTOR)
        p.add_argument("--lms-container", default="tutor_local-lms-1")
        p.add_argument("--gamma-container", default="tutor_local-rgg-1")
        p.add_argument("--mongo-container", default="tutor_local-mongodb-1")
        p.add_argument("--forum-db", default="cs_comments_service")
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "restore":
        return restore(args)
    try:
        return run(args)
    except Exception:  # noqa: BLE001 - cron has nobody watching; leave a marker and fail loudly
        text = traceback.format_exc()
        traceback.print_exc()
        if not (args.dry_run or args.test_apply):
            record_failure(args.state_dir, text)
            try:
                host = tutor_value(args.tutor, "LMS_HOST")
                subject, body = render(host, {"error": text.strip().splitlines()[-1] + "\n\n" + text[-3000:]})
                send(smtp_settings(args), args.to, subject, body)
            except Exception:  # noqa: BLE001 - the failure marker and cron.log still have it
                traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
