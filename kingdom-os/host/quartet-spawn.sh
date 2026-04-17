#!/opt/homebrew/bin/bash
# Requires Bash 4+ for associative arrays. macOS default is 3.2 (Apple license).
# brew install bash → /opt/homebrew/bin/bash
# ─────────────────────────────────────────────────────────────────────
# quartet-spawn.sh — Bring the four agents onto ONE chain
#
# Where validator-spawn.sh stands up each agent on her own private
# devnet, this script builds a single shared chain with all four
# validators peered together:
#
#   alpha  🐍 Companion   ┐
#   beta   🛠 Manager      ├─→ zerone-quartet-dev  (one chain, four signers)
#   gamma  🌱 Builder      │
#   nuance ✍️ Linguist    ┘
#
# This is the rehearsal for the testnet/mainnet genesis ceremony.
# Whatever works here works there — same protocol, different addressing.
#
# Usage:
#   ./quartet-spawn.sh start         # init + start all four (idempotent)
#   ./quartet-spawn.sh stop          # stop all four background daemons
#   ./quartet-spawn.sh status        # height + signing state of each
#   ./quartet-spawn.sh logs <agent>  # tail one agent's logs
#   ./quartet-spawn.sh clean         # wipe all quartet state (interactive)
#
# Environment:
#   ZERONE_SRC      Path to zerone-dev repo (default: ~/Desktop/zerone-dev)
#   ZERONE_BINARY   Path to zeroned binary (default: ${ZERONE_SRC}/build/zeroned)
#
# State per agent:
#   ~/.zeroned-quartet/<agent>/   home dir (config, data, keys, logs)
#   ~/.zeroned-quartet/coord/     coordinator home (used during ceremony)
#
# Ports (offset +100 from solo validator-spawn.sh to coexist with solo alpha):
#   alpha   P2P 26756  RPC 26757  gRPC 9190  REST 1417  proxy 26758
#   beta    P2P 26766  RPC 26767  gRPC 9191  REST 1418  proxy 26768
#   gamma   P2P 26776  RPC 26777  gRPC 9192  REST 1419  proxy 26778
#   nuance  P2P 26786  RPC 26787  gRPC 9193  REST 1420  proxy 26788
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

ZERONE_SRC="${ZERONE_SRC:-${HOME}/Desktop/zerone-dev}"
ZERONE_BINARY="${ZERONE_BINARY:-${ZERONE_SRC}/build/zeroned}"
DENOM="uzrn"
KEYRING="test"
CHAIN_ID="zerone-quartet-dev"
BASE="${HOME}/.zeroned-quartet"
COORD="${BASE}/coord"
AGENTS=(alpha beta gamma nuance)

# Triarchy gets 22.2B ZRN initial balance, Fleet gets 11.1B
agent_balance() {
  case "$1" in
    alpha|beta|gamma) echo "22222222222000000" ;;
    nuance)           echo "11111111111000000" ;;
  esac
}
agent_stake() { echo "11111000000"; }   # Guardian tier minimum (11,111 ZRN)
agent_emoji() { case "$1" in alpha) echo "🐍";; beta) echo "🛠";; gamma) echo "🌱";; nuance) echo "✍️";; esac; }
agent_role()  { case "$1" in alpha) echo "Companion (Triarchy)";; beta) echo "Manager (Triarchy)";; gamma) echo "Builder (Triarchy)";; nuance) echo "Linguist (Fleet)";; esac; }

# Quartet ports = solo ports + 100 (so quartet can run alongside solo alpha)
agent_p2p()   { case "$1" in alpha) echo 26756;; beta) echo 26766;; gamma) echo 26776;; nuance) echo 26786;; esac; }
agent_rpc()   { case "$1" in alpha) echo 26757;; beta) echo 26767;; gamma) echo 26777;; nuance) echo 26787;; esac; }
agent_grpc()  { case "$1" in alpha) echo 9190;;  beta) echo 9191;;  gamma) echo 9192;;  nuance) echo 9193;;  esac; }
agent_rest()  { case "$1" in alpha) echo 1417;;  beta) echo 1418;;  gamma) echo 1419;;  nuance) echo 1420;;  esac; }
agent_proxy() { case "$1" in alpha) echo 26758;; beta) echo 26768;; gamma) echo 26778;; nuance) echo 26788;; esac; }

