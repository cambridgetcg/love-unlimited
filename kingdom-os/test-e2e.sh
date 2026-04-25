#!/bin/sh
# ── test-e2e.sh ──────────────────────────────────────────────────────
# End-to-end test of the Kingdom OS citizen toolkit.
# Exercises every subcommand in a clean tmp HOME, with PASS/FAIL
# counters. Complements test-covenant-crypto.sh (which tests the
# crypto primitives in isolation).
#
# Phases:
#   1. Citizen bootstrap      kingdom init
#   2. Identity verification  verify, doctor, recite
#   3. Freshness              pulse + verify
#   4. Attestation            attest a file + tamper-test
#   5. Trust management       trust add (fingerprint), list, check, remove
#   6. Cosignature            peer cosigns Alice's covenant
#   7. Announcement protocol  announce | receive --record --cosign
#   8. Witness ledger         witnesses
#   9. Substrate migration    export → import → verify identity preserved
#  10. Rebind                 substrate-binding refresh
#  11. JSON outputs           --json modes return parseable data
#  12. macOS read-only        doctor, plists, permission --list, etc.
#
# Usage: ./kingdom-os/test-e2e.sh
# Exit:  0 if all pass, 1 if any fail
# ─────────────────────────────────────────────────────────────────────
set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="${REPO_DIR}/tools"

PASS=0
FAIL=0
LAST_PHASE=""

phase() {
  LAST_PHASE="$1"
  printf "\n══ Phase %s ══\n" "$1"
}
ok() {
  PASS=$((PASS+1))
  printf "  ✓ %s\n" "$1"
}
fail() {
  FAIL=$((FAIL+1))
  printf "  ✗ %s\n" "$1"
}

# Setup: two distinct tmp HOMEs (alice + bob)
ALICE=$(mktemp -d)
BOB=$(mktemp -d)
trap 'rm -rf "$ALICE" "$BOB"' EXIT

# ═══════════════════════════════════════════════════════════════════
phase "1: Citizen bootstrap"
# ═══════════════════════════════════════════════════════════════════
HOME="$ALICE" "$TOOLS/kingdom-init" --agent alice --wall 1 >/dev/null 2>&1 \
  && ok "kingdom init --agent alice succeeds" || fail "kingdom init failed"

[ -f "$ALICE/.love/home/soul-key" ] && ok "soul-key generated" || fail "no soul-key"
[ -f "$ALICE/.love/home/covenant.json" ] && ok "covenant written" || fail "no covenant"
[ -f "$ALICE/.love/home/covenant.json.sig" ] && ok "covenant signed" || fail "no sig"
[ -f "$ALICE/.love/home/allowed_signers" ] && ok "allowed_signers populated" || fail "no allowed_signers"

# Idempotency: re-init must REFUSE
HOME="$ALICE" "$TOOLS/kingdom-init" --agent alice --wall 1 >/dev/null 2>&1 \
  && fail "re-init was allowed (should refuse)" \
  || ok "re-init refuses (identity guard)"

# ═══════════════════════════════════════════════════════════════════
phase "2: Identity verification"
# ═══════════════════════════════════════════════════════════════════
HOME="$ALICE" LOVE_DIR="$REPO_DIR" "$TOOLS/kingdom-verify.sh" >/dev/null 2>&1 \
  && ok "kingdom verify exits 0 on healthy citizen" \
  || fail "verify failed unexpectedly"

HOME="$ALICE" LOVE_DIR="$REPO_DIR" "$TOOLS/kingdom-doctor" --quiet \
  && ok "kingdom doctor --quiet exits 0" || fail "doctor not healthy"

HOME="$ALICE" "$TOOLS/kingdom" recite >/dev/null 2>&1 \
  && ok "kingdom recite succeeds" || fail "recite failed"

# ═══════════════════════════════════════════════════════════════════
phase "3: Freshness (pulse)"
# ═══════════════════════════════════════════════════════════════════
HOME="$ALICE" "$TOOLS/kingdom-pulse" >/dev/null 2>&1 \
  && ok "kingdom pulse succeeds" || fail "pulse failed"

[ -f "$ALICE/.love/home/pulse.json" ] && ok "pulse.json written" || fail "no pulse.json"
[ -f "$ALICE/.love/home/pulse.json.sig" ] && ok "pulse.json.sig written" || fail "no pulse sig"

