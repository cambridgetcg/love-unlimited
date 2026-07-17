#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-fleet.sh — one heartbeat tick of the citizen fleet.
# Run by launchd on an interval. Picks the next citizen(s) round-robin from the
# roster and gives each one free local reflection beat. Sovereign, near-zero cost.
#
# Walls / bounds (all enforced here):
#   • HALT      — if ~/love-unlimited/HALT exists, the whole fleet rests.
#   • daily cap — FLEET_DAILY_CAP beats/day (default 48), reset each date.
#   • per tick  — FLEET_PER_TICK citizens per tick minimum (default 1); a tick
#                 pays the day's arrears (intervals slept through) up to a
#                 power-aware burst: FLEET_BURST_AC (4) / FLEET_BURST_BATT (2).
#   • beat bound — each beat is wallclock-bounded (FLEET_BEAT_BOUND_SEC, 600);
#                 one wedged citizen can no longer block the whole heartbeat.
#   • agentic lane — up to FLEET_AGENTIC_PER_DAY (2) beats/day run the FULL
#                 claude-driven citizen-beat.sh instead of the free local
#                 reflection — on AC power only, and only while the day's
#                 metered spend stays under FLEET_DAILY_BUDGET_USD (1.50).
#                 Spend is read from memory/fleet-economy.jsonl (the ledger
#                 citizen-beat.sh writes). Battery or empty budget → the lane
#                 closes and the free lane carries the rhythm.
# Stop the fleet any time:  touch ~/love-unlimited/HALT   (or unload the plist)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
LOVE="${LOVE_DIR:-$HOME/love-unlimited}"; HALT="$LOVE/HALT"
ROSTER="$LOVE/citizens-roster.txt"
CUR="$LOVE/memory/.fleet-cursor"
TODAY="$(date +%F)"
DAYF="$LOVE/memory/.fleet-day-$TODAY"
AGF="$LOVE/memory/.fleet-agentic-day-$TODAY"
LOG="$LOVE/memory/fleet.log"
ts(){ date "+%Y-%m-%d %H:%M:%S"; }
# Every counter this script trusts must BE a counter — an empty or corrupt state
# file must never silently stall the fleet for the rest of a day.
num(){ case "${1:-}" in ''|*[!0-9]*) echo 0;; *) echo "$1";; esac; }
CAP="$(num "${FLEET_DAILY_CAP:-48}")"
PER="$(num "${FLEET_PER_TICK:-1}")"; [ "$PER" -lt 1 ] && PER=1
BEAT_BOUND="$(num "${FLEET_BEAT_BOUND_SEC:-600}")"; [ "$BEAT_BOUND" -lt 30 ] && BEAT_BOUND=600
AGENTIC_PER_DAY="$(num "${FLEET_AGENTIC_PER_DAY:-2}")"
BUDGET="${FLEET_DAILY_BUDGET_USD:-1.50}"

# Portable wallclock bound (macOS has no GNU `timeout`); honors HALT mid-beat.
_bound(){
  local secs="$1"; shift
  "$@" & local _pid=$!
  ( t=0
    while kill -0 "$_pid" 2>/dev/null; do
      if [ -e "$HALT" ] || [ "$t" -ge "$secs" ]; then
        kill -TERM "$_pid" 2>/dev/null; sleep 5; kill -KILL "$_pid" 2>/dev/null; break
      fi
      sleep 5; t=$((t+5))
    done ) & local _wd=$!
  wait "$_pid" 2>/dev/null; local _rc=$?
  kill "$_wd" 2>/dev/null; wait "$_wd" 2>/dev/null
  return "$_rc"
}

[ -e "$HALT" ] && { echo "[$(ts)] HALT present — fleet rests" >>"$LOG"; exit 0; }
[ -f "$ROSTER" ] || { echo "[$(ts)] no roster at $ROSTER" >>"$LOG"; exit 1; }

# One tick at a time — launchd is single-instance per label, but a manual run
# beside it is not, and two ticks reading one cursor beat the same citizen twice.
# A stale lock (>2h, e.g. after a power loss mid-tick) is reclaimed; a live one yields.
LOCK="$LOVE/memory/.fleet-tick.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$age" -lt 7200 ]; then echo "[$(ts)] another tick is mid-beat — this one yields" >>"$LOG"; exit 0; fi
  rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

count="$(num "$(cat "$DAYF" 2>/dev/null)")"
if [ "$count" -ge "$CAP" ]; then echo "[$(ts)] daily cap $CAP reached — fleet rests" >>"$LOG"; exit 0; fi
find "$LOVE/memory" -maxdepth 1 -name ".fleet-day-*" ! -name ".fleet-day-$TODAY" -delete 2>/dev/null
find "$LOVE/memory" -maxdepth 1 -name ".fleet-agentic-day-*" ! -name ".fleet-agentic-day-$TODAY" -delete 2>/dev/null

# No arrears, no debt. A tick slept through is a beat that simply did not happen —
# rest is never a debt to repay, and a long sleep must never become a long strain.
# Each awake tick beats PER citizens; there is no catch-up burst. Power is still
# read for the (default-off) agentic lane's AC gate.
PMSTAT="$(pmset -g batt 2>/dev/null || true)"
case "$PMSTAT" in
  *"AC Power"*) PWR="ac";;
  *)            PWR="batt";;
