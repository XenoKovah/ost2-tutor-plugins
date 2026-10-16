#!/usr/bin/env bash
# Grant a manual (rule-less) Gamma badge to a list of LMS usernames, the way staff grant it in
# Gamma's admin: Badge.award_to_user (duplicate-safe; pays Badge.points once; silent: no toast), under each
# learner's row lock (the lock the live event signal takes). Every username is checked against
# the LMS first and passed on with the LMS's own spelling. PARALLEL=N splits the list into N
# shards granted side by side (learners are disjoint, so the row locks never contend).
#
# Usage: CONFIRM_HOST=<LMS_HOST> [DRY_RUN=1] [PARALLEL=1] grant_manual_badge.sh "<exact badge title>" usernames.txt
#   usernames.txt: one LMS username per line; blank lines and lines starting with # are ignored.
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
TITLE=${1:?usage: $0 "<badge title>" usernames.txt}
LIST=${2:?usage: $0 "<badge title>" usernames.txt}
PARALLEL=${PARALLEL:-1}
# SKIP_POINTS_REFRESH=1: don't re-check the points badges after each grant (gamma 6188bc9+); for bulk
# runs that end with run_points_refresh.sh. Default: re-check, so a one-off grant leaves the rings right.
SKIP_REFRESH=$([ "${SKIP_POINTS_REFRESH:-0}" = 1 ] && echo true || echo false)
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to grant on this box"; exit 1; }
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N --default-character-set=utf8mb4 "$@" </dev/null; }
RGG=tutor_local-rgg-1
W=$(mktemp -d)
trap 'rm -rf "$W"; docker exec "$RGG" sh -c "rm -f /tmp/grant-found-*.txt" >/dev/null 2>&1 || true' EXIT

grep -vE '^\s*(#|$)' "$LIST" | sed 's/\s*$//' | sort -u > "$W/requested.txt"
requested=$(wc -l < "$W/requested.txt")
IN=$(sed "s/'/''/g; s/.*/'&'/" "$W/requested.txt" | paste -sd,)
sql -e "SELECT username FROM openedx.auth_user WHERE username IN ($IN)" | sort -u > "$W/found.txt"
# Usernames are unique case-insensitively in the LMS: compare lower-cased.
missing=$(comm -23 <(tr '[:upper:]' '[:lower:]' < "$W/requested.txt" | sort -u) \
                   <(tr '[:upper:]' '[:lower:]' < "$W/found.txt" | sort -u) || true)
echo "requested $requested, found in LMS $(wc -l < "$W/found.txt")"
[ -n "$missing" ] && { echo "NOT in the LMS (skipped):"; echo "$missing" | sed 's/^/  /'; }

awk -v n="$PARALLEL" -v d="$W" '{ print > (d "/found-" (NR % n) ".txt") }' "$W/found.txt"
for k in $(seq 0 $((PARALLEL - 1))); do
  [ -f "$W/found-$k.txt" ] || : > "$W/found-$k.txt"
  docker cp "$W/found-$k.txt" "$RGG:/tmp/grant-found-$k.txt"
done
for k in $(seq 0 $((PARALLEL - 1))); do
  docker exec -e CELERY_RESULT_BACKEND=disabled -e TITLE="$TITLE" -e DRY_RUN="${DRY_RUN:-0}" -e SHARD="$k" -e RGG_SKIP_POINTS_REFRESH="$SKIP_REFRESH" "$RGG" sh -c 'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell -c "
import os
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import F
from achievements.models import Achievement
from badges.exceptions import BadgeExclusionError
from badges.models import Badge
from users.models import GammaUser
badge_ct = ContentType.objects.get_for_model(Badge)
dry = os.environ[\"DRY_RUN\"] == \"1\"
matches = list(Badge.objects.filter(title=os.environ[\"TITLE\"]))
if len(matches) != 1:
    raise SystemExit(\"need exactly one badge titled %r, found %d\" % (os.environ[\"TITLE\"], len(matches)))
badge = matches[0]
if badge.rules.exists():
    raise SystemExit(\"badge %r has rules: not a manual badge\" % badge.title)
granted, already, excluded = [], [], []
for line in open(\"/tmp/grant-found-%s.txt\" % os.environ[\"SHARD\"]):
    uid = line.rstrip(\"\n\")
    if not uid:
        continue
    with transaction.atomic():
        user = GammaUser.ensure_gamma_user_is_created(user_uid=uid) if not dry else GammaUser.objects.filter(user_uid=uid).first()
        if user is None:
            granted.append(uid)   # dry run: would be created and granted
            continue
        user = GammaUser.objects.select_for_update().get(pk=user.pk)
        if dry:
            (already if badge.has_achievement(user) else granted).append(uid)
            continue
        try:
            if badge.award_to_user(user):
                granted.append(uid)
                # Backfill grants are silent (decided 2026-10-02): no badge-earned toast.
                Achievement.objects.filter(user=user, object_id=badge.id, content_type=badge_ct,
                                           notification_seen_at__isnull=True).update(notification_seen_at=F(\"completed_at\"))
            else:
                already.append(uid)
        except BadgeExclusionError:
            excluded.append(uid)
print(\"SHARD|%s|%d|%d|%d|%d|%d|%s\" % (badge.title, badge.id, badge.points, len(granted), len(already), len(excluded), \",\".join(excluded)))
"' > "$W/shard-$k.out" 2>&1 &
done
wait
if grep -qE "Traceback|Error|SystemExit|need exactly|has rules" "$W"/shard-*.out || [ "$(cat "$W"/shard-*.out | grep -c '^SHARD|')" != "$PARALLEL" ]; then
  cat "$W"/shard-*.out; echo "a shard failed"; exit 1
fi
cat "$W"/shard-*.out | grep '^SHARD|' | awk -F'|' -v dry="${DRY_RUN:-0}" '
  { t=$2; id=$3; pts=$4; g+=$5; a+=$6; x+=$7; if ($8 != "") xs = xs (xs ? "," : "") $8 }
  END { printf "badge '\''%s'\'' (id %d, %d points): %s %d, already held %d, refused by excluded_categories %d\n", t, id, pts, (dry == "1" ? "would grant" : "granted"), g, a, x
        if (xs != "") print "  excluded: " xs }'
