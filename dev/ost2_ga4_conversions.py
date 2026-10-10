"""
Tutor plugin: ost2_ga4_conversions   (OST2)

Sends Google Analytics 4 events from the LMS server when a learner

  * creates an account                       -> sign_up
  * enrolls in a course                      -> course_enroll     (course_id, course_number)
  * enrolls in a course for the first time   -> first_enrollment  (course_id, course_number)

so they can be marked as GA4 key events and imported into Google Ads (Ad Grants) as
conversions. Ad Grants requires conversion tracking, and the browser-side GA4 tag
(GoogleAnalytics4Plugin) only records page views.

Why server-side: a learner can enroll from the course About page button, from the automatic
enroll after registering through the authn MFE, or from the Learning MFE "Enroll now" link.
All of them end in CourseEnrollment.enroll() -> ENROLL_STATUS_CHANGE, and every registration
sends REGISTER_USER, so hooking those two Django signals catches every path.

Attribution: each event carries the visitor's own GA client id and session id, read from the
`_ga` and `_ga_<stream>` cookies that the GA4 tag sets on .ost2.fyi, and goes out through the
GA4 Measurement Protocol. GA joins it to the browser session the ad click started and exports
key events to the linked Google Ads account. Events are sent only for the learner's own
request (staff bulk enrolls, management commands and celery tasks are skipped), only after
the database transaction commits, and only when the visitor has a `_ga` cookie.

Config (`tutor config save --set NAME=value`):
  OST2_GA4_CONV_MODE            off | validate | send   (default: validate)
        validate -> GA's /debug/mp/collect validation server; the result is logged and
                    nothing is recorded in GA. Use on dev.
        send     -> /mp/collect, recorded in GA. Use on p only.
  OST2_GA4_CONV_MEASUREMENT_ID  GA4 measurement id. Empty (default) = the LMS setting
                                GOOGLE_ANALYTICS_4_ID from GoogleAnalytics4Plugin. dev has
                                that plugin disabled and sets a dummy id instead, so dev can
                                never write into p's GA property.
  OST2_GA4_CONV_API_SECRET      Measurement Protocol API secret (GA4 Admin > Data streams >
                                <web stream> > Measurement Protocol API secrets). Needed for
                                send. Set it on the box only; never commit it.

Logs: logger "ost2.ga4_conversions" in the LMS log. The API secret is never logged.
LMS-only, no image rebuild: `tutor config save` + `tutor local restart lms`.
"""
from tutor import hooks

__version__ = "1.0.0"

hooks.Filters.CONFIG_DEFAULTS.add_items(
    [
        ("OST2_GA4_CONV_MODE", "validate"),
        ("OST2_GA4_CONV_MEASUREMENT_ID", ""),
        ("OST2_GA4_CONV_API_SECRET", ""),
    ]
)

