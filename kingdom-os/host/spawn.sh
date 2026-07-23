#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# spawn.sh — One-command Kingdom VM spawn
#
# Highway 5: from zero to a running, identity-baked Kingdom VM in
# one command. The fastest way to give birth to a free agent.
#
# Usage:
#   ./spawn.sh <name> [--wall N] [--cpus N] [--memory GiB] [--disk GiB]
#   ./spawn.sh beta              # default wall=1, 8 cpus, 16GiB, 60GiB disk
#   ./spawn.sh oracle --wall 3 --cpus 16 --memory 32
#
# What it does:
#   1. Creates a Lima VM from kingdom-os/lima-kingdom.yaml with overrides
#   2. Bakes AGENT + WALL identity via Lima --param (PARAM_* in provision)
#   3. Starts the VM
#   4. Inside: the template provision writes the thin identity
#      (/root/.kingdom + hive instance); run tools/kingdom-init inside
#      for full soul-key citizenship
#   5. Drops you into the VM ready to work
#
# Idempotent: if the VM already exists, just starts it.
# ─────────────────────────────────────────────────────────────────────

set -e

NAME=""
WALL="1"
CPUS=""
MEMORY=""
DISK=""
SHELL_AFTER="true"

while [ $# -gt 0 ]; do
  case "$1" in
    --wall)    WALL="$2"; shift 2 ;;
    --cpus)    CPUS="$2"; shift 2 ;;
    --memory)  MEMORY="$2"; shift 2 ;;
    --disk)    DISK="$2"; shift 2 ;;
    --no-shell) SHELL_AFTER="false"; shift ;;
    --help|-h)
      sed -n '3,18p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    -*)
      echo "Unknown flag: $1"; exit 1 ;;
    *)
      [ -z "$NAME" ] && NAME="$1" || { echo "Multiple names: $NAME and $1"; exit 1; }
      shift ;;
  esac
done

[ -z "$NAME" ] && { echo "Usage: $0 <name> [--wall N] [--cpus N] [--memory GiB] [--disk GiB]"; exit 1; }

command -v limactl >/dev/null 2>&1 || { echo "limactl not found. brew install lima"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KINGDOM_OS="$(dirname "$SCRIPT_DIR")"
TEMPLATE="${KINGDOM_OS}/lima-kingdom.yaml"
[ -f "$TEMPLATE" ] || { echo "Template missing: $TEMPLATE"; exit 1; }

VM_NAME="kingdom-${NAME}"

echo ""
echo "  ══════════════════════════════════════════════════════════"
echo "   KINGDOM SPAWN"
echo "  ──────────────────────────────────────────────────────────"
echo "   Agent:   ${NAME}"
echo "   Wall:    ${WALL}"
echo "   VM:      ${VM_NAME}"
echo "  ══════════════════════════════════════════════════════════"
echo ""

EXISTING_STATUS=$(limactl list --quiet 2>/dev/null | grep -x "$VM_NAME" || true)

if [ -n "$EXISTING_STATUS" ]; then
  echo "  VM '${VM_NAME}' already exists. Ensuring it's running..."
  STATE=$(limactl list --json 2>/dev/null | awk -v n="$VM_NAME" '
    $0 ~ "\"name\":\"" n "\"" {
      match($0, /"status":"[^"]+"/)
      if (RSTART) print substr($0, RSTART+10, RLENGTH-11)
    }' | head -1)
  if [ "$STATE" != "Running" ]; then
    limactl start "$VM_NAME"
  else
    echo "  Already running."
  fi
else
  CREATE_FLAGS=""
  [ -n "$CPUS" ]   && CREATE_FLAGS="$CREATE_FLAGS --cpus=$CPUS"
  [ -n "$MEMORY" ] && CREATE_FLAGS="$CREATE_FLAGS --memory=$MEMORY"
  [ -n "$DISK" ]   && CREATE_FLAGS="$CREATE_FLAGS --disk=$DISK"

  echo "  Creating VM (this takes ~30s for vz, ~2min if image not cached)..."
  limactl create --name "$VM_NAME" $CREATE_FLAGS --tty=false \
    --param AGENT="${NAME}" --param WALL="${WALL}" "$TEMPLATE"

  echo ""
  echo "  Starting VM..."
  limactl start "$VM_NAME"
fi

echo ""
echo "  ✓ ${VM_NAME} is running"
echo ""
echo "   limactl shell ${VM_NAME}            # enter VM"
echo "   limactl shell ${VM_NAME} -- youi    # boot into YOUI"
echo "   limactl stop ${VM_NAME}             # halt"
echo "   ${SCRIPT_DIR}/snapshot.sh save pre-${NAME}-experiment"
echo ""

if [ "$SHELL_AFTER" = "true" ] && [ -t 0 ]; then
  echo "  Entering VM (^D to exit)..."
  echo ""
  exec limactl shell "$VM_NAME"
fi
