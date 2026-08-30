"""
Tutor plugin: ost2_authoring_mfe_fork

Build the Course Authoring MFE (frontend-app-authoring) from the OST2 fork
instead of stock openedx, so the Studio customizations are carried in forked
SOURCE -- XenoKovah/frontend-app-authoring @ teak3_3_remove-studio-home-about-blurb
(a descendant of teak3_1_course-live-disable-fix, itself branched from the
release/teak.3 tag) -- rather than build-time seds.

Branch history: teak3_1 = Live-settings validation fix; teak3_2 = Course
Handouts UI gating + video-editor has_changes; teak3_3 = drop the "New to
Studio?" about blurb from the Studio home sidebar.
p is still on teak3_2 -- dev and beta share this variant.

BUILD-WIRING plugin only: it repoints the authoring MFE's git source via
tutor-mfe's MFE_APPS filter. It does NOT patch source. After enabling:
    tutor config save
    tutor images build mfe -d "--no-cache-filter=authoring-git"   # force re-fetch
    tutor local start -d mfe
"""
from tutormfe.hooks import MFE_APPS

__version__ = "1.1.0"

_REPOSITORY = "https://github.com/XenoKovah/frontend-app-authoring.git"
_VERSION = "teak3_3_remove-studio-home-about-blurb"


@MFE_APPS.add()
def _ost2_point_authoring_at_fork(apps):
    if "authoring" in apps:
        apps["authoring"] = {
            **apps["authoring"],
            "repository": _REPOSITORY,
            "version": _VERSION,
        }
    return apps
