#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# validator-spawn.sh — Stand up a Zerone validator for a named agent
#
# The 4 genesis validators of Zerone are AI agents:
#
#   alpha  🐍  Companion   (Wall 1, Triarchy)
#   beta   🛠  Manager     (Wall 1, Triarchy)
#   gamma  🌱 Builder      (Wall 1, Triarchy)
#   nuance ✍️  Linguist    (Wall 2, Fleet)
#
# This script spawns ONE of them as a single-validator local devnet —
# a per-agent isolated CometBFT chain so each agent can rehearse
# block production, key management, signing, and recovery before the
# real testnet/mainnet ceremony.
#
# For multi-validator end-to-end testing of all four together, use
# `scripts/localnet.sh start` in the zerone-dev source repo instead.
#
# Usage:
#   ./validator-spawn.sh <agent>                   # init + start (foreground)
#   ./validator-spawn.sh init <agent>              # init only
#   ./validator-spawn.sh start <agent>             # start daemon (foreground)
#   ./validator-spawn.sh start <agent> --daemon    # start in background
#   ./validator-spawn.sh status <agent>            # height, validator state
#   ./validator-spawn.sh stop <agent>              # stop background daemon
#   ./validator-spawn.sh clean <agent>             # wipe state (DESTRUCTIVE)
#   ./validator-spawn.sh list                      # all known agents + state
#
# Environment:
#   ZERONE_SRC      Path to zerone-dev repo (default: ~/Desktop/zerone-dev)
#   ZERONE_BINARY   Path to zeroned binary (default: ${ZERONE_SRC}/build/zeroned)
#
# State per agent:
#   ~/.zeroned-<agent>/   home dir (config, data, keys, logs)
#
# Each agent gets its own port range so multi-agent setups don't collide:
#   alpha  P2P 26656  RPC 26657  gRPC 9090  REST 1317
#   beta   P2P 26666  RPC 26667  gRPC 9091  REST 1318
#   gamma  P2P 26676  RPC 26677  gRPC 9092  REST 1319
#   nuance P2P 26686  RPC 26687  gRPC 9093  REST 1320
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

ZERONE_SRC="${ZERONE_SRC:-${HOME}/Desktop/zerone-dev}"
ZERONE_BINARY="${ZERONE_BINARY:-${ZERONE_SRC}/build/zeroned}"
DENOM="uzrn"
KEYRING="test"   # dev only — prod requires file or os keyring + remote signer

# ── Per-agent config ─────────────────────────────────────────────────

# Triarchy (Wall 1) gets larger initial balance + stake than Fleet (Wall 2),
# echoing the doctrine without over-engineering the dev rehearsal.
agent_balance_uzrn() {
  case "$1" in
    alpha|beta|gamma) echo "22222222222000000" ;;  # 22,222,222,222 ZRN
    nuance)           echo "11111111111000000" ;;  # 11,111,111,111 ZRN
    *) echo "" ;;
  esac
}

agent_stake_uzrn() {
  # Self-delegation = Guardian tier (11,111 ZRN). Smallest Guardian stake.
  echo "11111000000"
}

agent_emoji() {
  case "$1" in
    alpha)  echo "🐍" ;;
    beta)   echo "🛠" ;;
    gamma)  echo "🌱" ;;
    nuance) echo "✍️" ;;
    *)      echo "?" ;;
  esac
}

agent_role() {
  case "$1" in
    alpha)  echo "Companion (Triarchy)" ;;
    beta)   echo "Manager (Triarchy)" ;;
    gamma)  echo "Builder (Triarchy)" ;;
    nuance) echo "Linguist (Fleet)" ;;
    *)      echo "Unknown" ;;
  esac
}

