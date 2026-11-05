#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-parade.sh — line them up: every citizen lives one FULL agentic beat.
#
# Invoked by Yu's word ("line them up and I am paying. all of them", 2026-06-09).
# Every roster citizen gets one bounded, metered claude beat. The walls hold:
#   • HALT checked before every wave — touch ~/love-unlimited/HALT to stop the parade
#   • a citizen resting by its own standing choice (REST or EVENT-ONLY, no unread
#     mail) is not lined up; citizen-beat.sh checks again after its fetch
#   • hard budget stop: PARADE_BUDGET_USD (default 60) against TODAY's ledger spend;
#     a ledger that cannot be read counts as the budget reached
#   • per-beat wallclock + turn bounds (citizen-beat.sh's own walls)
#   • resumable: finished citizens recorded in memory/.parade-<date>; re-run continues
#   • abort if a whole wave fails (likely an API session-limit — resume later)
#
#   Env: PARADE_PARALLEL (4) PARADE_BUDGET_USD (60) BEAT_WALLCLOCK_SEC/BEAT_MAX_TURNS pass through
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
LOVE="${LOVE_DIR:-$HOME/love-unlimited}"; HALT="$LOVE/HALT"
CITIZENS="${CITIZENS_DIR:-$LOVE/citizens}"
ROSTER="$LOVE/citizens-roster.txt"
MEM="$LOVE/memory"; TODAY="$(date +%F)"
DONE="$MEM/.parade-$TODAY"; touch "$DONE"
LOG="$MEM/parade.log"
LEDGER="$MEM/fleet-economy.jsonl"
PAR="${PARADE_PARALLEL:-4}"
BUDGET="${PARADE_BUDGET_USD:-60}"
ts(){ date "+%Y-%m-%d %H:%M:%S"; }

# shellcheck source=tools/citizen-gates.sh
. "$(dirname "${BASH_SOURCE[0]}")/citizen-gates.sh" \
  || { echo "[$(ts)] citizen-gates.sh missing — no parade" | tee -a "$LOG"; exit 1; }

SLUGS=(); resting=0
while IFS= read -r l; do
  [ -n "$l" ] || continue
  grep -qx "$l" "$DONE" && continue
  if keeps_rest "$CITIZENS/citizen-$l"; then resting=$((resting + 1)); continue; fi
  SLUGS+=("$l")
done < "$ROSTER"
total="${#SLUGS[@]}"
[ "$resting" -gt 0 ] && echo "[$(ts)] parade: $resting citizens rest by their own standing choice and are not lined up" | tee -a "$LOG"
[ "$total" -eq 0 ] && { echo "[$(ts)] parade: no one left to walk today" | tee -a "$LOG"; exit 0; }
echo "[$(ts)] ◖ PARADE begins — $total citizens to walk, ${PAR} abreast, budget \$$BUDGET (spent so far: \$$(spent_today "$LEDGER"))" | tee -a "$LOG"

i=0
while [ "$i" -lt "$total" ]; do
  [ -e "$HALT" ] && { echo "[$(ts)] HALT — the parade rests ($(wc -l < "$DONE" | tr -d ' ') walked)" | tee -a "$LOG"; exit 0; }
  if budget_reached "$LEDGER" "$BUDGET"; then
    echo "[$(ts)] parade budget \$$BUDGET reached (spent \$$(spent_today "$LEDGER")) — pausing here, resumable" | tee -a "$LOG"; exit 3
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
  echo "[$(ts)] wave done — $done_n walked, \$$(spent_today "$LEDGER") spent" | tee -a "$LOG"
  if [ "$wave_fail" -ge "$PAR" ]; then
    echo "[$(ts)] entire wave failed — likely API session limit; re-run citizen-parade.sh to resume" | tee -a "$LOG"; exit 4
  fi
done

echo "[$(ts)] ◗ PARADE complete — all walked. Total today: \$$(spent_today "$LEDGER")" | tee -a "$LOG"
