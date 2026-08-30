"""
Tutor plugin: ost2_authoring_mfe_fork

Build the Course Authoring MFE (frontend-app-authoring) from the OST2 fork
instead of stock openedx, so the Studio customizations are carried in forked
SOURCE -- XenoKovah/frontend-app-authoring @ teak3_6_outline-declutter-and-phantom-changes
(a descendant of teak3_1_course-live-disable-fix, itself branched from the
release/teak.3 tag) -- rather than build-time seds.

Branch history: teak3_1 = Live-settings validation fix; teak3_2 = Course
Handouts UI gating + video-editor has_changes; teak3_3 = drop the "New to
Studio?" about blurb from the Studio home sidebar; teak3_4 = modernize the
"Course run" placeholder example on the create / re-run course form; teak3_5 =
carry that example into the 28 locales that translate it, by rewriting the
token after the build's atlas pull; teak3_6 = drop the course-outline
Checklists / Course highlight emails / Course tags status items, the two
generic help-sidebar cards, and the "upgraded discussion forum" alert, relink
the course-outline docs at docs.openedx.org (the CMS help token still points at
the retired edx.readthedocs.io project), and stop TinyMCE's load-time
reformatting from arming "You've made some changes" on Schedule & Details.
dev leads on teak3_6; beta is on teak3_5 and p on teak3_2.

BUILD-WIRING plugin only: it repoints the authoring MFE's git source via
tutor-mfe's MFE_APPS filter. It does NOT patch source. After enabling:
    tutor config save
    tutor images build mfe -d "--no-cache-filter=authoring-git"   # force re-fetch
    tutor local start -d mfe
"""
from tutormfe.hooks import MFE_APPS

__version__ = "1.4.0"

_REPOSITORY = "https://github.com/XenoKovah/frontend-app-authoring.git"
_VERSION = "teak3_6_outline-declutter-and-phantom-changes"


@MFE_APPS.add()
def _ost2_point_authoring_at_fork(apps):
    if "authoring" in apps:
        apps["authoring"] = {
            **apps["authoring"],
            "repository": _REPOSITORY,
            "version": _VERSION,
        }
    return apps
