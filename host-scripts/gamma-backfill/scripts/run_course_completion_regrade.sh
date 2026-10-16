#!/usr/bin/env bash
# Course Completion tier regrade (RGG/COURSE_COMPLETION_TIER_TIMING.md sections 3, 3a and 6).
# DRY RUN by default: extracts LMS and gamma data with SELECTs only (cc_extract.sh), computes every
# (learner, class) tier (cc_regrade.py) and prints the report. It writes nothing to any database.
# APPLY=1 EXPECTED_TOTAL_BEFORE=<site total printed by the dry run> applies the plan in PARALLEL
# learner shards (cc_apply.py in the rgg container), then verifies:
#   - each learner's points changed by exactly the plan's top-ups (plus any badge the live engine
#     itself awards as a side effect, which is listed);
#   - the per-course boards grew by the same total;
#   - no class is held at more than one tier;
#   - a fresh dry run plans nothing more (every row "keep").
# Records everything in exports/cc-regrade-<ts>/.
# Reads the LMS directly, not gamma's events, so it can run before or after the event backfills.
# Usage: CONFIRM_HOST=<LMS_HOST> [APPLY=1 EXPECTED_TOTAL_BEFORE=N] [PARALLEL=8] bash ~/gamma-backfill/run_course_completion_regrade.sh
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
if pgrep -af '[g]amma_reset_to_mirror|[r]un_backfill_fast|[i]mport_backfill_events|[r]ecompute_backfill|[g]rant_manual_badge|[r]un_backfill_continuous' >/dev/null; then
  echo "refusing: another backfill is running"; exit 1
fi
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N --default-character-set=utf8mb4 "$@"; }
TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=exports/cc-regrade-$TS; mkdir -p "$OUT"
W=/tmp/ccapply
cleanup(){ docker exec "$RGG" rm -rf "$W" >/dev/null 2>&1 || true; }
trap cleanup EXIT

plan(){  # plan DIR [EARLIER_DIR]: read-only extract + compute; with EARLIER_DIR, reuse its LMS
  mkdir -p "$1/extract"  # extract (the apply changes gamma only) and re-read just gamma's side
  cut -f1 manual/beta_completion_pairs.tsv | tail -n +2 | sort -u > "$1/candidate-usernames.txt"
  if [ -n "${2:-}" ]; then
    cp "$2"/extract/{certs,allowlist,clicks,candidate_users}.tsv "$1/extract/"
    GAMMA_ONLY=1 bash cc_extract.sh "$1/extract" > "$1/extract.log"
  else
    bash cc_extract.sh "$1/extract" "$1/candidate-usernames.txt" > "$1/extract.log"
  fi
  python3 cc_regrade.py "$1/extract" manual/beta_completion_pairs.tsv "$1" > "$1/report.txt"
}
echo "== dry run"
plan "$OUT/before"; cat "$OUT/before/report.txt"
if [ "${APPLY:-0}" != 1 ]; then echo OK > "$OUT/STATUS"; echo "DRY RUN DONE ($OUT): nothing written"; exit 0; fi

echo "== apply ($PARALLEL shards)"
total=$(tail -n +2 "$OUT/before/extract/site_points.tsv" | cut -f2)
[ -n "${EXPECTED_TOTAL_BEFORE:-}" ] || { echo "refusing: set EXPECTED_TOTAL_BEFORE to the dry run's site total"; exit 1; }
[ "$total" = "$EXPECTED_TOTAL_BEFORE" ] || { echo "refusing: site total is $total, not $EXPECTED_TOTAL_BEFORE: re-run the dry run"; exit 1; }
if cut -f15 "$OUT/before/plan.tsv" | grep -q conflict; then echo "refusing: some learners hold several tiers of a class (see plan.tsv)"; exit 1; fi
T0=$(sql -e "SELECT UTC_TIMESTAMP(6)" </dev/null)
sql -e "SELECT user_uid, points FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/points-before.tsv"
sql -e "SELECT COALESCE(SUM(points), 0) FROM rgg.users_gammausercoursepoints" </dev/null > "$OUT/course-points-before.txt"
docker exec "$RGG" rm -rf "$W"; docker exec "$RGG" mkdir -p "$W"
docker cp cc_apply.py "$RGG:$W/cc_apply.py"; docker cp "$OUT/before/plan.tsv" "$RGG:$W/plan.tsv"
s=$(date +%s)
for k in $(seq 0 $((PARALLEL - 1))); do
  docker exec -e CELERY_RESULT_BACKEND=disabled -e APPLY=1 -e SHARD_K="$k" -e SHARD_N="$PARALLEL" -e RGG_SKIP_POINTS_REFRESH=true "$RGG" \
    sh -c "cd /rgg/gamma && /rgg/venv/bin/python manage.py shell < $W/cc_apply.py" > "$OUT/apply_$k.log" 2>&1 &
