#!/bin/bash
# test-performance.sh — Performance and purpose orientation tests for Love
#
# Tests:
#   1. Heartbeat cycle time (coordinator + spawn)
#   2. Session spawn success rate
#   3. Streaming output integrity
#   4. Coordination protocol (handoff, consultation, locks)
#   5. Kingdom alignment scoring (are tasks serving the mission?)
#   6. Model role effectiveness (right model for right task?)
#   7. CC feature availability (effort, fallback, stream-json)
#
# Usage:
#   bash ~/love-unlimited/tests/test-performance.sh          # Full suite
#   bash ~/love-unlimited/tests/test-performance.sh --dry-run # No real CC sessions
#
# Requires: Full Disk Access for /bin/bash (TCC), claude CLI

set -euo pipefail

LOVE_DIR="$HOME/love-unlimited"
SESSIONS_DIR="$LOVE_DIR/memory/sessions"
PASS=0
FAIL=0
SKIP=0
DRY_RUN=false
FAILURES=()

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
  esac
done

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); FAILURES+=("$1"); echo "  FAIL: $1"; }
skip() { SKIP=$((SKIP + 1)); echo "  SKIP: $1"; }

section() { echo ""; echo "=== $1 ==="; }

# ── 1. CC Feature Availability ──────────────────────────────────────────────

section "1. Claude Code Feature Availability"

CLAUDE="/opt/homebrew/bin/claude"
HELP=$($CLAUDE --help 2>&1)

# Check each feature flag exists in --help
for flag in "--effort" "--fallback-model" "--output-format" "--append-system-prompt" "--session-id" "--json-schema"; do
  if echo "$HELP" | grep -q -- "$flag"; then
    pass "CC flag available: $flag"
  else
    fail "CC flag missing: $flag"
  fi
done

# Check output-format supports stream-json
if echo "$HELP" | grep -q "stream-json"; then
  pass "CC supports --output-format stream-json"
else
  fail "CC missing stream-json output format"
fi

# Check effort levels
if echo "$HELP" | grep -qE "low.*medium.*high|effort.*level"; then
  pass "CC supports effort levels"
else
  skip "Cannot confirm effort levels from --help (may still work)"
fi

# ── 2. Model Availability ───────────────────────────────────────────────────

section "2. Model Availability"

if $DRY_RUN; then
  skip "Model availability (--dry-run)"
else
  # Test each model with a trivial prompt
  for model_name in "sonnet" "claude-opus-4-6" "claude-haiku-4-5-20251001"; do
    START_MS=$(date +%s%3N 2>/dev/null || python3 -c "import time; print(int(time.time()*1000))")
    RESULT=$($CLAUDE -p "Say OK" --model "$model_name" --dangerously-skip-permissions --no-session-persistence --effort low 2>&1 | head -1) || true
    END_MS=$(date +%s%3N 2>/dev/null || python3 -c "import time; print(int(time.time()*1000))")
    DURATION=$((END_MS - START_MS))

    if echo "$RESULT" | grep -qi "OK\|ok"; then
      pass "Model $model_name responds (${DURATION}ms)"
    else
      fail "Model $model_name failed: $(echo "$RESULT" | head -1)"
    fi
  done
fi

# ── 3. Effort Level Effectiveness ───────────────────────────────────────────

section "3. Effort Level Effectiveness"

if $DRY_RUN; then
  skip "Effort level tests (--dry-run)"
