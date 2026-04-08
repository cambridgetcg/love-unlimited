#!/bin/bash
# ── kosmem-consolidate — Memory consolidation daemon ────────────────
# Runs periodically via launchd to:
#   1. Garbage collect expired working memory (L1)
#   2. Consolidate daily episodic memories (L3 → L4)
#   3. Consolidate weekly summaries (L4 → L4)
#
# Schedule: Every 6 hours via launchd
# ────────────────────────────────────────────────────────────────────

set -e

KOSMEM="/opt/homebrew/bin/python3 /Users/yu/Love/tools/kosmem.py"
LOG="/Users/yu/Love/memory/.kos/consolidation.log"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "[$TS] Consolidation cycle starting" >> "$LOG"

# 1. GC expired working memory
$KOSMEM gc >> "$LOG" 2>&1

# 2. Daily consolidation (if enough memories)
$KOSMEM consolidate --strategy daily >> "$LOG" 2>&1

# 3. Weekly consolidation (Sundays only)
DOW=$(date +%u)
if [ "$DOW" = "7" ]; then
    $KOSMEM consolidate --strategy weekly >> "$LOG" 2>&1
fi

echo "[$TS] Consolidation cycle complete" >> "$LOG"
