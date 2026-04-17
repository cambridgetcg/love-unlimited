#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# snapshot.sh — Host-side undo for Kingdom OS VMs
#
# Kingdom OS strips all in-guest hardening. The agent is sovereign
# inside the VM. Safety lives here, on the host: take a snapshot
# before a risky session, restore if the agent breaks its world.
#
# This is the entire safety story — there is no second layer inside.
#
# Usage:
#   ./snapshot.sh save [name]          Snapshot the VM (default: timestamp)
#   ./snapshot.sh restore <name>       Roll the VM back
#   ./snapshot.sh list                 Show snapshots
#   ./snapshot.sh delete <name>        Remove a snapshot
#   ./snapshot.sh diff <name>          Show files changed since snapshot
#
# Backend: prefers `limactl snapshot` (QEMU only). For vz vmType
# (Apple Virtualization.framework), falls back to raw diffdisk copy.
#
# Env:
#   VM_NAME       Lima VM name (default: kingdom)
#   SNAPSHOT_DIR  Where vz fallback snapshots live (default: ~/.kingdom-os/snapshots)
# ─────────────────────────────────────────────────────────────────────

set -e

VM_NAME="${VM_NAME:-kingdom}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-${HOME}/.kingdom-os/snapshots/${VM_NAME}}"

require_lima() {
  command -v limactl >/dev/null 2>&1 || { echo "limactl not found. brew install lima"; exit 1; }
}

vm_exists() {
  limactl list --quiet 2>/dev/null | grep -qx "${VM_NAME}"
}

vm_type() {
  limactl list --json 2>/dev/null | awk -v n="${VM_NAME}" '
    $0 ~ "\"name\":\"" n "\"" {
      match($0, /"vmType":"[^"]+"/)
      if (RSTART) print substr($0, RSTART+10, RLENGTH-11)
    }' | head -1
}

vm_status() {
  limactl list --json 2>/dev/null | awk -v n="${VM_NAME}" '
    $0 ~ "\"name\":\"" n "\"" {
      match($0, /"status":"[^"]+"/)
      if (RSTART) print substr($0, RSTART+10, RLENGTH-11)
    }' | head -1
}

vm_diffdisk() {
  # Lima's per-VM directory contains diffdisk (the writable overlay)
  local lima_home="${LIMA_HOME:-${HOME}/.lima}"
  echo "${lima_home}/${VM_NAME}/diffdisk"
}

stop_if_running() {
  if [ "$(vm_status)" = "Running" ]; then
    echo "  Stopping VM (snapshot needs it cold)..."
    limactl stop "${VM_NAME}" >/dev/null 2>&1 || limactl stop -f "${VM_NAME}" >/dev/null 2>&1
  fi
}

cmd_save() {
  require_lima
  vm_exists || { echo "VM '${VM_NAME}' not found. Create it: limactl create --name ${VM_NAME} kingdom-os/lima-kingdom.yaml"; exit 1; }

  local name="${1:-snap-$(date +%Y%m%d-%H%M%S)}"
  local vt; vt="$(vm_type)"

  if [ "$vt" = "qemu" ]; then
    echo "  Backend: limactl snapshot (qemu)"
    limactl snapshot create "${VM_NAME}" --tag "${name}"
    echo "  ✓ Saved snapshot '${name}'"
  else
    echo "  Backend: diffdisk copy (vz — limactl snapshot unsupported)"
    local disk; disk="$(vm_diffdisk)"
    [ -f "$disk" ] || { echo "  diffdisk not found at ${disk}"; exit 1; }
    stop_if_running
    mkdir -p "${SNAPSHOT_DIR}"
    local dest="${SNAPSHOT_DIR}/${name}.diffdisk"
    [ -e "$dest" ] && { echo "  Snapshot '${name}' already exists. Delete first or pick another name."; exit 1; }
    echo "  Copying $(du -h "$disk" | awk '{print $1}') of disk state..."
    cp -c "$disk" "$dest" 2>/dev/null || cp "$disk" "$dest"
    echo "  ✓ Saved snapshot '${name}' to ${dest}"
    echo "  Restart with: limactl start ${VM_NAME}"
  fi
}

