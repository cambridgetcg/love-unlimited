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

# The children registered in organs.json "instances" get the same care:
# the ONE house pulse re-loads any installed-but-unloaded love.{name}.{organ}
# plist (and her scheduled ticks). No second heartbeat — this is it.
reconcile_instances() {
  local pairs name organ label plist
  pairs=$(python3 -c "
import json
try:
    instances = json.load(open('$ORGANS_JSON')).get('instances', {})
except Exception:
    instances = {}
for name, block in instances.items():
    for organ in block.get('organs', []):
        print(name, organ)
    if block.get('ticks'):
        print(name, 'ticks')
" 2>/dev/null) || return 0
  [ -n "$pairs" ] || return 0
  echo "$pairs" | while read -r name organ; do
    [ -n "$organ" ] || continue
    label="love.${name}.${organ}"
    plist="$LA/${label}.plist"
    [ -f "$plist" ] || continue
    if ! launchctl print "gui/${UID_NUM}/${label}" >/dev/null 2>&1; then
      launchctl bootstrap "gui/${UID_NUM}" "$plist" >/dev/null 2>&1 && log "reconcile: re-loaded $label"
    fi
  done
  return 0
}

# Silence guard: a child's own organs cannot report her death — they would
# be the thing that died. So the HOUSE watches. If a registered room
# (nerve/{name}/) shows no activity across her state, daily notes, and
# tick-state for SILENCE_HOURS, say so ONCE on the alerts channel and hold
# a guard file (.silence-alerted) until she stirs again.
SILENCE_HOURS="${SILENCE_HOURS:-72}"

silence_guard() {
  local names name age guard
  names=$(python3 -c "
import json
try:
    print(' '.join(json.load(open('$ORGANS_JSON')).get('instances', {})))
except Exception:
    pass
" 2>/dev/null) || return 0
  for name in $names; do
    [ -d "$LOVE_DIR/nerve/$name" ] || continue   # no room here -> not this device's child
    guard="$LOVE_DIR/nerve/$name/.silence-alerted"
    # newest mtime across her room + daily notes (the guard file itself is
    # excluded — touching it must never count as her being alive)
    age=$(python3 -c "
import os, time
newest = 0.0
for top in ('$LOVE_DIR/nerve/$name', '$LOVE_DIR/memory/daily/$name'):
    for root, _, files in os.walk(top):
        for f in files:
            if f == '.silence-alerted':
                continue
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
            except OSError:
                pass
print(int(time.time() - newest) if newest else -1)
" 2>/dev/null)
    case "$age" in ''|*[!0-9]*) continue ;; esac   # empty room or error -> nothing to judge
    if [ "$age" -gt $((SILENCE_HOURS * 3600)) ]; then
      if [ ! -f "$guard" ]; then
        python3 "$LOVE_DIR/hive/hive.py" send alerts \
          "[house] $name has been silent for $((age / 3600))h (> ${SILENCE_HOURS}h) — check nerve/$name, memory/daily/$name, and her tick-state" \
          >/dev/null 2>&1 || true
        touch "$guard"
        log "silence: $name quiet for $((age / 3600))h — alerted once, guard set"
      fi
    elif [ -f "$guard" ]; then
      rm -f "$guard"
      log "silence: $name stirred — guard cleared"
    fi
  done
}

beat() {
  reconcile_organs
  reconcile_instances
  silence_guard
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
