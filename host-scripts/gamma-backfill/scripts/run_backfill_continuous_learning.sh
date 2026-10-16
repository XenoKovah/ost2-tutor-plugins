#!/usr/bin/env bash
# Continuous Learning streak-badge backfill. Runs gamma's new backfill_continuous_learning command
# (branch continuous-learning-backfill-grant @ b4aa2b3) as a drop-in beside the deployed gamma, so no deploy
# is needed: cl_streaks.py (= users/continuous_learning.py @ b4aa2b3) + cl_backfill_cmd.py (the command,
# plus --ignore-event-type / --ignore-events-file) are copied into the rgg container for the run and removed after.
# Per learner, N shards in parallel (GammaUser.id % N), each grant under the learner's row lock.
#   1. Active days leave out events whose stored date is not a day the learner was active: 5 profile
#      fields the profile backfill dated at import time, certificates (staff bulk-issue them), and the
#      enrollment events of enrollments that staff or a script created (LMS enrollment history, cl_ignore_enrollments.sql).
#   2. Dry run in N shards (read-only); prints the summed table.
#   3. COMMIT=1: grants in N shards, then verifies: each learner's points changed by exactly their new
#      streak badges' bonus, no streak counter changed, and a second dry run finds nothing left to grant.
#      Records the grants (the rollback list) in exports/cl-backfill-<ts>/granted.tsv.
# Run it AFTER the event backfills (it reads active days from gamma's events).
# Usage: CONFIRM_HOST=<LMS_HOST> [COMMIT=1] [PARALLEL=8] bash ~/gamma-backfill/run_backfill_continuous_learning.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"; [ -n "${OUT:-}" ] && echo FAILED > "$OUT/STATUS"' ERR
cd "$(dirname "$0")"
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
PARALLEL=${PARALLEL:-8}
RGG=tutor_local-rgg-1
for u in gamma-backfill gamma-backfill-uts; do
  if systemctl is-active --quiet "$u"; then echo "refusing: $u is running"; exit 1; fi
done
if pgrep -af '[g]amma_reset_to_mirror|[r]un_backfill_fast|[i]mport_backfill_events|[r]ecompute_backfill|[g]rant_manual_badge' >/dev/null; then
  echo "refusing: another backfill is running"; exit 1
fi
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N --default-character-set=utf8mb4 "$@"; }
export CL_IGNORE_TYPES="edx_profile_name_made_public edx_profile_education_set edx_profile_location_set edx_profile_language_set edx_profile_about_me_set edx_certificate_created"
TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=exports/cl-backfill-$TS; mkdir -p "$OUT"
W=/tmp/clbf
cleanup(){ rm -rf "$W"; docker exec "$RGG" rm -rf "$W" >/dev/null 2>&1 || true; }
trap cleanup EXIT
rm -rf "$W"; mkdir -p "$W"; cp cl_streaks.py cl_backfill_cmd.py "$W/"
sql < cl_ignore_enrollments.sql > "$W/ignore_event_ids.txt"
cp "$W/ignore_event_ids.txt" "$OUT/"
echo "events not counted as active days: types [$CL_IGNORE_TYPES] + $(wc -l < "$W/ignore_event_ids.txt") staff/script enrollment events"

mk(){  # mk NAME [extra args]: one runner per shard
  python3 - "$1" "$PARALLEL" "$W" "${2:-}" <<'PY'
import os, sys
name, n, w, extra = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
for k in range(n):
    args = ['--shard', '%d/%d' % (k, n), '--ignore-events-file', w + '/ignore_event_ids.txt']
    for t in os.environ['CL_IGNORE_TYPES'].split():
        args += ['--ignore-event-type', t]
    args += extra.split()
    open('%s/%s_%d.py' % (w, name, k), 'w').write(
        "import sys, time\nsys.path.insert(0, %r)\nfrom django.core.management import call_command\n"
        "from cl_backfill_cmd import Command\nt = time.time()\ncall_command(Command(), *%r)\n"
        "print('SHARD_SECONDS %%.1f' %% (time.time() - t))\n" % (w, args))
PY
}
run(){  # run NAME: all shards in parallel, logs in $OUT/NAME_k.log, summed table in $OUT/NAME-summary.txt
  docker exec "$RGG" rm -rf "$W"; docker cp "$W" "$RGG:$W"
  local s; s=$(date +%s)
  for k in $(seq 0 $((PARALLEL - 1))); do
    docker exec -e CELERY_RESULT_BACKEND=disabled -e RGG_SKIP_POINTS_REFRESH=true "$RGG" sh -c "cd /rgg/gamma && /rgg/venv/bin/python manage.py shell < $W/${1}_$k.py" > "$OUT/${1}_$k.log" 2>&1 &
  done
  wait
  if grep -lE 'Traceback|Error' "$OUT/${1}"_*.log; then echo "a shard failed: see $OUT"; exit 1; fi
  python3 cl_summarize.py "$OUT/${1}"_*.log | tee "$OUT/${1}-summary.txt"
  echo "   ($(( $(date +%s) - s ))s wall)"
}
snap(){ sql -e "SELECT user_uid, points, current_streak, last_active_date, current_weekday_streak, last_weekday_active_date FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/users-$1.tsv"; }
total(){ python3 -c "import json,sys; print(json.loads(open(sys.argv[1]).read().split('SUMMARY_JSON ')[1])['totals'].get(sys.argv[2], 0))" "$OUT/$1-summary.txt" "$2"; }

echo "== dry run ($PARALLEL shards)"
mk dry; run dry
if [ "${COMMIT:-0}" = 1 ]; then
  echo "== commit ($PARALLEL shards)"
  snap before
  mk commit "--commit --verbose-users"; run commit
  snap after
  python3 cl_verify.py "$OUT" | tee "$OUT/verify.txt"
  grep -q "CL_VERIFY OK" "$OUT/verify.txt" || { echo "verification failed"; echo FAILED > "$OUT/STATUS"; exit 1; }
  echo "== dry run again: nothing may be left to grant"
  mk again; run again
  [ "$(total again would_grant)" = 0 ] || { echo "verification failed: would_grant after commit"; echo FAILED > "$OUT/STATUS"; exit 1; }
  sql -e "SELECT a.id, b.slug, u.user_uid, a.completed_at, a.completion_points_paid
    FROM rgg.achievements_achievement a JOIN rgg.badges_badge b ON b.id = a.object_id
    JOIN rgg.django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
    JOIN rgg.users_gammauser u ON u.id = a.user_id
    WHERE b.category = 'Continuous Learning' AND a.completed_at IS NOT NULL
      AND EXISTS (SELECT 1 FROM rgg.achievements_achievementrule ar WHERE ar.achievement_id = a.id AND ar.dependencies LIKE '%achieved_at%')
    ORDER BY b.slug, u.user_uid" </dev/null > "$OUT/granted.tsv"
  echo "rollback list: $OUT/granted.tsv ($(wc -l < "$OUT/granted.tsv") backfilled streak badges on this box)"
fi
echo OK > "$OUT/STATUS"
echo "CL BACKFILL DONE ($OUT)"
