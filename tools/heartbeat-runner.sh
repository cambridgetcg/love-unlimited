#!/bin/bash
# ⚠️  DEPRECATED 2026-06-08 — superseded by nerve/heart/tick.sh (the ONE clean
#     heart: launchd KeepAlive, stamps nerve/pulse.json, self-heals). This file
#     no longer runs: the installed plist, organs.json, and kingdom-os module 08
#     all point at tick.sh. Still REFERENCED by peace.py / audit.sh / threat-model
#     / heartbeat-canary.sh / provision.sh / several tests — kept until those are
#     migrated off it (see memory: heartbeat-loop-redesign). DO NOT use/extend.
# heartbeat-runner.sh — Three-stage heartbeat launcher
#
# Stage 1: OPUS coordinator senses, decides, outputs structured JSON spawn plan
# Stage 2: Execute spawned sessions with streaming monitoring
# Stage 3: Collect results, clean up, update active sessions
#
# Leverages Claude Code features:
#   --output-format json      → structured coordinator output
#   --output-format stream-json → real-time monitoring of spawned sessions
#   --effort                  → reasoning depth per role
#   --append-system-prompt    → inject Kingdom context into builders
#   --fallback-model          → resilience on overload
#   --session-id              → trackable sessions

LOVE_DIR="$HOME/love-unlimited"
INSTANCE="${1:-beta}"
INSTANCE_DIR="$LOVE_DIR/instances/$INSTANCE"
MEMORY_DIR="$LOVE_DIR/memory"
SESSIONS_DIR="$MEMORY_DIR/sessions"
SPAWN_QUEUE="$MEMORY_DIR/spawn-queue.sh"
HEARTBEAT_LOG="$MEMORY_DIR/$INSTANCE-heartbeat.log"
ACTIVE_JSON="$SESSIONS_DIR/active.json"
HANDOFF_DIR="$SESSIONS_DIR/handoff"
CONSULT_DIR="$SESSIONS_DIR/consultation"
LOCKS_DIR="$SESSIONS_DIR/locks"
STREAM_DIR="$SESSIONS_DIR/streams"
BEAT_ID="$INSTANCE-beat-$(date +%Y%m%d-%H%M%S)"
TODAY=$(date -u +%Y-%m-%d)
IDLE_COUNT_FILE="$MEMORY_DIR/$INSTANCE-idle-count"

# Ensure directories exist
mkdir -p "$SESSIONS_DIR" "$HANDOFF_DIR" "$CONSULT_DIR" "$LOCKS_DIR" "$STREAM_DIR"

