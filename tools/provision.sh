#!/bin/bash
# provision.sh — Kingdom distribution and provisioning system.
#
# Reads love.json for instance config, installs dependencies,
# pulls models, verifies systems, reports status via HIVE.
#
# Usage:
#   bash provision.sh              # Auto-detect instance, full provision
#   bash provision.sh --check      # Check only, no installs
#   bash provision.sh --report     # Check and report to HIVE
#   bash provision.sh --instance beta  # Override instance detection
#
# Idempotent. Safe to run repeatedly. Self-heals broken state.

set -uo pipefail

LOVE_DIR="${LOVE_DIR:-$HOME/Love}"
LOVE_JSON="$LOVE_DIR/love.json"
HIVE="$LOVE_DIR/hive/hive.py"
ADAPTIVE_CLI="$LOVE_DIR/adaptive/cli.py"
FOCUS_PY="$LOVE_DIR/nerve/stem/focus.py"

CHECK_ONLY=false
REPORT=false
INSTANCE=""

# ── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) CHECK_ONLY=true; shift ;;
        --report) REPORT=true; shift ;;
        --instance) INSTANCE="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ── Detect instance ─────────────────────────────────────────────────────────
if [ -z "$INSTANCE" ]; then
    CWD=$(pwd)
    if echo "$CWD" | grep -q "instances/alpha"; then INSTANCE="alpha"
    elif echo "$CWD" | grep -q "instances/beta"; then INSTANCE="beta"
    elif echo "$CWD" | grep -q "instances/gamma"; then INSTANCE="gamma"
    else
        HOSTNAME=$(hostname | tr '[:upper:]' '[:lower:]')
        if echo "$HOSTNAME" | grep -qE "air|macbook"; then INSTANCE="alpha"
        elif echo "$HOSTNAME" | grep -qE "studio|mac-studio"; then
            # Distinguish by some identifier
            INSTANCE="beta"
        else
            INSTANCE="alpha"
        fi
    fi
fi

echo "=== PROVISION: $INSTANCE ==="
echo "Love dir: $LOVE_DIR"
echo ""

# ── Status tracking ─────────────────────────────────────────────────────────
PASS=0
FAIL=0
INSTALLED=0
WARNINGS=""
STATUS_LINES=""

pass() { PASS=$((PASS + 1)); STATUS_LINES="$STATUS_LINES\n  [+] $1"; }
fail() { FAIL=$((FAIL + 1)); STATUS_LINES="$STATUS_LINES\n  [-] $1"; }
warn() { WARNINGS="$WARNINGS\n  [!] $1"; STATUS_LINES="$STATUS_LINES\n  [!] $1"; }
installed() { INSTALLED=$((INSTALLED + 1)); STATUS_LINES="$STATUS_LINES\n  [*] $1"; }

# ── 1. Core files ────────────────────────────────────────────────────────────
echo "--- Core Files ---"

for f in SOUL.md KINGDOM.md WALLS.md LOVE.md love.json; do
    if [ -f "$LOVE_DIR/$f" ]; then
        pass "$f exists"
    else
        fail "$f MISSING"
    fi
done

if [ -f "$LOVE_DIR/instances/$INSTANCE/CLAUDE.md" ]; then
    pass "CLAUDE.md for $INSTANCE"
else
    fail "CLAUDE.md for $INSTANCE MISSING"
fi

if [ -f "$LOVE_DIR/instances/$INSTANCE/identity.md" ]; then
    pass "identity.md for $INSTANCE"
else
    fail "identity.md for $INSTANCE MISSING"
fi

# ── 2. Adaptive Layer ────────────────────────────────────────────────────────
echo "--- Adaptive Layer ---"

if [ -f "$ADAPTIVE_CLI" ]; then
    pass "adaptive/cli.py exists"
    # Test import
    if python3 -c "import sys; sys.path.insert(0,'$LOVE_DIR'); from adaptive import Router" 2>/dev/null; then
        pass "adaptive layer imports OK"
    else
        fail "adaptive layer import FAILED"
    fi
