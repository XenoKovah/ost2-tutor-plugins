from tutormfe.hooks import MFE_APPS

# Point the authn MFE at the OST2 fork branch that carries the 18-char
# client-side password policy. That branch (teak3_1_password18) drops the
# hard-coded "1 letter / 1 number / 8 characters" rules from BOTH the
# password-requirement tooltip AND the client-side submit/blur gates, so the
# MFE matches the LMS AUTH_PASSWORD_VALIDATORS set by the password_policy
# Tutor plugin (UserAttributeSimilarity + 18-char minimum, no complexity).
#
# teak3_1_password18 is the Teak port of the Palm branch
# palm4_branch3_password18 (rebased onto upstream release/teak; tutor 20.x).
#
# We MUTATE the existing authn entry rather than replacing the whole dict, so
# tutor-mfe's default `port` (and any other defaults) are preserved. We only
# repoint repository + version, and set `refs` so tutor-mfe invalidates the
# Docker build cache when the branch tip moves upstream.
@MFE_APPS.add()
def _ost2_authn_password18(mfes):
    mfes["authn"]["repository"] = "https://github.com/XenoKovah/frontend-app-authn.git"
    mfes["authn"]["version"] = "teak3_1_password18"
    mfes["authn"]["refs"] = "https://api.github.com/repos/XenoKovah/frontend-app-authn/git/refs/heads"
    return mfes
