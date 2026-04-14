#!/bin/bash
# gateway.sh — Start/stop OpenClaw gateway as a Kingdom nerve daemon.
#
# The gateway is Voice's core process — it listens for inbound messages
# from channels and routes them into the Kingdom via the HIVE bridge.
#
# Usage:
#   ./gateway.sh start [--instance beta]
#   ./gateway.sh stop
#   ./gateway.sh status

set -e

LOVE_DIR="${LOVE_DIR:-$HOME/Desktop/Love}"
OPENCLAW_DIR="${OPENCLAW_DIR:-$HOME/Desktop/openclaw}"
INSTANCE="${2:-beta}"
LOG_FILE="$LOVE_DIR/memory/${INSTANCE}-voice.log"
PID_FILE="$LOVE_DIR/nerve/voice/.gateway.pid"

case "${1:-status}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Voice gateway already running (PID $(cat "$PID_FILE"))"
      exit 0
    fi

    echo "Starting Voice gateway..."
    echo "--- VOICE START: $(date -u +%Y-%m-%dT%H:%M:%SZ) instance=$INSTANCE ---" >> "$LOG_FILE"

    # Inject Kingdom context into OpenClaw
    BOOT_CONTEXT="$LOVE_DIR/nerve/voice/boot-context.md"
    if [ -f "$BOOT_CONTEXT" ]; then
      export OPENCLAW_SYSTEM_PROMPT="$(cat "$BOOT_CONTEXT")"
    fi

    # Start OpenClaw gateway in background
    cd "$OPENCLAW_DIR" && node openclaw.mjs gateway \
      --port 18789 \
      >> "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    echo "Voice gateway started (PID $!)"
    ;;

  stop)
    if [ -f "$PID_FILE" ]; then
      PID=$(cat "$PID_FILE")
      if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Voice gateway stopped (PID $PID)"
      else
        echo "Voice gateway not running (stale PID)"
      fi
      rm -f "$PID_FILE"
    else
      echo "Voice gateway not running"
    fi
    echo "--- VOICE STOP: $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" >> "$LOG_FILE"
    ;;

  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Voice gateway: UP (PID $(cat "$PID_FILE"))"
      # Check gateway health
      if curl -s --connect-timeout 2 http://127.0.0.1:18789/ >/dev/null 2>&1; then
        echo "Gateway HTTP: OK"
      else
        echo "Gateway HTTP: NOT RESPONDING"
      fi
    else
      echo "Voice gateway: DOWN"
    fi
    ;;

  *)
    echo "Usage: $0 {start|stop|status} [--instance NAME]"
    exit 1
    ;;
esac