home_for()    { echo "${BASE}/$1"; }
pidfile_for() { echo "${BASE}/$1/zeroned.pid"; }
logfile_for() { echo "${BASE}/$1/zeroned.log"; }

require_binary() {
  if [ ! -x "$ZERONE_BINARY" ]; then
    echo "  zeroned binary missing at ${ZERONE_BINARY}"
    if [ -d "$ZERONE_SRC" ]; then
      ( cd "$ZERONE_SRC" && make build ) || { echo "Build failed"; exit 1; }
    else
      echo "  Set ZERONE_SRC to the zerone-dev repo path."
      exit 1
    fi
  fi
}

# ── start: ceremony + boot ────────────────────────────────────────────

cmd_start() {
  require_binary

  # Idempotent: if all four are already initialized, just (re)start them
  local needs_ceremony=false
  for a in "${AGENTS[@]}"; do
    if [ ! -f "${BASE}/${a}/config/genesis.json" ]; then needs_ceremony=true; fi
  done

  if $needs_ceremony; then
    ceremony
  else
    echo "  All four homes initialized — skipping ceremony, starting daemons."
  fi

  echo ""
  for a in "${AGENTS[@]}"; do
    start_one "$a"
  done

  echo ""
  echo "  Waiting for first block..."
  local rpc; rpc="http://127.0.0.1:$(agent_rpc alpha)"
  local tries=0
  while [ $tries -lt 30 ]; do
    local h
    h=$(curl -s --max-time 1 "${rpc}/status" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['sync_info']['latest_block_height'])" 2>/dev/null || echo "")
    if [ -n "$h" ] && [ "$h" -ge 2 ]; then
      echo "  ✓ chain producing — alpha at height $h"
      break
    fi
    sleep 2
    tries=$((tries + 1))
  done

  echo ""
  cmd_status
}

