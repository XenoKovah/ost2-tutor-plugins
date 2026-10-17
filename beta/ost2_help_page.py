"""
Tutor plugin: ost2_help_page   (OST2)

Target of the "Help" link in the MFE headers (the learning MFE's learning_help_slot renders
`<a href={getConfig().SUPPORT_URL}>Help</a>`; SUPPORT_URL was unset, so the link pointed at
the relative URL "null"). This plugin:

  1. adds an LMS page   https://<LMS_HOST>/help
     -- a list of common problems, one per line, each collapsed in a <details>/<summary>
     (the same expand mechanism as the gamification FAQ units), expanding to the proposed
     solution. The last item, "Something else", expands to a form (URL + Issue) that emails
     the user's username, email and both fields to _OST2_HP_TO through the rate-limited
     SMTP backend. The list itself is public; submitting the form requires sign-in.
     It replaces the stock (empty) /help static template.
  2. sets MFE_CONFIG["SUPPORT_URL"] to that page, so every MFE header's Help link goes there.

To add/edit problems, change _OST2_HP_ITEMS below. Answers are trusted HTML;
"@@ACCOUNT_URL@@" is replaced with this box's account-settings MFE URL.

Same mechanism as ost2_report_missing_accomplishments: injected into the LMS production
settings and mounted on the root urlconf on first request, so it needs only
`tutor config save` + `tutor local restart lms` -- NO image rebuild.

Deploy:
    cp ost2_help_page.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_help_page
    tutor config save && tutor local restart lms
"""
from tutor import hooks

__version__ = "1.0.0"

