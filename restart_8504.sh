#!/bin/bash
# Restart the 8504 MAZI DEX WORKBENCH (detached uvicorn daemon, no supervisor).
# Captures the exact launch recipe verified from the live process on 2026-07-05:
#   cwd  = /Users/stavrosaim4/mazidex-admin
#   cmd  = ./venv/bin/uvicorn app:app --host 0.0.0.0 --port 8504
#   env  = .env.mazi (MAZI_DB_URL, GEMINI_API_KEY) + the review-write gate flags below.
# The write-gate flags MUST be exported at launch or APPROVE fails closed (403/503).
#
# Server-side trusted gate: MAZIDEX_TRUSTED_GATE_ENFORCE defaults ON in promotion.py
# (hard-blocks non-single / binding-mismatch promotions). Export =0 here ONLY to fall
# back to log-only mode.
set -uo pipefail
cd /Users/stavrosaim4/mazidex-admin

OLD_PID="$(pgrep -f 'uvicorn app:app --host 0.0.0.0 --port 8504' || true)"
if [ -n "$OLD_PID" ]; then
  echo "stopping old 8504 daemon pid=$OLD_PID"
  kill "$OLD_PID"
  for _ in $(seq 1 20); do kill -0 "$OLD_PID" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$OLD_PID" 2>/dev/null && { echo "old daemon did not exit"; exit 1; }
fi

set -a
[ -f .env.mazi ] && . ./.env.mazi
set +a
export MAZIDEX_ADMIN_REVIEW_WRITE_ENABLED=1
export MAZIDEX_ADMIN_REVIEW_WRITE_SCOPE_IDENTIFIED_ALL=1
export MAZIDEX_ADMIN_ROW_ACTIONS_WRITE_ENABLED=1

mkdir -p "$HOME/Library/Logs/mazi"
nohup ./venv/bin/uvicorn app:app --host 0.0.0.0 --port 8504 \
  >>"$HOME/Library/Logs/mazi/mazidex_admin_8504.log" 2>&1 &
NEW_PID=$!
disown
sleep 3
if kill -0 "$NEW_PID" 2>/dev/null; then
  echo "8504 restarted pid=$NEW_PID"
  curl -s -o /dev/null -w "health: HTTP %{http_code}\n" "http://127.0.0.1:8504/" || true
else
  echo "FAILED to start 8504 — check $HOME/Library/Logs/mazi/mazidex_admin_8504.log"
  exit 1
fi
