from tutor import hooks

# Adds:
#   - a required custom "Age" field (validated 14-89; logic in customreg.py),
#     which renders directly below the password field, AND
#   - three OPTIONAL fields: the built-in Gender dropdown (Male/Female/Other),
#     the built-in Country dropdown, and a custom Education dropdown.
#
# NOTE on "optional": the authn MFE only renders REQUIRED configurable fields
# on the main registration form. Optional fields are surfaced on a separate
# post-signup "progressive profiling" page, gated by
# ENABLE_PROGRESSIVE_PROFILING_ON_AUTHN -- which is OFF here, so gender,
# country and education do NOT appear at signup at all (they're defined but
# uncollected). Flip them back to "required" / set education required=True in
# customreg.py to put them back on the main form. Country, when shown, is a
# typeahead pre-filled from the server's GeoIP guess (clear it to pick another).
# to the Open edX registration page, and enables dynamic registration fields
# for the LMS backend + authn MFE. The MFE flag is delivered live via
# /api/mfe_config/v1, so no MFE image rebuild is needed.
#
# IMPORTANT: the profile keys persisted to UserProfile.meta
# (`extended_profile_fields`) is NOT a Django setting -- Django only loads
# UPPERCASE settings. It must be set on the LMS *SiteConfiguration* for the
# site that serves registration, e.g.:
#     site_values["extended_profile_fields"] = ["age", "education"]
hooks.Filters.ENV_PATCHES.add_items([
    (
        "openedx-lms-production-settings",
        """
REGISTRATION_EXTENSION_FORM = "lms.envs.tutor.customreg.ExtraRegistrationForm"
REGISTRATION_EXTRA_FIELDS["country"] = "optional"
REGISTRATION_EXTRA_FIELDS["gender"] = "optional"
ENABLE_DYNAMIC_REGISTRATION_FIELDS = True
try:
    MFE_CONFIG["ENABLE_DYNAMIC_REGISTRATION_FIELDS"] = True
except NameError:
    MFE_CONFIG = {"ENABLE_DYNAMIC_REGISTRATION_FIELDS": True}
""",
    ),
])
