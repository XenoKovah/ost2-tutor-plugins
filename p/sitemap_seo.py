"""
Tutor plugin: sitemap_seo   (OST2)

Serves /sitemap.xml and /robots.txt on the LMS host, with the course list read live from
the catalog, so a newly launched class shows up without editing this file.

    https://<LMS_HOST>/sitemap.xml
    https://<LMS_HOST>/robots.txt

v1 (2026-05-23) was a Caddy patch holding a hand-written sitemap of 30 classes, which went
stale as classes launched. While it was disabled, p served Open edX's stock placeholder
sitemap (one https://www.example.com/ URL dated 2016) and /robots.txt was a 404.

Sitemap contents (rebuilt at most once an hour, Django cache):
  * <LMS_ROOT_URL>/, /courses and /tos (the other stock static pages -- /about, /faq,
    /privacy, /honor, /contact -- are still "This page left intentionally blank" on p);
  * /courses/<course_id>/about for every course whose About page is public
    (catalog_visibility "both" = listed on /courses, or "about" = About page only, not
    listed), that is not invitation-only and has already started. OST2 keeps unreleased
    classes on a far-future start date, so they stay out until launch.
    <lastmod> is when the course overview was last regenerated (a Studio publish, or a
    bulk regeneration such as a mass course-update rollout).
Only LMS-host URLs: a sitemap may only list URLs on its own host, so v1's
apps.<host>/learning/... course-home entries are dropped.

robots.txt allows everything and points crawlers at the sitemap.

The host comes from settings.LMS_ROOT_URL, so the file is the same on every box. Do NOT
enable it on dev: dev holds a copy of p's data and should not be advertised to crawlers.

Same mechanism as ost2_lil_stranger: injected into the LMS production settings and put at
the front of the root urlconf on the first request (ahead of the stock static_template_view
"sitemap.xml" route), so it needs only `tutor config save` + `tutor local restart lms` --
NO image rebuild. The injected code avoids Jinja-special sequences and f-strings so it
survives tutor's template rendering.

Deploy:
    cp sitemap_seo.py "$(tutor plugins printroot)"/
    tutor plugins enable sitemap_seo
    tutor config save && tutor local restart lms
"""
from tutor import hooks

__version__ = "2.0.0"

_CODE = r'''
# ============================================================================
# OST2: /sitemap.xml + /robots.txt built from the course catalog.
# Injected by the sitemap_seo tutor plugin.
# ============================================================================
import logging as _ost2_sm_logging

_ost2_sm_log = _ost2_sm_logging.getLogger("ost2.sitemap_seo")

_OST2_SM_CACHE_KEY = "ost2_sitemap_seo_xml_v2"
_OST2_SM_CACHE_SECONDS = 3600
_OST2_SM_PAGES = ("/", "/courses", "/tos")


def _ost2_sm_base():
    from django.conf import settings
    return settings.LMS_ROOT_URL.rstrip("/")


def _ost2_sm_url(loc, lastmod):
    from xml.sax.saxutils import escape
    out = "  <url><loc>" + escape(loc) + "</loc>"
    if lastmod:
        out += "<lastmod>" + lastmod + "</lastmod>"
    return out + "</url>"


def _ost2_sm_build():
    from django.utils import timezone
    from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
    base = _ost2_sm_base()
    courses = (
        CourseOverview.objects
        .filter(catalog_visibility__in=("both", "about"), invitation_only=False,
                start__lte=timezone.now())
        .order_by("id")
        .values_list("id", "modified")
    )
    course_urls = []
    newest = None
    for course_id, modified in courses:
        day = modified.date().isoformat() if modified else None
        if day and (newest is None or day > newest):
            newest = day
        course_urls.append(_ost2_sm_url(base + "/courses/" + str(course_id) + "/about", day))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in _OST2_SM_PAGES:
        lines.append(_ost2_sm_url(base + path, newest if path == "/courses" else None))
    lines.extend(course_urls)
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def _ost2_sm_sitemap_view(request):
    from django.core.cache import cache
    from django.http import HttpResponse
    body = cache.get(_OST2_SM_CACHE_KEY)
    if body is None:
        try:
            body = _ost2_sm_build()
        except Exception:
            _ost2_sm_log.exception("ost2 sitemap: build failed")
            resp = HttpResponse("sitemap temporarily unavailable\n", status=503,
                                content_type="text/plain; charset=utf-8")
            resp["Retry-After"] = "3600"
            return resp
        cache.set(_OST2_SM_CACHE_KEY, body, _OST2_SM_CACHE_SECONDS)
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


def _ost2_sm_robots_view(request):
    from django.http import HttpResponse
    body = "User-agent: *\nAllow: /\nSitemap: " + _ost2_sm_base() + "/sitemap.xml\n"
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


def _ost2_sm_install():
    import importlib
    from django.urls import re_path
    from django.conf import settings
    urls = importlib.import_module(settings.ROOT_URLCONF)
    if getattr(urls, "_ost2_sm_installed", False):
        return
    urls.urlpatterns.insert(0, re_path(r"^robots\.txt$", _ost2_sm_robots_view, name="ost2_robots_txt"))
    urls.urlpatterns.insert(0, re_path(r"^sitemap\.xml$", _ost2_sm_sitemap_view, name="ost2_sitemap_xml"))
    urls._ost2_sm_installed = True
    _ost2_sm_log.info("ost2 sitemap.xml + robots.txt installed")


from django.core.signals import request_started as _ost2_sm_req
from django.dispatch import receiver as _ost2_sm_receiver


@_ost2_sm_receiver(_ost2_sm_req, dispatch_uid="ost2_sitemap_seo_install")
def _ost2_sm_boot(sender, **kwargs):
    try:
        _ost2_sm_install()
    except Exception:
        _ost2_sm_log.exception("ost2 sitemap: install failed")
'''

hooks.Filters.ENV_PATCHES.add_item(("openedx-lms-production-settings", _CODE))
