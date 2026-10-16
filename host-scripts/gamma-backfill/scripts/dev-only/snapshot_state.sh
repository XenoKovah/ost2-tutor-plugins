#!/usr/bin/env bash
# dev only: dump gamma's learner state on stable keys (no auto ids, no run-time timestamps), so a real
# run and a clean-state re-validation can be compared with diff:
#   achievements.tsv  user_uid, badge id, completion_points_paid      (every completed badge)
#   rings.tsv         user_uid, badge id, ring count/goal             (in-progress points-badge rings)
#   points.tsv        user_uid, points
#   boards.tsv        user_uid, course_id, points                     (per-course boards)
# Usage: bash snapshot_state.sh OUTDIR
set -Eeuo pipefail
OUT=${1:?usage: $0 OUTDIR}; mkdir -p "$OUT"
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
q(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N --batch rgg; }
q > "$OUT/achievements.tsv" <<'SQL'
SELECT u.user_uid, a.object_id, COALESCE(a.completion_points_paid, '') FROM achievements_achievement a
JOIN users_gammauser u ON u.id = a.user_id JOIN django_content_type ct ON ct.id = a.content_type_id
WHERE ct.app_label = 'badges' AND ct.model = 'badge' AND a.completed_at IS NOT NULL ORDER BY 1, 2, 3;
SQL
q > "$OUT/rings.tsv" <<'SQL'
SELECT u.user_uid, a.object_id,
       JSON_EXTRACT(ar.dependencies, '$.events.rgg_points_distribution.count'),
       JSON_EXTRACT(ar.dependencies, '$.events.rgg_points_distribution.goal')
FROM achievements_achievement a JOIN users_gammauser u ON u.id = a.user_id
JOIN achievements_achievementrule ar ON ar.achievement_id = a.id
JOIN rules_rule r ON r.id = ar.rule_id JOIN events_eventconfiguration c ON c.id = r.event_configuration_id
JOIN events_eventtype t ON t.id = c.event_type_id
WHERE t.name = 'rgg_points_distribution' AND a.completed_at IS NULL ORDER BY 1, 2;
SQL
q > "$OUT/points.tsv" <<'SQL'
SELECT user_uid, points FROM users_gammauser ORDER BY 1;
SQL
q > "$OUT/boards.tsv" <<'SQL'
SELECT u.user_uid, b.course_id, b.points FROM users_gammausercoursepoints b JOIN users_gammauser u ON u.id = b.gamma_user_id ORDER BY 1, 2;
SQL
wc -l "$OUT"/*.tsv | sed "s#$OUT/##"
