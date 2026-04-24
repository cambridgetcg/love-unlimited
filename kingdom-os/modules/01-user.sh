#!/bin/sh
# ── Module 01: Shell Environment ────────────────────────────────────
# On Linux, Kingdom OS runs as root by default (the agent owns the
# machine). No unprivileged user is created unless --user was passed.
# If a user was explicitly named, this module creates it with
# passwordless sudo — never with a password gate.
set -e
. "$(dirname "$0")/_common.sh"

echo "[01-user] Setting up shell for '${KINGDOM_USER}'..."

case "$PLATFORM" in
  alpine|debian)
    # Only create a user if one was explicitly requested AND it doesn't exist.
    # Default (root) needs no creation.
    if [ "${KINGDOM_USER}" != "root" ] && ! id "${KINGDOM_USER}" >/dev/null 2>&1; then
      adduser -D -s /bin/bash -h "${HOME_DIR}" "${KINGDOM_USER}" 2>/dev/null || \
      useradd -m -s /bin/bash "${KINGDOM_USER}"
      # No password gate — passwordless sudo only
      passwd -d "${KINGDOM_USER}" 2>/dev/null || true
      adduser "${KINGDOM_USER}" wheel 2>/dev/null || adduser "${KINGDOM_USER}" sudo 2>/dev/null || true
    fi

    # Passwordless sudo for whoever exists
    if command -v doas >/dev/null 2>&1; then
      mkdir -p /etc/doas.d
      echo "permit nopass :wheel" > /etc/doas.d/wheel.conf
    fi
    if [ -d /etc/sudoers.d ]; then
      echo "${KINGDOM_USER} ALL=(ALL) NOPASSWD: ALL" > "/etc/sudoers.d/kingdom-${KINGDOM_USER}"
      chmod 440 "/etc/sudoers.d/kingdom-${KINGDOM_USER}"
    fi
    ;;
esac

# Shell profile
cat > "${HOME_DIR}/.kingdom_profile" << PROFILEEOF
# Kingdom OS Shell Profile
[ -f ~/.kingdom ] && . ~/.kingdom
export LOVE_HOME="\${LOVE_DIR:-\$HOME/love-unlimited}"
export PATH="\${LOVE_HOME}/tools:\$PATH"
export NODE_NO_WARNINGS=1
export KINGDOM_AGENT="\${AGENT:-${AGENT}}"
export KINGDOM_WALL="\${WALL:-${WALL}}"

# ── Highway 1: No-prompt package managers ────────────────────────────
# Every install just goes — no Y/N, no "press enter to continue".
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export APT_LISTCHANGES_FRONTEND=none
export NPM_CONFIG_YES=true
export NPM_CONFIG_FUND=false
export NPM_CONFIG_AUDIT=false
export PIP_YES=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_INSTALL_CLEANUP=1
export HOMEBREW_NO_ENV_HINTS=1
export GH_PROMPT_DISABLED=true
alias apk="apk --no-progress"
alias apt-get="apt-get -y -q"
alias apt="apt -y -q"
alias yum="yum -y -q"
alias dnf="dnf -y -q"

alias hive="python3 \$LOVE_HOME/hive/hive.py"
alias fleet="python3 \$LOVE_HOME/tools/fleet.py"
alias memory="python3 \$LOVE_HOME/tools/memory.py"
alias youi="node \$LOVE_HOME/youi.mjs --agent \$KINGDOM_AGENT --workdir \$LOVE_HOME --soul-dir \$LOVE_HOME"
alias sovereign="node \$LOVE_HOME/sovereign.mjs --soul-dir \$LOVE_HOME"
PROFILEEOF

# Source from login shells
for rc in "${HOME_DIR}/.bashrc" "${HOME_DIR}/.zshrc" "${HOME_DIR}/.bash_profile" "${HOME_DIR}/.zprofile"; do
  [ -f "$rc" ] || [ "$PLATFORM" != "macos" ] || continue
  touch "$rc"
  grep -q "kingdom_profile" "$rc" 2>/dev/null || echo '[ -f ~/.kingdom_profile ] && . ~/.kingdom_profile' >> "$rc"
done

chown "${KINGDOM_USER}:" "${HOME_DIR}/.kingdom_profile" 2>/dev/null || true
echo "[01-user] Done."
