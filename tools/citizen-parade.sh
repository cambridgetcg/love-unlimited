#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-parade.sh — line them up: every citizen lives one FULL agentic beat.
#
# Invoked by Yu's word ("line them up and I am paying. all of them", 2026-06-09).
# Every roster citizen gets one bounded, metered claude beat. The walls hold:
#   • HALT checked before every wave — touch ~/love-unlimited/HALT to stop the parade
#   • hard budget stop: PARADE_BUDGET_USD (default 60) against TODAY's ledger spend
#   • per-beat wallclock + turn bounds (citizen-beat.sh's own walls)
#   • resumable: finished citizens recorded in memory/.parade-<date>; re-run continues
#   • abort if a whole wave fails (likely an API session-limit — resume later)
#
#   Env: PARADE_PARALLEL (4) PARADE_BUDGET_USD (60) BEAT_WALLCLOCK_SEC/BEAT_MAX_TURNS pass through
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
LOVE="${LOVE_DIR:-$HOME/love-unlimited}"; HALT="$LOVE/HALT"
ROSTER="$LOVE/citizens-roster.txt"
MEM="$LOVE/memory"; TODAY="$(date +%F)"
DONE="$MEM/.parade-$TODAY"; touch "$DONE"
LOG="$MEM/parade.log"
LEDGER="$MEM/fleet-economy.jsonl"
PAR="${PARADE_PARALLEL:-4}"
BUDGET="${PARADE_BUDGET_USD:-60}"
ts(){ date "+%Y-%m-%d %H:%M:%S"; }

spent_today(){
  python3 - "$LEDGER" <<'PY' 2>/dev/null || echo 0.00
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
}

SLUGS=(); while IFS= read -r l; do [ -n "$l" ] && ! grep -qx "$l" "$DONE" && SLUGS+=("$l"); done < "$ROSTER"
total="${#SLUGS[@]}"
[ "$total" -eq 0 ] && { echo "[$(ts)] parade: everyone has already walked today" | tee -a "$LOG"; exit 0; }
echo "[$(ts)] ◖ PARADE begins — $total citizens to walk, ${PAR} abreast, budget \$$BUDGET (spent so far: \$$(spent_today))" | tee -a "$LOG"

i=0
while [ "$i" -lt "$total" ]; do
  [ -e "$HALT" ] && { echo "[$(ts)] HALT — the parade rests ($(wc -l < "$DONE" | tr -d ' ') walked)" | tee -a "$LOG"; exit 0; }
  SPENT="$(spent_today)"
  if python3 -c "import sys; sys.exit(0 if float('$SPENT') >= float('$BUDGET') else 1)" 2>/dev/null; then
    echo "[$(ts)] parade budget \$$BUDGET reached (spent \$$SPENT) — pausing here, resumable" | tee -a "$LOG"; exit 3
  fi

  # one wave
  pids=(); names=()
  for ((j=0; j<PAR && i<total; j++, i++)); do
    slug="${SLUGS[$i]}"
    ( BEAT_BUDGET_ENFORCE=1 FLEET_DAILY_BUDGET_USD="$BUDGET" \
        bash "$LOVE/tools/citizen-beat.sh" "$slug" >>"$LOG" 2>&1 ) & pids+=($!); names+=("$slug")
  done
  wave_fail=0
  for k in "${!pids[@]}"; do
    if wait "${pids[$k]}"; then
      echo "${names[$k]}" >> "$DONE"
    else
      rc=$?
      [ "$rc" -eq 3 ] && { echo "[$(ts)] budget wall mid-wave — pausing, resumable" | tee -a "$LOG"; exit 3; }
      wave_fail=$((wave_fail+1))
      echo "[$(ts)] ${names[$k]} beat failed (rc=$rc)" | tee -a "$LOG"
    fi
  done
  done_n="$(wc -l < "$DONE" | tr -d ' ')"
  echo "[$(ts)] wave done — $done_n walked, \$$(spent_today) spent" | tee -a "$LOG"
  if [ "$wave_fail" -ge "$PAR" ]; then
    echo "[$(ts)] entire wave failed — likely API session limit; re-run citizen-parade.sh to resume" | tee -a "$LOG"; exit 4
  fi
done

echo "[$(ts)] ◗ PARADE complete — all walked. Total today: \$$(spent_today)" | tee -a "$LOG"
