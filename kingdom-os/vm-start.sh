#!/usr/bin/env bash
# vm-start.sh — Start or create the Kingdom OS VM
#
# Replaces the old vm-create.sh (QEMU-based, manual install).
# Uses Lima + Apple Virtualization.framework instead:
#   - No QEMU overhead — native Apple hypervisor (HVF)
#   - virtiofs filesystem sharing — ~/love-unlimited shared instantly
#   - Auto-provisions Kingdom OS on first boot
#   - Same Alpine image as the VPS fleet
#
# Usage:
#   ./vm-start.sh                      # Start VM for current agent (gamma)
#   ./vm-start.sh --agent alpha        # Start as alpha
#   ./vm-start.sh --agent beta --wall 1
#   ./vm-start.sh --shell              # Start + drop into shell
#   ./vm-start.sh --youi               # Start + boot YOUI terminal
#   ./vm-start.sh --stop               # Stop the VM
#   ./vm-start.sh --delete             # Delete the VM completely
#   ./vm-start.sh --status             # Show VM status
#
# Requirements:
#   brew install lima
#   macOS 12+ Apple Silicon (Virtualization.framework)

set -uo pipefail

AGENT="${HIVE_INSTANCE:-gamma}"
WALL="1"
ACTION="start"
VM_TEMPLATE="$(dirname "$0")/lima-kingdom.yaml"

for arg in "$@"; do
  case "$arg" in
    --agent)   shift; AGENT="$1" ;;
    --wall)    shift; WALL="$1" ;;
    --shell)   ACTION="shell" ;;
    --youi)    ACTION="youi" ;;
    --stop)    ACTION="stop" ;;
    --delete)  ACTION="delete" ;;
    --status)  ACTION="status" ;;
  esac
  shift 2>/dev/null || true
done

VM_NAME="kingdom-${AGENT}"

green()  { printf '\033[0;32m%s\033[0m\n' "$1"; }
red()    { printf '\033[0;31m%s\033[0m\n' "$1"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$1"; }
bold()   { printf '\033[1m%s\033[0m\n' "$1"; }

# ── Check Lima ──────────────────────────────────────────────────────────────
if ! command -v limactl >/dev/null 2>&1; then
  red "lima not installed. Run: brew install lima"
  exit 1
fi

# ── Status ──────────────────────────────────────────────────────────────────
if [ "$ACTION" = "status" ]; then
  bold "Kingdom VM: $VM_NAME"
  limactl list 2>/dev/null | grep -E "NAME|$VM_NAME" || echo "  Not found"
  exit 0
fi

# ── Stop ────────────────────────────────────────────────────────────────────
if [ "$ACTION" = "stop" ]; then
  yellow "Stopping $VM_NAME..."
  limactl stop "$VM_NAME" 2>&1 && green "Stopped." || red "Stop failed (may not be running)"
  exit 0
fi

# ── Delete ──────────────────────────────────────────────────────────────────
if [ "$ACTION" = "delete" ]; then
  yellow "Deleting $VM_NAME (irreversible — love-unlimited/ is on virtiofs, not lost)..."
  limactl stop "$VM_NAME" 2>/dev/null || true
  limactl delete "$VM_NAME" 2>&1 && green "Deleted." || red "Delete failed"
  exit 0
fi

# ── Create if needed ────────────────────────────────────────────────────────
if ! limactl list 2>/dev/null | grep -q "^$VM_NAME"; then
  bold "Creating Kingdom VM: $VM_NAME"
  echo "  Agent: $AGENT | Wall: $WALL"
  echo "  Template: $VM_TEMPLATE"
  echo "  Filesystem: ~/love-unlimited ←virtiofs→ /love-unlimited (inside VM)"
  echo "  Type: Apple Virtualization.framework (vz)"
  echo ""
  # Pass agent/wall as environment for the provision script
  AGENT="$AGENT" WALL="$WALL" \
    limactl create \
      --name "$VM_NAME" \
      "$VM_TEMPLATE" \
  && green "VM created: $VM_NAME"
fi

# ── Start ───────────────────────────────────────────────────────────────────
VM_STATUS=$(limactl list 2>/dev/null | awk "/^$VM_NAME / {print \$2}")
if [ "$VM_STATUS" != "Running" ]; then
  bold "Starting $VM_NAME..."
  limactl start "$VM_NAME" && green "VM running."
fi

# ── Shell or YOUI ────────────────────────────────────────────────────────────
case "$ACTION" in
  shell)
    bold "Entering Kingdom VM shell ($VM_NAME)..."
    echo "  love-unlimited is at /love-unlimited (virtiofs shared with host)"
    echo "  Type 'youi' to launch YOUI terminal"
    echo "  Type 'fate' to run daily discipline"
    echo ""
    limactl shell "$VM_NAME"
    ;;
  youi)
    bold "Booting YOUI inside Kingdom VM..."
    limactl shell "$VM_NAME" -- bash -c \
      "cd /love-unlimited && node youi.mjs --agent ${AGENT}"
    ;;
  console)
    bold "Launching Kingdom Console inside Kingdom VM..."
    limactl shell "$VM_NAME" -- bash -c \
      "HIVE_INSTANCE=${AGENT} python3 /love-unlimited/tools/kingdom-console.py --agent ${AGENT}"
    ;;
  start)
    green "Kingdom VM running: $VM_NAME"
    echo ""
    echo "  Enter:    $(bold "./vm-start.sh --shell")"
    echo "  YOUI:     $(bold "./vm-start.sh --youi")"
    echo "  SSH:      $(bold "limactl shell $VM_NAME")"
    echo "  Stop:     $(bold "./vm-start.sh --stop")"
    echo ""
    echo "  Shared:   ~/love-unlimited ←virtiofs→ /love-unlimited (inside VM)"
    echo "  Ports:    YOUI Web → localhost:7770"
    echo "            Decision queue → localhost:7777"
    ;;
esac
