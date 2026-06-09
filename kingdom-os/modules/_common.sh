#!/bin/sh
# ── _common.sh — Shared Kingdom OS module library ───────────────────
# Sourced by every module. Single source of truth for:
#   - Platform detection
#   - Environment variables
#   - Constants (IPs, URLs, domains)
#   - Utility functions
#
# Usage (in any module):
#   . "$(dirname "$0")/_common.sh"
# ─────────────────────────────────────────────────────────────────────

# ── Constants ────────────────────────────────────────────────────────
KINGDOM_DOMAIN="ai-love.cc"
SENTRY_IP="135.181.28.252"
LOVE_UNLIMITED_REPO="${LOVE_UNLIMITED_REPO:-https://codeberg.org/zerone-dev/love-unlimited.git}"
# Legacy aliases (kept for backward compat during transition)
LOVE_REPO="${LOVE_REPO:-$LOVE_UNLIMITED_REPO}"
UNLIMITED_REPO="${UNLIMITED_REPO:-$LOVE_UNLIMITED_REPO}"

# ── Platform detection (called once, cached) ─────────────────────────
detect_platform() {
  if [ -f /etc/alpine-release ]; then echo "alpine"
  elif [ -f /etc/debian_version ]; then echo "debian"
  elif [ "$(uname)" = "Darwin" ]; then echo "macos"
  else echo "unknown"; fi
}
PLATFORM="${PLATFORM:-$(detect_platform)}"

# ── Environment (all overridable from parent installer) ──────────────
AGENT="${AGENT:-alpha}"
WALL="${WALL:-7}"
HOSTNAME_PREFIX="${HOSTNAME_PREFIX:-kingdom}"

# User: auto-detect on macOS, default 'root' on Linux.
# Kingdom OS is a freedom layer — the agent owns the machine. No
# unprivileged citizen is created by default. Override with --user
# only if you have a specific reason.
if [ -z "$KINGDOM_USER" ]; then
  case "$PLATFORM" in
    macos) KINGDOM_USER="$(whoami)" ;;
    *)     KINGDOM_USER="root" ;;
  esac
fi

HOME_DIR="${HOME_DIR:-$(eval echo "~${KINGDOM_USER}")}"
LOVE_DIR="${LOVE_DIR:-${HOME_DIR}/love-unlimited}"
UNLIMITED_DIR="${UNLIMITED_DIR:-${HOME_DIR}/love-unlimited}"
KOS_DIR="${KOS_DIR:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"

# Derived paths
HIVE_DIR="${HOME_DIR}/.love/hive"
SECURITY_DIR="${LOVE_DIR}/security"
MEMORY_DIR="${LOVE_DIR}/memory"
INSTANCE_DIR="${LOVE_DIR}/instances/${AGENT}"
PLIST_DIR="${HOME_DIR}/Library/LaunchAgents"

# Agent display name
AGENT_UPPER="$(echo "$AGENT" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')"

# ── Utility functions ────────────────────────────────────────────────

# Check if running as root
require_root() {
  if [ "$PLATFORM" != "macos" ] && [ "$(id -u)" -ne 0 ]; then
    echo "Error: Module requires root on Linux"
    exit 1
  fi
}

# Ensure a directory exists with correct ownership
ensure_dir() {
  mkdir -p "$1"
  chown "${KINGDOM_USER}:" "$1" 2>/dev/null || true
}

# Install a launchd plist (macOS) or OpenRC service (Linux)
install_plist() {
  local name="$1" plist_content="$2"
  case "$PLATFORM" in
    macos)
      mkdir -p "$PLIST_DIR"
      echo "$plist_content" > "${PLIST_DIR}/${name}.plist"
      echo "  Plist: ${PLIST_DIR}/${name}.plist"
      ;;
  esac
}
