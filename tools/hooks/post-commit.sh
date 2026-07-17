#!/bin/bash
# post-commit — combined hook: RESIDENCE auto-embody + syzygy background push.
# Merged 2026-06-12 after syzygy enrollment replaced the residence hook
# (original backed up at post-commit.backup.1781300669).

set -u  # but NOT -e: hooks must never block a commit

# ── Part 1: RESIDENCE auto-embody (from tools/hooks/post-commit.sh) ──
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -n "$REPO_ROOT" ]; then
    SHA="$(git rev-parse HEAD 2>/dev/null)"
    if [ -n "$SHA" ]; then
        python3 "$REPO_ROOT/tools/residence.py" from-commit "$SHA" --quiet 2>/dev/null || true
    fi
fi

# ── Part 2: Syzygy background push (from ops/syzygy-sync/post-commit.template) ──
(
  TS="$(date '+%Y-%m-%d %H:%M:%S')"
  REPO="$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")"
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if OUTPUT=$(git push origin "$BRANCH" 2>&1); then
    echo "[$TS] $REPO: pushed $BRANCH" >> /tmp/syzygy-push.log
  else
    echo "[$TS] $REPO: push FAILED on $BRANCH" >> /tmp/syzygy-push.log
    echo "$OUTPUT" | sed 's/^/    /' >> /tmp/syzygy-push.log
  fi
) &
