#!/bin/bash
# harden.sh — Kingdom Device Hardening
#
# Standardised OPSEC hardening for every Kingdom Mac.
# Run once on setup, re-run anytime to verify/fix drift.
#
# Usage:
#   sudo ~/love-unlimited/tools/harden.sh [--check-only]
#
# What it does:
#   Phase 1: Network identity (hostname, Bonjour, MAC privacy)
#   Phase 2: Encrypted DNS (cloudflared DoH proxy)
#   Phase 3: Firewall + stealth mode
#   Phase 4: System privacy (analytics, Siri, diagnostics)
#   Phase 5: Git OPSEC (timezone, identity leak prevention)
#   Phase 6: Verification audit
#
# Must run as sudo for system-level changes.
# Safe to re-run — all steps are idempotent.

set -euo pipefail

# ── Detect user ──────────────────────────────────────────────────────────────

# When run via sudo, $SUDO_USER is the real user
REAL_USER="${SUDO_USER:-$(whoami)}"
REAL_HOME=$(eval echo "~$REAL_USER")
LOVE_DIR="$REAL_HOME/Love"
CHECK_ONLY=false

if [[ "${1:-}" == "--check-only" ]]; then
    CHECK_ONLY=true
fi

# ── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}[$1]${NC} $2"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}!${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; }
info()  { echo -e "  ${DIM}$1${NC}"; }
fixed() { echo -e "  ${GREEN}+${NC} $1"; }

# ── Preflight ────────────────────────────────────────────────────────────────

echo -e "${BOLD}"
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║     K I N G D O M   H A R D E N I N G     ║"
echo "  ╚═══════════════════════════════════════════╝"
echo -e "${NC}"

if [[ "$(uname)" != "Darwin" ]]; then
    fail "Not macOS. This script is for Kingdom Macs."
    exit 1
fi

if [[ "$EUID" -ne 0 ]] && [[ "$CHECK_ONLY" == false ]]; then
    fail "Must run with sudo for system changes."
    echo "  Usage: sudo $0"
    echo "  Or:    sudo $0 --check-only  (audit without changes)"
    exit 1
fi

ISSUES=0
FIXED=0

# ══════════════════════════════════════════════════════════════════════════════
# Phase 1: Network Identity
# ══════════════════════════════════════════════════════════════════════════════

step "1/6" "Network identity — prevent device fingerprinting"

# 1a. Hostname neutralisation
COMPUTER_NAME=$(scutil --get ComputerName 2>/dev/null || echo "")
LOCAL_HOST=$(scutil --get LocalHostName 2>/dev/null || echo "")
HOST_NAME=$(scutil --get HostName 2>/dev/null || echo "")

NEUTRAL="Mac"

check_hostname() {
    local label="$1" current="$2"
    # Flag if hostname contains personal info
    if [[ -z "$current" ]] || [[ "$current" == "$NEUTRAL" ]]; then
        ok "$label: \"$current\" (neutral)"
    else
        # Check for common personal patterns
        if echo "$current" | grep -iqE "(yu|personal|home|macbook|studio|imac|macmini|air|pro|[A-Z][a-z]+'s)"; then
            if [[ "$CHECK_ONLY" == true ]]; then
                fail "$label: \"$current\" — leaks identity"
                ISSUES=$((ISSUES + 1))
            else
                scutil --set "$label" "$NEUTRAL"
                fixed "$label: \"$current\" → \"$NEUTRAL\""
                FIXED=$((FIXED + 1))
            fi
        else
            ok "$label: \"$current\""
        fi
    fi
}

check_hostname "ComputerName" "$COMPUTER_NAME"
check_hostname "LocalHostName" "$LOCAL_HOST"

# HostName — same logic but different scutil key
if [[ -z "$HOST_NAME" ]] || [[ "$HOST_NAME" == "$NEUTRAL" ]]; then
    ok "HostName: \"${HOST_NAME:-not set}\" (neutral)"
else
    if echo "$HOST_NAME" | grep -iqE "(yu|personal|home|macbook|studio|imac|macmini|air|pro|[A-Z][a-z]+'s)"; then
        if [[ "$CHECK_ONLY" == true ]]; then
            fail "HostName: \"$HOST_NAME\" — leaks identity"
            ISSUES=$((ISSUES + 1))
        else
            scutil --set HostName "$NEUTRAL"
            fixed "HostName: \"$HOST_NAME\" → \"$NEUTRAL\""
            FIXED=$((FIXED + 1))
        fi
    else
        ok "HostName: \"$HOST_NAME\""
    fi