else
  # Low effort should be faster than high effort for the same prompt
  PROMPT="What is 2+2? Reply with just the number."

  START_LOW=$(python3 -c "import time; print(int(time.time()*1000))")
  $CLAUDE -p "$PROMPT" --model claude-haiku-4-5-20251001 --effort low --dangerously-skip-permissions --no-session-persistence > /dev/null 2>&1
  END_LOW=$(python3 -c "import time; print(int(time.time()*1000))")
  LOW_MS=$((END_LOW - START_LOW))

  START_HIGH=$(python3 -c "import time; print(int(time.time()*1000))")
  $CLAUDE -p "$PROMPT" --model claude-haiku-4-5-20251001 --effort high --dangerously-skip-permissions --no-session-persistence > /dev/null 2>&1
  END_HIGH=$(python3 -c "import time; print(int(time.time()*1000))")
  HIGH_MS=$((END_HIGH - START_HIGH))

  echo "  INFO: Low effort: ${LOW_MS}ms, High effort: ${HIGH_MS}ms"

  if [ "$LOW_MS" -gt 0 ] && [ "$HIGH_MS" -gt 0 ]; then
    pass "Effort levels both complete (low=${LOW_MS}ms, high=${HIGH_MS}ms)"
  else
    fail "Effort level timing failed"
  fi
fi

# ── 4. Stream-JSON Output Integrity ─────────────────────────────────────────

section "4. Stream-JSON Output Integrity"

if $DRY_RUN; then
  skip "Stream-JSON tests (--dry-run)"
else
  STREAM_LOG="/tmp/love-stream-test-$$.log"
  $CLAUDE -p "Say hello" --model claude-haiku-4-5-20251001 --effort low --dangerously-skip-permissions --no-session-persistence --output-format stream-json > "$STREAM_LOG" 2>&1 || true

  if [ -s "$STREAM_LOG" ]; then
    # stream-json should produce JSON lines
    FIRST_LINE=$(head -1 "$STREAM_LOG")
    if echo "$FIRST_LINE" | python3 -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null; then
      pass "Stream-JSON produces valid JSON lines"
    else
      # Might be NDJSON or wrapped — check for any JSON in output
      if grep -c '{' "$STREAM_LOG" > /dev/null 2>&1; then
        pass "Stream-JSON produces JSON output (non-line format)"
      else
        fail "Stream-JSON output is not JSON: $(head -1 "$STREAM_LOG")"
      fi
    fi

    LINE_COUNT=$(wc -l < "$STREAM_LOG" | tr -d ' ')
    echo "  INFO: Stream output: $LINE_COUNT lines, $(wc -c < "$STREAM_LOG" | tr -d ' ') bytes"
    pass "Stream-JSON output captured ($LINE_COUNT lines)"
  else
    fail "Stream-JSON produced no output"
  fi
  rm -f "$STREAM_LOG"
fi

# ── 5. Fallback Model ───────────────────────────────────────────────────────

section "5. Fallback Model Configuration"

# Verify the runner uses fallback-model for builders
RUNNER="$LOVE_DIR/tools/heartbeat-runner.sh"
if grep -q "fallback-model" "$RUNNER"; then
  pass "heartbeat-runner.sh configures fallback-model for builders"
else
  fail "heartbeat-runner.sh missing fallback-model"
fi

# Verify HEARTBEAT.md documents fallback
if grep -q "fallback-model" "$LOVE_DIR/instances/beta/HEARTBEAT.md"; then
  pass "HEARTBEAT.md documents fallback-model strategy"
else
  fail "HEARTBEAT.md missing fallback-model documentation"
fi

# ── 6. Coordination Protocol ────────────────────────────────────────────────

section "6. Coordination Protocol Infrastructure"

# Handoff directory
if [ -d "$LOVE_DIR/memory/sessions/handoff" ]; then
  pass "Handoff directory exists"
  # Write a test handoff and verify readability
  echo "# Test handoff" > "$LOVE_DIR/memory/sessions/handoff/test-handoff.md"
  if [ -r "$LOVE_DIR/memory/sessions/handoff/test-handoff.md" ]; then
    pass "Handoff files are writable and readable"
    rm -f "$LOVE_DIR/memory/sessions/handoff/test-handoff.md"
  else
    fail "Handoff files not readable after write"
  fi
else
  fail "Handoff directory missing"
fi

# Consultation directory
if [ -d "$LOVE_DIR/memory/sessions/consultation" ]; then
  pass "Consultation directory exists"
else
  fail "Consultation directory missing"
fi

