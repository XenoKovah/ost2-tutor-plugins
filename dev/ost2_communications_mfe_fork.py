from tutormfe.hooks import MFE_APPS

# Point the communications MFE (the instructor "Email" / bulk email tool) at the OST2 fork branch that adds a
# "Don't send to" section to the bulk email form, with a "Students who completed the class." checkbox.
#
# Upstream frontend-app-communications only offers additive "Send to" recipient groups (Myself, Staff,
# All Learners, cohorts, course modes). The fork branch teak3_1_bulk_email_exclude_completed adds a second,
# subtractive section: checking "Students who completed the class." appends the `exclude_completed` target to the
# same `send_to` payload, and the LMS removes everyone holding a course certificate from the recipient list.
#
# This needs the matching LMS-side support, which lives on the edx-platform fork branch
# teak3_12_bulk_email_exclude_completed (EDX_PLATFORM_VERSION). Enabling this plugin without that branch deployed
# would let an author check the box and then get a 400 back on send, because the LMS would reject
# `exclude_completed` as an unrecognized target.
#
# teak3_1_bulk_email_exclude_completed = upstream release/teak.3 + that one commit, i.e. the exact communications
# source already built on dev.ost2.fyi plus the new section.
#
# We MUTATE the existing communications entry rather than replacing the whole dict, so tutor-mfe's default `port`
# (and any other defaults) are preserved. We only repoint repository + version, and set `refs` so tutor-mfe
# invalidates the Docker build cache when the branch tip moves.
@MFE_APPS.add()
def _ost2_communications_bulk_email_exclusions(mfes):
    mfes["communications"]["repository"] = "https://github.com/XenoKovah/frontend-app-communications.git"
    mfes["communications"]["version"] = "teak3_1_bulk_email_exclude_completed"
    mfes["communications"]["refs"] = (
        "https://api.github.com/repos/XenoKovah/frontend-app-communications/git/refs/heads"
    )
    return mfes