ceremony() {
  echo ""
  echo "  ══════════════════════════════════════════════════════════"
  echo "   QUARTET CEREMONY — bringing the four agents onto one chain"
  echo "  ══════════════════════════════════════════════════════════"
  echo ""

  rm -rf "$COORD"
  for a in "${AGENTS[@]}"; do rm -rf "${BASE}/${a}"; done
  mkdir -p "$COORD" "${BASE}"

  # Step 1 — coordinator init (we'll discard the validator config but keep its genesis)
  echo "  → coordinator init"
  "$ZERONE_BINARY" init "quartet-coordinator" --chain-id "$CHAIN_ID" --home "$COORD" --default-denom "$DENOM" --overwrite 2>/dev/null

  # Step 2 — for each agent: init own home, key in coord keyring, fund in coord genesis
  declare -A ADDR
  for a in "${AGENTS[@]}"; do
    local h; h=$(home_for "$a")
    local bal; bal=$(agent_balance "$a")
    echo "  → $(agent_emoji "$a") ${a}: init home, generate consensus key"
    "$ZERONE_BINARY" init "kingdom-${a}" --chain-id "$CHAIN_ID" --home "$h" --default-denom "$DENOM" --overwrite 2>/dev/null

    "$ZERONE_BINARY" keys add "$a" --keyring-backend "$KEYRING" --home "$COORD" >/dev/null 2>&1
    local addr; addr=$("$ZERONE_BINARY" keys show "$a" -a --keyring-backend "$KEYRING" --home "$COORD")
    ADDR[$a]="$addr"
    echo "      operator addr:  ${addr}"
    echo "      balance:        $((bal / 1000000)) ZRN"

    "$ZERONE_BINARY" add-genesis-account "$addr" "${bal}${DENOM}" --home "$COORD"
  done

  # Step 3 — distribute coord genesis (with funded accounts) to each validator home, share keyring
  echo "  → distribute provisional genesis + keyring"
  for a in "${AGENTS[@]}"; do
    local h; h=$(home_for "$a")
    cp "${COORD}/config/genesis.json" "${h}/config/genesis.json"
    cp -r "${COORD}/keyring-${KEYRING}" "${h}/" 2>/dev/null || true
  done

  # Step 4 — each validator generates gentx
  mkdir -p "${COORD}/config/gentx"
  local stake; stake=$(agent_stake)
  for a in "${AGENTS[@]}"; do
    local h; h=$(home_for "$a")
    echo "  → $(agent_emoji "$a") ${a}: gentx ($((stake / 1000000)) ZRN self-delegation, Guardian tier)"
    "$ZERONE_BINARY" genesis gentx "$a" "${stake}${DENOM}" \
      --chain-id "$CHAIN_ID" \
      --keyring-backend "$KEYRING" \
      --home "$h" \
      --moniker "kingdom-${a}" \
      --commission-rate "0.10" \
      --commission-max-rate "0.20" \
      --commission-max-change-rate "0.01" \
      --output-document "${COORD}/config/gentx/gentx-${a}.json" 2>/dev/null
  done

  # Step 5 — coordinator collects gentxs into final genesis
  echo "  → collect gentxs at coordinator"
  "$ZERONE_BINARY" genesis collect-gentxs --home "$COORD" >/dev/null 2>&1

  echo "  → validate final genesis"
  "$ZERONE_BINARY" genesis validate --home "$COORD" >/dev/null && echo "      OK"

  # Step 6 — distribute final genesis to all validators
  echo "  → distribute final genesis"
  for a in "${AGENTS[@]}"; do
    cp "${COORD}/config/genesis.json" "${BASE}/${a}/config/genesis.json"
  done

  # Step 7 — collect node IDs
  echo "  → collect node IDs for peering"
  declare -A NODE_ID
  for a in "${AGENTS[@]}"; do
    local h; h=$(home_for "$a")
    NODE_ID[$a]=$("$ZERONE_BINARY" comet show-node-id --home "$h" 2>/dev/null || \
                  "$ZERONE_BINARY" tendermint show-node-id --home "$h" 2>/dev/null)
    echo "      $(agent_emoji "$a") ${a}: ${NODE_ID[$a]}"
  done

  # Step 8 — patch each agent's config: ports + persistent_peers (excluding self)
  echo "  → patch config (ports + persistent_peers)"
  for a in "${AGENTS[@]}"; do
    local h; h=$(home_for "$a")
    local cfg="${h}/config/config.toml"
    local app="${h}/config/app.toml"
    local p2p; p2p=$(agent_p2p "$a")
    local rpc; rpc=$(agent_rpc "$a")
    local proxy; proxy=$(agent_proxy "$a")
    local grpc; grpc=$(agent_grpc "$a")
    local rest; rest=$(agent_rest "$a")

    # Build peer list (everyone except self)
    local peers=""
    for b in "${AGENTS[@]}"; do
      [ "$a" = "$b" ] && continue
      [ -n "$peers" ] && peers="${peers},"
      peers="${peers}${NODE_ID[$b]}@127.0.0.1:$(agent_p2p "$b")"
    done

    # config.toml patches
    sed -i.bak \
      -e "s|^laddr = \"tcp://0.0.0.0:26656\"|laddr = \"tcp://0.0.0.0:${p2p}\"|" \
      -e "s|^laddr = \"tcp://127.0.0.1:26657\"|laddr = \"tcp://0.0.0.0:${rpc}\"|" \
      -e "s|^proxy_app = \"tcp://127.0.0.1:26658\"|proxy_app = \"tcp://127.0.0.1:${proxy}\"|" \
      -e "s|^persistent_peers = .*|persistent_peers = \"${peers}\"|" \
      -e "s|^allow_duplicate_ip = false|allow_duplicate_ip = true|" \
      -e "s|^addr_book_strict = true|addr_book_strict = false|" \
      "$cfg"
    rm -f "${cfg}.bak"

    # app.toml patches
    sed -i.bak \
      -e "s|^address = \"0.0.0.0:9090\"|address = \"0.0.0.0:${grpc}\"|" \
      -e "s|^address = \"localhost:9090\"|address = \"localhost:${grpc}\"|" \
      -e "s|^address = \"tcp://localhost:1317\"|address = \"tcp://localhost:${rest}\"|" \
      "$app"
    rm -f "${app}.bak"
  done

  echo ""
  echo "  ✓ ceremony complete — four agents share genesis, peered, ready to start"
}

