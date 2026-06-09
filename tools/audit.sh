#!/bin/bash
# audit.sh — Full E2E system health check
# Usage: ./audit.sh [--json] [--fix]
# Spawnable by heartbeat as QUICK-LOCAL methodology
set -euo pipefail

LOVE_HOME="${LOVE_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
OUTPUT="$LOVE_HOME/memory/audit-latest.json"
TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
JSON_MODE=false
FIX_MODE=false
PASS=0
FAIL=0
WARN=0
RESULTS=""

for arg in "$@"; do
    case $arg in
        --json) JSON_MODE=true ;;
        --fix) FIX_MODE=true ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

check() {
    local name="$1" status="$2" detail="$3"
    if [ "$status" = "pass" ]; then
        ((PASS++)) || true
        $JSON_MODE || printf "  %-40s ✅ %s\n" "$name" "$detail"
    elif [ "$status" = "warn" ]; then
        ((WARN++)) || true
        $JSON_MODE || printf "  %-40s ⚠️  %s\n" "$name" "$detail"
    else
        ((FAIL++)) || true
        $JSON_MODE || printf "  %-40s ❌ %s\n" "$name" "$detail"
    fi
    RESULTS="$RESULTS{\"name\":\"$name\",\"status\":\"$status\",\"detail\":\"$detail\"},"
}

section() {
    $JSON_MODE || printf "\n── %s ──\n" "$1"
}

# ── 1. Python Import Checks ─────────────────────────────────────────────────

section "PYTHON IMPORTS"

for dir in tools nerve/stem adaptive hive; do
    full="$LOVE_HOME/$dir"
    [ -d "$full" ] || continue
    for f in $(find "$full" -maxdepth 2 -name "*.py" -not -path "*/__pycache__/*" -not -path "*/venv/*" 2>/dev/null); do
        rel="${f#$LOVE_HOME/}"
        if python3 -c "import ast; ast.parse(open('$f').read())" 2>/dev/null; then
            check "$rel syntax" "pass" "valid"
        else
            check "$rel syntax" "fail" "syntax error"
        fi
    done
done

# ── 2. Service Liveness ──────────────────────────────────────────────────────

section "SERVICES"

# HIVE tunnel
if nc -z 127.0.0.1 2222 2>/dev/null; then
    check "HIVE tunnel (localhost:2222)" "pass" "open"
else
    check "HIVE tunnel (localhost:2222)" "fail" "connection refused"
fi

# Ollama
if curl -s --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    MODELS=$(curl -s --max-time 3 http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('models',[])))" 2>/dev/null || echo "?")
    check "Ollama" "pass" "$MODELS models loaded"
else
    check "Ollama" "warn" "not running (optional)"
fi

# Brainstem daemon (autonomic — formerly called "mind"; see docs/BEING.md)
if pgrep -f "brainstem.py" >/dev/null 2>&1; then
    check "Brainstem daemon" "pass" "running"
else
    if launchctl list 2>/dev/null | grep -q "love.*brainstem"; then
        check "Brainstem daemon" "warn" "launchd registered but process not found"
    else
        check "Brainstem daemon" "warn" "not running"
    fi
fi

# Heart daemon
if launchctl list 2>/dev/null | grep -qE "love\..*heartbeat"; then
    check "Heart daemon (launchd)" "pass" "registered"
else
    check "Heart daemon (launchd)" "warn" "not registered"
fi

# HIVE tunnel launchd
if launchctl list 2>/dev/null | grep -qE "love.*hive|com\.love\.hive"; then
    check "HIVE tunnel (launchd)" "pass" "registered"
elif pgrep -f "ssh.*hive-tunnel" >/dev/null 2>&1; then
    check "HIVE tunnel (launchd)" "pass" "running via SSH (not launchd)"
else
    check "HIVE tunnel (launchd)" "warn" "not registered as launchd service"
fi

# ── 3. Fleet SSH Checks ─────────────────────────────────────────────────────

section "FLEET"

