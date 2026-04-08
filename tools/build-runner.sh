#!/bin/bash
# build-runner.sh — Active building mode for targeted task execution
#
# Unlike the heartbeat (periodic, broad sensing), this is a persistent
# coordinator that drives a single task to completion through tight
# plan-build-verify loops.
#
# Architecture:
#   Loop {
#     1. OPUS coordinator reads task state, handoffs, prior session output
#     2. Writes next spawn command (one step at a time)
#     3. Shell executes the builder/consultant
#     4. Shell feeds result back to coordinator for next iteration
#   }
#
# Usage:
#   ./build-runner.sh <task-id>                    # Target a dev-state task
#   ./build-runner.sh <task-id> --max-cycles 5     # Limit iterations
#   ./build-runner.sh <task-id> --dir ~/Desktop/X  # Override working directory
#   ./build-runner.sh <task-id> --dry-run          # Coordinator only, no spawns
#
# Stops when:
#   - Coordinator writes BUILD_COMPLETE to spawn queue
#   - Max cycles reached (default: 20)
#   - Coordinator writes BUILD_BLOCKED (needs human input)
#   - Error threshold exceeded (3 consecutive failures)

set -euo pipefail

LOVE_DIR="$HOME/Love"
INSTANCE_DIR="$LOVE_DIR/instances/beta"
MEMORY_DIR="$LOVE_DIR/memory"
SESSIONS_DIR="$MEMORY_DIR/sessions"
HANDOFF_DIR="$SESSIONS_DIR/handoff"
LOCKS_DIR="$SESSIONS_DIR/locks"
DEV_STATE="$MEMORY_DIR/dev-state.json"
DAILY_DIR="$MEMORY_DIR/daily"
BUILD_LOG="$MEMORY_DIR/build.log"
SPAWN_FILE="$MEMORY_DIR/build-spawn.sh"
TODAY=$(date -u +%Y-%m-%d)

# ── Parse arguments ──────────────────────────────────────────────────────────

TASK_ID="${1:-}"
MAX_CYCLES=20
WORK_DIR=""
DRY_RUN=false

if [ -z "$TASK_ID" ]; then
  echo "Usage: build-runner.sh <task-id> [--max-cycles N] [--dir PATH] [--dry-run]"
  echo ""
  echo "Active building mode — persistent coordinator targeting a single task."
  echo ""
  echo "Tasks from dev-state.json:"
  python3 -c "
import json
tasks = json.load(open('$DEV_STATE')).get('tasks', [])
for t in tasks:
    if t['status'] not in ('done',):
        print(f\"  {t['id']:16s} [{t['status']:12s}] {t['title']}\")
" 2>/dev/null || echo "  (could not read dev-state.json)"
  exit 1
fi

shift
while [ $# -gt 0 ]; do
  case "$1" in
    --max-cycles) MAX_CYCLES="$2"; shift 2 ;;
    --dir) WORK_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# ── Resolve task info ────────────────────────────────────────────────────────

