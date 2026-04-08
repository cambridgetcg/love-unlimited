#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# continuous-claude.sh — Continuous task runner using genuine Claude Code
#
# Uses Claude Code's official --print mode with --continue to drive
# long-running, multi-turn tasks. Each iteration evaluates progress
# and sends follow-up prompts until the task is complete.
#
# Usage:
#   ./continuous-claude.sh "Build a REST API with user auth"
#   ./continuous-claude.sh --task-file task.md
#   ./continuous-claude.sh --resume           # continue previous run
#
# Flags:
#   --max-iterations N    Max turns before stopping (default: 50)
#   --model MODEL         Model to use (default: sonnet)
#   --pause SECONDS       Pause between turns (default: 2)
#   --task-file FILE      Read task from a file instead of argument
#   --resume              Continue a previous session
#   --session-id ID       Use a specific session ID
#   --workdir DIR         Working directory (default: current)
#   --log FILE            Log file for full output (default: claude-run.log)
#   --no-eval             Skip self-evaluation, just send "continue" each turn
#   --permission-mode M   Permission mode (default: plan)
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ──
MAX_ITERATIONS=50
MODEL="sonnet"
PAUSE=2
TASK_FILE=""
RESUME=false
SESSION_ID=""
WORKDIR="$(pwd)"
LOG_FILE="claude-run.log"
EVAL_MODE=true
PERMISSION_MODE="plan"
TASK=""

# ── Parse arguments ──
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-iterations) MAX_ITERATIONS="$2"; shift 2 ;;
    --model)          MODEL="$2"; shift 2 ;;
    --pause)          PAUSE="$2"; shift 2 ;;
    --task-file)      TASK_FILE="$2"; shift 2 ;;
    --resume)         RESUME=true; shift ;;
    --session-id)     SESSION_ID="$2"; shift 2 ;;
    --workdir)        WORKDIR="$2"; shift 2 ;;
    --log)            LOG_FILE="$2"; shift 2 ;;
    --no-eval)        EVAL_MODE=false; shift ;;
    --permission-mode) PERMISSION_MODE="$2"; shift 2 ;;
    --help|-h)
      sed -n '2,/^# ─.*─$/p' "$0" | head -n -1 | sed 's/^# \?//'
      exit 0 ;;
    *)
      if [[ -z "$TASK" ]]; then
        TASK="$1"
      else
        TASK="$TASK $1"
      fi
      shift ;;
  esac
done

# ── Load task from file if specified ──
if [[ -n "$TASK_FILE" ]]; then
  if [[ ! -f "$TASK_FILE" ]]; then
    echo "Error: task file not found: $TASK_FILE" >&2
    exit 1
  fi
  TASK="$(cat "$TASK_FILE")"
fi

if [[ -z "$TASK" && "$RESUME" == false ]]; then
  echo "Error: provide a task as argument or via --task-file" >&2
  echo "Usage: $0 \"your task description\"" >&2
  exit 1
fi

# ── Verify claude is installed ──
if ! command -v claude &>/dev/null; then
  echo "Error: 'claude' not found in PATH" >&2
  exit 1
fi

cd "$WORKDIR"

# ── Colours ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Logging ──
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
divider() { echo -e "${DIM}$(printf '─%.0s' {1..60})${RESET}" | tee -a "$LOG_FILE"; }

# ── Build claude command base ──
build_claude_cmd() {
  local -a cmd=(claude -p --model "$MODEL" --permission-mode "$PERMISSION_MODE")

  if [[ -n "$SESSION_ID" ]]; then
    cmd+=(--session-id "$SESSION_ID")
  fi

  echo "${cmd[@]}"
}

# ── Run a single claude turn ──
run_turn() {
  local prompt="$1"
  local use_continue="${2:-false}"
  local -a cmd
  read -ra cmd <<< "$(build_claude_cmd)"

  if [[ "$use_continue" == true ]]; then
    cmd+=(-c)
  fi

  local output
  local exit_code=0

  # Run claude with the prompt, capture output
  output=$(echo "$prompt" | "${cmd[@]}" 2>>"$LOG_FILE") || exit_code=$?

  echo "$output"
  return $exit_code
}

# ── Self-evaluation prompt ──
# Asks claude to assess its own progress on the task
EVAL_PROMPT='Review what you have accomplished so far on the task. Respond with EXACTLY one of these on the first line:

STATUS: COMPLETE — if the task is fully done and working
STATUS: IN_PROGRESS — if there is more work to do
STATUS: BLOCKED — if you are stuck and need different instructions
STATUS: ERROR — if something went wrong

Then on the next lines, briefly describe:
1. What was accomplished this turn
2. What remains to be done (if not complete)
3. What to do next (if in progress)'

