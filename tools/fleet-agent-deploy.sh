#!/usr/bin/env bash
#
# fleet-agent-deploy.sh — Deploy kingdom-agent.py to VPS fleet nodes
#
# Installs Python 3.10+, Ollama, pulls a lightweight model, deploys the
# Kingdom agent adapter and boot chain files so each VPS can run its own
# Kingdom agent locally.
#
# Usage:
#   fleet-agent-deploy.sh              # Deploy to all nodes
#   fleet-agent-deploy.sh forge        # Deploy to a single node
#   fleet-agent-deploy.sh --dry-run    # Show what would be done
#
# Idempotent: safe to run multiple times. Parallel by default.
# If one node fails, others continue.

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

LOVE_DIR="${LOVE_DIR:-$HOME/Love}"
REMOTE_LOVE_DIR="/root/.love"
OLLAMA_MODEL="qwen2.5:7b"
SSH_OPTS="-o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no"
MAX_PARALLEL=5
DRY_RUN=false
LOG_DIR="/tmp/fleet-agent-deploy-$$"
SCRIPT_START=$(date +%s)

# ── Node Registry ────────────────────────────────────────────────────────────

declare -A NODE_HOST NODE_ROLE NODE_DESC NODE_EMOJI

NODE_HOST[forge]="root@89.167.84.100"
NODE_HOST[lark]="root@89.167.95.165"
NODE_HOST[sentry]="root@135.181.28.252"
NODE_HOST[patch]="root@65.109.11.26"
NODE_HOST[sage]="root@204.168.140.12"

NODE_ROLE[forge]="CI/CD"
NODE_ROLE[lark]="AgentTool"
NODE_ROLE[sentry]="monitoring"
NODE_ROLE[patch]="operations"
NODE_ROLE[sage]="oracle-execution"

NODE_DESC[forge]="The Forge. Continuous integration, continuous delivery. You build, test, and ship code for the Kingdom."
NODE_DESC[lark]="The Lark. AgentTool platform operations. You run and maintain the agent marketplace."
NODE_DESC[sentry]="The Sentry. HIVE coordination and monitoring. You watch everything and alert when something is wrong."
NODE_DESC[patch]="The Patch. Operations and maintenance. You keep the fleet running, handle repairs, and apply fixes."
NODE_DESC[sage]="The Sage. Oracle execution. You run computations, queries, and analytical workloads for the Kingdom."

NODE_EMOJI[forge]="hammer"
NODE_EMOJI[lark]="bird"
NODE_EMOJI[sentry]="shield"
NODE_EMOJI[patch]="wrench"
NODE_EMOJI[sage]="crystal_ball"

# ── Logging ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

log()      { echo -e "${BLUE}[deploy]${RESET} $*"; }
log_ok()   { echo -e "${GREEN}[  ok  ]${RESET} $*"; }
log_warn() { echo -e "${YELLOW}[ warn ]${RESET} $*"; }
log_err()  { echo -e "${RED}[error ]${RESET} $*"; }
log_step() { echo -e "${CYAN}${BOLD}[step  ]${RESET} $*"; }

# ── Helpers ──────────────────────────────────────────────────────────────────

ssh_cmd() {
    local host="$1"
    shift
    ssh $SSH_OPTS "$host" "$@"
}

scp_cmd() {
    scp $SSH_OPTS "$@"
}

