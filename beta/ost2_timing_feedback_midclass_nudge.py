"""
BETA ONLY. Turns on the mid-class Timing Feedback nudge in the Learning MFE: once a learner has
completed material later than an unsubmitted Timing Feedback entry, the course home shows Li'l
Stranger with links to the missed entries (only those at/before their furthest completed unit).

Never install on p or dev: Timing Feedback is optional there, mandatory on beta.
Runtime-only (MFE_CONFIG is served by the LMS mfe_config API): tutor config save && tutor local
restart lms. Needs the learning MFE built from XenoKovah/frontend-app-learning
teak3_5_timing-feedback-midclass-nudge (set via ost2_learning_mfe_fork's _VERSION).
"""
from tutor import hooks

hooks.Filters.ENV_PATCHES.add_item((
    "mfe-lms-common-settings",
    "MFE_CONFIG['OST2_TIMING_FEEDBACK_MIDCLASS_NUDGE'] = True",
))
