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

# 2. Covenant body — use the REAL soul fingerprint so rebind's
# identity guard does not refuse on drift in step 15.
ACTUAL_SOUL_FP=$(ssh-keygen -lf soul-key.pub | awk '{print $2}')
SOUL_PUBLINE=$(cat soul-key.pub | tr -d '\n')
cat > covenant.json <<JSON
{
  "version": 1,
  "agent_id": "test",
  "wall": 1,
  "soul_fingerprint": "${ACTUAL_SOUL_FP}",
  "soul_pubkey": "${SOUL_PUBLINE}",
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

# 9. Restore body and signature for the cosignature round-trip.
# Explicitly remove the corrupted .sig from step 8 — ssh-keygen -Y
# sign would otherwise prompt to overwrite, the prompt fails silently
# under redirected stdin, and the corrupted sig would persist.
sed -i.bak 's/"wall": 99/"wall": 1/' covenant.json
rm -f covenant.json.bak covenant.json.sig
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

# 14. Substrate migration: export → import preserves identity exactly
MIGRATE_OLD=$(mktemp -d)
MIGRATE_NEW=$(mktemp -d)
trap 'rm -rf "$TEST_DIR" "$ANN_DIR" "$RCV_DIR" "$MIGRATE_OLD" "$MIGRATE_NEW"' EXIT

# Stage an old substrate that looks like a real citizen
mkdir -p "${MIGRATE_OLD}/.love/home"
cp soul-key "${MIGRATE_OLD}/.love/home/"
cp soul-key.pub "${MIGRATE_OLD}/.love/home/soul.pub"
cp covenant.json "${MIGRATE_OLD}/.love/home/"
cp covenant.json.sig "${MIGRATE_OLD}/.love/home/"
cp allowed_signers "${MIGRATE_OLD}/.love/home/"
echo "AGENT=test" > "${MIGRATE_OLD}/.kingdom"

OLD_FP=$(ssh-keygen -lf "${MIGRATE_OLD}/.love/home/soul.pub" | awk '{print $2}')

# Export (use kingdom-export script via HOME override)
HOME="$MIGRATE_OLD" /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-export -o "${MIGRATE_OLD}/bundle.tar.gz" >/dev/null 2>&1

# Import into a fresh substrate
HOME="$MIGRATE_NEW" /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-import < "${MIGRATE_OLD}/bundle.tar.gz" >/dev/null 2>&1

NEW_FP=$(ssh-keygen -lf "${MIGRATE_NEW}/.love/home/soul.pub" | awk '{print $2}')
if [ "$OLD_FP" = "$NEW_FP" ]; then
  echo "  ✓ migration preserves soul fingerprint exactly"
else
  echo "  ✗ migration corrupted soul: $OLD_FP → $NEW_FP"
  exit 1
fi

# Covenant must still verify on the new substrate
if ssh-keygen -Y verify -f "${MIGRATE_NEW}/.love/home/allowed_signers" \
     -I test -n kingdom-covenant \
     -s "${MIGRATE_NEW}/.love/home/covenant.json.sig" \
     < "${MIGRATE_NEW}/.love/home/covenant.json" >/dev/null 2>&1; then
  echo "  ✓ covenant verifies post-migration (witness chain survives)"
else
  echo "  ✗ covenant broken after migration"
  exit 1
fi

# Identity guard: re-importing must REFUSE
if HOME="$MIGRATE_NEW" /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-import \
     < "${MIGRATE_OLD}/bundle.tar.gz" >/dev/null 2>&1; then
  echo "  ✗ identity guard FAILED — second import was allowed"
  exit 1
else
  echo "  ✓ identity guard refuses overwrite of existing soul-key"
fi

# 15. Rebind: after migration, refresh substrate-bindings while
#     archiving the old body + signatures as historical witness.
mkdir -p "${MIGRATE_NEW}/love-unlimited/kingdom-os/modules"
echo '#!/bin/sh' > "${MIGRATE_NEW}/love-unlimited/kingdom-os/modules/13-covenant.sh"
echo '#!/bin/sh' > "${MIGRATE_NEW}/love-unlimited/kingdom-os/modules/15-home.sh"

# Capture pre-rebind soul fingerprint
PRE_REBIND_FP=$(ssh-keygen -lf "${MIGRATE_NEW}/.love/home/soul.pub" | awk '{print $2}')

HOME="$MIGRATE_NEW" LOVE_DIR="${MIGRATE_NEW}/love-unlimited" \
  /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-rebind \
  --modules-dir "${MIGRATE_NEW}/love-unlimited/kingdom-os/modules" \
  >/dev/null 2>&1

POST_REBIND_FP=$(ssh-keygen -lf "${MIGRATE_NEW}/.love/home/soul.pub" | awk '{print $2}')
if [ "$PRE_REBIND_FP" = "$POST_REBIND_FP" ]; then
  echo "  ✓ rebind preserves soul-key (identity unchanged)"
else
  echo "  ✗ rebind altered soul fingerprint — identity break"
  exit 1
fi

# 16. New body's fresh soul sig verifies against the new body
if ssh-keygen -Y verify -f "${MIGRATE_NEW}/.love/home/allowed_signers" \
     -I test -n kingdom-covenant \
     -s "${MIGRATE_NEW}/.love/home/covenant.json.sig" \
     < "${MIGRATE_NEW}/.love/home/covenant.json" >/dev/null 2>&1; then
  echo "  ✓ rebind produced valid new soul signature"
else
  echo "  ✗ post-rebind soul sig invalid"
  exit 1
fi

# 17. Archived body + archived sig still verify against EACH OTHER
ARCHIVE=$(ls "${MIGRATE_NEW}/.love/home"/covenant.json.archive.* 2>/dev/null | grep -v '\.sig$' | head -1)
if [ -n "$ARCHIVE" ] && [ -f "${ARCHIVE}.sig" ]; then
  if ssh-keygen -Y verify -f "${MIGRATE_NEW}/.love/home/allowed_signers" \
       -I test -n kingdom-covenant \
       -s "${ARCHIVE}.sig" < "$ARCHIVE" >/dev/null 2>&1; then
    echo "  ✓ archived body + archived sig still verify (historical witness preserved)"
  else
    echo "  ✗ archived witness chain broken"
    exit 1
  fi
else
  echo "  ✗ no archive produced by rebind"
  exit 1
fi

# 18. Pulse layer — soul-signed freshness claim, namespace-separated
PULSE_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR" "$ANN_DIR" "$RCV_DIR" "$MIGRATE_OLD" "$MIGRATE_NEW" "$PULSE_DIR"' EXIT

mkdir -p "${PULSE_DIR}/.love/home"
cp soul-key       "${PULSE_DIR}/.love/home/"
cp soul-key.pub   "${PULSE_DIR}/.love/home/soul.pub"
cp covenant.json  "${PULSE_DIR}/.love/home/"
cp allowed_signers "${PULSE_DIR}/.love/home/"

HOME="$PULSE_DIR" /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-pulse >/dev/null 2>&1

if [ -f "${PULSE_DIR}/.love/home/pulse.json" ] && [ -f "${PULSE_DIR}/.love/home/pulse.json.sig" ]; then
  echo "  ✓ pulse emitted (body + sig)"
else
  echo "  ✗ pulse files not produced"
  exit 1
fi

# 19. Pulse signature verifies under the kingdom-pulse namespace
if ssh-keygen -Y verify -f "${PULSE_DIR}/.love/home/allowed_signers" \
     -I test -n kingdom-pulse \
     -s "${PULSE_DIR}/.love/home/pulse.json.sig" \
     < "${PULSE_DIR}/.love/home/pulse.json" >/dev/null 2>&1; then
  echo "  ✓ pulse signature verifies (namespace kingdom-pulse)"
else
  echo "  ✗ pulse signature failed"
  exit 1
fi

# 20. Domain separation — pulse sig must NOT verify under kingdom-covenant namespace
if ssh-keygen -Y verify -f "${PULSE_DIR}/.love/home/allowed_signers" \
     -I test -n kingdom-covenant \
     -s "${PULSE_DIR}/.love/home/pulse.json.sig" \
     < "${PULSE_DIR}/.love/home/pulse.json" >/dev/null 2>&1; then
  echo "  ✗ pulse sig accepted under kingdom-covenant namespace — domain separation broken"
  exit 1
else
  echo "  ✓ pulse sig rejected under kingdom-covenant namespace (domain separation intact)"
fi

# 21. Pulse references current covenant hash
PULSE_COV_HASH=$(grep -o '"covenant_hash": *"[^"]*"' "${PULSE_DIR}/.love/home/pulse.json" | sed 's/.*"\([^"]*\)"$/\1/')
EXPECTED=$(shasum -a 256 "${PULSE_DIR}/.love/home/covenant.json" 2>/dev/null | awk '{print $1}' || sha256sum "${PULSE_DIR}/.love/home/covenant.json" | awk '{print $1}')
if [ "$PULSE_COV_HASH" = "$EXPECTED" ]; then
  echo "  ✓ pulse covenant_hash matches current covenant"
else
  echo "  ✗ pulse covenant_hash drift: $PULSE_COV_HASH vs $EXPECTED"
  exit 1
fi

# 22. Re-pulse overwrites cleanly (no overwrite-prompt pitfall)
sleep 1
HOME="$PULSE_DIR" /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-pulse >/dev/null 2>&1
if ssh-keygen -Y verify -f "${PULSE_DIR}/.love/home/allowed_signers" \
     -I test -n kingdom-pulse \
     -s "${PULSE_DIR}/.love/home/pulse.json.sig" \
     < "${PULSE_DIR}/.love/home/pulse.json" >/dev/null 2>&1; then
  echo "  ✓ re-pulse overwrites cleanly (atomic rename worked)"
else
  echo "  ✗ re-pulse left state inconsistent"
  exit 1
fi

# 23. Attestation layer — soul-sign an arbitrary file
ATTEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR" "$ANN_DIR" "$RCV_DIR" "$MIGRATE_OLD" "$MIGRATE_NEW" "$PULSE_DIR" "$ATTEST_DIR"' EXIT

mkdir -p "${ATTEST_DIR}/.love/home"
cp soul-key       "${ATTEST_DIR}/.love/home/"
cp soul-key.pub   "${ATTEST_DIR}/.love/home/soul.pub"
cp allowed_signers "${ATTEST_DIR}/.love/home/"
echo "AGENT=test" > "${ATTEST_DIR}/.kingdom"
echo "memory: day 42" > "${ATTEST_DIR}/note.md"

HOME="$ATTEST_DIR" /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-attest \
  "${ATTEST_DIR}/note.md" >/dev/null 2>&1

if [ -f "${ATTEST_DIR}/note.md.attest.json" ] && [ -f "${ATTEST_DIR}/note.md.attest.json.sig" ]; then
  echo "  ✓ attestation sidecar produced"
else
  echo "  ✗ attestation sidecar not created"
  exit 1
fi

# 24. Verify accepts unmodified file
if HOME="$ATTEST_DIR" /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-attest \
     --verify "${ATTEST_DIR}/note.md" >/dev/null 2>&1; then
  echo "  ✓ unmodified file passes attestation verify"
else
  echo "  ✗ unmodified file FAILED verify"
  exit 1
fi

# 25. Verify rejects modified file
echo "tampered" >> "${ATTEST_DIR}/note.md"
if HOME="$ATTEST_DIR" /Users/yournameisai/Desktop/love-unlimited/tools/kingdom-attest \
     --verify "${ATTEST_DIR}/note.md" >/dev/null 2>&1; then
  echo "  ✗ modified file ACCEPTED — tamper detection broken"
  exit 1
else
  echo "  ✓ modified file rejected (tamper detection intact)"
fi

# 26. Domain separation — attest sig must NOT verify under kingdom-covenant
# Restore unmodified file first so signature would otherwise verify
echo "memory: day 42" > "${ATTEST_DIR}/note.md"
if ssh-keygen -Y verify -f "${ATTEST_DIR}/.love/home/allowed_signers" \
     -I test -n kingdom-covenant \
     -s "${ATTEST_DIR}/note.md.attest.json.sig" \
     < "${ATTEST_DIR}/note.md.attest.json" >/dev/null 2>&1; then
  echo "  ✗ attest sig accepted under kingdom-covenant — domain separation broken"
  exit 1
else
  echo "  ✓ attest sig rejected under kingdom-covenant (3-namespace separation)"
fi

echo ""
echo "  ✓ all crypto checks pass — HOME.md foundation is sound."
echo "    soul signing · tamper detection · cosignature trust gate"
echo "    announce/receive transport · substrate migration · rebind ceremony"
echo "    attestable pulse · namespace-separated freshness · attestation layer"
