#!/usr/bin/env bash
# Speed-runner! backfill ("Ignominious!" badge, -10,000 points): learners who clicked "Mark as complete"
# through an allowlisted course without engaging (speed_runner.py; SPEED_RUNNER_BADGE_MEASUREMENT.md;
# decided 2026-10-01/02). Computed at run time from this box's own LMS (sr_extract.sh, SELECT only).
#   1. The badge, once per learner (grant_manual_badge.sh, PARALLEL shards).
#   2. Gold -> Plain: every (learner, class) they speed-ran and hold at Gold is re-filed as Plain and the
#      Gold-Plain difference clawed back (sr_apply.py, PARALLEL learner shards, row-locked).
# Run it after the Course Completion tiers (section 5) and before the points-badge refresh. Re-running
# section 5 afterwards would move the demoted learners back to Gold: run this again after it.
# DRY RUN by default: prints the selection and the would-grant counts.
# APPLY=1 applies, then verifies: each learner's points changed by exactly -(clawed back) plus the
# points of a new Speed-runner! grant; no demoted class is still held at Gold; a second run changes nothing.
# Usage: CONFIRM_HOST=<LMS_HOST> [APPLY=1] [PARALLEL=8] bash ~/gamma-backfill/run_speed_runner_backfill.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"; [ -n "${OUT:-}" ] && echo FAILED > "$OUT/STATUS"' ERR
cd "$(dirname "$0")"
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
PARALLEL=${PARALLEL:-8}
BADGE='Speed-runner!'
if pgrep -af '[g]amma_reset_to_mirror|[r]un_backfill_fast|[i]mport_backfill_events|[r]ecompute_backfill|[r]un_course_completion|[r]un_ignominious' >/dev/null; then
  echo "refusing: another backfill is running"; exit 1
fi
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N --default-character-set=utf8mb4 "$@"; }
RGG=tutor_local-rgg-1
TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=exports/sr-backfill-$TS; mkdir -p "$OUT"

select_round() {  # $1 = output dir: extract + select
  bash sr_extract.sh "$1/extract" > "$1/extract.log"
  python3 speed_runner.py "$1/extract" manual/beta_completion_pairs.tsv manual/beta_completion_decisions.tsv "$1" > "$1/report.txt"
}
pending_demotions() {  # $1 = output dir: rows of demotions.tsv whose learner still holds Gold
  python3 - "$1" <<'PY'
import csv, sys
d = sys.argv[1]
held = {(r['user_uid'], r['badge_id']) for r in csv.DictReader(open(d + '/extract/tiers_held.tsv'), delimiter='\t', quoting=csv.QUOTE_NONE)}
rows = list(csv.DictReader(open(d + '/demotions.tsv'), delimiter='\t'))
print(sum(1 for r in rows if (r['username'], r['gold_badge_id']) in held))
PY
}

echo "== selection (read-only)"
select_round "$OUT"
cat "$OUT/report.txt"
echo "demotions still to apply: $(pending_demotions "$OUT")"
CONFIRM_HOST="$HOST" DRY_RUN=1 PARALLEL="$PARALLEL" bash grant_manual_badge.sh "$BADGE" "$OUT/speed_runners.txt" | grep -A1 "^badge "
if [ "${APPLY:-0}" != 1 ]; then echo OK > "$OUT/STATUS"; echo "DRY RUN DONE ($OUT): nothing written"; exit 0; fi

echo "== apply"
T0=$(sql -e "SELECT UTC_TIMESTAMP(6)" </dev/null)
sql -e "SELECT user_uid, points FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/points-before.tsv"
# Badge first: its -10,000 lands before the demotion re-evaluates the points badges.
s=$(date +%s)
CONFIRM_HOST="$HOST" PARALLEL="$PARALLEL" SKIP_POINTS_REFRESH=1 bash grant_manual_badge.sh "$BADGE" "$OUT/speed_runners.txt" | grep -A1 "^badge "
echo "   ($(( $(date +%s) - s ))s)"
docker exec "$RGG" mkdir -p /tmp/srapply
docker cp "$OUT/demotions.tsv" "$RGG:/tmp/srapply/demotions.tsv"
docker cp sr_apply.py "$RGG:/tmp/srapply/sr_apply.py"
s=$(date +%s)
for k in $(seq 0 $((PARALLEL - 1))); do
  docker exec -e CELERY_RESULT_BACKEND=disabled -e APPLY=1 -e SHARD_K="$k" -e SHARD_N="$PARALLEL" -e RGG_SKIP_POINTS_REFRESH=true "$RGG" sh -c \
    'cd /rgg/gamma && /rgg/venv/bin/python manage.py shell < /tmp/srapply/sr_apply.py' > "$OUT/demote-$k.out" 2>&1 &
