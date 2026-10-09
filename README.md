# ost2-tutor-plugins

Standalone [Tutor](https://docs.tutor.edly.io/) plugins for the OpenSecurityTraining2
Open edX deployments — **dev** (`dev.ost2.fyi`), **p** (`p.ost2.fyi`, live prod),
and **beta** (`beta.ost2.fyi`).

These are **single-file Tutor plugins** — mostly Python v1 plugins (each registers its
hooks directly via `tutor.hooks`), plus declarative **YAML patch plugins** (`.yml`).
They are dropped into the Tutor plugins root
(`$(tutor plugins printroot)` = `~/.local/share/tutor-plugins/`) and enabled per box.
Most are **settings-only** — the Open edX settings dir is bind-mounted into the
containers, so applying a change needs only `tutor config save` + a service restart,
**no image rebuild** (the few that change an MFE fork/image are noted below).

> This is distinct from `tutor-contrib-rgg`, which is the pip-installed RGG *plugin
> package* (`tutorrgg/`). This repo holds the loose per-box `ost2_*.py` (and other)
> standalone plugins that were previously untracked.

## Layout

```
common/   plugins byte-identical AND enabled on all three boxes
dev/      dev.ost2.fyi extras + dev-specific variants
p/        p.ost2.fyi   extras + p-specific variants
beta/     beta.ost2.fyi extras + beta-specific variants
host-scripts/  stdlib scripts run from cron on the Tutor HOST — NOT Tutor plugins
```

> **Never copy `host-scripts/` into the Tutor plugins root** — Tutor imports every `*.py`
> it finds there.

**To (re)provision a box: install `common/*.py` + `<box>/*.py`.** The plugin set for
each box is exactly `common` (12) plus that box's directory — dev=22, p=17, beta=18,
matching the live `tutor plugins list` on each. Contents were captured verbatim from
the running boxes (`~/.local/share/tutor-plugins/`) on 2026-07-31; committed files are
md5-identical to what is deployed.

## Enablement matrix

| Plugin | dev | p | beta | Source |
|---|:--:|:--:|:--:|---|
| disable_studio_course_pagination | ● | ● | ● | `common/` |
| ost2_authn_mfe_fork | ● | ● | ● | `common/` |
| ost2_authoring_mfe_fork | ● | ● | ● | per-box — **differs** |
| ost2_disable_survey_report | ● | ● | ● | `common/` |
| ost2_email_ratelimit | ● | ● | ● | `common/` |
| ost2_forum_sort_fix | ● | ● | ● | `common/` |
| ost2_handouts | ● | ● | ● | `common/` |
| password_policy | ● | ● | ● | `common/` |
| registration_custom_fields | ● | ● | ● | `common/` |
| enable_instructor_certificate_management | ● | ● | ● | `common/` |
| ost2_gamification_faq_static | ● | ● | ● | `common/` |
| ost2_lil_stranger_other_hosts | ● | ● | ● | `common/` — needs ost2_lil_stranger + ost2_mfe_media_proxy |
| ost2_course_discovery_sort | ● | ● | ● | per-box — **differs** |
| ost2_discussions_mfe_fork | ● | ● | ● | per-box — host |
| ost2_forum_profile_links | ● | ● | ● | per-box — host |
| ost2_mfe_media_proxy | ● | ● | ● | per-box — host (dev=p) |
| ost2_courses_hide_completed | ● | — | ● | `dev/`, `beta/` |
| GoogleAnalytics4Plugin | ○ | ● | ● | per-box — GA id |
| ost2_student_grade_lookup | ● | — | — | `dev/` (prototype) |
| ost2_default_grading_policy | ● | — | — | `dev/` |
| ost2_communications_mfe_fork | ● | — | — | `dev/` (needs matching LMS branch) |
| ost2_search_unreleased_for_staff | — | — | ● | `beta/` |
| disable_markdown_safemode | ● | ? | ○ | `dev/` |
| ost2_markdown_xblock_parse_xml | ● | — | — | `dev/` — needs `tutor images build openedx` |
| ost2_learning_mfe_fork | ● | — | ● | `dev/`, `beta/` — both `teak3_5_timing-feedback-midclass-nudge` (mid-class nudge only active where the flag plugin is); needs `tutor images build mfe` |
| ost2_timing_feedback_midclass_nudge | — | — | ● | `beta/` only — MFE_CONFIG flag for the mid-class Timing Feedback nudge (TF is mandatory on beta); never on dev/p |
| ost2_learner_dashboard_mfe_fork | ● | — | — | `dev/` — learner-dashboard fork `teak3_3_ost2-dashboard-customizations` (multi-select unenroll survey + current-grade banners + header labels); needs `tutor images build mfe` and a tutor-indigo without the old learner-dashboard patches |
| ost2_mfe_bookworm_base | ● | — | — | `dev/` — MFE image on node bookworm (bullseye apt is 404 since EOL); survives `config save`; p/beta get it on next sync-with-dev |

● enabled · ○ present but **disabled** · — not installed · ? not verified

### Per-box differences

- **GoogleAnalytics4Plugin** — GA4 measurement ID differs: p = `G-2J9NKZGFKK`,
  beta = `G-GL3SQZS9CB`. **Disabled on dev** ("GA dark" since the Teak cutover); dev's
  disabled copy is not captured here.
- **ost2_forum_profile_links**, **ost2_discussions_mfe_fork**, **ost2_mfe_media_proxy**
  — differ only by the baked-in MFE host (`apps.dev|p|beta.ost2.fyi`). dev's and p's
  `ost2_mfe_media_proxy.py` are byte-identical; beta's differs only in its docstring.
- **ost2_authoring_mfe_fork** — **p lags one fork branch**: dev and beta pin
  `teak3_3_remove-studio-home-about-blurb` (byte-identical copies), p still pins
  `teak3_2_course-handouts-ui`. teak3_3 only drops the "New to Studio?" about blurb
  from the Studio home sidebar, so converging p is purely additive — bump the pin and
  rebuild the `mfe` image there.
- **disable_markdown_safemode** — beta carries an untracked 2024 copy of this file that is
  **disabled** and has the wrong payload (`safe_mode: False` but with the seven extra
  markdown2 extras); it is not captured here. beta and p both still have the underlying
  `[HTML_REMOVED]` bug — the fix is a copy of `dev/disable_markdown_safemode.yml` plus
  `tutor plugins enable`. p's state was not verified (no SSH access at the time of writing).
- **ost2_course_discovery_sort** — **dev runs a newer variant** (sorts the `/courses`
  catalog by course start date, then course title `content.display_name` as tie-break);
  p and beta run the older **start-only** variant. Converging p/beta to dev's version is
  purely a content update (re-index not required — Meilisearch re-sorts on
  `sortableAttributes` change).

## What each plugin does

- **disable_studio_course_pagination** — Studio home lists all courses on one page (pre-Teak behavior).
- **ost2_authn_mfe_fork** — repoint the authn MFE to the XenoKovah fork (18-char password policy branch).
- **ost2_authoring_mfe_fork** — repoint the authoring (Studio) MFE to the XenoKovah fork
  (p is one branch behind dev/beta; see Per-box differences).
- **ost2_disable_survey_report** — disable the LMS Django-admin "Open edX Data Sharing Initiative" banner (`SURVEY_REPORT_ENABLE=False`).
- **ost2_email_ratelimit** — `RateLimitedEmailBackend` to stay under Gmail send throttles.
- **ost2_forum_sort_fix** — force-install the forum fork with the MySQL child-comment sort fix.
- **ost2_handouts** — course handouts fix.
- **password_policy** — `AUTH_PASSWORD_VALIDATORS` (min length 18, no complexity, max 128) + longer generated passwords so OAuth signups don't 400.
- **registration_custom_fields** — custom registration fields (only AGE required).
- **enable_instructor_certificate_management** — show the instructor-dashboard **Certificates** tab (`/courses/<id>/instructor#view-certificates`) to course-team **Admins** (`CourseInstructorRole`), not only to global site staff. Sets `FEATURES["ENABLE_CERTIFICATES_INSTRUCTOR_MANAGE"] = True`, because `instructor_dashboard.py` gates that section on `access['admin']`, which is Django's site-wide `request.user.is_staff` despite the name. The certificate endpoints in `instructor/permissions.py` are already `is_staff | HasAccessRule('instructor')`, so this only reveals UI the course Admin was already authorized to use — it does not widen who may call those endpoints. Effect is per-course (only where they hold Admin). Does **not** enable the bulk Generate/Regenerate panel — that is a separate flag, `CERTIFICATES_INSTRUCTOR_GENERATION`, left off.
- **ost2_gamification_faq_static** — serve the Gamification FAQ screenshots at `/GamificationFAQ/<file>` on the LMS, Studio and `apps.*` hosts, so course markdown can use `![](/GamificationFAQ/x.jpg)`. The images are not in the plugin: they live in the LMS media volume, `$(tutor config printroot)/data/openedx-media/GamificationFAQ/`, and Caddy rewrites `/GamificationFAQ/*` to `/media/GamificationFAQ/*` (the `apps.*` host reaches `/media` through `ost2_mfe_media_proxy`). Adding an image is just copying it into that directory. Caddy-only — `tutor config save` + `caddy reload` in the caddy and mfe containers, no restart.
- **ost2_lil_stranger_other_hosts** — reverse-proxy `/lil-stranger*` (served by the LMS via `ost2_lil_stranger`) from the Studio and `apps.*` hosts to the LMS, so relative `/lil-stranger/<img>` URLs render in Studio previews and MFE-rendered content. Caddy-only, like the above.
- **ost2_course_discovery_sort** — sort the `/courses` discovery catalog (oldest start first; +title tie-break on dev).
- **ost2_discussions_mfe_fork** — repoint the discussions MFE to the XenoKovah fork (default Oldest-first sort, etc.).
- **ost2_forum_profile_links** — make forum `/u/<name>` author links resolve to the profile MFE (`PROFILE_MFE_BASE`).
- **ost2_mfe_media_proxy** — proxy LMS content paths (`/media`, `/asset-v1`) from the MFE host to the LMS so Discussions-MFE forum images resolve.
- **ost2_courses_hide_completed** — hide already-completed courses from the `/courses` listing.
- **GoogleAnalytics4Plugin** — inject the GA4 tag id on the LMS + MFEs.
- **ost2_student_grade_lookup** — staff-only `/admin/student-grade-lookup/` page: look up a learner by username/email/ID and list all enrollments + live grade % + certificate status (incl. certificate-exception flag); sortable columns; linked from the admin index.
- **ost2_communications_mfe_fork** — repoint the communications (bulk email) MFE to the XenoKovah fork, which adds the "Don't send to" → "Students who completed the class." checkbox and a switch under the "Body" heading that turns the rich text editor into a plain textarea. The checkbox requires the edx-platform branch `teak3_12_bulk_email_exclude_completed`, which teaches the LMS the `exclude_completed` target; without it the LMS rejects the send with a 400. The plaintext switch is frontend-only. Changes an MFE image, so it needs an MFE rebuild, not just `tutor config save`.
- **ost2_learner_dashboard_mfe_fork** — repoint the learner-dashboard MFE to the XenoKovah fork branch `teak3_3_ost2-dashboard-customizations` (upstream `release/teak.3` + the multi-select unenroll survey and its tracking-log write, which feeds `host-scripts/ost2_unenroll_report.py`, + the "Current grade: N%" banners and "My Enrolled Courses" / "Discover New Courses" header labels that used to be tutor-indigo build patches). **Pair it with a tutor-indigo that no longer carries those patches** (their guard greps would fail the build); a box that does not pin this fork needs the patches. Changes an MFE image, so it needs an MFE rebuild.
- **ost2_default_grading_policy** — a newly created course comes up with OST2's house
  grading criteria instead of the Open edX stock one, and every new subsection is created
  graded as **Progress Marker**. Assignment types become *Progress Marker* /
  `ProgressMarker` (weight 100, total number 1, 0 droppable) and *Timing Feedback* /
  `TimingFeedback` (weight 0, total number 1, 0 droppable); the Overall Grade Range becomes
  Fail 0-99 / Pass 99-100 (`GRADE_CUTOFFS` `Pass` = 0.99). This is the setup documented in
  the InstructorHowTo class (“Grading”), which until now had to be rebuilt by hand on every
  new class. Works by mutating `xmodule.course_metadata_utils.DEFAULT_GRADING_POLICY` **in
  place** — XBlock keeps that exact object as `CourseFields.grading_policy`'s `Field._default`
  — in `openedx-common-settings`, so the CMS Grading page and LMS grade computation agree.
  The subsection default wraps `contentstore…create_xblock.create_xblock` (the single funnel
  for the legacy outline, the Authoring MFE, and the v0 CMS REST API) and calls the same
  `CourseGradingModel.update_section_grader_type()` the “Grade as” dropdown does. It fires
  only when the course's *own* policy defines a Progress Marker type, and reuses that
  course's spelling of it (older classes use the no-space `ProgressMarker`), so a course
  still on the stock Homework/Lab/Midterm/Final policy is left alone and no subsection can
  get an assignment type its course does not define. Course *import* bypasses
  `create_xblock`: verified on dev that importing an OLX over a freshly created course
  restores the OLX's own `GRADER`/`GRADE_CUTOFFS` and per-subsection `graded`/`format`
  exactly. Caveat — an OLX with **no** `policies/<run>/grading_policy.json` has nothing to
  override with, so it now falls back to this default rather than the upstream one (a normal
  Studio export always writes that file). Settings-only —
  `tutor config save` + `tutor local restart lms cms lms-worker cms-worker`.
- **disable_markdown_safemode** — stop the Markdown component from deleting raw HTML.
  hastexo's markdown-xblock hardcodes markdown2 `safe_mode='replace'`, which replaces every
  run of raw HTML with the literal string `[HTML_REMOVED]`, rewrites link hrefs it does not
  like to `#`, and leaks `<!-- author notes -->` as visible page text. Code fences do **not**
  protect their contents: with safe_mode on, markdown2 defers the `fenced-code-blocks` extra
  to `Stage.LINK_DEFS`, i.e. *after* its HTML-hashing pass (`markdown2.py` `test()`; stages run
  `PREPROCESS → HASH_HTML → LINK_DEFS`), so an HTML sample inside a triple-backtick fence — or in a
  4-space code block — is eaten before the fence is ever recognised. Sets
  `XBLOCK_SETTINGS["markdown"]`. **`safe_mode: False`, not `'escape'`**: of the 163 affected
  blocks on dev only 20 had their HTML inside a fence — 143 used raw HTML *outside* one,
  authored to render, which `escape` would turn into visible tag soup. The `extras` list is
  pinned to markdown_xblock's own 5 `DEFAULT_EXTRAS`; an earlier 2024 revision of this file —
  present on dev and beta but not enabled on either since the Teak cutover — also added header-ids / smarty-pants / strike / target-blank-links / wiki-tables
  / tag-friendly / pyshell, which would have changed the rendering of all 2447 markdown blocks
  on the box (curly quotes, heading ids, every link forced to `target=_blank`). Verified on dev
  by rendering every markdown block before and after: `[HTML_REMOVED]` 164 → 0, 2190 of 2447
  byte-identical, and no block lost content. Settings-only — `tutor config save` +
  `tutor local restart lms cms lms-worker cms-worker`.
- **ost2_markdown_xblock_parse_xml** — stop OLX course import from **silently dropping every
  Markdown component**. markdown-xblock 1.4.0 declares
  `parse_xml(cls, node, runtime, keys, id_generator)`, but XBlock 5.2.0 (Teak) removed the
  `id_generator` argument, so each `<markdown/>` element raises
  `TypeError: MarkdownXBlock.parse_xml() missing 1 required positional argument`.
  `xmodule/vertical_block.py` catches that and only logs "Unable to load child when parsing
  Vertical. Continuing...", so the import reports success while the blocks are discarded —
  observed importing two courses beta → dev, where 10 markdown blocks became 0 and 35 became 0.
  Only XML import is affected (`parse_xml` is not used for rendering, Studio editing or export),
  so it bites on course import, course rerun, and any export/import round trip. Patches the
  installed `html.py` at image-build time with a base64-embedded python script (same
  `echo <b64> | base64 -d | python -` pattern as `ost2_handouts`) to make `id_generator`
  optional and derive the resource path from `url_name`; upstream only used `id_generator` to
  compute `base`, which is always the literal `markdown` directory, so it is behaviour
  preserving. The script is idempotent and **fails the image build loudly** if its anchors ever
  stop matching, rather than silently no-op'ing. Hook is **`openedx-dockerfile`**, *not*
  `openedx-dockerfile-post-python-requirements` — the latter renders before the
  `OPENEDX_EXTRA_PIP_REQUIREMENTS` loop that installs markdown-xblock, so the file would not
  exist yet; `openedx-dockerfile` sits at the end of the `production` stage, after the venv
  `COPY` and while `USER` is `app`, and `development`/`final` both derive `FROM production`.
  Changes the image — needs `tutor images build openedx` + `tutor local start -d`. Belongs
  upstream in hastexo/markdown-xblock; drop this plugin if a release ever supports XBlock 5.
- **ost2_search_unreleased_for_staff** — let course staff search courses that have not started yet. edx-search's `SearchFilterGenerator.filter_dictionary()` hard-codes `{"start_date": DateRange(None, utcnow())}`, and `LmsSearchFilterGenerator` does not override it, so every block of a future-dated course is filtered out of search results — for staff and superusers too. OST2 uses a far-future start (typically 2030-01-01) as the “keep this course unreleased” sentinel, so those courses were silently unsearchable even though they were fully indexed (InstructorHowTo: 123 docs in Meilisearch, 36 matching “video”, `/search/` returned 0). **Reindexing does not fix this and never will.** Points `SEARCH_FILTER_GENERATOR` at a wrapper that delegates to the stock generator and drops *only* the `start_date` bound, and only for staff: with a `course_id`, for anyone holding `has_access(user, 'staff', course_key)` (course staff/instructor or global staff); without one, for global staff only. Learners keep the stock filter, so unreleased text cannot leak via the `/search/` endpoint; any error fails closed. Imports are deferred into method bodies because the settings module loads before `django.setup()`. Settings-only — `tutor config save` + `tutor local restart lms`.

## Deploy (per box)

Copy `common/` + the box's directory into the Tutor plugins root, enable each, then
apply:

```
box=dev                                     # or p , beta
root=$(~/tutor-venv/bin/tutor plugins printroot)
cp common/* "$box"/* "$root"/
for f in common/* "$box"/*; do
  b=$(basename "$f"); ~/tutor-venv/bin/tutor plugins enable "${b%.*}"
done
~/tutor-venv/bin/tutor config save
~/tutor-venv/bin/tutor local restart lms cms
```

The `ost2_forum_sort_fix`, `ost2_authn_mfe_fork`, `ost2_authoring_mfe_fork`, and
`ost2_discussions_mfe_fork` plugins pin MFE/forum forks that are baked at image build
— changing those needs the corresponding `tutor images build` (mfe / openedx), not just
a restart. `ost2_markdown_xblock_parse_xml` likewise patches a python package inside the
`openedx` image, so it needs `tutor images build openedx` + `tutor local start -d`.

## Host scripts (`host-scripts/`)

### ost2_unenroll_report.py — weekly unenrollment-reasons email

Emails the learner-dashboard "Why are you unenrolling?" survey answers collected since the
last report (Sundays 08:00 UTC, to `xeno@ost2.fyi`). Needs the learner-dashboard MFE branch
`teak3_2_unenroll-survey-multiselect`, which POSTs each submitted survey to the LMS `/event`
endpoint so it lands in `tracking.log` as an `unenrollment_reason.selected` record. (Before
that MFE is deployed nothing records these answers — the stock Segment tracker is a no-op
because `SEGMENT_KEY` is empty.) Python 3 standard library only; reads SMTP settings
(`SMTP_*`, `CONTACT_EMAIL`) from `~/tutor-venv/bin/tutor config printvalue`, so it sends
through the same Gmail relay as the LMS. The email names courses and reasons only — no
learner identity. It sends even when the week was empty, so silence means the job is broken.

Window = end of the last successful report (`~/.local/share/ost2-unenroll-report/last_report_end`)
→ now, so a missed week is caught up rather than lost; the first run covers 7 days. On a
failure the marker is not advanced and `last_failure` is written next to it.

Install on a box (then check the dry run before trusting the cron line):

```
mkdir -p ~/ost2-host-scripts ~/.local/share/ost2-unenroll-report
cp host-scripts/ost2_unenroll_report.py ~/ost2-host-scripts/
python3 ~/ost2-host-scripts/ost2_unenroll_report.py --to xeno@ost2.fyi --dry-run
( crontab -l 2>/dev/null; echo '0 8 * * 0 /usr/bin/python3 /home/ubuntu/ost2-host-scripts/ost2_unenroll_report.py --to xeno@ost2.fyi >> /home/ubuntu/.local/share/ost2-unenroll-report/report.log 2>&1' ) | crontab -
```

Cron uses the box clock (UTC on dev). Send one now with the real SMTP path:
`python3 ~/ost2-host-scripts/ost2_unenroll_report.py --to xeno@ost2.fyi --since 2026-01-01`
(an explicit `--since` replays history and never moves the weekly marker). Tests:
`cd host-scripts && python3 -m unittest -v test_ost2_unenroll_report`.

Caveat: `tracking.log` is deliberately never rotated (see the `openedx-tutor` logrotate
file), so on p it is tens of GB; the script pre-filters with `grep -F` so a run is a linear
scan, not a parse. Per-box: install it separately on p and beta when those get the MFE.

### ost2_completion_nudge.py — nudge learners stuck just short of finishing

Daily cron job that emails the "Li'l Stranger nudge" (the platform logo in the same light-gray header
band as normal course emails, linked to the box's home page; the mascot image; links to the class, to its
Progress page, and to the box's own `/gamma_dashboard/dashboard/` (Accomplishments) and
`/gamma_dashboard/leaderboard/` (Leaderboard) pages, built from `LMS_HOST`/`MFE_HOST` so p's email
points at p; then a course-email footer with a one-click per-class unsubscribe) to learners whose current grade is above 90% but who have not passed and have
not touched the class for more than 14 days. "% done" is the persisted course grade, the same number
the learner dashboard shows as "Current grade" (`grades_persistentcoursegrade.percent_grade`);
activity is the newest `courseware_studentmodule.modified` for that learner and course. Skipped:
inactive/staff accounts, inactive enrollments, courses that have not started, have ended or are
hidden, learners who hold a certificate or allowlist entry, opted out of that course's email, or
are on its course team, and learners who passed, got a certificate or were recently active in
another run of the same-named class. Python 3 standard library only; learners are selected on the
host with `docker exec <mysql container> mysql` (SELECT only, session READ ONLY).

**How it sends: through the same route as every other system email.** The host script renders the
messages and hands them to a small delivery agent that it runs inside the LMS container
(`docker exec -i tutor_local-lms-1 ./manage.py lms shell`, source on stdin, nothing installed). The
agent sends with Django's configured `EMAIL_BACKEND`, which on OST2 boxes is the
`ost2_email_ratelimit` plugin's `RateLimitedEmailBackend`: one Redis budget shared by ALL server mail
(password resets, forum notifications, instructor bulk email, these nudges) with messages at least 2 s
apart (30/min) and a hard daily cap (1,800 of Gmail's ~2,000/day for the one Workspace account all mail
is relayed through). On top of that the agent yields (stops once the whole server has sent 50% of the
daily cap today, `--yield-above`), keeps 2 s between its own messages (`--delay`), backs off and
retries when the limiter has no slot free (5/15/45/90 s) or Gmail answers 421/4xx (60 s, 5 min,
15 min), and ends the run on the daily cap or a Gmail quota reply. Those early stops are normal (exit 0,
nothing is dropped or double-sent, the next run resumes); credentials/network/route problems write
`last_failure` and exit 1. It refuses to send if the box's route is a real SMTP backend WITHOUT the
limiter, or if the limiter's Redis is unreachable (fail-open would send unpaced). The footer's
unsubscribe link is built by `bulk_email.api.get_unsubscribed_link` and opens the platform's
confirm page (no login; GET changes nothing, the Confirm button writes the `bulk_email_optout` row the
selection already honours). Course-email opt-out is PER CLASS in Open edX, there is no global one: the
only other place is the "Email settings" item in the menu on each class card of the learner dashboard,
which is why the stock footer's "update your course email settings here" (a link to `/dashboard`) is
left out. The host script no longer reads any SMTP setting.

Nothing is sent unless asked: no flag = dry run (counts, who would be nudged by learner id, a pre-flight
of the mail route with today's server-wide usage and an unsubscribe-link check, a preview of the first
email); `--only-to ADDR` = test (the real email for the top candidate, delivered to ADDR only, subject
tagged `[TEST]`, ledger untouched; its unsubscribe link belongs to the account owning ADDR or to
`--unsub-as USERNAME`, never to the learner whose data was used); `--send` = live. A learner is nudged
about a class at most once ever and at most once every 7 days overall
(`~/.local/share/ost2-completion-nudge/sent.jsonl`, ids only, so missed days and reruns are harmless).
One run sends at most 100 emails, closest-to-done and most-recently-active first, so a backlog drains
over several days; `--max-inactive-days 365` leaves out learners who vanished years ago. The mascot
and the header logo must both be reachable before anything is sent.

**dev never delivers mail:** the dev-only plugin `ost2_dev_mail_to_files` points the LMS at the file
backend so a copy of p's learners can never be emailed from dev. There the dry run says "NOTHING IS
DELIVERED from here", and `--send`/`--only-to` write files and leave the ledger alone. To really
deliver one test from dev, force the limiter backend in a test run (refused with `--send`):

```
python3 ~/ost2-host-scripts/ost2_completion_nudge.py --only-to xeno@ost2.fyi --unsub-as Xeno --mail-backend openedx.core.lib.ost2_ratelimit_email_backend.RateLimitedEmailBackend
```

The header logo is the one course emails use, `https://<LMS_HOST>/theming/asset/images/logo.png` (it
redirects to the theme's hashed static file; `--logo-url` overrides it). The mascot is the transparent
PNG `host-scripts/assets/lil-stranger/hello.png` (made from
`/lil-stranger/hello.webp` by `assets/make_lil_stranger_png.py`; 600 px wide, displayed at 300 px). It
is served from the LMS media volume at `https://<LMS_HOST>/media/lil-stranger/hello.png`, so it needs a
file copy on each box and no restart.

This script, the PNG and the cron line are NOT carried to another box by the OST2-sync-with-dev skill
(it syncs images, pins and enabled Tutor plugins only), so each box needs the install below. On p the
dry run must report `RateLimitedEmailBackend` as the mail route before the cron line is added.

Install on a box, check the dry run, send one test, then add the cron line:

```
mkdir -p ~/ost2-host-scripts ~/.local/share/ost2-completion-nudge ~/.local/share/tutor/data/openedx-media/lil-stranger
cp host-scripts/ost2_completion_nudge.py ~/ost2-host-scripts/
cp host-scripts/assets/lil-stranger/hello.png ~/.local/share/tutor/data/openedx-media/lil-stranger/
python3 ~/ost2-host-scripts/ost2_completion_nudge.py
python3 ~/ost2-host-scripts/ost2_completion_nudge.py --only-to xeno@ost2.fyi --unsub-as Xeno
( crontab -l 2>/dev/null; echo '17 15 * * * /usr/bin/python3 /home/ubuntu/ost2-host-scripts/ost2_completion_nudge.py --send >> /home/ubuntu/.local/share/ost2-completion-nudge/nudge.log 2>&1' ) | crontab -
```

Do not put the `--send` cron on dev (it would only write files, and dev holds p's learners' real
addresses). Tests: `cd host-scripts && python3 -m unittest -v test_ost2_completion_nudge`.

## Implementation notes

- Tutor **Jinja-renders** `ENV_PATCHES` strings, so any Python injected as a patch
  string must avoid `{{`, `{%`, `{#` (empty `{}` and lone `}}` are fine). Prefer
  `%`-formatting over f-strings and inline `style=` attributes over CSS blocks.
- The rendered `.../settings/lms/production.py` is bind-mounted **read-only**;
  syntax-check it with the built-in `compile(open(p).read(), p, 'exec')` (not
  `py_compile`, which tries to write a `.pyc`).