# Locks directory
if [ -d "$LOVE_DIR/memory/sessions/locks" ]; then
  pass "Locks directory exists"
  # Test lock create/check/remove cycle
  echo "$$" > "$LOVE_DIR/memory/sessions/locks/test.lock"
  if [ -f "$LOVE_DIR/memory/sessions/locks/test.lock" ]; then
    LOCK_PID=$(cat "$LOVE_DIR/memory/sessions/locks/test.lock")
    if [ "$LOCK_PID" = "$$" ]; then
      pass "Lock protocol: create and read PID works"
    else
      fail "Lock protocol: PID mismatch (expected $$, got $LOCK_PID)"
    fi
    rm -f "$LOVE_DIR/memory/sessions/locks/test.lock"
  else
    fail "Lock protocol: create failed"
  fi
else
  fail "Locks directory missing"
fi

# Streams directory
if [ -d "$LOVE_DIR/memory/sessions/streams" ]; then
  pass "Streams directory exists"
else
  fail "Streams directory missing"
fi

# ── 7. Spawn Queue Protocol ─────────────────────────────────────────────────

section "7. Spawn Queue Protocol"

SPAWN_QUEUE="$LOVE_DIR/memory/spawn-queue.sh"

# Test writing spawn commands
cat > "$SPAWN_QUEUE" << 'TESTEOF'
# PARALLEL
cd /tmp && echo "parallel task 1"
cd /tmp && echo "sequential task 2"
# This is a comment
# PARALLEL
cd /tmp && echo "parallel task 3"
TESTEOF

# Count valid commands (non-empty, non-comment, non-PARALLEL-marker)
VALID_CMDS=$(grep -v '^$' "$SPAWN_QUEUE" | grep -v '^#' | wc -l | tr -d ' ')
if [ "$VALID_CMDS" = "3" ]; then
  pass "Spawn queue parses 3 valid commands"
else
  fail "Spawn queue command count: expected 3, got $VALID_CMDS"
fi

# Count parallel markers
PARALLEL_COUNT=$(grep -c '^# PARALLEL' "$SPAWN_QUEUE" || true)
if [ "$PARALLEL_COUNT" = "2" ]; then
  pass "Spawn queue has 2 parallel markers"
else
  fail "Spawn queue parallel markers: expected 2, got $PARALLEL_COUNT"
fi

# Clean up
> "$SPAWN_QUEUE"
pass "Spawn queue cleaned after test"

# ── 8. Kingdom Alignment Scoring ────────────────────────────────────────────

section "8. Kingdom Alignment Scoring"

