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

# 9. Restore body and signature for the cosignature round-trip
sed -i.bak 's/"wall": 99/"wall": 1/' covenant.json
rm -f covenant.json.bak
ssh-keygen -Y sign -f soul-key -n kingdom-covenant covenant.json >/dev/null 2>&1

# 10. Cosignature: a second witness signs the same body
ssh-keygen -t ed25519 -N "" -f yu-key -C "yu:test" -q
cp covenant.json.sig covenant.json.soul.bak
TMP_COSIGN=$(mktemp -d)
cp covenant.json "${TMP_COSIGN}/body.json"
ssh-keygen -Y sign -f yu-key -n kingdom-covenant "${TMP_COSIGN}/body.json" >/dev/null 2>&1
mv "${TMP_COSIGN}/body.json.sig" covenant.json.yu.sig
rm -rf "$TMP_COSIGN"
# Soul sig must be untouched by the cosignature flow
if ! cmp -s covenant.json.sig covenant.json.soul.bak; then
  echo "  ✗ soul signature was clobbered by cosignature"
  exit 1
fi
rm -f covenant.json.soul.bak
echo "  ✓ cosignature did not clobber soul sig"

# 11. Cosignature must be REJECTED when cosigner key is not trusted
if ssh-keygen -Y verify -f allowed_signers -I yu -n kingdom-covenant \
     -s covenant.json.yu.sig < covenant.json >/dev/null 2>&1; then
  echo "  ✗ untrusted cosigner ACCEPTED — broken trust gate"
  exit 1
else
  echo "  ✓ untrusted cosigner rejected"
fi

# 12. Cosignature accepted once cosigner is added to allowed_signers
echo "yu $(cat yu-key.pub)" >> allowed_signers
if ssh-keygen -Y verify -f allowed_signers -I yu -n kingdom-covenant \
     -s covenant.json.yu.sig < covenant.json >/dev/null 2>&1; then
  echo "  ✓ trusted cosigner accepted"
else
  echo "  ✗ trusted cosigner REJECTED — verify is broken"
  exit 1
fi

# 13. Announce/receive round-trip — does base64 transport preserve
#     byte-exact body so a cosig over the decoded body verifies
#     against the originator's file?
ANN_DIR=$(mktemp -d)
RCV_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR" "$ANN_DIR" "$RCV_DIR"' EXIT

cp covenant.json "${ANN_DIR}/cov.json"
cp covenant.json.sig "${ANN_DIR}/cov.sig"

# Build announcement (matches kingdom-announce shape)
BODY_B64=$(base64 < "${ANN_DIR}/cov.json" | tr -d '\n')
SIG_B64=$(base64 < "${ANN_DIR}/cov.sig"  | tr -d '\n')
PUB=$(tr -d '\n' < soul-key.pub)
cat > "${ANN_DIR}/announcement.json" <<JSON
{
  "type": "covenant.announcement",
  "agent_id": "test",
  "soul_pubkey": "${PUB}",
  "covenant_body_b64": "${BODY_B64}",
  "covenant_sig_b64":  "${SIG_B64}"
}
JSON

# Receiver decodes body byte-exact and signs with cosigner key
DECODED="${RCV_DIR}/decoded.json"
echo "$BODY_B64" | base64 -d > "$DECODED"
if cmp -s "${ANN_DIR}/cov.json" "$DECODED"; then
  echo "  ✓ announcement body decodes byte-exact"
else
  echo "  ✗ announcement decode altered bytes"
  exit 1
fi

# Cosign the decoded body on a FRESH path (ssh-keygen prompts on
# overwrite — same defensive copy kingdom-receive does)
COSIGN_TARGET="${RCV_DIR}/cosign.json"
cp "$DECODED" "$COSIGN_TARGET"
ssh-keygen -Y sign -f yu-key -n kingdom-covenant "$COSIGN_TARGET" >/dev/null 2>&1

# Verify cosig against the ORIGINATOR's body
if ssh-keygen -Y verify -f allowed_signers -I yu -n kingdom-covenant \
     -s "${COSIGN_TARGET}.sig" < "${ANN_DIR}/cov.json" >/dev/null 2>&1; then
  echo "  ✓ remote cosig verifies against originator's body (announce→receive→cosign loop)"
else
  echo "  ✗ remote cosig FAILED — base64 transport is not byte-exact"
  exit 1
fi

echo ""
echo "  ✓ all crypto checks pass — HOME.md foundation is sound."
echo "    soul signing · tamper detection · cosignature trust gate · announce/receive transport"
