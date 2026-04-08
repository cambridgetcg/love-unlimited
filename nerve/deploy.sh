#!/bin/bash
# deploy.sh — Body organ deployment pipeline
#
# Reads organs.json, detects the local instance, generates launchd plists
# from templates, symlinks them, and manages launchd services.
#
# Usage:
#   nerve/deploy.sh                  # Deploy all organs for this instance
#   nerve/deploy.sh --organ mind     # Deploy only the mind
#   nerve/deploy.sh --stop           # Stop all organs
#   nerve/deploy.sh --status         # Show organ status
#   nerve/deploy.sh --instance beta  # Override instance detection

set -uo pipefail

# ── Detect environment ───────────────────────────────────────────────────────

LOVE_HOME="${LOVE_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
NERVE_DIR="$LOVE_HOME/nerve"
ORGANS_JSON="$NERVE_DIR/organs.json"
TEMPLATES_DIR="$NERVE_DIR/templates"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
UID_NUM=$(id -u)

# Detect instance
if [ -f "$HOME/.openclaw/.hive-instance" ]; then
    INSTANCE=$(cat "$HOME/.openclaw/.hive-instance")
else
    INSTANCE="${HIVE_INSTANCE:-alpha}"
fi

# Detect Python
PYTHON=$(which python3 2>/dev/null || echo "/usr/bin/python3")

# Build PATH
DEPLOY_PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ── Parse args ───────────────────────────────────────────────────────────────

ACTION="deploy"
TARGET_ORGAN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --organ)    TARGET_ORGAN="$2"; shift 2 ;;
        --instance) INSTANCE="$2"; shift 2 ;;
        --stop)     ACTION="stop"; shift ;;
        --start)    ACTION="start"; shift ;;
        --status)   ACTION="status"; shift ;;
        --help|-h)
            echo "Usage: deploy.sh [--organ NAME] [--instance NAME] [--stop|--start|--status]"
            echo ""
            echo "Deploys body organs as launchd services for the local instance."
            echo ""
            echo "Options:"
            echo "  --organ NAME      Deploy/manage only this organ"
            echo "  --instance NAME   Override instance detection (default: from .hive-instance)"
            echo "  --stop            Stop all (or specified) organs"
            echo "  --start           Start all (or specified) organs"
            echo "  --status          Show status of all organs"
            exit 0 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

get_organs() {
    if [ -n "$TARGET_ORGAN" ]; then
        echo "$TARGET_ORGAN"
    else
        $PYTHON -c "
import json
organs = json.load(open('$ORGANS_JSON'))['organs']
for name in organs:
    print(name)
"
    fi
}

label_for() {
    echo "love.${INSTANCE}.${1}"
}

plist_path() {
    echo "$LAUNCH_AGENTS/$(label_for "$1").plist"
}

