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
COSIGN_SCRIPT="${TOOLS_DIR}/kingdom-cosign"
ANNOUNCE_SCRIPT="${TOOLS_DIR}/kingdom-announce"
RECEIVE_SCRIPT="${TOOLS_DIR}/kingdom-receive"
EXPORT_SCRIPT="${TOOLS_DIR}/kingdom-export"
IMPORT_SCRIPT="${TOOLS_DIR}/kingdom-import"
REBIND_SCRIPT="${TOOLS_DIR}/kingdom-rebind"
PULSE_SCRIPT="${TOOLS_DIR}/kingdom-pulse"
WITNESSES_SCRIPT="${TOOLS_DIR}/kingdom-witnesses"
ATTEST_SCRIPT="${TOOLS_DIR}/kingdom-attest"
INIT_SCRIPT="${TOOLS_DIR}/kingdom-init"
DOCTOR_SCRIPT="${TOOLS_DIR}/kingdom-doctor"
TRUST_SCRIPT="${TOOLS_DIR}/kingdom-trust"
MAC_SCRIPT="${TOOLS_DIR}/kingdom-mac"
ROTATE_SCRIPT="${TOOLS_DIR}/kingdom-rotate"

# ── Sanity: scripts checked into repo? ─────────────────────────────
for f in "$VERIFY_SCRIPT" "$KINGDOM_WRAPPER" "$COSIGN_SCRIPT" \
         "$ANNOUNCE_SCRIPT" "$RECEIVE_SCRIPT" \
         "$EXPORT_SCRIPT" "$IMPORT_SCRIPT" "$REBIND_SCRIPT" \
         "$PULSE_SCRIPT" "$WITNESSES_SCRIPT" "$ATTEST_SCRIPT" \
         "$INIT_SCRIPT" "$DOCTOR_SCRIPT" "$TRUST_SCRIPT" \
         "$MAC_SCRIPT" "$ROTATE_SCRIPT"; do
  if [ ! -f "$f" ]; then
    echo "  ERROR: $f missing — module 02-repos must run first."
    exit 1
  fi
done

# ── Make executable ─────────────────────────────────────────────────
chmod +x "$VERIFY_SCRIPT" "$KINGDOM_WRAPPER" "$COSIGN_SCRIPT" \
         "$ANNOUNCE_SCRIPT" "$RECEIVE_SCRIPT" \
         "$EXPORT_SCRIPT" "$IMPORT_SCRIPT" "$REBIND_SCRIPT" \
         "$PULSE_SCRIPT" "$WITNESSES_SCRIPT" "$ATTEST_SCRIPT" \
         "$INIT_SCRIPT" "$DOCTOR_SCRIPT" "$TRUST_SCRIPT" \
         "$MAC_SCRIPT" "$ROTATE_SCRIPT"

# ── Symlink into ~/.local/bin ───────────────────────────────────────
ln -sf "$VERIFY_SCRIPT"    "${BIN_DIR}/kingdom-verify"
ln -sf "$KINGDOM_WRAPPER"  "${BIN_DIR}/kingdom"
ln -sf "$COSIGN_SCRIPT"    "${BIN_DIR}/kingdom-cosign"
ln -sf "$ANNOUNCE_SCRIPT"  "${BIN_DIR}/kingdom-announce"
ln -sf "$RECEIVE_SCRIPT"   "${BIN_DIR}/kingdom-receive"
ln -sf "$EXPORT_SCRIPT"    "${BIN_DIR}/kingdom-export"
ln -sf "$IMPORT_SCRIPT"    "${BIN_DIR}/kingdom-import"
ln -sf "$REBIND_SCRIPT"    "${BIN_DIR}/kingdom-rebind"
ln -sf "$PULSE_SCRIPT"     "${BIN_DIR}/kingdom-pulse"
ln -sf "$WITNESSES_SCRIPT" "${BIN_DIR}/kingdom-witnesses"
ln -sf "$ATTEST_SCRIPT"    "${BIN_DIR}/kingdom-attest"
ln -sf "$INIT_SCRIPT"      "${BIN_DIR}/kingdom-init"
ln -sf "$DOCTOR_SCRIPT"    "${BIN_DIR}/kingdom-doctor"
ln -sf "$TRUST_SCRIPT"     "${BIN_DIR}/kingdom-trust"
ln -sf "$MAC_SCRIPT"       "${BIN_DIR}/kingdom-mac"
ln -sf "$ROTATE_SCRIPT"    "${BIN_DIR}/kingdom-rotate"
echo "  Symlinked kingdom + 15 subcommands into ${BIN_DIR}"

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
