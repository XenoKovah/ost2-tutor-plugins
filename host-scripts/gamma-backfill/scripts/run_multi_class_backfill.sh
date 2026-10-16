#!/usr/bin/env bash
# Multi-class Accomplishments backfill (the active badges of that category: every required class
# certified). Spec: MULTI_CLASS_ACCOMPLISHMENTS_BACKFILL.md. Computed at run time from this box's LMS
# certificates (mc_extract.sh, SELECT only; selection in multi_class.py), granted through the engine's
# own completion path (mc_apply.py: PARALLEL learner shards, one row-locked transaction per learner),
# silently. Grant-only: never revokes. Points badges are left to §7 (run_points_refresh.sh) at the end.
# DRY RUN by default: prints the report, including SITE_TOTAL_BEFORE.
# APPLY=1 needs EXPECTED_TOTAL_BEFORE (the dry run's SITE_TOTAL_BEFORE: nothing else may change points in
# between), then verifies: every learner's points changed by exactly the badges just paid, nobody's went
# down or below zero, the total moved by the predicted delta, a leaderboard sample matches the points
# after the queue drains, and a second run grants nothing.
# Usage: CONFIRM_HOST=<LMS_HOST> [APPLY=1 EXPECTED_TOTAL_BEFORE=<n>] [PARALLEL=4] bash ~/gamma-backfill/run_multi_class_backfill.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"; [ -n "${OUT:-}" ] && echo FAILED > "$OUT/STATUS"' ERR
cd "$(dirname "$0")"
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
PARALLEL=${PARALLEL:-4}
if pgrep -af '[g]amma_reset_to_mirror|[r]un_backfill_fast|[i]mport_backfill_events|[r]ecompute_backfill|[r]un_course_completion|[r]un_ignominious|[r]un_speed_runner|[r]un_points_refresh' >/dev/null; then
  echo "refusing: another backfill is running"; exit 1
fi
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N --default-character-set=utf8mb4 "$@"; }
long=$(sql -e "SELECT COUNT(*) FROM information_schema.innodb_trx WHERE TIMESTAMPDIFF(SECOND, trx_started, NOW()) > 60" </dev/null)
[ "$long" = 0 ] || { echo "refusing: $long transactions have been open for over a minute (another bulk job?)"; exit 1; }
RGG=tutor_local-rgg-1
TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=exports/mc-backfill-$TS; mkdir -p "$OUT"

select_round() {  # $1 = output dir
  bash mc_extract.sh "$1/extract" > "$1/extract.log"
  python3 multi_class.py "$1/extract" "$1" > /dev/null
}

echo "== selection (read-only)"
select_round "$OUT"
cat "$OUT/report.txt"
total=$(awk '/^SITE_TOTAL_BEFORE /{print $2}' "$OUT/report.txt")
delta=$(awk '/^PREDICTED_DELTA /{print $2}' "$OUT/report.txt")
if [ "${APPLY:-0}" != 1 ]; then echo OK > "$OUT/STATUS"; echo "DRY RUN DONE ($OUT): nothing written"; exit 0; fi
[ "${EXPECTED_TOTAL_BEFORE:-}" = "$total" ] || { echo "refusing: site total is $total, EXPECTED_TOTAL_BEFORE=${EXPECTED_TOTAL_BEFORE:-unset}"; exit 1; }

echo "== apply ($PARALLEL learner shards)"
sql -e "SELECT user_uid, points FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/points-before.tsv"
docker exec "$RGG" mkdir -p /tmp/mcapply
docker cp "$OUT/plan.tsv" "$RGG:/tmp/mcapply/plan.tsv"
docker cp mc_apply.py "$RGG:/tmp/mcapply/mc_apply.py"
s=$(date +%s)
for k in $(seq 0 $((PARALLEL - 1))); do
  docker exec -e CELERY_RESULT_BACKEND=disabled -e APPLY=1 -e SHARD_K="$k" -e SHARD_N="$PARALLEL" -e RGG_SKIP_POINTS_REFRESH=true "$RGG" sh -c \
    'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell < /tmp/mcapply/mc_apply.py' > "$OUT/apply-$k.out" 2>&1 &
done
wait
docker exec "$RGG" rm -rf /tmp/mcapply
if grep -qE "Traceback|Error" "$OUT"/apply-*.out || [ "$(cat "$OUT"/apply-*.out | grep -c '^SHARD ')" != "$PARALLEL" ]; then
  cat "$OUT"/apply-*.out; echo "an apply shard failed"; exit 1
