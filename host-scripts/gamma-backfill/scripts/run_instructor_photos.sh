#!/usr/bin/env bash
# Instructor profile pictures from the course About pages (decided 2026-10-02), so leaderboards show the
# instructors' faces: for each row of manual/instructor_photos.tsv whose account has no profile picture,
# set it from that course asset, an approved photo kept in manual/instructor_photos/, or the same person's
# other account (ip_apply.py in the LMS shell, the profile-image upload view's own steps;
# photos over the 1 MB upload limit are shrunk to 1000 px first). Never replaces an existing picture;
# external photos are skipped. The plan was built by ip_extract.py (read-only) from every About page's
# "Course Staff" section and checked by hand (see BACKFILLS.md §9).
# DRY RUN by default; APPLY=1 writes, then checks that every new picture is served and silences the live
# badge toasts ("Set Photo") gamma grants those learners over the next 3 minutes.
# Usage: CONFIRM_HOST=<LMS_HOST> [APPLY=1] bash ~/gamma-backfill/run_instructor_photos.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
cd "$(dirname "$0")"
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
LMS=tutor_local-lms-1
cleanup(){ docker exec "$LMS" rm -rf /tmp/ip_apply.py /tmp/instructor_photos.tsv /tmp/ip_photos >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker cp manual/instructor_photos.tsv "$LMS:/tmp/instructor_photos.tsv"
docker exec "$LMS" mkdir -p /tmp/ip_photos
docker cp manual/instructor_photos/. "$LMS:/tmp/ip_photos/"
docker cp ip_apply.py "$LMS:/tmp/ip_apply.py"
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N rgg -e "$1" </dev/null; }
T0=$(sql "SELECT UTC_TIMESTAMP(6)")
out=$(docker exec -e APPLY="${APPLY:-0}" "$LMS" sh -c 'cd /openedx/edx-platform && python manage.py lms shell < /tmp/ip_apply.py 2>/dev/null')
echo "$out"
[ "${APPLY:-0}" = 1 ] || { echo "DRY RUN: nothing written"; exit 0; }
set=$(echo "$out" | awk '$1 == "SET" {print $2}')
[ -n "$set" ] || { echo "nothing to set"; exit 0; }
echo "== check that the new pictures are served"
IN=$(echo "$set" | sed 's/.*/"&"/' | paste -sd,)
docker exec -i "$LMS" sh -c 'cd /openedx/edx-platform && python manage.py lms shell 2>/dev/null' <<PY | while read -r user url; do
from django.contrib.auth.models import User
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_urls_for_user
for u in User.objects.filter(username__in=[$IN]):
    print(u.username, get_profile_image_urls_for_user(u)["full"])
PY
  case "$url" in http*) full=$url;; *) full=https://$HOST$url;; esac
  code=$(curl -s -o /dev/null -w "%{http_code}" "$full")
  echo "   $user $code ${url%%\?*}"
  [ "$code" = 200 ] || { echo "picture not served for $user"; exit 1; }
done
echo "== silence the badge toasts the new pictures trigger"
# A new picture fires the LMS profile event, so gamma grants "Set Photo" live a little later (LMS ->
# bridge -> gamma). Backfill grants are silent (decided 2026-10-02): for 3 minutes, mark any badge these
# learners are granted from now on as already seen.
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
echo "INSTRUCTOR PHOTOS DONE"
