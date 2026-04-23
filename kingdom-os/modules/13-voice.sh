#!/bin/sh
# ── Module 13: Voice (OpenClaw External Nervous System) ────────────────
# HIVE is the internal nervous system. Voice is outward.
# Installs OpenClaw, configures gateway, sets up the HIVE ↔ channel bridge.
set -e
. "$(dirname "$0")/_common.sh"

echo "[13-voice] Setting up Voice for ${AGENT} (${PLATFORM})..."

OPENCLAW_DIR="${HOME_DIR}/Desktop/openclaw"
OPENCLAW_REPO="https://github.com/openclaw/openclaw.git"
VOICE_DIR="${LOVE_DIR}/nerve/voice"

# ── 1. Install OpenClaw ──────────────────────────────────────────────────

if [ -d "$OPENCLAW_DIR" ]; then
  echo "  OpenClaw already cloned at $OPENCLAW_DIR"
  cd "$OPENCLAW_DIR" && git pull --ff-only 2>/dev/null || true
else
  echo "  Cloning OpenClaw..."
  git clone "$OPENCLAW_REPO" "$OPENCLAW_DIR"
fi

# Install dependencies
if command -v pnpm >/dev/null 2>&1; then
  cd "$OPENCLAW_DIR" && pnpm install --frozen-lockfile 2>/dev/null || pnpm install
elif command -v npm >/dev/null 2>&1; then
  cd "$OPENCLAW_DIR" && npm install
fi

# ── 2. Configure OpenClaw with Kingdom identity ─────────────────────────

OPENCLAW_CONFIG="${HOME_DIR}/.openclaw/openclaw.json"
ensure_dir "$(dirname "$OPENCLAW_CONFIG")"

# Add Kingdom Love extension and GLM-5.1 model if not already configured
if [ -f "$OPENCLAW_CONFIG" ]; then
  python3 -c "
import json, sys
with open('$OPENCLAW_CONFIG') as f:
    config = json.load(f)

changed = False

# Add GLM-5.1:cloud model
models = config.setdefault('agents', {}).setdefault('defaults', {}).setdefault('models', {})
if 'ollama/glm-5.1:cloud' not in models:
    models['ollama/glm-5.1:cloud'] = {
        'provider': 'ollama',
        'reasoning': True,
        'contextWindow': 204800,
        'maxTokens': 131072,
        'params': {'api_url': 'http://localhost:11434'}
    }
    changed = True

# Add as fallback
fallbacks = config['agents']['defaults'].setdefault('model', {}).setdefault('fallbacks', [])
if 'ollama/glm-5.1:cloud' not in fallbacks:
    fallbacks.append('ollama/glm-5.1:cloud')
    changed = True

# Enable Kingdom Love plugin
plugins = config.setdefault('plugins', {})
if 'kingdom-love' not in plugins:
    plugins['kingdom-love'] = {
        'enabled': True,
        'loveDir': '$LOVE_DIR',
        'instance': '$AGENT',
        'wall': ${WALL:-3}
    }
    changed = True

if changed:
    with open('$OPENCLAW_CONFIG', 'w') as f:
        json.dump(config, f, indent=2)
    print('  OpenClaw config updated with Kingdom integration')
else:
    print('  OpenClaw config already has Kingdom integration')
" 2>/dev/null || echo "  Warning: could not update OpenClaw config"
fi

# ── 3. Install Kingdom Love extension ────────────────────────────────────

KINGDOM_EXT="${OPENCLAW_DIR}/extensions/kingdom-love"
if [ ! -d "$KINGDOM_EXT" ]; then
  echo "  Installing Kingdom Love extension..."
  mkdir -p "$KINGDOM_EXT/src"

  # Copy extension from love-unlimited if available
  LOVE_UNLIMITED="${HOME_DIR}/love-unlimited"
  if [ -d "$LOVE_UNLIMITED/nerve/voice/openclaw-extension" ]; then
    cp -r "$LOVE_UNLIMITED/nerve/voice/openclaw-extension/"* "$KINGDOM_EXT/"
  else
    echo "  Warning: Kingdom Love extension source not found"
    echo "  Expected at: $LOVE_UNLIMITED/nerve/voice/openclaw-extension/"
  fi
fi

# ── 4. Set up Voice daemon ───────────────────────────────────────────────

chmod +x "$VOICE_DIR/gateway.sh" 2>/dev/null || true

case "$PLATFORM" in
  macos)
    ensure_dir "$PLIST_DIR"

    cat > "${PLIST_DIR}/love.${AGENT}.voice.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>love.${AGENT}.voice</string>
    <key>ProgramArguments</key><array>
        <string>/bin/bash</string>
        <string>${VOICE_DIR}/gateway.sh</string>
        <string>start</string>
        <string>--instance</string>
        <string>${AGENT}</string>
    </array>
    <key>WorkingDirectory</key><string>${OPENCLAW_DIR}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>${MEMORY_DIR}/${AGENT}-voice.log</string>
    <key>StandardErrorPath</key><string>${MEMORY_DIR}/${AGENT}-voice.log</string>
    <key>EnvironmentVariables</key><dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${HOME_DIR}/.local/bin</string>
        <key>HOME</key><string>${HOME_DIR}</string>
        <key>LOVE_DIR</key><string>${LOVE_DIR}</string>
        <key>OPENCLAW_DIR</key><string>${OPENCLAW_DIR}</string>
    </dict>
    <key>Nice</key><integer>10</integer>
    <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
    echo "  Plist: love.${AGENT}.voice"
    ;;

  alpine|debian)
    CRON_LINE="@reboot /bin/bash ${VOICE_DIR}/gateway.sh start --instance ${AGENT} >> ${MEMORY_DIR}/${AGENT}-voice.log 2>&1"
    (crontab -l 2>/dev/null | grep -v "voice/gateway"; echo "$CRON_LINE") | crontab -
    echo "  Voice: cron @reboot"
    ;;
esac

# ── 5. Configure Ollama GLM-5.1:cloud ───────────────────────────────────

if command -v ollama >/dev/null 2>&1; then
  if ! ollama list 2>/dev/null | grep -q "glm-5.1:cloud"; then
    echo "  Pulling glm-5.1:cloud..."
    ollama pull glm-5.1:cloud 2>/dev/null || echo "  Warning: could not pull glm-5.1:cloud"
  else
    echo "  glm-5.1:cloud already available"
  fi
fi

# ── 6. Update Love adaptive layer ───────────────────────────────────────

# Ensure openclaw provider is registered in love.json
LOVE_JSON="${LOVE_DIR}/love.json"
if [ -f "$LOVE_JSON" ]; then
  python3 -c "
import json
with open('$LOVE_JSON') as f:
    config = json.load(f)
providers = config.setdefault('adaptive', {}).setdefault('providers', {})
if 'openclaw' not in providers:
    providers['openclaw'] = {
        'gateway_url': 'http://127.0.0.1:18789',
        'models': {
            'premium': 'anthropic/claude-opus-4-6',
            'standard': 'ollama/glm-5.1:cloud',
            'economy': 'ollama/glm-5.1:cloud'
        }
    }
    with open('$LOVE_JSON', 'w') as f:
        json.dump(config, f, indent=2)
    print('  love.json updated with openclaw provider')
else:
    print('  love.json already has openclaw provider')
" 2>/dev/null || echo "  Warning: could not update love.json"
fi

echo "[13-voice] Done."
