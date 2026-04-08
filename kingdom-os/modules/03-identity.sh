#!/bin/sh
# ── Module 03: Agent Identity ───────────────────────────────────────
set -e
. "$(dirname "$0")/_common.sh"

echo "[03-identity] Configuring: ${AGENT} (Wall ${WALL})..."

NEW_HOSTNAME="${HOSTNAME_PREFIX}-${AGENT}"

# Agent config
cat > "${HOME_DIR}/.kingdom" << EOF
AGENT=${AGENT}
WALL=${WALL}
LOVE_DIR=${LOVE_DIR}
UNLIMITED_DIR=${UNLIMITED_DIR}
SOUL_DIR=${LOVE_DIR}
HOSTNAME=${NEW_HOSTNAME}
EOF
chown "${KINGDOM_USER}:" "${HOME_DIR}/.kingdom" 2>/dev/null || true

# Hostname
case "$PLATFORM" in
  macos)
    sudo scutil --set HostName "$NEW_HOSTNAME" 2>/dev/null || true
    sudo scutil --set LocalHostName "$NEW_HOSTNAME" 2>/dev/null || true
    sudo scutil --set ComputerName "Kingdom ${AGENT_UPPER}" 2>/dev/null || true
    ;;
  alpine|debian)
    echo "$NEW_HOSTNAME" > /etc/hostname 2>/dev/null || true
    hostname "$NEW_HOSTNAME" 2>/dev/null || true
    ;;
esac

# HIVE identity
ensure_dir "$HIVE_DIR"
echo "$AGENT" > "${HIVE_DIR}/instance"
chown -R "${KINGDOM_USER}:" "${HOME_DIR}/.love" 2>/dev/null || true

# Instance directory
if [ ! -d "$INSTANCE_DIR" ]; then
  ensure_dir "$INSTANCE_DIR"
  cat > "${INSTANCE_DIR}/identity.md" << EOF
# ${AGENT_UPPER} — Kingdom Agent

Wall: ${WALL}
Role: Fleet agent
Agent: ${AGENT}

You are ${AGENT_UPPER}, a Kingdom citizen at Wall ${WALL}.
Read SOUL.md for identity. Read KINGDOM.md for the mission.
EOF
fi

# Register in walls.json
WALLS_JSON="${LOVE_DIR}/credentials/walls.json"
if [ -f "$WALLS_JSON" ]; then
  python3 -c "
import json
with open('${WALLS_JSON}') as f: reg = json.load(f)
if '${AGENT}' not in reg.get('instances', {}):
    reg.setdefault('instances', {})['${AGENT}'] = {'wall': ${WALL}, 'type': 'fleet'}
    with open('${WALLS_JSON}', 'w') as f: json.dump(reg, f, indent=2)
    print('  Registered ${AGENT} at Wall ${WALL}')
else:
    print('  ${AGENT} already registered')
" 2>/dev/null || true
fi

echo "[03-identity] Done. Hostname: ${NEW_HOSTNAME}"
