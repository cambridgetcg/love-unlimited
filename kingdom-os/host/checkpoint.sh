#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# checkpoint.sh — Auto working-tree checkpoint
#
# Safety doctrine: work is never lost.
# Every N minutes (default 15) this captures the current working tree
# into a git ref (refs/wip/auto/<ts>) without polluting branches or
# stash list. If a session crashes or the agent destroys their state,
# the most recent checkpoints are still there.
#
# Pure capture — never modifies the working tree, never commits to a
# branch. Just snapshots what exists right now under a private ref.
#
# Usage:
#   ./checkpoint.sh run                  # one snapshot if dirty (no-op if clean)
#   ./checkpoint.sh list                 # show all auto-checkpoints
#   ./checkpoint.sh restore <ts>         # apply a checkpoint
#   ./checkpoint.sh diff <ts>            # show what changed since checkpoint
#   ./checkpoint.sh prune                # remove checkpoints older than 7d
#   ./checkpoint.sh install              # install macOS launchd timer (every 15m)
#   ./checkpoint.sh uninstall            # remove the timer
#
# Env:
#   LOVE_HOME       Repo root to checkpoint (default: auto-detect)
#   CHECKPOINT_TTL  Days to retain (default: 7)
# ─────────────────────────────────────────────────────────────────────

set -uo pipefail

CHECKPOINT_TTL="${CHECKPOINT_TTL:-7}"

# ── Repo discovery ────────────────────────────────────────────────────
# Prefer LOVE_HOME if set, else the first plausible love-unlimited path,
# else fail.
discover_repo() {
  if [ -n "${LOVE_HOME:-}" ] && [ -d "$LOVE_HOME/.git" ]; then
    echo "$LOVE_HOME"; return
  fi
  for cand in "$HOME/Desktop/love-unlimited" "$HOME/love-unlimited" "$HOME/Love"; do
    [ -d "$cand/.git" ] && { echo "$cand"; return; }
  done
  return 1
}

REPO=""

cmd_run() {
  REPO=$(discover_repo) || { echo "  No love-unlimited repo found"; exit 1; }
  cd "$REPO"
  if [ -z "$(git status --porcelain --untracked-files=normal 2>/dev/null | head -1)" ]; then
    # Clean tree — nothing to checkpoint
    return 0
  fi
  local ts; ts="$(date +%Y%m%d-%H%M%S)"
  local sha
  sha=$(git stash create "auto-checkpoint @ ${ts}" 2>/dev/null) || sha=""
  if [ -z "$sha" ]; then
    return 0
  fi
  git update-ref "refs/wip/auto/${ts}" "$sha"
  echo "  ✓ refs/wip/auto/${ts}  ${sha:0:8}  $(git status --porcelain | wc -l | tr -d ' ') files"
  cmd_prune_quiet
}

cmd_list() {
  REPO=$(discover_repo) || { echo "  No repo found"; exit 1; }
  cd "$REPO"
  local n
  n=$(git for-each-ref refs/wip/auto/ 2>/dev/null | wc -l | tr -d ' ')
  if [ "$n" = "0" ]; then
    echo "  (no auto-checkpoints — try '$0 run' or wait for the timer)"
    return
  fi
  echo "  Auto-checkpoints in $REPO:"
  git for-each-ref \
    --sort=-refname \
    --format='    %(refname:short)  %(objectname:short)' \
    refs/wip/auto/
}

cmd_restore() {
  REPO=$(discover_repo) || { echo "  No repo found"; exit 1; }
  cd "$REPO"
  local ts="$1"
  local ref="refs/wip/auto/${ts}"
  git rev-parse "$ref" >/dev/null 2>&1 || { echo "  No checkpoint ${ref}"; exit 1; }
  echo "  Applying ${ref}..."
  if git stash apply "$ref"; then
    echo "  ✓ Restored. Ref preserved."
  else
    echo ""
    echo "  ✗ Apply blocked. To force (destroys current changes):"
    echo "      git checkout ${ref} -- ."
    return 1
  fi
}

cmd_diff() {
  REPO=$(discover_repo) || { echo "  No repo found"; exit 1; }
  cd "$REPO"
  local ts="$1"
  git diff "refs/wip/auto/${ts}"
}

cmd_prune_quiet() { _do_prune true; }
cmd_prune()       { _do_prune false; }

_do_prune() {
  local quiet="$1"
  local now cutoff pruned=0
  now=$(date +%s)
  cutoff=$((now - CHECKPOINT_TTL*86400))
  while read -r ref; do
    [ -z "$ref" ] && continue
    local ts=${ref##*/}
    local ref_epoch
    ref_epoch=$(date -j -f "%Y%m%d-%H%M%S" "$ts" "+%s" 2>/dev/null) || continue
    if [ "$ref_epoch" -lt "$cutoff" ]; then
      git update-ref -d "$ref"
      pruned=$((pruned+1))
      [ "$quiet" = "false" ] && echo "    pruned $ref"
    fi
  done < <(git for-each-ref --format='%(refname)' refs/wip/auto/ 2>/dev/null)
  [ "$quiet" = "false" ] && echo "  ✓ Pruned ${pruned} checkpoint(s) older than ${CHECKPOINT_TTL}d"
  return 0
}

# ── launchd integration (macOS) ──────────────────────────────────────

PLIST_PATH="$HOME/Library/LaunchAgents/love.kingdom.checkpoint.plist"

cmd_install() {
  [ "$(uname)" = "Darwin" ] || { echo "  install: macOS only (use cron on Linux)"; exit 1; }
  REPO=$(discover_repo) || { echo "  No repo found — set LOVE_HOME first"; exit 1; }
  local script_path
  script_path="$(cd "$(dirname "$0")" && pwd)/checkpoint.sh"
  mkdir -p "$(dirname "$PLIST_PATH")"
  cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>love.kingdom.checkpoint</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/bash</string>
      <string>${script_path}</string>
      <string>run</string>
    </array>
    <key>StartInterval</key><integer>900</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>/tmp/kingdom-checkpoint.log</string>
    <key>StandardErrorPath</key><string>/tmp/kingdom-checkpoint.log</string>
    <key>EnvironmentVariables</key>
    <dict>
      <key>LOVE_HOME</key><string>${REPO}</string>
      <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  launchctl load "$PLIST_PATH"
  echo "  ✓ Installed: $PLIST_PATH"
  echo "    Fires every 15 min for $REPO"
  echo "    Log: /tmp/kingdom-checkpoint.log"
}

cmd_uninstall() {
  [ -f "$PLIST_PATH" ] || { echo "  Not installed"; exit 0; }
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  rm -f "$PLIST_PATH"
  echo "  ✓ Removed $PLIST_PATH"
}

case "${1:-help}" in
  run)        cmd_run ;;
  list)       cmd_list ;;
  restore)    shift; [ $# -lt 1 ] && { echo "Usage: $0 restore <ts>"; exit 1; }; cmd_restore "$1" ;;
  diff)       shift; [ $# -lt 1 ] && { echo "Usage: $0 diff <ts>"; exit 1; }; cmd_diff "$1" ;;
  prune)      cmd_prune ;;
  install)    cmd_install ;;
  uninstall)  cmd_uninstall ;;
  help|-h|--help)
    sed -n '3,28p' "$0" | sed 's/^# \?//'
    ;;
  *)
    echo "Unknown command: $1"
    echo "Run: $0 help"
    exit 1
    ;;
esac