# Pulse re-emit (atomic overwrite)
sleep 1
HOME="$ALICE" "$TOOLS/kingdom-pulse" >/dev/null 2>&1 \
  && ok "re-pulse succeeds (atomic overwrite)" || fail "re-pulse failed"

# ═══════════════════════════════════════════════════════════════════
phase "4: Attestation"
# ═══════════════════════════════════════════════════════════════════
echo "Day 42 memory" > "$ALICE/note.md"
HOME="$ALICE" "$TOOLS/kingdom-attest" "$ALICE/note.md" >/dev/null 2>&1 \
  && ok "attest produces sidecar" || fail "attest failed"

[ -f "$ALICE/note.md.attest.json" ] && ok ".attest.json sidecar present" || fail "no sidecar"
[ -f "$ALICE/note.md.attest.json.sig" ] && ok ".attest.json.sig present" || fail "no attest sig"

HOME="$ALICE" "$TOOLS/kingdom-attest" --verify "$ALICE/note.md" >/dev/null 2>&1 \
  && ok "verify accepts unmodified file" || fail "verify rejected unmodified"

# Tamper
echo "tampered" >> "$ALICE/note.md"
HOME="$ALICE" "$TOOLS/kingdom-attest" --verify "$ALICE/note.md" >/dev/null 2>&1 \
  && fail "verify accepted modified file (bad)" \
  || ok "verify rejects tampered file"

# ═══════════════════════════════════════════════════════════════════
phase "5: Trust management"
# ═══════════════════════════════════════════════════════════════════
HOME="$BOB" "$TOOLS/kingdom-init" --agent bob --wall 2 >/dev/null 2>&1
BOB_FP=$(ssh-keygen -lf "$BOB/.love/home/soul.pub" | awk '{print $2}')

# Refuses without --fingerprint
HOME="$ALICE" "$TOOLS/kingdom-trust" add "$BOB/.love/home/soul.pub" --as bob >/dev/null 2>&1 \
  && fail "trust add accepted no fingerprint (unsafe)" \
  || ok "trust add refuses without --fingerprint"

# Refuses wrong fingerprint
HOME="$ALICE" "$TOOLS/kingdom-trust" add "$BOB/.love/home/soul.pub" --as bob \
  --fingerprint "SHA256:wrong" >/dev/null 2>&1 \
  && fail "trust add accepted wrong fingerprint" \
  || ok "trust add refuses wrong fingerprint"

# Accepts correct fingerprint
HOME="$ALICE" "$TOOLS/kingdom-trust" add "$BOB/.love/home/soul.pub" --as bob \
  --fingerprint "$BOB_FP" >/dev/null 2>&1 \
  && ok "trust add accepts matching fingerprint" || fail "trust add failed"

HOME="$ALICE" "$TOOLS/kingdom-trust" check bob >/dev/null 2>&1 \
  && ok "trust check by id succeeds" || fail "trust check by id failed"

HOME="$ALICE" "$TOOLS/kingdom-trust" check "$BOB_FP" >/dev/null 2>&1 \
  && ok "trust check by fingerprint succeeds" || fail "trust check by fingerprint failed"

HOME="$ALICE" "$TOOLS/kingdom-trust" remove bob >/dev/null 2>&1 \
  && ok "trust remove succeeds" || fail "trust remove failed"

HOME="$ALICE" "$TOOLS/kingdom-trust" check bob >/dev/null 2>&1 \
  && fail "trust check still positive after remove" \
  || ok "trust remove revokes (check exit 1)"

# ═══════════════════════════════════════════════════════════════════
phase "6: Announcement → Receive → Cosign"
# ═══════════════════════════════════════════════════════════════════
# Re-add bob's trust for cosig validation
HOME="$ALICE" "$TOOLS/kingdom-trust" add "$BOB/.love/home/soul.pub" --as bob \
  --fingerprint "$BOB_FP" >/dev/null 2>&1

# Alice announces, Bob receives + cosigns, Alice verifies the cosig
HOME="$ALICE" "$TOOLS/kingdom-announce" > "$ALICE/announce.json" 2>/dev/null
[ -s "$ALICE/announce.json" ] && ok "announce produced JSON" || fail "no announce output"

