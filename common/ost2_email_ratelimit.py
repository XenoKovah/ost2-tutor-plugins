"""
Tutor plugin: ost2_email_ratelimit

Makes Open edX send mail gracefully within Google Workspace limits instead of
bursting past them. OST2 relays ALL mail through one Workspace user over
smtp.gmail.com, which throttles bursts (421 4.7.0 "Try again later, closing
connection") and hard-caps a single user at ~2,000 messages / 24h (10,000 via
the smtp-relay service). The stock bulk_email engine is tuned for AWS SES: it
paces only AFTER being throttled, has no global ceiling across the 8-way worker
pool, and on a 5xx daily-quota reply it counts the remaining recipients as
failures and DROPS them.

This plugin supplies the *configuration* half of the fix. The *code* half lives
in the edx-platform `teak3_2_email-rate-limiting` branch (off release/teak.3 / ap's
b4d353d):
  * lms/djangoapps/bulk_email/tasks.py  -- defer (don't drop) provider
    rate-limit / daily-quota responses, and
  * openedx/core/lib/ost2_ratelimit_email_backend.py  -- a drop-in SMTP
    EMAIL_BACKEND that paces every outgoing message against one Redis-coordinated
    global budget (even per-minute rate + rolling-24h cap).
Build that image and point EDX_PLATFORM_VERSION at the branch BEFORE enabling
this plugin (otherwise EMAIL_BACKEND below ImportErrors). See
EMAIL_RATELIMIT_RUNBOOK.md.

Three pieces:

  Piece 1 (Tier 1 -- pacing, works on ANY image): one recipient per Celery
    subtask + a per-worker-node Celery rate_limit on send_course_email, so bulk
    dispatch is paced to a fixed messages/minute and the prefork pool does not
    all hit the wire at once. Open edX loads Celery with
    config_from_object('django.conf:settings') and NO namespace (legacy names,
    e.g. BROKER_URL), so the annotation key is CELERY_ANNOTATIONS -- NOT
    task_annotations. Longer retry delays/max let a transient throttle ride out.

  Piece 2 (Tier 3b -- the global ceiling, needs the fork image): EMAIL_BACKEND
    points at the Redis-paced backend so BOTH bulk and transactional (edx-ace)
    mail share one budget. Tunables: OST2_EMAIL_*.

  Piece 3 (Tier 3a -- headroom, NOT done here): switch SMTP_HOST to
    smtp-relay.gmail.com (10,000/day) -- a `tutor config save --set` plus Google
    Admin console setup. See the runbook; bump OST2_EMAIL_DAILY_CAP afterwards.

No "{" characters appear in the settings patches below: tutor renders ENV_PATCHES
through Jinja, so CELERY_ANNOTATIONS is built with dict()/subscript assignment
rather than a "{...}" literal.

Deploy (after the fork image is built -- see runbook):
    cp ost2_email_ratelimit.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_email_ratelimit
    tutor config save
    tutor local restart lms cms lms-worker cms-worker
"""
from tutor import hooks

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# Tier 3b: global Redis-paced EMAIL_BACKEND for ALL mail (bulk + transactional).
# Applied to both LMS and CMS production settings so every sender shares one
# budget. The backend subclasses Django's SMTP backend, so EMAIL_HOST /
# EMAIL_HOST_USER / EMAIL_USE_TLS (set by tutor from SMTP_*) are inherited.
# Requires the teak3_2_email-rate-limiting edx-platform image (ships the module). To run on
# the STOCK image, comment out the EMAIL_BACKEND line -- Piece 1 still paces bulk.
# ---------------------------------------------------------------------------
_LIMITER_SETTINGS = '''
# OST2 graceful email rate limiting -- global ceiling (Tier 3b)
EMAIL_BACKEND = "openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend"
OST2_EMAIL_RATE_PER_MIN = 30
OST2_EMAIL_DAILY_CAP = 1800
OST2_EMAIL_MAX_BLOCK_SECONDS = 15
OST2_EMAIL_FAIL_OPEN = True
'''

# ---------------------------------------------------------------------------
# Tier 1: pace bulk course-email dispatch (LMS only). Pure settings -- effective
# on any image. Built without "{" for the Jinja renderer.
# ---------------------------------------------------------------------------
_BULK_SETTINGS = '''
# OST2 graceful email rate limiting -- bulk dispatch pacing (Tier 1)
BULK_EMAIL_EMAILS_PER_TASK = 1
BULK_EMAIL_DEFAULT_RETRY_DELAY = 300
BULK_EMAIL_MAX_RETRIES = 10
try:
    CELERY_ANNOTATIONS
except NameError:
    CELERY_ANNOTATIONS = dict()
CELERY_ANNOTATIONS["lms.djangoapps.bulk_email.tasks.send_course_email"] = dict(rate_limit="30/m")
'''

# LMS gets the ceiling + bulk pacing; CMS gets the ceiling only (no bulk email there).
hooks.Filters.ENV_PATCHES.add_item(
    ("openedx-lms-production-settings", _LIMITER_SETTINGS + _BULK_SETTINGS)
)
hooks.Filters.ENV_PATCHES.add_item(
    ("openedx-cms-production-settings", _LIMITER_SETTINGS)
)
