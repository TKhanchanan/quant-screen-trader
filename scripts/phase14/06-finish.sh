#!/usr/bin/env bash
# Phase 14 finish. Refuses (exit 1, nothing sent) until qualified capture reaches
# 24h total, 23h CapitalBear and 23h IQ Option. Then, with observation stopped on both platforms:
# checkpoint → storage verification → final state exported to .runtime/ (a local file).
# It never edits evidence, never commits anything and never marks Phase 14 closed.
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

case "${1:-}" in
  -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1"; exit 64 ;;
esac

title "PHASE 14 FINISH"
operator gates
case $? in
  0) pass "Duration targets reached." ;;
  1)
    fail "Duration targets NOT reached. Keep the soak running; nothing was finalized."
    info "Do not stop at 24 wall-clock hours: stop only when all three lines above are met."
    exit 1 ;;
  *) fail "Could not read the recorder state."; exit 1 ;;
esac

if ! operator capture-stopped; then
  echo
  fail "Stop observation in BOTH workspaces first (Stop observation button), wait 10 seconds, then run this again."
  exit 1
fi
if ! ask_yes_no "Observation is stopped on both platforms and you want to finalize this run now?"; then
  warn "Nothing finalized."
  exit 1
fi

title "Checkpoint"
operator checkpoint || { fail "Checkpoint failed."; exit 1; }

title "Storage verification (may take several minutes; the engine is busy meanwhile)"
operator verify-storage || warn "Storage is not verified. The final state below says why."

title "Final state"
EXPORT="$(operator export)" || { fail "Could not export the final state."; exit 1; }
FINAL_FILE="$(printf '%s\n' "$EXPORT" | head -n 1)"
printf '%s\n' "$EXPORT" | sed 's/^/  /'
info "Keep this file exactly as written. Do not edit it."
echo
if operator readiness --file "$FINAL_FILE"; then
  echo
  echo "  Give $FINAL_FILE and the output of this script to the reviewer for the final Phase 14 review."
  echo "  Phase 14 stays OPEN until that review; do not change README or docs/evidence yourself."
  exit 0
fi
echo
info "The run is not ready. Keep this file for diagnosis; see the runbook troubleshooting table."
exit 1
