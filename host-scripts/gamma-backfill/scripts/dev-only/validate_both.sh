#!/usr/bin/env bash
# dev only: clean-state validation of the "Using The Site" + "Complete your profile" backfills.
set -u
cd "$HOME/gamma-backfill"
log(){ echo "$(date -u +%FT%TZ) $*" | tee -a validate.log; }
: > validate.log
mkdir -p old-runs; [ -f fast-backfill.log ] && mv fast-backfill.log "old-runs/fast-backfill.before-validate-$(date -u +%H%M%S).log"
log "reset gamma to the mirror"
bash "$HOME/p2dev-20260930/gamma_reset_to_mirror.sh" > validate-reset.log 2>&1 || { log "VALIDATION_FAILED reset"; exit 1; }
log "Using The Site"
CONFIRM_HOST=dev.ost2.fyi bash run_backfill_fast.sh "Using The Site" > validate-uts.out 2>&1 || { log "VALIDATION_FAILED uts"; exit 1; }
bash backfill_results.sh > results-validate-uts.txt
log "Complete your profile"
PAY_AWARDS=1 FEEDS=backfill_profile_events CONFIRM_HOST=dev.ost2.fyi bash run_backfill_fast.sh "Complete your profile" > validate-profile.out 2>&1 || { log "VALIDATION_FAILED profile"; exit 1; }
bash backfill_results.sh "Complete your profile" > results-validate-profile.txt
log "VALIDATION_DONE"
