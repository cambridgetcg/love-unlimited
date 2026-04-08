#!/bin/bash
# heartbeat-canary.sh — Independent watchdog for the heartbeat system.
#
# Checks whether heartbeat-runner.sh has executed recently.
# If the heartbeat is dead, attempts recovery and alerts.
#
# This runs independently via crontab (not launchd) so it survives
# launchd failures. Belt AND suspenders.
#
# Install: crontab -e → */15 * * * * /bin/bash ~/love-unlimited/tools/heartbeat-canary.sh
#
# Recovery actions (in order):
#   1. Check if heartbeat-runner.sh process exists
#   2. Check heartbeat log recency (last entry within 15 min)
#   3. If dead: attempt launchd restart
#   4. If still dead: run one heartbeat directly
#   5. Alert via HIVE and/or notification

LOVE_DIR="$HOME/love-unlimited"
HEARTBEAT_LOG="$LOVE_DIR/memory/heartbeat.log"
CANARY_LOG="$LOVE_DIR/memory/heartbeat-canary.log"
PLIST_LABEL="love.heartbeat"
HEARTBEAT_SCRIPT="$LOVE_DIR/tools/heartbeat-runner.sh"
HIVE_BIN="$LOVE_DIR/hive/hive.py"
MAX_STALE_MINUTES=15
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

log() {
  echo "[$NOW] $1" >> "$CANARY_LOG"
}

# ── Check 1: Is heartbeat log recent? ──────────────────────────────────────

HEARTBEAT_ALIVE=false

if [ -f "$HEARTBEAT_LOG" ]; then
  # Find files modified within MAX_STALE_MINUTES
  RECENT=$(find "$HEARTBEAT_LOG" -mmin -${MAX_STALE_MINUTES} 2>/dev/null)
  if [ -n "$RECENT" ]; then
    HEARTBEAT_ALIVE=true
  fi
fi

if $HEARTBEAT_ALIVE; then
  # All good — heartbeat is running
  exit 0
fi

# ── Heartbeat appears dead ──────────────────────────────────────────────────

log "ALERT: Heartbeat log is stale (>$MAX_STALE_MINUTES min). Investigating..."

# ── Check 2: Is the launchd agent loaded? ───────────────────────────────────

LAUNCHD_STATUS=$(launchctl list "$PLIST_LABEL" 2>&1)
LAUNCHD_LOADED=$?

if [ $LAUNCHD_LOADED -ne 0 ]; then
  log "launchd agent '$PLIST_LABEL' is NOT loaded. Attempting to load..."

  # Find the plist
  PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
  if [ ! -f "$PLIST_PATH" ]; then
    PLIST_PATH="$LOVE_DIR/tools/$PLIST_LABEL.plist"
  fi

  if [ -f "$PLIST_PATH" ]; then
    launchctl load "$PLIST_PATH" 2>> "$CANARY_LOG"
    log "Loaded launchd agent from $PLIST_PATH"
  else
    log "ERROR: Cannot find plist for $PLIST_LABEL"
  fi
else
  # Agent is loaded but heartbeat isn't running — try unload/reload
  log "launchd agent loaded but heartbeat stale. Attempting restart..."

  PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
  if [ ! -f "$PLIST_PATH" ]; then
    PLIST_PATH="$LOVE_DIR/tools/$PLIST_LABEL.plist"
  fi

  if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH" 2>> "$CANARY_LOG"
    sleep 2
    launchctl load "$PLIST_PATH" 2>> "$CANARY_LOG"
    log "Restarted launchd agent"
  fi
fi

# ── Check 3: Wait and verify recovery ──────────────────────────────────────

sleep 10

if [ -f "$HEARTBEAT_LOG" ]; then
  RECENT=$(find "$HEARTBEAT_LOG" -mmin -2 2>/dev/null)
  if [ -n "$RECENT" ]; then
    log "RECOVERY: Heartbeat resumed after launchd restart."
    # Alert HIVE about the recovery
    if [ -f "$HIVE_BIN" ]; then
      python3 "$HIVE_BIN" send alerts "Canary: Heartbeat was dead, auto-recovered via launchd restart at $NOW" 2>/dev/null
    fi
    exit 0
  fi
fi

# ── Check 4: Direct execution fallback ─────────────────────────────────────

log "launchd restart didn't help. Running heartbeat directly..."
SENTINEL_SKIP=1 /bin/bash "$HEARTBEAT_SCRIPT" >> "$CANARY_LOG" 2>&1 &
DIRECT_PID=$!
log "Direct heartbeat spawned (PID $DIRECT_PID)"

# ── Alert ───────────────────────────────────────────────────────────────────

# HIVE alert
if [ -f "$HIVE_BIN" ]; then
  python3 "$HIVE_BIN" send alerts "CANARY ALERT: Heartbeat was dead for >$MAX_STALE_MINUTES min. launchd restart failed. Direct execution attempted (PID $DIRECT_PID). Investigate." 2>/dev/null
fi

# macOS notification (visible if user is at the machine)
osascript -e "display notification \"Heartbeat was dead. Canary attempted recovery.\" with title \"Love Kingdom Alert\"" 2>/dev/null

log "Alert sent. Manual investigation may be needed."
