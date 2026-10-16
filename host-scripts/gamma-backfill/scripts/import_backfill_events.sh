#!/usr/bin/env bash
# Bulk-load backfill events into this box's Gamma, from either
#   * a staging table written by backfill_generate.py (e.g. openedx.ost2_backfill_events), or
#   * an export file written by export_backfill_events.sh (e.g. dev-replay.sql.zst).
#
#  1. creates the Gamma users those events belong to, exactly as the live pipeline does
#     (GammaUser.ensure_gamma_user_is_created = get_or_create(user_uid=...)), in parallel shards,
#  2. inserts the events, mapping event types by NAME and skipping any this box already has
#     (unique uid+client+user_uid, e.g. the live event for the same action),
#  3. for those already-present events, fills in a missing block_id from the backfill — what
#     gamma's EventsAPIView._record_block_id_on_duplicate does when a replay collides,
#  4. with PAY_AWARDS=1: pays per-event points (EventConfiguration.award) for the events THIS
#     import inserted, the way the live signal does (GammaUser.run_update_user_pipeline under the
#     learner's row lock), in parallel learner shards.
#
# It does NOT evaluate badges or streaks: follow it with recompute_backfill.sh.
# Live boxes: events arriving during the import are skipped as duplicates or keep their live
# processing; the only theoretical double-pay is a learner's FIRST event of a type arriving live
# in the seconds the INSERT statement runs.
#
# Usage: CONFIRM_HOST=<LMS_HOST> [DRY_RUN=1] [PAY_AWARDS=1] [PARALLEL=8] import_backfill_events.sh SOURCE
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
IN=${1:?usage: CONFIRM_HOST=<lms host> $0 openedx.ost2_backfill_events | EXPORT.sql.zst}
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to import into this box"; exit 1; }
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot --default-character-set=utf8mb4 "$@"; }
RGG=tutor_local-rgg-1
PARALLEL=${PARALLEL:-8}
step_t0=$(date +%s)
took(){ echo "   ($(( $(date +%s) - step_t0 ))s)"; step_t0=$(date +%s); }

# Run a python snippet (stdin) in $PARALLEL learner shards inside the gamma container, in
# parallel. Each shard gets SHARD_K / SHARD_N and handles learners with crc32(user_uid) % N == K.
shards(){
  local code out; code=$(cat); out=$(mktemp)
  docker exec -i "$RGG" sh -c 'cat > /tmp/import_shard.py' <<<"$code"
  seq 0 $((PARALLEL - 1)) | xargs -P "$PARALLEL" -I{} docker exec -e CELERY_RESULT_BACKEND=disabled -e SHARD_K={} -e SHARD_N="$PARALLEL" "$RGG" \
    sh -c 'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell < /tmp/import_shard.py 2>&1 | grep -E "^SHARD|Error|Traceback"' \
    > "$out" || true
  cat "$out"
  if grep -qE "Error|Traceback" "$out" || [ "$(grep -c '^SHARD' "$out")" != "$PARALLEL" ]; then
    echo "a shard failed"; rm -f "$out"; exit 1
  fi
  rm -f "$out"
}

[ "$(sql -N -e "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='rgg' AND table_name='events_event' AND column_name='block_id'" </dev/null)" = 1 ] \
  || { echo "target gamma lacks events_event.block_id: deploy the same gamma version first"; exit 1; }

if [ -f "$IN" ]; then
  echo "1/5 load export file into staging db rgg_xfer"
  [ -f "$IN.sha256" ] && (cd "$(dirname "$IN")" && sha256sum -c --quiet "$(basename "$IN").sha256")
  sql -e "DROP DATABASE IF EXISTS rgg_xfer; CREATE DATABASE rgg_xfer" </dev/null
  zstd -dc "$IN" | sql rgg_xfer
  SRC=rgg_xfer.backfill_events
else
  echo "1/5 use staging table $IN"
  SRC=$IN
