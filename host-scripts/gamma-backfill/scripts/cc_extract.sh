#!/usr/bin/env bash
# Read-only extraction for the Course Completion tier regrade (cc_regrade.py). SELECT only.
# Usage: [GAMMA_ONLY=1] bash cc_extract.sh OUTDIR [usernames.txt]
#   GAMMA_ONLY=1 refreshes only the gamma files (holdings, site total, tiers), e.g. after an apply,
#   which changes gamma but never the LMS.
set -Eeuo pipefail
OUT=${1:?usage: $0 OUTDIR}; mkdir -p "$OUT"
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
q(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --batch --default-character-set=utf8mb4 "$@"; }
if [ "${GAMMA_ONLY:-0}" != 1 ]; then
q openedx > "$OUT/certs.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT gc.id cert_id, gc.user_id, au.username, LOWER(au.email) email, au.is_active, gc.course_id,
       gc.created_date, gc.modified_date
FROM certificates_generatedcertificate gc JOIN auth_user au ON au.id = gc.user_id
WHERE gc.status = 'downloadable';
SQL
q openedx > "$OUT/allowlist.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT al.user_id, al.course_id, REPLACE(REPLACE(REPLACE(COALESCE(al.notes, ''), '\t', ' '), '\r', ' '), '\n', ' ') notes, al.created
FROM certificates_certificateallowlist al WHERE al.allowlist = 1;
SQL
q openedx > "$OUT/clicks.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT sm.student_id user_id, sm.course_id, sm.created
FROM courseware_studentmodule sm
WHERE sm.module_type = 'done' AND sm.state LIKE '%"done": true%'
  AND sm.student_id IN (SELECT user_id FROM certificates_generatedcertificate WHERE status = 'downloadable');
SQL
if [ -n "${2:-}" ]; then
  IN=$(sed "s/'/''/g; s/.*/'&'/" "$2" | paste -sd,)
  q openedx -e "SET SESSION TRANSACTION READ ONLY; SELECT id, username, LOWER(email) email, is_active FROM auth_user WHERE username IN ($IN)" > "$OUT/candidate_users.tsv" </dev/null
fi
fi
q rgg > "$OUT/tiers.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT b.id badge_id, b.title, b.category, b.points, b.is_active, r.id rule_id, r.filters
FROM badges_badge b JOIN badges_badge_rules br ON br.badge_id = b.id JOIN rules_rule r ON r.id = br.rule_id
WHERE b.category LIKE 'Course Completion /%';
SQL
q rgg > "$OUT/holdings.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT u.user_uid, a.object_id badge_id, a.completed_at, a.completion_points_paid, a.notification_seen_at
FROM achievements_achievement a JOIN users_gammauser u ON u.id = a.user_id
JOIN badges_badge b ON b.id = a.object_id
JOIN django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
WHERE b.category LIKE 'Course Completion /%';
SQL
q rgg > "$OUT/site_points.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT COUNT(*) users, SUM(points) total_points, UTC_TIMESTAMP() at_utc FROM users_gammauser;
SQL
q rgg > "$OUT/shared_rules.tsv" <<'SQL'
SET SESSION TRANSACTION READ ONLY;
SELECT r.id rule_id, GROUP_CONCAT(DISTINCT b.category SEPARATOR ' | ') categories
FROM rules_rule r JOIN badges_badge_rules br ON br.rule_id = r.id JOIN badges_badge b ON b.id = br.badge_id
GROUP BY r.id HAVING SUM(b.category LIKE 'Course Completion /%') > 0 AND SUM(b.category NOT LIKE 'Course Completion /%') > 0;
SQL
wc -l "$OUT"/*.tsv | sed "s#$OUT/##"
