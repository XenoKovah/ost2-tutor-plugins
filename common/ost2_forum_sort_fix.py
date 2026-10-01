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
# teak3_2_forum_learner_stats_fixes (stacked on the commit above) adds two more fixes to the same
# MySQL backend: (1) the Discussions "Learners" username search no longer loads every forum user on
# the site (it took ~32 s with ~4,500 users; now one CourseStat query), and (2) deleting a thread
# or a response refreshes the course stats of every other user whose comments disappeared with it,
# so the Learners tab no longer counts activity that no longer exists.
#
# Force-reinstall over the PyPI build because the fork keeps version 0.3.6, so a plain
# requirement would be skipped by pip as "already satisfied". Pinned to the full commit SHA so
# the build layer is deterministic and cache-keyed by the ref.
hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-dockerfile-post-python-requirements",
        'RUN pip install --force-reinstall --no-deps '
        '"git+https://github.com/XenoKovah/forum.git'
        '@3e99fc97e58d2807bd065c6b3be86dc355527c46"',
    )
)