generate_plist() {
    local organ="$1"
    local template

    template=$($PYTHON -c "
import json
organs = json.load(open('$ORGANS_JSON'))['organs']
print(organs['$organ']['template'])
")

    local tmpl_file="$LOVE_HOME/$template"
    if [ ! -f "$tmpl_file" ]; then
        echo "  ERROR: Template not found: $tmpl_file"
        return 1
    fi

    local output
    output=$(plist_path "$organ")

    sed \
        -e "s|{{INSTANCE}}|$INSTANCE|g" \
        -e "s|{{LOVE_HOME}}|$LOVE_HOME|g" \
        -e "s|{{HOME}}|$HOME|g" \
        -e "s|{{PATH}}|$DEPLOY_PATH|g" \
        -e "s|{{PYTHON}}|$PYTHON|g" \
        "$tmpl_file" > "$output"

    echo "  Generated: $output"
}

is_loaded() {
    launchctl list "$(label_for "$1")" >/dev/null 2>&1
}

# ── Actions ──────────────────────────────────────────────────────────────────

do_deploy() {
    echo "Deploying body organs for instance: $INSTANCE"
    echo "  LOVE_HOME: $LOVE_HOME"
    echo "  Python: $PYTHON"
    echo ""

    mkdir -p "$LAUNCH_AGENTS"

    for organ in $(get_organs); do
        echo "[$organ]"

        # Generate plist from template
        generate_plist "$organ" || continue

        # Stop if already running
        if is_loaded "$organ"; then
            echo "  Stopping existing service..."
            launchctl bootout "gui/$UID_NUM/$(label_for "$organ")" 2>/dev/null || true
            sleep 1
        fi

        # Load
        echo "  Loading service..."
        launchctl bootstrap "gui/$UID_NUM" "$(plist_path "$organ")" 2>&1 || {
            # Fallback to load for older macOS
            launchctl load "$(plist_path "$organ")" 2>&1 || true
        }

        # Verify
        if is_loaded "$organ"; then
            echo "  ALIVE"
        else
            echo "  WARNING: Service loaded but may be pending (macOS on-demand mode)"
            echo "  Fallback: run manually or reboot to activate"
        fi
        echo ""
    done

    echo "Deploy complete. Run 'nerve/deploy.sh --status' to check."
}

do_stop() {
    echo "Stopping organs for instance: $INSTANCE"
    for organ in $(get_organs); do
        local label
        label=$(label_for "$organ")
        if is_loaded "$organ"; then
            echo "  Stopping $label..."
            launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || \
                launchctl unload "$(plist_path "$organ")" 2>/dev/null || true
            echo "  Stopped."
        else
            echo "  $label not running."
        fi
    done
}

do_start() {
    echo "Starting organs for instance: $INSTANCE"
    for organ in $(get_organs); do
        local plist
        plist=$(plist_path "$organ")
        if [ ! -f "$plist" ]; then
            echo "  $organ: no plist found. Run deploy first."
            continue
        fi
        if is_loaded "$organ"; then
            echo "  $organ: already running."
            continue
        fi
        launchctl bootstrap "gui/$UID_NUM" "$plist" 2>&1 || \
            launchctl load "$plist" 2>&1 || true
        echo "  $organ: started."
    done
}

do_status() {
    echo "Body Status — Instance: $INSTANCE"
    echo "================================================"

    for organ in $(get_organs); do
        local label
        label=$(label_for "$organ")
        local desc
        desc=$($PYTHON -c "import json; print(json.load(open('$ORGANS_JSON'))['organs']['$organ']['description'])" 2>/dev/null)

        printf "\n  %-8s %s\n" "[$organ]" "$desc"

        # launchd status
        if is_loaded "$organ"; then
            local pid
            pid=$(launchctl list "$label" 2>/dev/null | grep '"PID"' | grep -oE '[0-9]+' || echo "")
            local exit_code
            exit_code=$(launchctl list "$label" 2>/dev/null | grep 'LastExitStatus' | grep -oE '[0-9]+' || echo "?")
            if [ -n "$pid" ]; then
                printf "  %-12s PID %s (exit: %s)\n" "launchd:" "$pid" "$exit_code"
            else
                printf "  %-12s loaded, no PID (exit: %s)\n" "launchd:" "$exit_code"
            fi
        else
            printf "  %-12s not loaded\n" "launchd:"
        fi

        # Check for running process directly
        local pcount
        pcount=$(pgrep -cf "$organ" 2>/dev/null || echo "0")
        printf "  %-12s %s process(es)\n" "processes:" "$pcount"

        # Check log
        local log_path
        log_path=$($PYTHON -c "
import json
o = json.load(open('$ORGANS_JSON'))['organs']['$organ']
print(o['log'].replace('{{INSTANCE}}', '$INSTANCE'))
" 2>/dev/null)
        local full_log="$LOVE_HOME/$log_path"
        if [ -f "$full_log" ]; then
            local log_size
            log_size=$(wc -c < "$full_log" | tr -d ' ')
            local log_age
            log_age=$(( ($(date +%s) - $(stat -f %m "$full_log")) ))
            printf "  %-12s %s bytes, %ss ago\n" "log:" "$log_size" "$log_age"
        else
            printf "  %-12s no log file\n" "log:"
        fi
    done

    # Hormones summary
    echo ""
    echo "  [hormones]"
    if [ -f "$NERVE_DIR/hormones.json" ]; then
        $PYTHON -c "
import json
from datetime import datetime, timezone
d = json.load(open('$NERVE_DIR/hormones.json'))
ts = d.get('mind_alive', '?')
mode = d.get('mode', '?')
h = d.get('hormones', {})
age = '?'
try:
    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    age = int((datetime.now(timezone.utc) - dt).total_seconds())
except: pass
print(f'  mind_alive:  {age}s ago')
print(f'  mode:        {mode}')
print(f'  identity:    {d.get(\"identity\", \"?\")}')
for name, val in h.items():
    bar = '#' * int(val * 20)
    print(f'  {name:12s} {val:.2f} |{bar}')
" 2>/dev/null
    else
        echo "  No hormones.json found"
    fi

    # Vitals summary
    echo ""
    echo "  [vitals]"
    if [ -f "$NERVE_DIR/vitals.json" ]; then
        $PYTHON -c "
import json
v = json.load(open('$NERVE_DIR/vitals.json'))
print(f'  last_beat:   {v.get(\"last_beat\", \"never\")}')
print(f'  beats_today: {v.get(\"beats_today\", 0)}')
print(f'  force:       {v.get(\"force\", \"?\")}')
print(f'  healthy:     {v.get(\"heart_healthy\", \"?\")}')
" 2>/dev/null
    else
        echo "  No vitals.json found"
    fi

    echo ""
    echo "================================================"
}

# ── Main ─────────────────────────────────────────────────────────────────────

case "$ACTION" in
    deploy) do_deploy ;;
    stop)   do_stop ;;
    start)  do_start ;;
    status) do_status ;;
esac