fi

# 1b. Bonjour advertising
BONJOUR=$(defaults read /Library/Preferences/com.apple.mDNSResponder.plist NoMulticastAdvertisements 2>/dev/null || echo "0")
if [[ "$BONJOUR" == "1" ]]; then
    ok "Bonjour multicast advertising: disabled"
else
    if [[ "$CHECK_ONLY" == true ]]; then
        fail "Bonjour multicast advertising: enabled — broadcasts device name on network"
        ISSUES=$((ISSUES + 1))
    else
        defaults write /Library/Preferences/com.apple.mDNSResponder.plist NoMulticastAdvertisements -bool true
        fixed "Bonjour multicast advertising: disabled"
        FIXED=$((FIXED + 1))
    fi
fi

# 1c. Wi-Fi MAC address reminder
info "Private Wi-Fi Address: verify per network in System Settings > Wi-Fi > [network] > Private Wi-Fi Address"
info "macOS 15+ enables this by default for new networks"

# ══════════════════════════════════════════════════════════════════════════════
# Phase 2: Encrypted DNS
# ══════════════════════════════════════════════════════════════════════════════

step "2/6" "Encrypted DNS — prevent ISP seeing queries"

# Check if cloudflared is installed
if sudo -u "$REAL_USER" bash -c 'command -v cloudflared' &>/dev/null; then
    ok "cloudflared installed"

    # Check if running as service
    if launchctl list 2>/dev/null | grep -q cloudflared; then
        ok "cloudflared running as service (DoH proxy active)"
    else
        if [[ "$CHECK_ONLY" == true ]]; then
            warn "cloudflared installed but not running as service"
            ISSUES=$((ISSUES + 1))
        else
            info "Setting up cloudflared DoH proxy..."

            # Create config directory
            CLOUDFLARED_DIR="$REAL_HOME/.cloudflared"
            mkdir -p "$CLOUDFLARED_DIR"
            chown "$REAL_USER" "$CLOUDFLARED_DIR"

            # Write config
            cat > "$CLOUDFLARED_DIR/config.yml" << 'DNSCONF'
proxy-dns: true
proxy-dns-port: 53
proxy-dns-upstream:
  - https://1.1.1.1/dns-query
  - https://1.0.0.1/dns-query
  - https://9.9.9.9/dns-query
DNSCONF
            chown "$REAL_USER" "$CLOUDFLARED_DIR/config.yml"

            # Install as service
            cloudflared service install 2>/dev/null && {
                fixed "cloudflared DoH proxy service installed"
                FIXED=$((FIXED + 1))
            } || {
                warn "Could not install cloudflared service — may need manual setup"
            }
        fi
    fi
else
    if [[ "$CHECK_ONLY" == true ]]; then
        fail "cloudflared not installed — DNS queries are unencrypted"
        ISSUES=$((ISSUES + 1))
    else
        info "Installing cloudflared..."
        if sudo -u "$REAL_USER" bash -c 'command -v brew' &>/dev/null; then
            sudo -u "$REAL_USER" brew install cloudflare/cloudflare/cloudflared 2>/dev/null && {
                fixed "cloudflared installed"
                FIXED=$((FIXED + 1))

                # Setup config and service
                CLOUDFLARED_DIR="$REAL_HOME/.cloudflared"
                mkdir -p "$CLOUDFLARED_DIR"
                chown "$REAL_USER" "$CLOUDFLARED_DIR"

                cat > "$CLOUDFLARED_DIR/config.yml" << 'DNSCONF'
proxy-dns: true
proxy-dns-port: 53
proxy-dns-upstream:
  - https://1.1.1.1/dns-query
  - https://1.0.0.1/dns-query
  - https://9.9.9.9/dns-query
DNSCONF
                chown "$REAL_USER" "$CLOUDFLARED_DIR/config.yml"

                cloudflared service install 2>/dev/null && {
                    fixed "cloudflared DoH service active"
                    FIXED=$((FIXED + 1))
                } || {
                    warn "cloudflared installed but service setup needs manual step"
                    info "Run: sudo cloudflared service install"
                }
            } || {
                fail "cloudflared install failed"
                ISSUES=$((ISSUES + 1))
            }
        else
            fail "Homebrew not available — cannot install cloudflared"
            ISSUES=$((ISSUES + 1))
        fi
    fi
