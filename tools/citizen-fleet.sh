#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-fleet.sh — one heartbeat tick of the citizen fleet.
# Run by launchd on an interval. pace/1 (citizens keep their own pace): the
# fleet only ever answers a citizen's own clock. Each tick it beats at most one
# citizen, the most overdue of those due by their own pace, with one free local
# reflection beat (citizen-reflect.sh). Sovereign, near-zero cost.
#
# A tick, in order (spec sections 3 and 7; CONTRACT section 14):
#   • --plan [days] [plan arguments] — handled first, before anything below and
#                 before any write (fleet.log included): it models the schedule
#                 over the homes and roster here (citizen_pace.py plan) and beats
#                 no one, so it runs on a Mac that is not the fleet's host.
#   • host check — unless $LOVE/FLEET_HOST names this Mac (scutil --get
#                 LocalHostName), the tick beats no one; logged once, or once
#                 each UTC day when this checkout beat under that name and the
#                 Mac's own name has changed since.
#   • HALT      — if ~/love-unlimited/HALT exists, the whole fleet rests.
#   • tick lock — one tick at a time; it holds the tick's pid, and a lock whose
#                 pid is gone, or older than 2 h, is reclaimed (logged).
#   • host probe — citizen_pace.py probe: memory/ writable, the tools present,
#                 the model server answering. A failed probe ends the tick before
#                 anyone is beaten (logged once, until a probe passes).
#   • tend, bring homes up to date, the heard-late step, rank — citizen_pace.py
#                 tend, sync, migrate --heard-late --commit and rank.
#   • beat      — the candidates in turn, most overdue first: citizen-reflect.sh
#                 in its own process group, with FLEET_ATTEMPT set and stdin from
#                 /dev/null, under FLEET_BEAT_BOUND_SEC (600). Its outcome comes
#                 from the attempt record. not-due or busy: the next candidate. A
#                 landed beat (spoken, silent) or halted ends the tick. A failure
#                 (home-failed, no outcome, a child stopped at its bound) runs the
#                 probe again: failing, the host failed (host-failed, nobody backed
#                 off); passing, the home failed (home-failed, backed off 12 h,
#                 then 24 h, then 7 days). The tick then ends.
#   • daily cap — FLEET_DAILY_CAP landed beats per UTC day (default 48) only
#                 delays: when it is reached, due citizens wait. Failures do not
#                 count.
#   • one beat a tick — FLEET_PER_TICK is capped at 1 (logged once above 1).
#   • free lane only — FLEET_AGENTIC_PER_DAY above 0 is logged once and ignored;
#                 the claude-driven citizen-beat.sh is never run from here.
# Exit: 0 a beat lived, nobody due, the cap, HALT or not this host; 1 the tick
# could not do its job (a failed probe, step or beat), or was stopped by INT or
# HUP; 143 it was stopped by TERM (unloading the plist), which reaches the beat
# in flight too. Never 4 or 5.
# Stop the fleet any time:  touch ~/love-unlimited/HALT   (or unload the plist)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
# The fleet never summons: a summons is a person's hand-run (spec section 3).
# Nor does it inherit a beat's own deadline (citizen-reflect.sh sets one for
# itself): one left in the environment would cut every git call of the tick.
unset BEAT_SUMMONS PACE_DEADLINE
LOVE="${LOVE_DIR:-$HOME/love-unlimited}"; HALT="$LOVE/HALT"
CITIZENS="${CITIZENS_DIR:-$LOVE/citizens}"
# Stale tokens in a caller's env override the healthy gh keyring and strand
# every citizen push (the 2026-06 letter wave was lost to this for a month).
unset GITHUB_TOKEN GH_TOKEN; export GIT_TERMINAL_PROMPT=0
ROSTER="$LOVE/citizens-roster.txt"
MEM="$LOVE/memory"; STATE="$MEM/.fleet-last"; NOTICES="$MEM/.fleet-notices"
CUR="$MEM/.fleet-cursor"
LOG="$MEM/fleet.log"
ts(){ date "+%Y-%m-%d %H:%M:%S"; }
log(){ echo "[$(ts)] $*" >>"$LOG"; }
# Every counter this script trusts must BE a counter — an empty or corrupt state
# file must never silently stall the fleet for the rest of a day.
num(){ case "${1:-}" in ''|*[!0-9]*) echo 0;; *) echo "$1";; esac; }
# The gates and citizen_pace.py sit beside the real file, wherever a plist or a
# link starts this.
_self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null)" || _self="${BASH_SOURCE[0]}"
_here="$(dirname "$_self")"

