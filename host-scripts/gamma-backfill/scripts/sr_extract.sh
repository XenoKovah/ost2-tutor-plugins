#!/usr/bin/env bash
# Read-only extraction for the Speed-runner! backfill (speed_runner.py): SELECT only.
# LMS: every "Mark as complete" click (courseware_studentmodule 'done', state done:true, created = click
# time), videos watched >= 60 s, graded problems, per-course block counts, accounts (for the exclusions),
# course team, downloadable certificates. Gamma: anti-gaming Rule 39, the Course Completion tier badges
# and the tiers learners hold.
# Usage: bash sr_extract.sh OUTDIR
set -Eeuo pipefail
OUT=${1:?usage: $0 OUTDIR}; mkdir -p "$OUT"
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
q(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --batch --default-character-set=utf8mb4 "$@"; }
t0=$(date +%s)
q openedx > "$OUT/done.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT student_id, course_id, module_id, created FROM courseware_studentmodule
WHERE module_type = 'done' AND state LIKE '%"done": true%';
SQL
q openedx > "$OUT/videos.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT student_id, course_id, module_id, created FROM courseware_studentmodule
WHERE module_type = 'video' AND state LIKE '%saved_video_position%'
  AND SUBSTRING_INDEX(SUBSTRING_INDEX(state, '"saved_video_position": "', -1), '"', 1) >= '00:01:00';
SQL
q openedx > "$OUT/problems.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT student_id, course_id, module_id, created FROM courseware_studentmodule
WHERE module_type = 'problem' AND grade IS NOT NULL;
SQL
q openedx > "$OUT/blocks.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT course_id, COUNT(DISTINCT CASE WHEN module_type = 'done' THEN module_id END) done_units,
       COUNT(DISTINCT CASE WHEN module_type = 'video' THEN module_id END) video_blocks,
       COUNT(DISTINCT CASE WHEN module_type = 'problem' THEN module_id END) problem_blocks
FROM courseware_studentmodule WHERE module_type IN ('done', 'video', 'problem') GROUP BY course_id;
SQL
q openedx > "$OUT/users.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT id, username, is_active, is_staff, is_superuser FROM auth_user
WHERE id IN (SELECT DISTINCT student_id FROM courseware_studentmodule WHERE module_type = 'done');
SQL
q openedx > "$OUT/course_team.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT DISTINCT user_id FROM student_courseaccessrole WHERE role IN ('staff', 'instructor');
SQL
q openedx > "$OUT/certificates.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT user_id, course_id, created_date AS created FROM certificates_generatedcertificate WHERE status = 'downloadable';
SQL
q rgg > "$OUT/rule39.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT r.action, r.filters FROM rules_rule r JOIN events_eventconfiguration c ON c.id = r.event_configuration_id
JOIN events_eventtype t ON t.id = c.event_type_id WHERE t.name = 'rgg_rushed_completion';
SQL
q rgg > "$OUT/classes.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT b.id AS badge_id, b.title, b.category, b.points, r.filters FROM badges_badge b
JOIN badges_badge_rules br ON br.badge_id = b.id JOIN rules_rule r ON r.id = br.rule_id
WHERE b.category LIKE 'Course Completion /%';
SQL
q rgg > "$OUT/tiers_held.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT u.user_uid, b.id AS badge_id, b.title, b.category, a.completion_points_paid
FROM achievements_achievement a JOIN users_gammauser u ON u.id = a.user_id JOIN badges_badge b ON b.id = a.object_id
JOIN django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
WHERE b.category LIKE 'Course Completion /%' AND a.completed_at IS NOT NULL;
SQL
echo "extracted in $(( $(date +%s) - t0 ))s"; wc -l "$OUT"/*.tsv | sed "s#$OUT/##"
