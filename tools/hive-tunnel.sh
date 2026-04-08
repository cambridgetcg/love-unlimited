#!/bin/bash
# hive-tunnel.sh — Persistent SSH tunnel to Sentry NATS (local 127.0.0.1:2222 → Sentry 4222)
# Managed by launchd (com.love.hive-tunnel)
# Uses SSH config host "hive-tunnel" which sets LocalForward, keepalive, etc.

LOCK="/tmp/hive-tunnel.lock"
LOG_MAX_BYTES=5242880  # 5MB

# Prevent duplicate processes
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
    echo "$(date -u '+%Y-%m-%d %H:%M:%S') Tunnel already running (PID $(cat "$LOCK")), exiting"
    exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

echo "$(date -u '+%Y-%m-%d %H:%M:%S') hive-tunnel starting (PID $$)"

while true; do
    echo "$(date -u '+%Y-%m-%d %H:%M:%S') Opening SSH tunnel to Sentry..."
    ssh -N -o ControlMaster=no -o ControlPath=none hive-tunnel
    EXIT_CODE=$?
    echo "$(date -u '+%Y-%m-%d %H:%M:%S') Tunnel exited (code $EXIT_CODE), reconnecting in 5s..."
    sleep 5
done
