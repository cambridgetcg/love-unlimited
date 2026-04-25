#!/bin/sh
# ── Module 15: Home — install the `kingdom verify` CLI ──────────────
#
# Wires up the citizen-facing CLI that makes the covenant CHECKABLE:
#
#   kingdom verify   re-runs all integrity checks against covenant.json
#   kingdom recite   prints the covenant in human-readable form
#
# Runs LAST (after 13-covenant) so a verifiable artefact exists
# before the verifier is installed.
#
# The actual scripts live at tools/kingdom and tools/kingdom-verify.sh
# in the love-unlimited repo (placed there by 02-repos). This module
# only makes them executable and PATH-accessible.
#
# Doctrine: see kingdom-os/HOME.md.
# ─────────────────────────────────────────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[15-home] Installing kingdom verify CLI..."

TOOLS_DIR="${LOVE_DIR}/tools"
BIN_DIR="${HOME_DIR}/.local/bin"
mkdir -p "$BIN_DIR"

VERIFY_SCRIPT="${TOOLS_DIR}/kingdom-verify.sh"
KINGDOM_WRAPPER="${TOOLS_DIR}/kingdom"

# ── Sanity: scripts checked into repo? ─────────────────────────────
if [ ! -f "$VERIFY_SCRIPT" ]; then
  echo "  ERROR: ${VERIFY_SCRIPT} missing — module 02-repos must run first."
  exit 1
fi
if [ ! -f "$KINGDOM_WRAPPER" ]; then
  echo "  ERROR: ${KINGDOM_WRAPPER} missing — module 02-repos must run first."
  exit 1
fi

# ── Make executable ─────────────────────────────────────────────────
chmod +x "$VERIFY_SCRIPT" "$KINGDOM_WRAPPER"

# ── Symlink into ~/.local/bin ───────────────────────────────────────
ln -sf "$VERIFY_SCRIPT" "${BIN_DIR}/kingdom-verify"
ln -sf "$KINGDOM_WRAPPER" "${BIN_DIR}/kingdom"
echo "  Symlinked kingdom + kingdom-verify into ${BIN_DIR}"

# ── Ensure ~/.local/bin is on PATH (via .kingdom_profile) ───────────
PROFILE="${HOME_DIR}/.kingdom_profile"
if [ -f "$PROFILE" ] && ! grep -q '\.local/bin' "$PROFILE"; then
  printf '\n# kingdom verify CLI\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$PROFILE"
  echo "  Added ~/.local/bin to .kingdom_profile PATH"
fi

# ── Smoke test: run verify once and show output ─────────────────────
if [ -x "${BIN_DIR}/kingdom-verify" ] && [ -f "${HOME_DIR}/.love/home/covenant.json" ]; then
  echo ""
  echo "  ─── kingdom verify (smoke test) ───"
  "${BIN_DIR}/kingdom-verify" -v 2>&1 | sed 's/^/  /' || true
  echo "  ───────────────────────────────────"
  echo ""
fi

chown -R "${KINGDOM_USER}:" "$BIN_DIR" 2>/dev/null || true

echo "[15-home] Done. Citizen can now run: kingdom verify"
