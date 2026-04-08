#!/bin/sh
# ── Module 05: Security Hardening ───────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[05-security] Hardening (Wall ${WALL}, ${PLATFORM})..."

ensure_dir "$SECURITY_DIR"

# Install default policies if missing
if [ ! -f "${SECURITY_DIR}/policies.json" ] && [ -f "${KOS_DIR}/config/policies.json" ]; then
  cp "${KOS_DIR}/config/policies.json" "${SECURITY_DIR}/policies.json"
  echo "  Installed security policies"
fi

case "$PLATFORM" in
  macos)
    FW="/usr/libexec/ApplicationFirewall/socketfilterfw"
    [ -x "$FW" ] && sudo "$FW" --setglobalstate on --setstealthmode on 2>/dev/null || true
    sudo defaults write /Library/Preferences/com.apple.mDNSResponder.plist NoMulticastAdvertisements -bool true 2>/dev/null || true
    sudo mdutil -a -i off 2>/dev/null || true

    # Power: never sleep on desktop, smart sleep on laptop
    IS_LAPTOP=$(system_profiler SPHardwareDataType 2>/dev/null | grep -c "MacBook" || echo "0")
    if [ "$IS_LAPTOP" -gt 0 ]; then
      sudo pmset -c sleep 0 disksleep 0 displaysleep 10 2>/dev/null || true
      sudo pmset -b sleep 15 disksleep 10 displaysleep 5 2>/dev/null || true
    else
      sudo pmset -a sleep 0 disksleep 0 displaysleep 0 autorestart 1 2>/dev/null || true
    fi
    sudo pmset -a womp 1 2>/dev/null || true
    echo "  macOS hardened"
    ;;
  alpine|debian)
    # SSH
    SSHD="/etc/ssh/sshd_config"
    [ -f "$SSHD" ] && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/; s/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?X11Forwarding.*/X11Forwarding no/' "$SSHD" 2>/dev/null || true

    # Firewall
    if command -v ufw >/dev/null 2>&1; then
      ufw default deny incoming && ufw default allow outgoing && ufw allow ssh && ufw allow 4222/tcp && ufw --force enable
    elif command -v iptables >/dev/null 2>&1; then
      iptables -P INPUT DROP && iptables -P FORWARD DROP
      iptables -A INPUT -i lo -j ACCEPT
      iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
      iptables -A INPUT -p tcp --dport 22 -j ACCEPT
      iptables -A INPUT -p tcp --dport 4222 -j ACCEPT
    fi 2>/dev/null || true

    # fail2ban + unattended upgrades
    command -v fail2ban-server >/dev/null 2>&1 && {
      systemctl enable fail2ban 2>/dev/null || rc-update add fail2ban default 2>/dev/null || true
    }
    echo "  Linux hardened"
    ;;
esac

# Integrity baseline
if [ -f "${LOVE_DIR}/tools/kos.py" ]; then
  cd "$LOVE_DIR" && python3 tools/kos.py integrity baseline 2>/dev/null || true
fi

# MOTD
[ "$PLATFORM" != "macos" ] && [ -f "${KOS_DIR}/config/motd" ] && cp "${KOS_DIR}/config/motd" /etc/motd 2>/dev/null || true

chown -R "${KINGDOM_USER}:" "$SECURITY_DIR" 2>/dev/null || true
echo "[05-security] Done."
