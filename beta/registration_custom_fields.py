from tutor import hooks

# Adds:
#   - a required custom "Age" field (validated 14-89; logic in customreg.py),
#     which renders directly below the password field,
#   - the built-in Country/Region field, REQUIRED (it sets which country's
#     Leaderboard the account appears on); the authn MFE renders it after Age
#     as a searchable dropdown pre-filled from the GeoIP guess, AND
#   - two OPTIONAL fields: the built-in Gender dropdown (Male/Female/Other)
#     and a custom Education dropdown.
#
# NOTE on "optional": the authn MFE only renders REQUIRED configurable fields
# on the main registration form. Optional fields are surfaced on a separate
# post-signup "progressive profiling" page, gated by
# ENABLE_PROGRESSIVE_PROFILING_ON_AUTHN -- which is OFF here, so gender and
# education do NOT appear at signup at all (they're defined but uncollected).
# Flip gender to "required" / set education required=True in customreg.py to
# put them on the main form.
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
REGISTRATION_EXTRA_FIELDS["country"] = "required"
REGISTRATION_EXTRA_FIELDS["gender"] = "optional"
ENABLE_DYNAMIC_REGISTRATION_FIELDS = True
try:
    MFE_CONFIG["ENABLE_DYNAMIC_REGISTRATION_FIELDS"] = True
except NameError:
    MFE_CONFIG = {"ENABLE_DYNAMIC_REGISTRATION_FIELDS": True}
""",
    ),
])