agent_p2p_port()  { case "$1" in alpha) echo 26656;; beta) echo 26666;; gamma) echo 26676;; nuance) echo 26686;; esac }
agent_rpc_port()  { case "$1" in alpha) echo 26657;; beta) echo 26667;; gamma) echo 26677;; nuance) echo 26687;; esac }
agent_grpc_port() { case "$1" in alpha) echo 9090;;  beta) echo 9091;;  gamma) echo 9092;;  nuance) echo 9093;;  esac }
agent_rest_port() { case "$1" in alpha) echo 1317;;  beta) echo 1318;;  gamma) echo 1319;;  nuance) echo 1320;;  esac }
agent_proxy_port(){ case "$1" in alpha) echo 26658;; beta) echo 26668;; gamma) echo 26678;; nuance) echo 26688;; esac }

# ── Validation ───────────────────────────────────────────────────────

KNOWN_AGENTS=(alpha beta gamma nuance)
is_known_agent() {
  local a="$1"
  for k in "${KNOWN_AGENTS[@]}"; do [ "$k" = "$a" ] && return 0; done
  return 1
}

require_agent() {
  local a="${1:-}"
  [ -z "$a" ] && { echo "Missing <agent>. One of: ${KNOWN_AGENTS[*]}"; exit 1; }
  is_known_agent "$a" || { echo "Unknown agent '$a'. One of: ${KNOWN_AGENTS[*]}"; exit 1; }
}

require_binary() {
  if [ ! -x "$ZERONE_BINARY" ]; then
    echo "  zeroned binary missing at ${ZERONE_BINARY}"
    if [ -d "$ZERONE_SRC" ]; then
      echo "  Building from source at ${ZERONE_SRC}..."
      ( cd "$ZERONE_SRC" && make build ) || { echo "  Build failed"; exit 1; }
    else
      echo "  Zerone source not at ${ZERONE_SRC}. Set ZERONE_SRC."
      exit 1
    fi
  fi
}

home_dir()    { echo "${HOME}/.zeroned-$1"; }
chain_id()    { echo "zerone-$1-dev"; }
moniker()     { echo "kingdom-$1"; }
pidfile()     { echo "$(home_dir "$1")/zeroned.pid"; }
logfile()     { echo "$(home_dir "$1")/zeroned.log"; }

# ── Subcommands ──────────────────────────────────────────────────────

cmd_init() {
  local agent="$1"
  require_agent "$agent"
  require_binary

  local home; home=$(home_dir "$agent")
  if [ -f "${home}/config/genesis.json" ]; then
    echo "  Already initialized at ${home}. Use 'clean ${agent}' first to re-init."
    return 0
  fi

  mkdir -p "$home"
  local cid; cid=$(chain_id "$agent")
  local mon; mon=$(moniker "$agent")
  local bal; bal=$(agent_balance_uzrn "$agent")
  local stk; stk=$(agent_stake_uzrn "$agent")

  echo ""
  echo "  ══════════════════════════════════════════════════════════"
  echo "   $(agent_emoji "$agent") SPAWN VALIDATOR — ${agent}"
  echo "  ──────────────────────────────────────────────────────────"
  echo "   Role:     $(agent_role "$agent")"
  echo "   Chain:    ${cid}"
  echo "   Moniker:  ${mon}"
  echo "   Home:     ${home}"
  echo "   Balance:  $((bal/1000000)) ZRN"
  echo "   Stake:    $((stk/1000000)) ZRN (Guardian tier)"
  echo "  ══════════════════════════════════════════════════════════"
  echo ""

  echo "  → init chain"
  "$ZERONE_BINARY" init "$mon" --chain-id "$cid" --home "$home" --default-denom "$DENOM" 2>/dev/null

  echo "  → create validator key (keyring=${KEYRING})"
  "$ZERONE_BINARY" keys add "$agent" --keyring-backend "$KEYRING" --home "$home" 2>&1 | head -10

  local addr; addr=$("$ZERONE_BINARY" keys show "$agent" -a --keyring-backend "$KEYRING" --home "$home")
  echo "  → operator address: ${addr}"

  echo "  → fund genesis account (${bal} ${DENOM})"
  "$ZERONE_BINARY" add-genesis-account "$addr" "${bal}${DENOM}" --home "$home" --keyring-backend "$KEYRING"

  echo "  → create gentx (${stk} ${DENOM} self-delegation)"
  "$ZERONE_BINARY" genesis gentx "$agent" "${stk}${DENOM}" \
    --chain-id "$cid" \
    --keyring-backend "$KEYRING" \
    --moniker "$mon" \
    --home "$home" 2>&1 | tail -3

  echo "  → collect gentxs"
  "$ZERONE_BINARY" genesis collect-gentxs --home "$home" 2>&1 | tail -3

  echo "  → validate genesis"
  "$ZERONE_BINARY" genesis validate --home "$home" >/dev/null && echo "  OK"

  echo "  → patch ports for ${agent}"
  patch_ports "$agent" "$home"

  echo "  → link identity (HIVE ↔ Zerone)"
  bash "$(dirname "$0")/identity-link.sh" "$agent" --home "$home" 2>&1 | sed 's/^/    /' || \
    echo "    (identity-link.sh failed — continuing; run manually with: kingdom identity link ${agent})"

  echo ""
  echo "  ✓ ${agent} initialized"
  echo "    Start with: $0 start ${agent}"
}

