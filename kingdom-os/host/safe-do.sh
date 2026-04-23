#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# safe-do.sh — Snapshot, run, retain on failure
#
# Safety doctrine: the Kingdom catches falls, never blocks motion.
# safe-do takes a snapshot before your command runs. If the command
# succeeds, the snapshot is deleted. If it fails, the snapshot is
# kept and you're told exactly how to get back.
#
# Two modes (auto-detected from --vm):
#
#   ./safe-do.sh -- <cmd...>             # local working tree
#   ./safe-do.sh --vm <name> -- <cmd...> # Lima VM
#
# Local mode: creates a git ref refs/safe-do/<ts> via `git stash create`
# (no stash-list pollution, no branch clutter) capturing tracked +
# untracked + ignored files. On failure: `git stash apply refs/safe-do/<ts>`
# (or use `--restore <ts>` below).
#
# VM mode: calls snapshot.sh save <ts>, runs the command via
# `limactl shell`, deletes the snapshot on success, retains on failure.
#
# Other:
#   ./safe-do.sh --list                  # show retained safe-do refs
#   ./safe-do.sh --restore <ts>          # apply a retained checkpoint
#   ./safe-do.sh --prune-old             # remove safe-do refs older than 7d
#
# Env:
#   SAFE_DO_KEEP=1    Keep the snapshot even on success
# ─────────────────────────────────────────────────────────────────────

set -uo pipefail

VM_NAME=""
RESTORE_TS=""
LIST=false
PRUNE=false
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse flags up to '--'
while [ $# -gt 0 ]; do
  case "$1" in
    --vm)        VM_NAME="$2"; shift 2 ;;
    --restore)   RESTORE_TS="$2"; shift 2 ;;
    --list)      LIST=true; shift ;;
    --prune-old) PRUNE=true; shift ;;
    --help|-h)
      sed -n '3,30p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    --) shift; break ;;
    -*) echo "Unknown flag: $1"; exit 1 ;;
    *)  break ;;
  esac
done

# ── Subcommands that don't run anything ───────────────────────────────

cmd_list() {
  if [ -n "$VM_NAME" ]; then
    "$SCRIPT_DIR/snapshot.sh" list
  else
    if git rev-parse --git-dir >/dev/null 2>&1; then
      git for-each-ref --format='  %(refname:short)  %(objectname:short)  %(subject)' refs/safe-do/ 2>/dev/null
      [ -z "$(git for-each-ref refs/safe-do/ 2>/dev/null)" ] && echo "  (no retained safe-do checkpoints)"
    else
      echo "  Not in a git repo and no --vm specified"
      exit 1
    fi
  fi
}

cmd_restore() {
  local ts="$1"
  if [ -n "$VM_NAME" ]; then
    "$SCRIPT_DIR/snapshot.sh" restore "$ts"
    return
  fi
  git rev-parse "refs/safe-do/$ts" >/dev/null 2>&1 || { echo "  No checkpoint refs/safe-do/$ts"; exit 1; }
  echo "  Applying refs/safe-do/$ts on top of working tree..."
  if git stash apply "refs/safe-do/$ts"; then
    echo "  ✓ Restored. The ref is preserved — delete with: git update-ref -d refs/safe-do/$ts"
  else
    echo ""
    echo "  ✗ Apply blocked by current changes (git protected your work)."
    echo "    Either commit/stash current changes and retry, or inspect with:"
    echo "      git diff refs/safe-do/$ts"
    echo "    To force-replace working tree (destroys current changes):"
    echo "      git checkout refs/safe-do/$ts -- ."
    return 1
  fi
}

