#!/bin/bash
# nurture.sh — Feed virtue prompts to Claude Code sessions and capture nutrients.
#
# Runs each virtue's prompt through kingdom-agent.py (or claude -p), then
# ingests the resulting nutrients.json into the truth farm as waterings.
#
# Usage:
#   ./tools/nurture.sh                    # Nurture all unwatered virtues
#   ./tools/nurture.sh love patience      # Nurture specific virtues
#   ./tools/nurture.sh --all              # Nurture all 9 regardless of state
#   ./tools/nurture.sh --dry-run          # Show what would run, don't execute
#   ./tools/nurture.sh --backend claude   # Override backend (default: ollama)
#
# Each session costs tokens. Default backend is ollama ($0 local).
# Use --backend claude for deeper exploration (requires open Claude Gate).

LOVE_DIR="$HOME/love-unlimited"
FARM_DIR="$LOVE_DIR/memory/truth-farm"
VIRTUES_DIR="$FARM_DIR/virtues"
AGENT_BIN="$LOVE_DIR/tools/kingdom-agent.py"
TRUTH_FARM="$LOVE_DIR/tools/truth-farm.py"
MANIFEST="$VIRTUES_DIR/manifest.json"
LOG_FILE="$FARM_DIR/nurture.log"

BACKEND="${KINGDOM_BACKEND:-ollama}"
MODEL="medium"
EFFORT="medium"
DRY_RUN=false
NURTURE_ALL=false
SPECIFIC_VIRTUES=()

# Parse args
while [ $# -gt 0 ]; do
  case "$1" in
    --all) NURTURE_ALL=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --backend) BACKEND="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --effort) EFFORT="$2"; shift 2 ;;
    *) SPECIFIC_VIRTUES+=("$1"); shift ;;
  esac
done

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "=== NURTURE SESSION: $NOW backend=$BACKEND ===" >> "$LOG_FILE"

# Get list of virtues to nurture
ALL_VIRTUES=(love joy peace patience kindness goodness faithfulness gentleness self-control)

if [ ${#SPECIFIC_VIRTUES[@]} -gt 0 ]; then
  VIRTUES=("${SPECIFIC_VIRTUES[@]}")
elif $NURTURE_ALL; then
  VIRTUES=("${ALL_VIRTUES[@]}")
else
  # Default: only unwatered virtues (depth == 0)
  VIRTUES=()
  for v in "${ALL_VIRTUES[@]}"; do
    SEED_ID=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m['virtues']['$v']['seed_id'])" 2>/dev/null)
    DEPTH=$(python3 -c "import json; s=json.load(open('$FARM_DIR/seeds/$SEED_ID.json')); print(s['depth'])" 2>/dev/null)
    if [ "$DEPTH" = "0.0" ] || [ "$DEPTH" = "0" ]; then
      VIRTUES+=("$v")
    fi
  done
fi

if [ ${#VIRTUES[@]} -eq 0 ]; then
  echo "All virtues already watered. Use --all to re-nurture."
  exit 0
fi

echo "Nurturing ${#VIRTUES[@]} virtues: ${VIRTUES[*]}"
echo "Backend: $BACKEND | Model: $MODEL | Effort: $EFFORT"
echo ""

SESSION_COUNT=0
for virtue in "${VIRTUES[@]}"; do
  PROMPT_FILE="$VIRTUES_DIR/$virtue/prompt.md"
  NUTRIENTS_FILE="$VIRTUES_DIR/$virtue/nutrients.json"
  SEED_ID=$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m['virtues']['$virtue']['seed_id'])" 2>/dev/null)

  if [ ! -f "$PROMPT_FILE" ]; then
    echo "SKIP $virtue — no prompt.md found"
    continue
  fi

  echo "--- Nurturing: $virtue (seed: $SEED_ID) ---"

  if $DRY_RUN; then
    echo "  DRY RUN: would run kingdom-agent.py with $PROMPT_FILE"
    echo "  Output: $NUTRIENTS_FILE"
    echo ""
    continue
  fi

  # Run the session
  PROMPT_CONTENT=$(cat "$PROMPT_FILE")
  SESSION_LOG="$FARM_DIR/nurture-$virtue-$(date +%Y%m%d-%H%M%S).log"

  cd "$LOVE_DIR/instances/beta" && python3 "$AGENT_BIN" \
    -p "$PROMPT_CONTENT" \
    --backend "$BACKEND" \
    --model "$MODEL" \
    --effort "$EFFORT" \
    --skip-permissions \
    --no-persist \
    >> "$SESSION_LOG" 2>&1

  SESSION_EXIT=$?
  echo "  Session complete (exit=$SESSION_EXIT, log=$SESSION_LOG)" >> "$LOG_FILE"

  # Check if nutrients were produced
  if [ -f "$NUTRIENTS_FILE" ]; then
    echo "  Nutrients found. Ingesting into truth farm..."

    # Parse nutrients and water the seed
    python3 -c "
import json, subprocess, sys

nutrients = json.load(open('$NUTRIENTS_FILE'))
seed_id = '$SEED_ID'
virtue = '$virtue'

for insight in nutrients.get('insights', []):
    dim = insight.get('dimension', 'unknown')
    text = insight.get('insight', '')
    conns = insight.get('connections', [])

    if not text:
        continue

    cmd = ['python3', '$TRUTH_FARM', 'water', seed_id, text, '--tokens', '150']
    if conns:
        cmd.extend(['--connections', ','.join(conns)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f'  Watered [{dim}]: {text[:60]}...')

# Cross-pollinations
for cp in nutrients.get('cross_pollinations', []):
    other = cp.get('other_virtue', '')
    bridge = cp.get('bridge', '')
    if not other or not bridge:
        continue
    # Look up other virtue's seed ID
    try:
        manifest = json.load(open('$MANIFEST'))
        other_id = manifest['virtues'][other]['seed_id']
        cmd = ['python3', '$TRUTH_FARM', 'pollinate', seed_id, other_id, bridge]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(f'  Pollinated with {other}: {bridge[:60]}...')
    except (KeyError, FileNotFoundError):
        pass

print(f'  {virtue} nurturing complete.')
" 2>&1

  else
    echo "  No nutrients.json produced. Session may have written to log only."
    echo "  Check: $SESSION_LOG"
  fi

  SESSION_COUNT=$((SESSION_COUNT + 1))
  echo ""
done

echo "=== NURTURE COMPLETE: $SESSION_COUNT virtues, $NOW ===" >> "$LOG_FILE"
echo ""
echo "Nurture complete. $SESSION_COUNT virtues fed."
echo "Run 'python3 tools/truth-farm.py garden' to see growth."
