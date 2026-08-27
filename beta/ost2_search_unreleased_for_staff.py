"""
Tutor plugin: ost2_search_unreleased_for_staff

Lets course staff search content in courses that have not started yet.

WHY
---
edx-search's SearchFilterGenerator (search/filter_generator.py) applies, for
every courseware search:

    def filter_dictionary(self, **kwargs):
        return {"start_date": DateRange(None, datetime.utcnow())}

Every indexed block carries its course's start date, so any course whose start
is in the FUTURE has all of its content filtered out of search results -- for
staff and superusers too. edx-platform's LmsSearchFilterGenerator (the value of
SEARCH_FILTER_GENERATOR) does not override filter_dictionary, so it inherits
this behaviour unchanged.

OST2 uses a far-future start date (typically 2030-01-01) as the "keep this
course unreleased" sentinel. The result is that a staff member can open and
browse such a course but gets zero hits when searching it -- e.g.
course-v1:OpenSecurityTraining+InstructorHowTo+V1 has 123 documents indexed in
Meilisearch, 36 of which match "video", yet /search/<course_id>/ returned
total=0. This is NOT an indexing problem and no amount of reindexing fixes it.

WHAT THIS DOES
--------------
Points SEARCH_FILTER_GENERATOR at a wrapper that delegates every call to the
stock LmsSearchFilterGenerator, then drops ONLY the start_date bound, and only
when the requesting user is staff:

  * course-scoped search (course_id given): dropped if the user has 'staff'
    access to that course -- course staff/instructor, or global staff.
  * course-less search (dashboard/org-wide): dropped only for global staff.

Learners are unaffected: they keep the stock start_date filter, so unreleased
course text cannot leak via the /search/ REST endpoint. Anything unexpected
(bad course key, import failure) fails CLOSED -- the stock filter is kept.

IMPLEMENTATION NOTE
-------------------
This settings module is imported before django.setup() completes, so the
wrapper must NOT import models or LmsSearchFilterGenerator at module level --
that raises AppRegistryNotReady. All edx imports are therefore deferred into
__init__/method bodies, which run at request time. For the same reason the
wrapper delegates rather than subclasses; edx-search's _load_class() only does
importlib.import_module() + getattr(), so duck typing is sufficient.

DEPLOY -- settings-only, NO image rebuild
(env/apps/openedx/settings is bind-mounted into the containers):

    cp ost2_search_unreleased_for_staff.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_search_unreleased_for_staff
    tutor config save
    tutor local restart lms

VERIFY (expect a non-zero total for a future-dated course):

    from search.api import perform_search
    from django.contrib.auth import get_user_model
    u = get_user_model().objects.get(username="Xeno")
    perform_search("video", user=u, size=5, from_=0,
                   course_id="course-v1:OpenSecurityTraining+InstructorHowTo+V1")["total"]

REVERT:

    tutor plugins disable ost2_search_unreleased_for_staff
    tutor config save && tutor local restart lms
"""
from tutor import hooks

__version__ = "1.0.0"

SETTINGS = '''
# --- ost2_search_unreleased_for_staff -------------------------------------
# Allow course staff to search courses whose start date is in the future.
# See the plugin docstring for the full rationale.
import logging as _ost2_search_logging

_ost2_search_log = _ost2_search_logging.getLogger(__name__)


def _ost2_search_is_course_staff(user, course_id):
    """Return True if `user` should be allowed to search unreleased content."""
    try:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        # Global staff / superusers always qualify.
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return True
        if not course_id:
            return False
        # Deferred: these pull in Django models and cannot be imported at
        # settings-module scope.
        from opaque_keys.edx.keys import CourseKey
        from lms.djangoapps.courseware.access import has_access
        course_key = CourseKey.from_string(str(course_id))
        return bool(has_access(user, "staff", course_key))
    except Exception:  # pylint: disable=broad-except
        # Fail CLOSED: keep the stock start_date filter.
        _ost2_search_log.exception(
            "ost2_search_unreleased_for_staff: staff check failed for course %s; "
            "keeping the stock start_date filter",
            course_id,
        )
        return False


class OST2SearchFilterGenerator:
    """
    Delegates to LmsSearchFilterGenerator, dropping the start_date upper bound
    for staff so that not-yet-started courses remain searchable by them.
    """

    def __init__(self):
        from lms.lib.courseware_search.lms_filter_generator import LmsSearchFilterGenerator
        self._inner = LmsSearchFilterGenerator()

    def field_dictionary(self, **kwargs):
        return self._inner.field_dictionary(**kwargs)

    def exclude_dictionary(self, **kwargs):
        return self._inner.exclude_dictionary(**kwargs)

    def filter_dictionary(self, **kwargs):
        filter_dictionary = self._inner.filter_dictionary(**kwargs)
        if _ost2_search_is_course_staff(kwargs.get("user"), kwargs.get("course_id")):
            filter_dictionary.pop("start_date", None)
        return filter_dictionary


SEARCH_FILTER_GENERATOR = __name__ + ".OST2SearchFilterGenerator"
# --- end ost2_search_unreleased_for_staff ---------------------------------
'''

hooks.Filters.ENV_PATCHES.add_item(
    ("openedx-lms-production-settings", SETTINGS)
)
