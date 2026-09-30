"""
Tutor plugin: ost2_report_missing_accomplishments   (OST2)

Backend for the "Report missing accomplishments" link on the gamma dashboard
(/gamma_dashboard/dashboard/). Adds a login-required LMS page

    https://<LMS_HOST>/report-missing-accomplishments/

with a single free-text box ("Which accomplishments do you think you should already have
received on the site, but which aren't showing up on the Your Accomplishments page?").
Submitting emails the reporter, their profile link and the message to _OST2_RM_TO below,
through the rate-limited SMTP backend. Same mechanism as ost2_report_missing_accomplishments:
injected into the LMS production settings and mounted on the root urlconf on first request,
so it needs only `tutor config save` + `tutor local restart lms` -- NO image rebuild.

Deploy:
    cp ost2_report_missing_accomplishments.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_report_missing_accomplishments
    tutor config save && tutor local restart lms
"""
from tutor import hooks

__version__ = "1.0.0"

_CODE = '''
# ============================================================================
# OST2: "Report inappropriate content" page (profile link -> form -> email)
# Injected by the ost2_report_missing_accomplishments tutor plugin.
# ============================================================================
import logging as _ost2_rm_logging

_ost2_rm_log = _ost2_rm_logging.getLogger("ost2.report_missing")

_OST2_RM_TO = "xeno@ost2.fyi"
# Real SMTP (rate-limited) path, used explicitly so reports are delivered even on dev,
# where ost2_dev_mail_to_files redirects the DEFAULT backend to files.
_OST2_RM_BACKEND = "openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend"
_OST2_RM_MAX_CHARS = 2000
_OST2_RM_MAX_PER_HOUR = 10

_OST2_RM_CSS = (
    ":root{--bg:#fff;--fg:#1f2937;--muted:#5b6472;--card:#fff;--border:#d9dde3;--link:#0b6fa4;--err:#b00020;--btn:#0b6fa4;--btnfg:#fff}"
    "@media (prefers-color-scheme: dark){:root{--bg:#111827;--fg:#e5e7eb;--muted:#9ca3af;--card:#1f2937;--border:#374151;--link:#7cc4ee;--err:#ff8a9b;--btn:#2b8cc4;--btnfg:#fff}}"
    "body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}"
    "main{max-width:720px;margin:0 auto;padding:32px 16px}"
    "h1{font-size:1.6rem;margin:0 0 .5rem}"
    "a{color:var(--link)}"
    ".card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px}"
    ".muted{color:var(--muted)}"
    ".err{color:var(--err);font-weight:600}"
    "label.opt{display:flex;gap:10px;align-items:flex-start;padding:8px 0;cursor:pointer}"
    "label.opt input{margin-top:5px;flex:none}"
    "textarea{width:100%;box-sizing:border-box;padding:8px;font:inherit;color:inherit;background:var(--bg);border:1px solid var(--border);border-radius:6px}"
    "button{margin-top:16px;padding:9px 22px;font:inherit;font-weight:600;color:var(--btnfg);background:var(--btn);border:0;border-radius:6px;cursor:pointer}"
)


def _ost2_rm_shell(title, body):
    from django.utils.html import escape
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<title>@@TITLE@@</title><style>@@CSS@@</style></head>'
        '<body><main>@@BODY@@</main></body></html>'
    ).replace("@@TITLE@@", escape(title)).replace("@@CSS@@", _OST2_RM_CSS).replace("@@BODY@@", body)


def _ost2_rm_profile_url(request, username):
    from urllib.parse import quote
    from django.conf import settings
    base = getattr(settings, "PROFILE_MICROFRONTEND_URL", None)
    if not base:
        base = "https://apps." + request.get_host().split(":")[0] + "/profile"
    base = base.rstrip("/")
    if base.endswith("/u"):
        base = base[:-2]
    return base + "/u/" + quote(username)


def _ost2_rm_form(csrf, text, error):
    from django.utils.html import escape
    body = (
        '<h1>Report missing accomplishments</h1>'
        '@@ERR@@'
        '<form method="post" class="card">'
        '<input type="hidden" name="csrfmiddlewaretoken" value="@@CSRF@@">'
        '<p style="margin-top:0"><label for="missing_text"><strong>Which accomplishments do you think you should already have '
        'received on the site, but which aren&#39;t showing up on the Your Accomplishments page?</strong> '
        '<span class="muted">(up to @@MAX@@ characters)</span></label></p>'
        '<textarea id="missing_text" name="missing_text" rows="8" maxlength="@@MAX@@" required>@@TEXT@@</textarea>'
        '<button type="submit">Submit</button>'
        '</form>'
    )
    err_html = ('<p class="err" role="alert">' + escape(error) + '</p>') if error else ""
    return (body.replace("@@ERR@@", err_html).replace("@@CSRF@@", escape(csrf))
            .replace("@@MAX@@", str(_OST2_RM_MAX_CHARS)).replace("@@TEXT@@", escape(text)))


def _ost2_rm_view(request):
    from django.contrib.auth.views import redirect_to_login
    from django.core.cache import cache
    from django.core.mail import get_connection, send_mail
    from django.conf import settings
    from django.http import HttpResponse
    from django.middleware.csrf import get_token
    from django.utils import timezone

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())

    text, error = "", ""
    if request.method == "POST":
        text = request.POST.get("missing_text", "").strip()
        if not text:
            error = "Please describe the accomplishments you think are missing."
        elif len(text) > _OST2_RM_MAX_CHARS:
            error = "The description must be at most %d characters." % _OST2_RM_MAX_CHARS
        else:
            key = "ost2_report_missing_count_%d" % request.user.pk
            count = cache.get(key, 0)
            if count >= _OST2_RM_MAX_PER_HOUR:
                error = "You have submitted too many reports recently. Please try again later."
            else:
                cache.set(key, count + 1, 3600)
                lines = [
                    "Reported by:    %s <%s>" % (request.user.username, request.user.email),
                    "Profile:        " + _ost2_rm_profile_url(request, request.user.username),
                    "Submitted:      " + timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "",
                    "Accomplishments the user believes are missing:",
                    "",
                    text,
                ]
                send_mail(
                    "[OST2 report] Missing accomplishments: " + request.user.username,
                    "\\n".join(lines),
                    settings.DEFAULT_FROM_EMAIL,
                    [_OST2_RM_TO],
                    fail_silently=False,
                    connection=get_connection(_OST2_RM_BACKEND),
                )
                _ost2_rm_log.info("missing-accomplishments report filed by %s", request.user.username)
                done = (
                    '<h1>Thank you</h1><p>Your report has been sent to the OST2 team.</p>'
                    '<p><a href="/gamma_dashboard/dashboard/">Back to the dashboard</a></p>'
                )
                return HttpResponse(_ost2_rm_shell("Report sent", done))

    body = _ost2_rm_form(get_token(request), text, error)
    resp = HttpResponse(_ost2_rm_shell("Report missing accomplishments", body), status=400 if error else 200)
    resp["Cache-Control"] = "no-store"
    return resp


def _ost2_rm_install():
    import importlib
    from django.urls import re_path
    from django.conf import settings
    urls = importlib.import_module(settings.ROOT_URLCONF)
    if getattr(urls, "_ost2_rm_installed", False):
        return
    urls.urlpatterns.insert(0, re_path(r"^report-missing-accomplishments/$", _ost2_rm_view, name="ost2_report_missing"))
    urls._ost2_rm_installed = True
    _ost2_rm_log.info("ost2 report-missing-accomplishments page installed")


from django.core.signals import request_started as _ost2_rm_req
from django.dispatch import receiver as _ost2_rm_receiver


@_ost2_rm_receiver(_ost2_rm_req, dispatch_uid="ost2_report_missing_install")
def _ost2_rm_boot(sender, **kwargs):
    try:
        _ost2_rm_install()
    except Exception:
        _ost2_rm_log.exception("ost2 report-missing: install failed")
'''

hooks.Filters.ENV_PATCHES.add_item(("openedx-lms-production-settings", _CODE))
