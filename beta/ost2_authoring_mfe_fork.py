"""
Tutor plugin: ost2_authoring_mfe_fork

Build the Course Authoring MFE (frontend-app-authoring) from the OST2 fork
instead of stock openedx, so the Live-settings validation fix is carried in
forked SOURCE -- XenoKovah/frontend-app-authoring @ teak3_1_course-live-disable-fix
(branched from the release/teak.3 tag) -- rather than the
ost2_course_live_disable_fix_authoring build-time sed.

BUILD-WIRING plugin only: it repoints the authoring MFE's git source via
tutor-mfe's MFE_APPS filter. It does NOT patch source. After enabling:
    tutor config save
    tutor images build mfe -d "--no-cache-filter=authoring-git"   # force re-fetch
    tutor local start -d mfe
"""
from tutormfe.hooks import MFE_APPS

__version__ = "1.0.0"

_REPOSITORY = "https://github.com/XenoKovah/frontend-app-authoring.git"
_VERSION = "teak3_2_course-handouts-ui"


@MFE_APPS.add()
def _ost2_point_authoring_at_fork(apps):
    if "authoring" in apps:
        apps["authoring"] = {
            **apps["authoring"],
            "repository": _REPOSITORY,
            "version": _VERSION,
        }
    return apps