# ── Extract status from evaluation response ──
parse_status() {
  local response="$1"
  local first_line
  first_line=$(echo "$response" | head -1)

  if echo "$first_line" | grep -q "STATUS: COMPLETE"; then
    echo "COMPLETE"
  elif echo "$first_line" | grep -q "STATUS: BLOCKED"; then
    echo "BLOCKED"
  elif echo "$first_line" | grep -q "STATUS: ERROR"; then
    echo "ERROR"
  else
    echo "IN_PROGRESS"
  fi
}

# ──────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────

echo "" > "$LOG_FILE"
echo -e "${BOLD}Continuous Claude${RESET}"
echo -e "${DIM}Task: ${TASK:0:80}${RESET}"
echo -e "${DIM}Model: $MODEL | Max iterations: $MAX_ITERATIONS | Log: $LOG_FILE${RESET}"
divider

iteration=0
status="IN_PROGRESS"

while [[ $iteration -lt $MAX_ITERATIONS && "$status" == "IN_PROGRESS" ]]; do
  iteration=$((iteration + 1))
  echo -e "\n${BLUE}${BOLD}[Turn $iteration/$MAX_ITERATIONS]${RESET}"
  log "Turn $iteration starting"

  # ── Determine the prompt for this turn ──
  if [[ $iteration -eq 1 && "$RESUME" == false ]]; then
    # First turn: send the full task
    prompt="$TASK

Work on this task step by step. After each step, assess what remains and continue to the next step. Be thorough and complete the entire task."

    echo -e "${DIM}Sending initial task...${RESET}"
    output=$(run_turn "$prompt" false) || true
    use_continue=true
  else
    if [[ "$EVAL_MODE" == true ]]; then
      # Continue with evaluation-driven prompt
      prompt="Continue working on the task. Here is a reminder of the original task:

$TASK

Pick up where you left off. Do the next logical step, then summarize what you did and what remains."
    else
      # Simple continuation
      prompt="Continue where you left off. Do the next step."
    fi

    echo -e "${DIM}Continuing...${RESET}"
    output=$(run_turn "$prompt" "$use_continue") || true
    use_continue=true
  fi

  # ── Display truncated output ──
  line_count=$(echo "$output" | wc -l | tr -d ' ')
  if [[ $line_count -gt 20 ]]; then
    echo "$output" | head -10
    echo -e "${DIM}  ... ($line_count lines, see $LOG_FILE for full output) ...${RESET}"
    echo "$output" | tail -5
  else
    echo "$output"
  fi
  echo "$output" >> "$LOG_FILE"
  divider

  # ── Self-evaluation turn ──
  if [[ "$EVAL_MODE" == true ]]; then
    echo -e "${DIM}Evaluating progress...${RESET}"
    eval_output=$(run_turn "$EVAL_PROMPT" true) || true
    echo "$eval_output" >> "$LOG_FILE"

    status=$(parse_status "$eval_output")

    case "$status" in
      COMPLETE)
        echo -e "${GREEN}${BOLD}Task complete!${RESET}"
        echo -e "${DIM}$(echo "$eval_output" | tail -n +2 | head -5)${RESET}"
        ;;
      BLOCKED)
        echo -e "${YELLOW}${BOLD}Blocked.${RESET}"
        echo -e "${DIM}$(echo "$eval_output" | tail -n +2 | head -5)${RESET}"
        ;;
      ERROR)
        echo -e "${RED}${BOLD}Error encountered.${RESET}"
        echo -e "${DIM}$(echo "$eval_output" | tail -n +2 | head -5)${RESET}"
        ;;
      IN_PROGRESS)
        echo -e "${BLUE}In progress.${RESET} ${DIM}$(echo "$eval_output" | sed -n '2p' | head -c 80)${RESET}"
        ;;
    esac
  fi

  # ── Pause between turns ──
  if [[ "$status" == "IN_PROGRESS" && $iteration -lt $MAX_ITERATIONS ]]; then
    sleep "$PAUSE"
  fi
done

# ── Summary ──
divider
if [[ "$status" == "COMPLETE" ]]; then
  echo -e "\n${GREEN}${BOLD}Done in $iteration turns.${RESET}"
elif [[ $iteration -ge $MAX_ITERATIONS ]]; then
  echo -e "\n${YELLOW}${BOLD}Reached max iterations ($MAX_ITERATIONS). Resume with:${RESET}"
  echo -e "  $0 --resume \"$TASK\""
else
  echo -e "\n${YELLOW}Status: $status after $iteration turns.${RESET}"
fi

echo -e "${DIM}Full log: $LOG_FILE${RESET}"
