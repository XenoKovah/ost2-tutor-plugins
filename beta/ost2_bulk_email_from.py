"""
Tutor plugin: ost2_bulk_email_from (beta.ost2.fyi)

Sends instructor bulk email (Instructor Dashboard > Email) From: noreply2@ost2.fyi
instead of info@ost2.fyi.

Tutor's production.py sets
    BULK_EMAIL_DEFAULT_FROM_EMAIL = ENV_TOKENS.get("BULK_EMAIL_DEFAULT_FROM_EMAIL",
                                                   ENV_TOKENS["CONTACT_EMAIL"])
and CONTACT_EMAIL is info@ost2.fyi, so bulk mail went out From: info@ost2.fyi.
The box authenticates to smtp.gmail.com as noreply2@ost2.fyi (SMTP_USERNAME), and
info@ is not a verified "Send mail as" alias of that mailbox, so Gmail rewrote
the header to noreply2@ost2.fyi anyway (keeping the original in X-Google-Original-From).
This makes the header honest and matches the DKIM/DMARC-aligned sender.

Only bulk_email/tasks.py (when EMAIL_USE_COURSE_ID_FROM_FOR_BULK is off, the
default) and calendar_sync read this setting. DEFAULT_FROM_EMAIL (password
reset, activation and other transactional mail) and CONTACT_EMAIL are unchanged.

The value is per-box (p sends as noreply@, beta as noreply2@): never copy this
file between boxes.

Settings-only change -- NO image rebuild (env/apps/openedx/settings is
bind-mounted into the containers):
    cp ost2_bulk_email_from.py "$(tutor plugins printroot)"/
    tutor plugins enable ost2_bulk_email_from
    tutor local restart lms-worker cms-worker lms cms
"""
from tutor import hooks

__version__ = "1.0.0"

FROM_EMAIL = "noreply2@ost2.fyi"

for patch in ("openedx-lms-production-settings", "openedx-cms-production-settings"):
    hooks.Filters.ENV_PATCHES.add_item((
        patch,
        f'BULK_EMAIL_DEFAULT_FROM_EMAIL = "{FROM_EMAIL}"',
    ))