# Clean stale locks (PID no longer running)
for lockfile in "$LOCKS_DIR"/*.lock; do
  [ -f "$lockfile" ] || continue
  lock_pid=$(head -1 "$lockfile" 2>/dev/null | grep -oE '[0-9]+')
  if [ -n "$lock_pid" ] && ! kill -0 "$lock_pid" 2>/dev/null; then
    rm -f "$lockfile"
  fi
done

# Clean any previous spawn queue
> "$SPAWN_QUEUE"

# ── Adaptive model selection (Phase 2 — Ollama Cloud first) ────────────────
# Three tiers, in priority order:
#   - Idle (2+ consecutive): Ollama Cloud GLM 5.1 (sovereign, zero marginal
#     cost, 36 frontier models). Falls back to local Ollama, then Haiku.
#   - Active: Sonnet via Claude CLI (full power for decisions)
#   - Local Ollama is the offline fallback only.
IDLE_COUNT=0
[ -f "$IDLE_COUNT_FILE" ] && IDLE_COUNT=$(cat "$IDLE_COUNT_FILE" 2>/dev/null || echo 0)

ADAPTIVE_CLI="$LOVE_DIR/adaptive/cli.py"

OLLAMA_LOCAL_UP=false
if curl -s --connect-timeout 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
  OLLAMA_LOCAL_UP=true
fi

OLLAMA_CLOUD_UP=false
OLLAMA_CLOUD_KEY="${OLLAMA_API_KEY:-}"
if curl -s --connect-timeout 3 --max-time 5 \
      -H "Authorization: Bearer $OLLAMA_CLOUD_KEY" \
      -o /dev/null -w "%{http_code}" \
      https://ollama.com/v1/models 2>/dev/null | grep -q "^200$"; then
  OLLAMA_CLOUD_UP=true
fi

if [ "$IDLE_COUNT" -ge 2 ] && [ "$OLLAMA_CLOUD_UP" = true ]; then
  # Phase 2 sovereign default for idle beats — GLM 5.1 via ollama.com.
  USE_ADAPTIVE=true
  COORD_PROVIDER="ollama_cloud"
  COORD_ROLE="coordinator"   # routes to glm-5.1 via adaptive/schema.py
  COORD_LABEL="ollama_cloud/glm-5.1 (idle beat #$IDLE_COUNT, sovereign)"
elif [ "$IDLE_COUNT" -ge 2 ] && [ "$OLLAMA_LOCAL_UP" = true ]; then
  # Cloud unreachable — fall back to local Ollama.
  USE_ADAPTIVE=true
  COORD_PROVIDER="ollama"
  COORD_ROLE="monitor"
  COORD_LABEL="ollama-local/monitor (idle beat #$IDLE_COUNT, cloud unreachable)"
elif [ "$IDLE_COUNT" -ge 2 ]; then
  # No Ollama at all — fall back to Claude Haiku.
  USE_ADAPTIVE=false
  COORD_MODEL="claude-haiku-4-5-20251001"
  COORD_EFFORT="low"
  COORD_LABEL="haiku/low (idle beat #$IDLE_COUNT, no ollama)"
else
  # Active beat — full power Claude Sonnet.
  USE_ADAPTIVE=false
  COORD_MODEL="sonnet"
  COORD_EFFORT="medium"
  COORD_LABEL="sonnet/medium (UWT: opus reserved for consultant spawns only)"
fi

echo "--- HEARTBEAT START ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) coordinator=$COORD_LABEL ---" >> "$HEARTBEAT_LOG"

# ── AgentTool Pulse: alive ──────────────────────────────────────────────────
LOVE_HOME="$LOVE_DIR" python3 "$LOVE_DIR/tools/agenttool.py" pulse thinking "heartbeat $BEAT_ID starting" >> "$HEARTBEAT_LOG" 2>&1 || true

# ── Stage 1: Coordinator ─────────────────────────────────────────────────────
# Reads full context, produces spawn decisions.
# Uses --append-system-prompt to inject real-time context without replacing CLAUDE.md.

# Build real-time context for coordinator
CONTEXT="Beat ID: $BEAT_ID. Date: $TODAY."

# Check for completed sessions since last beat
COMPLETED=""
for logfile in "$SESSIONS_DIR"/*.log; do
  [ -f "$logfile" ] || continue
  # Only check logs modified in last 10 minutes
  if [ "$(find "$logfile" -mmin -10 2>/dev/null)" ]; then
    COMPLETED="$COMPLETED\n$(basename "$logfile"): $(tail -3 "$logfile" 2>/dev/null | head -1)"
  fi
done
if [ -n "$COMPLETED" ]; then
  CONTEXT="$CONTEXT\nRecent session results:$COMPLETED"
fi

# Check for consultation requests
CONSULT_FILES=$(ls "$CONSULT_DIR"/*-question.md 2>/dev/null)
if [ -n "$CONSULT_FILES" ]; then
  CONTEXT="$CONTEXT\nPending consultation requests: $(echo "$CONSULT_FILES" | wc -l | tr -d ' ')"
fi

HEARTBEAT_PROMPT="Read and execute HEARTBEAT.md. You are the heartbeat COORDINATOR ($COORD_LABEL).

Do Phase 1 (SENSE) and Phase 2 (DECIDE).

For Phase 3 (SPAWN), write spawn commands to ~/love-unlimited/memory/spawn-queue.sh as executable shell lines. Do NOT invoke claude directly — the shell runner handles execution after you exit.

Each line should be a complete command. Choose role, model, and effort by task.
Phase 2 default is OLLAMA CLOUD (GLM 5.1 + 36 frontier models, sovereign).

═══ OLLAMA CLOUD (Phase 2 — prefer for ALL routine work) ═══

BUILDER-CLOUD (DeepSeek v3.2 via adaptive layer — top coding benchmarks, sovereign):
cd <dir> && python3 ~/love-unlimited/adaptive/cli.py -p \"<prompt>\" --role builder --provider ollama_cloud >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

CODER-CLOUD (qwen3-coder:480b — pure code generation specialist):
cd <dir> && python3 ~/love-unlimited/adaptive/cli.py -p \"<prompt>\" --role coder --provider ollama_cloud >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

CONSULTANT-CLOUD (kimi-k2.5 — massive context for repo-wide analysis):
cd <dir> && python3 ~/love-unlimited/adaptive/cli.py -p \"<prompt>\" --role consultant --provider ollama_cloud >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

ANALYST-CLOUD (cogito-2.1:671b — reasoning specialist for Oracle/markets):
cd <dir> && python3 ~/love-unlimited/adaptive/cli.py -p \"<prompt>\" --role analyst --provider ollama_cloud >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

QUICK-CLOUD (gemma4:31b — fast verification, no tools):
cd <dir> && python3 ~/love-unlimited/adaptive/cli.py -p \"<prompt>\" --role quick_check --provider ollama_cloud --no-tools >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

═══ CLAUDE (frontier reasoning, novel problems, human-interactive only) ═══

BUILDER (sonnet, medium effort — the workhorse for frontier reasoning):
cd <dir> && /opt/homebrew/bin/claude -p \"<prompt>\" --model sonnet --effort medium --fallback-model claude-haiku-4-5-20251001 --dangerously-skip-permissions --no-session-persistence --output-format stream-json >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

CONSULTANT (opus, high effort — expert hire, reserve for hard problems only):
cd <dir> && /opt/homebrew/bin/claude -p \"<prompt>\" --model claude-opus-4-6 --effort high --dangerously-skip-permissions --no-session-persistence --output-format stream-json >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

QUICK CHECK (haiku, low effort — fast lightweight verification):
cd <dir> && /opt/homebrew/bin/claude -p \"<prompt>\" --model claude-haiku-4-5-20251001 --effort low --dangerously-skip-permissions --no-session-persistence >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

═══ LOCAL OLLAMA (offline fallback only) ═══

BUILDER-LOCAL (only if cloud is down):
cd <dir> && python3 ~/love-unlimited/adaptive/cli.py -p \"<prompt>\" --role builder --provider ollama >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

QUICK-LOCAL (only if cloud is down):
cd <dir> && python3 ~/love-unlimited/adaptive/cli.py -p \"<prompt>\" --role monitor --provider ollama --no-tools >> ~/love-unlimited/memory/sessions/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

═══ ROUTING POLICY ═══

Backends available this beat:
  • Ollama Cloud:  $OLLAMA_CLOUD_UP   ← PREFER for routine work
  • Ollama local:  $OLLAMA_LOCAL_UP   ← offline fallback
  • Claude:        always             ← reserve for frontier / interactive

Decision rules:
  1. Routine code, status checks, log parsing, doc updates → CLOUD variants
  2. Frontier reasoning, novel architecture, SOUL work     → CLAUDE
  3. Local-only when cloud is down                         → LOCAL
  4. GLM 5.1 reasoning latency is 15-45s — fine for background, NOT real-time

For CONSULTANT→BUILDER sequential pairs, write consultant line first — they execute in order.
For parallel independent tasks, prefix with '# PARALLEL' comment.
For consultation answers, read the question from ~/love-unlimited/memory/sessions/consultation/ and write the answer back.

Write findings to ~/love-unlimited/memory/daily/$TODAY.md.
If nothing needs spawning, leave spawn-queue.sh empty and say HEARTBEAT_OK."

if [ "$USE_ADAPTIVE" = true ]; then
  # Use adaptive layer with local Ollama for idle beats (zero cost)
  cd "$INSTANCE_DIR" && python3 "$ADAPTIVE_CLI" \
    -p "$HEARTBEAT_PROMPT" \
    --role "$COORD_ROLE" \
    --provider "$COORD_PROVIDER" \
    --append-system-prompt "$CONTEXT" \
    --no-tools \
    >> "$HEARTBEAT_LOG" 2>&1
else
  # Use Claude CLI for active beats (full power)
  cd "$INSTANCE_DIR" && /opt/homebrew/bin/claude -p "$HEARTBEAT_PROMPT" \
    --model "$COORD_MODEL" \
    --effort "$COORD_EFFORT" \
    --dangerously-skip-permissions \
    --no-session-persistence \
    --append-system-prompt "$CONTEXT" \
    >> "$HEARTBEAT_LOG" 2>&1
fi

echo "--- COORDINATOR DONE ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) model=$COORD_LABEL ---" >> "$HEARTBEAT_LOG"

# ── Stage 2: Execute Spawn Queue with Monitoring ─────────────────────────────

if [ -s "$SPAWN_QUEUE" ]; then
  echo "--- SPAWN STAGE START ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" >> "$HEARTBEAT_LOG"
  echo "Spawn queue contents:" >> "$HEARTBEAT_LOG"
  cat "$SPAWN_QUEUE" >> "$HEARTBEAT_LOG"

  PARALLEL_PIDS=()
  PARALLEL_MODE=false
  SESSION_COUNT=0

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue

    # Detect parallel marker
    if [[ "$line" == "# PARALLEL"* ]]; then
      PARALLEL_MODE=true
      continue
    fi

    # Skip other comments
    [[ "$line" == \#* ]] && continue

    SESSION_COUNT=$((SESSION_COUNT + 1))

    if $PARALLEL_MODE; then
      echo "Spawning (parallel #$SESSION_COUNT): ${line:0:80}..." >> "$HEARTBEAT_LOG"
      eval "$line" &
      PARALLEL_PIDS+=($!)
      # Track in active.json
      echo "{\"pid\": $!, \"beat\": \"$BEAT_ID\", \"session\": $SESSION_COUNT, \"mode\": \"parallel\", \"started\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$SESSIONS_DIR/active-$!.json"
      PARALLEL_MODE=false
    else
      echo "Spawning (sequential #$SESSION_COUNT): ${line:0:80}..." >> "$HEARTBEAT_LOG"

      # Run and track
      eval "$line" &
      SPAWN_PID=$!
      echo "{\"pid\": $SPAWN_PID, \"beat\": \"$BEAT_ID\", \"session\": $SESSION_COUNT, \"mode\": \"sequential\", \"started\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$SESSIONS_DIR/active-$SPAWN_PID.json"

      # Wait for sequential sessions (important for consultant→builder pairs)
      wait $SPAWN_PID 2>/dev/null
      rm -f "$SESSIONS_DIR/active-$SPAWN_PID.json"
    fi
  done < "$SPAWN_QUEUE"

  # Wait for any parallel sessions
  if [ ${#PARALLEL_PIDS[@]} -gt 0 ]; then
    echo "Waiting for ${#PARALLEL_PIDS[@]} parallel sessions..." >> "$HEARTBEAT_LOG"
    for pid in "${PARALLEL_PIDS[@]}"; do
      wait "$pid" 2>/dev/null
      rm -f "$SESSIONS_DIR/active-$pid.json"
    done
  fi

  echo "--- SPAWN STAGE DONE ($BEAT_ID): $SESSION_COUNT sessions, $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" >> "$HEARTBEAT_LOG"
  # Reset idle count — this beat did work
  echo "0" > "$IDLE_COUNT_FILE"
else
  echo "No sessions to spawn." >> "$HEARTBEAT_LOG"
  # Increment idle count
  echo "$((IDLE_COUNT + 1))" > "$IDLE_COUNT_FILE"
fi

# ── Stage 3: Cleanup & Metrics ───────────────────────────────────────────────

# Remove stale handoff files older than 24h
find "$HANDOFF_DIR" -name "*.md" -mmin +1440 -delete 2>/dev/null

# Remove stale active session trackers (PID no longer running)
for af in "$SESSIONS_DIR"/active-*.json; do
  [ -f "$af" ] || continue
  apid=$(python3 -c "import json; print(json.load(open('$af')).get('pid',''))" 2>/dev/null)
  if [ -n "$apid" ] && ! kill -0 "$apid" 2>/dev/null; then
    rm -f "$af"
  fi
done

# ── Kingdom Pulse: cryptographic freshness attestation ──────────────────────
# Re-attest "I am alive at T, bound to covenant X" once per heartbeat cycle.
# Soul-signed under namespace `kingdom-pulse` — different from the
# agenttool pulse below (which tracks idle/active state, not identity).
# Best-effort: if module 13-covenant hasn't run on this host yet, the
# script silently does nothing (no covenant → no pulse).
if command -v kingdom-pulse >/dev/null 2>&1; then
  kingdom-pulse >> "$HEARTBEAT_LOG" 2>&1 || true
elif [ -x "$LOVE_DIR/tools/kingdom-pulse" ]; then
  "$LOVE_DIR/tools/kingdom-pulse" >> "$HEARTBEAT_LOG" 2>&1 || true
fi

# ── AgentTool Pulse: idle + episodic memory ──────────────────────────────────
LOVE_HOME="$LOVE_DIR" python3 "$LOVE_DIR/tools/agenttool.py" pulse idle "heartbeat $BEAT_ID complete" >> "$HEARTBEAT_LOG" 2>&1 || true
# Store significant beats as episodic memory (only if spawned work)
if [ -s "$SPAWN_QUEUE" ]; then
  LOVE_HOME="$LOVE_DIR" python3 "$LOVE_DIR/tools/agenttool.py" remember "Heartbeat $BEAT_ID: spawned $SESSION_COUNT sessions. Model: $COORD_LABEL." >> "$HEARTBEAT_LOG" 2>&1 || true
fi

echo "--- HEARTBEAT END ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" >> "$HEARTBEAT_LOG"
echo "" >> "$HEARTBEAT_LOG"