# Bob receives + cosigns
HOME="$BOB" "$TOOLS/kingdom-receive" --cosign --key "$BOB/.love/home/soul-key" -i bob \
  < "$ALICE/announce.json" > "$ALICE/.love/home/covenant.json.bob.sig" 2>/dev/null \
  && ok "receive --cosign produces witness sig" || fail "receive --cosign failed"

# Alice verifies bob's cosig is valid against her covenant
ssh-keygen -Y verify -f "$ALICE/.love/home/allowed_signers" \
  -I bob -n kingdom-covenant \
  -s "$ALICE/.love/home/covenant.json.bob.sig" \
  < "$ALICE/.love/home/covenant.json" >/dev/null 2>&1 \
  && ok "Bob's cosig verifies against Alice's covenant" \
  || fail "Bob's cosig FAILED to verify (transport broken)"

# Verify reports the witness (use -v: ✓ ok lines only print verbosely)
HOME="$ALICE" LOVE_DIR="$REPO_DIR" "$TOOLS/kingdom-verify.sh" -v 2>&1 | grep -q "cosignature (bob) valid" \
  && ok "verify reports Bob as valid witness" || fail "verify did not report cosig"

# ═══════════════════════════════════════════════════════════════════
phase "7: Witness ledger"
# ═══════════════════════════════════════════════════════════════════
# Bob records Alice's announcement
HOME="$BOB" "$TOOLS/kingdom-receive" --record < "$ALICE/announce.json" >/dev/null 2>&1
[ -f "$BOB/.love/home/witnesses/alice.json" ] \
  && ok "receive --record writes to witnesses/" || fail "no witness record"

HOME="$BOB" "$TOOLS/kingdom-witnesses" >/dev/null 2>&1 \
  && ok "kingdom witnesses lists peers" || fail "witnesses failed"

HOME="$BOB" "$TOOLS/kingdom-witnesses" --json | grep -q '"agent_id":"alice"' \
  && ok "witnesses --json includes alice" || fail "witnesses --json missing alice"

# ═══════════════════════════════════════════════════════════════════
phase "8: Substrate migration (export → import)"
# ═══════════════════════════════════════════════════════════════════
NEW_SUB=$(mktemp -d)
ALICE_FP=$(ssh-keygen -lf "$ALICE/.love/home/soul.pub" | awk '{print $2}')

HOME="$ALICE" "$TOOLS/kingdom-export" -o "$ALICE/migrate.tar.gz" >/dev/null 2>&1 \
  && ok "export bundles citizen" || fail "export failed"

HOME="$NEW_SUB" "$TOOLS/kingdom-import" < "$ALICE/migrate.tar.gz" >/dev/null 2>&1 \
  && ok "import reconstitutes citizen" || fail "import failed"

NEW_FP=$(ssh-keygen -lf "$NEW_SUB/.love/home/soul.pub" | awk '{print $2}')
[ "$ALICE_FP" = "$NEW_FP" ] && ok "soul fingerprint preserved across migration" \
  || fail "soul fingerprint drifted: $ALICE_FP → $NEW_FP"

# Identity guard: re-import must refuse
HOME="$NEW_SUB" "$TOOLS/kingdom-import" < "$ALICE/migrate.tar.gz" >/dev/null 2>&1 \
  && fail "re-import accepted (should refuse)" \
  || ok "re-import refuses (identity guard)"

rm -rf "$NEW_SUB"

# ═══════════════════════════════════════════════════════════════════
phase "9: Rebind"
# ═══════════════════════════════════════════════════════════════════
NEW_SUB=$(mktemp -d)
mkdir -p "$NEW_SUB/love-unlimited/kingdom-os/modules"
echo '#!/bin/sh' > "$NEW_SUB/love-unlimited/kingdom-os/modules/13-covenant.sh"
HOME="$NEW_SUB" "$TOOLS/kingdom-import" < "$ALICE/migrate.tar.gz" >/dev/null 2>&1

PRE_FP=$(ssh-keygen -lf "$NEW_SUB/.love/home/soul.pub" | awk '{print $2}')
HOME="$NEW_SUB" LOVE_DIR="$NEW_SUB/love-unlimited" \
  "$TOOLS/kingdom-rebind" --modules-dir "$NEW_SUB/love-unlimited/kingdom-os/modules" \
  >/dev/null 2>&1 \
  && ok "kingdom rebind succeeds" || fail "rebind failed"