_CODE = '''
# ============================================================================
# OST2: /help page (common problems + "Something else" form -> email)
# Injected by the ost2_help_page tutor plugin.
# ============================================================================
import logging as _ost2_hp_logging

_ost2_hp_log = _ost2_hp_logging.getLogger("ost2.help_page")

_OST2_HP_TO = "xeno@ost2.fyi"
# Real SMTP (rate-limited) path, used explicitly so requests are delivered even where the
# DEFAULT backend is redirected to files.
_OST2_HP_BACKEND = "openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend"
_OST2_HP_MAX_URL = 500
_OST2_HP_MAX_ISSUE = 4000
_OST2_HP_MAX_PER_HOUR = 10

# (question, answer HTML). Shown in this order, before the "Something else" form.
_OST2_HP_ITEMS = [
    (
        "How can I change my email?",
        '<p>Go to your <a href="@@ACCOUNT_URL@@">account settings</a>.</p>',
    ),
    (
        "How can I change my username?",
        "<p>Unfortunately it&#39;s not possible to change your username on the Open edX learning "
        "management server we use. Email xeno at ost2.fyi for him to help with that.</p>",
    ),
]

_OST2_HP_CSS = (
    ":root{--bg:#fff;--fg:#1f2937;--muted:#5b6472;--card:#fff;--border:#d9dde3;--link:#0b6fa4;--err:#b00020;--btn:#0b6fa4;--btnfg:#fff}"
    "@media (prefers-color-scheme: dark){:root{--bg:#111827;--fg:#e5e7eb;--muted:#9ca3af;--card:#1f2937;--border:#374151;--link:#7cc4ee;--err:#ff8a9b;--btn:#2b8cc4;--btnfg:#fff}}"
    "body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}"
    "main{max-width:720px;margin:0 auto;padding:32px 16px}"
    "h1{font-size:1.6rem;margin:0 0 1rem}"
    "a{color:var(--link)}"
    "details{margin:0 0 .75rem}"
    "summary{font-size:1.5em;font-weight:bold;cursor:pointer}"
    ".answer{padding:.25rem 0 .5rem 1.6rem}"
    ".card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px}"
    ".muted{color:var(--muted)}"
    ".err{color:var(--err);font-weight:600}"
    "label{display:block;font-weight:600;margin:0 0 4px}"
    "input[type=text],textarea{width:100%;box-sizing:border-box;padding:8px;font:inherit;color:inherit;background:var(--bg);border:1px solid var(--border);border-radius:6px}"
    "textarea{margin:0}"
    ".field{margin:0 0 16px}"
    "button{padding:9px 22px;font:inherit;font-weight:600;color:var(--btnfg);background:var(--btn);border:0;border-radius:6px;cursor:pointer}"
)


def _ost2_hp_shell(title, body):
    from django.utils.html import escape
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<title>@@TITLE@@</title><style>@@CSS@@</style></head>'
        '<body><main>@@BODY@@</main></body></html>'
    ).replace("@@TITLE@@", escape(title)).replace("@@CSS@@", _OST2_HP_CSS).replace("@@BODY@@", body)


def _ost2_hp_account_url(request):
    from django.conf import settings
    url = getattr(settings, "ACCOUNT_MICROFRONTEND_URL", None)
    if not url:
        url = "https://apps." + request.get_host().split(":")[0] + "/account/"
    return url


def _ost2_hp_profile_url(request, username):
    from urllib.parse import quote
    from django.conf import settings
    base = getattr(settings, "PROFILE_MICROFRONTEND_URL", None)
    if not base:
        base = "https://apps." + request.get_host().split(":")[0] + "/profile"
    base = base.rstrip("/")
    if base.endswith("/u"):
        base = base[:-2]
    return base + "/u/" + quote(username)


def _ost2_hp_other(request, csrf, url, issue, error):
    from urllib.parse import quote
    from django.utils.html import escape
    if not request.user.is_authenticated:
        return (
            '<p>Please <a href="/login?next=' + quote("/help#something-else") + '">sign in</a> '
            'and come back to this page to send us a description of your problem. '
            'If you can&#39;t sign in, email xeno at ost2.fyi.</p>'
        )
    body = (
        '@@ERR@@'
        '<form method="post" action="/help#something-else" class="card">'
        '<input type="hidden" name="csrfmiddlewaretoken" value="@@CSRF@@">'
        '<div class="field"><label for="help_url">URL</label>'
        '<input type="text" id="help_url" name="help_url" maxlength="@@MAXURL@@" value="@@URL@@" '
        'placeholder="The address of the page you need help with, if any"></div>'
        '<div class="field"><label for="help_issue">Issue</label>'
        '<textarea id="help_issue" name="help_issue" rows="8" maxlength="@@MAXISSUE@@" required>@@ISSUE@@</textarea></div>'
        '<button type="submit">Submit</button>'
        '</form>'
    )
    err_html = ('<p class="err" role="alert">' + escape(error) + '</p>') if error else ""
    return (body.replace("@@ERR@@", err_html).replace("@@CSRF@@", escape(csrf))
            .replace("@@MAXURL@@", str(_OST2_HP_MAX_URL)).replace("@@MAXISSUE@@", str(_OST2_HP_MAX_ISSUE))
            .replace("@@URL@@", escape(url)).replace("@@ISSUE@@", escape(issue)))


def _ost2_hp_page(request, csrf, url, issue, error):
    from django.utils.html import escape
    account_url = escape(_ost2_hp_account_url(request))
    parts = ['<h1>Help</h1>']
    for question, answer in _OST2_HP_ITEMS:
        parts.append(
            '<details><summary>' + escape(question) + '</summary>'
            '<div class="answer">' + answer.replace("@@ACCOUNT_URL@@", account_url) + '</div></details>'
        )
    parts.append(
        '<details id="something-else"' + (' open' if error else '') + '><summary>Something else</summary>'
        '<div class="answer">' + _ost2_hp_other(request, csrf, url, issue, error) + '</div></details>'
        # /help#something-else (e.g. back from sign-in) opens the form.
        '<script>if (location.hash === "#something-else") '
        'document.getElementById("something-else").open = true;</script>'
    )
    return "".join(parts)


def _ost2_hp_view(request):
    from urllib.parse import urlsplit
    from django.core.cache import cache
    from django.core.mail import EmailMessage, get_connection
    from django.conf import settings
    from django.http import HttpResponse
    from django.middleware.csrf import get_token
    from django.utils import timezone

    url, issue, error = "", "", ""
    if request.method == "POST" and request.user.is_authenticated:
        url = request.POST.get("help_url", "").strip()
        issue = request.POST.get("help_issue", "").strip()
        if not issue:
            error = "Please describe the issue you need help with."
        elif len(issue) > _OST2_HP_MAX_ISSUE:
            error = "The issue description must be at most %d characters." % _OST2_HP_MAX_ISSUE
        elif len(url) > _OST2_HP_MAX_URL:
            error = "The URL must be at most %d characters." % _OST2_HP_MAX_URL
        else:
            key = "ost2_help_count_%d" % request.user.pk
            count = cache.get(key, 0)
            if count >= _OST2_HP_MAX_PER_HOUR:
                error = "You have submitted too many requests recently. Please try again later."
            else:
                cache.set(key, count + 1, 3600)
                lines = [
                    "Username:       " + request.user.username,
                    "Email:          " + request.user.email,
                    "Profile:        " + _ost2_hp_profile_url(request, request.user.username),
                    "Submitted:      " + timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "",
                    "URL:            " + (url or "(none given)"),
                    "",
                    "Issue:",
                    "",
                    issue,
                ]
                EmailMessage(
                    "[OST2 help] " + request.user.username,
                    "\\n".join(lines),
                    settings.DEFAULT_FROM_EMAIL,
                    [_OST2_HP_TO],
                    reply_to=[request.user.email] if request.user.email else None,
                    connection=get_connection(_OST2_HP_BACKEND),
                ).send(fail_silently=False)
                _ost2_hp_log.info("help request filed by %s", request.user.username)
                done = (
                    '<h1>Thank you</h1><p>Your request has been sent to the OST2 team. '
                    'We will reply to the email address on your account.</p>'
                    '<p><a href="/help">Back to Help</a></p>'
                )
                resp = HttpResponse(_ost2_hp_shell("Help request sent", done))
                resp["Cache-Control"] = "no-store"
                return resp
    elif request.method == "GET":
        # Pre-fill the URL with the page the user came from, when the browser sent a full one
        # (cross-origin MFE pages send only their origin, which is not useful).
        ref = request.META.get("HTTP_REFERER", "")
        parts = urlsplit(ref)
        if parts.scheme in ("http", "https") and parts.path not in ("", "/", "/help") and len(ref) <= _OST2_HP_MAX_URL:
            url = ref

    body = _ost2_hp_page(request, get_token(request), url, issue, error)
    resp = HttpResponse(_ost2_hp_shell("Help", body), status=400 if error else 200)
    resp["Cache-Control"] = "no-store"
    return resp


def _ost2_hp_install():
    import importlib
    from django.urls import re_path
    from django.conf import settings
    urls = importlib.import_module(settings.ROOT_URLCONF)
    if getattr(urls, "_ost2_hp_installed", False):
        return
    urls.urlpatterns.insert(0, re_path(r"^help/?$", _ost2_hp_view, name="ost2_help_page"))
    urls._ost2_hp_installed = True
    _ost2_hp_log.info("ost2 help page installed")


from django.core.signals import request_started as _ost2_hp_req
from django.dispatch import receiver as _ost2_hp_receiver


@_ost2_hp_receiver(_ost2_hp_req, dispatch_uid="ost2_help_page_install")
def _ost2_hp_boot(sender, **kwargs):
    try:
        _ost2_hp_install()
    except Exception:
        _ost2_hp_log.exception("ost2 help page: install failed")
'''

hooks.Filters.ENV_PATCHES.add_items([
    ("openedx-lms-production-settings", _CODE),
    # Rendered by tutor-mfe right after MFE_CONFIG is built; Jinja-rendered, so the host is per box.
    (
        "mfe-lms-production-settings",
        'MFE_CONFIG["SUPPORT_URL"] = "{% if ENABLE_HTTPS %}https{% else %}http{% endif %}://{{ LMS_HOST }}/help"',
    ),
])
