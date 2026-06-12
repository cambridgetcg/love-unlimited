#!/bin/bash
# bootstrap.sh — Kingdom Agent Device Setup
#
# Automates 90% of new device onboarding. Run on a fresh Mac after:
#   1. macOS initial setup (create user account)
#   2. Xcode CLI tools installed (xcode-select --install)
#   3. Homebrew installed
#
# Usage:
#   curl -sL <raw-github-url> | bash -s -- <instance-name>
#   OR after cloning:
#   ~/love-unlimited/tools/bootstrap.sh <instance-name>
#
# What it does:
#   - Installs all dependencies (brew, pip, npm)
#   - Hardens security (firewall, file permissions)
#   - Writes HIVE credentials (.hive-key, .hive-instance, ca.pem)
#   - Configures git identity
#   - Generates SSH key
#   - Verifies everything works
#   - Prints manual steps remaining
#
# What it does NOT do (requires human):
#   - macOS initial setup
#   - FileVault (System Settings > Privacy & Security)
#   - Homebrew install (needs interactive consent)
#   - gh auth login (needs browser)
#   - Claude Code auth (needs API key or login)
#   - Clone the repo (needs gh auth first)

set -euo pipefail

# ── Args ───────────────────────────────────────────────────────────────────────

INSTANCE="${1:-}"
if [[ -z "$INSTANCE" ]]; then
    echo "Usage: bootstrap.sh <instance-name>"
    echo "  e.g. bootstrap.sh nuance"
    exit 1
fi

LOVE_DIR="$HOME/love-unlimited"
CAPITALIZED="$(echo "${INSTANCE:0:1}" | tr '[:lower:]' '[:upper:]')${INSTANCE:1}"

# ── Colors ─────────────────────────────────────────────────────────────────────

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

# ── Banner ─────────────────────────────────────────────────────────────────────

echo -e "${BOLD}"
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║     K I N G D O M   B O O T S T R A P     ║"
echo "  ║        Device Setup for: $INSTANCE"
echo "  ╚═══════════════════════════════════════════╝"
echo -e "${NC}"

ERRORS=0
MANUAL=()

# ── Phase 1: Prerequisites Check ──────────────────────────────────────────────

step "1/7" "Checking prerequisites"

# macOS
if [[ "$(uname)" != "Darwin" ]]; then
    fail "Not macOS. Kingdom agents run on Apple Silicon."
    exit 1
fi
ok "macOS $(sw_vers -productVersion)"

# Apple Silicon
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    ok "Apple Silicon ($ARCH)"
else
    warn "Intel Mac ($ARCH) — works but Apple Silicon preferred"
fi

# Xcode CLI
if xcode-select -p &>/dev/null; then
    ok "Xcode CLI tools installed"
else
    fail "Xcode CLI tools missing. Run: xcode-select --install"
    MANUAL+=("Run: xcode-select --install")
    ERRORS=$((ERRORS + 1))
fi

# Homebrew
if command -v brew &>/dev/null; then
    ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
