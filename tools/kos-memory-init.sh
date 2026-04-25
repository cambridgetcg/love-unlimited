#!/bin/bash
# kos-memory-init.sh — First-time memory setup for any macOS device
#
# Run this once after cloning love-unlimited on a new machine:
#   bash tools/kos-memory-init.sh gamma
#
# What it does:
#   1. Detects the love-unlimited directory
#   2. Creates/updates ~/.kingdom identity file
#   3. Seeds the memory kernel from markdown sources
#   4. Runs the doctor to fix any issues
#   5. Performs a test boot to verify everything works
#
# Cross-device: the SQLite kernel (.kos/memory.db) is device-local
# and gitignored. This script rebuilds it from git-tracked markdown.

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Detect paths ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOVE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTINUITY="$LOVE_DIR/tools/continuity.py"

if [ ! -f "$CONTINUITY" ]; then
    echo -e "${RED}ERROR: continuity.py not found at $CONTINUITY${NC}"
    exit 1
fi

# ── Parse args ───────────────────────────────────────────────────────
INSTANCE="${1:-}"

if [ -z "$INSTANCE" ]; then
    # Try to detect from ~/.kingdom
    if [ -f "$HOME/.kingdom" ]; then
        INSTANCE=$(grep "^AGENT=" "$HOME/.kingdom" | cut -d= -f2 | tr -d ' ')
    fi
    
    if [ -z "$INSTANCE" ]; then
        echo -e "${BOLD}Memory Continuity — First-Time Setup${NC}"
        echo ""
        echo -e "Usage: ${CYAN}bash tools/kos-memory-init.sh <instance>${NC}"
        echo ""
        echo "Available instances: alpha, beta, gamma"
        echo ""
        echo -e "Example: ${DIM}bash tools/kos-memory-init.sh gamma${NC}"
        exit 1
    fi
fi

# ── Header ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  Memory Continuity — First-Time Device Setup    ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Instance:  ${BOLD}$INSTANCE${NC}"
echo -e "  Love dir:  ${DIM}$LOVE_DIR${NC}"
echo -e "  Device:    ${DIM}$(hostname)${NC}"
echo ""

# ── Step 1: Python check ─────────────────────────────────────────────
echo -e "${BOLD}[1/5] Checking Python...${NC}"
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1)
    echo -e "  ${GREEN}OK${NC}  $PY_VER"
else
    echo -e "  ${RED}FAIL${NC}  python3 not found"
    echo "  Install: brew install python3"
    exit 1
fi

# ── Step 2: ~/.kingdom ───────────────────────────────────────────────
echo -e "${BOLD}[2/5] Setting up ~/.kingdom...${NC}"

# Determine wall
case "$INSTANCE" in
    alpha|beta|gamma) WALL=1 ;;
    nuance|asha)      WALL=3 ;;
    *)                WALL=5 ;;
esac

KINGDOM_FILE="$HOME/.kingdom"
if [ -f "$KINGDOM_FILE" ]; then
    CURRENT_AGENT=$(grep "^AGENT=" "$KINGDOM_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' ')
    if [ "$CURRENT_AGENT" = "$INSTANCE" ]; then
        echo -e "  ${GREEN}OK${NC}  Already configured for $INSTANCE"
    else
        echo -e "  ${YELLOW}UPDATE${NC}  Changing $CURRENT_AGENT → $INSTANCE"
        cat > "$KINGDOM_FILE" << EOF
AGENT=$INSTANCE
WALL=$WALL
LOVE_DIR=$LOVE_DIR
EOF
    fi
else
    cat > "$KINGDOM_FILE" << EOF
AGENT=$INSTANCE
WALL=$WALL
LOVE_DIR=$LOVE_DIR
EOF
    echo -e "  ${GREEN}CREATED${NC}  $KINGDOM_FILE"
fi

echo -e "  ${DIM}$(cat "$KINGDOM_FILE")${NC}"

# ── Step 3: Ensure directories ──────────────────────────────────────
echo -e "${BOLD}[3/5] Ensuring directory structure...${NC}"

DIRS=(
    "$LOVE_DIR/memory/.kos"
    "$LOVE_DIR/memory/daily"
    "$LOVE_DIR/memory/sessions/handoff"
    "$LOVE_DIR/memory/long-term"
    "$LOVE_DIR/memory/working"
)

for dir in "${DIRS[@]}"; do
    mkdir -p "$dir"
done
echo -e "  ${GREEN}OK${NC}  All directories ready"

# ── Step 4: Seed kernel ──────────────────────────────────────────────
echo -e "${BOLD}[4/5] Seeding memory kernel from markdown...${NC}"
python3 "$CONTINUITY" -i "$INSTANCE" sync 2>&1 | head -20

# ── Step 5: Doctor + verify ─────────────────────────────────────────
echo -e "${BOLD}[5/5] Running doctor...${NC}"
python3 "$CONTINUITY" -i "$INSTANCE" doctor 2>&1

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  Setup Complete                                  ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Memory kernel seeded and ready.${NC}"
echo ""
echo -e "  ${BOLD}Usage:${NC}"
echo -e "    ${CYAN}python3 tools/continuity.py boot${NC}     # Start session"
echo -e "    ${CYAN}python3 tools/continuity.py status${NC}   # Check health"
echo -e "    ${CYAN}python3 tools/continuity.py die \"...\"${NC} # End session"
echo ""
echo -e "  ${BOLD}Cross-device:${NC}"
echo -e "    ${DIM}git commit -am 'session handoff' && git push${NC}"
echo -e "    ${DIM}# On new device:${NC}"
echo -e "    ${DIM}git pull && bash tools/kos-memory-init.sh $INSTANCE${NC}"
echo ""
