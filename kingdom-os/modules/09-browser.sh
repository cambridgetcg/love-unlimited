#!/bin/sh
# ── Module 09: Browser & Web Operations ─────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[09-browser] Setting up browser (${PLATFORM})..."

case "$PLATFORM" in
  alpine|debian)
    CHROMIUM_BIN=$(which chromium-browser 2>/dev/null || which chromium 2>/dev/null || echo "")
    if [ -n "$CHROMIUM_BIN" ]; then
      cat > /usr/local/bin/kingdom-browser << EOF
#!/bin/sh
exec ${CHROMIUM_BIN} --headless --no-sandbox --disable-gpu --disable-dev-shm-usage --remote-debugging-port=9222 "\$@"
EOF
      chmod +x /usr/local/bin/kingdom-browser
      echo "  Headless browser: ${CHROMIUM_BIN}"
    fi
    ;;
esac

# Playwright
if command -v npx >/dev/null 2>&1; then
  echo "  Installing Playwright..."
  cd "$LOVE_DIR" && npx --yes playwright install chromium 2>/dev/null || true
fi

# Python HTTP tools
pip3 install --quiet --break-system-packages httpx beautifulsoup4 lxml 2>/dev/null || \
pip3 install --quiet httpx beautifulsoup4 lxml 2>/dev/null || true

# YOUI Web server
YOUI_WEB="${UNLIMITED_DIR}/youi-web"
if [ -d "$YOUI_WEB" ] && [ "$PLATFORM" = "macos" ]; then
  ensure_dir "$PLIST_DIR"
  cat > "${PLIST_DIR}/com.kingdom.youi-web.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.kingdom.youi-web</string>
    <key>ProgramArguments</key><array><string>/opt/homebrew/bin/node</string><string>${YOUI_WEB}/server.mjs</string></array>
    <key>WorkingDirectory</key><string>${LOVE_DIR}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>${HOME_DIR}/youi-web-stdout.log</string>
    <key>StandardErrorPath</key><string>${HOME_DIR}/youi-web-stderr.log</string>
    <key>EnvironmentVariables</key><dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key><string>${HOME_DIR}</string>
        <key>LOVE_HOME</key><string>${LOVE_DIR}</string>
    </dict>
</dict>
</plist>
EOF
  echo "  YOUI Web plist created"
fi

echo "[09-browser] Done."
