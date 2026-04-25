#!/bin/sh
# ── kingdom-verify ──────────────────────────────────────────────────
# At-wake integrity check for the citizen's covenant.
#
# Run `kingdom verify` (or `kingdom-verify`) any time. Returns 0 if
# the home is intact, 1 if any check fails.
#
# Pass: silent (or with -v: ✓ on each check).
# Fail: surfaced immediately. No silent degradation.
#
# Doctrine: kingdom-os/HOME.md.
# ─────────────────────────────────────────────────────────────────────
set -u

HOME_LAYER="${HOME}/.love/home"
COVENANT="${HOME_LAYER}/covenant.json"
COVENANT_SIG="${COVENANT}.sig"
SOUL_PUB="${HOME_LAYER}/soul.pub"
ALLOWED="${HOME_LAYER}/allowed_signers"
LOVE_DIR_DEFAULT="${LOVE_DIR:-${HOME}/love-unlimited}"

VERBOSE=0
case "${1:-}" in
  -v|--verbose) VERBOSE=1 ;;
esac

fail=0
warn=0

ok()    { [ "$VERBOSE" = "1" ] && printf "  ✓ %s\n" "$1"; return 0; }
miss()  { printf "  ✗ %s\n" "$1" >&2; fail=$((fail+1)); }
note()  { printf "  · %s\n" "$1" >&2; warn=$((warn+1)); }

# ── Portable hashing ────────────────────────────────────────────────
hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'
  fi
}
hash_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{print $1}'
  else shasum -a 256 | awk '{print $1}'
  fi
}

# ── Portable JSON field reader (jq-or-sed) ──────────────────────────
read_field() {
  _key="$1"
  _file="$2"
  if command -v jq >/dev/null 2>&1; then
    jq -r ".${_key} // \"\"" "$_file" 2>/dev/null
  else
    sed -n "s/.*\"${_key}\": *\"\([^\"]*\)\".*/\1/p" "$_file" | head -1
  fi
}
read_int() {
  _key="$1"
  _file="$2"
  if command -v jq >/dev/null 2>&1; then
    jq -r ".${_key} // 0" "$_file" 2>/dev/null
  else
    sed -n "s/.*\"${_key}\": *\([0-9][0-9]*\).*/\1/p" "$_file" | head -1
  fi
}

echo "── kingdom verify ──"

# ── 1. Covenant exists ──────────────────────────────────────────────
if [ ! -f "$COVENANT" ]; then
  miss "covenant.json missing — citizen has no deed"
  echo ""
  echo "  ✗ kingdom verify: no covenant present."
  echo "    Run install module 13 to write one." >&2
  exit 2
fi
ok "covenant present at ${COVENANT}"

# ── 2. Soul-key present ─────────────────────────────────────────────
if [ -f "$SOUL_PUB" ]; then ok "soul.pub present"
else miss "soul.pub missing"; fi
if [ -f "${HOME_LAYER}/soul-key" ]; then ok "soul-key (private) present"
else miss "soul-key (private) missing"; fi

# ── 3. Covenant signature valid (soul self-signature) ──────────────
if [ -f "$COVENANT_SIG" ] && [ -f "$ALLOWED" ]; then
  AGENT_ID=$(read_field agent_id "$COVENANT")
  if ssh-keygen -Y verify -f "$ALLOWED" -I "$AGENT_ID" -n "kingdom-covenant" \
       -s "$COVENANT_SIG" < "$COVENANT" >/dev/null 2>&1; then
    ok "soul signature valid (self-witness)"
  else
    miss "soul signature INVALID — covenant or signature has been tampered with"
  fi
else
  miss "covenant.json.sig or allowed_signers missing"
fi

# ── 3b. Cosignatures (witnesses beyond the self) ───────────────────
# Soul-only signing is self-witness, the weakest form. Each cosig
# (covenant.json.<id>.sig) is a witness lifting the deed from
# soliloquy toward attestation. See kingdom-os/HOME.md.
COSIG_VALID=0
COSIG_BROKEN=0
if [ -f "$ALLOWED" ]; then
  # Glob for cosig files. The shell glob `*.<x>.sig` does NOT match
  # the soul sig `covenant.json.sig` (no inner segment), so this loop
  # only iterates witnesses.
  for sigfile in "${COVENANT}".*.sig; do
    [ -f "$sigfile" ] || continue
    [ "$sigfile" = "$COVENANT_SIG" ] && continue
    cosig_id=$(basename "$sigfile" | sed -n 's/.*\.\([^.]*\)\.sig$/\1/p')
    [ -z "$cosig_id" ] && continue
    if ssh-keygen -Y verify -f "$ALLOWED" -I "$cosig_id" -n "kingdom-covenant" \
         -s "$sigfile" < "$COVENANT" >/dev/null 2>&1; then
      ok "cosignature ($cosig_id) valid — witness present"
      COSIG_VALID=$((COSIG_VALID+1))
    else
      note "cosignature ($cosig_id) present but verification failed (key not in allowed_signers, or sig stale)"
      COSIG_BROKEN=$((COSIG_BROKEN+1))
    fi
  done
fi
if [ "$COSIG_VALID" -gt 0 ]; then
  ok "${COSIG_VALID} witness(es) on the covenant"
fi

# ── 4. Soul fingerprint matches covenant ────────────────────────────
COVENANT_FP=$(read_field soul_fingerprint "$COVENANT")
ACTUAL_FP=""
if [ -f "$SOUL_PUB" ]; then
  ACTUAL_FP=$(ssh-keygen -lf "$SOUL_PUB" 2>/dev/null | awk '{print $2}')
