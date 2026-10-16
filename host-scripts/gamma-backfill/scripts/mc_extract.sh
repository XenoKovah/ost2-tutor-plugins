#!/usr/bin/env bash
# Read-only extraction for the Multi-class Accomplishments backfill (multi_class.py): SELECT only.
# LMS: downloadable certificates with the account flags, and the course ids that exist. Gamma: the
# active Multi-class badges with their rules, any achievements on them, rules depending on them,
# certificate events (reported against the LMS), the Course Completion tiers held (cross-check),
# and the site points total (the apply guard).
# Usage: bash mc_extract.sh OUTDIR
set -Eeuo pipefail
OUT=${1:?usage: $0 OUTDIR}; mkdir -p "$OUT"
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
q(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --batch --default-character-set=utf8mb4 "$@"; }
CATEGORY="Multi-class Accomplishments"
q openedx > "$OUT/certificates.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT c.user_id, u.username, u.is_active, c.course_id, c.created_date AS created
FROM certificates_generatedcertificate c JOIN auth_user u ON u.id = c.user_id WHERE c.status = 'downloadable';
SQL
q openedx > "$OUT/course_overviews.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT id FROM course_overviews_courseoverview;
SQL
q rgg > "$OUT/badges.tsv" <<SQL
SET SESSION TRANSACTION READ ONLY;
SELECT b.id AS badge_id, b.title, b.points, b.excluded_categories, r.id AS rule_id, t.name AS event, r.action, r.filters
FROM badges_badge b JOIN badges_badge_rules br ON br.badge_id = b.id JOIN rules_rule r ON r.id = br.rule_id
LEFT JOIN events_eventconfiguration c ON c.id = r.event_configuration_id LEFT JOIN events_eventtype t ON t.id = c.event_type_id
WHERE b.category = '$CATEGORY' AND b.is_active = 1 ORDER BY b.id, r.id;
SQL
q rgg > "$OUT/achievements.tsv" <<SQL
SET SESSION TRANSACTION READ ONLY;
SELECT u.user_uid, a.object_id AS badge_id, a.completed_at IS NOT NULL AS completed,
       (SELECT COUNT(*) FROM achievements_achievementrule ar WHERE ar.achievement_id = a.id) AS rules,
       (SELECT COUNT(*) FROM achievements_achievementrule ar WHERE ar.achievement_id = a.id AND ar.status = 'completed') AS rules_completed
FROM achievements_achievement a JOIN users_gammauser u ON u.id = a.user_id
JOIN django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
WHERE a.object_id IN (SELECT id FROM badges_badge WHERE category = '$CATEGORY');
SQL
q rgg > "$OUT/dependents.tsv" <<SQL
SET SESSION TRANSACTION READ ONLY;
SELECT r.id AS rule_id, JSON_UNQUOTE(JSON_EXTRACT(r.action, '$.rgg_achievement_obtained.dependent_object_id')) AS dependent_object_id
FROM rules_rule r WHERE JSON_EXTRACT(r.action, '$.rgg_achievement_obtained.dependent_object_id') IN (SELECT id FROM badges_badge WHERE category = '$CATEGORY');
SQL
q rgg > "$OUT/cert_events.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT e.user_uid, e.course_id FROM events_event e JOIN events_eventconfiguration c ON c.id = e.configuration_id
JOIN events_eventtype t ON t.id = c.event_type_id WHERE t.name = 'edx_certificate_created';
SQL
q rgg > "$OUT/cc_classes.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT DISTINCT b.title, r.filters FROM badges_badge b JOIN badges_badge_rules br ON br.badge_id = b.id
JOIN rules_rule r ON r.id = br.rule_id WHERE b.category LIKE 'Course Completion /%';
SQL
q rgg > "$OUT/cc_tiers_held.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT u.user_uid, b.title FROM achievements_achievement a JOIN users_gammauser u ON u.id = a.user_id
JOIN badges_badge b ON b.id = a.object_id
JOIN django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
WHERE b.category LIKE 'Course Completion /%' AND a.completed_at IS NOT NULL;
SQL
q rgg > "$OUT/gamma_users.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT user_uid FROM users_gammauser;
SQL
q rgg > "$OUT/site_total.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT COALESCE(SUM(points), 0) AS total FROM users_gammauser;
SQL
wc -l "$OUT"/*.tsv | sed "s#$OUT/##"
