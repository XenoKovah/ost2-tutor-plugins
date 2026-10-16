#!/usr/bin/env bash
# Re-evaluate every learner's "Points points points!" badges (lifetime points total >= N) against
# their current total, in $PARALLEL learner shards at once (points_refresh_shard.py: one live
# rgg_points_distribution event per learner, so rings and grants come out exactly as the live
# pipeline makes them; new grants are silent). Run it LAST: every other backfill pays points.
# Needs gamma points-rings-refresh or later (the shard refuses an older gamma).
# Before and after, prints per badge: holders, learners due but not holding, rings, negative rings,
# stale rings (count != max(total, 0)) and learners with no ring. After a real run all of those
# problem counts must be 0, else it exits 1 (re-running is safe and only touches what is stale).
# Points-badge grants made earlier by gamma's own refresh (any backfill that changes points triggers one)
# are not silent; the after-check marks every pending points-badge toast seen, like the grants made here.
# REVOKE_BELOW=1 (default): afterwards a points badge is held exactly when the final total reaches its
# threshold. Holders below it (they crossed it before a later backfill step docked points, e.g. an
# Ignominious! penalty) lose it and get their ring back. REVOKE_BELOW=0 keeps live behavior, where a
# penalty never takes a points badge away. The badges are 0-point, so nobody's points change.
# Ends by scheduling one leaderboard rebuild.
#
# Usage: CONFIRM_HOST=<LMS_HOST> [PARALLEL=8] [DRY_RUN=1] [REVOKE_BELOW=1] run_points_refresh.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
RGG=tutor_local-rgg-1
BASE=$HOME/gamma-backfill
PARALLEL=${PARALLEL:-8}
DRY=${DRY_RUN:-0}
REVOKE_BELOW=${REVOKE_BELOW:-1}
CMD=/rgg/gamma/badges/management/commands/backfill_points_refresh_shard.py

# Not in the gamma image: install for the run, remove afterwards.
docker cp "$BASE/points_refresh_shard.py" "$RGG:$CMD"
trap 'docker exec "$RGG" rm -f "$CMD"' EXIT

check() {  # $1 = strict (1: exit 1 on any problem) ; $2 = 1 to schedule the leaderboard rebuild
  docker exec -e CELERY_RESULT_BACKEND=disabled -i -e STRICT="$1" -e REBUILD="${2:-0}" -e SILENCE="${2:-0}" -e REVOKE_BELOW="$REVOKE_BELOW" "$RGG" sh -lc 'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell' <<'PY'
import os
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from achievements.models import Achievement, AchievementRule
from badges.models import Badge
from users.models import GammaUser

PD = 'rgg_points_distribution'
ct = ContentType.objects.get_for_model(Badge)
goals = {}
for b in Badge.objects.filter(is_active=True, rules__event_configuration__event_type__name=PD).distinct():
    goals[b.id] = (b.title, int(b.rules.get().action[PD]['points']))
if os.environ['SILENCE'] == '1':
    from django.utils.timezone import now
    silenced = Achievement.objects.filter(content_type=ct, object_id__in=goals, completed_at__isnull=False,
                                          notification_seen_at__isnull=True).update(notification_seen_at=now())
    print('silenced %d pending points-badge toasts' % silenced)
points = dict(GammaUser.objects.values_list('id', 'points'))
print('learners %d (negative total %d, zero %d)' % (
    len(points), sum(p < 0 for p in points.values()), sum(p == 0 for p in points.values())))
problems = 0
for bid, (title, goal) in sorted(goals.items(), key=lambda kv: kv[1][1]):
    holders = set(Achievement.objects.filter(content_type=ct, object_id=bid, completed_at__isnull=False)
                  .values_list('user_id', flat=True))
    due_not_held = sum(1 for u, p in points.items() if p >= goal and u not in holders)
    rings = negative = stale = 0
    ringed = set()
    for uid, deps in AchievementRule.objects.filter(
            achievement__content_type=ct, achievement__object_id=bid, achievement__completed_at__isnull=True,
    ).values_list('achievement__user_id', 'dependencies'):
        rings += 1
        ringed.add(uid)
        count = (((deps or {}).get('events') or {}).get(PD) or {}).get('count')
        negative += count is not None and count < 0
        stale += count != max(points.get(uid, 0), 0)
    no_ring = len(set(points) - holders - ringed)
    held_below = sum(1 for u in holders if points.get(u, 0) < goal)
    problems += due_not_held + negative + stale + no_ring + (held_below if os.environ['REVOKE_BELOW'] == '1' else 0)
    print('  %-30s >=%-9d holders %6d  due-not-held %5d  held-below %5d  rings %6d  negative %5d  stale %5d  no-ring %5d' % (
        title[:30], goal, len(holders), due_not_held, held_below, rings, negative, stale, no_ring))
if os.environ['STRICT'] == '1':
    print('CHECK OK' if problems == 0 else 'CHECK FAILED: %d problems' % problems)
if os.environ['REBUILD'] == '1':
    from leaderboard.repository import ORMLeaderboardMemberDataRepository
    from leaderboard.usecases import ResetLeaderboardsInitializationStatusUseCase, ScheduleLeaderboardsInitializationUseCase
    ResetLeaderboardsInitializationStatusUseCase().execute()
    ScheduleLeaderboardsInitializationUseCase(ORMLeaderboardMemberDataRepository()).execute(
        settings.LEADERBOARD_INITIALIZATION_BATCH_SIZE)
    print('Leaderboard rebuild scheduled (rgg-worker will process it).')
PY
}

