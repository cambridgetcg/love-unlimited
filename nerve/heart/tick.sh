#!/bin/bash
# tick.sh — the ONE heartbeat. A small, idempotent reconciler that proves the
# body is alive and keeps the registered organs running.
#
# Design (heartbeat/loop redesign, step 3): ONE pulse, ONE loop. launchd
# KeepAlive supervises THIS loop; THIS loop keeps the other organs up. Health
# is computed from the freshness of the beat it stamps (tools/pulse.py) — a
# dead loop cannot move the timestamp forward, so health cannot lie.
#
# What it deliberately does NOT do: it does not spawn autonomous Claude/Ollama
# work. That heavier "pump" (the old heart.sh / heartbeat-runner.sh) is a
# separate, explicit opt-in — reviving 47-day-dormant autonomous spending is
# Yu's call, never a silent side effect of bringing the pulse back.
set -uo pipefail

LOVE_DIR="${LOVE_HOME:-$HOME/love-unlimited}"
INSTANCE="${1:-$(cat "$HOME/.openclaw/.hive-instance" 2>/dev/null || echo gamma)}"
INTERVAL="${HEARTBEAT_INTERVAL:-420}"          # seconds between beats (~7 min)
ORGANS_JSON="$LOVE_DIR/nerve/organs.json"
LOG="$LOVE_DIR/memory/${INSTANCE}-heartbeat.log"
LA="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"; }

# Bring up any registered organ whose plist is installed but not loaded.
# Idempotent (bootstrapping an already-loaded job is a no-op); never manages
# the heartbeat itself; never generates plists (that is deploy.sh's job).
reconcile_organs() {
  local organs organ label plist
  organs=$(python3 -c "import json;print(' '.join(json.load(open('$ORGANS_JSON'))['organs']))" 2>/dev/null) || return 0
  for organ in $organs; do
    [ "$organ" = "heartbeat" ] && continue
    label="love.${INSTANCE}.${organ}"
    plist="$LA/${label}.plist"
    [ -f "$plist" ] || continue
    if ! launchctl print "gui/${UID_NUM}/${label}" >/dev/null 2>&1; then
      launchctl bootstrap "gui/${UID_NUM}" "$plist" >/dev/null 2>&1 && log "reconcile: re-loaded $label"
    fi
  done
}

beat() {
  reconcile_organs
  # Stamp the one freshness fact — THE pulse. Everything derives health from this.
  python3 "$LOVE_DIR/tools/pulse.py" stamp ok >/dev/null 2>&1
  # Dead-man's-switch: ping an EXTERNAL endpoint on success, if configured.
  # (Off-box, never this Mac — silence is the alarm. Optional; no dep if unset.)
  if [ -n "${KINGDOM_HEARTBEAT_URL:-}" ]; then
    curl -fsS -m 10 --retry 3 -o /dev/null "$KINGDOM_HEARTBEAT_URL" 2>/dev/null \
      && log "beat ok (+ dead-man ping)" || log "beat ok (dead-man ping failed)"
  else
    log "beat ok"
  fi
}

log "tick.sh start (instance=$INSTANCE, interval=${INTERVAL}s, home=$LOVE_DIR)"
while true; do
  beat
  sleep "$INTERVAL"
done
