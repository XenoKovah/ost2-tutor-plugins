#!/usr/bin/env bash
# Run one backfill phase unattended (under tmux), so a dropped ssh connection can't kill it:
# executes ~/gamma-backfill/p-run/NAME.sh from ~/gamma-backfill, logs to p-run/NAME.log, and leaves
# p-run/NAME.OK or p-run/NAME.FAIL (with the exit code) when it ends; p-run/STATUS keeps the history.
# Usage: tmux new-session -d -s NAME "bash ~/gamma-backfill/run_phase.sh NAME"
set -u
D=$HOME/gamma-backfill/p-run
NAME=${1:?usage: $0 NAME}
[ -f "$D/$NAME.sh" ] || { echo "no $D/$NAME.sh"; exit 1; }
rm -f "$D/$NAME.OK" "$D/$NAME.FAIL"
cd "$HOME/gamma-backfill"
echo "$(date -u +%FT%TZ) START $NAME" >> "$D/STATUS"
bash "$D/$NAME.sh" > "$D/$NAME.log" 2>&1; rc=$?
if [ "$rc" = 0 ]; then
  date -u +%FT%TZ > "$D/$NAME.OK"; echo "$(date -u +%FT%TZ) OK $NAME" >> "$D/STATUS"
else
  echo "rc=$rc $(date -u +%FT%TZ)" > "$D/$NAME.FAIL"; echo "$(date -u +%FT%TZ) FAIL $NAME rc=$rc" >> "$D/STATUS"
fi
