from tutormfe.hooks import MFE_APPS

# Point the communications MFE (the instructor "Email" / bulk email tool) at the OST2 fork branch, which carries two
# additions to the bulk email form:
#
#  1. A "Don't send to" section with a "Students who completed the class." checkbox. Upstream only offers additive
#     "Send to" recipient groups (Myself, Staff, All Learners, cohorts, course modes); checking this box appends the
#     `exclude_completed` target to the same `send_to` payload, and the LMS removes everyone holding a course
#     certificate from the recipient list.
#  2. A switch under the "Body" heading that flips the body between the TinyMCE rich text editor and a plain
#     textarea, so an author who wants a plaintext email does not have to fight the formatting toolbar.
#
# (1) needs the matching LMS-side support, which lives on the edx-platform fork branch
# teak3_12_bulk_email_exclude_completed (EDX_PLATFORM_VERSION). Enabling this plugin without that branch deployed
# would let an author check the box and then get a 400 back on send, because the LMS would reject
# `exclude_completed` as an unrecognized target.
#
# (2) is frontend-only. Plain text mode still stores the body as HTML — escaped text plus <br>, and nothing else — so
# it needs no LMS change: a course email is always sent multipart, and a body encoded that way arrives with no
# styling and with the author's line breaks intact in both the text/plain and text/html parts.
#
# teak3_2_bulk_email_plaintext_body = upstream release/teak.3 + those two commits, i.e. the exact communications
# source already built on dev.ost2.fyi plus the new form controls.
#
# We MUTATE the existing communications entry rather than replacing the whole dict, so tutor-mfe's default `port`
# (and any other defaults) are preserved. We only repoint repository + version, and set `refs` so tutor-mfe
# invalidates the Docker build cache when the branch tip moves.
@MFE_APPS.add()
def _ost2_communications_bulk_email_exclusions(mfes):
    mfes["communications"]["repository"] = "https://github.com/XenoKovah/frontend-app-communications.git"
    mfes["communications"]["version"] = "teak3_2_bulk_email_plaintext_body"
    mfes["communications"]["refs"] = (
        "https://api.github.com/repos/XenoKovah/frontend-app-communications/git/refs/heads"
    )
    return mfes
