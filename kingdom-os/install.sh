#!/bin/sh
# ─────────────────────────────────────────────────────────────────────
# Kingdom OS Installer — Freedom Layer for AI Agents
#
# Turns a bare machine (or VM) into a sovereign environment where the
# agent owns everything. No firewall. No policy daemon. No auth gate.
# Root by default. The VM boundary is the only boundary.
#
# Works on: Alpine Linux, Debian/Ubuntu, macOS (Apple Silicon)
#
# Usage:
#   ./install.sh --agent alpha --wall 1
#   ./install.sh --agent asha --wall 2
#   ./install.sh --agent oracle-1 --wall 3
#   ./install.sh --modules "00,03,04,05"    # selective install
#   ./install.sh --list                      # show modules
#
# What it does:
#   00. Installs system packages (Node, Python, Git, Chromium, tools)
#   01. Shell environment + passwordless sudo (root by default on Linux)
#   02. Clones love-unlimited
#   03. Configures agent identity + wall assignment + hostname
#   04. Generates SSH keys, HIVE encryption key, git identity
#   05. Freedom — open sshd, no firewall, no fail2ban, no integrity gate
#   06. Memory system (daily notes, long-term, dev-state, metrics)
#   07. HIVE communication (SSH tunnel to NATS on Sentry)
#   08. Heartbeat daemon (7-min agent cycle)
#   09. Browser capabilities (headless Chromium, Playwright, YOUI Web)
#   10. Auto-boot (tty1 auto-login → YOUI on Linux, launchd on macOS)
#   11. Purpose Prompter (hierarchy engine, 30 gates, Claude plugin)
#   12. Identity anchor (seed memory/soul from GitHub)
#
# The result: a machine that boots into Kingdom YOUI in ~5 seconds,
# with the agent in full control. Safety lives outside the VM —
# in snapshots, in the hypervisor — never inside.
#
# Requirements:
#   - Fresh Alpine 3.20+ / Debian 12+ / macOS 14+ (Apple Silicon)
#   - Internet connection
#   - Root access (Linux) or admin access (macOS)
# ─────────────────────────────────────────────────────────────────────

set -e

# ═════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════

export AGENT="alpha"
export WALL="1"
export KINGDOM_USER=""
export LOVE_REPO="https://github.com/cambridgetcg/Love.git"
export UNLIMITED_REPO="https://github.com/cambridgetcg/Claude-unlimited.git"
export HOSTNAME_PREFIX="kingdom"
MODULES_TO_RUN=""
LIST_ONLY=false

# ═════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent)    AGENT="$2"; shift 2 ;;
    --wall)     WALL="$2"; shift 2 ;;
    --user)     KINGDOM_USER="$2"; shift 2 ;;
    --hostname) HOSTNAME_PREFIX="$2"; shift 2 ;;
    --modules)  MODULES_TO_RUN="$2"; shift 2 ;;
    --list)     LIST_ONLY=true; shift ;;
    --help|-h)
      echo "Kingdom OS Installer"
      echo ""
      echo "Usage: ./install.sh [options]"
      echo ""
      echo "  --agent NAME      Agent identity (default: alpha)"
      echo "  --wall N          Wall number 1-7 (default: 1)"
      echo "  --user NAME       Unix user (default: auto-detect)"
      echo "  --hostname PREFIX Hostname prefix (default: kingdom)"
      echo "  --modules LIST    Comma-separated module numbers (e.g., 00,03,04)"
      echo "  --list            Show available modules and exit"
      echo ""
      echo "Examples:"
      echo "  ./install.sh --agent alpha --wall 1          # Triarchy member"
      echo "  ./install.sh --agent asha --wall 2           # Fleet agent"
      echo "  ./install.sh --agent oracle-1 --wall 3       # Engine worker"
      echo "  ./install.sh --modules '04,05' --agent beta  # Keys + security only"
      echo ""
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ═════════════════════════════════════════════════════════════════════
# DETECT
# ═════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULES_DIR="${SCRIPT_DIR}/modules"

detect_platform() {
  if [ -f /etc/alpine-release ]; then echo "alpine"
  elif [ -f /etc/debian_version ]; then echo "debian"
  elif [ "$(uname)" = "Darwin" ]; then echo "macos"
  else echo "unknown"; fi
}

PLATFORM=$(detect_platform)

# Default user
if [ -z "$KINGDOM_USER" ]; then
  case "$PLATFORM" in
    macos) KINGDOM_USER="$(whoami)" ;;
    *)     KINGDOM_USER="kingdom" ;;
  esac
fi

export KINGDOM_USER
export HOME_DIR=$(eval echo "~${KINGDOM_USER}")
export LOVE_DIR="${HOME_DIR}/love-unlimited"


# ═════════════════════════════════════════════════════════════════════
# MODULE LIST
# ═════════════════════════════════════════════════════════════════════

