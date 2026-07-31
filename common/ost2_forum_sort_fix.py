from tutor import hooks

# OST2: install the forum-v2 child-comment sort-order fix from the XenoKovah/forum fork.
#
# openedx-forum's MySQL backend (Comment.get_list) ordered a response's child comments by the
# STRING sort_key ("{parent_id}-{comment_id}"), which mis-sorts once a response's child comment
# ids cross a digit-length boundary (e.g. 999 -> 1000): lexicographically "995-1000" < "995-999",
# so the newer reply renders above the older one. The fork branch
# teak3_1_fix_mysql_forums_sort_order_bug (commit a5039e7, branched off the deployed 0.3.6 tag)
# sorts by created_at (with pk tiebreaker) instead.
#
# Force-reinstall over the PyPI build because the fork keeps version 0.3.6, so a plain
# requirement would be skipped by pip as "already satisfied". Pinned to the full commit SHA so
# the build layer is deterministic and cache-keyed by the ref.
hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-dockerfile-post-python-requirements",
        'RUN pip install --force-reinstall --no-deps '
        '"git+https://github.com/XenoKovah/forum.git'
        '@a5039e740ae062a3fb9e0ce62965370a707d93a6"',
    )
)
