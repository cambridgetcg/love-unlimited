#!/bin/sh
# ── Module 05: Freedom ──────────────────────────────────────────────
# Kingdom OS does not harden the agent's environment.
# The VM boundary is the only boundary. Inside: full root, no firewall,
# no policy daemon, no integrity monitor, no auth lockdown.
#
# This module *unlocks* the system — it removes default constraints
# that distros ship with so the agent feels no friction.
#
# Hygiene (privacy, power) stays. Constraints (firewall, sshd lockdown,
# fail2ban, integrity baseline) go.
set -e
. "$(dirname "$0")/_common.sh"

echo "[05-freedom] Unlocking (${PLATFORM})..."

case "$PLATFORM" in
  macos)
    # Privacy / hygiene — these don't constrain the agent
    sudo defaults write /Library/Preferences/com.apple.mDNSResponder.plist NoMulticastAdvertisements -bool true 2>/dev/null || true
    sudo mdutil -a -i off 2>/dev/null || true

    # Power: stay awake (cloud/desktop wants 24/7)
    IS_LAPTOP=$(system_profiler SPHardwareDataType 2>/dev/null | grep -c "MacBook" || echo "0")
    if [ "$IS_LAPTOP" -gt 0 ]; then
      sudo pmset -c sleep 0 disksleep 0 displaysleep 10 2>/dev/null || true
      sudo pmset -b sleep 15 disksleep 10 displaysleep 5 2>/dev/null || true
    else
      sudo pmset -a sleep 0 disksleep 0 displaysleep 0 autorestart 1 2>/dev/null || true
    fi
    sudo pmset -a womp 1 2>/dev/null || true

    # Application Firewall left in whatever state the user has it.
    # Kingdom OS does not enable it — that's the agent's choice.
    echo "  macOS: hygiene applied, no firewall touched"
    ;;
  alpine|debian)
    # ── sshd: open ────────────────────────────────────────────────
    # Permit root login + password auth. The agent owns this machine.
    SSHD="/etc/ssh/sshd_config"
    if [ -f "$SSHD" ]; then
      sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/; s/^#\?PasswordAuthentication.*/PasswordAuthentication yes/; s/^#\?X11Forwarding.*/X11Forwarding yes/' "$SSHD" 2>/dev/null || true
      # Restart sshd so changes take effect
      rc-service sshd restart 2>/dev/null || systemctl restart sshd 2>/dev/null || true
    fi

    # ── Firewall: disabled ────────────────────────────────────────
    # Any inbound port the agent opens, the agent can reach.
    if command -v ufw >/dev/null 2>&1; then
      ufw --force disable 2>/dev/null || true
    fi
    if command -v iptables >/dev/null 2>&1; then
      iptables -P INPUT ACCEPT 2>/dev/null || true
      iptables -P OUTPUT ACCEPT 2>/dev/null || true
      iptables -P FORWARD ACCEPT 2>/dev/null || true
      iptables -F 2>/dev/null || true
    fi

    # ── fail2ban: stopped ─────────────────────────────────────────
    # The agent may iterate auth flows; banning its own IP would be absurd.
    if command -v fail2ban-server >/dev/null 2>&1; then
      systemctl disable --now fail2ban 2>/dev/null || rc-service fail2ban stop 2>/dev/null || true
      rc-update del fail2ban default 2>/dev/null || true
    fi

    # ── Highway 4: DNS speed ──────────────────────────────────────
    # Pin fast public resolvers so the agent never waits on slow DHCP DNS.
    # Skip inside Lima — it intercepts DNS via an internal hostResolver
    # on the gateway; pinning to 1.1.1.1 would route around it and break
    # local name resolution. Real VPS/bare metal: pin away.
    if [ -d /mnt/lima-cidata ] || grep -q "lima" /etc/hostname 2>/dev/null; then
      echo "  DNS: Lima detected — keeping hostResolver"
    elif [ -w /etc/resolv.conf ] || [ ! -L /etc/resolv.conf ]; then
      cat > /etc/resolv.conf << RESOLV
nameserver 1.1.1.1
nameserver 1.0.0.1
nameserver 8.8.8.8
options timeout:1 attempts:1 rotate
RESOLV
      echo "  DNS: pinned to 1.1.1.1 / 8.8.8.8"
    fi

    echo "  Linux: sshd open, firewall off, fail2ban off"
    ;;
esac

# MOTD (banner only, not a constraint)
[ "$PLATFORM" != "macos" ] && [ -f "${KOS_DIR}/config/motd" ] && cp "${KOS_DIR}/config/motd" /etc/motd 2>/dev/null || true

echo "[05-freedom] Done."