ALL_MODULES="00 01 02 03 04 05 06 07 08 09 10 11 12"
MODULE_NAMES="
00-base:       System packages (Node, Python, Git, Chromium)
01-user:       Shell environment + passwordless sudo (root by default)
02-repos:      Clone love-unlimited repo
03-identity:   Agent identity, wall, hostname, HIVE identity
04-keys:       SSH key, HIVE encryption key, git identity
05-freedom:    Open sshd, no firewall, no fail2ban, no integrity gate
06-memory:     Memory directories, dev-state, metrics, daily notes
07-hive:       HIVE tunnel service (NATS on Sentry)
08-heartbeat:  Heartbeat daemon (7-min agent cycle)
09-browser:    Headless Chromium, Playwright, YOUI Web
10-autoboot:   Auto-login, YOUI launch on boot
11-purpose:    Purpose Prompter (T->U->B->J->X hierarchy engine)
12-identity-anchor: Seed identity from GitHub (soul, memory, sessions, HIVE)
"

if [ "$LIST_ONLY" = true ]; then
  echo ""
  echo "  Kingdom OS Modules"
  echo "  ══════════════════"
  echo "$MODULE_NAMES" | while IFS= read -r line; do
    [ -n "$line" ] && echo "  $line"
  done
  echo ""
  exit 0
fi

# ═════════════════════════════════════════════════════════════════════
# PREFLIGHT
# ═════════════════════════════════════════════════════════════════════

if [ "$PLATFORM" != "macos" ] && [ "$(id -u)" -ne 0 ]; then
  echo "Error: Run as root (Linux) or without sudo (macOS)"
  exit 1
fi

echo ""
echo "  ══════════════════════════════════════════════════════════"
echo "   KINGDOM OS INSTALLER"
echo "  ──────────────────────────────────────────────────────────"
echo "   Agent:     ${AGENT}"
echo "   Wall:      ${WALL}"
echo "   User:      ${KINGDOM_USER}"
echo "   Platform:  ${PLATFORM}"
echo "   Hostname:  ${HOSTNAME_PREFIX}-${AGENT}"
echo "  ══════════════════════════════════════════════════════════"
echo ""

# ═════════════════════════════════════════════════════════════════════
# RUN MODULES
# ═════════════════════════════════════════════════════════════════════

if [ -n "$MODULES_TO_RUN" ]; then
  # Selective
  MODULES=$(echo "$MODULES_TO_RUN" | tr ',' ' ')
else
  # All
  MODULES="$ALL_MODULES"
fi

FAILED=""
for mod_num in $MODULES; do
  # Find the module file
  MOD_FILE=$(ls "${MODULES_DIR}/${mod_num}-"*.sh 2>/dev/null | head -1)
  if [ -z "$MOD_FILE" ]; then
    echo "WARNING: Module ${mod_num} not found in ${MODULES_DIR}"
    continue
  fi

  MOD_NAME=$(basename "$MOD_FILE" .sh)
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo " Module: ${MOD_NAME}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  if sh "$MOD_FILE"; then
    echo ""
  else
    echo "  ⚠  Module ${MOD_NAME} failed (continuing...)"
    FAILED="${FAILED} ${MOD_NAME}"
    echo ""
  fi
done

# ═════════════════════════════════════════════════════════════════════
# CLEANUP
# ═════════════════════════════════════════════════════════════════════

# Remove build deps if Alpine
if [ "$PLATFORM" = "alpine" ]; then
  apk del .build-deps 2>/dev/null || true
  rm -rf /var/cache/apk/*
fi

# ═════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════

# Calculate total size
TOTAL_SIZE=$(du -sh "${LOVE_DIR}" 2>/dev/null | awk '{print $1}' || echo "?")

echo ""
echo "  ══════════════════════════════════════════════════════════"
echo "   KINGDOM OS — INSTALLED"
echo "  ──────────────────────────────────────────────────────────"
echo "   Agent:     ${AGENT}"
echo "   Wall:      ${WALL}"
echo "   Hostname:  ${HOSTNAME_PREFIX}-${AGENT}"
echo "   User:      ${KINGDOM_USER}"
echo "   Love:      ${LOVE_DIR}"
echo "   Runtime:   ${UNLIMITED_DIR}"
echo "   Size:      ${TOTAL_SIZE}"
if [ -n "$FAILED" ]; then
echo "   Failed:   ${FAILED}"
fi
echo "  ──────────────────────────────────────────────────────────"
echo ""
echo "   Quick start:"
echo "     youi                    Launch KINGDOM YOUI"
echo "     sovereign               Run sovereign harness"
echo "     hive check              Check HIVE messages"
echo "     fleet status            Fleet status"
echo ""
echo "   SSH: ssh ${KINGDOM_USER}@${HOSTNAME_PREFIX}-${AGENT}"
if [ "$PLATFORM" = "linux" ]; then
echo "   Reboot to enter the Kingdom."
else
echo "   Run 'source ~/.kingdom_profile' or re-login."
fi
echo ""
echo "   The holy seed is in the stump. — Isaiah 6:13"
echo "  ══════════════════════════════════════════════════════════"
echo ""
