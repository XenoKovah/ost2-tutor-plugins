#!/usr/bin/env bash
# Instructors' real names on their LMS profiles (decided 2026-10-02), from manual/instructor_names.tsv: the
# About-page name of each instructor whose profile shows a handle or a short form (ip_names.py, through the
# account API, so the old name stays in the profile's name history). DRY RUN by default; APPLY=1 writes, then,
# like the photo step, silences for 3 minutes any badge toast gamma grants these learners (backfills are silent).
# Usage: CONFIRM_HOST=<LMS_HOST> [APPLY=1] bash ~/gamma-backfill/run_instructor_names.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
cd "$(dirname "$0")"
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
LMS=tutor_local-lms-1
cleanup(){ docker exec "$LMS" rm -f /tmp/ip_names.py /tmp/instructor_names.tsv >/dev/null 2>&1 || true; }
trap cleanup EXIT
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N rgg -e "$1" </dev/null; }
T0=$(sql "SELECT UTC_TIMESTAMP(6)")
docker cp manual/instructor_names.tsv "$LMS:/tmp/instructor_names.tsv"
docker cp ip_names.py "$LMS:/tmp/ip_names.py"
out=$(docker exec -e APPLY="${APPLY:-0}" "$LMS" sh -c 'cd /openedx/edx-platform && python manage.py lms shell < /tmp/ip_names.py 2>/dev/null')
echo "$out"
[ "${APPLY:-0}" = 1 ] || { echo "DRY RUN: nothing written"; exit 0; }
set=$(echo "$out" | awk '$1 == "SET" {sub(":", "", $2); print $2}')
[ -n "$set" ] || { echo "nothing to set"; exit 0; }
USERS=$(echo "$set" | sed "s/.*/'&'/" | paste -sd,)
silenced=0
for i in $(seq 1 18); do
  sleep 10
  n=$(sql "UPDATE achievements_achievement a JOIN users_gammauser u ON u.id = a.user_id
           SET a.notification_seen_at = a.completed_at
           WHERE u.user_uid IN ($USERS) AND a.completed_at >= '$T0' AND a.notification_seen_at IS NULL; SELECT ROW_COUNT()")
  silenced=$((silenced + n))
done
echo "   silenced $silenced toast(s)"
echo "INSTRUCTOR NAMES DONE"