cmd_prune() {
  local now cutoff
  now=$(date +%s)
  cutoff=$((now - 7*86400))
  if [ -n "$VM_NAME" ]; then
    echo "  Prune for VM snapshots: list with snapshot.sh list, delete with snapshot.sh delete <name>"
    return
  fi
  git rev-parse --git-dir >/dev/null 2>&1 || { echo "  Not a git repo"; exit 1; }
  local pruned=0
  git for-each-ref --format='%(refname)' refs/safe-do/ 2>/dev/null | while read -r ref; do
    local ts=${ref##*/}
    # ts format: YYYYMMDD-HHMMSS — convert via date
    local ref_epoch
    ref_epoch=$(date -j -f "%Y%m%d-%H%M%S" "$ts" "+%s" 2>/dev/null) || continue
    if [ "$ref_epoch" -lt "$cutoff" ]; then
      git update-ref -d "$ref"
      echo "  pruned $ref ($(date -r "$ref_epoch" '+%Y-%m-%d %H:%M'))"
      pruned=$((pruned+1))
    fi
  done
  echo "  ✓ Prune done"
}

[ "$LIST" = "true" ] && { cmd_list; exit 0; }
[ -n "$RESTORE_TS" ] && { cmd_restore "$RESTORE_TS"; exit 0; }
[ "$PRUNE" = "true" ] && { cmd_prune; exit 0; }

[ $# -eq 0 ] && { echo "Usage: $0 [--vm NAME] -- <command...>"; echo "Run with --help for more."; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"
KEEP="${SAFE_DO_KEEP:-0}"

# ── Snapshot ──────────────────────────────────────────────────────────

if [ -n "$VM_NAME" ]; then
  command -v limactl >/dev/null 2>&1 || { echo "limactl not found. brew install lima"; exit 1; }
  echo "  [safe-do] Snapshotting VM '${VM_NAME}' as 'safe-do-${TS}'..."
  "$SCRIPT_DIR/snapshot.sh" save "safe-do-${TS}" || { echo "  Snapshot failed"; exit 1; }
  echo ""
  echo "  Running: limactl shell ${VM_NAME} -- $*"
  echo "  ──────────────────────────────────────────────────────"
  limactl shell "${VM_NAME}" -- "$@"
  EXIT=$?
  echo "  ──────────────────────────────────────────────────────"
  if [ $EXIT -eq 0 ] && [ "$KEEP" != "1" ]; then
    "$SCRIPT_DIR/snapshot.sh" delete "safe-do-${TS}"
    echo "  ✓ Command succeeded. Snapshot 'safe-do-${TS}' deleted."
  else
    echo ""
    echo "  ⚠  exit=${EXIT}. Snapshot 'safe-do-${TS}' RETAINED."
    echo "     Restore: ${SCRIPT_DIR}/safe-do.sh --vm ${VM_NAME} --restore safe-do-${TS}"
    echo "     Or:      ${SCRIPT_DIR}/snapshot.sh restore safe-do-${TS}"
  fi
  exit $EXIT
fi

# Local mode — git-backed checkpoint
git rev-parse --git-dir >/dev/null 2>&1 || { echo "  Not in a git repo and no --vm specified"; exit 1; }

REF="refs/safe-do/${TS}"
echo "  [safe-do] Snapshotting working tree as ${REF}..."

# git stash create captures tracked + staged + untracked changes into a
# stash *object* without storing it on the stash list. We pin it under
# refs/safe-do/<ts> so it survives GC and doesn't pollute `git stash list`.
# If there are no changes at all, stash create returns empty; in that case
# capture HEAD itself so --restore still works.
STASH_SHA=$(git stash create "safe-do checkpoint @ ${TS}" 2>/dev/null || true)
if [ -z "$STASH_SHA" ]; then
  STASH_SHA=$(git rev-parse HEAD)
  echo "  (working tree clean — checkpoint = HEAD)"
fi
git update-ref "$REF" "$STASH_SHA"

echo ""
echo "  Running: $*"
echo "  ──────────────────────────────────────────────────────"
"$@"
EXIT=$?
echo "  ──────────────────────────────────────────────────────"

if [ $EXIT -eq 0 ] && [ "$KEEP" != "1" ]; then
  git update-ref -d "$REF"
  echo "  ✓ Command succeeded. Checkpoint ${REF} deleted."
else
  echo ""
  echo "  ⚠  exit=${EXIT}. Checkpoint ${REF} RETAINED."
  echo "     Restore: ${SCRIPT_DIR}/safe-do.sh --restore ${TS}"
  echo "     Or raw:  git stash apply ${REF}"
  echo "     Inspect: git diff ${REF}"
fi
exit $EXIT