echo "=== before"
check 0
echo "=== refresh ($PARALLEL learner shards$([ "$DRY" = 1 ] && echo ', DRY RUN'))"
t0=$(date +%s)
out=$(mktemp)
export RGG PARALLEL DRYFLAG=$([ "$DRY" = 1 ] && echo --dry-run || true)
seq 0 $((PARALLEL - 1)) | xargs -P "$PARALLEL" -I{} sh -c \
  'docker exec -e CELERY_RESULT_BACKEND=disabled "$RGG" sh -lc "cd /rgg/gamma && /rgg/venv/bin/python manage.py backfill_points_refresh_shard --shard {}/$PARALLEL $DRYFLAG" 2>&1' \
  | tee "$out"
if grep -qE "Error|Traceback" "$out"; then echo "refresh reported errors"; exit 1; fi
[ "$(grep -c '^shard ' "$out")" = "$PARALLEL" ] || { echo "not every shard finished"; exit 1; }
awk '/^shard /{ if (match($0, /[0-9]+ learners/)) l += substr($0, RSTART, RLENGTH - 9);
                if (match($0, /\(([0-9]+) already/)) c += substr($0, RSTART + 1, RLENGTH - 9);
                if (match($0, /\+[0-9]+ granted/)) g += substr($0, RSTART + 1, RLENGTH - 9) }
     END { printf "   total: %d learners, %d already current, +%d granted\n", l, c, g }' "$out"
echo "   took $(( $(date +%s) - t0 ))s"
rm -f "$out"
[ "$DRY" = 1 ] && { echo "DRY RUN: nothing written"; exit 0; }

if [ "$REVOKE_BELOW" = 1 ]; then
  echo "=== revoke points badges held below their threshold"
  docker exec -e CELERY_RESULT_BACKEND=disabled -i "$RGG" sh -lc 'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell' 2>&1 <<'PY' | grep -v '^Start event'
import collections
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from achievements.models import Achievement
from badges.models import Badge
from events.utils import simulate_rgg_internal_event
from users.models import GammaUser

PD = 'rgg_points_distribution'
ct = ContentType.objects.get_for_model(Badge)
revoked = collections.Counter()
for badge in Badge.objects.filter(is_active=True, rules__event_configuration__event_type__name=PD).distinct():
    goal = int(badge.rules.get().action[PD]['points'])
    holders = Achievement.objects.filter(content_type=ct, object_id=badge.id, completed_at__isnull=False, user__points__lt=goal)
    for user_id in list(holders.values_list('user_id', flat=True)):
        with transaction.atomic():
            user = GammaUser.objects.select_for_update().get(pk=user_id)
            if user.points < goal and badge.revoke_from_user(user):
                revoked[badge.title] += 1
                # A 0-point badge: the revoke changes no points, so bring the ring back explicitly.
                transaction.on_commit(lambda u=user: simulate_rgg_internal_event(u, PD))
    print('  %-30s -%d' % (badge.title[:30], revoked[badge.title]))
print('revoked %d' % sum(revoked.values()))
PY
fi

echo "=== after"
result=$(check 1 1)
echo "$result"
echo "$result" | grep -q "^CHECK OK" || { echo "POINTS_REFRESH FAILED (re-run to retry the stale learners)"; exit 1; }
echo "POINTS_REFRESH DONE"
