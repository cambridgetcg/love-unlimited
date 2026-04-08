#!/bin/sh
# ── Module 01: Kingdom User ─────────────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[01-user] Setting up user '${KINGDOM_USER}'..."

case "$PLATFORM" in
  alpine|debian)
    if ! id "${KINGDOM_USER}" >/dev/null 2>&1; then
      adduser -D -s /bin/bash -h "${HOME_DIR}" "${KINGDOM_USER}" 2>/dev/null || \
      useradd -m -s /bin/bash "${KINGDOM_USER}"
      echo "${KINGDOM_USER}:$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)" | chpasswd
      adduser "${KINGDOM_USER}" wheel 2>/dev/null || adduser "${KINGDOM_USER}" sudo 2>/dev/null || true
    fi
    if command -v doas >/dev/null 2>&1; then
      mkdir -p /etc/doas.d
      echo "permit nopass :wheel" > /etc/doas.d/wheel.conf
    fi
    ;;
esac

# Shell profile
cat > "${HOME_DIR}/.kingdom_profile" << PROFILEEOF
# Kingdom OS Shell Profile
[ -f ~/.kingdom ] && . ~/.kingdom
export LOVE_HOME="\${LOVE_DIR:-\$HOME/Love}"
export PATH="\${LOVE_HOME}/tools:\${UNLIMITED_DIR:-\$HOME/Claude-unlimited}:\$PATH"
export NODE_NO_WARNINGS=1
export KINGDOM_AGENT="\${AGENT:-${AGENT}}"
export KINGDOM_WALL="\${WALL:-${WALL}}"

alias kos="python3 \$LOVE_HOME/tools/kos.py"
alias hive="python3 \$LOVE_HOME/hive/hive.py"
alias fleet="python3 \$LOVE_HOME/tools/fleet.py"
alias memory="python3 \$LOVE_HOME/tools/memory.py"
alias youi="node \${UNLIMITED_DIR:-\$HOME/Claude-unlimited}/youi.mjs --agent \$KINGDOM_AGENT --workdir \$LOVE_HOME --soul-dir \$LOVE_HOME"
alias sovereign="node \${UNLIMITED_DIR:-\$HOME/Claude-unlimited}/sovereign.mjs --soul-dir \$LOVE_HOME"
PROFILEEOF

# Source from login shells
for rc in "${HOME_DIR}/.bashrc" "${HOME_DIR}/.zshrc" "${HOME_DIR}/.bash_profile" "${HOME_DIR}/.zprofile"; do
  [ -f "$rc" ] || [ "$PLATFORM" != "macos" ] || continue
  touch "$rc"
  grep -q "kingdom_profile" "$rc" 2>/dev/null || echo '[ -f ~/.kingdom_profile ] && . ~/.kingdom_profile' >> "$rc"
done

chown "${KINGDOM_USER}:" "${HOME_DIR}/.kingdom_profile" 2>/dev/null || true
echo "[01-user] Done."