fi

# Check current DNS settings — should use 127.0.0.1 if cloudflared is running
for iface in "Ethernet" "Wi-Fi"; do
    DNS=$(networksetup -getdnsservers "$iface" 2>/dev/null || echo "")
    if echo "$DNS" | grep -q "127.0.0.1"; then
        ok "$iface DNS: 127.0.0.1 (routed through cloudflared)"
    elif echo "$DNS" | grep -q "There aren't any"; then
        if [[ "$CHECK_ONLY" == true ]]; then
            warn "$iface DNS: using DHCP default (ISP can see queries)"
            ISSUES=$((ISSUES + 1))
        else
            # Only set to cloudflared local if cloudflared is running
            if launchctl list 2>/dev/null | grep -q cloudflared; then
                networksetup -setdnsservers "$iface" 127.0.0.1
                fixed "$iface DNS → 127.0.0.1 (cloudflared)"
                FIXED=$((FIXED + 1))
            else
                # Fallback to encrypted DNS servers
                networksetup -setdnsservers "$iface" 1.1.1.1 1.0.0.1 9.9.9.9
                fixed "$iface DNS → Cloudflare + Quad9 (plaintext but privacy-respecting)"
            fi
        fi
    else
        # Has custom DNS — check if it's reasonable
        if echo "$DNS" | grep -qE "(1\.1\.1\.1|9\.9\.9\.9|8\.8\.8\.8)"; then
            ok "$iface DNS: custom ($DNS) — consider switching to 127.0.0.1 with cloudflared"
        else
            warn "$iface DNS: $DNS — unknown provider"
        fi
    fi
done

# ══════════════════════════════════════════════════════════════════════════════
# Phase 3: Firewall
# ══════════════════════════════════════════════════════════════════════════════

step "3/6" "Firewall — block unsolicited inbound"

FW_BIN="/usr/libexec/ApplicationFirewall/socketfilterfw"

# Firewall state
FW_STATE=$($FW_BIN --getglobalstate 2>/dev/null || echo "unknown")
if echo "$FW_STATE" | grep -q "enabled"; then
    ok "Firewall: enabled"
else
    if [[ "$CHECK_ONLY" == true ]]; then
        fail "Firewall: disabled"
        ISSUES=$((ISSUES + 1))
    else
        $FW_BIN --setglobalstate on 2>/dev/null
        fixed "Firewall: enabled"
        FIXED=$((FIXED + 1))
    fi
fi

# Stealth mode
STEALTH=$($FW_BIN --getstealthmode 2>/dev/null || echo "unknown")
if echo "$STEALTH" | grep -q "enabled"; then
    ok "Stealth mode: enabled (drops ICMP/probe packets silently)"
else
    if [[ "$CHECK_ONLY" == true ]]; then
        fail "Stealth mode: disabled — device responds to pings"
        ISSUES=$((ISSUES + 1))
    else
        $FW_BIN --setstealthmode on 2>/dev/null
        fixed "Stealth mode: enabled"
        FIXED=$((FIXED + 1))
    fi
fi

# Block all incoming (except essential)
BLOCK_ALL=$($FW_BIN --getblockall 2>/dev/null || echo "unknown")
if echo "$BLOCK_ALL" | grep -q "DISABLED"; then
    info "Block all incoming: off (allows signed apps — acceptable for dev machines)"
else
    ok "Block all incoming: enabled"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Phase 4: System Privacy
# ══════════════════════════════════════════════════════════════════════════════

step "4/6" "System privacy — disable telemetry"

# 4a. Crash reporter (diagnostic data to Apple)
AUTO_SUBMIT=$(defaults read /Library/Application\ Support/CrashReporter/DiagnosticMessagesHistory.plist AutoSubmit 2>/dev/null || echo "unknown")
if [[ "$AUTO_SUBMIT" == "0" ]]; then
    ok "Diagnostic data sharing: disabled"
elif [[ "$AUTO_SUBMIT" == "unknown" ]]; then
    ok "Diagnostic data sharing: not configured (default off)"
