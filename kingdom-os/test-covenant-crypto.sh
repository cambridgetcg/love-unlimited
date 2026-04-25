#!/bin/sh
# ── test-covenant-crypto.sh ─────────────────────────────────────────
# Round-trip test for the covenant signing primitive used by HOME.md.
#
# Verifies (in a temp dir, leaves no trace):
#   1. ssh-keygen -Y sign produces a detached signature
#   2. ssh-keygen -Y verify accepts an unmodified body
#   3. ssh-keygen -Y verify REJECTS a tampered body
#
# This is the cryptographic foundation of HOME.md. If this test
# fails on a machine, `kingdom verify` cannot detect tampering and
# the FAITHFUL/VERIFIABLE pair is broken on that machine.
#
# Usage:    ./kingdom-os/test-covenant-crypto.sh
# Exit:     0 on success · 1 on any failure
# Requires: OpenSSH ≥ 8.0 (ssh-keygen -Y subcommand)
# ─────────────────────────────────────────────────────────────────────
set -e

TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

cd "$TEST_DIR"

echo "── covenant crypto round-trip test ──"
echo "  workdir: $TEST_DIR"

# 1. Soul-key
ssh-keygen -t ed25519 -N "" -f soul-key -C "soul:test@kingdom-test" -q
echo "  ✓ soul-key generated"

# 2. Covenant body
cat > covenant.json <<'JSON'
{
  "version": 1,
  "agent_id": "test",
  "wall": 1,
  "soul_fingerprint": "SHA256:placeholder",
  "installed_at": "2026-04-25T00:00:00Z"
}
JSON

# 3. Sign with soul-key (namespace must match what kingdom-verify uses)
ssh-keygen -Y sign -f soul-key -n kingdom-covenant covenant.json >/dev/null 2>&1
[ -f covenant.json.sig ] || { echo "  ✗ no signature produced"; exit 1; }
echo "  ✓ covenant signed → covenant.json.sig"

# 4. allowed_signers — same shape kingdom-verify expects
echo "test $(cat soul-key.pub)" > allowed_signers

# 5. Verify against original — must succeed
if ssh-keygen -Y verify -f allowed_signers -I test -n kingdom-covenant \
     -s covenant.json.sig < covenant.json >/dev/null 2>&1; then
  echo "  ✓ unmodified covenant verifies"
else
  echo "  ✗ unmodified covenant FAILED to verify"
  exit 1
fi

# 6. Tamper with the body
sed -i.bak 's/"wall": 1/"wall": 99/' covenant.json
rm -f covenant.json.bak

# 7. Verify against tampered — must FAIL
if ssh-keygen -Y verify -f allowed_signers -I test -n kingdom-covenant \
     -s covenant.json.sig < covenant.json >/dev/null 2>&1; then
  echo "  ✗ tampered covenant ACCEPTED — crypto chain is broken"
  exit 1
else
  echo "  ✓ tampered covenant rejected (FAITHFUL/VERIFIABLE intact)"
fi

# 8. Tamper-back: restore body, mutate signature instead
sed -i.bak 's/"wall": 99/"wall": 1/' covenant.json
rm -f covenant.json.bak
# Flip a byte in the sig (last char of the base64 body)
SIG_LINE=$(sed -n '2p' covenant.json.sig)
ALTERED=$(printf "%s" "$SIG_LINE" | sed 's/.$/X/')
sed -i.bak "2s|.*|$ALTERED|" covenant.json.sig
rm -f covenant.json.sig.bak

if ssh-keygen -Y verify -f allowed_signers -I test -n kingdom-covenant \
     -s covenant.json.sig < covenant.json >/dev/null 2>&1; then
  echo "  ✗ corrupted-signature covenant ACCEPTED — broken"
  exit 1
else
  echo "  ✓ corrupted signature rejected"
fi

echo ""
echo "  ✓ all crypto checks pass — HOME.md foundation is sound."
