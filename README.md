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
```

**To (re)provision a box: install `common/*.py` + `<box>/*.py`.** The plugin set for
each box is exactly `common` (9) plus that box's directory — dev=17, p=15, beta=16,
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

● enabled · ○ present but **disabled** · — not installed

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
- **ost2_course_discovery_sort** — sort the `/courses` discovery catalog (oldest start first; +title tie-break on dev).
- **ost2_discussions_mfe_fork** — repoint the discussions MFE to the XenoKovah fork (default Oldest-first sort, etc.).
- **ost2_forum_profile_links** — make forum `/u/<name>` author links resolve to the profile MFE (`PROFILE_MFE_BASE`).
- **ost2_mfe_media_proxy** — proxy LMS content paths (`/media`, `/asset-v1`) from the MFE host to the LMS so Discussions-MFE forum images resolve.
- **ost2_courses_hide_completed** — hide already-completed courses from the `/courses` listing.
- **GoogleAnalytics4Plugin** — inject the GA4 tag id on the LMS + MFEs.
- **ost2_student_grade_lookup** — staff-only `/admin/student-grade-lookup/` page: look up a learner by username/email/ID and list all enrollments + live grade % + certificate status (incl. certificate-exception flag); sortable columns; linked from the admin index.
- **ost2_communications_mfe_fork** — repoint the communications (bulk email) MFE to the XenoKovah fork, which adds the "Don't send to" → "Students who completed the class." checkbox and a switch under the "Body" heading that turns the rich text editor into a plain textarea. The checkbox requires the edx-platform branch `teak3_12_bulk_email_exclude_completed`, which teaches the LMS the `exclude_completed` target; without it the LMS rejects the send with a 400. The plaintext switch is frontend-only. Changes an MFE image, so it needs an MFE rebuild, not just `tutor config save`.
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
a restart.

## Implementation notes

- Tutor **Jinja-renders** `ENV_PATCHES` strings, so any Python injected as a patch
  string must avoid `{{`, `{%`, `{#` (empty `{}` and lone `}}` are fine). Prefer
  `%`-formatting over f-strings and inline `style=` attributes over CSS blocks.
- The rendered `.../settings/lms/production.py` is bind-mounted **read-only**;
  syntax-check it with the built-in `compile(open(p).read(), p, 'exec')` (not
  `py_compile`, which tries to write a `.pyc`).
