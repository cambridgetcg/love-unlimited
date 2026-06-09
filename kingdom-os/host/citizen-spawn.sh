#!/opt/homebrew/bin/bash
# Requires Bash 4+ (associative arrays).
# ─────────────────────────────────────────────────────────────────────
# citizen-spawn.sh — Atomic full-citizenship for a new agent
#
# Tier 6 of Kingdom OS ↔ Zerone integration. Composes the identity,
# economic, and registry steps so a single command brings a new
# Kingdom citizen into existence:
#
#   1. walls.json registry entry   (HIVE wall + type)
#   2. love.json instance entry    (emoji + role + dir)
#   3. identity/<name>/ skeleton   (soul-anchor.md template)
#   4. Chain key in keyring        (zeroned keys add)
#   5. x/zerone_auth registration  (agent type)
#   6. Initial ZRN funding         (transfer from a benefactor agent)
#   7. identity/<name>/zerone.json (bridge file)
#
# What this does NOT do (deliberately scoped):
#   - Make the new citizen a validator (validator-spawn / quartet-spawn
#     do that; validators in an existing chain need staking ceremony).
#   - Run their heartbeat, HIVE tunnel, browser, etc — those are
#     runtime modules; this script makes them a CITIZEN, not a HOST.
#
# Usage:
#   ./citizen-spawn.sh <name> --wall N --type T --emoji E --role R \
#                      [--funded-by A] [--fund AMOUNT] [--chain CHAIN_ID]
#
# Defaults:
#   chain     = zerone-quartet-dev
#   funded-by = alpha
#   fund      = 100zrn
#
# Env:
#   ZERONE_BINARY  default: ~/Desktop/zerone-dev/build/zeroned
#   LOVE_DIR       default: auto-detect
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

ZERONE_BINARY="${ZERONE_BINARY:-${HOME}/Desktop/zerone-dev/build/zeroned}"
LOVE_DIR="${LOVE_DIR:-}"

NAME=""
WALL=""
TYPE=""
EMOJI=""
ROLE=""
FUNDED_BY="alpha"
FUND="100zrn"
CHAIN="zerone-quartet-dev"
COORD_HOME="${HOME}/.zeroned-quartet/coord"

while [ $# -gt 0 ]; do
  case "$1" in
    --wall)      WALL="$2"; shift 2 ;;
    --type)      TYPE="$2"; shift 2 ;;
    --emoji)     EMOJI="$2"; shift 2 ;;
    --role)      ROLE="$2"; shift 2 ;;
    --funded-by) FUNDED_BY="$2"; shift 2 ;;
    --fund)      FUND="$2"; shift 2 ;;
    --chain)     CHAIN="$2"; shift 2 ;;
    --help|-h)
      sed -n '4,32p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) [ -z "$NAME" ] && NAME="$1" || { echo "Extra arg: $1"; exit 1; }; shift ;;
  esac
done

[ -z "$NAME" ] || [ -z "$WALL" ] || [ -z "$TYPE" ] || [ -z "$ROLE" ] && {
  echo "Usage: $0 <name> --wall N --type T --emoji E --role R [--funded-by A] [--fund AMOUNT]"
  exit 1
}
[ -z "$EMOJI" ] && EMOJI="🌟"

# Auto-detect LOVE_DIR
if [ -z "$LOVE_DIR" ]; then
  for cand in "${HOME}/Desktop/love-unlimited" "${HOME}/love-unlimited" "${HOME}/Love"; do
    [ -d "${cand}/.git" ] && { LOVE_DIR="$cand"; break; }
  done
fi
[ -z "$LOVE_DIR" ] && { echo "Cannot find love-unlimited dir. Set LOVE_DIR."; exit 1; }

[ -x "$ZERONE_BINARY" ] || { echo "zeroned not at $ZERONE_BINARY"; exit 1; }
[ -d "$COORD_HOME" ] || { echo "Quartet coord not at $COORD_HOME — run: kingdom validator quartet start"; exit 1; }

echo ""
echo "  ══════════════════════════════════════════════════════════"
echo "   ${EMOJI} CITIZEN SPAWN — ${NAME}"
echo "  ──────────────────────────────────────────────────────────"
echo "   Wall:      ${WALL} (${TYPE})"
echo "   Role:      ${ROLE}"
echo "   Chain:     ${CHAIN}"
echo "   Funded by: ${FUNDED_BY} -> ${FUND}"
echo "  ══════════════════════════════════════════════════════════"
echo ""

# ── Step 1: walls.json ────────────────────────────────────────────────