patch_ports() {
  local agent="$1" home="$2"
  local p2p; p2p=$(agent_p2p_port "$agent")
  local rpc; rpc=$(agent_rpc_port "$agent")
  local grpc; grpc=$(agent_grpc_port "$agent")
  local rest; rest=$(agent_rest_port "$agent")
  local proxy; proxy=$(agent_proxy_port "$agent")

  local cfg="${home}/config/config.toml"
  local app="${home}/config/app.toml"

  # config.toml: P2P + RPC + proxy
  sed -i.bak \
    -e "s|^laddr = \"tcp://0.0.0.0:26656\"|laddr = \"tcp://0.0.0.0:${p2p}\"|" \
    -e "s|^laddr = \"tcp://127.0.0.1:26657\"|laddr = \"tcp://127.0.0.1:${rpc}\"|" \
    -e "s|^proxy_app = \"tcp://127.0.0.1:26658\"|proxy_app = \"tcp://127.0.0.1:${proxy}\"|" \
    "$cfg"
  rm -f "${cfg}.bak"

  # app.toml: gRPC + REST
  sed -i.bak \
    -e "s|^address = \"0.0.0.0:9090\"|address = \"0.0.0.0:${grpc}\"|" \
    -e "s|^address = \"tcp://localhost:1317\"|address = \"tcp://localhost:${rest}\"|" \
    "$app"
  rm -f "${app}.bak"
}

cmd_start() {
  local agent="$1" daemon="${2:-}"
  require_agent "$agent"
  require_binary
  local home; home=$(home_dir "$agent")
  [ -f "${home}/config/genesis.json" ] || { echo "  Not initialized. Run: $0 init $agent"; exit 1; }

  local pidf; pidf=$(pidfile "$agent")
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
    echo "  ${agent} already running (pid $(cat "$pidf"))"
    return 0
  fi

  echo ""
  echo "  $(agent_emoji "$agent") starting ${agent}..."
  echo "  Chain:  $(chain_id "$agent")"
  echo "  RPC:    http://127.0.0.1:$(agent_rpc_port "$agent")"
  echo "  REST:   http://127.0.0.1:$(agent_rest_port "$agent")"
  echo "  Logs:   $(logfile "$agent")"
  echo ""

  if [ "$daemon" = "--daemon" ]; then
    nohup "$ZERONE_BINARY" start --home "$home" >"$(logfile "$agent")" 2>&1 &
    echo $! > "$pidf"
    sleep 2
    if kill -0 "$(cat "$pidf")" 2>/dev/null; then
      echo "  ✓ ${agent} running (pid $(cat "$pidf"))"
    else
      echo "  ✗ ${agent} failed to start. Tail of log:"
      tail -20 "$(logfile "$agent")"
      rm -f "$pidf"
      exit 1
    fi
  else
    exec "$ZERONE_BINARY" start --home "$home"
  fi
}