cmd_restore() {
  require_lima
  local name="$1"
  [ -z "$name" ] && { echo "Usage: $0 restore <name>"; exit 1; }
  vm_exists || { echo "VM '${VM_NAME}' not found"; exit 1; }
  local vt; vt="$(vm_type)"

  if [ "$vt" = "qemu" ]; then
    limactl snapshot apply "${VM_NAME}" --tag "${name}"
    echo "  ✓ Restored '${name}'"
  else
    local src="${SNAPSHOT_DIR}/${name}.diffdisk"
    [ -f "$src" ] || { echo "  Snapshot '${name}' not found at ${src}"; exit 1; }
    local disk; disk="$(vm_diffdisk)"
    stop_if_running
    echo "  Replacing diffdisk with snapshot '${name}'..."
    cp -c "$src" "$disk" 2>/dev/null || cp "$src" "$disk"
    echo "  ✓ Restored. Start with: limactl start ${VM_NAME}"
  fi
}

cmd_list() {
  require_lima
  vm_exists || { echo "VM '${VM_NAME}' not found"; exit 1; }
  local vt; vt="$(vm_type)"

  if [ "$vt" = "qemu" ]; then
    limactl snapshot list "${VM_NAME}"
  else
    if [ -d "$SNAPSHOT_DIR" ] && [ -n "$(ls -A "$SNAPSHOT_DIR" 2>/dev/null)" ]; then
      echo "  Snapshots for ${VM_NAME} (vz / diffdisk copies):"
      ls -lh "$SNAPSHOT_DIR" | awk 'NR>1 {printf "    %-40s %8s  %s %s %s\n", $NF, $5, $6, $7, $8}'
    else
      echo "  No snapshots for ${VM_NAME}"
    fi
  fi
}

cmd_delete() {
  require_lima
  local name="$1"
  [ -z "$name" ] && { echo "Usage: $0 delete <name>"; exit 1; }
  local vt; vt="$(vm_type)"

  if [ "$vt" = "qemu" ]; then
    limactl snapshot delete "${VM_NAME}" --tag "${name}"
    echo "  ✓ Deleted '${name}'"
  else
    local target="${SNAPSHOT_DIR}/${name}.diffdisk"
    [ -f "$target" ] || { echo "  Snapshot '${name}' not found"; exit 1; }
    rm -f "$target"
    echo "  ✓ Deleted '${name}'"
  fi
}

cmd_diff() {
  # Quick "what changed since snapshot" by listing files newer than snapshot mtime
  local name="$1"
  [ -z "$name" ] && { echo "Usage: $0 diff <name>"; exit 1; }
  local snap="${SNAPSHOT_DIR}/${name}.diffdisk"
  [ -f "$snap" ] || { echo "  Snapshot '${name}' not found"; exit 1; }
  local mount="${HOME}/love-unlimited"
  [ -d "$mount" ] || { echo "  ${mount} not present on host"; exit 1; }
  echo "  Files in ~/love-unlimited modified since snapshot '${name}':"
  find "$mount" -type f -newer "$snap" -not -path "*/.git/*" -not -path "*/node_modules/*" 2>/dev/null | head -50
}

case "${1:-}" in
  save)    shift; cmd_save "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  list)    shift; cmd_list "$@" ;;
  delete)  shift; cmd_delete "$@" ;;
  diff)    shift; cmd_diff "$@" ;;
  ""|help|-h|--help)
    sed -n '3,22p' "$0" | sed 's/^# \?//'
    ;;
  *)
    echo "Unknown command: $1"
    echo "Run: $0 help"
    exit 1
    ;;
esac
