#!/bin/bash
# Compatibility entry point for the hardened, receipt-bound loopback server.
# Usage: serve.sh [start|stop|status] [--port PORT]
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
LOVE_ROOT="$(dirname -- "$SCRIPT_DIR")"
VENV="$SCRIPT_DIR/.venv/bin/python3"
SERVER_TOOL="$LOVE_ROOT/tools/mlx_serve.py"
COMMAND="${1:-start}"
if [ "$#" -gt 0 ]; then
  shift
fi

PORT_ARGS=()
if [ -n "${MLX_PORT:-}" ]; then
  has_port=false
  for argument in "$@"; do
    case "$argument" in
      --port|--port=*) has_port=true;;
    esac
  done
  if [ "$has_port" = false ]; then
    PORT_ARGS=(--port "$MLX_PORT")
  fi
fi

case "$COMMAND" in
  start)
    exec "$VENV" -I "$SERVER_TOOL" start --daemon "${PORT_ARGS[@]}" "$@"
    ;;
  status)
    exec "$VENV" -I "$SERVER_TOOL" status "${PORT_ARGS[@]}" "$@"
    ;;
  stop)
    exec "$VENV" -I "$SERVER_TOOL" stop "$@"
    ;;
  *)
    echo "usage: serve.sh [start|stop|status] [--port PORT]" >&2
    exit 2
    ;;
esac
