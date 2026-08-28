"""
Tutor plugin: ost2_course_discovery_sort

Sorts the /courses course-discovery catalog (p.ost2.fyi, Teak, Meilisearch
backend) by course start date, OLDEST FIRST. Without this, discovery returns
documents in raw insertion order because edx-search's Meilisearch engine passes
no `sort` and the `course_info` index has empty `sortableAttributes`.

Two independent pieces (Piece 1 must be in effect before Piece 2 goes live, or
sorting a non-sortable attribute makes Meilisearch raise invalid_search_sort and
the discovery search 500s):

  Piece 1 -- make `start` sortable on the Meilisearch course_info index.
    Registered as an LMS init task so it re-applies on every
    `tutor local do init` / `launch` (survives an index drop/recreate).
    Idempotent. Changing sortableAttributes does NOT require an edx reindex --
    Meilisearch re-sorts stored docs internally. Apply it immediately once with:
      ~/tutor-venv/bin/tutor local exec -T lms ./manage.py lms shell <<'PY'
      import search.meilisearch as ms
      from django.conf import settings
      idx = getattr(settings, "COURSEWARE_INFO_INDEX_NAME", "course_info")
      c = ms.get_meilisearch_client(); i = c.get_index(ms.get_meilisearch_index_name(idx))
      t = i.update_sortable_attributes(sorted(set(list(i.get_sortable_attributes()) + ["start"])))
      ms.wait_for_task_to_succeed(c, t, timeout_in_ms=30000); print(i.get_sortable_attributes())
      PY

  Piece 2 -- inject `sort` into discovery (course_info) queries only, via a
    request_started-deferred monkeypatch of MeilisearchEngine.search (mirrors
    the social-links patch mechanism; LMS settings are bind-mounted so no
    openedx image rebuild -- just `tutor config save` + restart lms). The
    replacement body is identical to the stock search() except the lines that
    set opt_params["sort"], and it is scoped to self.index_name == the discovery
    index so in-course content search (courseware_content) is untouched. Flip
    RGG_COURSE_DISCOVERY_SORT to ["start:desc"] for newest-first, or [] to
    disable.

Deploy (LMS-only; course discovery is LMS-served):
    cp ost2_course_discovery_sort.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_course_discovery_sort
    # apply Piece 1 immediately (see docstring above), THEN:
    tutor config save
    tutor local restart lms lms-worker
"""
from tutor import hooks

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# Piece 1: ensure `start` is sortable on the discovery index (idempotent).
# No `{` characters (avoids Jinja templating of the init-task string).
# ---------------------------------------------------------------------------
_INIT_TASK = r"""
echo "ost2_course_discovery_sort: ensuring course_info.start is sortable"
./manage.py lms shell <<'PYEOF'
import search.meilisearch as ms
from django.conf import settings
idx = getattr(settings, "COURSEWARE_INFO_INDEX_NAME", "course_info")
client = ms.get_meilisearch_client()
index = client.get_index(ms.get_meilisearch_index_name(idx))
current = list(index.get_sortable_attributes())
wanted = ["start", "content.display_name"]
missing = [a for a in wanted if a not in current]
if missing:
    new = sorted(set(current + wanted))
    task = index.update_sortable_attributes(new)
    ms.wait_for_task_to_succeed(client, task, timeout_in_ms=30000)
    print("ost2_course_discovery_sort: set sortableAttributes ->", new)
else:
    print("ost2_course_discovery_sort: start + title already sortable")
PYEOF
"""

hooks.Filters.CLI_DO_INIT_TASKS.add_item(("lms", _INIT_TASK))

# ---------------------------------------------------------------------------
# Piece 2: inject the sort into discovery queries (course_info index only).
# Appended to LMS+CMS common settings (rendered + bind-mounted). No `{` dict
# literals -> no Jinja conflict. The receiver defers `import search.meilisearch`
# until the first request, when settings are fully configured.
# ---------------------------------------------------------------------------
_SETTINGS_PATCH = '''
# OST2: sort the /courses discovery catalog by course start date (oldest first),
# then alphabetically by course title (content.display_name) as the tie-break for
# courses sharing a start date. Flip the first term to ["start:desc"] for
# newest-first, or set [] to disable. Both fields must be sortable (Piece 1).
RGG_COURSE_DISCOVERY_SORT = ["start:asc", "content.display_name:asc"]

from django.core.signals import request_started as _ost2_request_started
from django.dispatch import receiver as _ost2_receiver


@_ost2_receiver(_ost2_request_started, dispatch_uid="ost2_discovery_sort_patch")
def _ost2_install_discovery_sort_patch(sender, **_kwargs):
    import search.meilisearch as _ms
    if getattr(_ms.MeilisearchEngine.search, "_ost2_sort_patched", False):
        return

    def search(self, query_string=None, field_dictionary=None, filter_dictionary=None,
               exclude_dictionary=None, aggregation_terms=None, log_search_params=False, **kwargs):
        from django.conf import settings as _s
        opt_params = _ms.get_search_params(
            field_dictionary=field_dictionary,
            filter_dictionary=filter_dictionary,
            exclude_dictionary=exclude_dictionary,
            aggregation_terms=aggregation_terms,
            **kwargs,
        )
        discovery_index = getattr(_s, "COURSEWARE_INFO_INDEX_NAME", "course_info")
        sort_spec = getattr(_s, "RGG_COURSE_DISCOVERY_SORT", None)
        if self.index_name == discovery_index and sort_spec:
            opt_params["sort"] = list(sort_spec)
        if log_search_params:
            _ms.logger.info("Search query: opt_params=%s", opt_params)
        results = self.meilisearch_index.search(query_string, opt_params)
        return _ms.process_results(results, self.index_name)

    search._ost2_sort_patched = True
    _ms.MeilisearchEngine.search = search
'''

hooks.Filters.ENV_PATCHES.add_item(("openedx-common-settings", _SETTINGS_PATCH))
