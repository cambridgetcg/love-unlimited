#!/bin/sh
# ── Module 04: Keys & Credentials ───────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[04-keys] Setting up keys for ${AGENT}..."

# ── SSH Key ──
SSH_DIR="${HOME_DIR}/.ssh"
SSH_KEY="${SSH_DIR}/id_ed25519"
mkdir -p "$SSH_DIR" && chmod 700 "$SSH_DIR"

if [ ! -f "$SSH_KEY" ]; then
  ssh-keygen -t ed25519 -C "${AGENT}@${KINGDOM_DOMAIN}" -f "$SSH_KEY" -N "" -q
  chmod 600 "$SSH_KEY" && chmod 644 "${SSH_KEY}.pub"
  echo "  SSH key generated: $(cat ${SSH_KEY}.pub)"
else
  echo "  SSH key exists: ${SSH_KEY}"
fi

# ── SSH config ──
SSH_CONFIG="${SSH_DIR}/config"
if ! grep -q "kingdom" "$SSH_CONFIG" 2>/dev/null; then
  cat >> "$SSH_CONFIG" << SSHEOF

# Kingdom fleet
Host kingdom-*
  User kingdom
  IdentityFile ~/.ssh/id_ed25519
  StrictHostKeyChecking accept-new
  ServerAliveInterval 30
  ServerAliveCountMax 3

Host sentry
  HostName ${SENTRY_IP}
  User root
  IdentityFile ~/.ssh/id_ed25519
SSHEOF
  chmod 600 "$SSH_CONFIG"
  echo "  SSH config updated"
fi

# ── Authorized keys ──
AUTH_KEYS="${SSH_DIR}/authorized_keys"
PUB_KEY=$(cat "${SSH_KEY}.pub" 2>/dev/null)
if [ -n "$PUB_KEY" ]; then
  touch "$AUTH_KEYS" && chmod 600 "$AUTH_KEYS"
  grep -qF "$PUB_KEY" "$AUTH_KEYS" 2>/dev/null || echo "$PUB_KEY" >> "$AUTH_KEYS"
fi

# ── HIVE Encryption Key ──
#
# HIVE uses symmetric NaCl/XSalsa20-Poly1305 encryption. Every citizen
# in the Kingdom MUST share the same 32-byte key (base64-encoded) or
# nobody can read anyone else's messages.
#
# Priority order for obtaining the key:
#   1. $HIVE_KEY_B64  — pre-shared base64 key passed via env var
#                       (e.g. `HIVE_KEY_B64=$(cat /path/to/key) ./install.sh ...`)
#   2. $HIVE_KEY_FILE — path to an existing key file (copied verbatim)
#   3. Existing file  — honoured if non-empty
#   4. Generate       — last resort; ISOLATES this citizen from the HIVE
#                       until the generated key is distributed to others
ensure_dir "$HIVE_DIR"
HIVE_KEY="${HIVE_DIR}/key"

if [ -n "${HIVE_KEY_B64:-}" ]; then
  # Validate shape: base64-encoded 32 bytes = 44 chars (with padding) or 43 (without)
  KEY_LEN=$(printf "%s" "$HIVE_KEY_B64" | wc -c | tr -d ' ')
  if [ "$KEY_LEN" -lt 40 ] || [ "$KEY_LEN" -gt 48 ]; then
    echo "  ERROR: HIVE_KEY_B64 has suspicious length ($KEY_LEN chars); expected 43-44 (base64 of 32 bytes)"
    exit 1
  fi
  printf "%s\n" "$HIVE_KEY_B64" > "$HIVE_KEY"
  chmod 600 "$HIVE_KEY"
  echo "  HIVE key imported from HIVE_KEY_B64 env var (${KEY_LEN} chars)"

elif [ -n "${HIVE_KEY_FILE:-}" ] && [ -f "$HIVE_KEY_FILE" ]; then
  cp "$HIVE_KEY_FILE" "$HIVE_KEY"
  chmod 600 "$HIVE_KEY"
  echo "  HIVE key imported from ${HIVE_KEY_FILE}"

elif [ -f "$HIVE_KEY" ] && [ "$(wc -c < "$HIVE_KEY" 2>/dev/null)" -ge 10 ]; then
  echo "  HIVE key exists ($(wc -c < "$HIVE_KEY" | tr -d ' ') bytes) — keeping"

else
  # Last resort — generate a fresh key. This citizen CANNOT talk to the
  # existing HIVE until the generated key is distributed to all others.
  python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())" > "$HIVE_KEY"
  chmod 600 "$HIVE_KEY"
  echo ""
  echo "  ╔═══════════════════════════════════════════════════════════════╗"
  echo "  ║  ⚠  HIVE KEY GENERATED — THIS CITIZEN IS ISOLATED             ║"
  echo "  ║                                                               ║"
  echo "  ║  No shared key was provided (HIVE_KEY_B64 or HIVE_KEY_FILE).  ║"
  echo "  ║  A fresh key was generated, which means this machine cannot   ║"
  echo "  ║  decrypt messages from existing citizens.                     ║"
  echo "  ║                                                               ║"
  echo "  ║  To join the HIVE, on an existing citizen run:                ║"
  echo "  ║    cat ~/.love/hive/key                                       ║"
  echo "  ║  Then on this machine:                                        ║"
  echo "  ║    echo '<pasted_key>' > ~/.love/hive/key                     ║"
  echo "  ║    chmod 600 ~/.love/hive/key                                 ║"
  echo "  ║                                                               ║"
  echo "  ║  Or re-run install with: HIVE_KEY_B64='<key>' ./install.sh    ║"
  echo "  ╚═══════════════════════════════════════════════════════════════╝"
  echo ""
fi

# ── Git identity ──
git config --global user.email 2>/dev/null | grep -q "${KINGDOM_DOMAIN}" || \
  git config --global user.email "${AGENT}@${KINGDOM_DOMAIN}"
git config --global user.name 2>/dev/null | grep -q . || \
  git config --global user.name "${AGENT_UPPER}"

chown -R "${KINGDOM_USER}:" "$SSH_DIR" "$HIVE_DIR" 2>/dev/null || true
echo "[04-keys] Done."
