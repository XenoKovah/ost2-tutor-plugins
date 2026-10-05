from tutor import hooks
# OST2: serve the Gamification FAQ screenshots at /GamificationFAQ/<file> on the
# LMS, Studio and apps.* MFE hosts, so course markdown can use ![](/GamificationFAQ/x.jpg).
# Files live in the LMS media volume: $(tutor config printroot)/data/openedx-media/GamificationFAQ/
# and are served by the LMS at /media/GamificationFAQ/; the MFE host reaches /media via
# ost2_mfe_media_proxy.
hooks.Filters.ENV_PATCHES.add_items(
    [
        ("caddyfile-lms", "rewrite /GamificationFAQ/* /media{path}"),
        ("caddyfile-cms", "rewrite /GamificationFAQ/* /media{path}"),
        ("mfe-caddyfile", "rewrite /GamificationFAQ/* /media{path}"),
    ]
)