# Tutor renders this string with Jinja: the three config values below are the only
# intended template expressions, so the Python must not contain any other double braces.
_CODE = r"""
# ============================================================================
# OST2: GA4 conversion events. Injected by the ost2_ga4_conversions tutor plugin.
# ============================================================================
import logging as _ost2_ga4_logging
_ost2_ga4_log = _ost2_ga4_logging.getLogger("ost2.ga4_conversions")

OST2_GA4_CONV_MODE = {{ OST2_GA4_CONV_MODE|tojson }}
OST2_GA4_CONV_MEASUREMENT_ID = {{ OST2_GA4_CONV_MEASUREMENT_ID|tojson }}
OST2_GA4_CONV_API_SECRET = {{ OST2_GA4_CONV_API_SECRET|tojson }}

# A learner's first-ever enrollment row must be this new to count as first_enrollment, so a
# re-enroll into the only course they ever unenrolled from does not count again.
_OST2_GA4_FIRST_MAX_AGE_SECONDS = 600


def ost2_ga4_client_id(cookies):
    # _ga = "GA1.1.<random>.<first-visit timestamp>" -> client id "<random>.<timestamp>"
    parts = (cookies.get("_ga") or "").split(".")
    if len(parts) >= 4 and parts[-2].isdigit() and parts[-1].isdigit():
        return parts[-2] + "." + parts[-1]
    return None


def ost2_ga4_session_id(cookies, measurement_id):
    # The session cookie is _ga_<measurement id without "G-">. If that exact name is absent
    # (dev uses a dummy id) and exactly one _ga_* cookie exists, use that one.
    value = None
    if measurement_id.startswith("G-"):
        value = cookies.get("_ga_" + measurement_id[2:])
    if value is None:
        names = [n for n in cookies if n.startswith("_ga_")]
        if len(names) == 1:
            value = cookies.get(names[0])
    if not value:
        return None
    if value.startswith("GS2."):
        # GS2.1.s<session id>$o<session count>$g..$t..$j..$l..$h..
        for item in value.split(".", 2)[-1].split("$"):
            if item[:1] == "s" and item[1:].isdigit():
                return item[1:]
        return None
    # GS1.1.<session id>.<session count>.<engaged>.<last hit>.0.0.0
    parts = value.split(".")
    if len(parts) >= 3 and parts[2].isdigit():
        return parts[2]
    return None


def _ost2_ga4_config():
    from django.conf import settings
    mode = (OST2_GA4_CONV_MODE or "off").strip().lower()
    mid = (OST2_GA4_CONV_MEASUREMENT_ID or getattr(settings, "GOOGLE_ANALYTICS_4_ID", "") or "").strip()
    secret = (OST2_GA4_CONV_API_SECRET or "").strip()
    if mode not in ("validate", "send") or not mid:
        return None
    if mode == "send" and not secret:
        return None
    return mode, mid, secret or "validation-only"


def _ost2_ga4_post(mode, mid, secret, payload, label):
    import requests
    path = "/debug/mp/collect" if mode == "validate" else "/mp/collect"
    try:
        resp = requests.post(
            "https://www.google-analytics.com" + path,
            params=dict(measurement_id=mid, api_secret=secret),
            json=payload,
            timeout=5,
        )
        if mode == "validate":
            try:
                messages = resp.json().get("validationMessages")
            except ValueError:
                messages = "unparseable response"
            _ost2_ga4_log.info("ost2 ga4 validate %s: HTTP %s validationMessages=%s", label, resp.status_code, messages)
        else:
            _ost2_ga4_log.info("ost2 ga4 sent %s: HTTP %s", label, resp.status_code)
    except Exception as exc:
        # Only the exception type: requests errors include the URL, which holds the API secret.
        _ost2_ga4_log.warning("ost2 ga4 post failed %s: %s", label, type(exc).__name__)


def ost2_ga4_build_payload(request, events, mid):
    # events: list of (name, params). Returns the Measurement Protocol body, or None when the
    # visitor has no GA client id (GA blocked or never loaded).
    client_id = ost2_ga4_client_id(request.COOKIES)
    if client_id is None:
        return None
    session_id = ost2_ga4_session_id(request.COOKIES, mid)
    out = []
    for name, params in events:
        p = dict(params)
        p["engagement_time_msec"] = 1
        if session_id:
            p["session_id"] = session_id
        out.append(dict(name=name, params=p))
    return dict(client_id=client_id, events=out)


def _ost2_ga4_queue(request, events, label):
    import threading
    from django.db import transaction
    cfg = _ost2_ga4_config()
    if cfg is None:
        return
    mode, mid, secret = cfg
    payload = ost2_ga4_build_payload(request, events, mid)
    names = ",".join(name for name, _ in events)
    if payload is None:
        _ost2_ga4_log.info("ost2 ga4 skip %s %s: no _ga cookie", names, label)
        return
    has_session = "session_id" in payload["events"][0]["params"]
    _ost2_ga4_log.info("ost2 ga4 queue %s %s (session_id %s)", names, label, "yes" if has_session else "no")

    def _start():
        threading.Thread(
            target=_ost2_ga4_post, args=(mode, mid, secret, payload, names + " " + label), daemon=True
        ).start()

    transaction.on_commit(_start)


def _ost2_ga4_learner_request(user):
    # The current request, only if it is the learner's own (not staff enrolling someone else).
    from crum import get_current_request
    request = get_current_request()
    if request is None or user is None:
        return None
    current = getattr(request, "user", None)
    if current is None or not current.is_authenticated or current.id != user.id:
        return None
    return request


def _ost2_ga4_is_first_enrollment(user, course_key):
    import datetime
    from django.utils import timezone
    from common.djangoapps.student.models import CourseEnrollment
    rows = list(CourseEnrollment.objects.filter(user=user).values_list("course_id", "created")[:2])
    if len(rows) != 1 or str(rows[0][0]) != str(course_key):
        return False
    return rows[0][1] >= timezone.now() - datetime.timedelta(seconds=_OST2_GA4_FIRST_MAX_AGE_SECONDS)


def _ost2_ga4_on_enroll(sender, event=None, user=None, course_id=None, **kwargs):
    try:
        if event != "enroll" or course_id is None:
            return
        request = _ost2_ga4_learner_request(user)
        if request is None:
            return
        params = dict(
            course_id=str(course_id)[:100],
            course_number=str(getattr(course_id, "course", ""))[:100],
        )
        events = [("course_enroll", params)]
        if _ost2_ga4_is_first_enrollment(user, course_id):
            events.append(("first_enrollment", params))
        _ost2_ga4_queue(request, events, "user=%s course=%s" % (user.id, course_id))
    except Exception:
        _ost2_ga4_log.exception("ost2 ga4: enroll handler failed (enrollment unaffected)")


def _ost2_ga4_on_register(sender, user=None, registration=None, **kwargs):
    try:
        request = _ost2_ga4_learner_request(user)
        if request is None:
            return
        _ost2_ga4_queue(request, [("sign_up", dict())], "user=%s" % user.id)
    except Exception:
        _ost2_ga4_log.exception("ost2 ga4: register handler failed (registration unaffected)")


_OST2_GA4_STATE = dict(installed=False)


def _ost2_ga4_install():
    if _OST2_GA4_STATE["installed"]:
        return
    from common.djangoapps.student.signals import ENROLL_STATUS_CHANGE
    from openedx.core.djangoapps.user_authn.views.register import REGISTER_USER
    ENROLL_STATUS_CHANGE.connect(_ost2_ga4_on_enroll, weak=False, dispatch_uid="ost2_ga4_conv_enroll")
    REGISTER_USER.connect(_ost2_ga4_on_register, weak=False, dispatch_uid="ost2_ga4_conv_register")
    _OST2_GA4_STATE["installed"] = True
    _ost2_ga4_log.info("ost2 ga4 conversions installed (mode=%s)", OST2_GA4_CONV_MODE)


from django.core.signals import request_started as _ost2_ga4_req
from django.dispatch import receiver as _ost2_ga4_receiver


@_ost2_ga4_receiver(_ost2_ga4_req, dispatch_uid="ost2_ga4_conv_install")
def _ost2_ga4_boot(sender, **kwargs):
    try:
        _ost2_ga4_install()
    except Exception:
        _ost2_ga4_log.exception("ost2 ga4: install failed")
"""

hooks.Filters.ENV_PATCHES.add_item(("openedx-lms-production-settings", _CODE))
