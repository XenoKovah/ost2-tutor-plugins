from tutor import hooks

# Replaces AUTH_PASSWORD_VALIDATORS with OST2's policy (min 18, no complexity,
# max 128) AND patches edx_django_utils.user.generate_password so the LMS's
# auto-generated OAuth-signup password passes the 18-char minimum.
#
# Why the monkeypatch: on Teak, the third-party-auth (Google, GitHub, ...)
# registration flow auto-generates the account password:
#     openedx/core/djangoapps/user_authn/views/register.py:178
#         params["password"] = generate_password()
#     openedx/core/djangoapps/user_authn/views/auto_auth.py:67
#         generated_password = generate_password()
# Upstream generate_password() defaults to length=12, which fails the 18-char
# MinimumLengthValidator below, causing every OAuth signup to die with HTTP 400
# ("We couldn't create your account"). The other callers (retirement
# accounts/utils.py:335, support manage_user.py:80) already pass length=25.
# We bump the wrapper default to 25 so auto-generated passwords satisfy the
# new policy.
#
# register.py / auto_auth.py do `from edx_django_utils.user import
# generate_password`, binding the *name* at import time. This patch lives in
# openedx-common-settings, which runs at settings-load time -- BEFORE the URLconf
# imports register.py -- so that import binds to the patched function.
# (Mechanism unchanged from Palm; only the register.py path/line moved on Teak.)
hooks.Filters.ENV_PATCHES.add_items([
    (
        "openedx-common-settings",
        """
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "common.djangoapps.util.password_policy_validators.MinimumLengthValidator",
     "OPTIONS": {"min_length": 18}},
    {"NAME": "common.djangoapps.util.password_policy_validators.MaximumLengthValidator",
     "OPTIONS": {"max_length": 128}},
]

try:
    import edx_django_utils.user as _ost2_eu
    _ost2_orig_gp = _ost2_eu.generate_password
    def _ost2_generate_password(length=25, *args, **kwargs):
        if length is None or length < 25:
            length = 25
        return _ost2_orig_gp(length=length, *args, **kwargs)
    _ost2_eu.generate_password = _ost2_generate_password
except Exception:
    pass
""",
    ),
])