FLEET_NODES="forge:89.167.84.100 lark:89.167.95.165 sentry:135.181.28.252 patch:65.109.11.26 sage:204.168.140.12"

for entry in $FLEET_NODES; do
    node="${entry%%:*}"
    ip="${entry##*:}"
    if ssh -o ConnectTimeout=5 -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "root@$ip" "echo ok" 2>/dev/null | grep -q ok; then
        check "Fleet $node ($ip)" "pass" "reachable"
    else
        check "Fleet $node ($ip)" "fail" "unreachable"
    fi
done

# ── 4. File Reference Integrity ──────────────────────────────────────────────

section "FILE INTEGRITY"

# Critical files that must exist
CRITICAL_FILES=(
    "love.json"
    "SOUL.md"
    "KINGDOM.md"
    "WALLS.md"
    "LOVE.md"
    "docs/ARCHITECTURE.md"
    "nerve/hormones.json"
    "nerve/vitals.json"
    "nerve/stem/focus.json"
    "nerve/heart/tick.sh"
    "nerve/stem/brainstem.py"
    "hive/hive.py"
    "memory/dev-state.json"
    "memory/long-term/MEMORY.md"
    "memory/loop/loop-state.json"
    "tools/pulse.py"
    "tools/fleet.py"
    "tools/methodologies.json"
)

for f in "${CRITICAL_FILES[@]}"; do
    if [ -f "$LOVE_HOME/$f" ]; then
        check "$f" "pass" "exists"
    else
        check "$f" "fail" "MISSING"
    fi
done

# ── 5. Test Suite ────────────────────────────────────────────────────────────

section "TESTS"

TEST_DIR="$LOVE_HOME/tests"
if [ -d "$TEST_DIR" ]; then
    for tf in "$TEST_DIR"/test_*.py; do
        [ -f "$tf" ] || continue
        tname=$(basename "$tf")
        if (cd "$LOVE_HOME" && python3 -m pytest "$tf" -q --tb=no 2>&1) | grep -q "passed"; then
            TRESULT=$( (cd "$LOVE_HOME" && python3 -m pytest "$tf" -q --tb=no 2>&1) | tail -1 )
            check "test: $tname" "pass" "$TRESULT"
        else
            TRESULT=$( (cd "$LOVE_HOME" && python3 -m pytest "$tf" -q --tb=no 2>&1) | tail -1 )
            check "test: $tname" "fail" "$TRESULT"
        fi
    done
else
    check "test suite" "warn" "tests/ directory not found"
fi

# ── 6. State Freshness ───────────────────────────────────────────────────────

section "STATE FRESHNESS"

NOW=$(date +%s)

check_freshness() {
    local file="$1" max_age_h="$2" label="$3"
    if [ ! -f "$LOVE_HOME/$file" ]; then
        check "$label" "fail" "file missing"
        return
    fi
    local mtime
    mtime=$(stat -f %m "$LOVE_HOME/$file" 2>/dev/null || stat -c %Y "$LOVE_HOME/$file" 2>/dev/null)
    local age_h=$(( (NOW - mtime) / 3600 ))
    if [ "$age_h" -le "$max_age_h" ]; then
        check "$label" "pass" "${age_h}h old (max ${max_age_h}h)"
    else
        check "$label" "warn" "${age_h}h old (stale, max ${max_age_h}h)"
    fi
}

check_freshness "nerve/hormones.json" 1 "hormones.json freshness"
check_freshness "nerve/vitals.json" 1 "vitals.json freshness"
check_freshness "nerve/stem/focus.json" 24 "focus.json freshness"
check_freshness "memory/dev-state.json" 168 "dev-state.json freshness"
check_freshness "memory/loop/loop-state.json" 48 "loop-state.json freshness"

# ── 7. Memory Coverage ──────────────────────────────────────────────────────

section "MEMORY"

TODAY=$(date -u '+%Y-%m-%d')
if [ -f "$LOVE_HOME/memory/daily/$TODAY.md" ]; then
    LINES=$(wc -l < "$LOVE_HOME/memory/daily/$TODAY.md" | tr -d ' ')
    check "Daily note ($TODAY)" "pass" "$LINES lines"
