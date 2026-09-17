#!/usr/bin/env bash
# Phase 14 status dashboard. Read-only: it only reads GET /api/shadow-live/state.
#   ./scripts/phase14/03-status.sh           dashboard (samples twice, 3 s apart, to show progress)
#   ./scripts/phase14/03-status.sh --once    one sample
#   ./scripts/phase14/03-status.sh --json    raw recorder state
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

ARGS=()
for arg in "$@"; do
  case "$arg" in
    --once|--json) ARGS+=("$arg") ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 64 ;;
  esac
done

if load_run_state && [ "$(current_head 2>/dev/null)" != "$TESTED_HEAD" ]; then
  warn "The checkout is now at $(current_head), not TESTED_HEAD $TESTED_HEAD. Do not change code during the run."
fi
operator status ${ARGS[@]+"${ARGS[@]}"}
