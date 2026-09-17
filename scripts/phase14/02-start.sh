#!/usr/bin/env bash
# Phase 14 step 2: start the official real soak.
#   ./scripts/phase14/02-start.sh             start detached (closing Terminal does not stop the app)
#   ./scripts/phase14/02-start.sh --new-run   archive a previous run state file first
#   ./scripts/phase14/02-start.sh --dry-run   print what would be started, start nothing
# It sets QST_SHADOW_LIVE=1 and QST_COMMIT_SHA, never QST_DATA_DIR, and launches no AI agent.
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

NEW_RUN=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --new-run) NEW_RUN=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 64 ;;
  esac
done

title "PHASE 14 START"
cd "$REPO_ROOT"
TESTED_HEAD="$(current_head)"

if ! worktree_clean; then
  fail "Working tree has changes. Commit or discard them; the soak must run a committed build."
  exit 1
fi
if [ -f "$STATE_FILE" ]; then
  load_run_state || true
  if [ "$NEW_RUN" -eq 0 ]; then
    fail "A run state already exists: RUN_ID=${RUN_ID:-?} TESTED_HEAD=${TESTED_HEAD:-?}"
    info "To continue that run after a restart use:  ./scripts/phase14/05-restart.sh --resume"
    info "To abandon it and start a NEW run use:      ./scripts/phase14/02-start.sh --new-run"
    exit 1
  fi
  TESTED_HEAD="$(current_head)"
fi
if [ -n "${QST_DATA_DIR:-}" ]; then
  fail "QST_DATA_DIR is set in this shell ($QST_DATA_DIR). Open a new Terminal without it; the soak uses the normal data root."
  exit 1
fi

export QST_SHADOW_LIVE=1
export QST_COMMIT_SHA="$TESTED_HEAD"
unset QST_SHADOW_LIVE_RUN_ID

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN — nothing started"
  echo "QST_SHADOW_LIVE=$QST_SHADOW_LIVE"
  echo "QST_COMMIT_SHA=$QST_COMMIT_SHA"
  echo "QST_SHADOW_LIVE_RUN_ID=${QST_SHADOW_LIVE_RUN_ID:-<unset>}"
  echo "QST_DATA_DIR=${QST_DATA_DIR:-<unset>}"
  echo "command: npm run dev (detached, log $APP_LOG, caffeinate -dimsu while it runs)"
  exit 0
fi

if port_in_use; then
  fail "Port $QST_ENGINE_PORT is already in use. Quit QuantScreen Trader first (or run 01-preflight.sh)."
  exit 1
fi
if [ -n "$(app_processes)" ]; then
  fail "Another QuantScreen Trader process is running:"
  app_processes | sed 's/^/        /'
  exit 1
fi
if [ "$NEW_RUN" -eq 1 ] && [ -f "$STATE_FILE" ]; then
  mkdir -p "$RUNTIME_DIR/archive"
  mv "$STATE_FILE" "$RUNTIME_DIR/archive/phase14-run-$(date -u +%Y%m%dT%H%M%SZ)"
  info "Previous run state archived in .runtime/archive/"
fi

launch_app
info "Starting QuantScreen Trader (npm run dev), log: $APP_LOG"
info "Waiting for the quant engine and the Phase 14 recorder (first start can take a few minutes)…"
READY="$(operator wait-ready --timeout-ms 300000)" || {
  fail "The engine did not come up. Last lines of the app log:"
  tail -n 30 "$APP_LOG" | sed 's/^/        /'
  exit 1
}
set -- $READY
RUN_ID="$1"
RECORDED_COMMIT="$2"
if [ "$RECORDED_COMMIT" != "$TESTED_HEAD" ]; then
  fail "The recorder reports commit $RECORDED_COMMIT, expected $TESTED_HEAD. Quit the app; this run cannot be accepted."
  exit 1
fi
save_run_state "$RUN_ID" "$TESTED_HEAD"

title "PHASE 14 STARTED"
echo "  TESTED_HEAD  $TESTED_HEAD"
echo "  RUN_ID       $RUN_ID"
echo "  saved in     .runtime/phase14-current-run"
echo "  app log      .runtime/phase14-app.log"
echo "  keep awake   $( [ -n "$CAFFEINATE_PID" ] && echo "caffeinate running while the app runs" || echo "NOT ACTIVE — disable sleep manually")"
echo
echo "  Now, inside QuantScreen Trader (docs/phase14-operator-runbook.md, section 3):"
echo "    CapitalBear and IQ Option: log in, show the nine charts, Sync Assets, Probe Prices, Start observation."
echo "    Execution must stay OFF and disarmed in both workspaces."
echo
echo "  Check progress any time (read-only):  ./scripts/phase14/03-status.sh"
echo "  Checkpoint:                            ./scripts/phase14/04-checkpoint.sh"
echo
echo "  ${C_BOLD}Do NOT edit code, git pull, switch branches or start another copy while this run is active.${C_OFF}"
echo "  This Terminal can be closed; the app keeps running. Quit the app normally (Cmd+Q) only when a step says so."
