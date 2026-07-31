"""
Tutor plugin: ost2_disable_survey_report

Permanently disables the Open edX "Join the Open edX Data Sharing Initiative
and shape the future of learning" banner shown at the top of the LMS Django
admin (/admin) on every fresh page load.

The banner is rendered by edx-platform's openedx.features.survey_report app.
Its context processor (survey_report/context_processors.py) shows the banner
whenever settings.SURVEY_REPORT_ENABLE is truthy AND no survey report has been
sent within the last SURVEY_REPORT_CHECK_THRESHOLD (6) months. The "Dismiss"
button is purely client-side (admin_banner.js), so it hides the banner only
for the current view -- it comes back on the next admin page load. The only
clean, permanent off-switch is the documented toggle SURVEY_REPORT_ENABLE
(default True in lms/envs/common.py:5556), set to False here. It is added to
openedx-common-settings so both LMS and CMS admin are covered (the feature is
LMS-only today; setting it in CMS is harmless and defensive).

Settings-only change -- NO image rebuild (env/apps/openedx/settings is
bind-mounted into the containers):
    cp ost2_disable_survey_report.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_disable_survey_report
    tutor config save
    tutor local restart lms cms
"""
from tutor import hooks

__version__ = "1.0.0"

hooks.Filters.ENV_PATCHES.add_item((
    "openedx-common-settings",
    "SURVEY_REPORT_ENABLE = False",
))