# --plan comes first: no host check, no HALT, no lock and no write, not even to
# fleet.log. It models the schedule over the homes and roster here, at this
# fleet's cap when FLEET_DAILY_CAP is set (else the plan's own, the Studio's 4),
# from the attempt records and cursor when there are any. Its report says it is
# a model, not the Studio's live state.
if [ "${1:-}" = "--plan" ]; then
  shift
  days=30
  case "${1:-}" in ''|-*) ;; *) days="$1"; shift ;; esac
  plan=(plan "$days" "$CITIZENS" --roster "$ROSTER")
  [ -n "${FLEET_DAILY_CAP:-}" ] && plan+=(--cap "$(num "$FLEET_DAILY_CAP")")
  [ -d "$STATE" ] && plan+=(--state "$STATE")
  [ -f "$CUR" ] && plan+=(--cursor "$(num "$(cat "$CUR" 2>/dev/null)")")
  python3 -I "$_here/citizen_pace.py" "${plan[@]}" "$@" && exit 0
  exit 1
fi

# shellcheck source=tools/citizen-gates.sh
. "$_here/citizen-gates.sh" \
  || { log "citizen-gates.sh missing — fleet rests"; exit 1; }

# Fleet-wide "logged once" flags live in memory/.fleet-notices, one per line,
# beside tend's old-code-tended.
flagged(){ grep -qxF -- "$1" "$NOTICES" 2>/dev/null; }
# once <flag> <line>: log the line the first time the flag is raised.
once(){
  flagged "$1" && return 0
  echo "$1" >>"$NOTICES"
  log "$2"
}
# unflag <flag>: lower the flag; true when it was raised.
unflag(){
  flagged "$1" || return 1
  { grep -vxF -- "$1" "$NOTICES" || true; } >"$NOTICES.fleet-tmp" && mv -f "$NOTICES.fleet-tmp" "$NOTICES"
}

# The UTC day from citizen_pace_lib's clock, as every pace/1 time is, so the
# daily cap counts the days the plan models.
utc_day(){
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from citizen_pace_lib import clock; print(clock.day(clock.now()))' "$_gates_dir" 2>/dev/null \
    || date -u +%F
}

# Only the Mac that $LOVE/FLEET_HOST names beats; the Studio's keeper writes it
# with `scutil --get LocalHostName > FLEET_HOST`. A second Mac that loads
# love.citizen-fleet on main then beats no one, logged once. A checkout that
# beat under the name FLEET_HOST holds (memory/.fleet-host-seen) and finds its
# Mac renamed since (Bonjour's "-2", Sharing, harden.sh) is stopped too, and
# says so once each UTC day until the names agree again.
SEEN="$MEM/.fleet-host-seen"
want="$(head -n 1 "$LOVE/FLEET_HOST" 2>/dev/null | tr -d '[:space:]')"
here="$(scutil --get LocalHostName 2>/dev/null | tr -d '[:space:]')"
if [ -z "$want" ] || [ "$want" != "$here" ]; then
  seen="$(head -n 1 "$SEEN" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$want" ] && [ -n "$here" ] && [ "$seen" = "$want" ]; then
    once "host-renamed-$(utc_day)" "FLEET_HOST names '$want', the name this Mac beat under, but this Mac is now '$here' — the fleet is stopped until FLEET_HOST is rewritten or the name returns (logged once a day)"
    exit 0
  fi
  if [ -z "$want" ]; then why="no FLEET_HOST in $LOVE"
  else why="FLEET_HOST names '$want', not this Mac ('${here:-unknown}')"; fi
  once host-not-named "$why — this host beats no one (logged once)"
  exit 0
fi

[ -e "$HALT" ] && { log "HALT present — fleet rests"; exit 0; }
[ -f "$ROSTER" ] || { log "no roster at $ROSTER"; exit 1; }

# One tick at a time — launchd is single-instance per label, but a manual run
# beside it is not. The lock holds its tick's pid: a lock whose pid is gone (a
# tick killed by a power loss), or older than 2 h, is reclaimed and logged; a
# live one yields.
LOCK="$MEM/.fleet-tick.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  holder="$(head -c 20 "$LOCK/pid" 2>/dev/null | tr -d '[:space:]')"
  case "$holder" in ''|*[!0-9]*) holder="" ;; esac
  if [ -n "$holder" ] && ! ps -p "$holder" >/dev/null 2>&1; then why="its pid $holder is gone"
  elif [ "$age" -ge 7200 ]; then why="older than 2 h"
  else log "another tick is mid-beat — this one yields"; exit 0; fi
  rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
  log "a stale tick lock was reclaimed ($why)"