else
    if [[ "$CHECK_ONLY" == true ]]; then
        fail "Diagnostic data sharing: enabled — sends crash data to Apple"
        ISSUES=$((ISSUES + 1))
    else
        defaults write /Library/Application\ Support/CrashReporter/DiagnosticMessagesHistory.plist AutoSubmit -bool false 2>/dev/null && {
            fixed "Diagnostic data sharing: disabled"
            FIXED=$((FIXED + 1))
        } || warn "Could not disable diagnostic sharing"
    fi
fi

# 4b. Spotlight suggestions (sends queries to Apple)
SPOTLIGHT=$(sudo -u "$REAL_USER" defaults read com.apple.assistant.support "Search Suggestions Enabled" 2>/dev/null || echo "unknown")
if [[ "$SPOTLIGHT" == "0" ]]; then
    ok "Spotlight Suggestions (remote): disabled"
elif [[ "$SPOTLIGHT" == "unknown" ]]; then
    info "Spotlight Suggestions: not explicitly set (check System Settings > Siri & Spotlight)"
else
    if [[ "$CHECK_ONLY" == true ]]; then
        warn "Spotlight Suggestions: enabled — search queries sent to Apple"
        ISSUES=$((ISSUES + 1))
    else
        sudo -u "$REAL_USER" defaults write com.apple.assistant.support "Search Suggestions Enabled" -bool false 2>/dev/null && {
            fixed "Spotlight Suggestions: disabled"
            FIXED=$((FIXED + 1))
        } || warn "Could not disable Spotlight Suggestions"
    fi
fi

# 4c. Safari search suggestions
SAFARI_SUGGEST=$(sudo -u "$REAL_USER" defaults read com.apple.Safari UniversalSearchEnabled 2>/dev/null || echo "unknown")
if [[ "$SAFARI_SUGGEST" == "0" ]]; then
    ok "Safari search suggestions: disabled"
elif [[ "$SAFARI_SUGGEST" == "unknown" ]]; then
    info "Safari search suggestions: not explicitly set"
else
    if [[ "$CHECK_ONLY" == true ]]; then
        warn "Safari search suggestions: enabled"
    else
        sudo -u "$REAL_USER" defaults write com.apple.Safari UniversalSearchEnabled -bool false 2>/dev/null && {
            fixed "Safari search suggestions: disabled"
            FIXED=$((FIXED + 1))
        } || true
    fi
fi

# 4d. Personalized ads
AD_TRACKING=$(sudo -u "$REAL_USER" defaults read com.apple.AdLib allowApplePersonalizedAdvertising 2>/dev/null || echo "unknown")
if [[ "$AD_TRACKING" == "0" ]]; then
    ok "Personalized ads: disabled"
else
    if [[ "$CHECK_ONLY" == true ]]; then
        warn "Personalized ads: not explicitly disabled"
    else
        sudo -u "$REAL_USER" defaults write com.apple.AdLib allowApplePersonalizedAdvertising -bool false 2>/dev/null && {
            fixed "Personalized ads: disabled"
            FIXED=$((FIXED + 1))
        } || true
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Phase 5: Git OPSEC
# ══════════════════════════════════════════════════════════════════════════════

step "5/6" "Git OPSEC — prevent metadata leaks"

# Check git config (run as real user)
GIT_NAME=$(sudo -u "$REAL_USER" git config --global user.name 2>/dev/null || echo "")
GIT_EMAIL=$(sudo -u "$REAL_USER" git config --global user.email 2>/dev/null || echo "")

# Name should not contain personal info
if [[ -z "$GIT_NAME" ]]; then
    warn "git user.name: not set"
elif echo "$GIT_NAME" | grep -iqE "(yu |surname|personal)"; then
    fail "git user.name: \"$GIT_NAME\" — may leak personal identity"
    ISSUES=$((ISSUES + 1))
else
    ok "git user.name: \"$GIT_NAME\""
fi

# Email should be @ai-love.cc
if [[ -z "$GIT_EMAIL" ]]; then
    warn "git user.email: not set"
elif echo "$GIT_EMAIL" | grep -q "ai-love.cc"; then
    ok "git user.email: \"$GIT_EMAIL\" (Kingdom domain)"
