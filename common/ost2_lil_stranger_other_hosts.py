from tutor import hooks
# OST2: /lil-stranger/* (served by the LMS via ost2_lil_stranger) also on the Studio and
# apps.* MFE hosts, so content previews and MFE-rendered content can use relative
# /lil-stranger/<img> URLs. Host header must be LMS_HOST or the LMS returns 400.
hooks.Filters.ENV_PATCHES.add_items(
    [
        (
            "caddyfile-cms",
            """reverse_proxy /lil-stranger* lms:8000 {
    header_up Host {{ LMS_HOST }}
    header_up X-Forwarded-Port 443
}""",
        ),
        (
            "mfe-caddyfile",
            """reverse_proxy /lil-stranger* lms:8000 {
    header_up Host {{ LMS_HOST }}
}""",
        ),
    ]
)
