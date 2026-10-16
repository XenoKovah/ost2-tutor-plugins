#!/usr/bin/env bash
# Daily flag-only anti-gaming run (decided 2026-10-02; turn it on for p once RGG launches).
# `process_anti_gaming --mode flag` re-scores every (learner, allowlisted course) and keeps one
# AntiGamingPenalty row per confirmed pair. Flag mode never touches points. The report lists the totals,
# the courses, and the learners newly flagged or cleared since the previous run. It is written to
# ~/anti-gaming/<date>.txt and, with MAIL_TO set, mailed through the LMS's own email (on dev that lands in
# dev's mail files, never in an inbox). A run with "Newly flagged: 0" is not mailed; the file is still written.
# Engagement is judged from gamma's events, which have almost no video data, so a flag is a lead for
# review, not proof. The Speed-runner! badge comes only from the LMS-based run_speed_runner_backfill.sh.
# Usage (cron): CONFIRM_HOST=<LMS_HOST> [MAIL_TO=xeno@ost2.fyi] bash ~/gamma-backfill/anti_gaming_daily.sh
set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO"' ERR
TUTOR=$HOME/tutor-venv/bin/tutor
HOST=$($TUTOR config printvalue LMS_HOST)
[ "${CONFIRM_HOST:-}" = "$HOST" ] || { echo "refusing: set CONFIRM_HOST=$HOST to run on this box"; exit 1; }
if pgrep -af '[v]alidate_all|[g]amma_reset_to_mirror|[r]un_backfill_fast|[r]ecompute_backfill|[r]un_course_completion|[r]un_speed_runner|[r]un_multi_class|[r]un_points_refresh|[r]un_ignominious' >/dev/null; then
  echo "refusing: a backfill is running"; exit 1
fi
export MYSQL_PWD=$($TUTOR config printvalue MYSQL_ROOT_PASSWORD)
sql(){ docker exec -i -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N --default-character-set=utf8mb4 rgg "$@"; }
RGG=tutor_local-rgg-1
DIR=$HOME/anti-gaming; mkdir -p "$DIR"
DAY=$(date -u +%F)
flags(){ sql -e "SELECT u.user_uid, p.course_id, p.rushed_blocks, p.longest_run FROM badges_antigamingpenalty p
                 JOIN users_gammauser u ON u.id = p.user_id ORDER BY 1, 2" </dev/null; }
flags > "$DIR/flags-before.tsv"
s=$(date +%s)
docker exec -e CELERY_RESULT_BACKEND=disabled "$RGG" sh -lc 'cd /rgg/gamma && /rgg/venv/bin/python manage.py process_anti_gaming --mode flag --force' > "$DIR/$DAY.run.log" 2>&1
took=$(( $(date +%s) - s ))
flags > "$DIR/flags-after.tsv"
python3 - "$DIR" "$DAY" "$HOST" "$took" <<'PY' > "$DIR/$DAY.txt"
import collections, sys
d, day, host, took = sys.argv[1:5]
rd = lambda f: {(r[0], r[1]): r for r in (line.rstrip('\n').split('\t') for line in open(f) if line.strip())}
before, after = rd(d + '/flags-before.tsv'), rd(d + '/flags-after.tsv')
new, cleared = sorted(set(after) - set(before)), sorted(set(before) - set(after))
print('OST2 anti-gaming daily report, %s (%s), flag mode: no points were changed. Run took %ss.' % (day, host, took))
print('Flagged (learner, course) pairs: %d, %d learners. Newly flagged: %d. Cleared since the last run: %d.' % (
    len(after), len({u for u, _ in after}), len(new), len(cleared)))
print('Caveat: engagement comes from gamma events (almost no video data), so review a flag before acting on it.\n')
print('Per course (flagged pairs):')
for course, n in collections.Counter(c for _, c in after).most_common():
    print('  %4d  %s' % (n, course.split(':', 1)[-1]))
if new:
    print('\nNewly flagged (learner | course | rushed clicks | longest run), up to 100:')
    for k in new[:100]:
        r = after[k]
        print('  %s | %s | %s | %s' % (r[0], r[1].split(':', 1)[-1], r[2], r[3]))
if cleared:
    print('\nCleared (no longer rushing, or engaged since), up to 100:')
    for u, c in cleared[:100]:
        print('  %s | %s' % (u, c.split(':', 1)[-1]))
PY
mv "$DIR/flags-after.tsv" "$DIR/flags-$DAY.tsv"; rm -f "$DIR/flags-before.tsv"
grep -q "^done:" "$DIR/$DAY.run.log" || { echo "process_anti_gaming did not finish; see $DIR/$DAY.run.log"; exit 1; }
NEW=$(grep -oE 'Newly flagged: [0-9]+' "$DIR/$DAY.txt" | grep -oE '[0-9]+$')
if [ -n "${MAIL_TO:-}" ] && [ "${NEW:-0}" = 0 ]; then
  echo "newly flagged: 0, so not mailing"
elif [ -n "${MAIL_TO:-}" ]; then
  docker cp "$DIR/$DAY.txt" tutor_local-lms-1:/tmp/anti-gaming-report.txt
  docker exec -i -e MAIL_TO="$MAIL_TO" -e DAY="$DAY" -e HOST="$HOST" tutor_local-lms-1 sh -c \
    'cd /openedx/edx-platform && python manage.py lms shell' <<'PY' 2>&1 | grep -E "^MAILED|Error|Traceback" || true
import os
from django.conf import settings
from django.core.mail import send_mail
body = open('/tmp/anti-gaming-report.txt').read()
send_mail('OST2 anti-gaming report %s (%s)' % (os.environ['DAY'], os.environ['HOST']), body,
          settings.DEFAULT_FROM_EMAIL, [os.environ['MAIL_TO']], fail_silently=False)
print('MAILED', os.environ['MAIL_TO'])
PY
  docker exec tutor_local-lms-1 rm -f /tmp/anti-gaming-report.txt
fi
head -2 "$DIR/$DAY.txt"
