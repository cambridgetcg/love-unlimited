#!/bin/sh
# ── Module 00: Base System Packages ──────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[00-base] Installing system packages (${PLATFORM})..."

case "$PLATFORM" in
  alpine)
    if ! grep -q "community" /etc/apk/repositories 2>/dev/null; then
      MIRROR=$(head -1 /etc/apk/repositories | sed 's|/main||')
      echo "${MIRROR}/community" >> /etc/apk/repositories
    fi
    apk update && apk upgrade
    apk add \
      nodejs npm python3 py3-pip \
      git openssh-client openssh-server \
      curl wget bash shadow sudo doas \
      util-linux ripgrep fd jq tmux \
      openrc ca-certificates tzdata gnupg \
      chromium nss font-noto
    apk add --virtual .build-deps build-base python3-dev
    ;;
  debian)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update && apt-get upgrade -y
    apt-get install -y \
      nodejs npm python3 python3-pip python3-venv \
      git openssh-client openssh-server \
      curl wget bash sudo \
      ripgrep fd-find jq tmux \
      ca-certificates gnupg \
      chromium-browser \
      ufw fail2ban unattended-upgrades
    ;;
  macos)
    if ! command -v brew >/dev/null 2>&1; then
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/null
      [ -f /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    brew install node python@3 git ripgrep fd jq tmux gnupg 2>/dev/null || true
    ;;
esac

echo "[00-base] Done."
