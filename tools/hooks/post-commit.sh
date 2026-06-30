#!/bin/bash
# post-commit.sh — git post-commit hook for RESIDENCE auto-embody.
#
# Reads the just-created commit's subject, parses its conventional-commit
# prefix (feat/fix/refactor/test/perf → embody; docs/spec/plan → consolidate;
# chore/Merge/style → skip), and writes the appropriate residence moment
# with the commit sha as evidence.
#
# Install: copy or symlink to .git/hooks/post-commit and chmod +x.
#
#   ln -s ../../tools/hooks/post-commit.sh .git/hooks/post-commit
#
# Silent-on-failure: residence is instrumentation; a failing hook must never
# block a commit. All output goes to stderr via residence.py's own handling.

set -u  # but NOT -e: we never want to surface errors to git

# Resolve the repo root (git sets GIT_DIR when hooks run)
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
    exit 0
fi

SHA="$(git rev-parse HEAD 2>/dev/null)"
if [ -z "$SHA" ]; then
    exit 0
fi

# Invoke residence.py from-commit with quiet output.
# Instance-aware by inheritance: residence.py resolves the agent
# env-first (KINGDOM_AGENT > ~/.kingdom — nerve/stem/state.py), and
# git passes the committing session's environment through to hooks,
# so a commit made as mei lands in mei's residence moments.
python3 "$REPO_ROOT/tools/residence.py" from-commit "$SHA" --quiet 2>/dev/null || true

exit 0