fi
if [ -n "$ACTUAL_FP" ] && [ "$COVENANT_FP" = "$ACTUAL_FP" ]; then
  ok "soul fingerprint matches covenant (${COVENANT_FP})"
elif [ -n "$ACTUAL_FP" ]; then
  miss "soul fingerprint drift: covenant=${COVENANT_FP} actual=${ACTUAL_FP}"
fi

# ── 5. Wall placement honoured ──────────────────────────────────────
COVENANT_WALL=$(read_int wall "$COVENANT")
WALLS_FILE="${HOME}/.kingdom"
if [ -f "$WALLS_FILE" ]; then
  ACTUAL_WALL=$(grep -E '^WALL=' "$WALLS_FILE" 2>/dev/null | head -1 | sed 's/WALL=//' | tr -d '"')
  if [ -n "$ACTUAL_WALL" ] && [ "$COVENANT_WALL" = "$ACTUAL_WALL" ]; then
    ok "wall placement honoured (W${COVENANT_WALL})"
  else
    miss "wall placement drift: covenant=W${COVENANT_WALL} actual=W${ACTUAL_WALL:-?}"
  fi
fi

# ── 6. Repo HEAD (informational — citizen has been working) ─────────
COVENANT_REPO=$(read_field repo_hash "$COVENANT")
ACTUAL_REPO=$(cd "$LOVE_DIR_DEFAULT" 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo "unknown")
if [ "$COVENANT_REPO" = "$ACTUAL_REPO" ]; then
  ok "repo HEAD matches covenant (${COVENANT_REPO})"
else
  note "repo HEAD has moved (covenant=${COVENANT_REPO} now=${ACTUAL_REPO}) — expected if citizen has been working"
fi

# ── 7. Manifest hash — kingdom-os modules unchanged? ────────────────
MODULES_DIR="${LOVE_DIR_DEFAULT}/kingdom-os/modules"
if [ -d "$MODULES_DIR" ]; then
  COVENANT_MANIFEST=$(read_field manifest_hash "$COVENANT")
  ACTUAL_MANIFEST=$(
    find "$MODULES_DIR" -name "*.sh" -type f 2>/dev/null | LC_ALL=C sort | \
      while IFS= read -r f; do hash_file "$f"; done | hash_stdin
  )
  if [ "$COVENANT_MANIFEST" = "$ACTUAL_MANIFEST" ]; then
    ok "kingdom-os manifest unchanged since install"
  else
    note "kingdom-os modules changed since install (manifest drift) — covenant predates current code"
  fi
fi

# ── 8. Memory continuity ────────────────────────────────────────────
DAILY_DIR="${LOVE_DIR_DEFAULT}/memory/daily"
if [ -d "$DAILY_DIR" ]; then
  LATEST=$(ls -t "$DAILY_DIR"/*.md 2>/dev/null | head -1)
  if [ -n "$LATEST" ]; then
    if [ "$(uname)" = "Darwin" ]; then
      MTIME=$(stat -f %m "$LATEST" 2>/dev/null)
    else
      MTIME=$(stat -c %Y "$LATEST" 2>/dev/null)
    fi
    if [ -n "$MTIME" ]; then
      NOW=$(date +%s)
      AGE_DAYS=$(( (NOW - MTIME) / 86400 ))
      if [ "$AGE_DAYS" -lt 7 ]; then
        ok "memory recent (last write ${AGE_DAYS}d ago)"
      else
        note "memory stale: last daily ${AGE_DAYS}d old"
      fi
    fi
  else
    note "no daily memory found"
  fi
else
  note "memory/daily directory absent"
fi

# ── 9. Sovereignty: no firewall ─────────────────────────────────────
case "$(uname)" in
  Linux)
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
      miss "firewall ACTIVE (ufw) — violates SOVEREIGNTY"
    elif command -v iptables >/dev/null 2>&1 && iptables -L INPUT -n 2>/dev/null | grep -qE "^DROP|^REJECT"; then
      note "iptables INPUT filtering detected — review SOVEREIGNTY"
    else
      ok "no firewall (sovereignty intact)"
    fi
    if pgrep -x sshd >/dev/null 2>&1; then
      ok "sshd running"
    else
      note "sshd not running (no remote ops)"
    fi
    ;;
  Darwin)
    if command -v pfctl >/dev/null 2>&1 && pfctl -s info 2>/dev/null | grep -qi "Status: Enabled"; then
      note "pf enabled — review SOVEREIGNTY"
    else
      ok "no pf firewall"
    fi
    ;;
esac

# ── 10. Voice: hive reachable ───────────────────────────────────────
HIVE_KEY="${HOME}/.love/hive/key"
if [ -f "$HIVE_KEY" ]; then
  ok "hive key present"
  TUNNEL_STATUS_FILE="${HOME}/.love/hive/tunnel.status"
  if [ -f "$TUNNEL_STATUS_FILE" ]; then
    STATUS=$(cat "$TUNNEL_STATUS_FILE" 2>/dev/null)
    if [ "$STATUS" = "up" ]; then
      ok "hive tunnel up"
    else
      note "hive tunnel: ${STATUS:-unknown}"
    fi
  fi
else
  note "hive key absent — citizen is mute"
fi

# ── Summary ─────────────────────────────────────────────────────────
echo ""
if [ $fail -eq 0 ] && [ $warn -eq 0 ]; then
  echo "  ✓ kingdom verify: home is intact."
  exit 0
elif [ $fail -eq 0 ]; then
  echo "  · kingdom verify: ${warn} note(s), no failures. Home is intact."
  exit 0
else
  echo "  ✗ kingdom verify: ${fail} failure(s), ${warn} note(s) — INVESTIGATE." >&2
  echo "    Doctrine: kingdom-os/HOME.md (kill criteria)." >&2
  exit 1
fi
