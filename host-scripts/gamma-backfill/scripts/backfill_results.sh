#!/usr/bin/env bash
# Print a stable summary of Gamma's backfill-relevant state, for comparing runs:
# per "Using The Site" badge: holders vs distinct learners with a qualifying event
# (they must be equal for these count:1 badges), plus totals.
# Usage: backfill_results.sh [CATEGORY]   (default "Using The Site")
set -Eeuo pipefail
CATEGORY=${1:-Using The Site}
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot rgg -e "
SELECT b.id AS badge, LEFT(b.title, 28) AS title, t.name AS event_type,
       (SELECT COUNT(*) FROM achievements_achievement a JOIN django_content_type ct ON ct.id = a.content_type_id
         AND ct.app_label = 'badges' AND ct.model = 'badge'
         WHERE a.object_id = b.id AND a.completed_at IS NOT NULL) AS holders,
       (SELECT COUNT(DISTINCT e.user_uid) FROM events_event e WHERE e.configuration_id = r.event_configuration_id) AS learners_with_event,
       (SELECT COUNT(*) FROM events_event e WHERE e.configuration_id = r.event_configuration_id) AS events
FROM badges_badge b
JOIN badges_badge_rules br ON br.badge_id = b.id
JOIN rules_rule r ON r.id = br.rule_id
JOIN events_eventconfiguration c ON c.id = r.event_configuration_id
JOIN events_eventtype t ON t.id = c.event_type_id
WHERE b.category = '$CATEGORY' AND b.is_active = 1
ORDER BY b.id;
SELECT (SELECT COUNT(*) FROM events_event) AS all_events,
       (SELECT COUNT(*) FROM achievements_achievement WHERE completed_at IS NOT NULL) AS completed_achievements,
       (SELECT COUNT(*) FROM users_gammauser) AS gamma_users,
       (SELECT SUM(points) FROM users_gammauser) AS total_points;" </dev/null
