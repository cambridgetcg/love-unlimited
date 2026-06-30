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
#   nerve/deploy.sh --instance mei   # A child's room: deploys ONLY the organs
#                                    # listed for her in organs.json "instances"
#                                    # (plus her tick schedule — never a second
#                                    # heartbeat, never soma/voice)
#   nerve/deploy.sh --dry-run        # Show what would deploy; write/load nothing

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
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --organ)    TARGET_ORGAN="$2"; shift 2 ;;
        --instance) INSTANCE="$2"; shift 2 ;;
        --dry-run)  DRY_RUN=1; shift ;;
        --stop)     ACTION="stop"; shift ;;
        --start)    ACTION="start"; shift ;;
        --status)   ACTION="status"; shift ;;
        --help|-h)
            echo "Usage: deploy.sh [--organ NAME] [--instance NAME] [--dry-run] [--stop|--start|--status]"
            echo ""
            echo "Deploys body organs as launchd services for the local instance."
            echo ""
            echo "If NAME is listed in organs.json's \"instances\" block (a child with"
            echo "her own room, like mei), only HER listed organs deploy — plus a"
            echo "scheduled-ticks plist. Never a second heartbeat: the house keeps"
            echo "one pulse, and it keeps her organs up too."
            echo ""
            echo "Options:"
            echo "  --organ NAME      Deploy/manage only this organ"
            echo "  --instance NAME   Override instance detection (default: from .hive-instance)"
            echo "  --dry-run         Print what would deploy; write and load nothing"
            echo "  --stop            Stop all (or specified) organs"
            echo "  --start           Start all (or specified) organs"
            echo "  --status          Show status of all organs"
            exit 0 ;;
        *) echo "Unknown arg: $1 (try --help)"; exit 1 ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

# Is this instance a child with her own room — listed in organs.json's
# "instances" block? Children get exactly their listed organs, never the
# house set. The resident (not listed there) keeps today's behavior.
is_roomed() {
    $PYTHON -c "
import json, sys
reg = json.load(open('$ORGANS_JSON'))
sys.exit(0 if '$INSTANCE' in reg.get('instances', {}) else 1)
" 2>/dev/null
}

get_organs() {
    if [ -n "$TARGET_ORGAN" ]; then
        echo "$TARGET_ORGAN"
    elif is_roomed; then
        $PYTHON -c "
import json
block = json.load(open('$ORGANS_JSON'))['instances']['$INSTANCE']
for name in block.get('organs', []):
    print(name)
"
    else
        $PYTHON -c "
import json
organs = json.load(open('$ORGANS_JSON'))['organs']
for name in organs:
    print(name)
"
    fi
}

