#!/usr/bin/env bash
# Phase 14 checkpoint: POST /api/shadow-live/checkpoint. Capture keeps running.
# Exit code 0 only when the engine wrote the checkpoint.
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

case "${1:-}" in
  -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1"; exit 64 ;;
esac
operator checkpoint
