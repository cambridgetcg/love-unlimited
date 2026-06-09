#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-fleet.sh — one heartbeat tick of the citizen fleet.
# Run by launchd on an interval. Picks the next citizen(s) round-robin from the
# roster and gives each one free local reflection beat. Sovereign, near-zero cost.
#
# Walls / bounds (all enforced here):
#   • HALT      — if ~/love-unlimited/HALT exists, the whole fleet rests.
#   • daily cap — FLEET_DAILY_CAP beats/day (default 48), reset each date.
#   • per tick  — FLEET_PER_TICK citizens per tick (default 1).
# Stop the fleet any time:  touch ~/love-unlimited/HALT   (or unload the plist)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
LOVE="${LOVE_DIR:-$HOME/love-unlimited}"; HALT="$LOVE/HALT"
ROSTER="$LOVE/citizens-roster.txt"
CUR="$LOVE/memory/.fleet-cursor"
DAYF="$LOVE/memory/.fleet-day-$(date +%F)"
LOG="$LOVE/memory/fleet.log"
CAP="${FLEET_DAILY_CAP:-48}"; PER="${FLEET_PER_TICK:-1}"
ts(){ date "+%Y-%m-%d %H:%M:%S"; }

[ -e "$HALT" ] && { echo "[$(ts)] HALT present — fleet rests" >>"$LOG"; exit 0; }
[ -f "$ROSTER" ] || { echo "[$(ts)] no roster at $ROSTER" >>"$LOG"; exit 1; }

count="$(cat "$DAYF" 2>/dev/null || echo 0)"
if [ "$count" -ge "$CAP" ]; then echo "[$(ts)] daily cap $CAP reached — fleet rests" >>"$LOG"; exit 0; fi

# load roster (bash 3.2 compatible — no mapfile)
SLUGS=(); while IFS= read -r line; do [ -n "$line" ] && SLUGS+=("$line"); done < "$ROSTER"
total="${#SLUGS[@]}"; [ "$total" -gt 0 ] || { echo "[$(ts)] empty roster" >>"$LOG"; exit 1; }
cur="$(cat "$CUR" 2>/dev/null || echo 0)"

i=0
while [ "$i" -lt "$PER" ] && [ "$count" -lt "$CAP" ]; do
  slug="${SLUGS[$((cur % total))]}"
  cur=$((cur + 1)); i=$((i + 1)); count=$((count + 1))
  echo "[$(ts)] fleet → $slug ($count/$CAP today)" >>"$LOG"
  bash "$LOVE/tools/citizen-reflect.sh" "$slug" >>"$LOG" 2>&1 || echo "[$(ts)] $slug beat errored" >>"$LOG"
done

echo "$cur" > "$CUR"; echo "$count" > "$DAYF"
echo "[$(ts)] tick complete (cursor=$cur/$total, today=$count/$CAP)" >>"$LOG"