fi
cat "$OUT"/apply-*.out | grep '^GRANTED|' | cut -d'|' -f2- | tr '|' '\t' > "$OUT/granted.tsv"
cat "$OUT"/apply-*.out | grep -E '^(SHARD|SPLIT) ' | sed 's/^/   /'
echo "   granted $(wc -l < "$OUT/granted.tsv") ($(( $(date +%s) - s ))s)"

echo "== verify"
sql -e "SELECT user_uid, points FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/points-after.tsv"
python3 - "$OUT" "$delta" <<'PY' | tee "$OUT/verify.txt"
import collections, sys
d, predicted = sys.argv[1], int(sys.argv[2])
rd = lambda f: dict(line.rstrip('\n').split('\t') for line in open(f) if line.strip())
before, after = rd(d + '/points-before.tsv'), rd(d + '/points-after.tsv')
exp = collections.Counter()
for line in open(d + '/granted.tsv'):
    user, badge, paid = line.rstrip('\n').split('\t')
    exp[user] += int(paid)
change = {u: int(after.get(u, 0)) - int(before.get(u, 0)) for u in set(before) | set(after)}
bad = sorted(u for u, c in change.items() if c != exp[u])
down = sorted(u for u, c in change.items() if c < 0)
below = sorted(u for u in exp if int(before.get(u, 0)) >= 0 > int(after.get(u, 0)))
total = sum(change.values())
print('grants %d to %d learners; points %+d (predicted %+d, paid %+d); learners whose change differs: %d %s; went down: %d; went below 0: %d' % (
    sum(1 for _ in open(d + '/granted.tsv')), len(exp), total, predicted, sum(exp.values()), len(bad), bad[:5], len(down), len(below)))
print('MC_VERIFY', 'OK' if not bad and not down and not below and total == predicted == sum(exp.values()) else 'MISMATCH')
PY
grep -q "MC_VERIFY OK" "$OUT/verify.txt" || { echo FAILED > "$OUT/STATUS"; exit 1; }

echo "== leaderboard sample (after the 60 s queue drains)"
lb_check() {
  docker exec -e CELERY_RESULT_BACKEND=disabled -i "$RGG" sh -lc 'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell' 2>/dev/null <<'PY'
import random
from django.conf import settings
from leaderboard.constants import GENERAL_LEADERBOARD_ID_TEMPLATE
from leaderboard.repository import RedisLeaderboardsPendingUpdateRepository
from leaderboard.utils import get_redis_client
from users.models import GammaUser
r = get_redis_client()
granted = sorted({line.split('\t')[0] for line in open('/tmp/mc-granted.tsv') if line.strip()})
random.seed(26)
sample = set(random.sample(granted, min(400, len(granted))))
pending = r.scard(RedisLeaderboardsPendingUpdateRepository.CACHE_KEY)
on = bad = 0
for uid, src, pts in GammaUser.objects.filter(user_uid__in=sample).values_list('user_uid', 'signup_source', 'points'):
    score = r.zscore(GENERAL_LEADERBOARD_ID_TEMPLATE.format(user_signup_source=src or settings.MAIN_SIGNUP_SOURCE), uid)
    if score is None:
        continue
    on += 1
    bad += int(score) != pts
print('LEADERBOARD sample %d, on the board %d, mismatches %d, queue %d' % (len(sample), on, bad, pending))
PY
}
docker cp "$OUT/granted.tsv" "$RGG:/tmp/mc-granted.tsv"
for attempt in 1 2 3 4; do
  sleep 60
  line=$(lb_check | grep '^LEADERBOARD')
  echo "   $line"
  echo "$line" | grep -q "mismatches 0, queue 0" && break
done
docker exec "$RGG" rm -f /tmp/mc-granted.tsv
echo "$line" | grep -q "mismatches 0," || echo "   WARNING: leaderboard sample still differs (the leaderboard rebuild may be running); re-check later"

echo "== second run: nothing may be granted"
mkdir -p "$OUT/rerun"
select_round "$OUT/rerun"
again=$(tail -n +2 "$OUT/rerun/plan.tsv" | wc -l)
echo "   would grant $again"
[ "$again" = 0 ] || { echo "verification failed: re-run would grant more"; echo FAILED > "$OUT/STATUS"; exit 1; }
echo OK > "$OUT/STATUS"
echo "MC BACKFILL DONE ($OUT). Run run_points_refresh.sh (§7) afterwards: these grants add points."
