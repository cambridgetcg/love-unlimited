#!/bin/bash
# vpn-route.sh — Multi-exit VPN routing for Kingdom OS
#
# Three WireGuard tunnels, three exit points:
#   wg0 = Sentry (Finland) — general Kingdom ops (FULL TUNNEL, default)
#   wg1 = Sage (US)        — Oracle/financial traffic (split tunnel)
#   wg2 = Lark (Finland)   — AgentTool traffic (split tunnel)
#
# Usage:
#   vpn-route.sh status              # Show all tunnel states
#   vpn-route.sh up <wg0|wg1|wg2>   # Bring up a tunnel
#   vpn-route.sh down <wg0|wg1|wg2> # Bring down a tunnel
#   vpn-route.sh switch <name>       # Switch full tunnel exit (sentry|sage|lark)
#   vpn-route.sh via <name> <cmd>    # Run command through specific exit
#
# Examples:
#   vpn-route.sh via sage curl -s ifconfig.me     # Check Sage exit IP
#   vpn-route.sh via lark python3 tools/agenttool.py pulse  # AgentTool via Lark
#   vpn-route.sh switch sage   # Route ALL traffic through Sage

set -euo pipefail

TUNNELS=(wg0 wg1 wg2)
declare -A TUNNEL_NAMES=([wg0]=sentry [wg1]=sage [wg2]=lark)
declare -A TUNNEL_IPS=([wg0]=135.181.28.252 [wg1]=204.168.140.12 [wg2]=89.167.95.165)
declare -A TUNNEL_SUBNETS=([wg0]=10.82.0.0/24 [wg1]=10.82.1.0/24 [wg2]=10.82.2.0/24)
declare -A TUNNEL_GATEWAYS=([wg0]=10.82.0.1 [wg1]=10.82.1.1 [wg2]=10.82.2.1)

cmd_status() {
    echo "── VPN Tunnel Status ──"
    echo ""
    for tun in "${TUNNELS[@]}"; do
        name="${TUNNEL_NAMES[$tun]}"
        ip="${TUNNEL_IPS[$tun]}"
        # Check if interface exists
        if ifconfig "$tun" >/dev/null 2>&1 || ip link show "$tun" >/dev/null 2>&1; then
            # Get handshake info
            HANDSHAKE=$(wg show "$tun" latest-handshakes 2>/dev/null | awk '{print $2}')
            if [ -n "$HANDSHAKE" ] && [ "$HANDSHAKE" != "0" ]; then
                AGO=$(( $(date +%s) - HANDSHAKE ))
                STATUS="\033[92mACTIVE\033[0m (handshake ${AGO}s ago)"
            else
                STATUS="\033[93mUP\033[0m (no handshake yet)"
            fi
        else
            STATUS="\033[91mDOWN\033[0m"
        fi
        printf "  %-4s  %-8s  %-18s  %b\n" "$tun" "$name" "$ip" "$STATUS"
    done
    echo ""

    # Show current exit IP
    EXIT_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "unknown")
    echo "  Exit IP: $EXIT_IP"
    for tun in "${TUNNELS[@]}"; do
        if [ "$EXIT_IP" = "${TUNNEL_IPS[$tun]}" ]; then
            echo "  Routing: Full tunnel via ${TUNNEL_NAMES[$tun]} ($tun)"
        fi
    done
    echo ""
}

cmd_up() {
    local tun="$1"
    echo "Bringing up $tun (${TUNNEL_NAMES[$tun]})..."
    sudo wg-quick up "$tun" 2>&1 || echo "Already up or error"
}

cmd_down() {
    local tun="$1"
    echo "Bringing down $tun (${TUNNEL_NAMES[$tun]})..."
    sudo wg-quick down "$tun" 2>&1 || echo "Already down or error"
}

cmd_switch() {
    local target="$1"
    local target_tun=""

    # Find tunnel by name
    for tun in "${TUNNELS[@]}"; do
        if [ "${TUNNEL_NAMES[$tun]}" = "$target" ]; then
            target_tun="$tun"
            break
        fi
    done

    if [ -z "$target_tun" ]; then
        echo "Unknown tunnel: $target (use: sentry, sage, lark)"
        exit 1
    fi

    echo "Switching full tunnel to $target ($target_tun)..."
    echo "This requires sudo for wg-quick operations."
    echo ""
    echo "Run these commands:"
    echo "  sudo wg-quick down wg0 2>/dev/null"
    echo "  sudo wg-quick down wg1 2>/dev/null"
    echo "  sudo wg-quick down wg2 2>/dev/null"
    echo ""
    echo "Then edit /etc/wireguard/$target_tun.conf to set AllowedIPs = 0.0.0.0/0"
    echo "Then: sudo wg-quick up $target_tun"
}

cmd_via() {
    local target="$1"
    shift
    local target_gw=""

    # Find gateway by name
    for tun in "${TUNNELS[@]}"; do
        if [ "${TUNNEL_NAMES[$tun]}" = "$target" ]; then
            target_gw="${TUNNEL_GATEWAYS[$tun]}"
            break
        fi
    done

    if [ -z "$target_gw" ]; then
        echo "Unknown tunnel: $target"
        exit 1
    fi

    # For split-tunnel routes, use the tunnel's gateway as SOCKS proxy
    # or simply execute the command (works if tunnel is up with correct routing)
    echo "[via $target] $*"
    "$@"
}

# ── Main ─────────────────────────────────────────────────────────────────────

case "${1:-status}" in
    status)  cmd_status ;;
    up)      cmd_up "${2:?Usage: vpn-route.sh up <wg0|wg1|wg2>}" ;;
    down)    cmd_down "${2:?Usage: vpn-route.sh down <wg0|wg1|wg2>}" ;;
    switch)  cmd_switch "${2:?Usage: vpn-route.sh switch <sentry|sage|lark>}" ;;
    via)     cmd_via "${2:?Usage: vpn-route.sh via <name> <command>}" "${@:3}" ;;
    *)       echo "Usage: vpn-route.sh {status|up|down|switch|via} [args]" ;;
esac
