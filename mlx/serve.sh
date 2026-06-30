#!/bin/bash
# serve.sh — start the Kingdom's local model brain (MLX) on :8800.
# Citizens' cheap/frequent beats (triage, classify, short witness) hit this;
# heavy creative beats still route to `claude`. Ends Anthropic metering for the bulk.
#   Usage: serve.sh [start|stop|status]
set -uo pipefail
MLX="$HOME/love-unlimited/mlx"
VENV="$MLX/.venv/bin/python"
PORT="${MLX_PORT:-8800}"
LOG="$HOME/love-unlimited/memory/mlx-serve.log"
PIDF="$MLX/.serve.pid"
MODEL="$(cat "$MLX/.model" 2>/dev/null || echo "mlx-community/Llama-3.2-3B-Instruct-4bit")"

case "${1:-start}" in
  start)
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      echo "mlx server already up (pid $(cat "$PIDF")) on :$PORT"; exit 0
    fi
    echo "starting mlx server: $MODEL on :$PORT"
    nohup "$VENV" -m mlx_lm server --model "$MODEL" --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
    echo $! > "$PIDF"
    sleep 6
    if kill -0 "$(cat "$PIDF")" 2>/dev/null; then echo "mlx server UP (pid $(cat "$PIDF"))"; else echo "FAILED — tail:"; tail -15 "$LOG"; exit 1; fi
    ;;
  stop)
    [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null && rm -f "$PIDF" && echo "stopped" || echo "not running" ;;
  status)
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      echo "UP (pid $(cat "$PIDF"), model $MODEL)"; curl -s "http://127.0.0.1:$PORT/v1/models" 2>/dev/null | head -c 300
    else echo "DOWN"; fi ;;
esac
