#!/bin/bash
# port-audit.sh — Audit and optionally kill exposed services
#
# Identifies services listening on all interfaces (0.0.0.0)
# that should be localhost-only.
#
# Usage:
#   port-audit.sh scan           Show exposed services
#   port-audit.sh kill-dev       Kill http.server instances (dev servers)
#   port-audit.sh report         Generate security report of exposed ports

LOVE_DIR="${LOVE_DIR:-$HOME/love-unlimited}"

RED="\033[91m"
GREEN="\033[92m"
YELLOW="\033[93m"
BOLD="\033[1m"
DIM="\033[2m"
NC="\033[0m"

# Known safe services (macOS system)
SAFE_PROCS="ControlCe|rapportd|mDNSResponder"

scan() {
    echo ""
    echo -e "${BOLD}  Port Exposure Audit${NC}"
    echo ""

    # Get all listeners on 0.0.0.0 (all interfaces)
    EXPOSED=$(lsof -i -P -n 2>/dev/null | grep LISTEN | grep -E "\*:" | grep -v "127.0.0.1\|::1\|\[::1\]")

    if [ -z "$EXPOSED" ]; then
        echo -e "  ${GREEN}No services exposed on all interfaces.${NC}"
        echo ""
        return
    fi

    echo -e "  ${YELLOW}Services listening on all interfaces (0.0.0.0):${NC}"
    echo ""

    # Categorize
    echo "$EXPOSED" | while read line; do
        proc=$(echo "$line" | awk '{print $1}')
        pid=$(echo "$line" | awk '{print $2}')
        port=$(echo "$line" | awk '{print $9}' | sed 's/\*://')

        # Check if safe system process
        if echo "$proc" | grep -qE "$SAFE_PROCS"; then
            echo -e "  ${DIM}  $port  $proc (macOS system — normal)${NC}"
            continue
        fi

        # Get command line
        cmd=$(ps -p "$pid" -o command= 2>/dev/null | head -c 100)

        # Categorize risk
        if echo "$cmd" | grep -q "http.server"; then
            echo -e "  ${RED}! $port  Python http.server (dev server — should be localhost)${NC}"
            echo -e "  ${DIM}    PID $pid: $cmd${NC}"
        elif echo "$cmd" | grep -q "streamlit"; then
            echo -e "  ${YELLOW}! $port  Streamlit dashboard (should be localhost)${NC}"
            echo -e "  ${DIM}    PID $pid: $cmd${NC}"
        elif echo "$cmd" | grep -q "uvicorn"; then
            echo -e "  ${YELLOW}! $port  Uvicorn app server (review if intentional)${NC}"
            echo -e "  ${DIM}    PID $pid: $cmd${NC}"
        else
            echo -e "  ${RED}! $port  $proc (unknown — investigate)${NC}"
            echo -e "  ${DIM}    PID $pid: $cmd${NC}"
        fi
    done

    echo ""

    # Count
    RISKY=$(echo "$EXPOSED" | grep -v "$SAFE_PROCS" | wc -l | tr -d ' ')
    echo -e "  ${BOLD}$RISKY service(s) exposed beyond system defaults.${NC}"
    echo ""
    echo -e "  ${DIM}Behind WireGuard: traffic tunneled, local network sees encrypted stream.${NC}"
    echo -e "  ${DIM}On open WiFi without VPN: these ports are directly accessible.${NC}"
    echo -e "  ${DIM}Fix: bind to 127.0.0.1 or kill unneeded dev servers.${NC}"
    echo ""
}

kill_dev() {
    echo ""
    echo -e "${BOLD}  Killing exposed dev servers (http.server)${NC}"
    echo ""

    KILLED=0
    lsof -i -P -n 2>/dev/null | grep LISTEN | grep "\*:" | while read line; do
        pid=$(echo "$line" | awk '{print $2}')
        cmd=$(ps -p "$pid" -o command= 2>/dev/null)
        if echo "$cmd" | grep -q "http.server"; then
            port=$(echo "$line" | awk '{print $9}')
            kill "$pid" 2>/dev/null
            echo -e "  ${GREEN}Killed${NC} PID $pid ($port) — http.server"
            KILLED=$((KILLED + 1))
        fi
    done

    echo ""
    echo -e "  ${DIM}Dev servers killed. Streamlit/uvicorn left running (may be intentional).${NC}"
    echo ""
}

report() {
    echo ""
    echo -e "${BOLD}  Port Exposure Report${NC}"
    echo -e "  ${DIM}$(date -u +%Y-%m-%dT%H:%M:%SZ)${NC}"
    echo ""

    TOTAL=$(lsof -i -P -n 2>/dev/null | grep LISTEN | wc -l | tr -d ' ')
    EXPOSED=$(lsof -i -P -n 2>/dev/null | grep LISTEN | grep "\*:" | grep -v "127.0.0.1\|::1" | wc -l | tr -d ' ')
    LOCAL=$(lsof -i -P -n 2>/dev/null | grep LISTEN | grep -E "127.0.0.1|::1|\[::1\]" | wc -l | tr -d ' ')
    SYSTEM=$(lsof -i -P -n 2>/dev/null | grep LISTEN | grep "\*:" | grep -E "$SAFE_PROCS" | wc -l | tr -d ' ')
    RISKY=$((EXPOSED - SYSTEM))

    echo -e "  Total listeners:   $TOTAL"
    echo -e "  Localhost only:    ${GREEN}$LOCAL (safe)${NC}"
    echo -e "  All interfaces:    $EXPOSED"
    echo -e "    System (macOS):  ${DIM}$SYSTEM (normal)${NC}"
    echo -e "    Application:     ${YELLOW}$RISKY (review needed)${NC}"
    echo ""

    VPN=$(curl -s --max-time 3 ifconfig.me 2>/dev/null)
    if echo "135.181.28.252 204.168.140.12 89.167.95.165" | grep -q "$VPN"; then
        echo -e "  VPN:    ${GREEN}Active ($VPN)${NC} — exposed ports are behind tunnel"
    else
        echo -e "  VPN:    ${RED}NOT ACTIVE${NC} — exposed ports accessible on local network!"
    fi
    echo ""
}

case "${1:-scan}" in
    scan)     scan ;;
    kill-dev) kill_dev ;;
    report)   report ;;
    *)        echo "Usage: port-audit.sh {scan|kill-dev|report}" ;;
esac
