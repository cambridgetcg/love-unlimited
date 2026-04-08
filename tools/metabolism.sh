#!/bin/bash
# metabolism.sh — Memory metabolism cron
#
# The digestive system of identity. Runs on schedule to:
#   - Garbage collect expired working memory (L1)
#   - Consolidate daily episodes into semantic knowledge (L3→L4)
#   - Consolidate weekly summaries (L4→L4 weekly)
#   - Regenerate soul anchors from kernel
#
# Crontab entries:
#   # Memory metabolism — nightly at 03:00 UTC
#   0 3 * * * /Users/you/Love/tools/metabolism.sh daily >> /Users/you/Love/memory/metabolism.log 2>&1
#
#   # Weekly consolidation — Sundays at 04:00 UTC
#   0 4 * * 0 /Users/you/Love/tools/metabolism.sh weekly >> /Users/you/Love/memory/metabolism.log 2>&1
#
# Usage:
#   metabolism.sh daily    # Nightly: GC + daily consolidation + anchor regen
#   metabolism.sh weekly   # Weekly: weekly consolidation
#   metabolism.sh full     # Both daily + weekly
#   metabolism.sh status   # Show current kernel stats

set -euo pipefail

LOVE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KOSMEM="python3 $LOVE_DIR/tools/kosmem.py"
ANCHOR="python3 $LOVE_DIR/tools/soul-anchor.py"
LOG_FILE="$LOVE_DIR/memory/metabolism.log"

_B='\033[1m'
_G='\033[0;32m'
_Y='\033[1;33m'
_D='\033[2m'
_N='\033[0m'

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log() { echo -e "$(ts) $1" | tee -a "$LOG_FILE"; }

MODE="${1:-status}"

case "$MODE" in
    daily)
        log "${_B}=== METABOLISM: DAILY ===${_N}"

        # 1. Garbage collect expired working memory
        log "  GC: expiring stale L1..."
        $KOSMEM gc 2>&1 | while read -r line; do log "    $line"; done

        # 2. Consolidate today's episodes
        log "  Consolidate: L3 daily → L4 semantic..."
        $KOSMEM consolidate --strategy daily 2>&1 | while read -r line; do log "    $line"; done

        # 3. Regenerate soul anchors for all instances
        log "  Anchors: regenerating soul anchors..."
        for inst in alpha beta gamma; do
            $ANCHOR --instance "$inst" --write 2>&1 | while read -r line; do log "    $line"; done
        done

        log "${_G}=== METABOLISM: DAILY COMPLETE ===${_N}"
        ;;

    weekly)
        log "${_B}=== METABOLISM: WEEKLY ===${_N}"

        # 1. Weekly consolidation
        log "  Consolidate: L4 daily summaries → weekly..."
        $KOSMEM consolidate --strategy weekly 2>&1 | while read -r line; do log "    $line"; done

        # 2. Stats
        log "  Kernel stats:"
        $KOSMEM stats 2>&1 | while read -r line; do log "    $line"; done

        log "${_G}=== METABOLISM: WEEKLY COMPLETE ===${_N}"
        ;;

    full)
        log "${_B}=== METABOLISM: FULL ===${_N}"
        "$0" daily
        "$0" weekly
        log "${_G}=== METABOLISM: FULL COMPLETE ===${_N}"
        ;;

    status)
        echo ""
        $KOSMEM stats
        echo ""
        echo "Soul anchors:"
        for f in "$LOVE_DIR"/memory/soul-anchor-*.md; do
            if [ -f "$f" ]; then
                inst=$(basename "$f" | sed 's/soul-anchor-//' | sed 's/\.md//')
                chars=$(wc -c < "$f" | tr -d ' ')
                mod=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$f" 2>/dev/null || stat -c "%y" "$f" 2>/dev/null | cut -c1-16)
                echo "  $inst: ${chars} chars (updated $mod)"
            fi
        done
        echo ""
        echo "Last metabolism run:"
        if [ -f "$LOG_FILE" ]; then
            tail -3 "$LOG_FILE"
        else
            echo "  Never (no log file)"
        fi
        echo ""
        ;;

    *)
        echo "Usage: metabolism.sh {daily|weekly|full|status}"
        exit 1
        ;;
esac