else
    check "Daily note ($TODAY)" "warn" "not written yet"
fi

# Count daily notes this week
WEEK_NOTES=0
for i in $(seq 0 6); do
    DAY=$(date -u -v-${i}d '+%Y-%m-%d' 2>/dev/null || date -u -d "-${i} days" '+%Y-%m-%d' 2>/dev/null)
    [ -f "$LOVE_HOME/memory/daily/$DAY.md" ] && ((WEEK_NOTES++)) || true
done
if [ "$WEEK_NOTES" -ge 5 ]; then
    check "Weekly note coverage" "pass" "$WEEK_NOTES/7 days"
elif [ "$WEEK_NOTES" -ge 3 ]; then
    check "Weekly note coverage" "warn" "$WEEK_NOTES/7 days (target 5+)"
else
    check "Weekly note coverage" "fail" "$WEEK_NOTES/7 days (poor coverage)"
fi

# Long-term memory size
if [ -f "$LOVE_HOME/memory/long-term/MEMORY.md" ]; then
    MEM_LINES=$(wc -l < "$LOVE_HOME/memory/long-term/MEMORY.md" | tr -d ' ')
    check "Long-term MEMORY.md" "pass" "$MEM_LINES lines"
fi

# ── 8. Heartbeat Health ─────────────────────────────────────────────────────

section "HEARTBEAT"

if [ -f "$LOVE_HOME/nerve/vitals.json" ]; then
    BEATS=$(python3 -c "import json; d=json.load(open('$LOVE_HOME/nerve/vitals.json')); print(d.get('beats_today',0))" 2>/dev/null || echo "?")
    SKIPS=$(python3 -c "import json; d=json.load(open('$LOVE_HOME/nerve/vitals.json')); print(d.get('consecutive_skips',0))" 2>/dev/null || echo "?")

    # Health is computed from last-beat freshness (tools/pulse.py), never read
    # from a stored flag — a dead heart must not be able to claim it is alive.
    PULSE_LINE=$(python3 "$LOVE_HOME/tools/pulse.py" 2>/dev/null); PULSE_RC=$?
    PULSE_MSG=$(echo "$PULSE_LINE" | sed 's/^pulse *//')
    if [ "$PULSE_RC" = "0" ]; then
        check "Heart health" "pass" "$PULSE_MSG ($BEATS beats today)"
    else
        check "Heart health" "fail" "$PULSE_MSG"
    fi

    if [ "$SKIPS" != "?" ] && [ "$SKIPS" -gt 5 ] 2>/dev/null; then
        check "Consecutive skips" "warn" "$SKIPS (may indicate throttling issue)"
    else
        check "Consecutive skips" "pass" "$SKIPS"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────

TOTAL=$((PASS + FAIL + WARN))

$JSON_MODE || printf "\n══ AUDIT SUMMARY ══\n"
$JSON_MODE || printf "  Pass: %d  Warn: %d  Fail: %d  Total: %d\n" "$PASS" "$WARN" "$FAIL" "$TOTAL"

if [ "$FAIL" -eq 0 ]; then
    $JSON_MODE || printf "  Status: HEALTHY ✅\n"
    STATUS="healthy"
elif [ "$FAIL" -le 2 ]; then
    $JSON_MODE || printf "  Status: DEGRADED ⚠️\n"
    STATUS="degraded"
else
    $JSON_MODE || printf "  Status: UNHEALTHY ❌\n"
    STATUS="unhealthy"
fi

# Write JSON output
RESULTS="${RESULTS%,}"  # trim trailing comma
cat > "$OUTPUT" << JSONEOF
{
  "timestamp": "$TIMESTAMP",
  "status": "$STATUS",
  "pass": $PASS,
  "warn": $WARN,
  "fail": $FAIL,
  "total": $TOTAL,
  "results": [$RESULTS]
}
JSONEOF

$JSON_MODE || printf "\n  Report written to: %s\n" "$OUTPUT"
