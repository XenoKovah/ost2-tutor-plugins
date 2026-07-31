# ost2-tutor-plugins

Standalone [Tutor](https://docs.tutor.edly.io/) plugins for the OpenSecurityTraining2
Open edX deployments (`dev.ost2.fyi`, `p.ost2.fyi`, `beta.ost2.fyi`).

These are **single-file Tutor v1 plugins** (each registers its hooks directly via
`tutor.hooks`). They are dropped into the Tutor plugins root
(`$(tutor plugins printroot)`, i.e. `~/.local/share/tutor-plugins/`) and enabled
per box. They are **settings-only** — the OpenedX settings dir is bind-mounted into
the containers, so applying a change needs only `tutor config save` + a service
restart, **no image rebuild**.

> Distinct from `tutor-contrib-rgg`, which is the pip-installed RGG *plugin package*
> (`tutorrgg/`). This repo holds the loose per-box `ost2_*.py` plugins that were
> previously untracked.

## Plugins

### `ost2_disable_survey_report.py`
Permanently disables the "Join the Open edX Data Sharing Initiative and shape the
future of learning" banner shown at the top of the LMS Django admin (`/admin`).
Sets `SURVEY_REPORT_ENABLE = False` (default `True`, `lms/envs/common.py`) via the
`openedx-common-settings` patch. The banner's "Dismiss" button is client-side only,
so this toggle is the only permanent off-switch.

Deployed on **dev, p, beta**.

### `ost2_student_grade_lookup.py`
Adds a **staff-only** page to the LMS Django admin — **OST2 tools → Student grade
lookup**, at `/admin/student-grade-lookup/` — that, given a username, email, or
numeric user ID, lists every course the learner is enrolled in with their **live
grade %**, pass/fail, and certificate status. No content-completion (OST2 does not
use block-completion). Features:

- Clickable column headers toggle ascending/descending sort (client-side).
- The learner's full name (`UserProfile.name`) is shown in the results header.
- The Certificate column flags certs granted by a **certificate exception**
  (`CertificateAllowlist`, i.e. regardless of grade) vs. earned by meeting the grade.
- Linked from the admin index + nav sidebar via an `AdminSite.get_app_list` injection.

Injected into the LMS via the `openedx-lms-production-settings` patch. Auth is
inherited from Django admin (`admin.site.admin_view` → active staff only).

Deployed on **dev** (prototype).

## Deploy (per box)

```
cp ost2_<name>.py "$(~/tutor-venv/bin/tutor plugins printroot)"/
~/tutor-venv/bin/tutor plugins enable ost2_<name>
~/tutor-venv/bin/tutor config save
~/tutor-venv/bin/tutor local restart lms cms
```

Then verify: the admin banner is gone, and/or `/admin/student-grade-lookup/`
returns `302` (redirect to the admin login) when unauthenticated.

## Implementation notes

- Tutor **Jinja-renders** `ENV_PATCHES` strings, so any Python injected as a patch
  string must avoid `{{`, `{%`, `{#` (empty `{}` and lone `}}` are fine). These
  plugins use `%`-formatting (no f-strings) and inline styles (no CSS blocks); sort
  arrows use `String.fromCharCode` to stay ASCII.
- The rendered `.../settings/lms/production.py` is bind-mounted **read-only**;
  syntax-check it with the built-in `compile(open(p).read(), p, 'exec')` (not
  `py_compile`, which tries to write a `.pyc`).
