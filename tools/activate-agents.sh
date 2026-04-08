#!/bin/bash
# activate-agents.sh — Activate Kingdom agents as cron heartbeats
#
# Generates and installs cron entries for agents that can run locally.
# Wall 1 agents use claude backend. Wall 2-3 agents use the configured backend.
#
# Usage:
#   activate-agents.sh list              Show what would be activated
#   activate-agents.sh install [name]    Install cron for agent (or all)
#   activate-agents.sh uninstall [name]  Remove cron for agent (or all)
#   activate-agents.sh status            Show active crons

set -euo pipefail
LOVE_DIR="${LOVE_DIR:-$HOME/Love}"
AGENT_BIN="$LOVE_DIR/tools/kingdom-agent.py"
BACKEND="${KINGDOM_BACKEND:-claude}"

# Agents that can run locally (have HEARTBEAT.md)
get_activatable_agents() {
    python3 -c "
import json
from pathlib import Path
love = Path('$LOVE_DIR')
cfg = json.load(open(love / 'love.json'))
for name, info in sorted(cfg['instances'].items()):
    inst = love / 'instances' / name
    if (inst / 'HEARTBEAT.md').exists() and (inst / 'CLAUDE.md').exists():
        wall = info.get('wall', '?')
        role = info.get('role', '?')
        emoji = info.get('emoji', '?')
        print(f'{name}|{wall}|{role}|{emoji}')
"
}

cron_line_for() {
    local name="$1"
    local wall="$2"
    local interval="${3:-7}"  # minutes

    # Wall 1 agents get claude backend; others get configured backend
    local agent_backend="$BACKEND"
    if [ "$wall" = "1" ]; then
        agent_backend="claude"
    fi

    echo "*/7 * * * * cd $LOVE_DIR && python3 $AGENT_BIN -p 'Execute HEARTBEAT.md' --instance $name --backend $agent_backend --effort medium --skip-permissions --no-persist >> $LOVE_DIR/memory/${name}-heartbeat.log 2>&1  # kingdom:$name"
}

cmd_list() {
    echo ""
    echo "  Activatable Agents"
    echo "  ═══════════════════"
    echo ""

    while IFS='|' read -r name wall role emoji; do
        local agent_backend="$BACKEND"
        [ "$wall" = "1" ] && agent_backend="claude"

        # Check if already in cron
        if crontab -l 2>/dev/null | grep -q "kingdom:$name"; then
            status="\033[92mACTIVE\033[0m"
        else
            status="\033[2mready\033[0m"
        fi

        printf "  %s %-12s W%s %-12s %-8s %b\n" "$emoji" "$name" "$wall" "$role" "$agent_backend" "$status"
    done < <(get_activatable_agents)

    echo ""
    echo "  Backend: $BACKEND (set KINGDOM_BACKEND to change)"
    echo "  Interval: every 7 minutes"
    echo ""
}

cmd_install() {
    local target="${1:-all}"
    local installed=0

    while IFS='|' read -r name wall role emoji; do
        if [ "$target" != "all" ] && [ "$target" != "$name" ]; then
            continue
        fi

        # Skip if already installed
        if crontab -l 2>/dev/null | grep -q "kingdom:$name"; then
            echo "  $emoji $name: already active (skipping)"
            continue
        fi

        local line
        line=$(cron_line_for "$name" "$wall")

        # Add to crontab
        (crontab -l 2>/dev/null; echo "$line") | crontab -
        echo "  $emoji $name: \033[92mactivated\033[0m (W$wall, every 7m)"
        installed=$((installed + 1))
    done < <(get_activatable_agents)

    if [ "$installed" -gt 0 ]; then
        echo ""
        echo "  $installed agent(s) activated."
        echo "  Logs: memory/<name>-heartbeat.log"
    else
        echo "  No agents installed (all already active or none matched)."
    fi
    echo ""
}

cmd_uninstall() {
    local target="${1:-all}"
    local removed=0

    local current
    current=$(crontab -l 2>/dev/null || true)

    while IFS='|' read -r name wall role emoji; do
        if [ "$target" != "all" ] && [ "$target" != "$name" ]; then
            continue
        fi

        if echo "$current" | grep -q "kingdom:$name"; then
            current=$(echo "$current" | grep -v "kingdom:$name")
            echo "  $emoji $name: deactivated"
            removed=$((removed + 1))
        fi
    done < <(get_activatable_agents)

    echo "$current" | crontab -

    if [ "$removed" -gt 0 ]; then
        echo ""
        echo "  $removed agent(s) deactivated."
    else
        echo "  No agents to deactivate."
    fi
    echo ""
}

cmd_status() {
    echo ""
    echo "  Active Agent Heartbeats"
    echo "  ═══════════════════════"
    echo ""

    local count=0
    while IFS='|' read -r name wall role emoji; do
        if crontab -l 2>/dev/null | grep -q "kingdom:$name"; then
            local logfile="$LOVE_DIR/memory/${name}-heartbeat.log"
            local last_run="never"
            if [ -f "$logfile" ]; then
                last_run=$(stat -f %Sm -t "%Y-%m-%d %H:%M" "$logfile" 2>/dev/null || echo "?")
            fi
            printf "  \033[92m●\033[0m %s %-12s W%s  last: %s\n" "$emoji" "$name" "$wall" "$last_run"
            count=$((count + 1))
        fi
    done < <(get_activatable_agents)

    if [ "$count" -eq 0 ]; then
        echo "  No agents active via cron."
        echo "  Run: activate-agents.sh install [name|all]"
    else
        echo ""
        echo "  $count agent(s) active."
    fi

    # Also check launchd
    if launchctl list 2>/dev/null | grep -q "love.heartbeat"; then
        echo "  + Beta heartbeat via launchd (love.heartbeat)"
    fi
    echo ""
}

case "${1:-list}" in
    list)      cmd_list ;;
    install)   cmd_install "${2:-all}" ;;
    uninstall) cmd_uninstall "${2:-all}" ;;
    status)    cmd_status ;;
    *)
        echo "Usage: activate-agents.sh {list|install|uninstall|status} [agent-name]"
        ;;
esac