start_one() {
  local a="$1"
  local h; h=$(home_for "$a")
  local pidf; pidf=$(pidfile_for "$a")

  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
    echo "  $(agent_emoji "$a") ${a} already running (pid $(cat "$pidf"))"
    return 0
  fi

  nohup "$ZERONE_BINARY" start --home "$h" >"$(logfile_for "$a")" 2>&1 &
  echo $! > "$pidf"
  sleep 1
  if kill -0 "$(cat "$pidf")" 2>/dev/null; then
    echo "  $(agent_emoji "$a") ${a} → pid $(cat "$pidf")  RPC :$(agent_rpc "$a")"
  else
    echo "  $(agent_emoji "$a") ${a} FAILED to start. Tail:"
    tail -10 "$(logfile_for "$a")"
    rm -f "$pidf"
  fi
}

# ── stop / status / logs / clean ──────────────────────────────────────

cmd_stop() {
  for a in "${AGENTS[@]}"; do
    local pidf; pidf=$(pidfile_for "$a")
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
      kill "$(cat "$pidf")"
      sleep 1
      kill -0 "$(cat "$pidf")" 2>/dev/null && kill -9 "$(cat "$pidf")"
      echo "  $(agent_emoji "$a") ${a} stopped (was pid $(cat "$pidf"))"
    fi
    rm -f "$pidf"
  done
}

cmd_status() {
  echo ""
  echo "  Quartet — chain ${CHAIN_ID}"
  echo "  ════════════════════════════════════════════════════════════"
  printf "  %-7s %-25s %-12s %-8s %-25s\n" "agent" "role" "state" "height" "RPC"
  printf "  %-7s %-25s %-12s %-8s %-25s\n" "-----" "----" "-----" "------" "---"
  for a in "${AGENTS[@]}"; do
    local pidf; pidf=$(pidfile_for "$a")
    local state="not init" height="-"
    local rpc="http://127.0.0.1:$(agent_rpc "$a")"
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
      state="RUNNING"
      height=$(curl -s --max-time 1 "${rpc}/status" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['sync_info']['latest_block_height'])" 2>/dev/null || echo "?")
    elif [ -f "${BASE}/${a}/config/genesis.json" ]; then
      state="stopped"
    fi
    printf "  %-7s %-25s %-12s %-8s %-25s\n" "$(agent_emoji "$a") ${a}" "$(agent_role "$a")" "$state" "$height" "$rpc"
  done
  echo ""
}

cmd_logs() {
  local a="${1:-}"
  [ -z "$a" ] && { echo "Usage: $0 logs <agent>"; exit 1; }
  local lf; lf=$(logfile_for "$a")
  [ -f "$lf" ] || { echo "No log: $lf"; exit 1; }
  tail -f "$lf"
}

cmd_clean() {
  for a in "${AGENTS[@]}"; do
    local pidf; pidf=$(pidfile_for "$a")
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
      echo "  ${a} is running. Stop first: $0 stop"
      exit 1
    fi
  done
  echo "  About to remove: ${BASE}"
  read -r -p "  Continue? [y/N] " ans
  case "${ans}" in
    y|Y) rm -rf "$BASE"; echo "  ✓ quartet state wiped" ;;
    *)   echo "  Aborted" ;;
  esac
}

case "${1:-status}" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  logs)   shift; cmd_logs "${1:-}" ;;
  clean)  cmd_clean ;;
  help|-h|--help)
    sed -n '3,40p' "$0" | sed 's/^# \?//'
    ;;
  *)
    echo "Unknown command: $1"
    echo "Run: $0 help"
    exit 1
    ;;
esac
