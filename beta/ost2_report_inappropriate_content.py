"""
Tutor plugin: ost2_report_inappropriate_content   (OST2)

Backend for the "Report inappropriate content" link on other users' profile pages
(profile MFE branch teak3_6_report-inappropriate-content). Adds a login-required LMS page

    https://<LMS_HOST>/report-user/<username>/

with checkboxes for each item listed under "Inappropriate behavior:" in the Terms of
Service (linked via the relative /tos URL, so it works on dev, p and beta), plus an
"Other" box with a free-text field limited to 256 Unicode characters. Submitting the
form emails the report -- reporter, reported user, ticked policies, message and a link
to the reported profile -- to REPORT_TO below, through the normal LMS email backend.

Implementation: the view is injected into the LMS production settings via the
`openedx-lms-production-settings` patch and mounted on the root urlconf on the first
request (the settings dir is bind-mounted, so this needs only `tutor config save` +
`tutor local restart lms` -- NO image rebuild). The injected code avoids Jinja-special
sequences and %-formatting/f-strings so it survives tutor's template rendering.

Deploy:
    cp ost2_report_inappropriate_content.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_report_inappropriate_content
    tutor config save && tutor local restart lms
"""
from tutor import hooks

__version__ = "1.0.0"

_CODE = '''
# ============================================================================
# OST2: "Report inappropriate content" page (profile link -> form -> email)
# Injected by the ost2_report_inappropriate_content tutor plugin.
# ============================================================================
import logging as _ost2_rp_logging

_ost2_rp_log = _ost2_rp_logging.getLogger("ost2.report_user")

_OST2_RP_TO = "xeno@ost2.fyi"
# Real SMTP (rate-limited) path, used explicitly so reports are delivered even on dev,
# where ost2_dev_mail_to_files redirects the DEFAULT backend to files.
_OST2_RP_BACKEND = "openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend"
_OST2_RP_MAX_CHARS = 256
_OST2_RP_MAX_PER_HOUR = 10

# Verbatim from the ToS, "Inappropriate behavior:" list.
_OST2_RP_POLICIES = [
    "Attempting to disrupt classes in any way.",
    "Utilizing OST2 infrastructure for purposes other than completing classes.",
    "Posting any information, non-public, or technically-public, about a person which is perceived by that person as threatening. (I.e. \\"doxing\\")",
    "Posting sexual or other unprofessional images.",
    "Unwelcome sexual attention.",
    "Comments that reinforce social structures of discrimination, such as comments related to gender, gender identity and expression, sexual orientation, disability, physical appearance, body size, race, age, geographic background, or religion.",
    "Any other forms of threats or harassment.",
    "Advocating for, or encouraging, any of the above behavior",
]

_OST2_RP_CSS = (
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


def _ost2_rp_shell(title, body):
    from django.utils.html import escape
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<title>@@TITLE@@</title><style>@@CSS@@</style></head>'
        '<body><main>@@BODY@@</main></body></html>'
    ).replace("@@TITLE@@", escape(title)).replace("@@CSS@@", _OST2_RP_CSS).replace("@@BODY@@", body)


def _ost2_rp_profile_url(request, username):
    from urllib.parse import quote
    from django.conf import settings
    base = getattr(settings, "PROFILE_MICROFRONTEND_URL", None)
    if not base:
        base = "https://apps." + request.get_host().split(":")[0] + "/profile"
    base = base.rstrip("/")
    if base.endswith("/u"):
        base = base[:-2]
    return base + "/u/" + quote(username)


def _ost2_rp_form(username, csrf, selected, other_checked, other_text, error):
    from django.utils.html import escape
    rows = []
    for i, text in enumerate(_OST2_RP_POLICIES):
        rows.append(
            '<label class="opt"><input type="checkbox" name="policy" value="@@I@@"@@C@@><span>@@T@@</span></label>'
            .replace("@@I@@", str(i))
            .replace("@@C@@", " checked" if str(i) in selected else "")
            .replace("@@T@@", escape(text))
        )
    rows.append(
        '<label class="opt"><input type="checkbox" name="policy" value="other"@@C@@><span>Other</span></label>'
        .replace("@@C@@", " checked" if other_checked else "")
    )
    body = (
        '<h1>Report inappropriate content</h1>'
        '<p class="muted">Reporting user: <strong>@@USER@@</strong></p>'
        '@@ERR@@'
        '<form method="post" class="card">'
        '<input type="hidden" name="csrfmiddlewaretoken" value="@@CSRF@@">'
        '<p style="margin-top:0"><strong>Which part of the <a href="/tos" target="_blank" rel="noopener">Terms of Service</a> '
        'does this content violate?</strong><br><span class="muted">Inappropriate behavior (select all that apply):</span></p>'
        '@@ROWS@@'
        '<p style="margin-bottom:4px"><label for="other_text"><strong>If Other, briefly describe the issue</strong> '
        '<span class="muted">(up to @@MAX@@ characters)</span></label></p>'
        '<textarea id="other_text" name="other_text" rows="4" maxlength="@@MAX@@">@@TEXT@@</textarea>'
        '<button type="submit">Submit</button>'
        '</form>'
    )
    err_html = ('<p class="err" role="alert">' + escape(error) + '</p>') if error else ""
    body = (body.replace("@@USER@@", escape(username)).replace("@@ERR@@", err_html)
            .replace("@@CSRF@@", escape(csrf)).replace("@@ROWS@@", "".join(rows))
            .replace("@@MAX@@", str(_OST2_RP_MAX_CHARS)).replace("@@TEXT@@", escape(other_text)))
    return body


def _ost2_rp_view(request, username):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.views import redirect_to_login
    from django.core.cache import cache
    from django.core.mail import get_connection, send_mail
    from django.conf import settings
    from django.http import Http404, HttpResponse
    from django.middleware.csrf import get_token
    from django.utils import timezone
    from django.utils.html import escape

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    target = get_user_model().objects.filter(username=username).first()
    if target is None or not target.is_active:
        raise Http404("No such user")
    if target.pk == request.user.pk:
        return HttpResponse(_ost2_rp_shell("Report", '<h1>Report inappropriate content</h1><p>You cannot report your own account.</p>'), status=400)

    selected, other_checked, other_text, error = set(), False, "", ""
    if request.method == "POST":
        picked = request.POST.getlist("policy")
        selected = set(p for p in picked if p.isdigit() and int(p) < len(_OST2_RP_POLICIES))
        other_checked = "other" in picked
        other_text = request.POST.get("other_text", "").strip()
        if not selected and not other_checked:
            error = "Please select at least one policy."
        elif other_checked and not other_text:
            error = "Please describe the issue in the Other box."
        elif len(other_text) > _OST2_RP_MAX_CHARS:
            error = "The description must be at most %d characters." % _OST2_RP_MAX_CHARS
        else:
            key = "ost2_report_user_count_%d" % request.user.pk
            count = cache.get(key, 0)
            if count >= _OST2_RP_MAX_PER_HOUR:
                error = "You have submitted too many reports recently. Please try again later."
            else:
                cache.set(key, count + 1, 3600)
                lines = [
                    "Reported user:  " + target.username,
                    "Profile:        " + _ost2_rp_profile_url(request, target.username),
                    "",
                    "Reported by:    %s <%s>" % (request.user.username, request.user.email),
                    "Reporter profile: " + _ost2_rp_profile_url(request, request.user.username),
                    "Submitted:      " + timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "",
                    "ToS policies violated (Inappropriate behavior):",
                ]
                for i in sorted(int(s) for s in selected):
                    lines.append("  - " + _OST2_RP_POLICIES[i])
                if other_checked:
                    lines.append("  - Other")
                if other_text:
                    lines.extend(["", "Message from reporter:", other_text])
                send_mail(
                    "[OST2 report] Inappropriate content: " + target.username,
                    "\\n".join(lines),
                    settings.DEFAULT_FROM_EMAIL,
                    [_OST2_RP_TO],
                    fail_silently=False,
                    connection=get_connection(_OST2_RP_BACKEND),
                )
                _ost2_rp_log.info("report filed by %s against %s", request.user.username, target.username)
                done = (
                    '<h1>Thank you</h1><p>Your report about <strong>@@USER@@</strong> has been sent to the OST2 team.</p>'
                    '<p><a href="@@URL@@">Back to the profile</a></p>'
                ).replace("@@USER@@", escape(target.username)).replace("@@URL@@", escape(_ost2_rp_profile_url(request, target.username)))
                return HttpResponse(_ost2_rp_shell("Report sent", done))

    body = _ost2_rp_form(target.username, get_token(request), selected, other_checked, other_text, error)
    resp = HttpResponse(_ost2_rp_shell("Report inappropriate content", body), status=400 if error else 200)
    resp["Cache-Control"] = "no-store"
    return resp


def _ost2_rp_install():
    import importlib
    from django.urls import re_path
    from django.conf import settings
    urls = importlib.import_module(settings.ROOT_URLCONF)
    if getattr(urls, "_ost2_rp_installed", False):
        return
    urls.urlpatterns.insert(0, re_path(r"^report-user/(?P<username>[\\w.@+-]+)/$", _ost2_rp_view, name="ost2_report_user"))
    urls._ost2_rp_installed = True
    _ost2_rp_log.info("ost2 report-user page installed")


from django.core.signals import request_started as _ost2_rp_req
from django.dispatch import receiver as _ost2_rp_receiver


@_ost2_rp_receiver(_ost2_rp_req, dispatch_uid="ost2_report_user_install")
def _ost2_rp_boot(sender, **kwargs):
    try:
        _ost2_rp_install()
    except Exception:
        _ost2_rp_log.exception("ost2 report-user: install failed")
'''

hooks.Filters.ENV_PATCHES.add_item(("openedx-lms-production-settings", _CODE))
