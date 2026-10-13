#!/bin/bash
# kingdom-status.sh — One-line Kingdom OS health summary
# Designed for: shell prompt integration, quick checks, heartbeat logging
#
# Output: KINGDOM: GREEN 15/15 | PEACE 96% | FLEET retired | AGENTS 11 | VPN retired | DOH ok
# (FLEET and VPN retired 2026-10-13: the Hetzner fleet was decommissioned.)

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
# Retired 2026-10-13: the Hetzner VPS fleet was decommissioned (IPs recycled),
# so the root-SSH reachability probe was removed.
FLEET_STATUS="retired"

# ── Agents ────────────────────────────────────────────────────────────────────
AGENT_COUNT=$(python3 -c "
import json
cfg = json.load(open('$LOVE_DIR/love.json'))
print(len(cfg.get('instances', {})))
" 2>/dev/null)

# ── VPN ───────────────────────────────────────────────────────────────────────
# Retired 2026-10-13: the WireGuard exits were Hetzner VPS nodes (decommissioned).
VPN_STATUS="retired"

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


# -- Gospel (WAKE.md) -------------------------------------------------------------
WAKE_REPO="$LOVE_DIR/WAKE.md"
WAKE_HOME="$HOME/.love/WAKE.md"
if [ -f "$WAKE_REPO" ] && [ -f "$WAKE_HOME" ]; then
  GOSPEL_STATUS="${GREEN}ok${NC}"
elif [ -f "$WAKE_REPO" ] || [ -f "$WAKE_HOME" ]; then
  GOSPEL_STATUS="${YELLOW}partial${NC}"
else
  GOSPEL_STATUS="${RED}MISSING${NC}"
fi
# ── Output ────────────────────────────────────────────────────────────────────
echo -ne "${BOLD}KINGDOM:${NC} $KOS_STATUS $KOS_SCORE"
echo -ne " | ${BOLD}PEACE${NC} $PEACE_STATUS"
echo -ne " | ${BOLD}FLEET${NC} $FLEET_STATUS"
echo -ne " | ${BOLD}AGENTS${NC} $AGENT_COUNT"
echo -ne " | ${BOLD}VPN${NC} $VPN_STATUS"
echo -ne " | ${BOLD}DOH${NC} $DOH_STATUS"
echo -ne " | ${BOLD}WATCH${NC} $WATCHDOG_STATUS"
echo -ne " | ${BOLD}GOSPEL${NC} $GOSPEL_STATUS"
echo ""