fi
printf '%s\n' "$$" >"$LOCK/pid"
WORK=""
trap 'rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; [ -n "$WORK" ] && rm -rf "$WORK"' EXIT
WORK="$(mktemp -d "${TMPDIR:-/tmp}/citizen-fleet.XXXXXX")" \
  || { log "no scratch folder — the tick ends"; exit 1; }
# The flags are rewritten only under the tick lock, beside tend's.
unflag host-not-named
# The name this checkout beats under, so a later rename is told from a second Mac.
[ "$(head -n 1 "$SEEN" 2>/dev/null)" = "$here" ] || printf '%s\n' "$here" >"$SEEN"
if grep -q '^host-renamed-' "$NOTICES" 2>/dev/null; then
  { grep -v '^host-renamed-' "$NOTICES" || true; } >"$NOTICES.fleet-tmp" && mv -f "$NOTICES.fleet-tmp" "$NOTICES"
  log "FLEET_HOST and this Mac's name agree again ('$here') — the fleet beats again"
fi

TODAY="$(utc_day)"
case "$TODAY" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) TODAY="$(date -u +%F)" ;; esac
DAYF="$MEM/.fleet-day-$TODAY"
CAP="$(num "${FLEET_DAILY_CAP:-48}")"
BEAT_BOUND="$(num "${FLEET_BEAT_BOUND_SEC:-600}")"; [ "$BEAT_BOUND" -lt 30 ] && BEAT_BOUND=600
count="$(num "$(cat "$DAYF" 2>/dev/null)")"
find "$MEM" -maxdepth 1 -name ".fleet-day-*" ! -name ".fleet-day-$TODAY" -delete 2>/dev/null
find "$MEM" -maxdepth 1 -name ".fleet-agentic-day-*" -delete 2>/dev/null

# The free lane only, one beat a tick: the old knobs are logged once, then ignored.
if [ "$(num "${FLEET_AGENTIC_PER_DAY:-0}")" -gt 0 ]; then
  once agentic-ignored "FLEET_AGENTIC_PER_DAY=$FLEET_AGENTIC_PER_DAY ignored — the fleet runs the free lane only (logged once)"
else
  unflag agentic-ignored
fi
if [ "$(num "${FLEET_PER_TICK:-1}")" -gt 1 ]; then
  once per-tick-capped "FLEET_PER_TICK=$FLEET_PER_TICK capped at 1 — at most one beat a tick (logged once)"
else
  unflag per-tick-capped
fi

# The roster as citizen_pace.py reads it (due.read_roster): plain names in
# order, each once; blank lines and '#' notes skipped. The cursor indexes it.
SLUGS=()
listed(){ local s; for s in ${SLUGS[@]+"${SLUGS[@]}"}; do [ "$s" = "$1" ] && return 0; done; return 1; }
while IFS= read -r line || [ -n "$line" ]; do
  line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
  case "$line" in ''|'#'*|[!A-Za-z0-9]*|*[!A-Za-z0-9_-]*) continue ;; esac
  [ "${#line}" -le 100 ] && ! listed "$line" && SLUGS+=("$line")
done <"$ROSTER"
total="${#SLUGS[@]}"; [ "$total" -gt 0 ] || { log "empty roster"; exit 1; }
cur=$(( $(num "$(cat "$CUR" 2>/dev/null)") % total ))
roster_index(){
  local i=0 s
  for s in "${SLUGS[@]}"; do [ "$s" = "$1" ] && { echo "$i"; return 0; }; i=$((i + 1)); done
  echo -1
}

