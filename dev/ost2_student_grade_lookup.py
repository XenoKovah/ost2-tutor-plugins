"""
Tutor plugin: ost2_student_grade_lookup   (OST2 prototype)

Adds a STAFF-ONLY page to the LMS Django admin that, given a username, email, or
numeric user ID, lists every course the learner is enrolled in together with
their live grade %, pass/fail, and certificate status -- the enrollment+grade
table, no content completion (OST2 does not use block-completion). The learner's
full name (UserProfile.name, if set) is shown in the results header.

    URL:  https://<LMS_HOST>/admin/student-grade-lookup/         (form)
          https://<LMS_HOST>/admin/student-grade-lookup/?username=<name>

It is also linked from the Django admin index page + nav sidebar as
"OST2 tools -> Student grade lookup" (via an AdminSite.get_app_list injection).

Column headers are clickable to sort the table by that column, toggling
ascending/descending (client-side JS, so re-sorting never recomputes grades).
Grade sorts numerically, Active/Passed by flag, Enrolled chronologically.

The Certificate column also flags certs granted by a CERTIFICATE EXCEPTION
(the user is on the course CertificateAllowlist, i.e. granted regardless of
grade) vs. earned by meeting the grade requirement.

Implementation: the view + admin-URL registration are injected into the LMS
production settings via the `openedx-lms-production-settings` patch (the settings
dir is bind-mounted, so this needs only `tutor config save` + `tutor local
restart lms` -- NO image rebuild). Auth is inherited from Django admin
(`admin.site.admin_view` -> active staff only; anonymous users are redirected to
the admin login).

The injected code is deliberately free of Jinja-special sequences ({{, {% , {#)
so it survives tutor's Jinja rendering of patch strings unchanged (same
constraint the other ost2 settings-patch plugins observe). No f-strings, no CSS
blocks -- inline styles only, %-formatting only; the sort arrows use
String.fromCharCode so the file stays pure ASCII.

Deploy:
    cp ost2_student_grade_lookup.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_student_grade_lookup
    tutor config save && tutor local restart lms
"""
from tutor import hooks

__version__ = "1.4.0"