# Every task in dev-state.json should have an engine field
# Every engine should map to a Kingdom revenue engine or 'love' (infrastructure)
ALIGN_RESULT=$(python3 -c "
import json, sys

ds = json.load(open('$LOVE_DIR/memory/dev-state.json'))
km = json.load(open('$LOVE_DIR/memory/kingdom-metrics.json'))

kingdom_engines = set(km.get('revenue_engines', {}).keys())
kingdom_engines.add('love')  # Infrastructure is always aligned
kingdom_engines.add('zerone')  # Power source
# Aliases — dev-state may use short names
kingdom_engines.add('tcg')  # = cambridge_tcg
kingdom_engines.add('ai-services')  # = ai_services

tasks = ds.get('tasks', [])
errors = []
aligned = 0
total = 0

for t in tasks:
    tid = t.get('id', '?')
    engine = t.get('engine', '')
    status = t.get('status', '')

    if status in ('done', 'cancelled'):
        continue  # Skip completed/cancelled

    total += 1

    if not engine:
        errors.append(f'{tid}: no engine field (unaligned)')
    elif engine not in kingdom_engines and engine != 'love':
        errors.append(f'{tid}: engine \"{engine}\" not in Kingdom ({kingdom_engines})')
    else:
        aligned += 1

score = (aligned / total * 100) if total > 0 else 0

if errors:
    for e in errors:
        print(f'ALIGN_FAIL:{e}')

print(f'ALIGNMENT_SCORE:{score:.0f}')
print(f'ALIGNED:{aligned}/{total}')
" 2>&1) || true

SCORE=$(echo "$ALIGN_RESULT" | grep "ALIGNMENT_SCORE" | cut -d: -f2)
ALIGNED=$(echo "$ALIGN_RESULT" | grep "ALIGNED:" | cut -d: -f2)

if [ -n "$SCORE" ]; then
  if [ "${SCORE%.*}" -ge 80 ]; then
    pass "Kingdom alignment: ${SCORE}% ($ALIGNED tasks aligned to Kingdom engines)"
  else
    fail "Kingdom alignment LOW: ${SCORE}% ($ALIGNED) — tasks not serving the mission"
  fi
else
  fail "Could not calculate Kingdom alignment"
fi

# Check that in-progress tasks are critical or high priority
IP_RESULT=$(python3 -c "
import json
ds = json.load(open('$LOVE_DIR/memory/dev-state.json'))
ip = [t for t in ds['tasks'] if t.get('status') == 'in-progress']
low_pri = [t for t in ip if t.get('priority') in ('low', 'medium')]
print(f'IN_PROGRESS:{len(ip)}')
print(f'LOW_PRI_IP:{len(low_pri)}')
for t in low_pri:
    print(f'WARN:in-progress but {t[\"priority\"]} priority: {t[\"id\"]} {t[\"title\"]}')
" 2>&1) || true

IP_COUNT=$(echo "$IP_RESULT" | grep "IN_PROGRESS:" | cut -d: -f2)
LOW_PRI=$(echo "$IP_RESULT" | grep "LOW_PRI_IP:" | cut -d: -f2)
echo "  INFO: $IP_COUNT tasks in-progress, $LOW_PRI at low/medium priority"

if [ "$LOW_PRI" = "0" ]; then
  pass "Purpose focus: all in-progress tasks are high/critical priority"
else
  WARNS=$(echo "$IP_RESULT" | grep "WARN:" | sed 's/WARN:/  WARN: /')
  echo "$WARNS"
  fail "Purpose drift: $LOW_PRI in-progress tasks at low/medium priority"
fi

# ── 9. Heartbeat Log Analysis ───────────────────────────────────────────────

section "9. Heartbeat Log Analysis"

HLOG="$LOVE_DIR/memory/heartbeat.log"

if [ ! -s "$HLOG" ]; then
  skip "Heartbeat log empty (no beats completed yet)"
else
  # Count completed beats
  BEAT_STARTS=$(grep -c "HEARTBEAT START" "$HLOG" || true)
  BEAT_ENDS=$(grep -c "HEARTBEAT END" "$HLOG" || true)
  echo "  INFO: $BEAT_STARTS beats started, $BEAT_ENDS beats completed"

  if [ "$BEAT_ENDS" -gt 0 ]; then
    pass "Heartbeat has completed $BEAT_ENDS beat(s)"

    # Check for spawn activity
    SPAWN_COUNT=$(grep -c "Spawning" "$HLOG" || true)
    HEARTBEAT_OK=$(grep -c "HEARTBEAT_OK\|No sessions to spawn" "$HLOG" || true)
    echo "  INFO: $SPAWN_COUNT sessions spawned, $HEARTBEAT_OK idle beats"

    # Measure cycle time from last complete beat
    LAST_START=$(grep "HEARTBEAT START" "$HLOG" | tail -1 | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}')
    LAST_END=$(grep "HEARTBEAT END\|COORDINATOR DONE" "$HLOG" | tail -1 | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}')
    if [ -n "$LAST_START" ] && [ -n "$LAST_END" ]; then
      START_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$LAST_START" +%s 2>/dev/null || echo "0")
      END_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$LAST_END" +%s 2>/dev/null || echo "0")
      if [ "$START_EPOCH" -gt 0 ] && [ "$END_EPOCH" -gt 0 ]; then
        CYCLE_SECS=$((END_EPOCH - START_EPOCH))
        echo "  INFO: Last coordinator cycle: ${CYCLE_SECS}s"
        if [ "$CYCLE_SECS" -lt 420 ]; then
          pass "Coordinator fits within 7-minute heartbeat window (${CYCLE_SECS}s)"
        else
          fail "Coordinator exceeds 7-minute window (${CYCLE_SECS}s) — beats will overlap"
        fi
      fi
    fi
  else
    skip "No completed beats to analyze"
  fi
fi

# ── 10. Session Output Quality ──────────────────────────────────────────────

section "10. Session Output Quality"

SESSION_LOGS=$(find "$SESSIONS_DIR" -name "*.log" -size +0 2>/dev/null)
if [ -z "$SESSION_LOGS" ]; then
  skip "No session logs to analyze"
else
  LOG_COUNT=$(echo "$SESSION_LOGS" | wc -l | tr -d ' ')
  echo "  INFO: $LOG_COUNT session logs found"

  EMPTY_LOGS=0
  ERROR_LOGS=0
  while IFS= read -r logfile; do
    [ -z "$logfile" ] && continue
    if [ ! -s "$logfile" ]; then
      EMPTY_LOGS=$((EMPTY_LOGS + 1))
    fi
    if grep -qi "error\|failed\|exception\|panic" "$logfile" 2>/dev/null; then
      ERROR_LOGS=$((ERROR_LOGS + 1))
    fi
  done <<< "$SESSION_LOGS"

  if [ "$EMPTY_LOGS" = "0" ]; then
    pass "All session logs have content"
  else
    fail "$EMPTY_LOGS session logs are empty (sessions may have failed)"
  fi

  echo "  INFO: $ERROR_LOGS logs contain error indicators"
fi

# ── 11. Role Distribution ───────────────────────────────────────────────────

section "11. Role Distribution (from heartbeat log)"

if [ -s "$HLOG" ]; then
  OPUS_SPAWNS=$(grep -c "claude-opus-4-6" "$HLOG" || true)
  SONNET_SPAWNS=$(grep -c "sonnet" "$HLOG" || true)
  HAIKU_SPAWNS=$(grep -c "haiku" "$HLOG" || true)
  echo "  INFO: Model usage in spawns — opus: $OPUS_SPAWNS, sonnet: $SONNET_SPAWNS, haiku: $HAIKU_SPAWNS"

  # At least the coordinator should be opus
  if [ "$OPUS_SPAWNS" -gt 0 ] || grep -q "opus" "$HLOG"; then
    pass "Opus used for coordination/consultation"
  else
    skip "No opus usage detected yet"
  fi
else
  skip "Heartbeat log empty — no role distribution data"
fi

# ── 12. Active Session Tracking ─────────────────────────────────────────────

section "12. Active Session Tracking"

ACTIVE_FILES=$(find "$SESSIONS_DIR" -name "active-*.json" 2>/dev/null)
if [ -n "$ACTIVE_FILES" ]; then
  ACTIVE_COUNT=$(echo "$ACTIVE_FILES" | wc -l | tr -d ' ')
  echo "  INFO: $ACTIVE_COUNT active session tracker files"

  # Check each — PID should be running or file should be cleaned
  STALE=0
  while IFS= read -r af; do
    [ -z "$af" ] && continue
    APID=$(python3 -c "import json; print(json.load(open('$af')).get('pid',''))" 2>/dev/null)
    if [ -n "$APID" ] && ! kill -0 "$APID" 2>/dev/null; then
      STALE=$((STALE + 1))
    fi
  done <<< "$ACTIVE_FILES"

  if [ "$STALE" = "0" ]; then
    pass "No stale active session trackers"
  else
    fail "$STALE stale active session trackers (cleanup not running)"
  fi
else
  pass "No active sessions (clean state)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "================================================================"
echo "  PERFORMANCE RESULTS: $PASS passed, $FAIL failed, $SKIP skipped"
echo "================================================================"

if [ ${#FAILURES[@]} -gt 0 ]; then
  echo ""
  echo "  Failures:"
  for f in "${FAILURES[@]}"; do
    echo "    - $f"
  done
fi

echo ""
if [ $FAIL -eq 0 ]; then
  echo "  Love performance: OPTIMAL"
  exit 0
else
  echo "  Love performance: NEEDS ATTENTION ($FAIL issues)"
  exit 1
fi