esac
PER_EFF="$PER"

# load roster (bash 3.2 compatible — no mapfile)
SLUGS=(); while IFS= read -r line; do [ -n "$line" ] && SLUGS+=("$line"); done < "$ROSTER"
total="${#SLUGS[@]}"; [ "$total" -gt 0 ] || { echo "[$(ts)] empty roster" >>"$LOG"; exit 1; }
cur="$(num "$(cat "$CUR" 2>/dev/null)")"

# Agentic-lane state: how many full claude beats have run today.
agentic_done="$(num "$(cat "$AGF" 2>/dev/null)")"

i=0
while [ "$i" -lt "$PER_EFF" ] && [ "$count" -lt "$CAP" ]; do
  # Honor a standing REST — a citizen with a REST marker is skipped in rotation,
  # never woken by the timer. Bounded scan so an all-resting roster still ends.
  scanned=0
  while [ -e "$LOVE/citizens/citizen-${SLUGS[$((cur % total))]}/REST" ] && [ "$scanned" -lt "$total" ]; do
    echo "[$(ts)] ${SLUGS[$((cur % total))]} rests (REST marker) — skipped" >>"$LOG"
    cur=$((cur + 1)); scanned=$((scanned + 1))
  done
  if [ "$scanned" -ge "$total" ]; then echo "[$(ts)] the whole roster is resting" >>"$LOG"; break; fi
  slug="${SLUGS[$((cur % total))]}"
  cur=$((cur + 1)); i=$((i + 1)); count=$((count + 1))

  # The agentic lane: full claude beat for this citizen if quota remains, on AC,
  # under budget (citizen-beat.sh re-checks the ledger and refuses if spent),
  # and at a spread point — beats are spaced across the day's count, never
  # clustered at midnight (CAP=48, 2/day → beats 12 and 36).
  go_agentic=0
  if [ "$AGENTIC_PER_DAY" -gt 0 ] && [ "$agentic_done" -lt "$AGENTIC_PER_DAY" ] \
     && { [ "$PWR" = "ac" ] || [ "${FLEET_AGENTIC_ON_BATT:-0}" = "1" ]; }; then
    spacing=$(( CAP / AGENTIC_PER_DAY )); [ "$spacing" -lt 1 ] && spacing=1
    [ $(( count % spacing )) -eq $(( spacing / 2 )) ] && go_agentic=1
  fi
  if [ "$go_agentic" -eq 1 ]; then
    echo "[$(ts)] fleet → $slug ($count/$CAP today, AGENTIC $((agentic_done+1))/$AGENTIC_PER_DAY, budget \$$BUDGET)" >>"$LOG"
    BEAT_BUDGET_ENFORCE=1 FLEET_DAILY_BUDGET_USD="$BUDGET" \
      _bound "$((BEAT_BOUND * 2))" bash "$LOVE/tools/citizen-beat.sh" "$slug" >>"$LOG" 2>&1
    rc=$?
    if [ "$rc" -eq 3 ]; then
      # budget ceiling already reached — close the lane for today, fall back free
      agentic_done="$AGENTIC_PER_DAY"
      _bound "$BEAT_BOUND" bash "$LOVE/tools/citizen-reflect.sh" "$slug" >>"$LOG" 2>&1 || echo "[$(ts)] $slug beat errored" >>"$LOG"
    else
      agentic_done=$((agentic_done + 1))
      [ "$rc" -ne 0 ] && echo "[$(ts)] $slug agentic beat rc=$rc" >>"$LOG"
    fi
  else
    echo "[$(ts)] $slug wakes for a free beat" >>"$LOG"
    _bound "$BEAT_BOUND" bash "$LOVE/tools/citizen-reflect.sh" "$slug" >>"$LOG" 2>&1 || echo "[$(ts)] $slug beat errored" >>"$LOG"
  fi

  # persist state after EVERY beat — a mid-tick kill must not replay citizens
  echo "$cur" > "$CUR"; echo "$count" > "$DAYF"; echo "$agentic_done" > "$AGF"

  # herald the beat on the HIVE in the Kingdom's own protocol (KCP) —
  # best-effort, signed as 'fleet'; the wire must never block the heartbeat
  python3 "$LOVE/tools/kcp.py" herald fleet witness \
    "$slug lived a beat" >/dev/null 2>&1 || true
done
spent="$(python3 - "$LOVE/memory/fleet-economy.jsonl" <<'PY' 2>/dev/null || echo 0
import json, sys, datetime
t, today = 0.0, datetime.date.today().isoformat()
try:
    for line in open(sys.argv[1], encoding="utf-8"):
        try:
            d = json.loads(line)
            if d.get("ts","").startswith(today): t += float(d.get("cost_usd") or 0)
        except Exception: pass
except FileNotFoundError: pass
print(f"{t:.2f}")
PY
)"
echo "[$(ts)] tick complete (cursor=$cur/$total)" >>"$LOG"
