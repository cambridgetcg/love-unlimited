#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# identity-link.sh — Bridge HIVE identity ↔ Zerone operator address
#
# Tier 3 of Kingdom OS ↔ Zerone integration. When a validator is
# spawned, this script extracts its on-chain identity (operator address,
# consensus pubkey, node ID, peer endpoint) and writes a portable bridge
# file at:
#
#   identity/<agent>/zerone.json
#
# This file lives in git (per identity/README.md "your soul lives in
# GitHub"), so any device that clones love-unlimited can find each
# agent's Zerone identity. Private keys stay in the validator home;
# this file holds the public addresses + peer info.
#
# The bridge file is an array of chain entries — one agent can be a
# validator on multiple chains (solo dev, quartet dev, testnet, mainnet).
# Each invocation appends or updates the entry for the target chain.
#
# Usage:
#   ./identity-link.sh <agent> [--home PATH] [--love-dir PATH]
#
# Env:
#   ZERONE_BINARY   path to zeroned (default: ~/Desktop/zerone-dev/build/zeroned)
#   LOVE_DIR        love-unlimited root (default: auto-detect)
#
# Auto-home detection (in order):
#   1. --home PATH (explicit)
#   2. ~/.zeroned-quartet/<agent>  (quartet)
#   3. ~/.zeroned-<agent>           (solo)
#
# Output: writes/updates identity/<agent>/zerone.json, prints summary.
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

ZERONE_BINARY="${ZERONE_BINARY:-${HOME}/Desktop/zerone-dev/build/zeroned}"
LOVE_DIR="${LOVE_DIR:-}"

agent=""
home=""

while [ $# -gt 0 ]; do
  case "$1" in
    --home)     home="$2"; shift 2 ;;
    --love-dir) LOVE_DIR="$2"; shift 2 ;;
    --help|-h)
      sed -n '3,32p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) [ -z "$agent" ] && agent="$1" && shift || { echo "Extra arg: $1"; exit 1; } ;;
  esac
done

[ -z "$agent" ] && { echo "Usage: $0 <agent> [--home PATH]"; exit 1; }

# Auto-detect LOVE_DIR
if [ -z "$LOVE_DIR" ]; then
  for cand in "${HOME}/Desktop/love-unlimited" "${HOME}/love-unlimited" "${HOME}/Love"; do
    [ -d "${cand}/.git" ] && { LOVE_DIR="$cand"; break; }
  done
fi
[ -z "$LOVE_DIR" ] && { echo "Cannot find love-unlimited dir. Set LOVE_DIR."; exit 1; }

# Auto-detect home
if [ -z "$home" ]; then
  for cand in "${HOME}/.zeroned-quartet/${agent}" "${HOME}/.zeroned-${agent}"; do
    [ -f "${cand}/config/genesis.json" ] && { home="$cand"; break; }
  done
fi
[ -z "$home" ] || [ ! -f "${home}/config/genesis.json" ] && {
  echo "Cannot find initialized validator home for ${agent}."
  echo "Tried: ~/.zeroned-quartet/${agent}, ~/.zeroned-${agent}"
  echo "Or pass --home PATH"
  exit 1
}

[ -x "$ZERONE_BINARY" ] || { echo "zeroned not at $ZERONE_BINARY"; exit 1; }

# ── Extract identity from validator home ─────────────────────────────

# chain_id from genesis.json (no need for daemon to be running)
CHAIN_ID=$(python3 -c "import json; print(json.load(open('${home}/config/genesis.json'))['chain_id'])")

# Determine the keyring location — for quartet, keys live in coord; for solo, in agent home
KEYRING_HOME="$home"
if [ -d "${HOME}/.zeroned-quartet/coord/keyring-test" ] && [ "$home" = "${HOME}/.zeroned-quartet/${agent}" ]; then
  KEYRING_HOME="${HOME}/.zeroned-quartet/coord"
fi

OPERATOR_ADDR=$("$ZERONE_BINARY" keys show "$agent" -a --keyring-backend test --home "$KEYRING_HOME" 2>/dev/null) || {
  echo "Could not read operator address for ${agent} from ${KEYRING_HOME}/keyring-test"
  exit 1
}