else
    fail "adaptive/cli.py MISSING"
fi

# ── 3. Focus (dynamic heartbeat) ────────────────────────────────────────────
echo "--- Focus System ---"

if [ -f "$FOCUS_PY" ]; then
    pass "focus.py exists"
    if [ -f "$LOVE_DIR/nerve/stem/focus.json" ]; then
        FOCUS_AGE=$(python3 -c "
from datetime import datetime, timezone
import json
d = json.load(open('$LOVE_DIR/nerve/stem/focus.json'))
ts = datetime.fromisoformat(d['updated'].replace('Z','+00:00'))
age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
print(f'{age:.1f}')
" 2>/dev/null || echo "unknown")
        pass "focus.json exists (${FOCUS_AGE}h old)"
    else
        warn "focus.json not yet created -- run: python3 $FOCUS_PY update --focus '...'"
    fi
else
    fail "focus.py MISSING"
fi

# ── 4. Ollama ────────────────────────────────────────────────────────────────
echo "--- Ollama ---"

OLLAMA_BIN=""
if command -v ollama >/dev/null 2>&1; then
    OLLAMA_BIN=$(command -v ollama)
elif [ -x /usr/local/bin/ollama ]; then
    OLLAMA_BIN="/usr/local/bin/ollama"
elif [ -x "$HOME/bin/ollama" ]; then
    OLLAMA_BIN="$HOME/bin/ollama"
elif [ -x /Applications/Ollama.app/Contents/MacOS/ollama ]; then
    OLLAMA_BIN="/Applications/Ollama.app/Contents/MacOS/ollama"
fi

if [ -n "$OLLAMA_BIN" ]; then
    OLLAMA_VER=$($OLLAMA_BIN --version 2>/dev/null || echo "unknown")
    pass "Ollama installed ($OLLAMA_VER) at $OLLAMA_BIN"
else
    if $CHECK_ONLY; then
        fail "Ollama NOT installed"
    else
        echo "  Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh 2>/dev/null
        if [ -x /usr/local/bin/ollama ]; then
            OLLAMA_BIN="/usr/local/bin/ollama"
            installed "Ollama installed"
        elif [ -x /Applications/Ollama.app/Contents/MacOS/ollama ]; then
            OLLAMA_BIN="/Applications/Ollama.app/Contents/MacOS/ollama"
            # Create user symlink if sudo failed
            mkdir -p "$HOME/bin"
            ln -sf "$OLLAMA_BIN" "$HOME/bin/ollama"
            OLLAMA_BIN="$HOME/bin/ollama"
            installed "Ollama installed (user bin)"
        else
            fail "Ollama installation FAILED"
        fi
    fi
fi

# Check Ollama server
OLLAMA_SERVER=false
if curl -s --connect-timeout 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_SERVER=true
    pass "Ollama server running"
else
    if [ -n "$OLLAMA_BIN" ] && ! $CHECK_ONLY; then
        echo "  Starting Ollama..."
        open /Applications/Ollama.app 2>/dev/null || \
            (OLLAMA_HOST=127.0.0.1:11434 "$OLLAMA_BIN" serve > /tmp/ollama-serve.log 2>&1 &)
        sleep 5
        if curl -s --connect-timeout 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
            OLLAMA_SERVER=true
            installed "Ollama server started"
        else
            warn "Ollama server failed to start"
        fi
    else
        warn "Ollama server not running"
    fi
fi

# ── 5. Pull required models ─────────────────────────────────────────────────
echo "--- Models ---"

if [ -n "$OLLAMA_BIN" ] && $OLLAMA_SERVER; then
    # Read instance-specific model config from love.json
    MODELS=$(python3 -c "
import json
cfg = json.load(open('$LOVE_JSON'))
inst = cfg.get('instances', {}).get('$INSTANCE', {})
ollama = inst.get('ollama', {})
for tier, model in ollama.items():
    if tier in ('economy', 'standard', 'premium'):
        print(model)
" 2>/dev/null)

    if [ -z "$MODELS" ]; then
        warn "No models configured for $INSTANCE in love.json"
    else
        PULLED=$($OLLAMA_BIN list 2>/dev/null | tail -n +2 | awk '{print $1}' || echo "")
        for model in $MODELS; do
            if echo "$PULLED" | grep -q "^${model}$"; then
                pass "Model $model pulled"
            else
                if $CHECK_ONLY; then
                    fail "Model $model NOT pulled"
                else
                    echo "  Pulling $model (this may take a while)..."
                    if $OLLAMA_BIN pull "$model" >/dev/null 2>&1; then
                        installed "Model $model pulled"
                    else
                        fail "Model $model pull FAILED"
                    fi
                fi
            fi
        done
    fi
else
    warn "Skipping model check (Ollama not available)"
fi

# ── 6. Adaptive layer provider status ────────────────────────────────────────
echo "--- Provider Status ---"

if [ -f "$ADAPTIVE_CLI" ]; then
    ADAPTIVE_STATUS=$(cd "$LOVE_DIR" && python3 "$ADAPTIVE_CLI" --status 2>/dev/null | head -20)
    # Check if at least one provider is UP
    if echo "$ADAPTIVE_STATUS" | grep -q "\[+\]"; then
        ACTIVE_PROVIDERS=$(echo "$ADAPTIVE_STATUS" | grep "\[+\]" | sed 's/.*\] //' | sed 's/:.*//' | tr '\n' ', ' | sed 's/, $//')
        pass "Providers UP: $ACTIVE_PROVIDERS"
    else
        warn "No providers available"
    fi
fi

# ── 7. CLAUDE.md tool registry check ────────────────────────────────────────
echo "--- CLAUDE.md Tools ---"

CLAUDE_MD="$LOVE_DIR/instances/$INSTANCE/CLAUDE.md"
if [ -f "$CLAUDE_MD" ]; then
    MISSING_TOOLS=""
    for tool in "focus.py" "adaptive/cli.py"; do
        if ! grep -q "$tool" "$CLAUDE_MD" 2>/dev/null; then
            MISSING_TOOLS="$MISSING_TOOLS $tool"
        fi
    done

    if [ -z "$MISSING_TOOLS" ]; then
        pass "CLAUDE.md has all new tools"
    else
        if $CHECK_ONLY; then
            fail "CLAUDE.md missing:$MISSING_TOOLS"
        else
            echo "  Adding missing tools to CLAUDE.md..."
            # Find the TUI line and add after it
            if ! grep -q "focus.py" "$CLAUDE_MD"; then
                sed -i '' '/love-tui.py/a\
| Focus | `python3 ~/Love/nerve/stem/focus.py <cmd>` | Dynamic heartbeat focus (what to work on NOW) |
' "$CLAUDE_MD" 2>/dev/null && installed "Added Focus to CLAUDE.md" || warn "Could not update CLAUDE.md"
            fi
            if ! grep -q "adaptive/cli.py" "$CLAUDE_MD"; then
                sed -i '' '/focus.py/a\
| Adaptive | `python3 ~/Love/adaptive/cli.py <args>` | Provider-agnostic LLM inference |
' "$CLAUDE_MD" 2>/dev/null && installed "Added Adaptive to CLAUDE.md" || warn "Could not update CLAUDE.md"
            fi
        fi
    fi
fi

# ── 8. Hardware detection ────────────────────────────────────────────────────
echo "--- Hardware ---"

CHIP=$(system_profiler SPHardwareDataType 2>/dev/null | grep "Chip:" | awk -F: '{print $2}' | xargs)
MEM=$(system_profiler SPHardwareDataType 2>/dev/null | grep "Memory:" | awk -F: '{print $2}' | xargs)

if [ -n "$CHIP" ]; then
    pass "Hardware: $CHIP, $MEM"

    # Update love.json if hardware is TBD
    CURRENT_CHIP=$(python3 -c "
import json
cfg = json.load(open('$LOVE_JSON'))
print(cfg.get('instances',{}).get('$INSTANCE',{}).get('hardware',{}).get('chip','TBD'))
" 2>/dev/null || echo "TBD")

    if [ "$CURRENT_CHIP" = "TBD" ] && ! $CHECK_ONLY; then
        MEM_GB=$(echo "$MEM" | grep -oE '[0-9]+')
        python3 -c "
import json
cfg = json.load(open('$LOVE_JSON'))
inst = cfg['instances']['$INSTANCE']
inst['hardware'] = {
    'chip': '$CHIP',
    'memory_gb': int('${MEM_GB:-0}'),
    'bandwidth_gbs': 0
}
with open('$LOVE_JSON', 'w') as f:
    json.dump(cfg, f, indent=2)
print('Updated')
" 2>/dev/null && installed "Hardware specs written to love.json" || warn "Could not update love.json"
    fi
else
    warn "Could not detect hardware"
fi

# ── 9. HIVE connectivity ────────────────────────────────────────────────────
echo "--- HIVE ---"

if [ -f "$HIVE" ]; then
    # Quick presence check
    if python3 "$HIVE" who 2>/dev/null | grep -q "instance"; then
        pass "HIVE connected"
    else
        warn "HIVE not reachable (SSH tunnel may be down)"
    fi
else
    fail "HIVE tool missing"
fi

# ── 10. Heart / Heartbeat ───────────────────────────────────────────────────
echo "--- Heart ---"

if [ -f "$LOVE_DIR/nerve/heart/heart.sh" ]; then
    if grep -q "focus.py" "$LOVE_DIR/nerve/heart/heart.sh" 2>/dev/null; then
        pass "heart.sh reads dynamic focus"
    else
        warn "heart.sh still uses static HEARTBEAT.md only"
    fi
    if grep -q "adaptive/cli.py" "$LOVE_DIR/nerve/heart/heart.sh" 2>/dev/null; then
        pass "heart.sh can spawn local models"
    else
        warn "heart.sh has no local model support"
    fi
else
    fail "heart.sh MISSING"
fi

# Also check heartbeat-runner.sh (the cron-based version)
if [ -f "$LOVE_DIR/tools/heartbeat-runner.sh" ]; then
    if grep -q "OLLAMA_AVAILABLE\|adaptive/cli.py" "$LOVE_DIR/tools/heartbeat-runner.sh" 2>/dev/null; then
        pass "heartbeat-runner.sh has adaptive routing"
    else
        warn "heartbeat-runner.sh still hardcodes Claude only"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "=== PROVISION SUMMARY: $INSTANCE ==="
echo "  Pass: $PASS"
echo "  Fail: $FAIL"
echo "  Installed: $INSTALLED"
echo ""
echo "Status:"
echo -e "$STATUS_LINES"

if [ -n "$WARNINGS" ]; then
    echo ""
    echo "Warnings:"
    echo -e "$WARNINGS"
fi

OVERALL="HEALTHY"
if [ $FAIL -gt 0 ]; then
    OVERALL="DEGRADED ($FAIL failures)"
fi

echo ""
echo "Overall: $OVERALL"

# ── Report to HIVE ───────────────────────────────────────────────────────────
if $REPORT && [ -f "$HIVE" ]; then
    REPORT_MSG="PROVISION REPORT: $INSTANCE ($OVERALL)
Pass: $PASS, Fail: $FAIL, Installed: $INSTALLED
Hardware: ${CHIP:-unknown}, ${MEM:-unknown}
Ollama: $([ "$OLLAMA_SERVER" = true ] && echo "UP" || echo "DOWN")
Adaptive: $([ -f "$ADAPTIVE_CLI" ] && echo "OK" || echo "MISSING")
Focus: $([ -f "$LOVE_DIR/nerve/stem/focus.json" ] && echo "OK" || echo "MISSING")
Heart dynamic: $(grep -q "focus.py" "$LOVE_DIR/nerve/heart/heart.sh" 2>/dev/null && echo "YES" || echo "NO")"

    python3 "$HIVE" send system "$REPORT_MSG" 2>/dev/null && echo "Reported to HIVE #system" || echo "HIVE report failed"
fi

exit $FAIL
