#!/usr/bin/env bash
# The manual grants (BACKFILLS.md §3), in the p order: positive badges first, Beta Flake last. Same steps as
# dev-only/manual_grants_test.sh (validated on dev 2026-10-01/02) on any box, plus College Outreach (2026-10-02).
# For each: dry run, grant, re-run (must grant 0). Then per-learner points check:
# every learner's points change == sum of the points of the badges newly granted to them.
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
cd "$HOME/gamma-backfill"
HOST=$($HOME/tutor-venv/bin/tutor config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to grant on this box"; exit 1; }
TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=exports/manual-grants-$TS; mkdir -p "$OUT"
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
q(){ docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N rgg -e "$1" </dev/null; }
snap(){ q "SELECT user_uid, points FROM users_gammauser ORDER BY user_uid" > "$OUT/points-$1.tsv"; }
IDS=8,71,72,73,177,301,302,303,304,305,306,307,318,319,320,350   # the badges below, by id (the config import keeps dev's ids)
holders(){ q "SELECT b.title, COUNT(a.id) FROM badges_badge b LEFT JOIN achievements_achievement a ON a.object_id=b.id AND a.completed_at IS NOT NULL AND a.content_type_id=(SELECT id FROM django_content_type WHERE app_label='badges' AND model='badge') WHERE b.id IN ($IDS) GROUP BY b.id ORDER BY b.id"; }
T0=$(q "SELECT UTC_TIMESTAMP(6)")
echo "start $T0" | tee "$OUT/log.txt"
holders | sed 's/^/before\t/' | tee -a "$OUT/log.txt"
snap before
for spec in "Completed Beta Class|manual/completed_beta_class.txt" "Subtitle Superhero|manual/subtitle_superhero.txt" \
            "OST2 Board Member|manual/ost2_board_member.txt" "Social Media|manual/social_media.txt" \
            "Aesthetic Improver|manual/aesthetic_improver.txt" "College Outreach|manual/college_outreach.txt" \
            "Instructor|manual/instructor.txt" \
            "Instructor (6 hours of class material)|manual/instructor_6h.txt" "Instructor (8 hours of class material)|manual/instructor_8h.txt" \
            "Instructor (13 hours of class material)|manual/instructor_13h.txt" "Instructor (16 hours of class material)|manual/instructor_16h.txt" \
            "Instructor (31 hours of class material)|manual/instructor_31h.txt" "Instructor (40 hours of class material)|manual/instructor_40h.txt" \
            "Instructor (154 hours of class material)|manual/instructor_154h.txt" "Completionist|manual/completionist.txt" \
            "Beta Flake|manual/beta_flake.txt"; do
  title=${spec%%|*}; list=${spec#*|}
  for mode in dry grant rerun; do
    echo "== $title ($list): $mode" | tee -a "$OUT/log.txt"
    if [ $mode = dry ]; then
      CONFIRM_HOST=$HOST DRY_RUN=1 bash grant_manual_badge.sh "$title" "$list" 2>&1 | tee -a "$OUT/log.txt"
    else
      s=$(date +%s); CONFIRM_HOST=$HOST bash grant_manual_badge.sh "$title" "$list" 2>&1 | tee -a "$OUT/log.txt"; echo "   ($(( $(date +%s) - s ))s)" | tee -a "$OUT/log.txt"
    fi
  done
done
snap after
holders | sed 's/^/after\t/' | tee -a "$OUT/log.txt"
q "SELECT u.user_uid, b.points FROM achievements_achievement a JOIN users_gammauser u ON u.id=a.user_id JOIN badges_badge b ON b.id=a.object_id
   WHERE a.content_type_id=(SELECT id FROM django_content_type WHERE app_label='badges' AND model='badge') AND a.completed_at >= '$T0' AND b.id IN ($IDS)" > "$OUT/new-grants.tsv"
python3 - "$OUT" <<'PY' | tee -a "$OUT/log.txt"
import sys, collections
d = sys.argv[1]
rd = lambda f: dict(l.rstrip('\n').split('\t') for l in open('%s/%s' % (d, f)) if l.strip())
b, a = rd('points-before.tsv'), rd('points-after.tsv')
exp = collections.Counter()
for l in open(d + '/new-grants.tsv'):
    if l.strip():
        u, p = l.rstrip('\n').split('\t'); exp[u] += int(p)
bad = [(u, int(a[u]) - int(b.get(u, 0)), exp[u]) for u in set(a) | set(b) if int(a.get(u, 0)) - int(b.get(u, 0)) != exp[u]]
print('new grants %d to %d learners; expected points change %+d; actual %+d; learners whose change != their new badges: %d %s' % (
    sum(1 for l in open(d + '/new-grants.tsv') if l.strip()), len(exp), sum(exp.values()),
    sum(int(v) for v in a.values()) - sum(int(v) for v in b.values()), len(bad), bad[:5]))
neg = [u for u, p in a.items() if int(p) < 0]
print('learners with negative points after: %d (before: %d)' % (len(neg), sum(1 for p in b.values() if int(p) < 0)))
print('POINTS_CHECK', 'OK' if not bad else 'MISMATCH')
PY
echo "MANUAL_GRANTS_DONE $OUT" | tee -a "$OUT/log.txt"