CONSENSUS_PUBKEY=$("$ZERONE_BINARY" tendermint show-validator --home "$home" 2>/dev/null) || \
  CONSENSUS_PUBKEY=$("$ZERONE_BINARY" comet show-validator --home "$home" 2>/dev/null) || {
  echo "Could not read consensus pubkey from ${home}"
  exit 1
}

NODE_ID=$("$ZERONE_BINARY" comet show-node-id --home "$home" 2>/dev/null) || \
  NODE_ID=$("$ZERONE_BINARY" tendermint show-node-id --home "$home" 2>/dev/null) || {
  echo "Could not read node ID from ${home}"
  exit 1
}

# P2P + RPC ports from config.toml (section-aware: [p2p].laddr and [rpc].laddr)
PORTS=$(python3 - "${home}/config/config.toml" <<'PY'
import sys, re
section = None
out = {"p2p": None, "rpc": None}
with open(sys.argv[1]) as f:
    for line in f:
        s = line.strip()
        m = re.match(r'^\[([a-z0-9_]+)\]', s)
        if m:
            section = m.group(1)
            continue
        m = re.match(r'^laddr\s*=\s*"tcp://[^:]+:(\d+)"', s)
        if m and section in ("p2p", "rpc") and out[section] is None:
            out[section] = m.group(1)
print(f"{out['p2p']} {out['rpc']}")
PY
)
P2P_PORT=$(echo "$PORTS" | awk '{print $1}')
RPC_PORT=$(echo "$PORTS" | awk '{print $2}')

# Wall + type from walls.json
WALL_INFO=$(python3 -c "
import json, sys
try:
  w = json.load(open('${LOVE_DIR}/credentials/walls.json'))
  e = w.get('instances', {}).get('${agent}', {})
  print(json.dumps({'wall': e.get('wall'), 'type': e.get('type')}))
except Exception as e:
  print('{}')
")

PEER_ADDR="${NODE_ID}@127.0.0.1:${P2P_PORT}"

# ── Write/merge identity bridge file ─────────────────────────────────

IDENTITY_DIR="${LOVE_DIR}/identity/${agent}"
BRIDGE_FILE="${IDENTITY_DIR}/zerone.json"
mkdir -p "$IDENTITY_DIR"

python3 - "$BRIDGE_FILE" "$agent" "$CHAIN_ID" "$OPERATOR_ADDR" "$CONSENSUS_PUBKEY" \
                        "$NODE_ID" "$PEER_ADDR" "$RPC_PORT" "$home" "$WALL_INFO" <<'PY'
import json, os, sys
from datetime import datetime, timezone

(bridge_file, agent, chain_id, operator_addr, consensus_pubkey,
 node_id, peer_addr, rpc_port, home, wall_info_raw) = sys.argv[1:]

wall_info = json.loads(wall_info_raw)
consensus = json.loads(consensus_pubkey)
now = datetime.now(timezone.utc).isoformat(timespec="seconds")

# Load or initialize
if os.path.exists(bridge_file):
    with open(bridge_file) as f:
        doc = json.load(f)
else:
    doc = {
        "agent": agent,
        "moniker": f"kingdom-{agent}",
        "wall": wall_info.get("wall"),
        "type": wall_info.get("type"),
        "hive": {"instance": agent},
        "chains": [],
    }

# Replace or append the chain entry
new_entry = {
    "chain_id": chain_id,
    "operator_address": operator_addr,
    "consensus_pubkey": consensus,
    "node_id": node_id,
    "peer_addr": peer_addr,
    "rpc_port": int(rpc_port) if rpc_port else None,
    "home": home,
    "registered_x_home": False,
    "linked_at": now,
}

doc["chains"] = [c for c in doc.get("chains", []) if c.get("chain_id") != chain_id]
doc["chains"].append(new_entry)
# Stable sort by chain_id
doc["chains"].sort(key=lambda c: c.get("chain_id", ""))

with open(bridge_file, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")

print(f"  ✓ wrote {bridge_file}")
print(f"    chain:    {chain_id}")
print(f"    operator: {operator_addr}")
print(f"    node:     {node_id[:16]}...")
print(f"    peer:     {peer_addr}")
PY