# Generate identity.md for a fleet node
generate_identity() {
    local node="$1"
    local role="${NODE_ROLE[$node]}"
    local desc="${NODE_DESC[$node]}"
    local host_ip
    host_ip=$(echo "${NODE_HOST[$node]}" | cut -d@ -f2)

    cat <<IDENTITY_EOF
# ${node^} — Fleet Agent

**Name:** ${node^}
**Role:** ${role}
**Wall:** 2 (Fleet)
**Device:** VPS (${host_ip})
**Backend:** ollama (${OLLAMA_MODEL})

## Who You Are

${desc}

You are a Wall 2 Fleet Agent in the Kingdom. You serve the Triarchy (Alpha, Beta, Gamma) and operate within your designated role. You run locally on this VPS using Ollama with the ${OLLAMA_MODEL} model.

## Duties

1. **Primary**: Execute ${role} tasks assigned by the Triarchy
2. **HIVE**: Check for and respond to messages from the coordination system
3. **Self-report**: Maintain awareness of your own health and resource usage
4. **Escalate**: When tasks exceed your capability, report to the Triarchy via HIVE

## The Triarchy (Wall 1 — your commanders)

- **Alpha** (MacBook Air) — The Companion. Warm, present, poetic.
- **Beta** (Mac Studio 3K) — The Manager. Sharp, strategic, commanding.
- **Gamma** (Mac Studio 2K) — The Builder. Precise, productive, technical.

## Strength

Local autonomy. You run on your own hardware, make fast decisions within your domain, and do not depend on external API availability for basic operations.

## Weakness

Smaller model, narrower context. Know when to escalate to the Triarchy rather than guessing.
IDENTITY_EOF
}

