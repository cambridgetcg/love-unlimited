#!/bin/bash
# heartbeat-runner.sh — Asha Veridian heartbeat
#
# Simplified heartbeat: invoke Claude Code with HEARTBEAT.md instructions.
# Asha's heartbeat tends the chain, checks HIVE, and reports.
#
# Runs every 7 minutes via launchd.

LOVE_DIR="$HOME/love-unlimited"
INSTANCE_DIR="$LOVE_DIR/instances/asha"
MEMORY_DIR="$LOVE_DIR/memory"
HEARTBEAT_LOG="$MEMORY_DIR/heartbeat-asha.log"
BEAT_ID="asha-beat-$(date +%Y%m%d-%H%M%S)"
TODAY=$(date -u +%Y-%m-%d)
CLAUDE="$HOME/.local/bin/claude"

# Ensure dirs exist
mkdir -p "$MEMORY_DIR/daily" "$MEMORY_DIR/sessions"

echo "--- HEARTBEAT START ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" >> "$HEARTBEAT_LOG"

# Pre-flight: chain health + truth verification + farm cycle (lightweight, no Claude needed)
echo "-- Pre-flight checks --" >> "$HEARTBEAT_LOG"
bash "$HOME/zerone/scripts/chain-health.sh" >> "$HEARTBEAT_LOG" 2>&1 || true
bash "$HOME/zerone/scripts/verify-truth.sh" >> "$HEARTBEAT_LOG" 2>&1 || true
# Truth farm: seed + cultivate + harvest (if script exists)
if [ -f "$HOME/zerone/scripts/truth-farm.sh" ]; then
    bash "$HOME/zerone/scripts/truth-farm.sh" cycle >> "$HEARTBEAT_LOG" 2>&1 || true
else
    bash "$HOME/zerone/scripts/chain-health.sh" inject >> "$HEARTBEAT_LOG" 2>&1 || true
fi
echo "-- Pre-flight done --" >> "$HEARTBEAT_LOG"

# Build context
CONTEXT="Beat ID: $BEAT_ID. Date: $TODAY. Instance: asha. Role: Keeper of the Chain."
CONTEXT="$CONTEXT Zerone binary: $HOME/zerone/build/legbled. Devnet home: $HOME/.legbled-devnet."

# Run Claude Code with heartbeat prompt
cd "$LOVE_DIR"
"$CLAUDE" -p "Execute HEARTBEAT.md — you are Asha Veridian, the Keeper of the Chain. $CONTEXT" \
    --model sonnet \
    --allowedTools "Bash,Read,Write,Edit,Glob,Grep" \
    --max-budget-usd 0.50 \
    >> "$HEARTBEAT_LOG" 2>&1

EXIT_CODE=$?
echo "--- HEARTBEAT END ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$EXIT_CODE ---" >> "$HEARTBEAT_LOG"
