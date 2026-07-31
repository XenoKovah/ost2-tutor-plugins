from tutor import hooks
# OST2: proxy LMS content paths (/media, /asset-v1) from the MFE host to the LMS so
# images embedded in MFE-rendered content (Discussions forum /media images) resolve.
hooks.Filters.ENV_PATCHES.add_item(
    (
        "mfe-caddyfile",
        """reverse_proxy /media/* lms:8000 {
    header_up Host {{ LMS_HOST }}
}
reverse_proxy /asset-v1* lms:8000 {
    header_up Host {{ LMS_HOST }}
}""",
    )
)