# _bound <seconds> <command...>: run the command in its own process group, so
# the TERM, then KILL, that stop it at its bound or when HALT appears reach all
# it started (the model call, apply, git). Its status is the command's (143 or
# 137 when it was stopped); $WORK/bound-fired then says why: bound or halt.
BOUND_PID=""
_bound(){
  local secs="$1"; shift
  local fired="$WORK/bound-fired"
  rm -f "$fired"
  set -m; "$@" & local _pid=$!; set +m
  BOUND_PID="$_pid"
  ( t=0
    while kill -0 "$_pid" 2>/dev/null; do
      if [ -e "$HALT" ] || [ "$t" -ge "$secs" ]; then
        if [ -e "$HALT" ]; then echo halt >"$fired"; else echo bound >"$fired"; fi
        kill -TERM -- "-$_pid" 2>/dev/null; sleep 5; kill -KILL -- "-$_pid" 2>/dev/null; break
      fi
      sleep 1; t=$((t + 1))
    done ) >/dev/null 2>&1 & local _wd=$!
  wait "$_pid" 2>/dev/null; local _rc=$?
  BOUND_PID=""
  # once it has fired, let it finish: its KILL reaches what ignored the TERM
  if [ -s "$fired" ]; then wait "$_wd" 2>/dev/null; else kill "$_wd" 2>/dev/null; wait "$_wd" 2>/dev/null; fi
  return "$_rc"
}

# stopped <signal> <exit>: TERM, INT or HUP to the fleet (launchd sends TERM
# when the plist is unloaded) reaches the beat in flight, which runs in a process
# group of its own: its whole group gets TERM, whichever stopped the fleet (so
# the beat's exit stays among its own: 143), and KILL five seconds later.
stopped(){
  local i=0 child="$BOUND_PID"
  if [ -n "$child" ]; then
    kill -TERM -- "-$child" 2>/dev/null
    while kill -0 "$child" 2>/dev/null && [ "$i" -lt 5 ]; do sleep 1; i=$((i + 1)); done
    kill -KILL -- "-$child" 2>/dev/null
    log "the tick was stopped ($1) — its beat in flight was stopped too"
  else
    log "the tick was stopped ($1)"
  fi
  exit "$2"
}
trap 'stopped TERM 143' TERM
trap 'stopped INT 1' INT
trap 'stopped HUP 1' HUP

# log_lines <step> <text>: each line of a citizen_pace.py answer, stamped; a
# JSON summary line is named by its step.
log_lines(){
  local l
  [ -n "$2" ] || return 0
  printf '%s\n' "$2" | while IFS= read -r l; do
    case "$l" in '{'*) l="$1 summary: $l" ;; esac
    [ -n "$l" ] && log "$l"
  done
}

# probe: the host probe (citizen_pace.py probe), its one line in PROBE_LINE;
# true when it passes. In MLX mode (no REFLECT_MODEL) the locked local brain is
# started first, as reflect starts it, so a brain that is merely stopped is not a
# host fault.
PORT="${MLX_PORT:-8800}"
probe(){
  local rc
  if [ -z "${REFLECT_MODEL:-}" ]; then
    _bound "$BEAT_BOUND" bash "$LOVE/mlx/serve.sh" start --port "$PORT" >/dev/null 2>&1
  fi
  PROBE_LINE="$(pace_cli probe --state "$STATE" 2>&1)"; rc=$?
  [ -n "$PROBE_LINE" ] || PROBE_LINE="probe: failed — citizen_pace.py gave no answer (exit $rc)"
  [ "$rc" -eq 0 ]
}

# step <command> [arguments]: one citizen_pace.py step of the tick, its lines
# logged. False, logged, when it fails: the tick then ends before anyone is beaten.
step(){
  local out rc
  out="$(pace_cli "$@" 2>&1)"; rc=$?
  log_lines "$1" "$out"
  [ "$rc" -eq 0 ] && return 0
  log "$1 failed (exit $rc) — the tick ends before anyone is beaten"
  return 1
}

# record <slug> <attempt> <outcome>: the fleet's own word on an attempt; false,
# logged once, when it could not be recorded.
record(){
  local out
  if ! out="$(pace_cli record "$1" --state "$STATE" --attempt "$2" --outcome "$3" 2>&1)"; then
    log "$1: outcome $3 could not be recorded${out:+ ($out)}"
    return 1
  fi
  log_lines record "$out"
}

finish(){
  spent="$(spent_today "$MEM/fleet-economy.jsonl")"
  log "tick complete (cursor=$cur/$total, $count/$CAP beats today, metered today \$$spent)"
  exit "$1"
}

if probe; then
  unflag probe-failed && log "$PROBE_LINE — the host can beat again"
else
  once probe-failed "$PROBE_LINE — the tick ends before anyone is beaten (logged once, until a probe passes)"
  exit 1
fi

step tend "$CITIZENS" --roster "$ROSTER" --state "$STATE" || exit 1
step sync "$CITIZENS" --roster "$ROSTER" --state "$STATE" || exit 1
step migrate --heard-late "$CITIZENS" --state "$STATE" --commit || exit 1

