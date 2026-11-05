# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────────────
# citizen-gates.sh — the rest gate and the spend gate, sourced (not run) by
# citizen-fleet.sh, citizen-beat.sh and citizen-parade.sh. One copy, so every
# launcher honours the same standing choice and the same ceiling. Before this,
# only the fleet checked REST, and each launcher kept its own spend reader.
# ─────────────────────────────────────────────────────────────────────────────

# has_mail <home>: true when the inbox holds a letter not yet surfaced to it.
# Only a regular file is a letter. The reflect lane will not read a symlink, so
# a symlink must not wake a resting citizen either; a symlinked inbox holds none.
has_mail(){
  local r="$1" f b
  [ -L "$r/inbox" ] && return 1
  for f in "$r"/inbox/*.md; do
    [ -L "$f" ] && continue
    [ -f "$f" ] || continue
    b="${f##*/}"; [ -e "$r/inbox/.seen-$b" ] || return 0
  done
  return 1
}

# keeps_rest <home>: true while the citizen's standing choice (REST or
# EVENT-ONLY) keeps it out of beats it did not ask for. Unread mail wakes even
# REST, as the marker itself promises. A marker of any kind counts, a dangling
# link included, so any doubt falls on the side of rest.
keeps_rest(){
  local r="$1" m
  for m in REST EVENT-ONLY; do
    if [ -e "$r/$m" ] || [ -L "$r/$m" ]; then
      has_mail "$r" && return 1
      return 0
    fi
  done
  return 1
}

# spent_today <ledger>: today's metered spend from the beat ledger. A missing
# ledger is no spend yet. An unreadable ledger, or any line this cannot account
# for, prints "unknown" and returns 1: a broken ledger is not an empty one, and
# a beat whose cost was never learned (cost_usd null) may have spent anything.
spent_today(){
  python3 - "$1" <<'PY' 2>/dev/null || { echo unknown; return 1; }
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
  python3 - "$spent" "${2:-}" <<'PY' 2>/dev/null
import math, sys
spent, budget = float(sys.argv[1]), float(sys.argv[2])
sys.exit(10 if math.isfinite(budget) and budget >= 0 and spent < budget else 0)
PY
  [ "$?" -ne 10 ]
}