_CODE = '''
# ============================================================================
# OST2 prototype: staff-only student enrollment + grade lookup (Django admin)
# Injected by the ost2_student_grade_lookup tutor plugin.
# ============================================================================
import logging as _ost2_gl_logging

_ost2_gl_log = _ost2_gl_logging.getLogger("ost2.grade_lookup")


def _ost2_gl_page(request):
    from django.http import HttpResponse
    from django.utils.html import escape
    from django.contrib.auth import get_user_model
    User = get_user_model()

    q = (request.GET.get("username") or "").strip()
    out = []
    out.append('<h1 style="font-size:20px;">Student enrollment &amp; grade lookup</h1>')
    out.append(
        '<form method="get" style="margin:14px 0;">'
        '<input type="text" name="username" value="%s" placeholder="username, email, or user ID" '
        'autofocus style="padding:7px;width:340px;font-size:14px;">'
        '<button type="submit" style="padding:7px 18px;margin-left:8px;font-size:14px;cursor:pointer;">'
        'Look up</button></form>' % escape(q)
    )

    if q:
        user = None
        if q.isdigit():
            user = User.objects.filter(id=int(q)).first()
        if user is None:
            user = User.objects.filter(username=q).first() or User.objects.filter(email__iexact=q).first()
        if user is None:
            out.append('<p style="color:#b00020;">No user found matching <b>%s</b>.</p>' % escape(q))
        else:
            from common.djangoapps.student.models import CourseEnrollment
            from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
            from lms.djangoapps.grades.api import CourseGradeFactory

            certs = {}
            try:
                from lms.djangoapps.certificates.models import GeneratedCertificate
                for c in GeneratedCertificate.objects.filter(user=user):
                    certs[str(c.course_id)] = (c.status, c.grade)
            except Exception:
                _ost2_gl_log.exception("ost2 grade lookup: certificate query failed")

            # Certificate exceptions: users on the CertificateAllowlist get a cert
            # regardless of grade (Instructor Dashboard -> "Certificate Exceptions").
            allow = {}
            try:
                from lms.djangoapps.certificates.models import CertificateAllowlist
                for a in CertificateAllowlist.objects.filter(user=user):
                    active = getattr(a, "allowlist", None)
                    if active is None:
                        active = getattr(a, "whitelist", True)
                    if active:
                        allow[str(a.course_id)] = getattr(a, "notes", "") or ""
            except Exception:
                _ost2_gl_log.exception("ost2 grade lookup: certificate-exception (allowlist) query failed")

            prof_name = ""
            try:
                prof_name = (user.profile.name or "").strip()
            except Exception:
                prof_name = ""
            if not prof_name:
                prof_name = ("%s %s" % (user.first_name or "", user.last_name or "")).strip()

            enrolls = list(CourseEnrollment.objects.filter(user=user).order_by("-created"))
            active_n = sum(1 for e in enrolls if e.is_active)
            who = []
            if prof_name:
                who.append("<b>%s</b>" % escape(prof_name))
            who.append("username <b>%s</b>" % escape(user.username))
            who.append("id %s" % user.id)
            if user.email:
                who.append(escape(user.email))
            out.append(
                '<p>%s &mdash; %d active enrollment(s), %d total. '
                'Grades are computed live from the current graded state. '
                '<span style="color:#777;">Click a column header to sort (toggles ascending/descending).</span></p>'
                % (" &middot; ".join(who), active_n, len(enrolls))
            )

            def _th(lbl, dtype):
                return ('<th data-type="%s" onclick="ost2glSort(this)" title="click to sort" '
                        'style="cursor:pointer;user-select:none;white-space:nowrap;">'
                        '%s<span class="arr"></span></th>') % (dtype, lbl)

            out.append(
                '<table id="ost2gl" cellspacing="0" cellpadding="7" '
                'style="border-collapse:collapse;font-size:13px;border:1px solid #b0b0b0;">'
            )
            out.append(
                '<thead><tr style="background:#ececec;text-align:left;">'
                + _th("Course ID", "text") + _th("Name", "text") + _th("Mode", "text")
                + _th("Active", "num") + _th("Enrolled", "text") + _th("Grade", "num")
                + _th("Passed", "num") + _th("Certificate", "text")
                + '</tr></thead><tbody id="ost2gl_body">'
            )

            gf = CourseGradeFactory()
            for e in enrolls:
                ck = e.course_id
                co = CourseOverview.objects.filter(id=ck).first()
                name = co.display_name if co is not None else ""
                grade_txt = ""
                passed_txt = ""
                grade_sort = "-1"
                passed_sort = "-1"
                if e.is_active:
                    try:
                        g = gf.read(user, course_key=ck)
                        pct = 100.0 * g.percent
                        grade_txt = "%.0f%%" % pct
                        grade_sort = "%.4f" % pct
                        passed_txt = "yes" if g.passed else "no"
                        passed_sort = "1" if g.passed else "0"
                    except Exception:
                        _ost2_gl_log.exception("ost2 grade lookup: grade calc failed for %s / %s", user.username, ck)
                        grade_txt = "err"
                cert = certs.get(str(ck))
                is_exc = str(ck) in allow
                if cert:
                    cert_cell = escape("%s (grade %s)" % (cert[0], cert[1]))
                elif is_exc:
                    cert_cell = escape("allowlisted, no cert yet")
                else:
                    cert_cell = ""
                if is_exc:
                    _note = allow.get(str(ck)) or ""
                    _title = (' title="exception note: %s"' % escape(_note)) if _note else ''
                    cert_cell += ('<span style="color:#8a5a00;font-weight:bold;"%s>'
                                  ' (certificate exception)</span>') % _title
                active_txt = "yes" if e.is_active else "no (unenrolled)"
                active_sort = "1" if e.is_active else "0"
                bg = "#ffffff" if e.is_active else "#f6f6f6"
                out.append(
                    '<tr style="background:%s;">'
                    '<td style="font-family:monospace;">%s</td><td>%s</td>'
                    '<td style="text-align:center;">%s</td>'
                    '<td style="text-align:center;" data-sort="%s">%s</td>'
                    '<td style="text-align:center;">%s</td>'
                    '<td style="text-align:right;font-weight:bold;" data-sort="%s">%s</td>'
                    '<td style="text-align:center;" data-sort="%s">%s</td><td>%s</td></tr>'
                    % (bg, escape(str(ck)), escape(name), escape(e.mode or ""),
                       active_sort, active_txt, escape(str(e.created.date())),
                       grade_sort, grade_txt, passed_sort, passed_txt, cert_cell)
                )
            out.append("</tbody></table>")
            out.append(
                '<script>'
                'function ost2glSort(th){'
                'var tb=document.getElementById("ost2gl_body");'
                'var tab=document.getElementById("ost2gl");'
                'var idx=th.cellIndex;'
                'var type=th.getAttribute("data-type")||"text";'
                'var dir=(th.getAttribute("data-dir")==="asc")?"desc":"asc";'
                'var hs=tab.tHead.rows[0].cells;'
                'for(var i=0;i<hs.length;i=i+1){hs[i].setAttribute("data-dir","none");'
                'var s=hs[i].querySelector(".arr");if(s){s.textContent="";}}'
                'th.setAttribute("data-dir",dir);'
                'var a=th.querySelector(".arr");'
                'if(a){a.textContent=(dir==="asc")?" "+String.fromCharCode(9650):" "+String.fromCharCode(9660);}'
                'var rows=Array.prototype.slice.call(tb.rows);'
                'rows.sort(function(r1,r2){'
                'var x=r1.cells[idx],y=r2.cells[idx];'
                'var v1=x.getAttribute("data-sort");if(v1===null){v1=x.textContent;}'
                'var v2=y.getAttribute("data-sort");if(v2===null){v2=y.textContent;}'
                'var r;'
                'if(type==="num"){r=(parseFloat(v1)||0)-(parseFloat(v2)||0);}'
                'else{v1=(""+v1).toLowerCase();v2=(""+v2).toLowerCase();r=(v1<v2)?-1:((v1>v2)?1:0);}'
                'return (dir==="asc")?r:-r;});'
                'for(var j=0;j<rows.length;j=j+1){tb.appendChild(rows[j]);}'
                '}'
                '</script>'
            )

    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>Student grade lookup</title></head>'
        '<body style="font-family:Arial,Helvetica,sans-serif;margin:26px;color:#111;">'
        + "".join(out) +
        '<p style="margin-top:22px;color:#777;font-size:12px;">'
        'OST2 prototype &middot; staff only &middot; content-completion intentionally omitted.</p>'
        '</body></html>'
    )
    return HttpResponse(html)


def _ost2_gl_install():
    from django.contrib import admin
    from django.urls import path, reverse
    site = admin.site
    if getattr(site, "_ost2_gl_installed", False):
        return
    wrapped = site.admin_view(_ost2_gl_page)
    _ost2_gl_orig_get_urls = site.get_urls

    def _ost2_gl_get_urls():
        extra = [path("student-grade-lookup/", wrapped, name="ost2_student_grade_lookup")]
        return extra + _ost2_gl_orig_get_urls()

    site.get_urls = _ost2_gl_get_urls

    # Surface a link on the admin index page + nav sidebar so the tool is
    # discoverable (otherwise it is reachable only by its direct URL). Done by
    # appending a synthetic "app" to AdminSite.get_app_list -- the keys below are
    # exactly those the admin/index.html + nav_sidebar.html templates read.
    _ost2_gl_orig_app_list = site.get_app_list

    def _ost2_gl_get_app_list(request, *args, **kwargs):
        app_list = list(_ost2_gl_orig_app_list(request, *args, **kwargs))
        app_label = kwargs.get("app_label", args[0] if args else None)
        if app_label is None:
            try:
                url = reverse("admin:ost2_student_grade_lookup")
            except Exception:
                url = "/admin/student-grade-lookup/"
            app_list.append({
                "name": "OST2 tools",
                "app_label": "ost2_tools",
                "app_url": url,
                "has_module_perms": True,
                "models": [{
                    "name": "Student grade lookup",
                    "object_name": "StudentGradeLookup",
                    "perms": {"add": False, "change": False, "delete": False, "view": True},
                    "admin_url": url,
                    "add_url": None,
                    "view_only": True,
                }],
            })
        return app_list

    site.get_app_list = _ost2_gl_get_app_list
    site._ost2_gl_installed = True
    _ost2_gl_log.info("ost2 grade lookup admin page + index link installed")


# Prefer installing at settings-import time (before the admin URLconf is built).
# If that is too early in this process, fall back to the first request_started.
try:
    _ost2_gl_install()
except Exception:
    from django.core.signals import request_started as _ost2_gl_req
    from django.dispatch import receiver as _ost2_gl_receiver

    @_ost2_gl_receiver(_ost2_gl_req, dispatch_uid="ost2_grade_lookup_install")
    def _ost2_gl_boot(sender, **kwargs):
        try:
            _ost2_gl_install()
        except Exception:
            _ost2_gl_log.exception("ost2 grade lookup: deferred install failed")
'''

hooks.Filters.ENV_PATCHES.add_item(("openedx-lms-production-settings", _CODE))
