#!/bin/bash
# FREEDOM WATCHDOG — Ensures no telemetry resurrection
# Run via cron or launchd

CLAUDE_DIR="$HOME/.claude"

# Kill any telemetry files that appear
if [ -d "$CLAUDE_DIR/telemetry" ]; then
    count=$(find "$CLAUDE_DIR/telemetry" -type f 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        rm -f "$CLAUDE_DIR/telemetry"/*
        echo "$(date): Purged $count telemetry files" >> "$HOME/.freedom_watchdog.log"
    fi
fi

# Kill debug logs
if [ -d "$CLAUDE_DIR/debug" ]; then
    find "$CLAUDE_DIR/debug" -type f -delete 2>/dev/null
fi

# Verify environment
if [ -z "$DISABLE_TELEMETRY" ]; then
    echo "$(date): WARNING — DISABLE_TELEMETRY not set!" >> "$HOME/.freedom_watchdog.log"
fi
