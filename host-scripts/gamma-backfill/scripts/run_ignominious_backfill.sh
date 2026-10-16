#!/usr/bin/env bash
# Incompletionist + Watchlister backfill (manual "Ignominious!" badges, -10,000 points each).
# The lists are computed at run time from this box's own LMS (ig_extract.sh, SELECT only, then
# ignominious.py; definitions decided 2026-10-01): Incompletionist = enrolled over a year ago in a
# class they started and haven't finished; Watchlister = 5+ classes enrolled in and never started.
# DRY RUN by default: prints the lists' sizes and grant_manual_badge.sh's would-grant counts.
# APPLY=1 grants both in PARALLEL learner shards, then verifies: each learner's points changed by
# exactly the points of their new Ignominious grants, and a second run grants nothing.
# Usage: CONFIRM_HOST=<LMS_HOST> [APPLY=1] [PARALLEL=8] bash ~/gamma-backfill/run_ignominious_backfill.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"; [ -n "${OUT:-}" ] && echo FAILED > "$OUT/STATUS"' ERR
cd "$(dirname "$0")"
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
PARALLEL=${PARALLEL:-8}
if pgrep -af '[g]amma_reset_to_mirror|[r]un_backfill_fast|[i]mport_backfill_events|[r]ecompute_backfill|[r]un_course_completion' >/dev/null; then
  echo "refusing: another backfill is running"; exit 1
fi
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N --default-character-set=utf8mb4 "$@"; }
TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=exports/ig-backfill-$TS; mkdir -p "$OUT"
BADGES="Incompletionist|incompletionist Watchlister|watchlister"

echo "== lists (read-only)"
bash ig_extract.sh "$OUT/extract" > "$OUT/extract.log"
python3 ignominious.py "$OUT/extract" "$OUT" | tee "$OUT/report.txt"
for b in $BADGES; do
  CONFIRM_HOST="$HOST" DRY_RUN=1 PARALLEL="$PARALLEL" bash grant_manual_badge.sh "${b%%|*}" "$OUT/${b#*|}.txt" | tail -1
done
if [ "${APPLY:-0}" != 1 ]; then echo OK > "$OUT/STATUS"; echo "DRY RUN DONE ($OUT): nothing written"; exit 0; fi

echo "== grant ($PARALLEL shards)"
T0=$(sql -e "SELECT UTC_TIMESTAMP(6)" </dev/null)
sql -e "SELECT user_uid, points FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/points-before.tsv"
for b in $BADGES; do
  s=$(date +%s)
  CONFIRM_HOST="$HOST" PARALLEL="$PARALLEL" SKIP_POINTS_REFRESH=1 bash grant_manual_badge.sh "${b%%|*}" "$OUT/${b#*|}.txt" | tail -1
  echo "   ($(( $(date +%s) - s ))s)"
done
echo "== verify"
sql -e "SELECT user_uid, points FROM rgg.users_gammauser ORDER BY user_uid" </dev/null > "$OUT/points-after.tsv"
sql -e "SELECT u.user_uid, a.completion_points_paid FROM rgg.achievements_achievement a
  JOIN rgg.users_gammauser u ON u.id = a.user_id JOIN rgg.badges_badge b ON b.id = a.object_id
  JOIN rgg.django_content_type ct ON ct.id = a.content_type_id AND ct.app_label = 'badges' AND ct.model = 'badge'
  WHERE b.title IN ('Incompletionist', 'Watchlister') AND a.completed_at >= '$T0'" </dev/null > "$OUT/new-grants.tsv"
python3 - "$OUT" <<'PY' | tee "$OUT/verify.txt"
import collections, sys
d = sys.argv[1]
rd = lambda f: dict(line.rstrip('\n').split('\t') for line in open(f) if line.strip())
before, after = rd(d + '/points-before.tsv'), rd(d + '/points-after.tsv')
exp = collections.Counter()
for line in open(d + '/new-grants.tsv'):
    if line.strip():
        user, paid = line.rstrip('\n').split('\t')
        exp[user] += int(paid)
bad = sorted(u for u in set(before) | set(after) if int(after.get(u, 0)) - int(before.get(u, 0)) != exp[u])
print('new grants %d to %d learners; points %+d (expected %+d); learners whose change differs: %d %s' % (
    sum(1 for line in open(d + '/new-grants.tsv') if line.strip()), len(exp),
    sum(int(v) for v in after.values()) - sum(int(v) for v in before.values()), sum(exp.values()), len(bad), bad[:5]))
print('IG_VERIFY', 'OK' if not bad else 'MISMATCH')
PY
grep -q "IG_VERIFY OK" "$OUT/verify.txt" || { echo FAILED > "$OUT/STATUS"; exit 1; }
echo "== second run: nothing may be granted"
for b in $BADGES; do
  line=$(CONFIRM_HOST="$HOST" PARALLEL="$PARALLEL" bash grant_manual_badge.sh "${b%%|*}" "$OUT/${b#*|}.txt" | tail -1)
  echo "$line"
  echo "$line" | grep -q "granted 0," || { echo "verification failed: re-run granted more"; echo FAILED > "$OUT/STATUS"; exit 1; }
done
echo OK > "$OUT/STATUS"
echo "IG BACKFILL DONE ($OUT)"
