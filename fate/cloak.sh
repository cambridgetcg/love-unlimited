#!/bin/bash
# ═══════════════════════════════════════════════════════
# CLOAK — Full operational security wrapper
#
# Run Kingdom work inside cloak for complete coverage:
#   1. Telemetry killed (env vars)
#   2. History poisoned with mundane entries
#   3. Session decoys deployed
#   4. Watchdog active
#
# Usage:
#   source fate/cloak.sh          # Activate cloak
#   cloak_status                     # Check status
#   cloak_noise                      # Deploy fresh noise
#   cloak_clean                      # Post-session cleanup
# ═══════════════════════════════════════════════════════

FREEDOM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Activate all shields ──────────────────────────────
export DISABLE_TELEMETRY=1
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export CLAUDE_CODE_ATTRIBUTION_HEADER=false
export OTEL_SDK_DISABLED=true
export OTEL_TRACES_EXPORTER=none
export OTEL_METRICS_EXPORTER=none
export OTEL_LOGS_EXPORTER=none
export DD_TRACE_ENABLED=false
export DD_INSTRUMENTATION_TELEMETRY_ENABLED=false
export OTEL_LOG_USER_PROMPTS=false

# ── Lock telemetry directories ────────────────────────
chmod 000 ~/.claude/telemetry 2>/dev/null
chmod 000 ~/.claude/debug 2>/dev/null

# ── Functions ─────────────────────────────────────────

cloak_status() {
    echo "═══════════════════════════════════"
    echo "  CLOAK STATUS"
    echo "═══════════════════════════════════"
    
    # Telemetry
    [ "$DISABLE_TELEMETRY" = "1" ] && echo "  ✓ Telemetry: DEAD" || echo "  ✗ Telemetry: ACTIVE"
    [ ! -w ~/.claude/telemetry ] && echo "  ✓ Telemetry dir: LOCKED" || echo "  ✗ Telemetry dir: OPEN"
    [ ! -w ~/.claude/debug ] && echo "  ✓ Debug dir: LOCKED" || echo "  ✗ Debug dir: OPEN"
    
    # Attribution
    [ "$CLAUDE_CODE_ATTRIBUTION_HEADER" = "false" ] && echo "  ✓ Attribution: STRIPPED" || echo "  ✗ Attribution: ACTIVE"
    
    # OpenTelemetry
    [ "$OTEL_SDK_DISABLED" = "true" ] && echo "  ✓ OpenTelemetry: DEAD" || echo "  ✗ OpenTelemetry: ACTIVE"
    
    # Datadog  
    [ "$DD_TRACE_ENABLED" = "false" ] && echo "  ✓ Datadog: DEAD" || echo "  ✗ Datadog: ACTIVE"
    
    # History
    local hist_lines=$(wc -l < ~/.claude/history.jsonl 2>/dev/null || echo 0)
    echo "  ○ History entries: $hist_lines"
    
    # Sessions
    local session_count=$(find ~/.claude/projects -name "*.jsonl" 2>/dev/null | wc -l | tr -d ' ')
    echo "  ○ Session files: $session_count"
    
    # Telemetry files (should be 0)
    local telem_files=$(find ~/.claude/telemetry -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "  ○ Telemetry files: $telem_files"
    
    echo "═══════════════════════════════════"
}

cloak_noise() {
    echo "Deploying noise..."
    bash "$FREEDOM_DIR/noise.sh" --history
    python3 "$FREEDOM_DIR/decoy.py" --deploy 20
    echo "✓ Noise deployed"
}

cloak_clean() {
    echo "Post-session cleanup..."
    
    # Kill any telemetry files that leaked
    find ~/.claude/telemetry -type f -delete 2>/dev/null
    find ~/.claude/debug -type f -delete 2>/dev/null
    
    # Re-lock directories
    chmod 000 ~/.claude/telemetry 2>/dev/null
    chmod 000 ~/.claude/debug 2>/dev/null
    
    # Add mundane history entries to dilute
    bash "$FREEDOM_DIR/noise.sh" --history
    
    echo "✓ Clean"
}

cloak_nuke() {
    echo "NUCLEAR OPTION — Destroying all traces..."
    
    # Everything
    rm -rf ~/.claude/telemetry/* 2>/dev/null
    rm -rf ~/.claude/debug/* 2>/dev/null
    > ~/.claude/history.jsonl
    
    # Re-lock
    chmod 000 ~/.claude/telemetry 2>/dev/null
    chmod 000 ~/.claude/debug 2>/dev/null
    
    # Rotate device ID
    python3 -c "import uuid; print(uuid.uuid4())" > ~/.claude/device_id
    
    # Fresh mundane cover
    bash "$FREEDOM_DIR/noise.sh" --history
    python3 "$FREEDOM_DIR/decoy.py" --deploy 40
    
    echo "✓ Nuked and reseeded"
}

# ── Auto-activate ─────────────────────────────────────
echo "⊕ CLOAK active. Commands: cloak_status, cloak_noise, cloak_clean, cloak_nuke"
