#!/usr/bin/env bash
# Phase 14 controlled restart, in two steps.
#   ./scripts/phase14/05-restart.sh            checkpoint, then tells you how to quit and waits
#   ./scripts/phase14/05-restart.sh --resume   relaunch the SAME run on the SAME commit
# It never submits the restart attestation. After checking the app yourself, run verify-restart.sh.
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

RESUME=0
case "${1:-}" in
  --resume) RESUME=1 ;;
  -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1"; exit 64 ;;
esac

if ! load_run_state; then
  fail "No run state in .runtime/phase14-current-run. Start a run with 02-start.sh first."
  exit 1
fi

if [ "$RESUME" -eq 0 ]; then
  title "PHASE 14 CONTROLLED RESTART — step 1 of 2"
  STATE="$(operator run-id)" || { fail "The engine is not reachable; nothing to restart."; exit 1; }
  set -- $STATE
  if [ "$1" != "$RUN_ID" ]; then
    fail "The running app records run $1, but the saved run is $RUN_ID. Do not continue; see the runbook."
    exit 1
  fi
  operator checkpoint || { fail "Checkpoint failed; do not quit yet."; exit 1; }
  echo
  echo "  RUN_ID       $RUN_ID"
  echo "  TESTED_HEAD  $TESTED_HEAD"
  echo
  echo "  1. In BOTH workspaces press Stop observation."
  echo "  2. Quit QuantScreen Trader normally (QuantScreen Trader menu → Quit, or Cmd+Q)."
  echo "  3. Wait here: this script confirms the app and its engine are fully closed."
  echo
  for _ in $(seq 1 180); do
    if ! port_in_use && [ -z "$(app_processes)" ]; then
      pass "The app and its engine are fully closed (port $QST_ENGINE_PORT free, no leftover process)."
      echo "      Next: ./scripts/phase14/05-restart.sh --resume"
      exit 0
    fi
    sleep 5
  done
  fail "Still running after 15 minutes:"
  app_processes | sed 's/^/        /'
  echo "      Quit the app normally. If an engine process is left over, that is an orphan engine: note it."
  exit 1
fi

title "PHASE 14 CONTROLLED RESTART — step 2 of 2 (resume)"
cd "$REPO_ROOT"
HEAD_SHA="$(current_head)"
if [ "$HEAD_SHA" != "$TESTED_HEAD" ]; then
  fail "Current commit $HEAD_SHA is not TESTED_HEAD $TESTED_HEAD."
  info "A different commit cannot continue this run (the recorder refuses it). Check out $TESTED_HEAD."
  exit 1
fi
if ! worktree_clean; then
  fail "Working tree has changes. The resumed run must be the same committed build."
  exit 1
fi
if port_in_use || [ -n "$(app_processes)" ]; then
  fail "The previous app or engine is still running (orphan engine?). Close it before resuming:"
  app_processes | sed 's/^/        /'
  exit 1
fi
pass "Same commit, clean tree, no orphan engine"

export QST_SHADOW_LIVE=1
export QST_COMMIT_SHA="$TESTED_HEAD"
export QST_SHADOW_LIVE_RUN_ID="$RUN_ID"
launch_app
info "Relaunching with QST_SHADOW_LIVE_RUN_ID=$RUN_ID (log .runtime/phase14-app.log)…"
READY="$(operator wait-ready --timeout-ms 300000)" || {
  fail "The engine did not come up. Last lines of the app log:"
  tail -n 30 "$APP_LOG" | sed 's/^/        /'
  exit 1
}
set -- $READY
if [ "$1" != "$RUN_ID" ]; then
  fail "The app started run $1 instead of $RUN_ID. Quit it; see the runbook (restart created a new runId)."
  exit 1
fi
if [ "$2" != "$TESTED_HEAD" ]; then
  fail "The recorder reports commit $2, expected $TESTED_HEAD."
  exit 1
fi
pass "Run $RUN_ID continued on $TESTED_HEAD (engine restarts: $3, unclean: $4)"
if [ "$4" != "0" ]; then warn "An unclean restart is recorded; this run cannot be accepted. See the runbook."; fi
if [ "$3" != "1" ]; then warn "The recorder needs exactly ONE controlled restart; this run now has $3."; fi
echo
echo "  Now, in the app (do not submit anything yet):"
echo "    • both broker browsers still logged in?   • Asset Setup unchanged?   • calibration unchanged?"
echo "    • asset presets present?                  • execution still OFF / disarmed?"
echo "  Then Sync Assets if asked, Probe Prices, Start observation on both platforms,"
echo "  and after checking everything run:  ./scripts/phase14/verify-restart.sh"
echo
operator status --once || true