fi
total=$(sql -N -e "SELECT COUNT(*) FROM $SRC" </dev/null)
unmapped=$(sql -N -e "SELECT GROUP_CONCAT(DISTINCT s.event_type) FROM $SRC s
  LEFT JOIN rgg.events_eventtype t ON t.name = s.event_type LEFT JOIN rgg.events_eventconfiguration c ON c.event_type_id = t.id
  WHERE c.id IS NULL" </dev/null)
[ "$unmapped" = NULL ] || { echo "event types missing on target: $unmapped"; exit 1; }
new_events=$(sql -N -e "SELECT COUNT(*) FROM $SRC s LEFT JOIN rgg.events_event e
  ON e.uid = s.uid AND e.client <=> s.client AND e.user_uid = s.user_uid WHERE e.id IS NULL" </dev/null)
new_users=$(sql -N -e "SELECT COUNT(DISTINCT s.user_uid) FROM $SRC s
  LEFT JOIN rgg.users_gammauser u ON u.user_uid = s.user_uid WHERE u.id IS NULL" </dev/null)
echo "   $total events: $new_events new, $((total - new_events)) already present; $new_users new Gamma users"
sql -e "SELECT event_type, COUNT(*) FROM $SRC GROUP BY event_type" </dev/null
took
if [ "${DRY_RUN:-0}" = 1 ]; then echo "DRY RUN: nothing written"; exit 0; fi

echo "2/5 create missing Gamma users (live-pipeline method, $PARALLEL shards)"
sql -N -e "SELECT DISTINCT s.user_uid FROM $SRC s
  LEFT JOIN rgg.users_gammauser u ON u.user_uid = s.user_uid WHERE u.id IS NULL" </dev/null \
  | docker exec -i "$RGG" sh -c 'cat > /tmp/import-new-users.txt'
shards <<'PY'
import os, zlib
from users.models import GammaUser
k, n, made = int(os.environ['SHARD_K']), int(os.environ['SHARD_N']), 0
for line in open('/tmp/import-new-users.txt'):
    uid = line.rstrip('\n')
    if uid and zlib.crc32(uid.encode()) % n == k:
        GammaUser.ensure_gamma_user_is_created(user_uid=uid)
        made += 1
print('SHARD %d/%d users ensured %d' % (k, n, made))
PY
took

echo "3/5 insert events (already-present ones are skipped)"
before_id=$(sql -N -e "SELECT COALESCE(MAX(id), 0) FROM rgg.events_event" </dev/null)
inserted=$(sql -N -e "INSERT IGNORE INTO rgg.events_event (uid, signup_source, user_uid, created_at, client, org, course_id, block_id, configuration_id)
  SELECT s.uid, s.signup_source, s.user_uid, s.created_at, s.client, s.org, s.course_id, s.block_id, c.id
  FROM $SRC s
  JOIN rgg.events_eventtype t ON t.name = s.event_type
  JOIN rgg.events_eventconfiguration c ON c.event_type_id = t.id;
  SELECT ROW_COUNT();" </dev/null | tail -1)
echo "   inserted $inserted, skipped as already present $((total - inserted))"
took

echo "4/5 fill in block_id on already-present events that lack it"
filled=$(sql -N -e "UPDATE rgg.events_event e
  JOIN $SRC s ON s.uid = e.uid AND s.client = e.client AND s.user_uid = e.user_uid
  SET e.block_id = s.block_id
  WHERE e.block_id IS NULL AND s.block_id IS NOT NULL;
  SELECT ROW_COUNT();" </dev/null | tail -1)
echo "   block_id filled on $filled events"
took

if [ "${PAY_AWARDS:-0}" = 1 ]; then
  echo "   paying per-event points for the events this import inserted ($PARALLEL learner shards)"
  # Only events that came from this import (in the staging set, inserted after before_id) —
  # live events that arrived meanwhile were already paid by the live signal.
  sql -N -e "SELECT e.id, e.user_uid FROM rgg.events_event e
    JOIN $SRC s ON s.uid = e.uid AND s.client = e.client AND s.user_uid = e.user_uid
    JOIN rgg.events_eventconfiguration c ON c.id = e.configuration_id
    WHERE e.id > $before_id AND c.award > 0 ORDER BY e.id" </dev/null \
    | docker exec -i "$RGG" sh -c 'cat > /tmp/import-pay-events.tsv'
  shards <<'PY' | tee /tmp/import-pay.out
import os, zlib
from django.db import transaction
from events.models import Event
from users.models import GammaUser
k, n = int(os.environ['SHARD_K']), int(os.environ['SHARD_N'])
ids = []
for line in open('/tmp/import-pay-events.tsv'):
    event_id, user_uid = line.rstrip('\n').split('\t')
    if zlib.crc32(user_uid.encode()) % n == k:
        ids.append(int(event_id))
paid = points = 0
for start in range(0, len(ids), 1000):
    for event in Event.objects.filter(id__in=ids[start:start + 1000]).select_related('configuration').order_by('id'):
        with transaction.atomic():
            user = GammaUser.objects.select_for_update().get(user_uid=event.username)
            user.run_update_user_pipeline(event)
        paid += 1
        points += event.configuration.award
print('SHARD %d/%d paid %d points across %d events' % (k, n, points, paid))
PY
  # "SHARD k/n paid P points across E events"
  awk '/^SHARD .* paid /{p+=$4; e+=$7} END {printf "   paid %d points across %d events\n", p, e}' /tmp/import-pay.out
  rm -f /tmp/import-pay.out
  took
fi

echo "5/5 clean up"
[ "$SRC" = rgg_xfer.backfill_events ] && sql -e "DROP DATABASE rgg_xfer" </dev/null
echo "IMPORT DONE: next run recompute_backfill.sh"
