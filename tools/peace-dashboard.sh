#!/bin/bash
# peace-dashboard.sh — PEACE resilience overview in one screen
# Shows: state, score, threats, incidents, playbooks, snapshots, fleet canaries

LOVE_DIR="${LOVE_DIR:-$HOME/love-unlimited}"

GREEN="\033[92m" RED="\033[91m" YELLOW="\033[93m" CYAN="\033[96m"
BOLD="\033[1m" DIM="\033[2m" NC="\033[0m"

echo ""
echo -e "${BOLD}══════════════════════════════════════════════════════════════${NC}"
echo -e "  ${BOLD}PEACE — Kingdom Resilience Dashboard${NC}"
echo -e "  ${DIM}$(date -u +%Y-%m-%dT%H:%M:%SZ)${NC}"
echo -e "${BOLD}══════════════════════════════════════════════════════════════${NC}"
echo ""

# ── State ──
PEACE_STATE=$(python3 -c "
import json
from pathlib import Path
s = json.loads(Path('$LOVE_DIR/security/peace-state.json').read_text())
state = s.get('state', s.get('status', 'unknown')).upper()
score = s.get('score', '?')
print(f'{state}|{score}')
" 2>/dev/null)
STATE=$(echo "$PEACE_STATE" | cut -d'|' -f1)
SCORE=$(echo "$PEACE_STATE" | cut -d'|' -f2)

case "$STATE" in
  NOMINAL|ACTIVE) STATE_COLOR="$GREEN" ;;
  ALERT|INVESTIGATING) STATE_COLOR="$YELLOW" ;;
  HALTED) STATE_COLOR="$RED" ;;
  *) STATE_COLOR="$DIM" ;;
esac

echo -e "  ${BOLD}State:${NC} ${STATE_COLOR}${BOLD}$STATE${NC}    ${BOLD}Score:${NC} ${SCORE}%"
echo ""

# ── Phase Scores ──
echo -e "  ${BOLD}Phase Scores${NC}"
python3 -c "
import json
from pathlib import Path
s = json.loads(Path('$LOVE_DIR/security/peace-state.json').read_text())
phases = s.get('phases', {})
names = {'detect': 'DETECT', 'contain': 'CONTAIN', 'fix': 'FIX', 'revert': 'REVERT', 'resume': 'RESUME'}
for pid in ['detect', 'contain', 'fix', 'revert', 'resume']:
    score = phases.get(pid, 0)
    bar_len = int(score / 5)
    if score >= 80:
        color = '\033[92m'
    elif score >= 60:
        color = '\033[93m'
    else:
        color = '\033[91m'
    bar = f'{color}[{\"█\" * bar_len}{\"░\" * (20 - bar_len)}]\033[0m'
    print(f'    {bar} {score:5.1f}%  {names[pid]}')
" 2>/dev/null
echo ""

# ── Threats ──
echo -e "  ${BOLD}Threat Model${NC}"
python3 -c "
import json
from pathlib import Path
tm_file = Path('$LOVE_DIR/security/threat-model.json')
if tm_file.exists():
    data = json.loads(tm_file.read_text())
    threats = data.get('threats', data if isinstance(data, list) else [])
    mitigated = sum(1 for t in threats if t.get('mitigated'))
    critical = sum(1 for t in threats if t.get('priority') == 'critical' and not t.get('mitigated'))
    high = sum(1 for t in threats if t.get('priority') == 'high' and not t.get('mitigated'))
    print(f'    Total: {len(threats)}  Mitigated: {mitigated}  Open: {len(threats)-mitigated}')
    if critical:
        print(f'    \033[91m{critical} CRITICAL open\033[0m')
    if high:
        print(f'    \033[93m{high} HIGH open\033[0m')
else:
    print('    No threat model defined')
" 2>/dev/null
echo ""

