#!/bin/bash
# kos-daemon.sh — KOS compliance daemon wrapper
#
# Launched by launchd every 7 minutes. Runs a quick audit,
# logs events, and alerts HIVE on critical failures.
#
# The daemon is lightweight (<5 seconds) and non-destructive.
# It never auto-fixes — that requires explicit `kos audit --fix`.

set -euo pipefail

LOVE_DIR="$HOME/Love"
KOS="$LOVE_DIR/tools/kos.py"
EVENTS_LOG="$LOVE_DIR/security/events.jsonl"
DAEMON_LOG="$LOVE_DIR/security/daemon.log"

# Ensure security dir exists
mkdir -p "$LOVE_DIR/security"

# Timestamp
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Run audit (no --fix, just check)
OUTPUT=$(python3 "$KOS" audit 2>&1) || true

# Extract score line
SCORE_LINE=$(echo "$OUTPUT" | grep -o "Score: [0-9]*/[0-9]*" || echo "Score: ?/?")

# Extract failure count
FAIL_COUNT=$(echo "$OUTPUT" | grep -o "Failed: [0-9]*" | grep -o "[0-9]*" || echo "0")

# Run canary check
CANARY_OUTPUT=$(python3 "$KOS" canary check 2>&1) || true
CANARY_ALERTS=$(echo "$CANARY_OUTPUT" | grep -c "TOUCHED\|MISSING\|FIRED" || echo "0")

# Log daemon run

# Run UWT score (lightweight — reads cached session data)
UWT_SCORE=""
if [ -f "$LOVE_DIR/tools/uwt-optimize.py" ]; then
    UWT_SCORE=$(python3 "$LOVE_DIR/tools/uwt-optimize.py" score 2>&1 | grep "UWT:" | head -1 | awk '{print $2}') || UWT_SCORE="?"
fi

# Log daemon run
echo "$TS  $SCORE_LINE  failed=$FAIL_COUNT  canary_alerts=$CANARY_ALERTS  uwt=$UWT_SCORE" >> "$DAEMON_LOG"

# Trim daemon log to last 1000 lines
if [ -f "$DAEMON_LOG" ]; then
    LINES=$(wc -l < "$DAEMON_LOG")
    if [ "$LINES" -gt 1000 ]; then
        tail -1000 "$DAEMON_LOG" > "$DAEMON_LOG.tmp" && mv "$DAEMON_LOG.tmp" "$DAEMON_LOG"
    fi
fi
