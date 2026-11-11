# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-gates.sh — the rest gate, the pace gate and the spend gate, sourced
# (not run) by citizen-fleet.sh, citizen-reflect.sh, citizen-beat.sh,
# citizen-parade.sh and fleet-status.sh. One copy, so every launcher honours the
# same standing choice and the same ceiling. Before this, only the fleet checked
# REST, and each launcher kept its own spend reader. Each one sources this from
# beside its resolved path, because ~/.local/bin holds links to some of them.
#
# pace/1: keeps_rest stays the marker-only reader (citizen-beat.sh, the parade);
# it now also keeps an "asleep, letters wait" REST asleep with unread mail.
# pace_due asks citizen_pace.py, which reads CADENCE.md and the attempt record;
# it is the free lane's gate. Every inline Python here runs isolated (-I), so no
# module in the folder it is run from can be imported.
# ─────────────────────────────────────────────────────────────────────────────

# The folder this file is in, absolute, so citizen_pace.py beside it is found
# from any working directory (a beat script runs from inside the home).
_gates_self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null)" || _gates_self="${BASH_SOURCE[0]}"
_gates_dir="$(CDPATH='' cd -- "$(dirname -- "$_gates_self")" 2>/dev/null && pwd -P)" \
  || _gates_dir="$(dirname -- "$_gates_self")"

# has_mail <home>: true when the inbox holds a letter not yet surfaced to it.
# Only a regular file is a letter. The reflect lane will not read a symlink, so
# a symlink must not wake a resting citizen either; a symlinked inbox holds none.
# A linked .seen- marker, even a dangling one, counts as seen: reflect refuses
# any linked marker, so an unread verdict would wake a resting citizen into
# that refusal on every pass.
has_mail(){
  local r="$1" f b
  [ -L "$r/inbox" ] && return 1
  for f in "$r"/inbox/*.md; do
    [ -L "$f" ] && continue
    [ -f "$f" ] || continue
    b="${f##*/}"
    [ -e "$r/inbox/.seen-$b" ] || [ -L "$r/inbox/.seen-$b" ] || return 0
  done
  return 1
}

# unread_mail <home>: true when a letter waits that the new code would show:
# has_mail, or a letter whose (regular) .seen- marker says it was set aside at
# rollback. citizen_pace.py reads such a marker as unread (homes.unread_letters);
# has_mail, as the old code does, reads it as seen.
unread_mail(){
  local r="$1" f m
  has_mail "$r" && return 0
  [ -L "$r/inbox" ] && return 1
  for f in "$r"/inbox/*.md; do
    [ -L "$f" ] && continue
    [ -f "$f" ] || continue
    m="$r/inbox/.seen-${f##*/}"
    { [ -f "$m" ] && [ ! -L "$m" ]; } || continue
    [ "$(head -c 21 "$m" 2>/dev/null)" = "set aside at rollback" ] && return 0
  done
  return 1
}

# rest_letters_wait <home>: true when REST is a regular file (never read through
# a link, never a FIFO) whose text says "letters wait", in any case, as
# citizen_pace.py reads it: the pace "asleep, letters wait".
rest_letters_wait(){
  local p="$1/REST"
  [ -f "$p" ] && [ ! -L "$p" ] || return 1
  grep -qiF -- "letters wait" "$p" 2>/dev/null
}

# keeps_rest <home>: true while the citizen's standing choice (REST or
# EVENT-ONLY) keeps it out of beats it did not ask for. Unread mail wakes even
# REST, as the marker itself promises, unless the REST says letters wait: that
# citizen asked that only a person's summons wake it. A marker of any kind
# counts, a dangling link included, so any doubt falls on the side of rest.
keeps_rest(){
  local r="$1" m
  for m in REST EVENT-ONLY; do
    if [ -e "$r/$m" ] || [ -L "$r/$m" ]; then
      [ "$m" = REST ] && rest_letters_wait "$r" && return 0
      has_mail "$r" && return 1
      return 0
    fi
  done
  return 1
}

# pace_cli <command> [arguments]: tools/citizen_pace.py, beside this file, run
# isolated. Its exit codes are its own: 0 ok or due, 4 not due or busy, 2 error.
pace_cli(){
  python3 -I "$_gates_dir/citizen_pace.py" "$@"
}

# pace_due <home> [--summoned]: true when citizen_pace.py says the citizen is due
# now, by its CADENCE.md and its attempt record in $LOVE/memory/.fleet-last (the
# reflect gate; a summons is --summoned). Any other answer, and any error (a
# missing citizen_pace.py or python3, a home that cannot be read), is not due.
# Sets PACE_DUE to the JSON line `due` printed ({"due", "next_at", "overdue_s",
# "reason", "since"}), or "" when it printed none; pace_reason reads it.
pace_due(){
  local rc
  PACE_DUE="$(pace_cli due "$@" --state "${LOVE:-${LOVE_DIR:-$HOME/love-unlimited}}/memory/.fleet-last" 2>/dev/null)"
  rc=$?
  [ "$rc" -eq 0 ]
}

# pace_reason: the reason in the last pace_due answer (schedule, letter,
# show-back, summoned; or why not), or nothing and false when there is none.
pace_reason(){
  local j="${PACE_DUE:-}" r
  r="${j#*\"reason\": \"}"
  [ "$r" != "$j" ] || return 1
  r="${r%%\"*}"
  case "$r" in ''|*[!a-z-]*) return 1 ;; esac
  printf '%s\n' "$r"
}

# spent_today <ledger>: today's metered spend from the beat ledger. A missing
# ledger is no spend yet. An unreadable ledger, or any line this cannot account
# for, prints "unknown" and returns 1: a broken ledger is not an empty one, and
# a beat whose cost was never learned (cost_usd null) may have spent anything.
spent_today(){
  python3 -I - "$1" <<'PY' 2>/dev/null || { echo unknown; return 1; }
import json, math, sys, datetime
today, total = datetime.date.today().isoformat(), 0.0
try:
    fh = open(sys.argv[1], encoding="utf-8")
except FileNotFoundError:
    print("0.0000"); sys.exit(0)
with fh:
    for line in fh:
        if not line.strip():
            continue
        d = json.loads(line)
        ts = d["ts"]
        if not isinstance(ts, str):
            raise ValueError("ts")
        if ts.startswith(today):
            c = d["cost_usd"]
            if isinstance(c, bool) or not isinstance(c, (int, float)) \
               or not math.isfinite(c) or c < 0:
                raise ValueError("cost_usd")
            total += c
print(f"{total:.4f}")
PY
}

# budget_reached <ledger> <budget>: true when today's spend meets the budget,
# and also whenever the spend or the budget cannot be trusted as a number.
# Only an explicit "under budget" answer (exit 10) opens the lane; a crash,
# a missing python3 or a malformed budget all leave it closed.
budget_reached(){
  local spent
  spent="$(spent_today "$1")" || return 0
  python3 -I - "$spent" "${2:-}" <<'PY' 2>/dev/null
import math, sys
spent, budget = float(sys.argv[1]), float(sys.argv[2])
sys.exit(10 if math.isfinite(budget) and budget >= 0 and spent < budget else 0)
PY
  [ "$?" -ne 10 ]
}