# ── Playbooks ──
echo -e "  ${BOLD}Recovery Playbooks${NC}"
PLAYBOOK_COUNT=$(ls "$LOVE_DIR/security/runbooks/"*.md 2>/dev/null | wc -l | tr -d ' ')
echo -e "    $PLAYBOOK_COUNT playbook(s) available"
for pb in "$LOVE_DIR/security/runbooks/"*.md; do
  [ -f "$pb" ] && echo -e "    ${DIM}$(basename "$pb" .md)${NC}"
done
echo ""

# ── Snapshots ──
echo -e "  ${BOLD}State Snapshots${NC}"
SNAP_COUNT=$(ls "$LOVE_DIR/security/snapshots/snapshot-"*.json 2>/dev/null | wc -l | tr -d ' ')
SNAP_LATEST=$(ls -t "$LOVE_DIR/security/snapshots/snapshot-"*.json 2>/dev/null | head -1 | xargs basename 2>/dev/null || echo "none")
echo -e "    Total: $SNAP_COUNT  Latest: ${DIM}$SNAP_LATEST${NC}"
echo ""

# ── Watchdog ──
echo -e "  ${BOLD}Watchdog${NC}"
python3 -c "
import json
from pathlib import Path
wf = Path('$LOVE_DIR/security/watchdog-state.json')
if wf.exists():
    s = json.loads(wf.read_text())
    alerts = len(s.get('active_alerts', []))
    esc = s.get('escalation_count', 0)
    halts = s.get('auto_halt_count', 0)
    if alerts > 0:
        print(f'    \033[91m{alerts} active alert(s)\033[0m')
    else:
        print(f'    \033[92mCLEAR\033[0m  Escalations: {esc}  Auto-halts: {halts}')
else:
    print('    No watchdog state')
" 2>/dev/null
echo ""

# ── Fleet Canaries ──
echo -e "  ${BOLD}Fleet Canaries${NC}"
echo -e "    ${DIM}Local:${NC} $(python3 -c "
import json
from pathlib import Path
c = json.loads(Path('$LOVE_DIR/security/canaries.json').read_text())
print(f'{len(c)} canaries deployed')
" 2>/dev/null)"
echo -e "    ${DIM}Fleet:${NC} 25 canary files across 5 VPS nodes (hourly check cron)"
echo ""

# ── Recent Incidents ──
echo -e "  ${BOLD}Recent Incidents${NC}"
python3 -c "
import json
from pathlib import Path
events = Path('$LOVE_DIR/security/events.jsonl')
if events.exists():
    incidents = []
    for line in events.read_text().strip().splitlines()[-100:]:
        try:
            e = json.loads(line)
            if e.get('type') in ('emergency_halt', 'fleet_canary_trip', 'integrity_violation', 'watchdog_escalation'):
                incidents.append(e)
        except: pass
    if incidents:
        for inc in incidents[-5:]:
            ts = inc.get('ts', '?')[:16]
            print(f'    \033[2m{ts}\033[0m  {inc.get(\"type\", \"?\")}  {inc.get(\"message\", \"\")[:60]}')
    else:
        print('    \033[92mNo incidents recorded\033[0m')
else:
    print('    No event log')
" 2>/dev/null
echo ""

# ── Reflection ──
echo -e "  ${BOLD}Ouroboros (Reflection Loop)${NC}"
python3 -c "
import json
from pathlib import Path
ls = Path('$LOVE_DIR/memory/loop/loop-state.json')
if ls.exists():
    s = json.loads(ls.read_text())
    lr = s.get('last_reflect', 'never')[:16] if s.get('last_reflect') else 'never'
    rc = s.get('reflection_count', 0)
    pp = s.get('pending_proposals', 0)
    print(f'    Last reflect: {lr}  Count: {rc}  Proposals: {pp} pending')
else:
    print('    No loop state')
" 2>/dev/null
echo ""

echo -e "${BOLD}══════════════════════════════════════════════════════════════${NC}"
echo -e "  ${DIM}kingdom peace — for full score  |  kingdom watch — for watchdog${NC}"
echo -e "  ${DIM}kingdom drill — for PEACE drill  |  kingdom snapshot — save state${NC}"
echo -e "${BOLD}══════════════════════════════════════════════════════════════${NC}"
echo ""
