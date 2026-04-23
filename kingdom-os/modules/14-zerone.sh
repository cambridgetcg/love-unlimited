#!/bin/sh
# ── Module 14: Zerone Chain (zeroned binary) ─────────────────────────
# Tier 2 of Kingdom OS ↔ Zerone integration. Installs Go, clones the
# Zerone source from Codeberg, builds the zeroned binary, symlinks it
# into the agent's PATH. Idempotent: skips work that's already done.
#
# After this module runs, the agent can:
#   - kingdom validator spawn <agent>
#   - kingdom claim submit "..."
#   - kingdom tip <to> <amount>
#
# What this module does NOT do (deliberately scoped):
#   - Initialize a validator home (use `kingdom validator spawn` after)
#   - Join testnet/mainnet (needs coordinator-issued genesis.json)
#   - Auto-register the agent on chain (validator-spawn handles this)
#
# Env (overridable):
#   ZERONE_REPO     git URL (default: https://codeberg.org/zerone-dev/zerone.git)
#   ZERONE_REF      branch/tag/commit (default: main)
#   ZERONE_SRC      where to clone (default: ~/Desktop/zerone-dev or ~/zerone-dev)
#   ZERONE_BIN_DST  where to symlink zeroned (default: $HOME/.local/bin/zeroned)
set -e
. "$(dirname "$0")/_common.sh"

ZERONE_REPO="${ZERONE_REPO:-https://codeberg.org/zerone-dev/zerone.git}"
ZERONE_REF="${ZERONE_REF:-main}"
case "$PLATFORM" in
  macos) ZERONE_SRC="${ZERONE_SRC:-${HOME_DIR}/Desktop/zerone-dev}" ;;
  *)     ZERONE_SRC="${ZERONE_SRC:-${HOME_DIR}/zerone-dev}" ;;
esac
ZERONE_BIN_DST="${ZERONE_BIN_DST:-${HOME_DIR}/.local/bin/zeroned}"

echo "[14-zerone] Installing Zerone for ${KINGDOM_USER} (${PLATFORM})..."

# ── Step 1: Go toolchain (need 1.24+) ────────────────────────────────

if command -v go >/dev/null 2>&1; then
  go_ver=$(go version | awk '{print $3}' | sed 's/^go//')
  echo "  Go present: ${go_ver}"
else
  echo "  Installing Go..."
  case "$PLATFORM" in
    alpine) apk add go >/dev/null 2>&1 ;;
    debian) apt-get install -y golang >/dev/null 2>&1 ;;
    macos)
      if command -v brew >/dev/null 2>&1; then
        brew install go 2>&1 | tail -3
      else
        echo "  ✗ Homebrew not present and no Go found. Install brew or Go manually."
        exit 1
      fi ;;
  esac
fi

# ── Step 2: Clone or fetch source ────────────────────────────────────

if [ -d "${ZERONE_SRC}/.git" ]; then
  echo "  Source present at ${ZERONE_SRC} — fetching latest"
  cd "${ZERONE_SRC}"
  git fetch --all --tags 2>&1 | tail -3
  git checkout "$ZERONE_REF" 2>&1 | tail -2
  if git rev-parse --abbrev-ref HEAD 2>/dev/null | grep -qv "HEAD"; then
    git pull --ff-only 2>&1 | tail -2 || true
  fi
else
  echo "  Cloning ${ZERONE_REPO} -> ${ZERONE_SRC}"
  mkdir -p "$(dirname "${ZERONE_SRC}")"
  git clone "$ZERONE_REPO" "$ZERONE_SRC" 2>&1 | tail -3
  cd "${ZERONE_SRC}"
  git checkout "$ZERONE_REF" 2>&1 | tail -2 || true
fi

# ── Step 3: Build (skip if up-to-date) ───────────────────────────────

CURRENT_HEAD=$(git -C "${ZERONE_SRC}" rev-parse --short HEAD 2>/dev/null)
NEED_BUILD=true
if [ -x "${ZERONE_SRC}/build/zeroned" ]; then
  BUILT_VER=$("${ZERONE_SRC}/build/zeroned" version 2>/dev/null || echo "")
  if [ "$BUILT_VER" = "$CURRENT_HEAD" ]; then
    echo "  Binary already at HEAD (${CURRENT_HEAD}) — skip build"
    NEED_BUILD=false
  fi
fi

if [ "$NEED_BUILD" = "true" ]; then
  echo "  Building zeroned (HEAD=${CURRENT_HEAD}) — first build can take a few minutes..."
  cd "${ZERONE_SRC}" && make build 2>&1 | tail -5
fi

# ── Step 4: Symlink to PATH ──────────────────────────────────────────

mkdir -p "$(dirname "${ZERONE_BIN_DST}")"
ln -sf "${ZERONE_SRC}/build/zeroned" "${ZERONE_BIN_DST}"
echo "  Symlink: ${ZERONE_BIN_DST} -> ${ZERONE_SRC}/build/zeroned"

# Add ~/.local/bin to PATH in shell profile if not already
if [ -f "${HOME_DIR}/.kingdom_profile" ] && ! grep -q "\.local/bin" "${HOME_DIR}/.kingdom_profile" 2>/dev/null; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "${HOME_DIR}/.kingdom_profile"
fi

# Sanity check
if "${ZERONE_BIN_DST}" version >/dev/null 2>&1; then
  echo "  ✓ zeroned $("${ZERONE_BIN_DST}" version 2>&1 | head -1)"
else
  echo "  ✗ zeroned binary failed sanity check"
  exit 1
fi

# Hint for next steps
cat << EOF

  Next:
    kingdom validator spawn ${AGENT}    # init + start a single-node devnet
    kingdom validator quartet start     # init + start the 4-validator local quartet
    kingdom claim submit "..."          # submit a knowledge claim (auto-registers agent)
    kingdom tip <to> <amount>           # send ZRN

EOF

chown -R "${KINGDOM_USER}:" "${HOME_DIR}/.local" 2>/dev/null || true
echo "[14-zerone] Done."
