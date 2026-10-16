#!/usr/bin/env bash
# Fast backfill of Gamma badges from LMS history, without Gamma's per-event pipeline:
#   1. generate every event the bridge's backfill feeds would send, in parallel learner
#      shards, into a staging table in the LMS DB (backfill_generate.py; identical payloads),
#   2. bulk-import them into Gamma (import_backfill_events.sh),
#   3. grant badges from the stored events (recompute_backfill.sh),
#   4. keep a record of exactly what was imported, then drop the staging table.
# The streak engine never runs (no per-event signals), so no backfill mode is needed.
#
# Usage: CONFIRM_HOST=<LMS_HOST> run_backfill_fast.sh "Using The Site" ["Points points points!" ...]
#   env: PARALLEL=8 SHARDS=8 FEEDS="backfill_done_events ..." (default: the Using The Site feeds)
set -Eeuo pipefail
trap 'log "FAILED at line $LINENO"' ERR
[ $# -ge 1 ] || { echo "usage: CONFIRM_HOST=<lms host> $0 CATEGORY [CATEGORY ...]"; exit 1; }
BASE=$HOME/gamma-backfill
LOG=$BASE/fast-backfill.log
LMS=tutor_local-lms-1
TABLE=ost2_backfill_events
PARALLEL=${PARALLEL:-8}
SHARDS=${SHARDS:-8}
FEEDS=${FEEDS:-backfill_done_events backfill_problem_submitted_events backfill_enrollment_events backfill_bookmark_events backfill_forum_events}
TUTOR=$HOME/tutor-venv/bin/tutor
[ "${CONFIRM_HOST:-}" = "$($TUTOR config printvalue LMS_HOST)" ] || { echo "refusing: CONFIRM_HOST must equal this box's LMS_HOST"; exit 1; }
log(){ echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
t0=$(date +%s)

log "START categories=[$*] feeds=[$FEEDS] parallel=$PARALLEL shards=$SHARDS"

# A bigger InnoDB buffer pool for the run (recompute went 5.4k -> 9.1k grants/min on dev with
# 8 GB vs the 128 MB default). Runtime-only setting; restored on exit (and lost on MySQL restart).
BUFFER_POOL_GB=${BUFFER_POOL_GB:-8}
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
my(){ docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N -e "$1" </dev/null; }
ORIG_POOL=$(my "SELECT @@innodb_buffer_pool_size")
restore_pool(){ my "SET GLOBAL innodb_buffer_pool_size = $ORIG_POOL" || true; }
trap 'restore_pool' EXIT
my "SET GLOBAL innodb_buffer_pool_size = $((BUFFER_POOL_GB * 1024 * 1024 * 1024))"
log "   InnoDB buffer pool: $((ORIG_POOL / 1048576)) MB -> $(( $(my 'SELECT @@innodb_buffer_pool_size') / 1048576 )) MB for this run"
BRIDGE=$(docker exec "$LMS" python -c "import gamma_bridge, os; print(os.path.dirname(gamma_bridge.__file__))")
for dropin in backfill_generate.py backfill_profile_events.py; do   # LMS-side drop-ins
  docker cp "$BASE/$dropin" "$LMS:$BRIDGE/management/commands/$dropin"
done
lms(){ docker exec "$LMS" sh -lc "cd /openedx/edx-platform && ./manage.py lms $*"; }
lms backfill_generate --create-table --table "$TABLE" >/dev/null 2>&1

log "1/4 generate events"
jobs=()
for feed in $FEEDS; do
  case "$feed" in
    backfill_forum_events|backfill_bookmark_events|backfill_profile_events) jobs+=("$feed") ;;   # small: one job
    *) for k in $(seq 0 $((SHARDS - 1))); do jobs+=("$feed --shard $k/$SHARDS"); done ;;
  esac
done
export LMS TABLE
GEN_OUT=$(mktemp)
printf '%s\n' "${jobs[@]}" | xargs -P "$PARALLEL" -I{} sh -c \
  'docker exec "$LMS" sh -lc "cd /openedx/edx-platform && ./manage.py lms backfill_generate {} --table $TABLE" 2>&1 | grep -iE "sent [0-9]+( \(|:)|Error|Traceback" | sed "s/^/   /"' \
  | tee "$GEN_OUT" | tee -a "$LOG"
# Summaries: "<feed>: sent N (... N error ...)" from the base feeds, "Sent N: ... N errors ..." from the forum feed.
if grep -qE "Error|Traceback| [1-9][0-9]* errors?" "$GEN_OUT"; then log "generation reported errors; stopping before import"; exit 1; fi
[ "$(grep -ciE 'sent [0-9]+( \(|:)' "$GEN_OUT")" = "${#jobs[@]}" ] || { log "not every generation job reported a summary; stopping"; exit 1; }
rm -f "$GEN_OUT"
docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -e \
  "SELECT event_type, COUNT(*) events, COUNT(DISTINCT user_uid) learners FROM openedx.$TABLE GROUP BY event_type WITH ROLLUP" </dev/null | tee -a "$LOG"
log "   generated in $(( $(date +%s) - t0 ))s"

log "2/4 import into gamma"
CONFIRM_HOST=$CONFIRM_HOST bash "$BASE/import_backfill_events.sh" "openedx.$TABLE" 2>&1 | tee -a "$LOG"

log "3/4 recompute (parallel learner shards): $*"
REC_OUT=$(mktemp)
PARALLEL=$PARALLEL bash "$BASE/recompute_backfill.sh" "$@" 2>&1 | grep -vE "^  |Warning|warn\(|declare_namespace|pkg_resources" | tee "$REC_OUT" | tee -a "$LOG"
grep -q "RECOMPUTE DONE" "$REC_OUT" || { log "recompute did not finish"; exit 1; }
rm -f "$REC_OUT"

log "4/4 record what was imported, drop staging"
mkdir -p "$BASE/exports"
REC=$BASE/exports/fast-backfill-$(date -u +%Y%m%dT%H%M%SZ).sql.zst
docker exec -e MYSQL_PWD tutor_local-mysql-1 mysqldump -uroot --single-transaction --set-gtid-purged=OFF \
  --no-tablespaces --default-character-set=utf8mb4 openedx "$TABLE" </dev/null | zstd -q -3 > "$REC"
docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -e "DROP TABLE openedx.$TABLE" </dev/null
log "DONE in $(( $(date +%s) - t0 ))s; record: $REC"
