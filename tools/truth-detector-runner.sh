#!/usr/bin/env bash
# Launches the Mode-Two Detector service in a detached screen session.
# Matches the pattern used by vLLM pod runners. Safe to re-run — replaces any
# existing screen session of the same name.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SESSION="truth-detector"
HOST="${TRUTH_DETECTOR_HOST:-127.0.0.1}"
PORT="${TRUTH_DETECTOR_PORT:-8787}"
LOG="${TRUTH_DETECTOR_LOG:-$ROOT/memory/truth-alignment/service.log}"

mkdir -p "$(dirname "$LOG")"

if screen -list | grep -q "\.${SESSION}\b"; then
  echo "[truth-detector] existing session found — terminating" >&2
  screen -S "$SESSION" -X quit || true
  sleep 1
fi

cd "$ROOT"
CMD=(uvicorn tools.truth_detector.service:app --host "$HOST" --port "$PORT")

screen -dmS "$SESSION" bash -c "exec ${CMD[*]} >> '$LOG' 2>&1"
echo "[truth-detector] started on ${HOST}:${PORT} (screen: $SESSION, log: $LOG)"
