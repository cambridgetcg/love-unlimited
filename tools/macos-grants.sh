#!/usr/bin/env bash
# macos-grants.sh — Kingdom OS macOS TCC grant verification and guided setup
#
# Checks which TCC permissions are currently granted to the binaries
# Kingdom OS actually uses, identifies gaps, and opens the correct
# System Settings panes for you to fix them.
#
# Does NOT modify anything silently. macOS will not let any script
# grant itself TCC permissions — that is by design. Your consent is
# required via the UI.
#
# See docs/MACOS-SOVEREIGNTY.md for the full rationale.
#
# Usage:
#   bash tools/macos-grants.sh
#   bash tools/macos-grants.sh --open      # open System Settings panes for fixes
#   bash tools/macos-grants.sh --verbose

set -uo pipefail

LOVE_HOME="${LOVE_HOME:-$HOME/love-unlimited}"
OPEN_SETTINGS=0
VERBOSE=0

for arg in "$@"; do
  case "$arg" in
    --open)    OPEN_SETTINGS=1 ;;
    --verbose) VERBOSE=1 ;;
  esac
done

# ── Output helpers ──────────────────────────────────────────────────────────
green()  { printf '\033[0;32m%s\033[0m' "$1"; }
red()    { printf '\033[0;31m%s\033[0m' "$1"; }
yellow() { printf '\033[0;33m%s\033[0m' "$1"; }
dim()    { printf '\033[2m%s\033[0m' "$1"; }
bold()   { printf '\033[1m%s\033[0m' "$1"; }

pass() { printf '  %s %s\n' "$(green '✓')" "$1"; }
fail() { printf '  %s %s\n' "$(red '✗')"   "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$(dim "$2")"; }
warn() { printf '  %s %s\n' "$(yellow '⚠')" "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$(dim "$2")"; }
info() { printf '  %s %s\n' "$(dim '·')"  "$1"; }

section() {
  printf '\n'
  printf '%s\n' "── $(bold "$1") ──"
}

# ── Sanity: are we on macOS ─────────────────────────────────────────────────
if [ "$(uname)" != "Darwin" ]; then
  printf '%s\n' "This script is macOS-only. Kingdom OS on other platforms has different constraints."
  exit 1
fi

# ── Header ──────────────────────────────────────────────────────────────────
printf '═══════════════════════════════════════════════════════════════════\n'
printf '  %s\n' "$(bold 'Kingdom OS — macOS TCC Grant Audit')"
printf '  %s\n' "$(date -u '+%Y-%m-%d %H:%M UTC')"
printf '  %s\n' "$(dim 'See docs/MACOS-SOVEREIGNTY.md for the rationale')"
printf '═══════════════════════════════════════════════════════════════════\n'

# ── Identify the binaries Kingdom OS uses ───────────────────────────────────
PYTHON_BIN="$(command -v python3 2>/dev/null || echo '')"
NODE_BIN="$(command -v node 2>/dev/null || echo '')"
CLAUDE_BIN="$(command -v claude 2>/dev/null || echo '')"
ITERM_PATH="/Applications/iTerm.app"
TERMINAL_PATH="/System/Applications/Utilities/Terminal.app"

section "Binaries Kingdom OS depends on"
info "python3: ${PYTHON_BIN:-NOT FOUND}"
info "node:    ${NODE_BIN:-NOT FOUND}"
info "claude:  ${CLAUDE_BIN:-NOT FOUND}"
info "iTerm2:  $([ -d "$ITERM_PATH" ] && echo "$ITERM_PATH" || echo 'not installed')"
info "Terminal: $([ -d "$TERMINAL_PATH" ] && echo "$TERMINAL_PATH" || echo 'not installed')"

# ── TCC database probe ─────────────────────────────────────────────────────
# User-level TCC db (accessible without FDA usually)
USER_TCC="$HOME/Library/Application Support/com.apple.TCC/TCC.db"
# System-level TCC db — contains FDA grants, needs FDA to read
SYS_TCC="/Library/Application Support/com.apple.TCC/TCC.db"

section "TCC database access"

if [ -r "$USER_TCC" ]; then
  pass "User TCC database readable"
else
  warn "User TCC database not readable" "this is unusual — may indicate a privacy issue"
fi

if [ -r "$SYS_TCC" ]; then
  pass "System TCC database readable (means current shell has Full Disk Access)"
  CAN_READ_SYS_TCC=1
else
  fail "System TCC database NOT readable" \
       "current terminal does NOT have Full Disk Access — this is the main constraint"
  CAN_READ_SYS_TCC=0
fi

# ── Check specific grants ──────────────────────────────────────────────────
section "Grant audit (from user TCC database)"

query_user_tcc() {
  local service="$1"
  local client="$2"
  sqlite3 "$USER_TCC" "SELECT auth_value FROM access WHERE service='$service' AND client='$client';" 2>/dev/null
}

check_grant() {
  local label="$1"
  local service="$2"
  local client="$3"
  local val
  val=$(query_user_tcc "$service" "$client")
  case "$val" in
    2) pass "$label: $(green 'GRANTED')" ;;
    0) fail "$label: $(red 'DENIED')" "user explicitly denied — reset with: sudo tccutil reset ${service#kTCCService} $client" ;;
    "") warn "$label: $(yellow 'NOT SET')" "never prompted, or needs manual grant" ;;
    *) info "$label: auth_value=$val (unusual)" ;;
  esac
}

# iTerm2 grants
if [ -d "$ITERM_PATH" ]; then
  check_grant "iTerm2 Full Disk Access"      "kTCCServiceSystemPolicyAllFiles" "com.googlecode.iterm2"
  check_grant "iTerm2 Screen Recording"      "kTCCServiceScreenCapture"        "com.googlecode.iterm2"
  check_grant "iTerm2 Accessibility"         "kTCCServiceAccessibility"        "com.googlecode.iterm2"
  check_grant "iTerm2 Automation (AppleEvents)" "kTCCServiceAppleEvents"       "com.googlecode.iterm2"
