"""
BETA COPY: pins teak3_5_timing-feedback-midclass-nudge (= dev's teak3_4 + the mid-class Timing
Feedback nudge, which only shows where ost2_timing_feedback_midclass_nudge sets its MFE_CONFIG flag).

Point the Learning MFE build at the OST2 fork branch that adds the Timing Feedback
nudge to the certificate boxes (course home alert + progress-tab card) when a learner
submitted some but not all Timing Feedback subsections.

teak3_2_timing-feedback-nudge = upstream openedx/frontend-app-learning release/teak.3
(db0a565, exactly what dev was built from) + one commit. All other OST2 learning
customizations are the tutor-indigo Dockerfile patches applied at build time and are
unaffected. NOTE: teak3_1_hide-completion-tracking-better is a separate branch that was
never deployed on dev; this pin deliberately does not include it.

A new branch name changes the Dockerfile ADD line, so a plain `tutor images build mfe`
re-resolves it; use --no-cache-filter=learning-git when re-deploying the SAME branch name.
"""
from tutormfe.hooks import MFE_APPS

__version__ = "0.1.0"

_REPOSITORY = "https://github.com/XenoKovah/frontend-app-learning.git"
_VERSION = "teak3_5_timing-feedback-midclass-nudge"


@MFE_APPS.add()
def _ost2_point_learning_at_fork(apps):
    if "learning" in apps:
        apps["learning"] = {
            **apps["learning"],
            "repository": _REPOSITORY,
            "version": _VERSION,
        }
    return apps
