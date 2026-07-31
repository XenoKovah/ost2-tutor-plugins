from tutor import hooks

# OST2: proxy LMS-served content paths from the MFE host (apps.*) to the LMS, so
# images embedded in MFE-rendered content resolve instead of hitting the MFE SPA
# fallback. Fixes Discussions-MFE forum images that use relative /media/ URLs.
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
