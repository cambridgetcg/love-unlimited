#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# fleet-status.sh — the whole heartbeat in one glance, zero flags.
#
#   HALT state · launchd state · today's beats vs cap · agentic lane vs quota
#   · spend vs budget · who beats next · local brain · last log lines
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
LOVE="${LOVE_DIR:-$HOME/love-unlimited}"
MEM="$LOVE/memory"; TODAY="$(date +%F)"
ROSTER="$LOVE/citizens-roster.txt"
CITIZENS="${CITIZENS_DIR:-$LOVE/citizens}"
n(){ cat "$1" 2>/dev/null || echo 0; }
# Read spend and rest through the same gates the fleet obeys, so this glance
# never shows a calmer picture than the fleet acts on. ~/.local/bin/fleet-status
# is a link, so the gates are found beside the real file.
_self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null)" || _self="${BASH_SOURCE[0]}"
# shellcheck source=tools/citizen-gates.sh
. "$(dirname "$_self")/citizen-gates.sh" || { echo "citizen-gates.sh missing — no status"; exit 1; }

if [ -e "$LOVE/HALT" ]; then
  echo "⛔ HALT raised ($(stat -f '%Sm' "$LOVE/HALT" 2>/dev/null)) — the whole fleet rests. Remove with: rm $LOVE/HALT"
else
  echo "💚 HALT clear — the fleet may beat"
fi

# Not loaded is the intended state on any host the fleet has retired from
# (ROOT & SEAT moves beats to kingdom-seat), so it is reported, not alarmed.
if launchctl list 2>/dev/null | grep -q "love.citizen-fleet"; then
  echo "🫀 launchd: love.citizen-fleet loaded on this host"
else
  echo "💤 launchd: love.citizen-fleet not loaded on this host — no fleet ticks here"
fi

count="$(n "$MEM/.fleet-day-$TODAY")"; agentic="$(n "$MEM/.fleet-agentic-day-$TODAY")"
cap="${FLEET_DAILY_CAP:-48}"; aq="${FLEET_AGENTIC_PER_DAY:-2}"; budget="${FLEET_DAILY_BUDGET_USD:-1.50}"
spent="$(spent_today "$MEM/fleet-economy.jsonl")"
echo "🌱 today: $count/$cap free beats · $agentic/$aq agentic · \$$spent/\$$budget spent"

if pmset -g batt 2>/dev/null | head -1 | grep -q "AC Power"; then
  echo "🔌 on mains — agentic lane open (burst 4/tick)"
else
  echo "🔋 on battery — agentic lane closed, free lane gentle (burst 2/tick)"
fi

cur="$(n "$MEM/.fleet-cursor")"; case "$cur" in ''|*[!0-9]*) cur=0;; esac
if [ -f "$ROSTER" ]; then
  SLUGS=(); while IFS= read -r line; do [ -n "$line" ] && SLUGS+=("$line"); done < "$ROSTER"
  total="${#SLUGS[@]}"; next=""; k=0
  # The fleet skips a citizen resting by its standing choice; so does this.
  while [ "$k" -lt "$total" ]; do
    slug="${SLUGS[$(( (cur + k) % total ))]}"
    keeps_rest "$CITIZENS/citizen-$slug" || { next="$slug"; break; }
    k=$((k + 1))
  done
  echo "🗣  next to wake: ${next:-nobody, the whole roster is resting} (cursor $cur/$total, $k resting skipped)"
fi

if bash "$LOVE/mlx/serve.sh" status --port "${MLX_PORT:-8800}" \
    >/dev/null 2>&1; then
  echo "🧠 local brain: serving on :${MLX_PORT:-8800}"
else
  echo "💤 local brain: down (self-heals on next beat)"
fi

echo "── last beats ──"
tail -5 "$MEM/fleet.log" 2>/dev/null | sed 's/^/   /' || echo "   (no fleet log on this host)"