done
wait
if grep -qE "Traceback|Error" "$OUT"/demote-*.out || [ "$(cat "$OUT"/demote-*.out | grep -c '^SHARD ')" != "$PARALLEL" ]; then
  cat "$OUT"/demote-*.out; echo "a demotion shard failed"; exit 1
fi
cat "$OUT"/demote-*.out | grep '^DEMOTED|' | cut -d'|' -f2- | tr '|' '\t' > "$OUT/demoted.tsv"
cat "$OUT"/demote-*.out | grep '^SHARD ' | sed 's/^/   /'
echo "   demoted $(wc -l < "$OUT/demoted.tsv") ($(( $(date +%s) - s ))s)"
docker exec "$RGG" rm -rf /tmp/srapply

echo "== verify"
sql -e "SELECT user_uid, points FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/points-after.tsv"
sql -e "SELECT u.user_uid, a.completion_points_paid FROM rgg.achievements_achievement a
  JOIN rgg.users_gammauser u ON u.id = a.user_id JOIN rgg.badges_badge b ON b.id = a.object_id
  JOIN rgg.django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
  WHERE b.title = '$BADGE' AND a.completed_at >= '$T0'" </dev/null > "$OUT/new-grants.tsv"
python3 - "$OUT" <<'PY' | tee "$OUT/verify.txt"
import collections, sys
d = sys.argv[1]
rd = lambda f: dict(line.rstrip('\n').split('\t') for line in open(f) if line.strip())
before, after = rd(d + '/points-before.tsv'), rd(d + '/points-after.tsv')
exp = collections.Counter()
grants = [line.rstrip('\n').split('\t') for line in open(d + '/new-grants.tsv') if line.strip()]
for user, paid in grants:
    exp[user] += int(paid)
demoted = [line.rstrip('\n').split('\t') for line in open(d + '/demoted.tsv') if line.strip()]
for user, cls, claw, board in demoted:
    exp[user] -= int(claw)
bad = sorted(u for u in set(before) | set(after) if int(after.get(u, 0)) - int(before.get(u, 0)) != exp[u])
print('new %s grants %d; demotions %d (clawed back %d points, course boards %d); points %+d (expected %+d); learners whose change differs: %d %s' % (
    'Speed-runner!', len(grants), len(demoted), sum(int(r[2]) for r in demoted), sum(int(r[3]) for r in demoted),
    sum(int(v) for v in after.values()) - sum(int(v) for v in before.values()), sum(exp.values()), len(bad), bad[:5]))
print('SR_VERIFY', 'OK' if not bad else 'MISMATCH')
PY
grep -q "SR_VERIFY OK" "$OUT/verify.txt" || { echo FAILED > "$OUT/STATUS"; exit 1; }
echo "== second run: nothing may change"
mkdir -p "$OUT/rerun"
select_round "$OUT/rerun"
left=$(pending_demotions "$OUT/rerun")
line=$(CONFIRM_HOST="$HOST" DRY_RUN=1 PARALLEL="$PARALLEL" bash grant_manual_badge.sh "$BADGE" "$OUT/rerun/speed_runners.txt" | grep "^badge ")
echo "   demotions still to apply: $left"; echo "   $line"
cmp -s "$OUT/speed_runners.txt" "$OUT/rerun/speed_runners.txt" || { echo "verification failed: the selection changed on re-run"; echo FAILED > "$OUT/STATUS"; exit 1; }
[ "$left" = 0 ] || { echo "verification failed: Gold still held for demoted classes"; echo FAILED > "$OUT/STATUS"; exit 1; }
echo "$line" | grep -q "would grant 0," || { echo "verification failed: re-run would grant more"; echo FAILED > "$OUT/STATUS"; exit 1; }
echo OK > "$OUT/STATUS"
echo "SR BACKFILL DONE ($OUT)"
