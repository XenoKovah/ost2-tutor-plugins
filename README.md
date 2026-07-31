# ost2-tutor-plugins

Standalone [Tutor](https://docs.tutor.edly.io/) plugins for the OpenSecurityTraining2
Open edX deployments — **dev** (`dev.ost2.fyi`), **p** (`p.ost2.fyi`, live prod),
and **beta** (`beta.ost2.fyi`).

These are **single-file Tutor v1 plugins** (each registers its hooks directly via
`tutor.hooks`). They are dropped into the Tutor plugins root
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
each box is exactly `common` (9) plus that box's directory — dev=15, p=14, beta=15,
matching the live `tutor plugins list` on each. Contents were captured verbatim from
the running boxes (`~/.local/share/tutor-plugins/`) on 2026-07-31; committed files are
md5-identical to what is deployed.

## Enablement matrix

| Plugin | dev | p | beta | Source |
|---|:--:|:--:|:--:|---|
| disable_studio_course_pagination | ● | ● | ● | `common/` |
| ost2_authn_mfe_fork | ● | ● | ● | `common/` |
| ost2_authoring_mfe_fork | ● | ● | ● | `common/` |
| ost2_disable_survey_report | ● | ● | ● | `common/` |
| ost2_email_ratelimit | ● | ● | ● | `common/` |
| ost2_forum_sort_fix | ● | ● | ● | `common/` |
| ost2_handouts | ● | ● | ● | `common/` |
| password_policy | ● | ● | ● | `common/` |
| registration_custom_fields | ● | ● | ● | `common/` |
| ost2_course_discovery_sort | ● | ● | ● | per-box — **differs** |
| ost2_discussions_mfe_fork | ● | ● | ● | per-box — host |
| ost2_forum_profile_links | ● | ● | ● | per-box — host |
| ost2_mfe_media_proxy | ● | ● | ● | per-box — host (dev=p) |
| ost2_courses_hide_completed | ● | — | ● | `dev/`, `beta/` |
| GoogleAnalytics4Plugin | ○ | ● | ● | per-box — GA id |
| ost2_student_grade_lookup | ● | — | — | `dev/` (prototype) |

● enabled · ○ present but **disabled** · — not installed

### Per-box differences

- **GoogleAnalytics4Plugin** — GA4 measurement ID differs: p = `G-2J9NKZGFKK`,
  beta = `G-GL3SQZS9CB`. **Disabled on dev** ("GA dark" since the Teak cutover); dev's
  disabled copy is not captured here.
- **ost2_forum_profile_links**, **ost2_discussions_mfe_fork**, **ost2_mfe_media_proxy**
  — differ only by the baked-in MFE host (`apps.dev|p|beta.ost2.fyi`). dev's and p's
  `ost2_mfe_media_proxy.py` are byte-identical; beta's differs only in its docstring.
- **ost2_course_discovery_sort** — **dev runs a newer variant** (sorts the `/courses`
  catalog by course start date, then course title `content.display_name` as tie-break);
  p and beta run the older **start-only** variant. Converging p/beta to dev's version is
  purely a content update (re-index not required — Meilisearch re-sorts on
  `sortableAttributes` change).

## What each plugin does

- **disable_studio_course_pagination** — Studio home lists all courses on one page (pre-Teak behavior).
- **ost2_authn_mfe_fork** — repoint the authn MFE to the XenoKovah fork (18-char password policy branch).
- **ost2_authoring_mfe_fork** — repoint the authoring (Studio) MFE to the XenoKovah fork.
- **ost2_disable_survey_report** — disable the LMS Django-admin "Open edX Data Sharing Initiative" banner (`SURVEY_REPORT_ENABLE=False`).
- **ost2_email_ratelimit** — `RateLimitedEmailBackend` to stay under Gmail send throttles.
- **ost2_forum_sort_fix** — force-install the forum fork with the MySQL child-comment sort fix.
- **ost2_handouts** — course handouts fix.
- **password_policy** — `AUTH_PASSWORD_VALIDATORS` (min length 18, no complexity, max 128) + longer generated passwords so OAuth signups don't 400.
- **registration_custom_fields** — custom registration fields (only AGE required).
- **ost2_course_discovery_sort** — sort the `/courses` discovery catalog (oldest start first; +title tie-break on dev).
- **ost2_discussions_mfe_fork** — repoint the discussions MFE to the XenoKovah fork (default Oldest-first sort, etc.).
- **ost2_forum_profile_links** — make forum `/u/<name>` author links resolve to the profile MFE (`PROFILE_MFE_BASE`).
- **ost2_mfe_media_proxy** — proxy LMS content paths (`/media`, `/asset-v1`) from the MFE host to the LMS so Discussions-MFE forum images resolve.
- **ost2_courses_hide_completed** — hide already-completed courses from the `/courses` listing.
- **GoogleAnalytics4Plugin** — inject the GA4 tag id on the LMS + MFEs.
- **ost2_student_grade_lookup** — staff-only `/admin/student-grade-lookup/` page: look up a learner by username/email/ID and list all enrollments + live grade % + certificate status (incl. certificate-exception flag); sortable columns; linked from the admin index.

## Deploy (per box)

Copy `common/` + the box's directory into the Tutor plugins root, enable each, then
apply:

```
root=$(~/tutor-venv/bin/tutor plugins printroot)
cp common/*.py dev/*.py "$root"/            # or p/*.py , beta/*.py
for f in "$root"/*.py; do ~/tutor-venv/bin/tutor plugins enable "$(basename "${f%.py}")"; done
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
