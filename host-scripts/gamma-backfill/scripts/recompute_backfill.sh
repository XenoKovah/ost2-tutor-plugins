#!/usr/bin/env bash
# Grant badges from the events already stored in Gamma (after an import, or to apply a new or
# edited badge without replaying). For each category, in order, reconcile every rule-driven
# badge per learner in $PARALLEL learner shards at once (backfill_recompute_shard.py; same
# reconciliation as gamma's recompute_holders, under each learner's row lock); then schedule
# one leaderboard rebuild.
# Put state-derived categories (e.g. "Points points points!") LAST: they depend on the points
# the earlier categories pay out. The shards run with RGG_SKIP_POINTS_REFRESH=true (gamma 6188bc9+):
# the points badges are re-checked once, by run_points_refresh.sh at the end of the backfill.
#
# Usage: [PARALLEL=8] recompute_backfill.sh "Using The Site" ["Points points points!" ...]
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
[ $# -ge 1 ] || { echo "usage: $0 CATEGORY [CATEGORY ...]"; exit 1; }
RGG=tutor_local-rgg-1
BASE=$HOME/gamma-backfill
PARALLEL=${PARALLEL:-8}

# Not in the gamma image: install (idempotent).
docker cp "$BASE/backfill_recompute_shard.py" "$RGG:/rgg/gamma/badges/management/commands/backfill_recompute_shard.py"

for category in "$@"; do
  echo "=== $category ($PARALLEL learner shards)"
  t0=$(date +%s)
  out=$(mktemp)
  export RGG category PARALLEL
  seq 0 $((PARALLEL - 1)) | xargs -P "$PARALLEL" -I{} sh -c \
    'docker exec -e CELERY_RESULT_BACKEND=disabled -e RGG_SKIP_POINTS_REFRESH=true "$RGG" sh -lc "cd /rgg/gamma && /rgg/venv/bin/python manage.py backfill_recompute_shard --category \"$category\" --shard {}/$PARALLEL" 2>&1 | grep -E "granted|Error|Traceback"' \
    | tee "$out"
  if grep -qE "Error|Traceback" "$out"; then echo "recompute reported errors"; exit 1; fi
  [ "$(grep -c '^shard ' "$out")" = "$PARALLEL" ] || { echo "not every shard finished"; exit 1; }
  awk '/^shard /{ if (match($0, /\+[0-9]+ granted/)) g += substr($0, RSTART + 1, RLENGTH - 9);
                  if (match($0, /-[0-9]+ revoked/)) r += substr($0, RSTART + 1, RLENGTH - 9) }
       END { printf "   total: +%d granted, -%d revoked\n", g, r }' "$out"
  echo "   took $(( $(date +%s) - t0 ))s"
  rm -f "$out"
done
docker exec -e CELERY_RESULT_BACKEND=disabled "$RGG" sh -lc "cd /rgg/gamma && /rgg/venv/bin/python manage.py backfill_recompute_shard --rebuild-leaderboard" 2>&1 | tail -1
echo "RECOMPUTE DONE"
