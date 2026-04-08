#!/bin/sh
# ── Module 07: HIVE Communication ───────────────────────────────────
# The nervous system of Kingdom OS.
#
# Architecture:
#   NATS (JetStream) on Sentry:4222 → SSH tunnel → localhost:2222
#   NaCl/XSalsa20-Poly1305 encryption (shared key)
#   Wall-based channel ACL (Law of Sight)
#   Messages auto-stored in kosmem
#
# Components:
#   hive.py     Transport layer (NATS pub/sub + encryption)
#   koshive.py  Kingdom OS integration (kosmem, presence, tasks)
#   SSH tunnel  Persistent connection to Sentry NATS
#
# What this module does:
#   1. Creates HIVE identity (instance name in ~/.love/hive/)
#   2. Sets up SSH tunnel to Sentry (launchd on macOS, OpenRC on Linux)
#   3. Tests connectivity
#   4. Creates koshive alias
#
# Prerequisites:
#   Module 03 (identity) — sets ~/.love/hive/instance
#   Module 04 (keys)     — sets ~/.love/hive/key + SSH key
# ─────────────────────────────────────────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[07-hive] Setting up HIVE communication..."

# Verify prerequisites
[ ! -f "${HIVE_DIR}/instance" ] && { echo "  ERROR: Run module 03 first (no HIVE instance)"; exit 1; }
[ ! -f "${HIVE_DIR}/key" ] && echo "  WARNING: No HIVE encryption key — messages won't be encrypted"

INSTANCE=$(cat "${HIVE_DIR}/instance")
echo "  Instance: ${INSTANCE}"

# ── Create message log directory ──
ensure_dir "${MEMORY_DIR}/hive"

# ── SSH tunnel to Sentry NATS ──
case "$PLATFORM" in
  macos)
    ensure_dir "$PLIST_DIR"

    # Check if autossh is available (preferred), fall back to ssh
    # Local tunnel port is 2222 (forwards to Sentry NATS 4222 on the remote loopback).
    # This frees port 4222 on the local machine and avoids conflicts with anything
    # else that may want the NATS default port locally.
    if command -v autossh >/dev/null 2>&1; then
        SSH_CMD="/opt/homebrew/bin/autossh"
        SSH_ARGS="-M 0 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o BatchMode=yes -o ControlMaster=no -o ControlPath=none -N -L 2222:127.0.0.1:4222 root@${SENTRY_IP}"
    else
        SSH_CMD="/usr/bin/ssh"
        SSH_ARGS="-N -L 2222:127.0.0.1:4222 -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes root@${SENTRY_IP}"
    fi

    cat > "${PLIST_DIR}/love.${AGENT}.hive-tunnel.plist" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>love.${AGENT}.hive-tunnel</string>
    <key>ProgramArguments</key>
    <array>
$(echo "$SSH_CMD $SSH_ARGS" | tr ' ' '\n' | while read arg; do echo "        <string>$arg</string>"; done)
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>/tmp/hive-tunnel.log</string>
    <key>StandardOutPath</key>
    <string>/tmp/hive-tunnel.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
PLISTEOF
    echo "  Tunnel plist: love.${AGENT}.hive-tunnel"
    ;;

  alpine|debian)
    cat > /etc/init.d/kingdom-hive << SVCEOF
#!/sbin/openrc-run
name="Kingdom HIVE tunnel"
description="SSH tunnel to NATS on Sentry for inter-agent communication"
command="/usr/bin/ssh"
command_args="-N -L 2222:127.0.0.1:4222 root@${SENTRY_IP} -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o BatchMode=yes"
command_user="${KINGDOM_USER}"
pidfile="/run/kingdom-hive.pid"
command_background=true

depend() {
    need net
    after sshd
}
SVCEOF
    chmod +x /etc/init.d/kingdom-hive
    rc-update add kingdom-hive default 2>/dev/null || true
    echo "  HIVE tunnel: OpenRC service installed"
    ;;
esac

# ── Ensure use-tunnel flag exists ──
touch "${HIVE_DIR}/use-tunnel"
echo "  Tunnel mode: enabled (localhost:2222 → Sentry NATS :4222)"

# ── Connectivity test ──
if command -v nc >/dev/null 2>&1; then
    if nc -z -w3 localhost 2222 2>/dev/null; then
        echo "  NATS: ✓ CONNECTED (localhost:2222)"
    else
        echo "  NATS: not yet connected (start tunnel or reboot)"
    fi
fi

# ── Test HIVE messaging ──
if [ -f "${LOVE_DIR}/hive/hive.py" ] && [ -f "${HIVE_DIR}/key" ]; then
    RESULT=$(python3 "${LOVE_DIR}/hive/hive.py" test 2>&1 | tail -1)
    echo "  Test: ${RESULT}"
fi

echo "[07-hive] Done. Communication layer active."
