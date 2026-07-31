"""
Tutor plugin: ost2_courses_hide_completed

On the legacy LMS course-discovery page (/courses), hide the courses a signed-in
learner has already completed (a downloadable certificate), and drop the
org / modes / language facets (meaningless for OST2: one org, one language, and
real courses are all "honor"). When the learner UN-checks the "Not completed"
checkbox (see the courses.html theme override), it sets an `ost2_show_completed`
cookie that re-includes completed courses (a show_completed POST/GET param is
also honoured as a fallback).

The exclusion is applied at QUERY time via the Meilisearch engine's
exclude_dictionary on the course id (which the engine maps to a `NOT _pk = ...`
filter), so result counts and pagination stay correct. The course-discovery
search api takes no request, so the current learner is found via crum.

IMPLEMENTATION NOTES
  * tutor renders ENV_PATCHES through Jinja, so this settings code contains NO
    "{" / "}" literals -- dicts are built with dict()/subscript assignment.
  * The monkeypatch is installed lazily on the first request (one-shot
    request_started signal) so Django's app registry is fully populated before
    `search.api` / `search.views` are imported -- importing them at settings
    evaluation time would race app loading.
  * Both `search.api.course_discovery_search` and the reference the view bound at
    import time (`search.views.course_discovery_search`) are replaced.
"""
from tutor import hooks

_SETTINGS = '''
# OST2: drop the (meaningless) org / modes / language facets on /courses
COURSE_DISCOVERY_FILTERS = []

# OST2: hide a signed-in learner's completed courses from /courses discovery,
# installed after app-loading via a one-shot request_started signal.
def _ost2_install_hide_completed_courses(sender=None, **kwargs):
    from django.core.signals import request_started
    request_started.disconnect(_ost2_install_hide_completed_courses)

    import search.api as _api
    import search.views as _views
    import search.meilisearch as _meili
    from crum import get_current_request

    # OST2: the course-discovery (course_info) index does NOT ship with the
    # primary key (_pk) as a filterable attribute, so a per-course exclusion by id
    # raises "Attribute `_pk` is not filterable". Add it to INDEX_FILTERABLES so a
    # full index (re)build keeps it filterable. (Existing indexes are updated in
    # place once via update_index_filterables -- run on dev; a reindex applies it
    # everywhere else.)
    _course_info_index = getattr(_api.settings, "COURSEWARE_INFO_INDEX_NAME", "course_info")
    _filterables = _meili.INDEX_FILTERABLES.setdefault(_course_info_index, [])
    if _meili.PRIMARY_KEY_FIELD_NAME not in _filterables:
        _filterables.append(_meili.PRIMARY_KEY_FIELD_NAME)

    # NOTE: discovery result ORDERING (start date, then title) is handled by the
    # separate ost2_course_discovery_sort plugin (RGG_COURSE_DISCOVERY_SORT). This
    # plugin only handles the completed-course EXCLUSION; the two compose (our
    # exclude_dictionary flows through that plugin's patched MeilisearchEngine.search).

    def _completed_course_ids(user):
        from lms.djangoapps.certificates.models import GeneratedCertificate
        rows = GeneratedCertificate.objects.filter(
            user=user, status="downloadable"
        ).values_list("course_id", flat=True)
        return [str(cid) for cid in rows]

    def course_discovery_search(search_term=None, size=20, from_=0, field_dictionary=None):
        use_search_fields = ["org"]
        (search_fields, _f, exclude_dictionary) = _api.SearchFilterGenerator.generate_field_filters()
        use_field_dictionary = dict(
            (f, search_fields[f]) for f in search_fields if f in use_search_fields
        )
        if field_dictionary:
            use_field_dictionary.update(field_dictionary)
        if not getattr(_api.settings, "SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING", False):
            use_field_dictionary["enrollment_start"] = _api.DateRange(None, _api.datetime.utcnow())

        # OST2: exclude this learner's completed courses unless they asked to show them.
        # The "Not completed" checkbox on /courses sets the ost2_show_completed cookie
        # (and we also accept a show_completed POST/GET param as a fallback).
        req = get_current_request()
        user = getattr(req, "user", None) if req is not None else None
        if user is not None and user.is_authenticated:
            show = str(
                req.COOKIES.get("ost2_show_completed", "")
                or req.POST.get("show_completed", "")
                or req.GET.get("show_completed", "")
            ).strip().lower()
            if show not in ("1", "true", "yes", "on"):
                completed = _completed_course_ids(user)
                if completed:
                    exclude_dictionary = dict(exclude_dictionary or dict())
                    existing = exclude_dictionary.get("id") or []
                    if not isinstance(existing, list):
                        existing = [existing]
                    exclude_dictionary["id"] = existing + completed

        searcher = _api.SearchEngine.get_search_engine(
            getattr(_api.settings, "COURSEWARE_INFO_INDEX_NAME", "course_info")
        )
        if not searcher:
            raise _api.NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")
        return searcher.search(
            query_string=search_term,
            size=size,
            from_=from_,
            field_dictionary=use_field_dictionary,
            filter_dictionary=dict(enrollment_end=_api.DateRange(_api.datetime.utcnow(), None)),
            exclude_dictionary=exclude_dictionary,
            aggregation_terms=_api.course_discovery_aggregations(),
        )

    _api.course_discovery_search = course_discovery_search
    _views.course_discovery_search = course_discovery_search


from django.core.signals import request_started as _ost2_request_started
_ost2_request_started.connect(_ost2_install_hide_completed_courses)
'''

hooks.Filters.ENV_PATCHES.add_item(
    ("openedx-lms-common-settings", _SETTINGS)
)