done
wait
if grep -lE 'Traceback|Error' "$OUT"/apply_*.log; then echo "a shard failed: see $OUT"; echo FAILED > "$OUT/STATUS"; exit 1; fi
[ "$(cat "$OUT"/apply_*.log | grep -c '^SHARD')" = "$PARALLEL" ] || { echo "missing shard output"; echo FAILED > "$OUT/STATUS"; exit 1; }
grep -h '^SHARD\|^CONFLICT' "$OUT"/apply_*.log
echo "   ($(( $(date +%s) - s ))s wall)"

echo "== verify"
sql -e "SELECT user_uid, points FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/points-after.tsv"
sql -e "SELECT COALESCE(SUM(points), 0) FROM rgg.users_gammausercoursepoints" </dev/null > "$OUT/course-points-after.txt"
sql -e "SELECT u.user_uid, b.title, b.category, COALESCE(a.completion_points_paid, 0)
  FROM rgg.achievements_achievement a JOIN rgg.users_gammauser u ON u.id = a.user_id JOIN rgg.badges_badge b ON b.id = a.object_id
  JOIN rgg.django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
  WHERE a.completed_at >= '$T0' AND b.category NOT LIKE 'Course Completion /%'" </dev/null > "$OUT/side-effect-badges.tsv"
python3 - "$OUT" <<'PY' | tee "$OUT/verify.txt"
import collections, csv, sys
d = sys.argv[1]
exp = collections.Counter()
for p in csv.DictReader(open(d + '/before/plan.tsv'), delimiter='\t'):
    if p['action'] == 'new' or p['action'].startswith('move'):
        exp[p['username']] += int(p['topup'])
planned = sum(exp.values())
side = collections.Counter()
for line in open(d + '/side-effect-badges.tsv'):
    user, title, category, paid = line.rstrip('\n').split('\t')
    exp[user] += int(paid)
    side['%s (%s)' % (title, category)] += 1
rd = lambda f: dict(line.rstrip('\n').split('\t') for line in open(f) if line.strip())
before, after = rd(d + '/points-before.tsv'), rd(d + '/points-after.tsv')
bad = sorted(u for u in set(before) | set(after) if int(after.get(u, 0)) - int(before.get(u, 0)) != exp[u])
course = int(open(d + '/course-points-after.txt').read()) - int(open(d + '/course-points-before.txt').read())
print('planned tier points %+d; side-effect badges %d %s' % (planned, sum(side.values()), dict(side.most_common(5))))
print('site points %+d (expected %+d); learners whose change differs: %d %s; course boards %+d (expected %+d)' % (
    sum(int(v) for v in after.values()) - sum(int(v) for v in before.values()), sum(exp.values()), len(bad), bad[:5], course, planned))
print('CC_VERIFY', 'OK' if not bad and course == planned else 'MISMATCH')
PY
grep -q "CC_VERIFY OK" "$OUT/verify.txt" || { echo "verification failed"; echo FAILED > "$OUT/STATUS"; exit 1; }
multi=$(sql -e "SELECT COUNT(*) FROM (SELECT a.user_id, b.title FROM rgg.achievements_achievement a
  JOIN rgg.badges_badge b ON b.id = a.object_id
  JOIN rgg.django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
  WHERE b.category LIKE 'Course Completion /%' AND a.completed_at IS NOT NULL
  GROUP BY a.user_id, b.title HAVING COUNT(*) > 1) x" </dev/null)
echo "classes held at more than one tier: $multi"
[ "$multi" = 0 ] || { echo "verification failed"; echo FAILED > "$OUT/STATUS"; exit 1; }
echo "== dry run again: nothing may be left to do"
plan "$OUT/after" "$OUT/before"
grep -A1 "^Against what gamma holds now" "$OUT/after/report.txt" | head -2
left=$(tail -n +2 "$OUT/after/plan.tsv" | cut -f15 | grep -vc '^keep$' || true)
[ "$left" = 0 ] || { echo "verification failed: $left rows still to do"; echo FAILED > "$OUT/STATUS"; exit 1; }
tail -n +2 "$OUT/after/plan.tsv" | cut -f2,3,4,5,8,9 > "$OUT/granted.tsv"
echo "record: $OUT/granted.tsv ($(wc -l < "$OUT/granted.tsv") learner-class tiers)"
echo OK > "$OUT/STATUS"
echo "CC REGRADE DONE ($OUT)"