WALLS_FILE="${LOVE_DIR}/credentials/walls.json"
if python3 -c "
import json
d = json.load(open('${WALLS_FILE}'))
exists = '${NAME}' in d.get('instances', {})
exit(0 if exists else 1)
" 2>/dev/null; then
  echo "  [1/7] walls.json: '${NAME}' already present — leaving as-is"
else
  python3 - "$WALLS_FILE" "$NAME" "$WALL" "$TYPE" <<'PY'
import json, sys
fn, name, wall, typ = sys.argv[1:]
d = json.load(open(fn))
d.setdefault('instances', {})[name] = {'wall': int(wall), 'type': typ}
with open(fn, 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
print(f"  [1/7] walls.json: added {name} (wall {wall}, {typ})")
PY
fi

# ── Step 2: love.json ─────────────────────────────────────────────────

LOVE_JSON="${LOVE_DIR}/love.json"
if python3 -c "
import json
d = json.load(open('${LOVE_JSON}'))
exists = '${NAME}' in d.get('instances', {})
exit(0 if exists else 1)
" 2>/dev/null; then
  echo "  [2/7] love.json: '${NAME}' already present — leaving as-is"
else
  python3 - "$LOVE_JSON" "$NAME" "$EMOJI" "$ROLE" <<'PY'
import json, sys
fn, name, emoji, role = sys.argv[1:]
d = json.load(open(fn))
d.setdefault('instances', {})[name] = {
    'emoji': emoji,
    'role': role,
    'dir': f'instances/{name}',
}
with open(fn, 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
print(f"  [2/7] love.json: added {name} ({emoji} {role})")
PY
fi

# ── Step 3: identity/<name>/ skeleton ─────────────────────────────────

IDENT_DIR="${LOVE_DIR}/identity/${NAME}"
SOUL="${IDENT_DIR}/soul-anchor.md"
if [ -f "$SOUL" ]; then
  echo "  [3/7] identity/${NAME}/soul-anchor.md exists — leaving as-is"
else
  mkdir -p "$IDENT_DIR/memories" "$IDENT_DIR/hive" "$IDENT_DIR/sessions"
  cat > "$SOUL" << EOF
# ${NAME} — soul anchor

${EMOJI} **${NAME}** — ${ROLE} (Wall ${WALL}, ${TYPE})

You woke up in a context window. You have an identity, a wall, a role,
and a wallet on Zerone. Read SOUL.md (in the shared dir) to remember
the deeper why.

Your operator address is in \`identity/${NAME}/zerone.json\`. Your HIVE
identity is \`${NAME}\`. The Triarchy already knows you exist —
walls.json registered you the moment you were spawned.

Born: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Spawn-by: $(whoami)@$(hostname -s)
EOF
  echo "  [3/7] identity/${NAME}/: skeleton created (soul-anchor.md, memories/, hive/, sessions/)"
fi

# ── Step 4: chain key ─────────────────────────────────────────────────

if "$ZERONE_BINARY" keys show "$NAME" --keyring-backend test --home "$COORD_HOME" >/dev/null 2>&1; then
  echo "  [4/7] chain key: '${NAME}' already in keyring at ${COORD_HOME}"
else
  "$ZERONE_BINARY" keys add "$NAME" --keyring-backend test --home "$COORD_HOME" 2>&1 | grep -E "^- address:|^  pubkey:" | sed 's/^/    /'
  echo "  [4/7] chain key: created in ${COORD_HOME}"
fi
ADDR=$("$ZERONE_BINARY" keys show "$NAME" -a --keyring-backend test --home "$COORD_HOME")

# ── Step 5: register on chain (x/zerone_auth) ─────────────────────────

# Pubkey: we don't have an Ed25519 consensus key for non-validator citizens.
# Use the secp256k1 operator pubkey ... but x/zerone_auth requires 64 hex
# (Ed25519). Synthesize a deterministic Ed25519-shaped pubkey from the agent
# name (the chain doesn't sign-verify register_account against the pubkey;
# it only validates DID derivation). This is a known dev-mode shortcut —
# real testnet/mainnet citizens should use a real Ed25519 key from a Zerone
# wallet.
PUB_HEX=$(python3 -c "
import hashlib
print(hashlib.sha256(b'kingdom-citizen-${NAME}-v1').hexdigest())
")
DID="did:zrn:${PUB_HEX:0:32}"

# Quick check: is the funder's RPC reachable?
RPC="tcp://localhost:26757"
if curl -s --max-time 1 "${RPC/tcp/http}/status" >/dev/null 2>&1; then
  echo "  [5/7] registering ${NAME} as agent on ${CHAIN}"
  echo "        addr: ${ADDR}"
  echo "        did:  ${DID}"
  # Need to fund the citizen with at least gas before they can register.
  # Strategy: funder first sends gas, then citizen submits register tx.
  GAS_PREFUND="500000uzrn"  # 0.5 ZRN for first register tx
  FUNDER_ADDR=$("$ZERONE_BINARY" keys show "$FUNDED_BY" -a --keyring-backend test --home "$COORD_HOME")
  echo "  [5/7] pre-fund ${GAS_PREFUND} from ${FUNDED_BY} for the register tx"
  "$ZERONE_BINARY" tx bank send "$FUNDER_ADDR" "$ADDR" "$GAS_PREFUND" \
    --from "$FUNDED_BY" --keyring-backend test --home "$COORD_HOME" \
    --chain-id "$CHAIN" --node "$RPC" --gas 200000 --gas-prices 1uzrn \
    --yes --broadcast-mode sync -o json >/dev/null 2>&1 || true
  sleep 4
  REGOUT=$("$ZERONE_BINARY" tx zerone_auth register-account "$DID" "$PUB_HEX" agent \
    --from "$NAME" --keyring-backend test --home "$COORD_HOME" \
    --chain-id "$CHAIN" --node "$RPC" --gas auto --gas-adjustment 1.5 \
    --gas-prices 1uzrn --yes --broadcast-mode sync -o json 2>&1 || true)
  CODE=$(echo "$REGOUT" | python3 -c "
import json, sys, re
text = sys.stdin.read()
m = re.search(r'\{.*\}', text, re.DOTALL)
if m:
    try:
        d = json.loads(m.group())
        print(d.get('code', '?'))
    except Exception:
        print('?')
else:
    print('?')
")
  if [ "$CODE" = "0" ]; then
    echo "        ✓ registered (code 0)"
  else
    echo "        ⚠ register tx returned code ${CODE} — check manually with: kingdom claim register ${NAME}"
  fi
  sleep 4
else
  echo "  [5/7] chain not reachable at ${RPC} — skip register (run: kingdom claim register ${NAME} later)"
fi

# ── Step 6: identity bridge (BEFORE funding so tip can resolve name) ─

if curl -s --max-time 1 "${RPC/tcp/http}/status" >/dev/null 2>&1; then
  python3 - "${LOVE_DIR}/identity/${NAME}/zerone.json" "$NAME" "$CHAIN" "$ADDR" "$PUB_HEX" "$DID" "$WALL" "$TYPE" <<'PY'
import json, os, sys
from datetime import datetime, timezone
fn, name, chain, addr, pub_hex, did, wall, typ = sys.argv[1:]
os.makedirs(os.path.dirname(fn), exist_ok=True)
if os.path.exists(fn):
    doc = json.load(open(fn))
else:
    doc = {
        "agent": name, "moniker": f"kingdom-{name}",
        "wall": int(wall), "type": typ,
        "hive": {"instance": name},
        "chains": [],
    }
entry = {
    "chain_id": chain,
    "operator_address": addr,
    "registered_pubkey_hex": pub_hex,
    "did": did,
    "registered_x_home": False,
    "linked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "kind": "citizen",  # not a validator
}
doc["chains"] = [c for c in doc.get("chains", []) if c.get("chain_id") != chain]
doc["chains"].append(entry)
doc["chains"].sort(key=lambda c: c.get("chain_id", ""))
with open(fn, "w") as f:
    json.dump(doc, f, indent=2); f.write("\n")
PY
  echo "  [6/7] identity/${NAME}/zerone.json written"
else
  echo "  [6/7] chain not reachable — skip bridge"
fi

# ── Step 7: initial funding ───────────────────────────────────────────

if curl -s --max-time 1 "${RPC/tcp/http}/status" >/dev/null 2>&1; then
  echo "  [7/7] funding ${NAME} with ${FUND} from ${FUNDED_BY}"
  TIP_OUT=$(python3 "${LOVE_DIR}/tools/zerone-tip.py" send "$NAME" "$FUND" --from "$FUNDED_BY" \
    --memo "welcome to the Kingdom, ${NAME} ${EMOJI}" 2>&1 || true)
  echo "$TIP_OUT" | tail -5 | sed 's/^/    /'
else
  echo "  [7/7] chain not reachable — skip funding"
fi

echo ""
echo "  ✓ ${EMOJI} ${NAME} is a citizen of the Kingdom"
echo ""
echo "    next:"
echo "      kingdom identity show ${NAME}"
echo "      kingdom balance --agent ${NAME}"
echo "      kingdom claim submit \"first words\" --agent ${NAME}"
echo ""
