#!/usr/bin/env bash
# dev only: clean-state validation of every backfill in BACKFILLS.md, in the order they run on p:
# reset gamma to the 2026-09-30 mirror (without re-applying any manual badge a backfill grants), then
# 1 Using The Site -> 2 Complete your profile -> 3 manual lists + Ignominious! -> 4 Continuous Learning
# -> 5 Course Completion tiers -> 6 Speed-runner! -> 8 Multi-class Accomplishments -> 7 Points points points! (last).
# FROM=N resumes at step N: 0 reset, 1-6 = sections 1-6, 7 = section 8, 8 = section 7.
set -u
cd "$HOME/gamma-backfill"
log(){ echo "$(date -u +%FT%TZ) $*" | tee -a validate-all.log; }
FROM=${FROM:-0}  # resume at step N after a failure (0 = from the reset); the log is then appended
[ "$FROM" = 0 ] && : > validate-all.log
run(){ [ "$FROM" -le "$1" ]; }
export MYSQL_PWD=$($HOME/tutor-venv/bin/tutor config printvalue MYSQL_ROOT_PASSWORD)
IDS=$(docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N rgg -e "SELECT GROUP_CONCAT(id ORDER BY id) FROM badges_badge WHERE title IN ('Completed Beta Class','Subtitle Superhero','OST2 Board Member','Social Media','Aesthetic Improver','Beta Flake','Incompletionist','Watchlister','Speed-runner!') OR category = 'Manually-assigned Uber-accomplishments'" </dev/null)
if run 0; then
log "reset gamma to the mirror (not re-applying manual badges $IDS)"
SKIP_MANUAL_BADGE_IDS=$IDS bash "$HOME/p2dev-20260930/gamma_reset_to_mirror.sh" > validate-all-reset.log 2>&1 || { log "VALIDATION_FAILED reset"; exit 1; }
fi
if run 1; then
log "1 Using The Site"
CONFIRM_HOST=dev.ost2.fyi bash run_backfill_fast.sh "Using The Site" > validate-all-uts.out 2>&1 || { log "VALIDATION_FAILED uts"; exit 1; }
bash backfill_results.sh > results-all-uts.txt
fi
if run 2; then
log "2 Complete your profile"
PAY_AWARDS=1 FEEDS=backfill_profile_events CONFIRM_HOST=dev.ost2.fyi bash run_backfill_fast.sh "Complete your profile" > validate-all-profile.out 2>&1 || { log "VALIDATION_FAILED profile"; exit 1; }
bash backfill_results.sh "Complete your profile" > results-all-profile.txt
fi
if run 3; then
log "3 manual lists"
bash manual_grants_test.sh > validate-all-manual.out 2>&1 || { log "VALIDATION_FAILED manual"; exit 1; }
grep -q "POINTS_CHECK OK" validate-all-manual.out || { log "VALIDATION_FAILED manual points"; exit 1; }
fi
if run 3; then
log "3 Ignominious! (Incompletionist, Watchlister)"
CONFIRM_HOST=dev.ost2.fyi APPLY=1 bash run_ignominious_backfill.sh > validate-all-ig.out 2>&1 || { log "VALIDATION_FAILED ignominious"; exit 1; }
grep -q "IG BACKFILL DONE" validate-all-ig.out || { log "VALIDATION_FAILED ignominious"; exit 1; }
fi
if run 4; then
log "4 Continuous Learning"
CONFIRM_HOST=dev.ost2.fyi COMMIT=1 bash run_backfill_continuous_learning.sh > validate-all-cl.out 2>&1 || { log "VALIDATION_FAILED cl"; exit 1; }
fi
if run 5; then
log "5 Course Completion tiers"
TOTAL=$(docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N rgg -e "SELECT SUM(points) FROM users_gammauser" </dev/null)
CONFIRM_HOST=dev.ost2.fyi APPLY=1 EXPECTED_TOTAL_BEFORE=$TOTAL bash run_course_completion_regrade.sh > validate-all-cc.out 2>&1 || { log "VALIDATION_FAILED cc"; exit 1; }
fi
if run 6; then
log "6 Speed-runner!"
CONFIRM_HOST=dev.ost2.fyi APPLY=1 bash run_speed_runner_backfill.sh > validate-all-sr.out 2>&1 || { log "VALIDATION_FAILED speed-runner"; exit 1; }
grep -q "SR BACKFILL DONE" validate-all-sr.out || { log "VALIDATION_FAILED speed-runner"; exit 1; }
fi
if run 7; then
log "8 Multi-class Accomplishments (BACKFILLS §8)"
TOTAL=$(docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N rgg -e "SELECT SUM(points) FROM users_gammauser" </dev/null)
CONFIRM_HOST=dev.ost2.fyi APPLY=1 EXPECTED_TOTAL_BEFORE=$TOTAL bash run_multi_class_backfill.sh > validate-all-mc.out 2>&1 || { log "VALIDATION_FAILED multi-class"; exit 1; }
grep -q "MC BACKFILL DONE" validate-all-mc.out || { log "VALIDATION_FAILED multi-class"; exit 1; }
fi
if run 8; then
log "7 Points points points! (BACKFILLS §7, last)"
CONFIRM_HOST=dev.ost2.fyi bash run_points_refresh.sh > validate-all-points.out 2>&1 || { log "VALIDATION_FAILED points"; exit 1; }
fi
bash backfill_results.sh > results-all-final.txt
bash snapshot_state.sh snapshot-validate > /dev/null
PENDING=$(docker exec -e MYSQL_PWD tutor_local-mysql-1 mysql -uroot -N rgg -e "SELECT COUNT(*) FROM achievements_achievement WHERE completed_at IS NOT NULL AND notification_seen_at IS NULL" </dev/null)
log "badge toasts pending after the backfills: $PENDING (expected 0: every backfill grant is silent)"
log "VALIDATION_DONE"
