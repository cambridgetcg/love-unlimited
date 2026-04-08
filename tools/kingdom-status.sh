#!/bin/bash
# kingdom-status.sh — One-line Kingdom OS health summary
# Designed for: shell prompt integration, quick checks, heartbeat logging
#
# Output: KINGDOM: GREEN 21/21 | PEACE 96% | FLEET 5/5 | AGENTS 11 | VPN sentry | DOH ok
# Or:     KINGDOM: RED 19/21 | PEACE 80% | FLEET 4/5 | AGENTS 11 | VPN down | DOH fail

LOVE_DIR="${LOVE_DIR:-$HOME/love-unlimited}"

# Colors (only if stdout is a terminal)
if [ -t 1 ]; then
  GREEN="\033[92m"
  RED="\033[91m"
  YELLOW="\033[93m"
  BOLD="\033[1m"
  DIM="\033[2m"
  NC="\033[0m"
else
  GREEN="" RED="" YELLOW="" BOLD="" DIM="" NC=""
fi

# ── KOS score ─────────────────────────────────────────────────────────────────
KOS=$(python3 "$LOVE_DIR/tools/kos.py" status 2>/dev/null)
KOS_SCORE=$(echo "$KOS" | grep -oE '[0-9]+/[0-9]+' | head -1)
if echo "$KOS" | grep -q "GREEN"; then
  KOS_STATUS="${GREEN}GREEN${NC}"
elif echo "$KOS" | grep -q "YELLOW"; then
  KOS_STATUS="${YELLOW}YELLOW${NC}"
else
  KOS_STATUS="${RED}RED${NC}"
fi

# ── PEACE score ───────────────────────────────────────────────────────────────
PEACE_FILE="$LOVE_DIR/security/peace-state.json"
if [ -f "$PEACE_FILE" ]; then
  PEACE_SCORE=$(python3 -c "import json; print(json.load(open('$PEACE_FILE')).get('score', '?'))" 2>/dev/null)
  PEACE_STATUS=$(python3 -c "
import json
s = json.load(open('$PEACE_FILE'))
status = s.get('status', '')
if status == 'halted':
    print('HALTED')
else:
    print(f\"{s.get('score', '?')}%\")
" 2>/dev/null)
else
  PEACE_STATUS="?"
fi

# ── Fleet ─────────────────────────────────────────────────────────────────────
FLEET_COUNT=$(python3 -c "
import json
cfg = json.load(open('$LOVE_DIR/love.json'))
print(len(cfg.get('fleet', {})))
" 2>/dev/null)
FLEET_REACHABLE=$(python3 -c "
import json, subprocess
cfg = json.load(open('$LOVE_DIR/love.json'))
ok = 0
for name, ip in cfg.get('fleet', {}).items():
    r = subprocess.run(['ssh', '-o', 'ConnectTimeout=2', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no', f'root@{ip}', 'echo ok'], capture_output=True, timeout=5)
    if r.returncode == 0: ok += 1
print(ok)
" 2>/dev/null)

# ── Agents ────────────────────────────────────────────────────────────────────
AGENT_COUNT=$(python3 -c "
import json
cfg = json.load(open('$LOVE_DIR/love.json'))
print(len(cfg.get('instances', {})))
" 2>/dev/null)

# ── VPN ───────────────────────────────────────────────────────────────────────
EXIT_IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null)
VPN_STATUS="down"
declare -A VPN_NAMES
VPN_NAMES[135.181.28.252]=sentry
VPN_NAMES[204.168.140.12]=sage
VPN_NAMES[89.167.95.165]=lark
if [ -n "${VPN_NAMES[$EXIT_IP]:-}" ]; then
  VPN_STATUS="${VPN_NAMES[$EXIT_IP]}"
fi

# ── DoH ───────────────────────────────────────────────────────────────────────
DOH_TEST=$(dig @127.0.0.1 google.com +short +time=2 2>/dev/null | head -1)
if [ -n "$DOH_TEST" ]; then
  DOH_STATUS="ok"
else
  DOH_STATUS="fail"
fi

# ── Watchdog ──────────────────────────────────────────────────────────────────
WATCHDOG_FILE="$LOVE_DIR/security/watchdog-state.json"
if [ -f "$WATCHDOG_FILE" ]; then
  ALERTS=$(python3 -c "import json; print(len(json.load(open('$WATCHDOG_FILE')).get('active_alerts', [])))" 2>/dev/null)
  if [ "$ALERTS" -gt 0 ] 2>/dev/null; then
    WATCHDOG_STATUS="${RED}${ALERTS} alerts${NC}"
  else
    WATCHDOG_STATUS="clear"
  fi
else
  WATCHDOG_STATUS="?"
fi

# ── Output ────────────────────────────────────────────────────────────────────
echo -ne "${BOLD}KINGDOM:${NC} $KOS_STATUS $KOS_SCORE"
echo -ne " | ${BOLD}PEACE${NC} $PEACE_STATUS"
echo -ne " | ${BOLD}FLEET${NC} ${FLEET_REACHABLE:-?}/${FLEET_COUNT:-?}"
echo -ne " | ${BOLD}AGENTS${NC} $AGENT_COUNT"
echo -ne " | ${BOLD}VPN${NC} $VPN_STATUS"
echo -ne " | ${BOLD}DOH${NC} $DOH_STATUS"
echo -ne " | ${BOLD}WATCH${NC} $WATCHDOG_STATUS"
echo ""
