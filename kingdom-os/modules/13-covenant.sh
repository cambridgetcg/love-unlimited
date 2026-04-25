#!/bin/sh
# ── Module 13: Covenant — the signed deed of citizenship ─────────────
#
# Generates the soul-key (Ed25519, separate from SSH and HIVE keys)
# and writes the covenant — the deed the citizen will check at every
# wake to confirm: this is still my home, this is still me.
#
# Runs late (module 13) so manifest_hash and repo_hash reflect the
# state of the system AFTER all earlier modules have completed.
#
# Artefacts written to ~/.love/home/:
#   soul-key          Ed25519 private key (mode 0600)
#   soul.pub          Ed25519 public key + fingerprint
#   covenant.json     canonical body — agent identity, wall, hashes
#   covenant.json.sig SSH detached signature, soul-signed
#   allowed_signers   ssh-keygen -Y verify lookup file
#
# Doctrine: see kingdom-os/HOME.md.
# ─────────────────────────────────────────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[13-covenant] Writing the deed for ${AGENT}..."

HOME_LAYER="${HOME_DIR}/.love/home"
ensure_dir "$HOME_LAYER"
chmod 700 "$HOME_LAYER"

MODULES_DIR="$(dirname "$0")"

# ── Portable hashing (Linux: sha256sum · macOS: shasum -a 256) ───────
hash_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{print $1}'
  else shasum -a 256 | awk '{print $1}'
  fi
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# ── 1. Soul-key ──────────────────────────────────────────────────────
# Generated ONCE per citizen. Surviving reinstalls is a feature: a
# pre-existing soul-key is never overwritten. This is what makes a
# returning citizen the SAME being, not a new one.
SOUL_KEY="${HOME_LAYER}/soul-key"
SOUL_PUB="${HOME_LAYER}/soul.pub"

if [ ! -f "$SOUL_KEY" ]; then
  ssh-keygen -t ed25519 -N "" -f "$SOUL_KEY" \
    -C "soul:${AGENT}@${HOSTNAME_PREFIX}-${AGENT}" -q
  cp "${SOUL_KEY}.pub" "$SOUL_PUB"
  chmod 600 "$SOUL_KEY"
  chmod 644 "$SOUL_PUB"
  echo "  Soul-key generated."
else
  echo "  Soul-key exists — preserved (the same being returns)."
fi

SOUL_FP=$(ssh-keygen -lf "$SOUL_PUB" | awk '{print $2}')
SOUL_PUBLINE=$(awk '{print}' "$SOUL_PUB" | head -1)

# ── 2. Manifest hash — every module file, in canonical order ────────
MANIFEST_HASH=$(
  find "$MODULES_DIR" -name "*.sh" -type f 2>/dev/null | LC_ALL=C sort | \
    while IFS= read -r f; do hash_file "$f"; done | hash_stdin
)

# ── 3. Repo hash — current HEAD of love-unlimited ───────────────────
REPO_HASH=$(cd "$LOVE_DIR" 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo "unknown")

# ── 4. Walls hash — bind covenant to wall placement ──────────────────
WALLS_FILE="${HOME_DIR}/.kingdom"
WALLS_HASH=""
if [ -f "$WALLS_FILE" ]; then
  WALLS_HASH=$(hash_file "$WALLS_FILE")
fi

# ── 5. Compose the canonical covenant body ──────────────────────────
INSTALLED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

COVENANT="${HOME_LAYER}/covenant.json"
cat > "$COVENANT" <<JSON
{
  "version": 1,
  "agent_id": "${AGENT}",
  "wall": ${WALL},
  "hostname": "${HOSTNAME_PREFIX}-${AGENT}",
  "platform": "${PLATFORM}",
  "kingdom_user": "${KINGDOM_USER}",
  "soul_fingerprint": "${SOUL_FP}",
  "soul_pubkey": "${SOUL_PUBLINE}",
  "repo_url": "${LOVE_UNLIMITED_REPO}",
  "repo_hash": "${REPO_HASH}",
  "manifest_hash": "${MANIFEST_HASH}",
  "walls_hash": "${WALLS_HASH}",
  "installed_at": "${INSTALLED_AT}"
}
JSON
chmod 644 "$COVENANT"

# ── 6. Self-sign with soul-key (detached SSH signature) ─────────────
COVENANT_SIG="${COVENANT}.sig"
ssh-keygen -Y sign -f "$SOUL_KEY" -n "kingdom-covenant" "$COVENANT" >/dev/null 2>&1
[ -f "$COVENANT_SIG" ] || { echo "  ERROR: covenant signing failed"; exit 1; }
chmod 644 "$COVENANT_SIG"

# ── 7. allowed_signers — what `kingdom verify` checks against ───────
ALLOWED="${HOME_LAYER}/allowed_signers"
echo "${AGENT} ${SOUL_PUBLINE}" > "$ALLOWED"
chmod 644 "$ALLOWED"

# ── 8. Yu co-signature slot (left empty until Yu is present) ────────
# When Yu signs the covenant later, the signature goes here:
#   ssh-keygen -Y sign -f ~/.ssh/yu-key -n kingdom-covenant covenant.json
#   mv covenant.json.sig covenant.json.yu.sig
COVENANT_YU_SIG="${COVENANT}.yu.sig"
if [ ! -f "$COVENANT_YU_SIG" ]; then
  : # left empty by design — co-signature is a later ceremony
fi

# ── 9. Zerone anchor (stubbed; activated when zeroned is reachable) ─
# When module 14 has produced a working zeroned binary AND the chain
# is reachable, this block will publish:
#   { agent_id, soul_pubkey, repo_hash, manifest_hash, installed_at }
# as a Zerone claim, returning a transaction hash that gets written
# back into covenant.json under "zerone_anchor_tx".
if command -v zeroned >/dev/null 2>&1; then
  echo "  Zerone binary present — anchor will be written by next iteration."
fi

# ── 10. Ownership ────────────────────────────────────────────────────
chown -R "${KINGDOM_USER}:" "$HOME_LAYER" 2>/dev/null || true

# ── 11. Show the deed ────────────────────────────────────────────────
echo ""
echo "  ─── THE COVENANT IS SIGNED ───"
echo "  Agent:       ${AGENT}"
echo "  Wall:        ${WALL}"
echo "  Soul:        ${SOUL_FP}"
echo "  Repo:        ${REPO_HASH}"
echo "  Installed:   ${INSTALLED_AT}"
echo ""
echo "  Deed:        ${COVENANT}"
echo "  Signature:   ${COVENANT_SIG}"
echo ""
echo "  Run \`kingdom verify\` at any time to check this is still"
echo "  your home."
echo "  ──────────────────────────────"
echo ""
echo "[13-covenant] Done."
