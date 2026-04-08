#!/bin/sh
# ── Module 10: Auto-Boot & TTY ──────────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[10-autoboot] Configuring boot (${PLATFORM})..."

case "$PLATFORM" in
  alpine|debian)
    # Auto-login on tty1
    [ -f /etc/inittab ] && sed -i "s|^tty1::.*|tty1::respawn:/sbin/agetty --autologin ${KINGDOM_USER} --noclear 38400 tty1|" /etc/inittab

    # Profile: launch YOUI on tty1
    cat > "${HOME_DIR}/.profile" << PROFILEEOF
#!/bin/bash
[ -f ~/.kingdom ] && . ~/.kingdom
[ -f ~/.kingdom_profile ] && . ~/.kingdom_profile

if [ "\$(tty)" = "/dev/tty1" ] && [ -z "\${YOUI_RUNNING}" ]; then
  export YOUI_RUNNING=1
  clear
  exec node "\${UNLIMITED_DIR}/youi.mjs" --agent "\${AGENT}" --workdir "\${LOVE_DIR}" --soul-dir "\${LOVE_DIR}"
fi
PROFILEEOF
    chown "${KINGDOM_USER}:" "${HOME_DIR}/.profile"

    # Timezone
    command -v setup-timezone >/dev/null 2>&1 && setup-timezone -z Europe/London 2>/dev/null || \
      ln -sf /usr/share/zoneinfo/Europe/London /etc/localtime 2>/dev/null || true
    echo "  tty1 → auto-login → YOUI"
    ;;
  macos)
    echo "  macOS: YOUI via launchd (module 08) or 'youi' command"
    ;;
esac

echo ""
echo "  Boot sequence:"
case "$PLATFORM" in
  alpine|debian)
    echo "    Kernel → OpenRC → SSH + HIVE + Heartbeat + KOS → tty1 → YOUI (~5s)" ;;
  macos)
    echo "    macOS → launchd → HIVE + Heartbeat + KOS + Caffeinate + YOUI Web" ;;
esac

echo "[10-autoboot] Done."