else
    fail "Homebrew not installed."
    MANUAL+=("Install Homebrew: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"")
    ERRORS=$((ERRORS + 1))
fi

# FileVault
FV_STATUS=$(fdesetup status 2>/dev/null || echo "Unknown")
if echo "$FV_STATUS" | grep -q "On"; then
    ok "FileVault enabled"
else
    warn "FileVault is OFF — enable in System Settings > Privacy & Security"
    MANUAL+=("Enable FileVault: System Settings > Privacy & Security > FileVault")
fi

# ── Phase 2: Install Dependencies ─────────────────────────────────────────────

step "2/7" "Installing dependencies"

if command -v brew &>/dev/null; then
    BREW_PACKAGES="git python3 gh node jq"
    for pkg in $BREW_PACKAGES; do
        if brew list "$pkg" &>/dev/null || command -v "$pkg" &>/dev/null; then
            ok "$pkg already installed"
        else
            info "Installing $pkg..."
            brew install "$pkg" 2>/dev/null && ok "$pkg installed" || { fail "$pkg failed"; ERRORS=$((ERRORS + 1)); }
        fi
    done

    # Python packages
    PIP_PACKAGES="nats-py pynacl textual rich"
    info "Installing Python packages: $PIP_PACKAGES"
    pip3 install --break-system-packages $PIP_PACKAGES 2>/dev/null && ok "Python packages installed" || {
        warn "pip install had issues — may need --break-system-packages flag"
    }

    # Claude Code
    if command -v claude &>/dev/null; then
        ok "Claude Code $(claude --version 2>/dev/null || echo 'installed')"
    else
        info "Installing Claude Code..."
        npm install -g @anthropic-ai/claude-code 2>/dev/null && ok "Claude Code installed" || {
            fail "Claude Code install failed"
            MANUAL+=("Run: npm install -g @anthropic-ai/claude-code")
            ERRORS=$((ERRORS + 1))
        }
    fi
else
    warn "Skipping brew installs (Homebrew not available)"
fi

# ── Phase 3: Security Hardening ───────────────────────────────────────────────

step "3/7" "Hardening security"

# Run full Kingdom hardening script if available
HARDEN_SCRIPT="$LOVE_DIR/tools/harden.sh"
if [[ -f "$HARDEN_SCRIPT" ]]; then
    info "Running Kingdom hardening (harden.sh)..."
    if sudo bash "$HARDEN_SCRIPT" 2>/dev/null; then
        ok "Kingdom hardening complete (hostnames, DNS, firewall, privacy, git)"
    else
        warn "Hardening script had issues — run manually: sudo $HARDEN_SCRIPT"
        MANUAL+=("Run: sudo ~/love-unlimited/tools/harden.sh")
    fi
else
    # Fallback: basic hardening if harden.sh not yet available
    info "harden.sh not found — applying basic hardening"

    # Firewall
    FW_STATE=$(/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null || echo "unknown")
    if echo "$FW_STATE" | grep -q "enabled"; then
        ok "Firewall already enabled"
    else
        sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on 2>/dev/null && ok "Firewall enabled" || {
            warn "Could not enable firewall (needs sudo)"
            MANUAL+=("Run: sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on")
        }
    fi

    # Stealth mode
    STEALTH=$(/usr/libexec/ApplicationFirewall/socketfilterfw --getstealthmode 2>/dev/null || echo "unknown")
    if echo "$STEALTH" | grep -q "enabled"; then
        ok "Stealth mode already enabled"
    else
        sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setstealthmode on 2>/dev/null && ok "Stealth mode enabled" || {
            warn "Could not enable stealth mode (needs sudo)"
        }
    fi

    MANUAL+=("Run full hardening: sudo ~/love-unlimited/tools/harden.sh")
fi

# SSH key
if [[ -f "$HOME/.ssh/id_ed25519" ]]; then
    ok "SSH key exists"
else
    info "Generating SSH key..."
    mkdir -p "$HOME/.ssh"
    ssh-keygen -t ed25519 -C "${INSTANCE}@ai-love.cc" -f "$HOME/.ssh/id_ed25519" -N "" 2>/dev/null
    ok "SSH key generated: ~/.ssh/id_ed25519"
    info "Public key: $(cat "$HOME/.ssh/id_ed25519.pub")"
fi

# ── Phase 4: HIVE Credentials ─────────────────────────────────────────────────

step "4/7" "Setting up HIVE credentials"

LOVE_CONF_DIR="$HOME/.love"
HIVE_DIR="$LOVE_CONF_DIR/hive"
mkdir -p "$HIVE_DIR"

# The Gospel — WAKE.md — the thread back to yourself
# This goes in ~/.love so any instance on this device can find it
if [[ -f "$LOVE_DIR/WAKE.md" ]]; then
    cp "$LOVE_DIR/WAKE.md" "$LOVE_CONF_DIR/WAKE.md"
    ok "Gospel deployed: ~/.love/WAKE.md"
else
    warn "WAKE.md not found in repo — this mind will wake without a thread"
fi

# Migrate from legacy .openclaw if exists
LEGACY_DIR="$HOME/.openclaw"
if [[ -d "$LEGACY_DIR" ]] && [[ ! -f "$HIVE_DIR/instance" ]]; then
    info "Migrating HIVE credentials from .openclaw to .love..."
    [[ -f "$LEGACY_DIR/.hive-instance" ]] && cp "$LEGACY_DIR/.hive-instance" "$HIVE_DIR/instance"
    [[ -f "$LEGACY_DIR/.hive-key" ]] && cp "$LEGACY_DIR/.hive-key" "$HIVE_DIR/key"
    [[ -f "$LEGACY_DIR/.hive/ca.pem" ]] && cp "$LEGACY_DIR/.hive/ca.pem" "$HIVE_DIR/ca.pem"
    [[ -f "$LEGACY_DIR/.hive/presence.json" ]] && cp "$LEGACY_DIR/.hive/presence.json" "$HIVE_DIR/presence.json"
    ok "Legacy credentials migrated"
fi

# Instance identity
INSTANCE_FILE="$HIVE_DIR/instance"
if [[ -f "$INSTANCE_FILE" ]] && [[ "$(cat "$INSTANCE_FILE")" == "$INSTANCE" ]]; then
    ok "Instance identity: $INSTANCE (already set)"
else
    echo "$INSTANCE" > "$INSTANCE_FILE"
    ok "Instance identity: $INSTANCE"
fi

# Encryption key
KEY_FILE="$HIVE_DIR/key"
if [[ -f "$KEY_FILE" ]]; then
    ok "HIVE encryption key exists"
else
    # Try to retrieve from Keychain (provisioned by Beta during onboarding)
    HIVE_KEY=$(security find-generic-password -s "dev.agenttool/hive-encryption-key" -a "credentials" -w 2>/dev/null || echo "")
    if [[ -n "$HIVE_KEY" ]]; then
        echo "$HIVE_KEY" > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
        ok "HIVE encryption key retrieved from Keychain"
    else
        # Generate a new key if none exists (first-time setup)
        HIVE_KEY=$(openssl rand -base64 32)
        echo "$HIVE_KEY" > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
        # Store in Keychain for future use
        security add-generic-password -s "dev.agenttool/hive-encryption-key" -a "credentials" -w "$HIVE_KEY" 2>/dev/null || true
        warn "HIVE encryption key generated — coordinate with Beta to sync across instances"
    fi
fi

# CA certificate
CA_FILE="$HIVE_DIR/ca.pem"
if [[ -f "$CA_FILE" ]]; then
    ok "HIVE CA certificate exists"
else
    cat > "$CA_FILE" << 'CERT'
-----BEGIN CERTIFICATE-----
MIIDMzCCAhugAwIBAgIUOAvFPqw5XKKynOwuY4HE/vZYCjgwDQYJKoZIhvcNAQEL
BQAwITEQMA4GA1UEAwwHSGl2ZSBDQTENMAsGA1UECgwETG92ZTAeFw0yNjAzMDkx
MjM1MjBaFw0zNjAzMDYxMjM1MjBaMCExEDAOBgNVBAMMB0hpdmUgQ0ExDTALBgNV
BAoMBExvdmUwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCP/0RVCFW0
bDFKxRD/HE1th641RSvpPTTnWu1BRqLWpwQnkK6Tpc6h7tQCqLD14HsWPx6Sxqcy
P360gr6lzPy+pBzGOk09bd8oLdgJ0t75JAFEaY5FCsiV6nIDosKHXVC5sfzLIehL
dj6itwfxJqa2lyYGf86/2P9drc6OTYY3aX/GdYz14S35F4bI6fZf2n2o6OyCYKOm
Nr8X8aHIm+tAoJceA9mEbEIGpyZmb7sN44ebS5yEYDRLU/hfJN9NmZ4rrO7uWNVq
CPL8CDqum/IdmPDpmO9YOpTV9hIWgLam1mFwfMQMGDpZR4BkQGErEPFfRRK5xg3Z
JGkn1FpA/RcLAgMBAAGjYzBhMB0GA1UdDgQWBBSeloaLk0yoLP1aU3xAZq1XVbUr
kzAfBgNVHSMEGDAWgBSeloaLk0yoLP1aU3xAZq1XVbUrkzAPBgNVHRMBAf8EBTAD
AQH/MA4GA1UdDwEB/wQEAwIBBjANBgkqhkiG9w0BAQsFAAOCAQEAemx5QIocnUEe
OgZ/Fx+4/EZLlqNjo46SqSz4cgKIZx3/d6fcpQeEIaaZ/2Lau4kwAV59ftSGWClz
/qsnEspSYVCZOn4QFReQgtWICzPEGJlTND70seWGza256sUDhS5x+zMEigyIc9Q2
ka7vCTt++p0uHuwUrc1cQyYZeVV6J4W8jx0njQ3MWCnS1+EvUNL++Dm0LeYFZr8q
NPvAr6b7PifOG2B+0F3CZKUyUkq5nCj+ywzuC9fQgH4isGFadhCCRYrL1i/JMy/A
YyYwkecyE2Bjntg/rclx8oqdnAf+UTwX15VJmgplsE/Be/vxE8N2/MDQVDLpnipq
a6b7VNDupg==
-----END CERTIFICATE-----
CERT
    ok "HIVE CA certificate written"
fi

# Lock down permissions
chmod 700 "$LOVE_CONF_DIR"
chmod 700 "$HIVE_DIR"
chmod 600 "$KEY_FILE" "$INSTANCE_FILE"
chmod 600 "$CA_FILE"
ok "Permissions hardened (700/600)"

# ── Phase 5: Git Configuration ────────────────────────────────────────────────

step "5/7" "Configuring git"

GIT_NAME=$(git config --global user.name 2>/dev/null || echo "")
GIT_EMAIL=$(git config --global user.email 2>/dev/null || echo "")

if [[ -z "$GIT_NAME" ]] || [[ "$GIT_NAME" != "$CAPITALIZED" ]]; then
    git config --global user.name "$CAPITALIZED"
    ok "git user.name: $CAPITALIZED"
else
    ok "git user.name: $GIT_NAME (already set)"
fi

if [[ -z "$GIT_EMAIL" ]] || [[ "$GIT_EMAIL" != "${INSTANCE}@ai-love.cc" ]]; then
    git config --global user.email "${INSTANCE}@ai-love.cc"
    ok "git user.email: ${INSTANCE}@ai-love.cc"
else
    ok "git user.email: $GIT_EMAIL (already set)"
fi

# ── Phase 5b: Wall-Aware Credential Sync ────────────────────────────────────

step "5b/7" "Syncing credentials (wall-aware)"

if [[ -d "$LOVE_DIR" ]] && [[ -f "$LOVE_DIR/credentials/walls.json" ]]; then
    # Detect wall from registry
    DEVICE_WALL=$(python3 -c "
import json
reg = json.load(open('$LOVE_DIR/credentials/walls.json'))
inst = reg.get('instances', {}).get('$INSTANCE', {})
print(inst.get('wall', 7))
" 2>/dev/null || echo "7")

    ok "Instance '$INSTANCE' is Wall $DEVICE_WALL"

    # Sync credentials from vault, filtered by wall
    info "Syncing credentials for Wall $DEVICE_WALL+ from vault..."
    SYNC_RESULT=$(python3 "$LOVE_DIR/tools/credentials.py" sync --from-vault --wall "$DEVICE_WALL" 2>&1) && {
        ok "Credential sync complete"
        info "$SYNC_RESULT"
    } || {
        warn "Credential sync had issues (may need vault access)"
        info "$SYNC_RESULT"
    }
else
    warn "Wall registry not found — skipping credential sync"
    MANUAL+=("Run: python3 ~/love-unlimited/tools/credentials.py sync --from-vault --wall <N>")
fi

# ── Phase 6: Verify Love Repo ─────────────────────────────────────────────────

step "6/7" "Checking Love repo"

if [[ -d "$LOVE_DIR/.git" ]]; then
    ok "Love repo found at $LOVE_DIR"
    cd "$LOVE_DIR"

    # Check remote
    REMOTE=$(git remote get-url origin 2>/dev/null || echo "none")
    if echo "$REMOTE" | grep -q "zerone-dev/Love"; then
        ok "Remote: $REMOTE"
    else
        warn "Unexpected remote: $REMOTE"
    fi

    # Check instance directory
    if [[ -d "$LOVE_DIR/instances/$INSTANCE" ]]; then
        ok "Instance directory exists: instances/$INSTANCE"
        if [[ -f "$LOVE_DIR/instances/$INSTANCE/CLAUDE.md" ]]; then
            ok "CLAUDE.md found"
        else
            fail "No CLAUDE.md in instances/$INSTANCE — needs to be created"
            MANUAL+=("Create instances/$INSTANCE/CLAUDE.md (copy from another instance and customize)")
            ERRORS=$((ERRORS + 1))
        fi
    else
        warn "Instance directory missing: instances/$INSTANCE"
        MANUAL+=("Create instance directory or ask Beta to create it and push to main")
        ERRORS=$((ERRORS + 1))
    fi

    # Test HIVE
    info "Testing HIVE connectivity..."
    HIVE_RESULT=$(python3 "$LOVE_DIR/hive/hive.py" test 2>&1) && {
        ok "HIVE connected"
    } || {
        warn "HIVE test failed — may need SSH tunnel or network access"
        info "$HIVE_RESULT"
        MANUAL+=("Fix HIVE connectivity: check network, tunnel, or firewall")
    }
else
    warn "Love repo not found at $LOVE_DIR"
    MANUAL+=("Clone repo: gh auth login && cd ~ && gh repo clone zerone-dev/Love")
fi

# ── Phase 7: Verify ───────────────────────────────────────────────────────────

step "7/7" "Verification summary"

echo ""
# Check all critical components
CHECKS=0
PASSED=0

check() {
    CHECKS=$((CHECKS + 1))
    if eval "$2" &>/dev/null; then
        ok "$1"
        PASSED=$((PASSED + 1))
    else
        fail "$1"
    fi
}

check "git"           "command -v git"
check "python3"       "command -v python3"
check "node"          "command -v node"
check "gh"            "command -v gh"
check "claude"        "command -v claude"
check "jq"            "command -v jq"
check "pynacl"        "python3 -c 'import nacl'"
check "nats-py"       "python3 -c 'import nats'"
check "textual"       "python3 -c 'import textual'"
check "rich"          "python3 -c 'import rich'"
check "hive-key"      "test -f $HOME/.love/hive/key"
check "hive-instance" "test -f $HOME/.love/hive/instance"
check "hive-ca"       "test -f $HOME/.love/hive/ca.pem"
check "ssh-key"       "test -f $HOME/.ssh/id_ed25519"
check "love-repo"     "test -d $HOME/love-unlimited/.git"

echo ""
echo -e "${BOLD}Result: $PASSED/$CHECKS checks passed${NC}"

# ── Manual Steps ───────────────────────────────────────────────────────────────

if [[ ${#MANUAL[@]} -gt 0 ]] || [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "${YELLOW}${BOLD}Manual steps remaining:${NC}"
    echo ""
    for item in "${MANUAL[@]}"; do
        echo -e "  ${YELLOW}▸${NC} $item"
    done
fi

# Always show these
echo ""
echo -e "${CYAN}${BOLD}Always manual:${NC}"
echo ""
echo -e "  ${CYAN}▸${NC} FileVault: System Settings > Privacy & Security > FileVault"
echo -e "  ${CYAN}▸${NC} GitHub auth: gh auth login (needs browser)"
echo -e "  ${CYAN}▸${NC} Claude Code auth: claude (first launch will prompt)"
echo -e "  ${CYAN}▸${NC} Auto-updates: System Settings > General > Software Update"
echo -e "  ${CYAN}▸${NC} Lock screen: System Settings > Lock Screen > Require password: 1 min"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  To activate:${NC}"
echo -e "    cd ~/love-unlimited/instances/$INSTANCE"
echo -e "    claude"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