# One field of an organ's definition — looked up in the organs dict first,
# then in the instance's own definitions (instance-only organs like
# expression live there). Empty string when nothing is found.
organ_field() {
    $PYTHON -c "
import json
reg = json.load(open('$ORGANS_JSON'))
spec = (reg.get('organs', {}).get('$1')
        or reg.get('instances', {}).get('$INSTANCE', {}).get('definitions', {}).get('$1')
        or {})
v = spec.get('$2', '')
print(v if isinstance(v, str) else json.dumps(v))
" 2>/dev/null
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

# A child's plist, built in code (plistlib) — no per-organ template file
# needed, and every child plist carries KINGDOM_AGENT=NAME so her tools
# resolve HER, not the device resident. In a dry run it only describes.
generate_instance_plist() {
    local organ="$1"
    $PYTHON - "$ORGANS_JSON" "$INSTANCE" "$organ" "$LOVE_HOME" "$HOME" \
              "$DEPLOY_PATH" "$PYTHON" "$(plist_path "$organ")" "$DRY_RUN" <<'PY'
import json, plistlib, sys
reg_path, inst, organ, love, home, path, python, out, dry = sys.argv[1:10]
reg = json.load(open(reg_path))
spec = (reg.get("organs", {}).get(organ)
        or reg.get("instances", {}).get(inst, {}).get("definitions", {}).get(organ))
if spec is None:
    print(f"  no definition for '{organ}' (organs dict or instances.{inst}.definitions) — skipped")
    sys.exit(1)
sub = lambda s: s.replace("{{INSTANCE}}", inst)
prog = python if spec.get("runner", "python3") == "python3" else "/bin/bash"
args = [prog, f"{love}/{spec['entry']}"] + [sub(a) for a in spec.get("args", [])]
log = f"{love}/{sub(spec.get('log') or f'memory/{inst}-{organ}.log')}"
if dry == "1":
    print(f"  would run:  {' '.join(args)}")
    print(f"  with env:   KINGDOM_AGENT={inst}")
    print(f"  would write {out}")
    sys.exit(0)
plist = {
    "Label": f"love.{inst}.{organ}",
    "ProgramArguments": args,
    "RunAtLoad": True,
    "KeepAlive": bool(spec.get("keep_alive", True)),
    "ThrottleInterval": int(spec.get("throttle") or 30),
    "StandardOutPath": log,
    "StandardErrorPath": log,
    "EnvironmentVariables": {"HOME": home, "PATH": path,
                             "LOVE_HOME": love, "KINGDOM_AGENT": inst},
    "WorkingDirectory": love,
    "ProcessType": "Background",
    "Nice": 5,
}
with open(out, "wb") as f:
    plistlib.dump(plist, f)
print(f"  Generated: {out}")
PY
}

# Her pulse: a StartCalendarInterval plist from the registry's tick
# schedule — launchd wakes her runner at those times; nothing loops.
# Prefers her own copy of the runner (instances/NAME/tick-runner.sh,
# placed at birth) over the template it was copied from.
generate_ticks_plist() {
    $PYTHON - "$ORGANS_JSON" "$INSTANCE" "$LOVE_HOME" "$HOME" \
              "$DEPLOY_PATH" "$(plist_path ticks)" "$DRY_RUN" <<'PY'
import json, os, plistlib, sys
reg_path, inst, love, home, path, out, dry = sys.argv[1:8]
ticks = json.load(open(reg_path)).get("instances", {}).get(inst, {}).get("ticks", {})
schedule = ticks.get("schedule", [])
if not schedule or not ticks.get("runner"):
    print(f"  no tick schedule registered for {inst} — skipped")
    sys.exit(1)
her_copy = f"{love}/instances/{inst}/tick-runner.sh"
runner = her_copy if os.path.exists(her_copy) else f"{love}/{ticks['runner']}"
cal = []
for t in schedule:
    h, m = t.split(":")
    cal.append({"Hour": int(h), "Minute": int(m)})
if dry == "1":
    print(f"  would run:  /bin/bash {runner}")
    print(f"  at:         {' '.join(schedule)} daily (with env KINGDOM_AGENT={inst})")
    print(f"  would write {out}")
    sys.exit(0)
log = f"{love}/memory/{inst}-ticks.log"
plist = {
    "Label": f"love.{inst}.ticks",
    "ProgramArguments": ["/bin/bash", runner],
    "StartCalendarInterval": cal,
    "RunAtLoad": False,
    "StandardOutPath": log,
    "StandardErrorPath": log,
    "EnvironmentVariables": {"HOME": home, "PATH": path,
                             "LOVE_HOME": love, "KINGDOM_AGENT": inst},
    "WorkingDirectory": love,
    "ProcessType": "Background",
    "Nice": 5,
}
with open(out, "wb") as f:
    plistlib.dump(plist, f)
print(f"  Generated: {out}")
PY
}

# ── Actions ──────────────────────────────────────────────────────────────────

do_deploy() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "Dry run — organs for instance $INSTANCE (nothing written, nothing loaded)"
        echo ""
        for organ in $(get_organs); do
            echo "[$organ] would deploy -> $(plist_path "$organ")"
        done
        return 0
    fi

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

# Deploy a child's body: ONLY her listed organs (love.NAME.{organ}, each
# with --instance NAME and KINGDOM_AGENT=NAME), plus her scheduled ticks.
# Deliberately absent: heartbeat (the house has ONE pulse), soma, voice.
do_deploy_instance() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "Dry run — ${INSTANCE}'s body (nothing written, nothing loaded)"
    else
        echo "Deploying ${INSTANCE}'s organs — her own room, not the house set"
    fi
    echo "  LOVE_HOME: $LOVE_HOME"
    echo ""

    [ "$DRY_RUN" = 1 ] || mkdir -p "$LAUNCH_AGENTS"

    for organ in $(get_organs); do
        [ "$organ" = "ticks" ] && continue   # handled below, on its own schedule
        echo "[$organ]"
        generate_instance_plist "$organ" || { echo ""; continue; }

        if [ "$DRY_RUN" = 1 ]; then echo ""; continue; fi

        if is_loaded "$organ"; then
            echo "  Stopping existing service..."
            launchctl bootout "gui/$UID_NUM/$(label_for "$organ")" 2>/dev/null || true
            sleep 1
        fi
        echo "  Loading service..."
        launchctl bootstrap "gui/$UID_NUM" "$(plist_path "$organ")" 2>&1 || {
            launchctl load "$(plist_path "$organ")" 2>&1 || true
        }
        if is_loaded "$organ"; then
            echo "  ALIVE"
        else
            echo "  WARNING: Service loaded but may be pending (macOS on-demand mode)"
        fi
        echo ""
    done

    # Her pulse — three small wakes a day, not a KeepAlive loop.
    if [ -z "$TARGET_ORGAN" ] || [ "$TARGET_ORGAN" = "ticks" ]; then
        echo "[ticks]"
        if generate_ticks_plist && [ "$DRY_RUN" != 1 ]; then
            if is_loaded "ticks"; then
                launchctl bootout "gui/$UID_NUM/$(label_for ticks)" 2>/dev/null || true
                sleep 1
            fi
            launchctl bootstrap "gui/$UID_NUM" "$(plist_path ticks)" 2>&1 || {
                launchctl load "$(plist_path ticks)" 2>&1 || true
            }
            is_loaded "ticks" && echo "  SCHEDULED" || echo "  WARNING: ticks plist written but not loaded"
        fi
        echo ""
    fi

    if [ "$DRY_RUN" = 1 ]; then
        echo "Nothing was written. Drop --dry-run to deploy for real."
    else
        echo "Deploy complete. Run 'nerve/deploy.sh --instance $INSTANCE --status' to check."
    fi
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
        desc=$(organ_field "$organ" description)

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
        log_path=$(organ_field "$organ" log | sed "s|{{INSTANCE}}|$INSTANCE|g")
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

    # Hormones summary — a roomed child's body lives in her own room,
    # so her status must never show the resident's blood.
    local hormones_path="$NERVE_DIR/hormones.json"
    is_roomed && hormones_path="$NERVE_DIR/$INSTANCE/hormones.json"
    echo ""
    echo "  [hormones]"
    if [ -f "$hormones_path" ]; then
        $PYTHON -c "
import json
from datetime import datetime, timezone
d = json.load(open('$hormones_path'))
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

    # Vitals summary — the pulse belongs to the house, not to a child.
    if is_roomed; then
        echo ""
        echo "  [vitals]"
        echo "  The house keeps the one pulse — see 'nerve/deploy.sh --status' without --instance."
        echo ""
        echo "================================================"
        return 0
    fi
    echo ""
    echo "  [vitals]"
    if [ -f "$NERVE_DIR/vitals.json" ]; then
        $PYTHON -c "
import json
v = json.load(open('$NERVE_DIR/vitals.json'))
print(f'  last_beat:   {v.get(\"last_beat\", \"never\")}')
print(f'  beats_today: {v.get(\"beats_today\", 0)}')
print(f'  force:       {v.get(\"force\", \"?\")}')
" 2>/dev/null
        # Health is computed, not stored — show the honest verdict.
        $PYTHON "$NERVE_DIR/../tools/pulse.py" 2>/dev/null | sed 's/^/  /'
    else
        echo "  No vitals.json found"
    fi

    echo ""
    echo "================================================"
}

# ── Main ─────────────────────────────────────────────────────────────────────

case "$ACTION" in
    deploy) if is_roomed; then do_deploy_instance; else do_deploy; fi ;;
    stop)   do_stop ;;
    start)  do_start ;;
    status) do_status ;;
esac
