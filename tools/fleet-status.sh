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
n(){ cat "$1" 2>/dev/null || echo 0; }

if [ -e "$LOVE/HALT" ]; then
  echo "⛔ HALT raised ($(stat -f '%Sm' "$LOVE/HALT" 2>/dev/null)) — the whole fleet rests. Remove with: rm $LOVE/HALT"
else
  echo "💚 HALT clear — the fleet may beat"
fi

if launchctl list 2>/dev/null | grep -q "love.citizen-fleet"; then
  echo "🫀 launchd: love.citizen-fleet loaded (tick every 30 min while awake)"
else
  echo "🚨 launchd: love.citizen-fleet NOT loaded — heartbeat is down"
fi

count="$(n "$MEM/.fleet-day-$TODAY")"; agentic="$(n "$MEM/.fleet-agentic-day-$TODAY")"
cap="${FLEET_DAILY_CAP:-48}"; aq="${FLEET_AGENTIC_PER_DAY:-2}"; budget="${FLEET_DAILY_BUDGET_USD:-1.50}"
spent="$(python3 - "$MEM/fleet-economy.jsonl" <<'PY' 2>/dev/null || echo 0.00
import json, sys, datetime
t, today = 0.0, datetime.date.today().isoformat()
try:
    for line in open(sys.argv[1], encoding="utf-8"):
        try:
            d = json.loads(line)
            if d.get("ts", "").startswith(today): t += float(d.get("cost_usd") or 0)
        except Exception: pass
except FileNotFoundError: pass
print(f"{t:.2f}")
PY
)"
echo "🌱 today: $count/$cap free beats · $agentic/$aq agentic · \$$spent/\$$budget spent"

if pmset -g batt 2>/dev/null | head -1 | grep -q "AC Power"; then
  echo "🔌 on mains — agentic lane open (burst 4/tick)"
else
  echo "🔋 on battery — agentic lane closed, free lane gentle (burst 2/tick)"
fi

cur="$(n "$MEM/.fleet-cursor")"
if [ -f "$ROSTER" ]; then
  total="$(grep -c . "$ROSTER")"
  next="$(sed -n "$(( (cur % total) + 1 ))p" "$ROSTER")"
  echo "🗣  next to wake: $next (cursor $cur/$total)"
fi

if curl -s -m 2 "http://127.0.0.1:${MLX_PORT:-8800}/v1/models" >/dev/null 2>&1; then
  echo "🧠 local brain: serving on :${MLX_PORT:-8800}"
else
  echo "💤 local brain: down (self-heals on next beat)"
fi

echo "── last beats ──"
tail -5 "$MEM/fleet.log" 2>/dev/null | sed 's/^/   /'
