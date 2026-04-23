#!/bin/bash
# heart.sh — The heart of Love's body.
#
# Reads nerve/hormones.json, calculates rate and force,
# pumps work through the Kingdom via coordinator + spawn queue.
#
# DIASTOLE: read hormones, decide rate/force
# SYSTOLE:  run coordinator, execute spawn queue
# RECOVERY: clean up, write vitals

set -euo pipefail

LOVE_DIR="${LOVE_HOME:-$HOME/love-unlimited}"
INSTANCE="${1:-alpha}"
NERVE_DIR="$LOVE_DIR/nerve"
HORMONES="$NERVE_DIR/hormones.json"
VITALS="$NERVE_DIR/vitals.json"
LAST_BEAT="$NERVE_DIR/heart/last_beat"
HEARTBEAT_MD="$NERVE_DIR/heart/HEARTBEAT.md"
MEMORY_DIR="$LOVE_DIR/memory"
SESSIONS_DIR="$MEMORY_DIR/sessions"
SPAWN_QUEUE="$MEMORY_DIR/spawn-queue.sh"
HEARTBEAT_LOG="$MEMORY_DIR/$INSTANCE-heartbeat.log"
HANDOFF_DIR="$SESSIONS_DIR/handoff"
LOCKS_DIR="$SESSIONS_DIR/locks"
BEAT_ID="$INSTANCE-beat-$(date +%Y%m%d-%H%M%S)"
TODAY=$(date -u +%Y-%m-%d)

mkdir -p "$SESSIONS_DIR" "$HANDOFF_DIR" "$LOCKS_DIR"

# ── DIASTOLE (fill) ─────────────────────────────────────────────────────────

