#!/usr/bin/env bash
# One-time silence of badge-earned toasts when p goes back up after its backfill (decided 2026-10-02: no toasts
# the first time after launch for anything earned before it; toasts as usual for actions after the system is
# back up). Every achievement completed at or before CUTOFF that hasn't been shown yet is marked as shown
# (notification_seen_at = completed_at, as the backfills do). Achievements completed after CUTOFF toast as usual.
# Inactive badges are included: the toast API skips them, but a pending one would pop up if its badge were
# re-activated. Run once, as the last step before p goes back up and the student UI is turned on.
# DRY RUN by default: prints what CUTOFF would silence. CUTOFF defaults to now, as 'YYYY-MM-DD HH:MM:SS' UTC.
# TARGET_DB overrides the database (for tests).
# Usage: CONFIRM_HOST=<LMS_HOST> [CUTOFF='YYYY-MM-DD HH:MM:SS'] [APPLY=1] bash ~/gamma-backfill/silence_prelaunch_toasts.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
DB=${TARGET_DB:-rgg}
NOW=$(date -u '+%Y-%m-%d %H:%M:%S')
CUTOFF=${CUTOFF:-$NOW}
[[ $CUTOFF =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}$ ]] || { echo "refusing: CUTOFF must look like 'YYYY-MM-DD HH:MM:SS' (UTC)"; exit 1; }
[[ ! $CUTOFF > $NOW ]] || { echo "refusing: CUTOFF $CUTOFF is in the future (now $NOW UTC)"; exit 1; }
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
q(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --batch --default-character-set=utf8mb4 "$DB" "$@" </dev/null; }
PENDING="a.completed_at IS NOT NULL AND a.notification_seen_at IS NULL"
BEFORE="$PENDING AND a.completed_at <= '$CUTOFF'"
BADGE="a.content_type_id = (SELECT id FROM django_content_type WHERE app_label = 'badges' AND model = 'badge')"

echo "cutoff: $CUTOFF UTC (host $HOST, database $DB)"
echo "pending toasts completed at or before the cutoff: $(q -N -e "SELECT CONCAT(COUNT(*), ' achievements, ', COUNT(DISTINCT a.user_id),
  ' learners (first ', IFNULL(MIN(a.completed_at), '-'), ', last ', IFNULL(MAX(a.completed_at), '-'), ')')
  FROM achievements_achievement a WHERE $BEFORE")"
q --table -e "SELECT CASE WHEN NOT ($BADGE) THEN 'not a badge (no toast UI)' WHEN b.is_active = 1 THEN 'active badge: would toast'
                     ELSE 'inactive badge: would toast if re-activated' END AS kind, COUNT(*) AS achievements, COUNT(DISTINCT a.user_id) AS learners
  FROM achievements_achievement a LEFT JOIN badges_badge b ON b.id = a.object_id AND $BADGE
  WHERE $BEFORE GROUP BY kind ORDER BY kind"
q --table -e "SELECT a.title AS most_common, COUNT(*) AS achievements FROM achievements_achievement a WHERE $BEFORE
  GROUP BY a.title ORDER BY 2 DESC, 1 LIMIT 10"
after(){ q -N -e "SELECT COUNT(*) FROM achievements_achievement a WHERE $PENDING AND a.completed_at > '$CUTOFF'"; }
echo "pending toasts completed after the cutoff (left alone): $(after)"
[ "${APPLY:-0}" = 1 ] || { echo "DRY RUN: nothing written"; exit 0; }

silenced=$(q -N -e "UPDATE achievements_achievement a SET a.notification_seen_at = a.completed_at WHERE $BEFORE; SELECT ROW_COUNT()")
left=$(q -N -e "SELECT COUNT(*) FROM achievements_achievement a WHERE $BEFORE")
echo "silenced: $silenced"
echo "still pending at or before the cutoff: $left (expected 0)"
echo "pending after the cutoff (they toast as usual): $(after)"
[ "$left" = 0 ] || { echo "TOAST SILENCE CHECK FAILED"; exit 1; }
echo "TOAST SILENCE DONE"
