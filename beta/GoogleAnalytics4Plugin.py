from tutor import hooks
hooks.Filters.ENV_PATCHES.add_items([
    (
        "openedx-common-settings",
        "GOOGLE_ANALYTICS_4_ID = 'G-GL3SQZS9CB'"
    ),
    (
        "mfe-lms-common-settings",
        "MFE_CONFIG['GOOGLE_ANALYTICS_4_ID'] = 'G-GL3SQZS9CB'"
    ),
])
