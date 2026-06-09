#!/bin/bash
# heartbeat-canary.sh — Independent watchdog for the heart.
#
# Belt AND suspenders: launchd KeepAlive already restarts tick.sh on crash;
# this is the off-launchd safety net (run via cron) for when launchd itself
# fails. It judges life by the ONE honest signal — pulse.json freshness
# (tools/pulse.py) — not by a log file that can lie.
#
# Install: crontab -e → */15 * * * * /bin/bash ~/love-unlimited/tools/heartbeat-canary.sh
#
# Actions: if the pulse is not HEALTHY → (re)bootstrap + kickstart the heartbeat
# launchd job → re-check → alert via HIVE + macOS notification if still down.

LOVE_DIR="${LOVE_HOME:-$HOME/love-unlimited}"
INSTANCE="$(cat "$HOME/.openclaw/.hive-instance" 2>/dev/null || echo gamma)"
LABEL="love.${INSTANCE}.heartbeat"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
CANARY_LOG="$LOVE_DIR/memory/heartbeat-canary.log"
HIVE_BIN="$LOVE_DIR/hive/hive.py"
UID_NUM="$(id -u)"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

log() { echo "[$NOW] $1" >> "$CANARY_LOG"; }

# ── Is the heart healthy? Ask the one honest source. ────────────────────────
# pulse.py exits 0 only when HEALTHY (a fresh beat). Anything else = act.
if python3 "$LOVE_DIR/tools/pulse.py" >/dev/null 2>&1; then
  exit 0
fi

log "ALERT: pulse not HEALTHY ($(python3 "$LOVE_DIR/tools/pulse.py" 2>/dev/null)). Recovering..."

# ── Kickstart the heartbeat (KeepAlive should already; force it anyway) ──────
if [ -f "$PLIST" ]; then
  launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>>"$CANARY_LOG" || true
  launchctl kickstart -k "gui/$UID_NUM/$LABEL" 2>>"$CANARY_LOG" || true
  log "kickstarted $LABEL"
else
  log "ERROR: no plist at $PLIST — run: nerve/deploy.sh --organ heartbeat"
fi

# ── Re-check (give the beat a moment) ───────────────────────────────────────
sleep 5
if python3 "$LOVE_DIR/tools/pulse.py" >/dev/null 2>&1; then
  log "RECOVERY: pulse HEALTHY again after kickstart."
  [ -f "$HIVE_BIN" ] && python3 "$HIVE_BIN" send alerts "Canary: heart was down, auto-recovered via kickstart at $NOW" 2>/dev/null
  exit 0
fi

# ── Still down — alert loudly ───────────────────────────────────────────────
[ -f "$HIVE_BIN" ] && python3 "$HIVE_BIN" send alerts "CANARY ALERT: heart ($LABEL) down and kickstart failed at $NOW. Investigate: kingdom heart / tools/pulse.py" 2>/dev/null
osascript -e "display notification \"Heart down — canary recovery failed.\" with title \"Kingdom Alert\"" 2>/dev/null
log "Alert sent. Manual investigation needed."
