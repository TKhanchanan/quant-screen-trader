#!/usr/bin/env bash
# Phase 14 Auto Sync review. Use it only when 03-status.sh shows "REVIEW PENDING".
# You compare each workspace's slot assets with the instruments the broker charts really show,
# then type how many of the applied Auto Sync changes were unexpected. Nothing is assumed.
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

case "${1:-}" in
  -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1"; exit 64 ;;
esac

title "PHASE 14 AUTO SYNC REVIEW (operator observation)"
PENDING="$(operator auto-sync-pending)" || { fail "The engine is not reachable."; exit 1; }
REVIEWED=0
while IFS="$(printf '\t')" read -r PLATFORM LABEL APPLIED REVIEW ASSETS <&3; do
  [ -n "$PLATFORM" ] || continue
  if [ "$APPLIED" = "0" ]; then
    info "$LABEL: no Auto Sync change applied — nothing to review."
    continue
  fi
  if [ "$REVIEW" = "OK" ]; then
    info "$LABEL: $APPLIED applied change(s) already reviewed."
    continue
  fi
  echo
  echo "$LABEL: Auto Sync applied $APPLIED change(s) during this run."
  echo "  Slot assets the app currently has:"
  printf '%s\n' "$ASSETS" | tr ';' '\n' | sed 's/^/    /'
  echo "  Look at the $LABEL workspace now and compare every slot with the instrument its chart really shows."
  while true; do
    printf 'How many of the %s applied change(s) were UNEXPECTED (did not match the real chart)? Type a number 0-%s: ' "$APPLIED" "$APPLIED"
    IFS= read -r UNEXPECTED || { echo; fail "No answer received."; exit 1; }
    case "$UNEXPECTED" in
      ''|*[!0-9]*) echo "Please type a whole number." ;;
      *) if [ "$UNEXPECTED" -le "$APPLIED" ]; then break; fi; echo "The number cannot be larger than $APPLIED." ;;
    esac
  done
  if ask_yes_no "Submit for $LABEL: reviewed $APPLIED change(s), $UNEXPECTED unexpected?"; then
    operator verify-auto-sync --platform "$PLATFORM" --reviewed "$APPLIED" --unexpected "$UNEXPECTED" || exit 1
    REVIEWED=$((REVIEWED + 1))
    if [ "$UNEXPECTED" -gt 0 ]; then fail "$LABEL: unexpected Auto Sync changes fail this run (see the runbook)."; fi
  else
    warn "$LABEL: nothing submitted."
  fi
done 3<<EOF
$PENDING
EOF
echo
info "Reviews submitted: $REVIEWED. Check with ./scripts/phase14/03-status.sh"
