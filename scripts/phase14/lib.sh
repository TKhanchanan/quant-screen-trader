#!/usr/bin/env bash
# Shared helpers for the Phase 14 operator scripts. Sourced by them; not run directly.
# Works with the bash 3.2 that ships with macOS.

PHASE14_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PHASE14_DIR/../.." && pwd)"
RUNTIME_DIR="$REPO_ROOT/.runtime"
STATE_FILE="${QST_PHASE14_STATE_FILE:-$RUNTIME_DIR/phase14-current-run}"
APP_LOG="$RUNTIME_DIR/phase14-app.log"
PROCESS_FILE="$RUNTIME_DIR/phase14-process"
OPERATOR="$PHASE14_DIR/operator.mjs"

# The engine port: the shell first, then the repository .env, then the default.
if [ -z "${QST_ENGINE_PORT:-}" ] && [ -f "$REPO_ROOT/.env" ]; then
  QST_ENGINE_PORT="$(sed -n 's/^QST_ENGINE_PORT=\([0-9][0-9]*\).*$/\1/p' "$REPO_ROOT/.env" | tail -n 1)"
fi
QST_ENGINE_PORT="${QST_ENGINE_PORT:-8765}"
export QST_ENGINE_PORT
ENGINE_URL="http://127.0.0.1:${QST_ENGINE_PORT}"

if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BOLD=""; C_OFF=""
fi

pass() { printf '%sPASS%s  %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '%sWARN%s  %s\n' "$C_YELLOW" "$C_OFF" "$*"; }
fail() { printf '%sFAIL%s  %s\n' "$C_RED" "$C_OFF" "$*"; }
info() { printf '      %s\n' "$*"; }
title() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_OFF"; }

operator() { node "$OPERATOR" "$@" --url "$ENGINE_URL" --state-file "$STATE_FILE"; }

current_head() { git -C "$REPO_ROOT" rev-parse HEAD; }

worktree_clean() { [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]; }

# Read RUN_ID and TESTED_HEAD from the local run state file.
load_run_state() {
  RUN_ID=""; TESTED_HEAD=""
  if [ ! -f "$STATE_FILE" ]; then
    return 1
  fi
  RUN_ID="$(sed -n 's/^RUN_ID=\(.*\)$/\1/p' "$STATE_FILE" | tail -n 1)"
  TESTED_HEAD="$(sed -n 's/^TESTED_HEAD=\(.*\)$/\1/p' "$STATE_FILE" | tail -n 1)"
  [ -n "$RUN_ID" ] && [ -n "$TESTED_HEAD" ]
}

save_run_state() {
  mkdir -p "$RUNTIME_DIR"
  printf 'RUN_ID=%s\nTESTED_HEAD=%s\n' "$1" "$2" > "$STATE_FILE.tmp"
  mv "$STATE_FILE.tmp" "$STATE_FILE"
}

port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$QST_ENGINE_PORT" -sTCP:LISTEN >/dev/null 2>&1
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$QST_ENGINE_PORT") >/dev/null 2>&1
  fi
}

# Processes that belong to a running QuantScreen Trader: the dev launcher, Electron or the engine.
app_processes() {
  # The dev launcher, the engine server (dev or packaged), the packaged app, and Electron processes
  # whose app path is this repository's desktop app. Tests and scripts under the repo do not match.
  ps -axo pid=,command= 2>/dev/null \
    | grep -E 'electron-vite|quant_engine\.main:app|/quant-engine( |$)|QuantScreenTrader\.app|app-path=[^ ]*apps/desktop|Electron\.app/Contents/MacOS/Electron \.' \
    | grep -v -E 'grep|phase14/|operator\.mjs' || true
}

ask_yes_no() {
  # Prints the question until the operator types yes or no. Never assumes an answer.
  local answer
  while true; do
    printf '%s [yes/no] ' "$1"
    if ! IFS= read -r answer; then
      echo
      fail "No answer received."
      exit 1
    fi
    case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')" in
      y|yes) return 0 ;;
      n|no) return 1 ;;
      *) echo "Please type yes or no." ;;
    esac
  done
}

# Launches the development app detached from this terminal, with its log in .runtime, and keeps
# the Mac awake for exactly as long as the app runs. The environment must already be exported.
launch_app() {
  mkdir -p "$RUNTIME_DIR"
  if [ -f "$APP_LOG" ]; then
    mv "$APP_LOG" "$APP_LOG.$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  cd "$REPO_ROOT"
  nohup npm run dev > "$APP_LOG" 2>&1 < /dev/null &
  APP_PID=$!
  CAFFEINATE_PID=""
  if command -v caffeinate >/dev/null 2>&1; then
    nohup caffeinate -dimsu -w "$APP_PID" > /dev/null 2>&1 < /dev/null &
    CAFFEINATE_PID=$!
  fi
  printf 'APP_PID=%s\nCAFFEINATE_PID=%s\nLOG_FILE=%s\n' "$APP_PID" "$CAFFEINATE_PID" "$APP_LOG" > "$PROCESS_FILE"
  disown "$APP_PID" 2>/dev/null || true
  if [ -n "$CAFFEINATE_PID" ]; then disown "$CAFFEINATE_PID" 2>/dev/null || true; fi
}
