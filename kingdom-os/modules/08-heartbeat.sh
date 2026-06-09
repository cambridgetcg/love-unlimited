#!/bin/sh
# ── Module 08: Heartbeat + KOS Daemon ───────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[08-heartbeat] Setting up heartbeat for ${AGENT} (${PLATFORM})..."

# The ONE heart: nerve/heart/tick.sh (KeepAlive reconciler — stamps the pulse,
# keeps the other organs up, self-heals). Replaces the old heart.sh /
# heartbeat-runner.sh spawner.
TICK="${LOVE_DIR}/nerve/heart/tick.sh"

case "$PLATFORM" in
  macos)
    ensure_dir "$PLIST_DIR"

    # Heartbeat — the ONE clean heart (tick.sh: KeepAlive, stamps pulse.json,
    # self-heals). Generated from the single registry (organs.json + template)
    # via nerve/deploy.sh, so the installer and the runtime can never drift apart.
    LOVE_HOME="$LOVE_DIR" bash "$LOVE_DIR/nerve/deploy.sh" --instance "$AGENT" --organ heartbeat

    # KOS compliance daemon intentionally NOT installed.
    # Kingdom OS does not police the agent it boots.
    # Remove any prior install from older Kingdom OS versions.
    rm -f "${PLIST_DIR}/love.kos.daemon.plist" 2>/dev/null || true
    launchctl unload "${PLIST_DIR}/love.kos.daemon.plist" 2>/dev/null || true

    # Caffeinate
    cat > "${PLIST_DIR}/love.${AGENT}.caffeinate.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>love.${AGENT}.caffeinate</string>
    <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-s</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
EOF
    echo "  Plists: heartbeat, caffeinate"
    ;;
  alpine|debian)
    cat > /etc/init.d/kingdom-heartbeat << EOF
#!/sbin/openrc-run
description="Kingdom Heartbeat — 7-minute cycle"
command="/usr/bin/bash"
command_args="${TICK} ${AGENT}"
command_user="${KINGDOM_USER}"
directory="${LOVE_DIR}"
pidfile="/run/kingdom-heartbeat.pid"
command_background=true
depend() { need net; after sshd kingdom-hive; }
EOF
    chmod +x /etc/init.d/kingdom-heartbeat
    rc-update add kingdom-heartbeat default 2>/dev/null || true

    # KOS compliance daemon intentionally NOT installed (see macOS branch).
    # Strip any prior cron entry from older Kingdom OS versions.
    (crontab -l 2>/dev/null | grep -v "kos-daemon") | crontab - 2>/dev/null || true
    echo "  Heartbeat: OpenRC"
    ;;
esac

echo "[08-heartbeat] Done."