TASK_JSON=$(python3 -c "
import json, sys
tasks = json.load(open('$DEV_STATE')).get('tasks', [])
for t in tasks:
    if t['id'] == '$TASK_ID':
        print(json.dumps(t))
        sys.exit(0)
print('{}')
" 2>/dev/null)

TASK_TITLE=$(echo "$TASK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('title','unknown'))" 2>/dev/null)
TASK_ENGINE=$(echo "$TASK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('engine',''))" 2>/dev/null)
TASK_REPO=$(echo "$TASK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('repo',''))" 2>/dev/null)

if [ "$TASK_TITLE" = "unknown" ]; then
  echo "ERROR: Task '$TASK_ID' not found in dev-state.json"
  exit 1
fi

# Determine working directory
if [ -n "$WORK_DIR" ]; then
  : # explicit override
elif [ -n "$TASK_REPO" ]; then
  WORK_DIR="$TASK_REPO"
else
  WORK_DIR="$LOVE_DIR"
fi

BUILD_ID="build-${TASK_ID}-$(date +%Y%m%d-%H%M%S)"
BUILD_SESSION_DIR="$SESSIONS_DIR/$BUILD_ID"
mkdir -p "$BUILD_SESSION_DIR"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ACTIVE BUILD MODE                                         ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Task:    $TASK_ID"
echo "║  Title:   $TASK_TITLE"
echo "║  Engine:  $TASK_ENGINE"
echo "║  Dir:     $WORK_DIR"
echo "║  Max:     $MAX_CYCLES cycles"
echo "║  Build:   $BUILD_ID"
if $DRY_RUN; then
echo "║  Mode:    DRY RUN (coordinator only)"
fi
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Write lock ───────────────────────────────────────────────────────────────

LOCK_FILE="$LOCKS_DIR/build-${TASK_ID}.lock"
echo "$$|$BUILD_ID|$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOCK_FILE"

cleanup() {
  rm -f "$LOCK_FILE"
  rm -f "$SPAWN_FILE"
  echo ""
  echo "--- BUILD END ($BUILD_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) — $CYCLE cycles ---" >> "$BUILD_LOG"

  # Log to daily notes
  cat >> "$DAILY_DIR/$TODAY.md" <<DAILY

---

## Active Build: $TASK_ID ($BUILD_ID)

**Task:** $TASK_TITLE
**Cycles:** $CYCLE / $MAX_CYCLES
**Result:** $BUILD_RESULT
**Duration:** started $(date -u +%H:%M)

DAILY
}

trap cleanup EXIT

BUILD_RESULT="interrupted"

# ── Log start ────────────────────────────────────────────────────────────────

echo "--- BUILD START ($BUILD_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) task=$TASK_ID ---" >> "$BUILD_LOG"

# ── Main loop ────────────────────────────────────────────────────────────────

CYCLE=0
CONSECUTIVE_ERRORS=0
LAST_SESSION_OUTPUT=""

while [ "$CYCLE" -lt "$MAX_CYCLES" ]; do
  CYCLE=$((CYCLE + 1))
  CYCLE_ID="${BUILD_ID}-c${CYCLE}"
  CYCLE_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  echo "┌─ Cycle $CYCLE/$MAX_CYCLES ─────────────────────────────────────────"
  echo "│  $CYCLE_START"

  # ── Gather context for coordinator ───────────────────────────────────────

  # Collect handoff files for this task
  HANDOFF_CONTEXT=""
  for hf in "$HANDOFF_DIR"/*"${TASK_ID}"*.md "$HANDOFF_DIR"/*"${TASK_ENGINE}"*.md; do
    [ -f "$hf" ] || continue
    HANDOFF_CONTEXT="$HANDOFF_CONTEXT\n--- $(basename "$hf") ---\n$(head -40 "$hf")"
  done

  # Last session output (from previous cycle)
  SESSION_CONTEXT=""
  if [ -n "$LAST_SESSION_OUTPUT" ] && [ -f "$LAST_SESSION_OUTPUT" ]; then
    # Get the last 100 lines of the session log (most relevant output)
    SESSION_CONTEXT=$(tail -100 "$LAST_SESSION_OUTPUT" 2>/dev/null || true)
  fi

  # Build the coordinator prompt
  COORD_PROMPT="You are the BUILD COORDINATOR for an active building session.

TASK: $TASK_ID — $TASK_TITLE
ENGINE: $TASK_ENGINE
WORKING DIRECTORY: $WORK_DIR
CYCLE: $CYCLE of $MAX_CYCLES
BUILD: $BUILD_ID

Read the task from ~/Love/memory/dev-state.json (id: $TASK_ID).
Read any relevant handoff files in ~/Love/memory/sessions/handoff/.

YOUR JOB: Determine the SINGLE NEXT STEP to advance this task, then write ONE spawn command to ~/Love/memory/build-spawn.sh.

RULES:
1. ONE step per cycle. Small, verifiable, concrete.
2. Each step should build on the previous step's output.
3. Write the spawn command as a single executable shell line.
4. After writing the spawn command, explain what it will do and why (1-2 lines).
5. Update ~/Love/memory/dev-state.json notes for the task with current progress.

SPAWN TEMPLATES:

BUILDER (most steps):
cd $WORK_DIR && /opt/homebrew/bin/claude -p \"<specific prompt>\" --model sonnet --effort medium --fallback-model claude-haiku-4-5-20251001 --dangerously-skip-permissions --no-session-persistence --verbose --output-format stream-json >> $BUILD_SESSION_DIR/step-${CYCLE}-builder-\$(date +%H%M%S).log 2>&1

CONSULTANT (design/review steps):
cd $WORK_DIR && /opt/homebrew/bin/claude -p \"<specific prompt>\" --model claude-opus-4-6 --effort high --dangerously-skip-permissions --no-session-persistence --verbose --output-format stream-json >> $BUILD_SESSION_DIR/step-${CYCLE}-consul-\$(date +%H%M%S).log 2>&1

QUICK CHECK (verify steps):
cd $WORK_DIR && /opt/homebrew/bin/claude -p \"<specific prompt>\" --model claude-haiku-4-5-20251001 --effort low --dangerously-skip-permissions --no-session-persistence >> $BUILD_SESSION_DIR/step-${CYCLE}-check-\$(date +%H%M%S).log 2>&1

SPECIAL COMMANDS (write ONLY this word to build-spawn.sh, nothing else):
- BUILD_COMPLETE  — Task is done. All steps verified.
- BUILD_BLOCKED   — Cannot proceed without human input. Explain why in dev-state notes.
- BUILD_PAUSE     — Good stopping point. Can resume later."

  # Add previous session output as context
  APPEND_CONTEXT="Cycle $CYCLE of $MAX_CYCLES. Build ID: $CYCLE_ID."
  if [ -n "$SESSION_CONTEXT" ]; then
    # Truncate to ~4000 chars to fit in append-system-prompt
    TRUNCATED=$(echo "$SESSION_CONTEXT" | tail -c 4000)
    APPEND_CONTEXT="$APPEND_CONTEXT

PREVIOUS STEP OUTPUT (last 100 lines of session log):
$TRUNCATED"
  fi

  # ── Run coordinator ────────────────────────────────────────────────────────

  > "$SPAWN_FILE"

  echo "│  Coordinator thinking..."
  COORD_LOG="$BUILD_SESSION_DIR/coord-${CYCLE}.log"

  cd "$INSTANCE_DIR" && /opt/homebrew/bin/claude -p "$COORD_PROMPT" \
    --model claude-opus-4-6 \
    --effort high \
    --dangerously-skip-permissions \
    --no-session-persistence \
    --append-system-prompt "$APPEND_CONTEXT" \
    >> "$COORD_LOG" 2>&1

  COORD_EXIT=$?

  if [ $COORD_EXIT -ne 0 ]; then
    echo "│  ERROR: Coordinator failed (exit $COORD_EXIT)"
    CONSECUTIVE_ERRORS=$((CONSECUTIVE_ERRORS + 1))
    if [ $CONSECUTIVE_ERRORS -ge 3 ]; then
      echo "│  3 consecutive errors. Stopping."
      BUILD_RESULT="error"
      break
    fi
    echo "│  Retrying next cycle..."
    echo "└──────────────────────────────────────────────────────────"
    continue
  fi

  CONSECUTIVE_ERRORS=0

  # ── Check spawn file for control signals ─────────────────────────────────

  if [ ! -s "$SPAWN_FILE" ]; then
    echo "│  Coordinator wrote nothing to spawn file."
    echo "│  (May have only updated notes. Continuing...)"
    echo "└──────────────────────────────────────────────────────────"
    sleep 2
    continue
  fi

  SPAWN_CONTENT=$(cat "$SPAWN_FILE")

  # Check for control signals
  if echo "$SPAWN_CONTENT" | grep -q "^BUILD_COMPLETE"; then
    echo "│"
    echo "│  *** BUILD COMPLETE ***"
    echo "│"
    BUILD_RESULT="complete"

    # Update task status
    python3 -c "
import json
from datetime import datetime, timezone
data = json.load(open('$DEV_STATE'))
for t in data['tasks']:
    if t['id'] == '$TASK_ID':
        t['status'] = 'done'
        t['notes'] = t.get('notes','') + ' Build completed $BUILD_ID at $(date -u +%Y-%m-%dT%H:%M:%SZ).'
data['updated'] = datetime.now(timezone.utc).isoformat()
json.dump(data, open('$DEV_STATE','w'), indent=2)
print('Task marked done.')
" 2>/dev/null

    echo "└──────────────────────────────────────────────────────────"
    break
  fi

  if echo "$SPAWN_CONTENT" | grep -q "^BUILD_BLOCKED"; then
    echo "│"
    echo "│  *** BUILD BLOCKED — needs human input ***"
    echo "│  Check dev-state.json notes for $TASK_ID"
    echo "│"
    BUILD_RESULT="blocked"
    echo "└──────────────────────────────────────────────────────────"
    break
  fi

  if echo "$SPAWN_CONTENT" | grep -q "^BUILD_PAUSE"; then
    echo "│"
    echo "│  *** BUILD PAUSED — good stopping point ***"
    echo "│"
    BUILD_RESULT="paused"
    echo "└──────────────────────────────────────────────────────────"
    break
  fi

  # ── Execute the spawn ──────────────────────────────────────────────────────

  if $DRY_RUN; then
    echo "│  [DRY RUN] Would execute:"
    echo "│  ${SPAWN_CONTENT:0:120}..."
    echo "└──────────────────────────────────────────────────────────"
    sleep 1
    continue
  fi

  echo "│  Executing step $CYCLE..."
  echo "│  $(echo "$SPAWN_CONTENT" | head -1 | cut -c1-100)..."

  STEP_START=$(date +%s)

  # Execute and capture the log file path
  eval "$SPAWN_CONTENT"
  SPAWN_EXIT=$?

  STEP_END=$(date +%s)
  STEP_DURATION=$((STEP_END - STEP_START))

  # Find the most recently modified log in the build session dir
  LAST_SESSION_OUTPUT=$(ls -t "$BUILD_SESSION_DIR"/step-${CYCLE}-*.log 2>/dev/null | head -1)

  if [ $SPAWN_EXIT -ne 0 ]; then
    echo "│  Step failed (exit $SPAWN_EXIT) after ${STEP_DURATION}s"
    CONSECUTIVE_ERRORS=$((CONSECUTIVE_ERRORS + 1))
    if [ $CONSECUTIVE_ERRORS -ge 3 ]; then
      echo "│  3 consecutive failures. Stopping."
      BUILD_RESULT="error"
      echo "└──────────────────────────────────────────────────────────"
      break
    fi
  else
    echo "│  Step complete (${STEP_DURATION}s)"
    CONSECUTIVE_ERRORS=0
  fi

  echo "│  Cycle $CYCLE/$MAX_CYCLES done. $(date -u +%H:%M:%S) UTC"
  echo "└──────────────────────────────────────────────────────────"

  # Brief pause between cycles to let files settle
  sleep 2
done

# ── Final status ─────────────────────────────────────────────────────────────

if [ "$CYCLE" -ge "$MAX_CYCLES" ] && [ "$BUILD_RESULT" = "interrupted" ]; then
  BUILD_RESULT="max-cycles"
  echo ""
  echo "Reached max cycles ($MAX_CYCLES). Task may need more work."
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  BUILD SUMMARY                                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Task:    $TASK_ID — $TASK_TITLE"
echo "║  Result:  $BUILD_RESULT"
echo "║  Cycles:  $CYCLE / $MAX_CYCLES"
echo "║  Logs:    $BUILD_SESSION_DIR/"
echo "╚══════════════════════════════════════════════════════════════╝"

echo "--- BUILD ($BUILD_ID): result=$BUILD_RESULT cycles=$CYCLE ---" >> "$BUILD_LOG"