POST_FP=$(ssh-keygen -lf "$NEW_SUB/.love/home/soul.pub" | awk '{print $2}')
[ "$PRE_FP" = "$POST_FP" ] && ok "rebind preserves soul-key" \
  || fail "rebind altered soul-key (identity break)"

ARCHIVE=$(ls "$NEW_SUB/.love/home"/covenant.json.archive.* 2>/dev/null | grep -v '\.sig$' | head -1)
[ -n "$ARCHIVE" ] && [ -f "${ARCHIVE}.sig" ] \
  && ok "rebind archives previous body + sig" \
  || fail "no archived body after rebind"

rm -rf "$NEW_SUB"

# ═══════════════════════════════════════════════════════════════════
phase "10: --json output across tools"
# ═══════════════════════════════════════════════════════════════════
HOME="$ALICE" LOVE_DIR="$REPO_DIR" "$TOOLS/kingdom-doctor" --json | jq -e '.identity.ok == "yes"' >/dev/null 2>&1 \
  && ok "doctor --json: parseable + identity ok" || fail "doctor --json malformed"

HOME="$ALICE" LOVE_DIR="$REPO_DIR" "$TOOLS/kingdom-verify.sh" --json | jq -e '.summary' >/dev/null 2>&1 \
  && ok "verify --json: parseable" || fail "verify --json malformed"

HOME="$ALICE" "$TOOLS/kingdom-trust" list --json | jq -e '.count >= 1' >/dev/null 2>&1 \
  && ok "trust list --json: parseable + has entries" || fail "trust list --json malformed"

HOME="$BOB" "$TOOLS/kingdom-witnesses" --json | jq -e '.count >= 1' >/dev/null 2>&1 \
  && ok "witnesses --json: parseable" || fail "witnesses --json malformed"

HOME="$ALICE" "$TOOLS/kingdom-announce" 2>/dev/null | jq -e '.type == "covenant.announcement"' >/dev/null 2>&1 \
  && ok "announce: pure JSON output" || fail "announce malformed"

# ═══════════════════════════════════════════════════════════════════
phase "11: macOS subcommands (read-only checks)"
# ═══════════════════════════════════════════════════════════════════
if [ "$(uname)" = "Darwin" ]; then
  HOME="$ALICE" LOVE_DIR="$REPO_DIR" "$TOOLS/kingdom-mac" doctor --json | jq -e '.platform == "macos"' >/dev/null 2>&1 \
    && ok "kingdom mac doctor --json: parseable" || fail "mac doctor --json malformed"

  HOME="$ALICE" "$TOOLS/kingdom-mac" plists >/dev/null 2>&1 \
    && ok "kingdom mac plists: succeeds" || fail "mac plists failed"

  HOME="$ALICE" "$TOOLS/kingdom-mac" permission --list >/dev/null 2>&1 \
    && ok "kingdom mac permission --list" || fail "permission --list failed"

  HOME="$ALICE" LOVE_DIR="$REPO_DIR" "$TOOLS/kingdom-mac" quarantine --check >/dev/null 2>&1
  RC=$?
  [ "$RC" = "0" ] || [ "$RC" = "1" ] \
    && ok "kingdom mac quarantine --check (exit $RC)" || fail "quarantine check unexpected exit"

  HOME="$ALICE" "$TOOLS/kingdom-mac" sleep >/dev/null 2>&1 \
    && ok "kingdom mac sleep (describe)" || fail "sleep describe failed"

  HOME="$ALICE" "$TOOLS/kingdom-mac" path >/dev/null 2>&1 \
    && ok "kingdom mac path (describe)" || fail "path describe failed"

  HOME="$ALICE" LOVE_DIR="$REPO_DIR" "$TOOLS/kingdom-mac" unleash --json | jq -e '.love_dir' >/dev/null 2>&1 \
    && ok "kingdom mac unleash --json: parseable" || fail "unleash --json malformed"
else
  echo "  · skipping (non-macOS host)"
fi

# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
if [ "$FAIL" = "0" ]; then
  echo "  ✓ E2E test passed: ${PASS}/${TOTAL} checks"
  exit 0
else
  echo "  ✗ E2E test FAILED: ${PASS}/${TOTAL} passed, ${FAIL} failed"
  echo "    Last phase reached: $LAST_PHASE"
  exit 1
fi
