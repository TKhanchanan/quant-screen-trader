#!/usr/bin/env bash
# Phase 14 step 1: check this Mac and this checkout before the real soak starts.
#   ./scripts/phase14/01-preflight.sh                 full check, including lint/typecheck/test/build
#   ./scripts/phase14/01-preflight.sh --skip-checks   skip the (slow) project checks
# Read-only: it changes no broker, application or system setting.
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

SKIP_CHECKS=0
for arg in "$@"; do
  case "$arg" in
    --skip-checks) SKIP_CHECKS=1 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 64 ;;
  esac
done

FAILURES=0
WARNINGS=0
bad() { fail "$*"; FAILURES=$((FAILURES + 1)); }
soft() { warn "$*"; WARNINGS=$((WARNINGS + 1)); }

version_at_least() { # version_at_least 22.12.0 22.12
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -n 1)" = "$2" ]
}

title "PHASE 14 PREFLIGHT"
cd "$REPO_ROOT"

title "Code"
HEAD_SHA="$(current_head 2>/dev/null || true)"
if [ -z "$HEAD_SHA" ]; then
  bad "Not a git checkout: $REPO_ROOT"
else
  info "commit  $HEAD_SHA"
  info "branch  $(git rev-parse --abbrev-ref HEAD)"
  if worktree_clean; then pass "Working tree is clean"; else
    bad "Working tree has uncommitted or untracked changes (git status). The soak must run a committed build."
  fi
  if git rev-parse --verify --quiet origin/main >/dev/null; then
    if [ "$HEAD_SHA" = "$(git rev-parse origin/main)" ]; then pass "HEAD matches origin/main (as last fetched)"; else
      soft "HEAD differs from origin/main as last fetched. Run 'git fetch' and make sure you are on the intended commit."
    fi
  fi
fi

title "Tools"
if command -v node >/dev/null 2>&1; then
  NODE_VERSION="$(node -p 'process.versions.node')"
  if version_at_least "$NODE_VERSION" "22.12.0"; then pass "Node.js $NODE_VERSION"; else bad "Node.js $NODE_VERSION is older than 22.12"; fi
else
  bad "Node.js is not installed"
fi
if command -v npm >/dev/null 2>&1; then pass "npm $(npm --version)"; else bad "npm is not installed"; fi
PY_VERSION="$(node scripts/python.mjs -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
if [ -n "$PY_VERSION" ] && version_at_least "$PY_VERSION" "3.12.0"; then pass "Python $PY_VERSION"; else bad "Python 3.12+ not found (services/quant-engine/.venv)"; fi
if node scripts/python.mjs -c 'import duckdb, fastapi, uvicorn, quant_engine' >/dev/null 2>&1; then pass "Quant engine dependencies import"; else
  bad "Quant engine dependencies missing. Run: node scripts/python.mjs -m pip install -e \"services/quant-engine[dev]\""
fi
if [ -x node_modules/.bin/electron-vite ]; then pass "Node dependencies installed"; else bad "Node dependencies missing. Run: npm ci"; fi

title "This Mac"
if port_in_use; then
  bad "Port $QST_ENGINE_PORT is already in use. Quit QuantScreen Trader (or the process holding the port) first."
else
  pass "Port $QST_ENGINE_PORT is free"
fi
RUNNING="$(app_processes)"
if [ -n "$RUNNING" ]; then
  bad "Another QuantScreen Trader process is running:"
  printf '%s\n' "$RUNNING" | sed 's/^/        /'
else
  pass "No other QuantScreen Trader process"
fi
DATA_ROOT="${QST_DATA_DIR:-$HOME/Library/Application Support/QuantScreenTrader}"
if [ -n "${QST_DATA_DIR:-}" ]; then soft "QST_DATA_DIR is set in this shell ($QST_DATA_DIR). The soak should use the normal data root."; fi
DISK_PATH="$DATA_ROOT"; [ -d "$DISK_PATH" ] || DISK_PATH="$HOME"
FREE_KB="$(df -Pk "$DISK_PATH" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "$FREE_KB" ]; then
  FREE_GB=$((FREE_KB / 1024 / 1024))
  if [ "$FREE_GB" -lt 2 ]; then bad "Only ${FREE_GB} GB free on the data disk (need at least 5 GB)"
  elif [ "$FREE_GB" -lt 5 ]; then soft "Only ${FREE_GB} GB free on the data disk (5 GB or more recommended)"
  else pass "${FREE_GB} GB free on the data disk"; fi
fi
if command -v pmset >/dev/null 2>&1; then
  if pmset -g batt 2>/dev/null | grep -q "AC Power"; then pass "Running on AC power"; else soft "Not on AC power. Plug the Mac in for the whole run."; fi
  SLEEP_MIN="$(pmset -g 2>/dev/null | awk '$1 == "sleep" {print $2}' | head -n 1)"
  info "System sleep setting: ${SLEEP_MIN:-unknown} minute(s). 02-start.sh keeps the Mac awake with caffeinate while the app runs."
  if command -v caffeinate >/dev/null 2>&1; then pass "caffeinate is available"; else soft "caffeinate not found; disable sleep manually for the run"; fi
else
  soft "pmset not available (not macOS?). The Phase 14 soak is supported on macOS."
fi
if [ -f "$STATE_FILE" ]; then
  load_run_state || true
  soft "A previous run state exists (RUN_ID=${RUN_ID:-?}). 02-start.sh --new-run archives it before a new run."
fi

if [ "$SKIP_CHECKS" -eq 0 ]; then
  title "Project checks (the same commands CI runs)"
  mkdir -p "$RUNTIME_DIR"
  for step in lint typecheck test build; do
    LOG="$RUNTIME_DIR/preflight-$step.log"
    printf '      running npm run %s … ' "$step"
    if npm run "$step" > "$LOG" 2>&1; then echo; pass "npm run $step"; else echo; bad "npm run $step (see $LOG)"; fi
  done
else
  title "Project checks"
  soft "Skipped (--skip-checks). Run the full preflight at least once on this commit."
fi

title "RESULT"
if [ "$FAILURES" -gt 0 ]; then
  fail "PREFLIGHT FAILED: $FAILURES problem(s), $WARNINGS warning(s). Fix the FAIL lines before starting."
  exit 1
fi
pass "PREFLIGHT PASSED with $WARNINGS warning(s). Commit $HEAD_SHA"
echo "      Next: ./scripts/phase14/02-start.sh"