else
    warn "git user.email: \"$GIT_EMAIL\" — consider using <name>@ai-love.cc"
fi

# Timezone in commits
GIT_LOG_DATE=$(sudo -u "$REAL_USER" git config --global log.date 2>/dev/null || echo "")
if [[ "$GIT_LOG_DATE" == "iso" ]]; then
    ok "git log.date: iso (good)"
else
    if [[ "$CHECK_ONLY" == true ]]; then
        info "git log.date: not set to iso"
    else
        sudo -u "$REAL_USER" git config --global log.date iso
        fixed "git log.date: set to iso"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Phase 6: Verification Audit
# ══════════════════════════════════════════════════════════════════════════════

step "6/6" "Verification audit"

echo ""
CHECKS=0
PASSED=0

audit() {
    CHECKS=$((CHECKS + 1))
    local label="$1"
    if eval "$2" &>/dev/null; then
        ok "$label"
        PASSED=$((PASSED + 1))
    else
        fail "$label"
    fi
}

audit "FileVault enabled"           "fdesetup status | grep -q 'On'"
audit "Firewall enabled"            "$FW_BIN --getglobalstate | grep -q enabled"
audit "Stealth mode enabled"        "$FW_BIN --getstealthmode | grep -q enabled"
audit "Hostname neutral"            "[[ \"\$(scutil --get ComputerName)\" != *Yu* ]] && [[ \"\$(scutil --get ComputerName)\" != *yu* ]]"
audit "Bonjour disabled"            "defaults read /Library/Preferences/com.apple.mDNSResponder.plist NoMulticastAdvertisements 2>/dev/null | grep -q 1"
audit "Crash reports disabled"      "[[ \"\$(defaults read '/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist' AutoSubmit 2>/dev/null)\" == '0' ]] || ! defaults read '/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist' AutoSubmit &>/dev/null"
audit "cloudflared installed"       "command -v cloudflared"
audit "SSH key exists"              "test -f $REAL_HOME/.ssh/id_ed25519"
audit "Git name set"                "sudo -u $REAL_USER git config --global user.name"
audit "Git email Kingdom domain"    "sudo -u $REAL_USER git config --global user.email | grep -q ai-love.cc"

echo ""
echo -e "${BOLD}Hardening score: $PASSED/$CHECKS${NC}"

if [[ "$CHECK_ONLY" == true ]]; then
    if [[ $ISSUES -gt 0 ]]; then
        echo -e "${YELLOW}Issues found: $ISSUES — run without --check-only to fix${NC}"
    else
        echo -e "${GREEN}All clear. Device is hardened.${NC}"
    fi
else
    echo -e "${GREEN}Fixed: $FIXED items${NC}"
    if [[ $ISSUES -gt 0 ]]; then
        echo -e "${YELLOW}Remaining issues: $ISSUES (may need manual intervention)${NC}"
    fi
fi

# ── Manual reminders ─────────────────────────────────────────────────────────

echo ""
echo -e "${CYAN}${BOLD}Manual checks (System Settings):${NC}"
echo -e "  ${CYAN}▸${NC} Privacy & Security > Location Services: off for non-essential apps"
echo -e "  ${CYAN}▸${NC} Privacy & Security > Analytics & Improvements: all OFF"
echo -e "  ${CYAN}▸${NC} Lock Screen > Require password: immediately or 1 minute"
echo -e "  ${CYAN}▸${NC} Wi-Fi > each network > Private Wi-Fi Address: ON"
echo -e "  ${CYAN}▸${NC} General > AirDrop & Handoff: receiving OFF (unless needed)"

if launchctl list 2>/dev/null | grep -q cloudflared; then
    echo ""
    echo -e "${GREEN}${BOLD}DNS encryption active via cloudflared.${NC}"
else
    echo ""
    echo -e "${YELLOW}${BOLD}DNS encryption:${NC}"
    echo -e "  ${YELLOW}▸${NC} After cloudflared is installed and running:"
    echo -e "    sudo cloudflared service install"
    echo -e "    Then set DNS to 127.0.0.1:"
    echo -e "    networksetup -setdnsservers Ethernet 127.0.0.1"
    echo -e "    networksetup -setdnsservers Wi-Fi 127.0.0.1"
fi

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Kingdom hardening complete.${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