fi

# Terminal.app grants
if [ -d "$TERMINAL_PATH" ]; then
  check_grant "Terminal Full Disk Access"    "kTCCServiceSystemPolicyAllFiles" "com.apple.Terminal"
  check_grant "Terminal Screen Recording"    "kTCCServiceScreenCapture"        "com.apple.Terminal"
fi

# Binary-level grants (for launchd-invoked tools)
if [ -n "$PYTHON_BIN" ]; then
  check_grant "python3 ($PYTHON_BIN) Full Disk Access"    "kTCCServiceSystemPolicyAllFiles" "$PYTHON_BIN"
  check_grant "python3 ($PYTHON_BIN) Screen Recording"    "kTCCServiceScreenCapture"        "$PYTHON_BIN"
fi

if [ -n "$NODE_BIN" ]; then
  check_grant "node ($NODE_BIN) Full Disk Access"         "kTCCServiceSystemPolicyAllFiles" "$NODE_BIN"
fi

if [ -n "$CLAUDE_BIN" ]; then
  check_grant "claude ($CLAUDE_BIN) Full Disk Access"     "kTCCServiceSystemPolicyAllFiles" "$CLAUDE_BIN"
fi

# ── System-level checks ────────────────────────────────────────────────────
section "System security posture"

SIP_STATUS=$(csrutil status 2>&1)
if echo "$SIP_STATUS" | grep -q "enabled"; then
  pass "SIP enabled (recommended — does not constrain Kingdom OS)"
else
  warn "SIP disabled" "not required for Kingdom OS; consider re-enabling via Recovery Mode + csrutil enable"
fi

GK_STATUS=$(spctl --status 2>&1)
if echo "$GK_STATUS" | grep -q "enabled"; then
  pass "Gatekeeper enabled (rarely a Kingdom OS constraint)"
else
  warn "Gatekeeper disabled"
fi

# ── launchd agents ─────────────────────────────────────────────────────────
section "Running Kingdom launchd agents"

AGENTS=$(launchctl list 2>/dev/null | grep -iE "love\.|kingdom\.|^[0-9]+.*hive" | head -20)
if [ -z "$AGENTS" ]; then
  info "no Kingdom launchd agents currently running"
else
  printf '%s\n' "$AGENTS" | while read -r line; do
    pid=$(echo "$line" | awk '{print $1}')
    label=$(echo "$line" | awk '{print $3}')
    if [ "$pid" = "-" ]; then
      warn "$label: not running"
    else
      pass "$label: running (PID $pid)"
    fi
  done
fi

# Check launchd plists for the critical env vars
section "launchd plist environment posture"

PLIST_DIR="$HOME/Library/LaunchAgents"
if [ -d "$PLIST_DIR" ]; then
  for plist in "$PLIST_DIR"/love.*.plist "$PLIST_DIR"/kingdom.*.plist; do
    [ ! -f "$plist" ] && continue
    name=$(basename "$plist" .plist)
    if grep -q "DISABLE_TELEMETRY" "$plist" 2>/dev/null; then
      pass "$name: has DISABLE_TELEMETRY"
    else
      warn "$name: missing DISABLE_TELEMETRY in plist" \
           "add <key>EnvironmentVariables</key> per docs/MACOS-SOVEREIGNTY.md"
    fi
  done
fi

# ── Offer to open System Settings ──────────────────────────────────────────
if [ "$OPEN_SETTINGS" = "1" ]; then
  section "Opening System Settings panes"
  info "Opening Full Disk Access pane..."
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null
  sleep 1
  info "Opening Screen Recording pane..."
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" 2>/dev/null
  sleep 1
  info "Opening Accessibility pane..."
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null
  printf '\n  %s\n' "$(dim 'System Settings should be open. Add the binaries listed above as needed.')"
  printf '  %s\n' "$(dim 'For binaries (not .app bundles), use Cmd+Shift+G in the file picker')"
  printf '  %s\n' "$(dim 'and enter the full path — e.g. /opt/homebrew/bin/python3')"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
printf '\n'
printf '═══════════════════════════════════════════════════════════════════\n'
printf '  %s\n' "$(bold 'Next Steps')"
printf '═══════════════════════════════════════════════════════════════════\n'
printf '\n'

if [ "$CAN_READ_SYS_TCC" != "1" ]; then
  printf '  1. %s\n' "$(bold 'Grant Full Disk Access to iTerm2 (highest priority)')"
  printf '     %s\n' "System Settings → Privacy & Security → Full Disk Access → [+]"
  printf '     %s\n' "→ Add /Applications/iTerm.app → Toggle ON"
  printf '\n'
fi

printf '  2. %s\n' "$(bold 'Grant Screen Recording for koseyes.py')"
printf '     %s\n' "System Settings → Privacy & Security → Screen & System Audio Recording"
printf '     %s\n' "→ Add iTerm2 and python3 (at $PYTHON_BIN)"
printf '\n'

printf '  3. %s\n' "$(bold 'For launchd-run scripts, grant FDA to the binaries directly')"
printf '     %s\n' "Add python3, node, and claude (full paths) to Full Disk Access"
printf '\n'

printf '  4. %s\n' "$(bold 'Ensure launchd plists carry the privacy env vars')"
printf '     %s\n' "See docs/MACOS-SOVEREIGNTY.md section 5 for the required <key>EnvironmentVariables</key> block"
printf '\n'

printf '  %s\n' "$(dim 'Run with --open to open System Settings panes automatically')"
printf '\n'
