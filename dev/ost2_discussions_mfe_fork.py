from tutormfe.hooks import MFE_APPS

# Point the discussions MFE at the OST2 fork branch that defaults the in-thread
# response/comment sort to "Oldest first".
#
# Upstream frontend-app-discussions hard-codes the comments Redux slice
# initialState `sortOrder: true` (= "Newest first"), and that value is ephemeral
# client state (no localStorage / no server preference), so it is the effective
# platform default that every page load starts from. The fork branch
# teak3_1_default-oldest-response-sort flips that single initialState line to
# `false` (= "Oldest first"); learners can still toggle per-view via the
# CommentsSort dropdown (the choice still does not persist).
#
# teak3_1_default-oldest-response-sort = upstream release/teak.3 + that one
# commit, i.e. the exact discussions source already built on ap.ost2.fyi plus the
# default flip. All other OST2 discussions customizations -- the "(profile)"
# author link injected by ost2_forum_profile_links and the dark-mode filter-bar
# SCSS -- are applied on top at build time and are unaffected by this repoint.
#
# We MUTATE the existing discussions entry rather than replacing the whole dict,
# so tutor-mfe's default `port` (and any other defaults) are preserved. We only
# repoint repository + version, and set `refs` so tutor-mfe invalidates the
# Docker build cache when the branch tip moves.
@MFE_APPS.add()
def _ost2_discussions_default_oldest_sort(mfes):
    mfes["discussions"]["repository"] = "https://github.com/XenoKovah/frontend-app-discussions.git"
    mfes["discussions"]["version"] = "teak3_1_default-oldest-response-sort"
    mfes["discussions"]["refs"] = "https://api.github.com/repos/XenoKovah/frontend-app-discussions/git/refs/heads"
    return mfes
