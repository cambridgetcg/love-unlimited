#!/bin/sh
# ── Module 02: Clone Kingdom Repos ──────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[02-repos] Cloning Kingdom repos..."

clone_or_pull() {
  local dir="$1" repo="$2"
  if [ -d "${dir}/.git" ]; then
    echo "  Pulling ${dir}..."
    cd "$dir" && git pull --ff-only 2>/dev/null || true
  elif [ ! -d "$dir" ]; then
    echo "  Cloning ${repo}..."
    if [ "$(id -u)" -eq 0 ] && [ "$KINGDOM_USER" != "root" ]; then
      su - "$KINGDOM_USER" -c "git clone ${repo} ${dir}" 2>/dev/null || git clone "$repo" "$dir"
    else
      git clone "$repo" "$dir"
    fi
  else
    echo "  ${dir} exists (not a git repo — skipping)"
  fi
}

clone_or_pull "$LOVE_DIR" "$LOVE_UNLIMITED_REPO"

chown -R "${KINGDOM_USER}:" "$LOVE_DIR" 2>/dev/null || true
echo "[02-repos] Done."