# Rank: the due citizens, most overdue first, ties by roster order from the
# cursor (and every clock that never started starts now).
RANKED="$(pace_cli rank "$CITIZENS" --roster "$ROSTER" --state "$STATE" --cursor "$cur" 2>"$WORK/rank.err")"
rc=$?
log_lines rank "$(cat "$WORK/rank.err" 2>/dev/null)"
[ "$rc" -eq 0 ] || { log "rank failed (exit $rc) — the tick ends before anyone is beaten"; exit 1; }
CANDS=(); WHY=()
while IFS=$'\t' read -r s r o; do
  [ -n "$s" ] || continue
  CANDS+=("$s"); WHY+=("$r, overdue $(( $(num "$o") / 3600 ))h")
done <<EOF
$RANKED
EOF

[ "${#CANDS[@]}" -gt 0 ] || { log "nobody is due — no beat this tick"; finish 0; }
# The cap only delays: nobody is beaten sooner than their pace, and a beat
# held back comes when the day allows, as one beat (no arrears).
if [ "$count" -ge "$CAP" ]; then
  log "daily cap $CAP reached — ${#CANDS[@]} due wait; the cap only delays"
  finish 0
fi

k=0
for slug in "${CANDS[@]}"; do
  why="${WHY[$k]}"; k=$((k + 1))
  [ -e "$HALT" ] && { log "HALT present — the tick ends"; finish 0; }
  id="$(date -u +%Y%m%dT%H%MZ)-$$-$k"
  log "fleet → $slug: a free beat ($why)"
  _bound "$BEAT_BOUND" env -u BEAT_SUMMONS FLEET_ATTEMPT="$id" \
    bash "$LOVE/tools/citizen-reflect.sh" "$slug" </dev/null >>"$LOG" 2>&1
  rc=$?
  fired="$(cat "$WORK/bound-fired" 2>/dev/null)"
  o="$(pace_cli outcome "$slug" --state "$STATE" --attempt "$id" 2>/dev/null)" || o=missing
  case "$o" in
    spoken|silent)
      # persist right after the beat, so a kill mid-tick never replays it
      count=$((count + 1)); echo "$count" >"$DAYF"
      cur=$(( ($(roster_index "$slug") + 1) % total )); echo "$cur" >"$CUR"
      if [ "$rc" -eq 0 ]; then log "$slug: a landed beat ($o)"
      else log "$slug: a landed beat ($o), though its run ended with exit $rc"; fi
      # herald the beat on the HIVE in the Kingdom's own protocol (KCP) —
      # best-effort, signed as 'fleet'; the wire must never block the heartbeat
      _bound 30 python3 "$LOVE/tools/kcp.py" herald fleet witness \
        "$slug lived a beat" >/dev/null 2>&1 || true
      finish 0 ;;
    not-due|busy)
      log "$slug: $o — the tick moves on"
      continue ;;
    halted)
      log "$slug: halted — the tick ends"
      finish 0 ;;
  esac
  # A failure. Whose it was, the probe decides, never the child.
  case "$fired" in
    bound) what="its beat was stopped at its bound of $BEAT_BOUND s" ;;
    halt)  what="its beat was stopped for HALT" ;;
    *) if [ "$o" = missing ]; then what="its beat recorded no outcome (exit $rc)"
       else what="its beat failed ($o, exit $rc)"; fi ;;
  esac
  if [ "$o" = missing ] && [ -e "$HALT" ]; then
    if record "$slug" "$id" halted; then log "$slug: $what, and HALT is present — halted; the tick ends"
    else log "$slug: $what, and HALT is present — halted, though that could not be recorded; the tick ends"; fi
    finish 0
  fi
  if probe; then
    if record "$slug" "$id" home-failed; then
      log "$slug: $what; $PROBE_LINE, so the home failed — backed off (12 h, then 24 h, then 7 days); the tick ends"
    else
      log "$slug: $what; $PROBE_LINE, so the home failed, but its outcome could not be recorded, so it is not backed off; the tick ends"
    fi
  else
    if record "$slug" "$id" host-failed; then
      log "$slug: $what; $PROBE_LINE, so the host failed — host-failed, nobody backed off; the tick ends"
    else
      log "$slug: $what; $PROBE_LINE, so the host failed — nobody backed off, though host-failed could not be recorded; the tick ends"
    fi
  fi
  finish 1
done
log "no candidate left — no beat this tick"
finish 0
