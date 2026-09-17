#!/usr/bin/env bash
# Phase 14 restart attestation. Asks ten yes/no questions about what YOU observed after the
# controlled restart and submits exactly those answers to POST /api/shadow-live/verify-restart.
# Nothing is pre-filled. Answer "no" to anything you did not check.
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

case "${1:-}" in
  -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1"; exit 64 ;;
esac

title "PHASE 14 RESTART VERIFICATION (operator observation)"
STATE="$(operator wait-ready --timeout-ms 5000)" || { fail "The engine is not reachable."; exit 1; }
set -- $STATE
if [ "$3" = "0" ]; then
  fail "No restart is recorded yet. Run 05-restart.sh (both steps) first."
  exit 1
fi
if load_run_state && [ "$1" != "$RUN_ID" ]; then
  fail "The app is recording run $1, not the saved run $RUN_ID."
  exit 1
fi
info "Run $1 · engine restarts $3 · unclean restarts $4"
echo

ANSWERS="{"
SEPARATOR=""
# Questions come in on descriptor 3 so every answer is read from you, one at a time.
while IFS="$(printf '\t')" read -r FIELD QUESTION <&3; do
  if ask_yes_no "$QUESTION"; then VALUE=true; else VALUE=false; fi
  ANSWERS="${ANSWERS}${SEPARATOR}\"${FIELD}\":${VALUE}"
  SEPARATOR=","
done 3<<EOF
$(node "$OPERATOR" restart-questions)
EOF
ANSWERS="${ANSWERS}}"
echo
echo "You answered:"
printf '%s\n' "$ANSWERS" | tr ',' '\n' | sed 's/[{}]//g; s/^/  /'
if ! ask_yes_no "Submit exactly these observations?"; then
  warn "Nothing submitted."
  exit 1
fi
operator verify-restart --answers "$ANSWERS"