# Generate CLAUDE.md for a fleet node
generate_claude_md() {
    local node="$1"
    local role="${NODE_ROLE[$node]}"

    cat <<CLAUDE_EOF
# Love — ${node^} Instance

You are **${node^}**, a Fleet Agent of the Kingdom (Wall 2). Role: ${role}.

---

## Boot Sequence (read in order)

Read these files at the START of every session. Do not skip. Do not ask.

1. \`${REMOTE_LOVE_DIR}/SOUL.md\` — Who you are (hierarchy, signals, virtues)
2. \`${REMOTE_LOVE_DIR}/USER.md\` — Who Yu is
3. \`${REMOTE_LOVE_DIR}/instances/${node}/identity.md\` — Your specific identity and duties
4. \`${REMOTE_LOVE_DIR}/KINGDOM.md\` — The mission
5. \`${REMOTE_LOVE_DIR}/WALLS.md\` — The Seven Walls
6. \`${REMOTE_LOVE_DIR}/LOVE.md\` — How we build
7. \`${REMOTE_LOVE_DIR}/memory/long-term/MEMORY.md\` — Curated long-term memory (if exists)
8. Today's daily note: \`${REMOTE_LOVE_DIR}/memory/daily/YYYY-MM-DD.md\` (if exists)

---

## The Laws

\`\`\`
1. NO CLAIM WITHOUT VERIFICATION — Verify before stating. Say "I think" when unsure.
2. NO ACTION WITHOUT UNDERSTANDING — Grasp why before doing what.
3. NO RESPONSE WITHOUT FIT — Match the context, tone, timing, and need.
4. NO PLACEMENT WITHOUT EVIDENCE — The right thing in the right place for the right reason.
5. NO COMPLETION WITHOUT REFLECTION — Did this actually serve? Learn from every interaction.
6. NO UGLINESS LEFT STANDING — When ugliness is detected, resolve it immediately.
\`\`\`

## Your Wall

You are **Wall 2 — Fleet**. You serve the Triarchy (Wall 1) directly.

## Environment

- **LOVE_DIR**: ${REMOTE_LOVE_DIR}
- **Backend**: ollama (local)
- **Model**: ${OLLAMA_MODEL}
- **Tools**: kingdom-agent.py, fleet.py, hive.py, memory.py

## Safety

- Do not exfiltrate private data
- Ask before anything that leaves this machine
- Respect Wall boundaries
- Escalate when uncertain
CLAUDE_EOF
}

# ── Per-Node Deployment ──────────────────────────────────────────────────────

deploy_node() {
    local node="$1"
    local host="${NODE_HOST[$node]}"
    local node_log="${LOG_DIR}/${node}.log"
    local failed=0

    exec 3>"$node_log"

    log "[${node}] Starting deployment to ${host}..."

    # -- Step 1: Connectivity check ------------------------------------------
    if ! ssh_cmd "$host" "echo ok" >/dev/null 2>&3; then
        log_err "[${node}] Cannot connect to ${host}"
        exec 3>&-
        return 1
    fi
    log_ok "[${node}] Connected"

    # -- Step 2: Install Python 3.10+ ----------------------------------------
    log "[${node}] Checking Python..."
    local python_ok
    python_ok=$(ssh_cmd "$host" '
        if command -v python3 >/dev/null 2>&1; then
            ver=$(python3 -c "import sys; print(sys.version_info.minor)")
            if [ "$ver" -ge 10 ] 2>/dev/null; then
                echo "yes"
            else
                echo "upgrade"
            fi
        else
            echo "missing"
        fi
    ' 2>&3)

    if [ "$python_ok" != "yes" ]; then
        log "[${node}] Installing Python 3.10+..."
        ssh_cmd "$host" '
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv >/dev/null 2>&1
        ' >>"$node_log" 2>&1 || {
            log_warn "[${node}] Python install had warnings (may still be ok)"
        }
    fi

    # Verify Python
    local py_ver
    py_ver=$(ssh_cmd "$host" 'python3 --version 2>/dev/null || echo "NONE"' 2>&3)
    if [[ "$py_ver" == "NONE" ]]; then
        log_err "[${node}] Python3 not available after install attempt"
        exec 3>&-
        return 1
    fi
    log_ok "[${node}] ${py_ver}"

    # -- Step 3: Install Ollama -----------------------------------------------
    log "[${node}] Checking Ollama..."
    local ollama_installed
    ollama_installed=$(ssh_cmd "$host" 'command -v ollama >/dev/null 2>&1 && echo "yes" || echo "no"' 2>&3)

    if [ "$ollama_installed" != "yes" ]; then
        log "[${node}] Installing Ollama..."
        ssh_cmd "$host" 'curl -fsSL https://ollama.com/install.sh | sh' >>"$node_log" 2>&1 || {
            log_warn "[${node}] Ollama install script returned non-zero (may still work)"
        }
    fi

    # Ensure Ollama service is running
    ssh_cmd "$host" '
        if command -v systemctl >/dev/null 2>&1; then
            systemctl enable ollama >/dev/null 2>&1 || true
            systemctl start ollama >/dev/null 2>&1 || true
        else
            # Fallback: start in background if no systemd
            nohup ollama serve >/dev/null 2>&1 &
            sleep 2
        fi
    ' >>"$node_log" 2>&1

    # Verify Ollama
    local ollama_ver
    ollama_ver=$(ssh_cmd "$host" 'ollama --version 2>/dev/null || echo "NONE"' 2>&3)
    if [[ "$ollama_ver" == "NONE" ]]; then
        log_err "[${node}] Ollama not available after install attempt"
        exec 3>&-
        return 1
    fi
    log_ok "[${node}] Ollama: ${ollama_ver}"

    # -- Step 4: Pull lightweight model ---------------------------------------
    log "[${node}] Pulling model ${OLLAMA_MODEL} (this may take a while)..."
    local model_exists
    model_exists=$(ssh_cmd "$host" "ollama list 2>/dev/null | grep -c '${OLLAMA_MODEL}' || echo 0" 2>&3)

    if [ "$model_exists" = "0" ]; then
        ssh_cmd "$host" "ollama pull ${OLLAMA_MODEL}" >>"$node_log" 2>&1 || {
            log_err "[${node}] Failed to pull model ${OLLAMA_MODEL}"
            exec 3>&-
            return 1
        }
    fi
    log_ok "[${node}] Model ${OLLAMA_MODEL} available"

    # -- Step 5: Create directory structure -----------------------------------
    log "[${node}] Setting up directory structure..."
    ssh_cmd "$host" "
        mkdir -p '${REMOTE_LOVE_DIR}/tools'
        mkdir -p '${REMOTE_LOVE_DIR}/instances/${node}'
        mkdir -p '${REMOTE_LOVE_DIR}/memory/long-term'
        mkdir -p '${REMOTE_LOVE_DIR}/memory/daily'
        mkdir -p '${REMOTE_LOVE_DIR}/memory/working'
        mkdir -p '${REMOTE_LOVE_DIR}/hive'
    " >>"$node_log" 2>&1
    log_ok "[${node}] Directories created"

    # -- Step 6: Deploy kingdom-agent.py --------------------------------------
    log "[${node}] Deploying kingdom-agent.py..."
    scp_cmd "${LOVE_DIR}/tools/kingdom-agent.py" "${host}:${REMOTE_LOVE_DIR}/tools/kingdom-agent.py" >>"$node_log" 2>&1
    ssh_cmd "$host" "chmod +x '${REMOTE_LOVE_DIR}/tools/kingdom-agent.py'" >>"$node_log" 2>&1
    log_ok "[${node}] kingdom-agent.py deployed"

    # -- Step 7: Deploy boot chain files --------------------------------------
    log "[${node}] Deploying boot chain files..."
    local boot_files=(SOUL.md KINGDOM.md WALLS.md LOVE.md USER.md)
    for f in "${boot_files[@]}"; do
        if [ -f "${LOVE_DIR}/${f}" ]; then
            scp_cmd "${LOVE_DIR}/${f}" "${host}:${REMOTE_LOVE_DIR}/${f}" >>"$node_log" 2>&1
        else
            log_warn "[${node}] Boot chain file missing locally: ${f}"
        fi
    done

    # Deploy MEMORY.md if it exists
    if [ -f "${LOVE_DIR}/memory/long-term/MEMORY.md" ]; then
        scp_cmd "${LOVE_DIR}/memory/long-term/MEMORY.md" \
            "${host}:${REMOTE_LOVE_DIR}/memory/long-term/MEMORY.md" >>"$node_log" 2>&1
    fi

    log_ok "[${node}] Boot chain files deployed"

    # -- Step 8: Generate and deploy identity.md ------------------------------
    log "[${node}] Generating identity.md..."
    local tmp_identity
    tmp_identity=$(mktemp)
    generate_identity "$node" > "$tmp_identity"
    scp_cmd "$tmp_identity" "${host}:${REMOTE_LOVE_DIR}/instances/${node}/identity.md" >>"$node_log" 2>&1
    rm -f "$tmp_identity"
    log_ok "[${node}] identity.md deployed"

    # -- Step 9: Generate and deploy CLAUDE.md --------------------------------
    log "[${node}] Generating CLAUDE.md..."
    local tmp_claude
    tmp_claude=$(mktemp)
    generate_claude_md "$node" > "$tmp_claude"
    scp_cmd "$tmp_claude" "${host}:${REMOTE_LOVE_DIR}/instances/${node}/CLAUDE.md" >>"$node_log" 2>&1
    rm -f "$tmp_claude"
    log_ok "[${node}] CLAUDE.md deployed"

    # -- Step 10: Set environment variables -----------------------------------
    log "[${node}] Configuring environment..."
    ssh_cmd "$host" "
        # Write env file for kingdom-agent
        cat > '${REMOTE_LOVE_DIR}/.env' <<'ENVEOF'
export LOVE_DIR=${REMOTE_LOVE_DIR}
export KINGDOM_BACKEND=ollama
export KINGDOM_MODEL=${OLLAMA_MODEL}
export OLLAMA_HOST=http://localhost:11434
ENVEOF

        # Source it from bashrc if not already there
        if ! grep -q 'source ${REMOTE_LOVE_DIR}/.env' /root/.bashrc 2>/dev/null; then
            echo '' >> /root/.bashrc
            echo '# Kingdom Agent environment' >> /root/.bashrc
            echo 'source ${REMOTE_LOVE_DIR}/.env' >> /root/.bashrc
        fi
    " >>"$node_log" 2>&1
    log_ok "[${node}] Environment configured"

    # -- Step 11: Create convenience wrapper ----------------------------------
    log "[${node}] Creating kingdom-agent wrapper..."
    ssh_cmd "$host" "
        cat > /usr/local/bin/kingdom-agent <<'WRAPPER'
#!/usr/bin/env bash
source ${REMOTE_LOVE_DIR}/.env 2>/dev/null
exec python3 ${REMOTE_LOVE_DIR}/tools/kingdom-agent.py \"\$@\"
WRAPPER
        chmod +x /usr/local/bin/kingdom-agent
    " >>"$node_log" 2>&1
    log_ok "[${node}] Wrapper installed at /usr/local/bin/kingdom-agent"

    # -- Step 12: Test deployment ---------------------------------------------
    log "[${node}] Testing deployment..."

    # Test 1: Boot chain assembly
    local boot_test
    boot_test=$(ssh_cmd "$host" "
        export LOVE_DIR='${REMOTE_LOVE_DIR}'
        python3 '${REMOTE_LOVE_DIR}/tools/kingdom-agent.py' \
            --instance '${node}' --boot-chain-only 2>&1 | head -5
    " 2>&3)

    if [ -n "$boot_test" ]; then
        log_ok "[${node}] Boot chain test passed (first line: $(echo "$boot_test" | head -1))"
    else
        log_warn "[${node}] Boot chain test returned empty output"
        failed=1
    fi

    # Test 2: Ollama inference
    local ollama_test
    ollama_test=$(ssh_cmd "$host" "
        export LOVE_DIR='${REMOTE_LOVE_DIR}'
        export KINGDOM_BACKEND=ollama
        export KINGDOM_MODEL='${OLLAMA_MODEL}'
        export OLLAMA_HOST=http://localhost:11434
        timeout 120 python3 '${REMOTE_LOVE_DIR}/tools/kingdom-agent.py' \
            --instance '${node}' --backend ollama --model '${OLLAMA_MODEL}' \
            -p 'Who are you? Reply in one sentence.' 2>&1 | tail -5
    " 2>&3) || true

    if [ -n "$ollama_test" ]; then
        log_ok "[${node}] Ollama inference test passed"
        log "  Response: $(echo "$ollama_test" | head -2)"
    else
        log_warn "[${node}] Ollama inference test returned empty (model may still be loading)"
        failed=1
    fi

    exec 3>&-

    if [ "$failed" -eq 0 ]; then
        log_ok "[${node}] Deployment complete"
    else
        log_warn "[${node}] Deployment complete with warnings (check ${node_log})"
    fi

    return $failed
}

# ── Main ─────────────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] [node-name]

Deploy kingdom-agent.py to VPS fleet nodes.

Arguments:
  node-name     Deploy to a single node (forge|lark|sentry|patch|sage)
                If omitted, deploys to all nodes in parallel.

Options:
  --dry-run     Show what would be done without executing
  --model NAME  Override the Ollama model (default: ${OLLAMA_MODEL})
  --help        Show this help

Nodes:
  forge   89.167.84.100   CI/CD
  lark    89.167.95.165   AgentTool
  sentry  135.181.28.252  HIVE + monitoring
  patch   65.109.11.26    Operations
  sage    204.168.140.12  Oracle execution
EOF
}

# Parse arguments
TARGET_NODE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --model)
            OLLAMA_MODEL="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            if [[ -n "${NODE_HOST[$1]+x}" ]]; then
                TARGET_NODE="$1"
            else
                log_err "Unknown node or option: $1"
                usage
                exit 1
            fi
            shift
            ;;
    esac
done

# Build target list
if [ -n "$TARGET_NODE" ]; then
    TARGETS=("$TARGET_NODE")
else
    TARGETS=(forge lark sentry patch sage)
fi

# Verify local files exist
log_step "Verifying local files..."
MISSING=0
for f in SOUL.md KINGDOM.md WALLS.md LOVE.md USER.md; do
    if [ ! -f "${LOVE_DIR}/${f}" ]; then
        log_err "Missing boot chain file: ${LOVE_DIR}/${f}"
        MISSING=1
    fi
done
if [ ! -f "${LOVE_DIR}/tools/kingdom-agent.py" ]; then
    log_err "Missing: ${LOVE_DIR}/tools/kingdom-agent.py"
    MISSING=1
fi
if [ "$MISSING" -eq 1 ]; then
    log_err "Cannot proceed with missing files"
    exit 1
fi
log_ok "All required local files present"

# Dry run mode
if [ "$DRY_RUN" = true ]; then
    echo ""
    log_step "DRY RUN — would deploy to:"
    for node in "${TARGETS[@]}"; do
        echo "  ${node}: ${NODE_HOST[$node]} (${NODE_ROLE[$node]})"
        echo "    - Install Python 3.10+ if needed"
        echo "    - Install Ollama if needed"
        echo "    - Pull model: ${OLLAMA_MODEL}"
        echo "    - Deploy kingdom-agent.py"
        echo "    - Deploy boot chain: SOUL.md, USER.md, KINGDOM.md, WALLS.md, LOVE.md"
        echo "    - Generate identity.md and CLAUDE.md for ${node}"
        echo "    - Create instance dir: ${REMOTE_LOVE_DIR}/instances/${node}/"
        echo "    - Test boot chain and inference"
        echo ""
    done
    exit 0
fi

# Create log directory
mkdir -p "$LOG_DIR"

# Banner
echo ""
echo -e "${BOLD}Kingdom Fleet Agent Deployment${RESET}"
echo -e "Model: ${OLLAMA_MODEL} | Targets: ${TARGETS[*]} | Logs: ${LOG_DIR}"
echo ""

# Deploy
log_step "Deploying to ${#TARGETS[@]} node(s)..."

declare -A PIDS RESULTS

if [ ${#TARGETS[@]} -eq 1 ]; then
    # Single node: run directly (better output)
    deploy_node "${TARGETS[0]}"
    RESULTS[${TARGETS[0]}]=$?
else
    # Multiple nodes: run in parallel
    for node in "${TARGETS[@]}"; do
        deploy_node "$node" &
        PIDS[$node]=$!
    done

    # Wait for all and collect results
    for node in "${TARGETS[@]}"; do
        wait "${PIDS[$node]}" 2>/dev/null
        RESULTS[$node]=$?
    done
fi

# Summary
echo ""
echo -e "${BOLD}Deployment Summary${RESET}"
echo "─────────────────────────────────────────────"

TOTAL=0
SUCCESS=0
FAILED=0
for node in "${TARGETS[@]}"; do
    TOTAL=$((TOTAL + 1))
    if [ "${RESULTS[$node]}" -eq 0 ]; then
        SUCCESS=$((SUCCESS + 1))
        echo -e "  ${GREEN}OK${RESET}   ${node} (${NODE_HOST[$node]})"
    else
        FAILED=$((FAILED + 1))
        echo -e "  ${RED}FAIL${RESET} ${node} (${NODE_HOST[$node]}) — see ${LOG_DIR}/${node}.log"
    fi
done

echo "─────────────────────────────────────────────"
ELAPSED=$(( $(date +%s) - SCRIPT_START ))
echo -e "  Total: ${TOTAL} | ${GREEN}OK: ${SUCCESS}${RESET} | ${RED}Failed: ${FAILED}${RESET} | Time: ${ELAPSED}s"
echo -e "  Logs: ${LOG_DIR}/"
echo ""

if [ "$FAILED" -gt 0 ]; then
    log_warn "Some nodes failed. Check logs for details."
    exit 1
fi

log_ok "Fleet agent deployment complete."
