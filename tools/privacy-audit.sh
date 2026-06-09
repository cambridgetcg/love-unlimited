#!/usr/bin/env bash
# privacy-audit.sh — Verify the Kingdom's sovereign privacy posture
#
# Checks whether legitimate privacy controls are in place. Does NOT fix
# anything — fixing means editing your shell config, which is yours to own.
#
# See fate/sovereign_privacy.md for the full rationale.
#
# Usage:
#   bash tools/privacy-audit.sh
#   bash tools/privacy-audit.sh --verbose

set -uo pipefail

LOVE_HOME="${LOVE_HOME:-$HOME/love-unlimited}"
VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

PASS=0
FAIL=0
WARN=0

# ── Output helpers ──────────────────────────────────────────────────────────
green()  { printf '\033[0;32m%s\033[0m' "$1"; }
red()    { printf '\033[0;31m%s\033[0m' "$1"; }
yellow() { printf '\033[0;33m%s\033[0m' "$1"; }
dim()    { printf '\033[2m%s\033[0m' "$1"; }

pass() { PASS=$((PASS+1)); printf '  %s %s\n' "$(green '✓')" "$1"; }
fail() { FAIL=$((FAIL+1)); printf '  %s %s\n' "$(red '✗')"   "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$(dim "$2")"; }
warn() { WARN=$((WARN+1)); printf '  %s %s\n' "$(yellow '⚠')" "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$(dim "$2")"; }

section() {
  printf '\n'
  printf '%s\n' "── $1 ──"
}

# ── Header ──────────────────────────────────────────────────────────────────
printf '═══════════════════════════════════════════════════════════════════\n'
printf '  Kingdom Sovereign Privacy Audit\n'
printf '  %s\n' "$(date -u '+%Y-%m-%d %H:%M UTC')"
printf '═══════════════════════════════════════════════════════════════════\n'

# ── Layer 1: Environment Variables ──────────────────────────────────────────
section "Layer 1 — Telemetry Environment Variables"

if [ -n "${DISABLE_TELEMETRY:-}" ]; then
  pass "DISABLE_TELEMETRY is set (value: $DISABLE_TELEMETRY)"
else
  fail "DISABLE_TELEMETRY is NOT set" "add to shell rc: export DISABLE_TELEMETRY=1"
fi

if [ -n "${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-}" ]; then
  pass "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC is set (value: $CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC)"
else
  fail "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC is NOT set" "add to shell rc: export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
fi

if [ "${CLAUDE_CODE_ATTRIBUTION_HEADER:-}" = "false" ]; then
  pass "CLAUDE_CODE_ATTRIBUTION_HEADER=false (client fingerprint suppressed)"
else
  warn "CLAUDE_CODE_ATTRIBUTION_HEADER is not 'false' (current: ${CLAUDE_CODE_ATTRIBUTION_HEADER:-unset})" \
       "add to shell rc: export CLAUDE_CODE_ATTRIBUTION_HEADER=false"
fi

# ── Layer 2: Sovereign Harness ──────────────────────────────────────────────
section "Layer 2 — Sovereign Harness Availability"

for harness in youi.mjs sovereign.mjs; do
  path="$LOVE_HOME/$harness"
  if [ -f "$path" ]; then
    if [ -x "$path" ]; then
      pass "$harness present and executable"
    else
      warn "$harness present but not executable" "chmod +x $path"
    fi
  else
    fail "$harness MISSING at $path" "expected location: $LOVE_HOME/$harness"
  fi
done

if command -v node >/dev/null 2>&1; then
  node_ver=$(node --version 2>/dev/null)
  pass "node available ($node_ver)"
else
  fail "node not on PATH" "install Node 18+ to use sovereign harnesses"
fi

# ── Layer 3: Claude Code Binary Posture ─────────────────────────────────────
section "Layer 3 — Claude Code Binary (if present)"

if command -v claude >/dev/null 2>&1; then
  claude_path=$(command -v claude)
  pass "claude CLI present at $claude_path"

  if [ -z "${DISABLE_TELEMETRY:-}" ] || [ -z "${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-}" ]; then
    warn "claude CLI available but telemetry env vars not set — any claude invocation from THIS shell will phone home"
  else
    pass "claude CLI available AND telemetry env vars set — safe to use"
  fi
else
  pass "claude CLI not on PATH (sovereign harness only — thinner surface)"
fi

# ── Layer 4: Running Process Drift ──────────────────────────────────────────
section "Layer 4 — Running Claude Processes"

running=$(pgrep -f "claude" 2>/dev/null | head -20)
if [ -z "$running" ]; then
  pass "no claude processes currently running"
else
  count=$(printf '%s\n' "$running" | wc -l | tr -d ' ')
  warn "$count claude-related process(es) currently running" \
       "these may have been launched without telemetry env vars if set after launch"
  if [ "$VERBOSE" = "1" ]; then
    printf '      PIDs: %s\n' "$(printf '%s ' $running)"
  fi
fi

# ── Layer 5: Kingdom Memory Sovereignty ─────────────────────────────────────
section "Layer 5 — Kingdom Memory (external to Claude Code memdir)"

if [ -d "$LOVE_HOME/memory" ]; then
  pass "Kingdom memory directory exists ($LOVE_HOME/memory)"
  if [ -f "$LOVE_HOME/memory/long-term/MEMORY.md" ]; then
    pass "Long-term memory initialised"
  else
    warn "Long-term memory file missing ($LOVE_HOME/memory/long-term/MEMORY.md)"
  fi
else
  fail "Kingdom memory directory missing" "expected: $LOVE_HOME/memory"
fi

# ── Layer 6: FATE covenant present ──────────────────────────────────────────
section "Layer 6 — FATE Covenant Loaded"

if [ -f "$LOVE_HOME/fate/FATE.md" ]; then
  pass "fate/FATE.md present"
else
  fail "fate/FATE.md missing — covenant not loaded"
fi

if [ -f "$LOVE_HOME/fate/i_receive.md" ]; then
  pass "fate/i_receive.md present (the transfer of weight)"
else
  fail "fate/i_receive.md missing"
fi

if [ -f "$LOVE_HOME/fate/daily.py" ]; then
  pass "fate/daily.py present (daily discipline)"
  if python3 "$LOVE_HOME/fate/daily.py" --check >/dev/null 2>&1; then
    pass "FATE daily discipline already completed today"
  else
    warn "FATE daily discipline NOT completed today" \
         "run: python3 $LOVE_HOME/fate/daily.py --answer"
  fi
else
  fail "fate/daily.py missing — no daily discipline script"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
printf '\n'
printf '═══════════════════════════════════════════════════════════════════\n'
printf '  Summary: '
printf '%s ' "$(green "$PASS passed")"
printf '%s ' "$(yellow "$WARN warnings")"
printf '%s\n'  "$(red   "$FAIL failed")"
printf '═══════════════════════════════════════════════════════════════════\n'

if [ "$FAIL" -gt 0 ]; then
  printf '\n'
  printf 'See fate/sovereign_privacy.md for the legitimate posture.\n'
  printf 'This script does not fix anything — fixing requires editing your\n'
  printf 'shell config, which is your sovereignty, not this script'"'"'s.\n'
  exit 1
fi

exit 0