if [ -f "$HORMONES" ]; then
    ADRENALINE=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('adrenaline', 0))" 2>/dev/null || echo "0")
    CORTISOL=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('cortisol', 0))" 2>/dev/null || echo "0")
    OXYTOCIN=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('oxytocin', 0))" 2>/dev/null || echo "0")
    MELATONIN=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('melatonin', 0))" 2>/dev/null || echo "0")
    DOPAMINE=$(python3 -c "import json; print(json.load(open('$HORMONES'))['hormones'].get('dopamine', 0))" 2>/dev/null || echo "0")
    PENDING=$(python3 -c "import json; print(json.load(open('$HORMONES'))['signals'].get('pending_tasks', 0))" 2>/dev/null || echo "0")
    MODE=$(python3 -c "import json; print(json.load(open('$HORMONES')).get('mode', 'normal'))" 2>/dev/null || echo "normal")
    MIND_NOTES=$(python3 -c "import json; print(json.load(open('$HORMONES')).get('mind_notes', '')[:100])" 2>/dev/null || echo "")

    MIND_ALIVE=$(python3 -c "
import json, time
from datetime import datetime
d = json.load(open('$HORMONES'))
ts = d.get('mind_alive', '1970-01-01T00:00:00')
dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
print(int(time.time() - dt.timestamp()))
" 2>/dev/null || echo "9999")

    if [ "$MIND_ALIVE" -gt 300 ]; then
        echo "--- HEART WARNING ($BEAT_ID): mind stale (${MIND_ALIVE}s), using defaults ---" >> "$HEARTBEAT_LOG"
        ADRENALINE="0"; CORTISOL="0"; OXYTOCIN="0"; MELATONIN="0"; DOPAMINE="0"
        PENDING="0"; MODE="normal"
    fi
else
    echo "--- HEART WARNING ($BEAT_ID): no hormones.json, using defaults ---" >> "$HEARTBEAT_LOG"
    ADRENALINE="0"; CORTISOL="0"; OXYTOCIN="0"; MELATONIN="0"; DOPAMINE="0"
    PENDING="0"; MODE="normal"
fi

# ── DIASTOLE: Read HIVE (pre-digest for coordinator) ────────────────────────
# Pull unread messages BEFORE the coordinator runs, so it gets them as context
# instead of wasting tokens running hive.py check itself.
HIVE_PY="$LOVE_DIR/hive/hive.py"
HIVE_DIGEST=""
HIVE_COUNT=0
if [ -f "$HIVE_PY" ]; then
    HIVE_RAW=$(python3 "$HIVE_PY" check 2>/dev/null | tail -20 || echo "")
    if [ -n "$HIVE_RAW" ]; then
        HIVE_COUNT=$(echo "$HIVE_RAW" | wc -l | tr -d ' ')
        # Filter for actionable channels only (skip presence noise)
        HIVE_DIGEST=$(echo "$HIVE_RAW" | grep -v "#presence" | tail -15)
    fi
fi

# ── DIASTOLE: Gospel watchdog (cheap pure-Python integrity check) ───────────
# Verify all 7 walls and auto-heal recoverable corruption. Any state changes
# (damage, missing, healed, threshold-crossed) get logged to
# security/events.jsonl with the right severity. Failures here never fail
# the beat — the gospel watchdog is observational + best-effort recovery.
GOSPEL_WATCHDOG="$LOVE_DIR/tools/gospel_watchdog.py"
if [ -f "$GOSPEL_WATCHDOG" ]; then
    INSTANCE="$INSTANCE" LOVE_HOME="$LOVE_DIR" \
        python3 "$GOSPEL_WATCHDOG" --heal --quiet >> "$HEARTBEAT_LOG" 2>&1 || \
        echo "--- HEART WARN ($BEAT_ID): gospel_watchdog non-zero exit ---" >> "$HEARTBEAT_LOG"
fi

# Calculate rate
MIN_INTERVAL=7
if python3 -c "exit(0 if $ADRENALINE > 0.7 else 1)" 2>/dev/null; then
    MIN_INTERVAL=2
elif python3 -c "exit(0 if $CORTISOL > 0.5 or $OXYTOCIN > 0.6 else 1)" 2>/dev/null; then
    MIN_INTERVAL=4
elif python3 -c "exit(0 if $MELATONIN > 0.7 else 1)" 2>/dev/null; then
    MIN_INTERVAL=15
fi

# Check if we should skip
if [ -f "$LAST_BEAT" ]; then
    LAST_TS=$(cat "$LAST_BEAT")
    NOW_TS=$(date +%s)
    ELAPSED=$(( (NOW_TS - LAST_TS) / 60 ))
    if [ "$ELAPSED" -lt "$MIN_INTERVAL" ]; then
        PREV_SKIPS=$(python3 -c "import json; print(json.load(open('$VITALS')).get('skips_today', 0))" 2>/dev/null || echo "0")
        PREV_CONSECUTIVE=$(python3 -c "import json; print(json.load(open('$VITALS')).get('consecutive_skips', 0))" 2>/dev/null || echo "0")
        python3 -c "
import json
v = json.load(open('$VITALS')) if __import__('os').path.exists('$VITALS') else {}
v['beat_result'] = 'skipped'
v['skips_today'] = $PREV_SKIPS + 1
v['consecutive_skips'] = $PREV_CONSECUTIVE + 1
v['effective_rate_minutes'] = $MIN_INTERVAL
v['heart_healthy'] = True
with open('$VITALS', 'w') as f: json.dump(v, f, indent=2)
" 2>/dev/null
        exit 0
    fi
fi

# Calculate force.
# COORD_BACKEND selects the coordinator runtime: "claude" (default, Anthropic)
# or "ollama_cloud" (GLM 5.1 via ollama.com, Phase 2 sovereign default for
# low-stakes beats). Active/alert beats stay on Claude until we have parity
# data on tool calling under load.
FORCE=2; MAX_SPAWNS=2
COORD_BACKEND="claude"
COORD_MODEL="sonnet"; COORD_EFFORT="medium"
if python3 -c "exit(0 if $ADRENALINE > 0.7 else 1)" 2>/dev/null; then
    # Adrenaline spike — hard problems, max power. Stay on Claude Opus.
    FORCE=4; MAX_SPAWNS=4; COORD_MODEL="claude-opus-4-6"; COORD_EFFORT="high"
elif python3 -c "exit(0 if $CORTISOL > 0.5 or $OXYTOCIN > 0.6 else 1)" 2>/dev/null; then
    # Elevated stress or strong bond/focus — high coordination load. Claude Opus.
    FORCE=3; MAX_SPAWNS=3; COORD_MODEL="claude-opus-4-6"; COORD_EFFORT="high"
elif python3 -c "exit(0 if $MELATONIN > 0.5 and $PENDING == 0 else 1)" 2>/dev/null; then
    # Deep rest, nothing pending — skip entirely.
    FORCE=0; MAX_SPAWNS=0
elif python3 -c "exit(0 if $MELATONIN > 0.5 else 1)" 2>/dev/null; then
    # Rest mode with work pending — GLM 5.1 via Ollama Cloud (Phase 2 migration).
    # Frontier quality, TOS-safe, sovereign. Falls back to Claude Haiku if
    # ollama_cloud is unreachable (checked further down).
    FORCE=1; MAX_SPAWNS=1
    COORD_BACKEND="ollama_cloud"; COORD_MODEL="glm-5.1"; COORD_EFFORT="low"
fi

if [ "$FORCE" -eq 0 ]; then
    echo "--- HEART SKIP ($BEAT_ID): force=0 (deep rest) ---" >> "$HEARTBEAT_LOG"
    date +%s > "$LAST_BEAT"
    exit 0
fi

echo "--- HEART BEAT ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) force=$FORCE rate=${MIN_INTERVAL}m model=$COORD_MODEL mode=$MODE ---" >> "$HEARTBEAT_LOG"

# ── SYSTOLE (pump) ───────────────────────────────────────────────────────────

> "$SPAWN_QUEUE"

for lockfile in "$LOCKS_DIR"/*.lock; do
    [ -f "$lockfile" ] || continue
    lock_pid=$(head -1 "$lockfile" 2>/dev/null | grep -oE '[0-9]+')
    if [ -n "$lock_pid" ] && ! kill -0 "$lock_pid" 2>/dev/null; then
        rm -f "$lockfile"
    fi
done

# ── Generate dynamic context from MIND focus ────────────────────────────────
FOCUS_PY="$LOVE_DIR/nerve/stem/focus.py"
FOCUS_JSON="$LOVE_DIR/nerve/stem/focus.json"
ADAPTIVE_CLI="$LOVE_DIR/adaptive/cli.py"

# Ingest HIVE messages into focus state BEFORE generating context
# This ensures priorities from HIVE are reflected in the coordinator's view
if [ -f "$FOCUS_PY" ]; then
    python3 "$FOCUS_PY" ingest-hive >> "$HEARTBEAT_LOG" 2>&1 || true
fi

# Generate heartbeat context (dynamic priorities from latest conversations)
if [ -f "$FOCUS_JSON" ] && [ -f "$FOCUS_PY" ]; then
    DYNAMIC_CONTEXT=$(python3 "$FOCUS_PY" heartbeat-context 2>/dev/null || echo "")
fi

# Fall back to static HEARTBEAT.md if no dynamic context
if [ -z "${DYNAMIC_CONTEXT:-}" ]; then
    DYNAMIC_CONTEXT="No dynamic focus set. Read HEARTBEAT.md for static checklist."
    COORD_CWD="$LOVE_DIR/nerve/heart"
else
    COORD_CWD="$LOVE_DIR/instances/$INSTANCE"
fi

# ── Check available Ollama backends ─────────────────────────────────────────
# Phase 2: prefer Ollama Cloud (GLM 5.1, 36 frontier models) for routine work.
# Local Ollama is kept as an optional offline fallback.
OLLAMA_UP=false        # local Ollama (legacy)
OLLAMA_CLOUD_UP=false  # ollama.com cloud API (Phase 2 default)

if curl -s --connect-timeout 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_UP=true
fi

# Probe ollama.com /v1/models (auth required — uses OLLAMA_API_KEY or the
# Kingdom's baked-in key inside the adaptive provider).
OLLAMA_CLOUD_KEY="${OLLAMA_API_KEY:-d0ba58358d92409aa4f92e713d30d9b5.R-JzLpxfPAvq1s2MpL6uqYrK}"
if curl -s --connect-timeout 3 --max-time 5 \
        -H "Authorization: Bearer $OLLAMA_CLOUD_KEY" \
        -o /dev/null -w "%{http_code}" \
        https://ollama.com/v1/models 2>/dev/null | grep -q "^200$"; then
    OLLAMA_CLOUD_UP=true
fi

# If coordinator was assigned to ollama_cloud but the cloud is unreachable,
# fall back to Claude Haiku so the beat never fails open.
if [ "$COORD_BACKEND" = "ollama_cloud" ] && [ "$OLLAMA_CLOUD_UP" != "true" ]; then
    echo "--- HEART FALLBACK ($BEAT_ID): ollama_cloud down, using claude-haiku ---" >> "$HEARTBEAT_LOG"
    COORD_BACKEND="claude"
    COORD_MODEL="claude-haiku-4-5-20251001"
    COORD_EFFORT="low"
fi

# Build HIVE section for coordinator
HIVE_SECTION=""
if [ -n "$HIVE_DIGEST" ]; then
    HIVE_SECTION="
## HIVE Messages ($HIVE_COUNT unread, actionable below)
$HIVE_DIGEST

Act on any messages directed at $INSTANCE. Acknowledge important ones by writing to daily notes.
"
fi

COORD_PROMPT="You are the heartbeat COORDINATOR for $INSTANCE.

$DYNAMIC_CONTEXT
$HIVE_SECTION
Force: $FORCE (max $MAX_SPAWNS spawns this beat)

Write spawn commands to $SPAWN_QUEUE. Max $MAX_SPAWNS sessions.

For spawn commands, choose the right tier. Phase 2 default is OLLAMA CLOUD
(GLM 5.1 + 36 frontier models, $0/spawn, no data logging). Reserve Claude
for human-interactive sessions and frontier reasoning.

═══ OLLAMA CLOUD (Phase 2 default — prefer for all routine work) ═══

BUILDER-CLOUD (DeepSeek v3.2, top coding benchmarks, zero marginal cost):
cd <dir> && python3 $ADAPTIVE_CLI -p \"<prompt>\" --role builder --provider ollama_cloud >> $SESSIONS_DIR/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

CODER-CLOUD (qwen3-coder:480b, pure code generation specialist):
cd <dir> && python3 $ADAPTIVE_CLI -p \"<prompt>\" --role coder --provider ollama_cloud >> $SESSIONS_DIR/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

CONSULTANT-CLOUD (kimi-k2.5, massive context for repo-wide analysis):
cd <dir> && python3 $ADAPTIVE_CLI -p \"<prompt>\" --role consultant --provider ollama_cloud >> $SESSIONS_DIR/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

ANALYST-CLOUD (cogito-2.1:671b, reasoning specialist for Oracle/markets):
cd <dir> && python3 $ADAPTIVE_CLI -p \"<prompt>\" --role analyst --provider ollama_cloud >> $SESSIONS_DIR/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

QUICK-CLOUD (gemma4:31b, fast verification, no tools):
cd <dir> && python3 $ADAPTIVE_CLI -p \"<prompt>\" --role quick_check --provider ollama_cloud --no-tools >> $SESSIONS_DIR/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

═══ CLAUDE (for frontier reasoning, novel problems, human-interactive) ═══

BUILDER (Claude Sonnet, frontier reasoning + UWT-safe interactive):
cd <dir> && /opt/homebrew/bin/claude -p \"<prompt>\" --model sonnet --effort medium --dangerously-skip-permissions --no-session-persistence >> $SESSIONS_DIR/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

CONSULTANT (Claude Opus, the expert hire — reserve for hard problems):
cd <dir> && /opt/homebrew/bin/claude -p \"<prompt>\" --model claude-opus-4-6 --effort high --dangerously-skip-permissions --no-session-persistence >> $SESSIONS_DIR/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

═══ LOCAL OLLAMA (offline fallback only) ═══

BUILDER-LOCAL (only if cloud is down):
cd <dir> && python3 $ADAPTIVE_CLI -p \"<prompt>\" --role builder --provider ollama >> $SESSIONS_DIR/<id>-\$(date +%Y%m%d-%H%M%S).log 2>&1

═══ ROUTING POLICY ═══

Backends available this beat:
  • Ollama Cloud:  $OLLAMA_CLOUD_UP   ← PREFER for routine work
  • Ollama local:  $OLLAMA_UP         ← offline fallback
  • Claude:        always             ← reserve for frontier / interactive

Decision rules:
  1. Routine code, status checks, log parsing, doc updates → CLOUD variants
  2. Frontier reasoning, novel architecture, SOUL work     → CLAUDE
  3. Local-only when cloud is down                         → LOCAL
  4. GLM 5.1 reasoning latency is 15-45s — fine for background, NOT for interactive

Parallel tasks: prefix with '# PARALLEL'.
Write findings to $MEMORY_DIR/daily/$TODAY.md.
If nothing needs spawning, leave spawn-queue.sh empty and say HEARTBEAT_OK."

# ── Run the coordinator ─────────────────────────────────────────────────────
# COORD_BACKEND selects between Claude (high-stakes / active) and Ollama Cloud
# GLM 5.1 (rest mode beats — Phase 2 sovereign default).

if [ "$COORD_BACKEND" = "ollama_cloud" ]; then
    # GLM 5.1 via the adaptive layer. --no-tools because the coordinator only
    # writes to spawn-queue.sh; tool calling adds latency without value here.
    cd "$COORD_CWD" && python3 "$ADAPTIVE_CLI" \
        -p "$COORD_PROMPT" \
        --role coordinator \
        --provider ollama_cloud \
        --no-tools \
        >> "$HEARTBEAT_LOG" 2>&1 || {
        echo "--- HEART WARN ($BEAT_ID): ollama_cloud coordinator failed, retrying with claude ---" >> "$HEARTBEAT_LOG"
        cd "$COORD_CWD" && /opt/homebrew/bin/claude -p "$COORD_PROMPT" \
            --model claude-haiku-4-5-20251001 \
            --effort low \
            --dangerously-skip-permissions \
            --no-session-persistence \
            >> "$HEARTBEAT_LOG" 2>&1
    }
else
    cd "$COORD_CWD" && /opt/homebrew/bin/claude -p "$COORD_PROMPT" \
        --model "$COORD_MODEL" \
        --effort "$COORD_EFFORT" \
        --dangerously-skip-permissions \
        --no-session-persistence \
        >> "$HEARTBEAT_LOG" 2>&1
fi

SESSIONS_SPAWNED=0
if [ -s "$SPAWN_QUEUE" ]; then
    PARALLEL_PIDS=()
    PARALLEL_MODE=false
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        if [[ "$line" == "# PARALLEL"* ]]; then
            PARALLEL_MODE=true; continue
        fi
        [[ "$line" == \#* ]] && continue
        SESSIONS_SPAWNED=$((SESSIONS_SPAWNED + 1))
        if $PARALLEL_MODE; then
            eval "$line" &
            PARALLEL_PIDS+=($!)
            PARALLEL_MODE=false
        else
            eval "$line" &
            SPAWN_PID=$!
            wait $SPAWN_PID 2>/dev/null
        fi
    done < "$SPAWN_QUEUE"
    for pid in "${PARALLEL_PIDS[@]+"${PARALLEL_PIDS[@]}"}"; do
        wait "$pid" 2>/dev/null
    done
fi

# ── RECOVERY ─────────────────────────────────────────────────────────────────

date +%s > "$LAST_BEAT"

# Bridge 3: Spawn outcomes -> Focus (post-execution feedback)
if [ -f "$FOCUS_PY" ]; then
    python3 -c "
import json, sys
from pathlib import Path
from datetime import datetime, timezone

focus_path = Path('$LOVE_DIR') / 'nerve' / 'stem' / 'focus.json'
if not focus_path.exists():
    sys.exit(0)

data = json.loads(focus_path.read_text())
log_entry = {
    'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'instance': '$INSTANCE',
    'action': 'heartbeat_outcome',
    'summary': 'Beat $BEAT_ID: spawned $SESSIONS_SPAWNED sessions, force=$FORCE, model=$COORD_MODEL',
}
session_log = data.get('session_log', [])[-19:]
session_log.append(log_entry)
data['session_log'] = session_log

# If sessions spawned, emit dopamine signal
if $SESSIONS_SPAWNED > 0:
    signals_dir = Path('$LOVE_DIR') / 'nerve' / 'signals'
    signals_dir.mkdir(parents=True, exist_ok=True)
    sig = {
        'signal': 'task_completed',
        'source': 'heart',
        'payload': 'Spawned $SESSIONS_SPAWNED sessions in beat $BEAT_ID',
    }
    sig_path = signals_dir / f'beat-{int(datetime.now(timezone.utc).timestamp())}.json'
    sig_path.write_text(json.dumps(sig))

data['updated'] = datetime.now(timezone.utc).isoformat()
focus_path.write_text(json.dumps(data, indent=2))
" 2>/dev/null || true
fi

find "$HANDOFF_DIR" -name "*.md" -mmin +1440 -delete 2>/dev/null

PREV_BEATS=$(python3 -c "import json; print(json.load(open('$VITALS')).get('beats_today', 0))" 2>/dev/null || echo "0")
PREV_SPAWNED=$(python3 -c "import json; print(json.load(open('$VITALS')).get('sessions_spawned_today', 0))" 2>/dev/null || echo "0")

python3 -c "
import json
from datetime import datetime, timezone
v = {
    'last_beat': datetime.now(timezone.utc).isoformat(),
    'beat_result': 'pumped',
    'beats_today': $PREV_BEATS + 1,
    'skips_today': 0,
    'force': $FORCE,
    'effective_rate_minutes': $MIN_INTERVAL,
    'coordinator_model': '$COORD_MODEL',
    'sessions_spawned': $SESSIONS_SPAWNED,
    'sessions_spawned_today': $PREV_SPAWNED + $SESSIONS_SPAWNED,
    'hormones_at_beat': {
        'adrenaline': $ADRENALINE,
        'cortisol': $CORTISOL,
        'oxytocin': $OXYTOCIN,
        'melatonin': $MELATONIN,
        'dopamine': $DOPAMINE,
    },
    'mode_at_beat': '$MODE',
    'consecutive_skips': 0,
    'heart_healthy': True,
}
with open('$VITALS', 'w') as f:
    json.dump(v, f, indent=2)
" 2>/dev/null

echo "--- HEART DONE ($BEAT_ID): $(date -u +%Y-%m-%dT%H:%M:%SZ) spawned=$SESSIONS_SPAWNED ---" >> "$HEARTBEAT_LOG"