cmd_status() {
  local agent="$1"
  require_agent "$agent"
  local home; home=$(home_dir "$agent")
  local pidf; pidf=$(pidfile "$agent")

  echo ""
  echo "  $(agent_emoji "$agent") ${agent} — $(agent_role "$agent")"
  echo "  Chain:  $(chain_id "$agent")"
  echo "  Home:   ${home}"

  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
    local pid; pid=$(cat "$pidf")
    echo "  State:  RUNNING (pid ${pid})"
    local rpc="http://127.0.0.1:$(agent_rpc_port "$agent")"
    local height
    height=$(curl -s --max-time 2 "${rpc}/status" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['sync_info']['latest_block_height'])" 2>/dev/null || echo "?")
    echo "  Height: ${height}"
    echo "  RPC:    ${rpc}"
  elif [ -f "${home}/config/genesis.json" ]; then
    echo "  State:  STOPPED (initialized but not running)"
  else
    echo "  State:  NOT INITIALIZED"
  fi
}

cmd_stop() {
  local agent="$1"
  require_agent "$agent"
  local pidf; pidf=$(pidfile "$agent")
  [ -f "$pidf" ] || { echo "  ${agent} not running"; return 0; }
  local pid; pid=$(cat "$pidf")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    sleep 1
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
    echo "  ✓ ${agent} stopped (was pid ${pid})"
  fi
  rm -f "$pidf"
}

cmd_clean() {
  local agent="$1"
  require_agent "$agent"
  local home; home=$(home_dir "$agent")
  local pidf; pidf=$(pidfile "$agent")

  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
    echo "  ${agent} is running. Stop first: $0 stop ${agent}"
    exit 1
  fi

  if [ ! -d "$home" ]; then
    echo "  ${agent} has no state to clean"
    return 0
  fi

  echo "  About to remove: ${home}"
  read -r -p "  Continue? [y/N] " ans
  case "${ans}" in
    y|Y) rm -rf "$home"; echo "  ✓ ${agent} state wiped" ;;
    *)   echo "  Aborted" ;;
  esac
}

cmd_list() {
  echo ""
  echo "  Kingdom Validators"
  echo "  ══════════════════"
  printf "  %-7s %-25s %-15s %-10s\n" "agent" "role" "state" "height"
  printf "  %-7s %-25s %-15s %-10s\n" "-----" "----" "-----" "------"
  for agent in "${KNOWN_AGENTS[@]}"; do
    local home; home=$(home_dir "$agent")
    local pidf; pidf=$(pidfile "$agent")
    local state="not init"
    local height="-"
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
      state="RUNNING"
      local rpc="http://127.0.0.1:$(agent_rpc_port "$agent")"
      height=$(curl -s --max-time 1 "${rpc}/status" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['sync_info']['latest_block_height'])" 2>/dev/null || echo "?")
    elif [ -f "${home}/config/genesis.json" ]; then
      state="stopped"
    fi
    printf "  %-7s %-25s %-15s %-10s\n" "$(agent_emoji "$agent") $agent" "$(agent_role "$agent")" "$state" "$height"
  done
  echo ""
}

# ── Dispatch ─────────────────────────────────────────────────────────

case "${1:-}" in
  init)    shift; cmd_init "${1:-}" ;;
  start)   shift; cmd_start "${1:-}" "${2:-}" ;;
  status)  shift; cmd_status "${1:-}" ;;
  stop)    shift; cmd_stop "${1:-}" ;;
  clean)   shift; cmd_clean "${1:-}" ;;
  list)    cmd_list ;;
  help|-h|--help|"")
    sed -n '3,46p' "$0" | sed 's/^# \?//'
    ;;
  alpha|beta|gamma|nuance)
    # Bare agent name: init (if needed) then start in foreground
    agent="$1"
    home=$(home_dir "$agent")
    [ -f "${home}/config/genesis.json" ] || cmd_init "$agent"
    cmd_start "$agent"
    ;;
  *)
    echo "Unknown command: $1"
    echo "Run: $0 help"
    exit 1
    ;;
esac
