#!/bin/sh
# ── Module 08: Heartbeat + KOS Daemon ───────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[08-heartbeat] Setting up heartbeat for ${AGENT} (${PLATFORM})..."

# Locate runner
RUNNER="${INSTANCE_DIR}/heartbeat-runner.sh"
[ ! -f "$RUNNER" ] && RUNNER="${LOVE_DIR}/tools/heartbeat-runner.sh"

case "$PLATFORM" in
  macos)
    ensure_dir "$PLIST_DIR"

    # Heartbeat
    cat > "${PLIST_DIR}/love.${AGENT}.heartbeat.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>love.${AGENT}.heartbeat</string>
    <key>ProgramArguments</key><array><string>/bin/bash</string><string>${RUNNER}</string></array>
    <key>WorkingDirectory</key><string>${LOVE_DIR}</string>
    <key>StartInterval</key><integer>420</integer>
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>${MEMORY_DIR}/heartbeat-${AGENT}-launchd.log</string>
    <key>StandardErrorPath</key><string>${MEMORY_DIR}/heartbeat-${AGENT}-launchd.log</string>
    <key>EnvironmentVariables</key><dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${HOME_DIR}/.local/bin</string>
        <key>HOME</key><string>${HOME_DIR}</string>
        <key>LOVE_HOME</key><string>${LOVE_DIR}</string>
    </dict>
</dict>
</plist>
EOF

    # KOS daemon
    cat > "${PLIST_DIR}/love.kos.daemon.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>love.kos.daemon</string>
    <key>ProgramArguments</key><array><string>/bin/bash</string><string>${LOVE_DIR}/tools/kos-daemon.sh</string></array>
    <key>StartInterval</key><integer>420</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>/tmp/kos-daemon-stdout.log</string>
    <key>StandardErrorPath</key><string>/tmp/kos-daemon-stderr.log</string>
    <key>Nice</key><integer>10</integer>
    <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF

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
    echo "  Plists: heartbeat, kos-daemon, caffeinate"
    ;;
  alpine|debian)
    cat > /etc/init.d/kingdom-heartbeat << EOF
#!/sbin/openrc-run
description="Kingdom Heartbeat — 7-minute cycle"
command="/usr/bin/bash"
command_args="${RUNNER}"
command_user="${KINGDOM_USER}"
directory="${LOVE_DIR}"
pidfile="/run/kingdom-heartbeat.pid"
command_background=true
depend() { need net; after sshd kingdom-hive; }
EOF
    chmod +x /etc/init.d/kingdom-heartbeat
    rc-update add kingdom-heartbeat default 2>/dev/null || true

    # KOS via cron
    CRON_LINE="*/7 * * * * /bin/bash ${LOVE_DIR}/tools/kos-daemon.sh >> /tmp/kos-daemon.log 2>&1"
    (crontab -l 2>/dev/null | grep -v "kos-daemon"; echo "$CRON_LINE") | crontab -
    echo "  Heartbeat: OpenRC, KOS: cron"
    ;;
esac

echo "[08-heartbeat] Done."
