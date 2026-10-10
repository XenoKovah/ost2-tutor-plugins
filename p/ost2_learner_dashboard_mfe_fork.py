"""
Point the learner-dashboard MFE build at the OST2 fork branch that carries ALL of OST2's learner
dashboard customizations in source (no build-time patches):

  * the multi-select unenroll survey ("What are your reasons for unenrolling?", checkboxes, new
    first option "I needed to unenroll from a 0%-completion class to register for new classes"),
    which also records each submitted survey in the LMS tracking log via /event, where
    host-scripts/ost2_unenroll_report.py reads it;
  * "Current grade: N%" next to the required grade in the course banners (needs percentGraded
    from the learner_home BFF, edx-platform teak3_11+);
  * "My Enrolled Courses" / "Discover New Courses" header labels.

teak3_3_ost2-dashboard-customizations = upstream openedx/frontend-app-learner-dashboard
release/teak.3 + three commits. The last two items used to be sed patches in tutor-indigo
(mfe-dockerfile-pre-npm-build-learner-dashboard); those were removed from tutor-indigo when this
pin was introduced, because their guard greps no longer match this source. A box that pins this
fork therefore needs a tutor-indigo WITHOUT those patches, and a box that does not pin it needs
tutor-indigo WITH them. The old minimize/sort fork (teak3_1_minimize-completed-courses) is not
part of this pin.

A new branch name changes the Dockerfile ADD line, so a plain `tutor images build mfe`
re-resolves it; use --no-cache-filter=learner-dashboard-git when re-deploying the SAME branch name.
"""
from tutormfe.hooks import MFE_APPS

__version__ = "0.2.0"

_REPOSITORY = "https://github.com/XenoKovah/frontend-app-learner-dashboard.git"
_VERSION = "teak3_3_ost2-dashboard-customizations"


@MFE_APPS.add()
def _ost2_point_learner_dashboard_at_fork(apps):
    if "learner-dashboard" in apps:
        apps["learner-dashboard"] = {
            **apps["learner-dashboard"],
            "repository": _REPOSITORY,
            "version": _VERSION,
        }
    return apps
