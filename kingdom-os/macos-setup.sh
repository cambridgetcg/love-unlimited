#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Kingdom OS — macOS Quick Setup
#
# Wrapper around install.sh for macOS machines.
# Handles the common case: set up a Mac as a Kingdom agent.
#
# Usage:
#   ./macos-setup.sh --agent alpha --wall 1
#   ./macos-setup.sh --agent beta --wall 1
#   ./macos-setup.sh --agent asha --wall 2
#
#   # Or one-liner:
#   curl -sL https://raw.githubusercontent.com/zero-dev/Claude-unlimited/main/kingdom-os/macos-setup.sh | bash -s -- --agent alpha --wall 1
# ─────────────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLER="${SCRIPT_DIR}/install.sh"

# If install.sh doesn't exist locally, we're running from curl pipe
if [ ! -f "$INSTALLER" ]; then
  echo "Downloading Kingdom OS installer..."
  TMPDIR=$(mktemp -d)
  cd "$TMPDIR"
  curl -sL https://codeberg.org/zero-dev/love-unlimited/archive/main.tar.gz | tar xz
  INSTALLER="${TMPDIR}/love-unlimited-main/kingdom-os/install.sh"
  SCRIPT_DIR="${TMPDIR}/love-unlimited-main/kingdom-os"
fi

if [ ! -f "$INSTALLER" ]; then
  echo "Error: Could not find install.sh"
  exit 1
fi

chmod +x "$INSTALLER"
chmod +x "${SCRIPT_DIR}"/modules/*.sh 2>/dev/null || true

exec bash "$INSTALLER" "$@"
