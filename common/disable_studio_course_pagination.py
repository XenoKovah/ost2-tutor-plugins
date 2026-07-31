"""
Tutor plugin: disable_studio_course_pagination

Restores the pre-Teak Studio home behavior of listing ALL courses on one page
instead of the Authoring MFE's "Showing 10 of N" paginated view.

The Authoring MFE (frontend-app-authoring) gates its course-list pagination on
the runtime config flag ENABLE_HOME_PAGE_COURSE_API_V2:

  * truthy -> GET /api/contentstore/v2/home/courses (paginated, page size 10)
  * falsy  -> GET /api/contentstore/v1/home/courses (full list, no pagination)

tutor-mfe's openedx-lms-production-settings patch sets it to the STRING "true"
in MFE_CONFIG, served to the MFE by the LMS mfe_config API. The MFE evaluates it
with a plain truthy check, so the string "false" would still be truthy -- we
must set a real Python False (serialized to JSON false) to turn it off.

This patch runs at the mfe-lms-production-settings hook, which tutor-mfe invokes
at the end of openedx-lms-production-settings (after its own
MFE_CONFIG["ENABLE_HOME_PAGE_COURSE_API_V2"] = "true" line), so this wins.

No image rebuild is needed (MFE_CONFIG is runtime): tutor config save && tutor
local restart lms, then hard-refresh Studio.
"""
from tutor import hooks

__version__ = "1.0.0"

hooks.Filters.ENV_PATCHES.add_item((
    "mfe-lms-production-settings",
    'MFE_CONFIG["ENABLE_HOME_PAGE_COURSE_API_V2"] = False',
))
