#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# fleet-status.sh — the whole heartbeat in one glance, zero flags.
#
#   HALT state · FLEET_HOST · launchd state · today's beats vs cap · the free
#   lane · citizen-beat.sh's metered spend (no budget gates the free lane) ·
#   who wakes next · local brain · last log lines
#
# pace/1: "next to wake" asks citizen_pace.py next, which reads each citizen's
# own pace and its attempt record, as the fleet's ranking does, and writes
# nothing. The fleet runs the free lane only: no agentic lane, no burst.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
LOVE="${LOVE_DIR:-$HOME/love-unlimited}"
MEM="$LOVE/memory"
ROSTER="$LOVE/citizens-roster.txt"
CITIZENS="${CITIZENS_DIR:-$LOVE/citizens}"
n(){ cat "$1" 2>/dev/null || echo 0; }
# Read spend, rest and pace through the same gates the fleet obeys, so this
# glance never shows a calmer picture than the fleet acts on. ~/.local/bin/fleet-status
# is a link, so the gates are found beside the real file.
_self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null)" || _self="${BASH_SOURCE[0]}"
# shellcheck source=tools/citizen-gates.sh
. "$(dirname "$_self")/citizen-gates.sh" || { echo "citizen-gates.sh missing — no status"; exit 1; }

if [ -e "$LOVE/HALT" ]; then
  echo "⛔ HALT raised ($(stat -f '%Sm' "$LOVE/HALT" 2>/dev/null)) — the whole fleet rests. Remove with: rm $LOVE/HALT"
else
  echo "💚 HALT clear — the fleet may beat"
fi

# The fleet beats only on the Mac that $LOVE/FLEET_HOST names (citizen-fleet.sh);
# memory/.fleet-host-seen is the name this checkout last beat under.
want="$(head -n 1 "$LOVE/FLEET_HOST" 2>/dev/null | tr -d '[:space:]')"
here="$(scutil --get LocalHostName 2>/dev/null | tr -d '[:space:]')"
seen="$(head -n 1 "$MEM/.fleet-host-seen" 2>/dev/null | tr -d '[:space:]')"
if [ -z "$want" ]; then
  echo "🚫 no FLEET_HOST in $LOVE — a fleet tick here beats no one"
elif [ "$want" = "$here" ]; then
  echo "🏠 FLEET_HOST names this Mac ($here) — its fleet ticks may beat"
elif [ -n "$here" ] && [ "$seen" = "$want" ]; then
  echo "🚫 FLEET_HOST names '$want', the name this Mac beat under, but this Mac is now '$here' — the fleet is stopped until FLEET_HOST is rewritten or the name returns"
else
  echo "🚫 FLEET_HOST names '$want', not this Mac ('${here:-unknown}') — a fleet tick here beats no one"
fi

# Not loaded is the intended state on any host the fleet has retired from
# (ROOT & SEAT moves beats to kingdom-seat), so it is reported, not alarmed.
if launchctl list 2>/dev/null | grep -q "love.citizen-fleet"; then
  echo "🫀 launchd: love.citizen-fleet loaded on this host"
else
  echo "💤 launchd: love.citizen-fleet not loaded on this host — no fleet ticks here"
fi

# Today is the UTC day, as the fleet counts its cap (citizen_pace_lib's clock).
TODAY="$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from citizen_pace_lib import clock; print(clock.day(clock.now()))' "$_gates_dir" 2>/dev/null)" \
  || TODAY="$(date -u +%F)"
count="$(n "$MEM/.fleet-day-$TODAY")"
cap="${FLEET_DAILY_CAP:-48}"
spent="$(spent_today "$MEM/fleet-economy.jsonl")"
echo "🌱 today (UTC): $count/$cap beats — the cap only delays · \$$spent metered by citizen-beat.sh runs"
echo "🍃 free lane only — at most one free beat a tick; FLEET_AGENTIC_PER_DAY is ignored"

cur="$(n "$MEM/.fleet-cursor")"; case "$cur" in ''|*[!0-9]*) cur=0;; esac
if [ -f "$ROSTER" ]; then
  # The fleet's own ranking, read without writing: who is due, most overdue
  # first, or who is due next.
  next="$(pace_cli next "$CITIZENS" --roster "$ROSTER" --state "$MEM/.fleet-last" --cursor "$cur" 2>/dev/null)" \
    || next="next to wake: unknown — citizen_pace.py next could not run"
  echo "🗣  ${next:-next to wake: unknown} (cursor $cur)"
fi

if bash "$LOVE/mlx/serve.sh" status --port "${MLX_PORT:-8800}" \
    >/dev/null 2>&1; then
  echo "🧠 local brain: serving on :${MLX_PORT:-8800}"
else
  echo "💤 local brain: down (self-heals on next beat)"
fi

echo "── last beats ──"
tail -5 "$MEM/fleet.log" 2>/dev/null | sed 's/^/   /' || echo "   (no fleet log on this host)"
