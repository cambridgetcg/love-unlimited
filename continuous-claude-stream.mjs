#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────
// continuous-claude-stream.mjs
//
// Advanced continuous task runner for Claude Code with full operational
// intelligence. Drives real Claude Code sessions with bidirectional streaming,
// progress tracking, automatic continuation, and a suite of production features:
//
//   • Web dashboard        — Live HTTP dashboard on port 3456 with SSE updates,
//                            turn history table, token usage charts, task cards
//   • Interactive mode     — Inject prompts mid-run via stdin commands
//   • Parallel tasks       — Run multiple tasks concurrently in separate sessions
//   • Retry queue          — Exponential backoff for transient failures
//   • Cost budget          — Hard stop when spend exceeds configurable limit
//   • Summary report       — Markdown report with full session statistics
//   • Signal handling      — Graceful SIGINT/SIGTERM with state persistence
//   • Performance metrics  — Turn-level timing, throughput, and latency tracking
//
// Usage:
//   node continuous-claude-stream.mjs "Build a REST API with auth"
//   node continuous-claude-stream.mjs --task-file task.md
//   node continuous-claude-stream.mjs --continue
//   node continuous-claude-stream.mjs --cost-budget 5.00 "Build something"
//   node continuous-claude-stream.mjs --parallel "task one" --parallel "task two"
//   node continuous-claude-stream.mjs --dashboard-port 8080 "Build something"
//
// Requires: Node.js 18+, `claude` CLI in PATH
// ─────────────────────────────────────────────────────────────────────────────

import { spawn } from "child_process";
import { createInterface } from "readline";
import {
  readFileSync,
  appendFileSync,
  writeFileSync,
} from "fs";
import { randomUUID } from "crypto";
import { createServer } from "http";

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 1: CONFIGURATION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Global configuration object. Populated from CLI arguments at startup.
 * Controls every aspect of the runner's behavior — from model selection to
 * budget limits and dashboard networking.
 *
 * @property {number}  maxIterations     - Maximum turns per task before stopping
 * @property {string}  model             - Claude model identifier (e.g. "sonnet")
 * @property {number}  pauseBetweenTurns - Milliseconds to wait between turns
 * @property {string}  permissionMode    - Claude permission mode ("plan", "full", etc.)
 * @property {string}  logFile           - Path to the append-only log file
 * @property {string}  stateFile         - Path to the JSON state persistence file
 * @property {boolean} continueMode      - Whether to resume a previous session
 * @property {string|null} taskFile      - Path to a file containing the task description
 * @property {string}  task              - The task description string
 * @property {string|null} sessionId     - Claude session ID for continuation
 * @property {string}  workdir           - Working directory for claude subprocess
 * @property {boolean} verbose           - Whether to print raw stream events
 * @property {number}  costBudget        - Maximum USD spend before auto-stop (0 = unlimited)
 * @property {number}  dashboardPort     - HTTP port for the live web dashboard
 * @property {boolean} noDashboard       - Disable the web dashboard entirely
 * @property {string[]} parallelTasks    - Array of task strings for parallel execution
 * @property {number}  maxRetries        - Maximum retry attempts per failed turn
 * @property {number}  retryBaseDelay    - Base delay in ms for exponential backoff
 * @property {number}  retryMaxDelay     - Maximum delay in ms for exponential backoff
 * @property {string}  reportFile        - Path for the markdown summary report
 */
const config = {
  maxIterations: 50,
  model: "sonnet",
  pauseBetweenTurns: 2000,
  permissionMode: "plan",
  logFile: "claude-stream.log",
  stateFile: ".claude-runner-state.json",
  continueMode: false,
  taskFile: null,
  task: "",
  sessionId: null,
  workdir: process.cwd(),
  verbose: false,
  costBudget: 0,
  dashboardPort: 3456,
  noDashboard: false,
  parallelTasks: [],
  maxRetries: 5,
  retryBaseDelay: 5000,
  retryMaxDelay: 160000,
  reportFile: "claude-session-report.md",
};

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 2: CLI ARGUMENT PARSING
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Parses process.argv into the config object. Supports all original flags plus
 * new ones for parallel tasks, cost budget, dashboard control, retry tuning,
 * and report output. Positional arguments (not starting with --) are
 * concatenated into config.task.
 */
function parseArgs() {
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case "--max-iterations":
        config.maxIterations = parseInt(args[++i], 10);
        break;
      case "--model":
        config.model = args[++i];
        break;
      case "--pause":
        config.pauseBetweenTurns = parseInt(args[++i], 10) * 1000;
        break;
      case "--permission-mode":
        config.permissionMode = args[++i];
        break;
      case "--log":
        config.logFile = args[++i];
        break;
      case "--task-file":
        config.taskFile = args[++i];
        break;
      case "--continue":
        config.continueMode = true;
        break;
      case "--session-id":
        config.sessionId = args[++i];
        break;
      case "--workdir":
        config.workdir = args[++i];
        break;
      case "--verbose":
        config.verbose = true;
        break;
      case "--cost-budget":
        config.costBudget = parseFloat(args[++i]);
        break;
      case "--dashboard-port":
        config.dashboardPort = parseInt(args[++i], 10);
        break;
      case "--no-dashboard":
        config.noDashboard = true;
        break;
      case "--parallel":
        config.parallelTasks.push(args[++i]);
        break;
      case "--max-retries":
        config.maxRetries = parseInt(args[++i], 10);
        break;
      case "--retry-base-delay":
        config.retryBaseDelay = parseInt(args[++i], 10);
        break;
      case "--retry-max-delay":
        config.retryMaxDelay = parseInt(args[++i], 10);
        break;
      case "--report":
        config.reportFile = args[++i];
        break;
      case "--help":
      case "-h":
        console.log(`
Usage: node continuous-claude-stream.mjs [options] "task description"

Options:
  --max-iterations N      Max turns per task (default: 50)
  --model MODEL           Claude model (default: sonnet)
  --pause SECONDS         Pause between turns (default: 2)
  --permission-mode M     Permission mode (default: plan)
  --task-file FILE        Read task from file
  --continue              Resume previous session
  --session-id ID         Specific session ID
  --workdir DIR           Working directory
  --log FILE              Log file (default: claude-stream.log)
  --verbose               Show raw stream events
  --cost-budget USD       Stop if total cost exceeds this (default: 0 = unlimited)
  --dashboard-port PORT   Web dashboard port (default: 3456)
  --no-dashboard          Disable web dashboard
  --parallel "TASK"       Add a parallel task (repeatable)
  --max-retries N         Max retries per failed turn (default: 5)
  --retry-base-delay MS   Base delay for exponential backoff (default: 5000)
  --retry-max-delay MS    Max delay for exponential backoff (default: 160000)
  --report FILE           Summary report path (default: claude-session-report.md)
`);
        process.exit(0);
      default:
        if (!args[i].startsWith("--")) {
          config.task += (config.task ? " " : "") + args[i];
        }
    }
  }

  // Load task from file if specified
  if (config.taskFile) {
    config.task = readFileSync(config.taskFile, "utf-8").trim();
  }

  // Validate: need a task, --continue, or --parallel tasks
  if (!config.task && !config.continueMode && config.parallelTasks.length === 0) {
    console.error("Error: provide a task, use --continue, or use --parallel");
    process.exit(1);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 3: TERMINAL COLORS AND FORMATTING UTILITIES
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * ANSI color code map for terminal output. Each property returns the escape
 * sequence for the named style. Use c.reset to clear all formatting.
 */
const c = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
};

/**
 * Strips ANSI escape codes from a string, producing plain text suitable
 * for log files or JSON serialization.
 *
 * @param {string} str - The string potentially containing ANSI codes
 * @returns {string} The plain text with all escape sequences removed
 */
function stripAnsi(str) {
  return str.replace(/\x1b\[[0-9;]*m/g, "");
}

/**
 * Formats a USD cost for display. Values under $0.01 are shown in cents
 * for readability; larger values show 4 decimal places.
 *
 * @param {number} usd - The cost in US dollars
 * @returns {string} Formatted cost string (e.g. "$0.42c" or "$1.2345")
 */
function formatCost(usd) {
  if (usd < 0.01) return `$${(usd * 100).toFixed(2)}c`;
  return `$${usd.toFixed(4)}`;
}

/**
 * Formats a duration in milliseconds into a human-readable string.
 * Outputs seconds for short durations, minutes:seconds for longer ones,
 * and hours:minutes:seconds for very long runs.
 *
 * @param {number} ms - Duration in milliseconds
 * @returns {string} Formatted duration (e.g. "3.2s", "2m 15s", "1h 5m 30s")
 */
function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}.${Math.floor((ms % 1000) / 100)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainSec = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainSec}s`;
  const hours = Math.floor(minutes / 60);
  const remainMin = minutes % 60;
  return `${hours}h ${remainMin}m ${remainSec}s`;
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 4: LOGGING
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Appends a timestamped message to the log file. If verbose mode is enabled,
 * the message is also printed to stdout in dim text. All messages are
 * stripped of ANSI codes before writing to the log file.
 *
 * @param {string} msg - The message to log
 */
function log(msg) {
  const line = `[${new Date().toISOString()}] ${stripAnsi(msg)}`;
  appendFileSync(config.logFile, line + "\n");
  if (config.verbose) console.log(`${c.dim}${line}${c.reset}`);
}

/**
 * Prints a message to stdout AND appends it (ANSI-stripped) to the log file.
 * This is the primary output function for user-visible progress messages.
 *
 * @param {string} msg - The message to print and log
 */
function print(msg) {
  console.log(msg);
  log(stripAnsi(msg));
}

/**
 * Prints a 60-character horizontal rule as a visual separator in terminal
 * output. Uses dim styling to avoid visual clutter.
 */
function divider() {
  print(`${c.dim}${"─".repeat(60)}${c.reset}`);
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 5: STATE PERSISTENCE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Loads the previously saved runner state from the JSON state file.
 * Returns null if the file doesn't exist or is unparseable, allowing
 * fresh starts to proceed without errors.
 *
 * @returns {Object|null} The parsed state object, or null if unavailable
 */
function loadState() {
  try {
    return JSON.parse(readFileSync(config.stateFile, "utf-8"));
  } catch {
    return null;
  }
}

/**
 * Persists the current runner state to the JSON state file. This is called
 * after every turn, on graceful shutdown, and when the session completes.
 * The state includes enough information to resume the session later via
 * --continue.
 *
 * @param {Object} state - The state object to serialize and save
 */
function saveState(state) {
  writeFileSync(config.stateFile, JSON.stringify(state, null, 2));
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 6: METRICS COLLECTOR
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * MetricsCollector accumulates turn-level and session-level performance data.
 * It tracks timing, token counts, costs, tool usage, and error rates across
 * all tasks (including parallel). The dashboard and report generator both
 * read from this collector.
 *
 * @class
 */
class MetricsCollector {
  constructor() {
    /** @type {Map<string, Object>} Map of taskId -> task metadata */
    this.tasks = new Map();
    /** @type {Array<Object>} Chronological list of all turn records */
    this.turns = [];
    /** @type {number} Session start timestamp */
    this.sessionStartTime = Date.now();
    /** @type {string} Overall session status */
    this.sessionStatus = "STARTING";
    /** @type {number} Count of retried turns */
    this.totalRetries = 0;
  }

  /**
   * Registers a new task in the collector. Call this before the first turn
   * of each task (including parallel tasks).
   *
   * @param {string} taskId   - Unique identifier for the task
   * @param {string} taskName - Human-readable name or description
   * @param {string} taskText - Full task prompt text
   */
  addTask(taskId, taskName, taskText) {
    this.tasks.set(taskId, {
      id: taskId,
      name: taskName,
      task: taskText,
      status: "pending",
      sessionId: null,
      turns: [],
      totalCost: 0,
      totalToolCalls: 0,
      totalTokensIn: 0,
      totalTokensOut: 0,
      startTime: null,
      endTime: null,
      iteration: 0,
      lastEvaluation: null,
    });
  }

  /**
   * Records a completed turn (successful or failed) for a given task.
   * Updates both the task-level aggregates and the global turn list.
   * Broadcasts the update to connected dashboard SSE clients.
   *
   * @param {string} taskId     - The task this turn belongs to
   * @param {Object} turnData   - Turn result data from runClaudeTurn
   * @param {number} turnData.tokensIn    - Input tokens consumed
   * @param {number} turnData.tokensOut   - Output tokens generated
   * @param {number} turnData.costUsd     - Cost in USD for this turn
   * @param {number} turnData.toolCalls   - Number of tool calls made
   * @param {number} turnData.durationMs  - Turn wall-clock duration in ms
   * @param {string} turnData.status      - "success", "error", or "retrying"
   * @param {string} [turnData.error]     - Error message if status is "error"
   * @param {number} [turnData.retryCount]- Number of retry attempts
   * @param {string} [turnData.textPreview]- First ~200 chars of response
   */
  recordTurn(taskId, turnData) {
    const task = this.tasks.get(taskId);
    if (!task) return;

    const turn = {
      id: randomUUID(),
      taskId,
      turnNumber: task.turns.length + 1,
      timestamp: new Date().toISOString(),
      tokensIn: turnData.tokensIn || 0,
      tokensOut: turnData.tokensOut || 0,
      costUsd: turnData.costUsd || 0,
      toolCalls: turnData.toolCalls || 0,
      durationMs: turnData.durationMs || 0,
      status: turnData.status || "success",
      error: turnData.error || null,
      retryCount: turnData.retryCount || 0,
      textPreview: turnData.textPreview || "",
    };

    task.turns.push(turn);
    task.totalCost += turn.costUsd;
    task.totalToolCalls += turn.toolCalls;
    task.totalTokensIn += turn.tokensIn;
    task.totalTokensOut += turn.tokensOut;
    task.iteration = task.turns.length;

    this.turns.push(turn);

    if (turn.retryCount > 0) this.totalRetries++;

    // Broadcast to dashboard
    broadcastSSE("turn", turn);
    broadcastSSE("status", this.getSnapshot());
  }

  /**
   * Updates the status of a specific task (e.g. "running", "complete", "error").
   * Broadcasts the change to dashboard clients.
   *
   * @param {string} taskId - The task to update
   * @param {string} status - New status string
   * @param {Object} [evaluation] - Optional evaluation result from progress check
   */
  updateTaskStatus(taskId, status, evaluation) {
    const task = this.tasks.get(taskId);
    if (!task) return;
    task.status = status;
    if (evaluation) task.lastEvaluation = evaluation;
    if (status === "running" && !task.startTime) task.startTime = Date.now();
    if (["complete", "error", "blocked"].includes(status)) task.endTime = Date.now();
    broadcastSSE("taskStatus", { taskId, status, evaluation });
    broadcastSSE("status", this.getSnapshot());
  }

  /**
   * Computes aggregate statistics across all tasks. Used by the dashboard
   * and report generator for top-level summaries.
   *
   * @returns {Object} Aggregate stats including totals and per-task breakdowns
   */
  getAggregates() {
    let totalCost = 0;
    let totalTokensIn = 0;
    let totalTokensOut = 0;
    let totalToolCalls = 0;
    let totalTurns = 0;

    for (const task of this.tasks.values()) {
      totalCost += task.totalCost;
      totalTokensIn += task.totalTokensIn;
      totalTokensOut += task.totalTokensOut;
      totalToolCalls += task.totalToolCalls;
      totalTurns += task.turns.length;
    }

    return {
      totalCost,
      totalTokensIn,
      totalTokensOut,
      totalToolCalls,
      totalTurns,
      totalRetries: this.totalRetries,
      elapsedMs: Date.now() - this.sessionStartTime,
      taskCount: this.tasks.size,
    };
  }

  /**
   * Returns a full snapshot of the current state, suitable for JSON
   * serialization and dashboard rendering. Includes config, all tasks,
   * all turns, and aggregate stats.
   *
   * @returns {Object} Complete state snapshot
   */
  getSnapshot() {
    const agg = this.getAggregates();
    return {
      sessionStatus: this.sessionStatus,
      config: {
        model: config.model,
        maxIterations: config.maxIterations,
        costBudget: config.costBudget,
        permissionMode: config.permissionMode,
      },
      aggregates: agg,
      tasks: Array.from(this.tasks.values()).map((t) => ({
        id: t.id,
        name: t.name,
        status: t.status,
        iteration: t.iteration,
        totalCost: t.totalCost,
        totalToolCalls: t.totalToolCalls,
        totalTokensIn: t.totalTokensIn,
        totalTokensOut: t.totalTokensOut,
        startTime: t.startTime,
        endTime: t.endTime,
        lastEvaluation: t.lastEvaluation,
      })),
      recentTurns: this.turns.slice(-50),
    };
  }
}

/** Global metrics collector instance, shared across all tasks and the dashboard */
const metrics = new MetricsCollector();

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 7: RETRY QUEUE WITH EXPONENTIAL BACKOFF
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * RetryQueue manages failed turn retries with exponential backoff. When a
 * turn fails with a retryable error (rate limit, overload, network), it is
 * enqueued here. The queue calculates the next retry time using the formula:
 *
 *   delay = min(baseDelay * 2^retryCount, maxDelay) + random jitter
 *
 * Jitter prevents thundering-herd problems when multiple parallel tasks
 * hit rate limits simultaneously.
 *
 * @class
 */
class RetryQueue {
  /**
   * @param {number} maxRetries  - Maximum number of retry attempts per item
   * @param {number} baseDelayMs - Initial delay before first retry (ms)
   * @param {number} maxDelayMs  - Maximum delay cap for any retry (ms)
   */
  constructor(maxRetries = 5, baseDelayMs = 5000, maxDelayMs = 160000) {
    /** @type {Array<Object>} Pending retry items sorted by nextRetryAt */
    this.queue = [];
    this.maxRetries = maxRetries;
    this.baseDelayMs = baseDelayMs;
    this.maxDelayMs = maxDelayMs;
  }

  /**
   * Determines whether a given error message represents a transient failure
   * that is safe to retry. Network errors, rate limits, and overload responses
   * are retryable; permission errors, auth failures, and logic errors are not.
   *
   * @param {string} errorMessage - The error message to classify
   * @returns {boolean} True if the error is retryable
   */
  isRetryable(errorMessage) {
    const retryablePatterns = [
      "overloaded",
      "rate",
      "timeout",
      "ECONNRESET",
      "ECONNREFUSED",
      "ETIMEDOUT",
      "socket hang up",
      "503",
      "529",
      "too many requests",
    ];
    const lower = errorMessage.toLowerCase();
    return retryablePatterns.some((p) => lower.includes(p.toLowerCase()));
  }

  /**
   * Checks whether the item has remaining retry attempts.
   *
   * @param {Object} item - A retry queue item with a retryCount field
   * @returns {boolean} True if retryCount < maxRetries
   */
  canRetry(item) {
    return item.retryCount < this.maxRetries;
  }

  /**
   * Enqueues a failed turn for retry. Increments the retry counter, computes
   * the next retry time with exponential backoff and jitter, and inserts the
   * item into the queue sorted by nextRetryAt.
   *
   * @param {Object} item - The retry item containing taskId, prompt, options, etc.
   * @returns {number} The delay in ms until the next retry attempt
   */
  enqueue(item) {
    item.retryCount = (item.retryCount || 0) + 1;
    // Exponential backoff: baseDelay * 2^(retryCount-1), capped at maxDelay
    const exponentialDelay = Math.min(
      this.baseDelayMs * Math.pow(2, item.retryCount - 1),
      this.maxDelayMs
    );
    // Add random jitter of up to 20% to prevent thundering herd
    const jitter = Math.random() * exponentialDelay * 0.2;
    const delay = Math.floor(exponentialDelay + jitter);
    item.nextRetryAt = Date.now() + delay;
    this.queue.push(item);
    this.queue.sort((a, b) => a.nextRetryAt - b.nextRetryAt);
    log(`Retry enqueued: attempt ${item.retryCount}/${this.maxRetries}, delay ${formatDuration(delay)}`);
    return delay;
  }

  /**
   * Retrieves all items whose nextRetryAt time has passed, removing them
   * from the queue. Call this in the turn loop to check for ready retries.
   *
   * @returns {Array<Object>} Items ready to be retried now
   */
  getReady() {
    const now = Date.now();
    const ready = this.queue.filter((i) => i.nextRetryAt <= now);
    this.queue = this.queue.filter((i) => i.nextRetryAt > now);
    return ready;
  }

  /**
   * Returns the number of items currently waiting in the retry queue.
   *
   * @returns {number} Queue depth
   */
  get size() {
    return this.queue.length;
  }
}

/** Global retry queue instance */
const retryQueue = new RetryQueue(
  config.maxRetries,
  config.retryBaseDelay,
  config.retryMaxDelay
);

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 8: COST BUDGET ENFORCEMENT
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Checks whether the total accumulated cost across all tasks exceeds the
 * configured budget. If costBudget is 0 (the default), no limit is enforced.
 * When the budget is exceeded, this function logs a warning and returns true,
 * signaling the caller to stop execution.
 *
 * @returns {boolean} True if the budget has been exceeded and execution should stop
 */
function isBudgetExceeded() {
  if (config.costBudget <= 0) return false;
  const agg = metrics.getAggregates();
  if (agg.totalCost >= config.costBudget) {
    print(
      `${c.red}${c.bold}BUDGET EXCEEDED: ${formatCost(agg.totalCost)} >= ${formatCost(config.costBudget)}${c.reset}`
    );
    log(`Budget exceeded: ${agg.totalCost} >= ${config.costBudget}`);
    return true;
  }
  // Warn at 80% threshold
  if (agg.totalCost >= config.costBudget * 0.8) {
    print(
      `${c.yellow}Budget warning: ${formatCost(agg.totalCost)} / ${formatCost(config.costBudget)} (${Math.round((agg.totalCost / config.costBudget) * 100)}%)${c.reset}`
    );
  }
  return false;
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 9: CLAUDE TURN RUNNER
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Executes a single Claude Code turn by spawning the `claude` CLI process
 * with stream-json output format. Parses the NDJSON stream in real time,
 * extracting text, tool calls, token usage, cost, and session ID.
 *
 * This function handles:
 *   - Building the correct CLI argument list based on options
 *   - Parsing each NDJSON event type (system, assistant, content_block_delta,
 *     result, tool_use)
 *   - Accumulating text output from content blocks
 *   - Tracking tool call count for metrics
 *   - Capturing stderr for error diagnostics
 *   - Resolving with a structured result object on success
 *   - Rejecting with a descriptive Error on failure
 *
 * @param {string} prompt          - The prompt to send to Claude
 * @param {Object} [options={}]    - Execution options
 * @param {boolean} [options.continueSession] - Use -c flag to continue session
 * @param {string}  [options.sessionId]       - Override session ID
 * @returns {Promise<Object>} Result with text, events, toolCalls, tokens, cost, sessionId
 */
function runClaudeTurn(prompt, options = {}) {
  return new Promise((resolve, reject) => {
    const cmdArgs = [
      "-p",
      "--model",
      config.model,
      "--output-format",
      "stream-json",
      "--verbose",
      "--permission-mode",
      config.permissionMode,
    ];

    if (options.continueSession) {
      cmdArgs.push("-c");
      // Don't pass --session-id with -c — Claude Code rejects the combo
      // unless --fork-session is also set. -c auto-picks the latest session.
    } else {
      const sid = options.sessionId || config.sessionId;
      if (sid) {
        cmdArgs.push("--session-id", sid);
      }
    }

    cmdArgs.push(prompt);

    log(`Spawning: claude ${cmdArgs.join(" ").slice(0, 200)}...`);

    const child = spawn("claude", cmdArgs, {
      cwd: config.workdir,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env },
    });

    let fullOutput = "";
    const events = [];
    let toolCalls = 0;
    let tokensIn = 0;
    let tokensOut = 0;
    let costUsd = 0;
    let sessionId = null;
    let assistantText = "";

    // Parse the NDJSON (newline-delimited JSON) stream from stdout.
    // Each line is a self-contained JSON event from Claude's stream-json format.
    const rl = createInterface({ input: child.stdout });
    rl.on("line", (line) => {
      try {
        const event = JSON.parse(line);
        events.push(event);
        log(`Event: ${event.type}`);

        switch (event.type) {
          case "system":
            // System event carries the session ID for continuation
            sessionId = event.session_id;
            log(`Session: ${sessionId}`);
            break;

          case "assistant":
            // Full assistant message with content blocks. Extract all text blocks.
            if (event.message?.content) {
              for (const block of event.message.content) {
                if (block.type === "text") {
                  assistantText += block.text;
                }
              }
            }
            break;

          case "content_block_delta":
            // Streaming text delta — print immediately for real-time output
            if (event.delta?.type === "text_delta") {
              process.stdout.write(`${c.dim}${event.delta.text}${c.reset}`);
            }
            break;

          case "result":
            // Final result event with complete text, usage, and cost data
            if (event.result) {
              assistantText = "";
              for (const block of event.result) {
                if (block.type === "text") {
                  assistantText += block.text;
                }
              }
            }
            if (event.usage) {
              tokensIn = event.usage.input_tokens || 0;
              tokensOut = event.usage.output_tokens || 0;
            }
            if (event.cost_usd) {
              costUsd = event.cost_usd;
            }
            if (event.session_id) {
              sessionId = event.session_id;
            }
            break;

          case "tool_use":
            // A tool was invoked during this turn
            toolCalls++;
            const toolName = event.name || event.tool_name || "unknown";
            print(`  ${c.cyan}tool:${c.reset} ${toolName}`);
            break;
        }
      } catch {
        // Line wasn't valid JSON — accumulate as raw output
        fullOutput += line + "\n";
      }
    });

    // Capture stderr for error diagnostics
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
      log(`stderr: ${chunk.toString().trim()}`);
    });

    child.on("close", (code) => {
      process.stdout.write("\n");

      if (code !== 0 && !assistantText) {
        log(`Exit code ${code}: ${stderr}`);
        reject(new Error(`Claude exited with code ${code}: ${stderr}`));
        return;
      }

      resolve({
        text: assistantText || fullOutput.trim(),
        events,
        toolCalls,
        tokensIn,
        tokensOut,
        costUsd,
        sessionId,
        exitCode: code,
      });
    });

    child.on("error", reject);
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 10: PROGRESS EVALUATOR
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Evaluates whether a task is complete by asking Claude to review what has
 * been accomplished in the current session. Sends a structured prompt
 * requesting a JSON response with status, summary, remaining work, and
 * next step.
 *
 * The evaluation runs as a continuation of the existing session, so Claude
 * has full context of prior turns. If the JSON response can't be parsed,
 * it falls back to assuming IN_PROGRESS to avoid premature termination.
 *
 * @param {string} task             - The original task description
 * @param {Object} [options={}]     - Options passed through to runClaudeTurn
 * @param {string} [options.sessionId] - Session ID to continue
 * @returns {Promise<Object>} Evaluation result with status, summary, remaining, nextStep
 */
async function evaluateProgress(task, options = {}) {
  const evalPrompt = `You are reviewing progress on this task:

"${task}"

Look at what has been done in this session. Respond with a JSON object (no markdown fences):
{"status": "COMPLETE" | "IN_PROGRESS" | "BLOCKED" | "ERROR", "summary": "what was done", "remaining": "what is left", "next_step": "what to do next"}`;

  try {
    const result = await runClaudeTurn(evalPrompt, {
      continueSession: true,
      ...options,
    });
    const text = result.text.trim();

    // Attempt to extract JSON from the response (may be wrapped in prose)
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      const parsed = JSON.parse(jsonMatch[0]);
      return {
        status: parsed.status || "IN_PROGRESS",
        summary: parsed.summary || "",
        remaining: parsed.remaining || "",
        nextStep: parsed.next_step || "Continue working.",
      };
    }
  } catch (e) {
    log(`Eval error: ${e.message}`);
  }

  // Fallback: avoid premature termination by assuming work continues
  return {
    status: "IN_PROGRESS",
    summary: "Could not parse evaluation",
    remaining: "Unknown",
    nextStep: "Continue working on the task.",
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 11: SLEEP HELPER
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Returns a promise that resolves after the specified number of milliseconds.
 * Used for pauses between turns and retry backoff delays.
 *
 * @param {number} ms - Duration to sleep in milliseconds
 * @returns {Promise<void>} Resolves after the delay
 */
function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 12: WEB DASHBOARD
// ═══════════════════════════════════════════════════════════════════════════════

/** @type {Set<import('http').ServerResponse>} Active SSE client connections */
const sseClients = new Set();

/** @type {import('http').Server|null} The dashboard HTTP server instance */
let dashboardServer = null;

/**
 * Broadcasts a Server-Sent Event to all connected dashboard clients.
 * Events are formatted per the SSE specification: each message has an
 * event type and JSON-encoded data payload. Disconnected clients are
 * silently removed from the set.
 *
 * @param {string} event - The SSE event name (e.g. "turn", "status", "log")
 * @param {Object} data  - The data payload to JSON-serialize
 */
function broadcastSSE(event, data) {
  const msg = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const client of sseClients) {
    try {
      client.write(msg);
    } catch {
      sseClients.delete(client);
    }
  }
}

/**
 * Generates the complete HTML for the web dashboard as a single inline string.
 * Includes all CSS and JavaScript — no external dependencies. The dashboard
 * features:
 *
 *   - Real-time status badge with color coding
 *   - Summary stat cards (turns, cost, tokens in/out, tool calls, duration)
 *   - Token usage bar chart rendered on a <canvas> element
 *   - Turn history table with per-turn metrics
 *   - Parallel task cards (shown only when multiple tasks exist)
 *   - Budget progress bar (shown only when a budget is configured)
 *   - Auto-updating via SSE (Server-Sent Events) connection
 *   - Auto-reconnect if the SSE connection drops
 *
 * @returns {string} Complete HTML document string
 */
function getDashboardHTML() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Runner Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0f1117; color: #c9d1d9;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
    line-height: 1.5;
  }
  .header {
    background: #161b22; border-bottom: 1px solid #30363d;
    padding: 16px 24px; display: flex; justify-content: space-between; align-items: center;
  }
  .header h1 { font-size: 1.25rem; color: #f0f6fc; }
  .header h1 span { color: #7c6bf7; }
  .badge {
    padding: 4px 12px; border-radius: 12px; font-size: 0.8rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  .badge-running { background: #1f3a2e; color: #3fb950; }
  .badge-complete { background: #1a3a5c; color: #58a6ff; }
  .badge-error { background: #3d1a1a; color: #f85149; }
  .badge-starting { background: #2a2318; color: #d29922; }
  .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
  .stats {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px; margin-bottom: 20px;
  }
  .stat-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px;
  }
  .stat-label { font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; }
  .stat-value { font-size: 1.75rem; font-weight: 700; color: #f0f6fc; margin-top: 4px; }
  .stat-value.cost { color: #3fb950; }
  .stat-value.tokens { color: #58a6ff; }
  .stat-value.tools { color: #d29922; }
  .budget-bar {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 12px 16px; margin-bottom: 20px;
  }
  .budget-track {
    background: #21262d; border-radius: 4px; height: 8px; margin-top: 8px; overflow: hidden;
  }
  .budget-fill {
    height: 100%; border-radius: 4px; transition: width 0.5s ease;
  }
  .budget-ok { background: #3fb950; }
  .budget-warn { background: #d29922; }
  .budget-danger { background: #f85149; }
  .section-title {
    font-size: 1rem; font-weight: 600; color: #f0f6fc; margin-bottom: 12px;
    padding-bottom: 8px; border-bottom: 1px solid #21262d;
  }
  .chart-container {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 16px; margin-bottom: 20px;
  }
  canvas { width: 100%; height: 200px; }
  .tasks-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 12px; margin-bottom: 20px;
  }
  .task-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px;
  }
  .task-card .task-name { font-weight: 600; color: #f0f6fc; margin-bottom: 8px; }
  .task-card .task-meta { font-size: 0.8rem; color: #8b949e; }
  .task-status {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
  }
  .task-status-running { background: #1f3a2e; color: #3fb950; }
  .task-status-complete { background: #1a3a5c; color: #58a6ff; }
  .task-status-error { background: #3d1a1a; color: #f85149; }
  .task-status-pending { background: #21262d; color: #8b949e; }
  .task-status-blocked { background: #2a2318; color: #d29922; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; color: #8b949e; font-size: 0.75rem;
       text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #30363d; }
  td { padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 0.85rem; }
  tr:hover { background: #161b22; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .status-dot-success { background: #3fb950; }
  .status-dot-error { background: #f85149; }
  .status-dot-retrying { background: #d29922; }
  .text-preview { color: #8b949e; max-width: 300px; overflow: hidden;
                   text-overflow: ellipsis; white-space: nowrap; }
  .footer { text-align: center; padding: 20px; color: #484f58; font-size: 0.75rem; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
  .live-dot { width: 8px; height: 8px; border-radius: 50%; background: #3fb950;
              display: inline-block; margin-right: 8px; animation: pulse 2s infinite; }
</style>
</head>
<body>
<div class="header">
  <h1><span>&#9670;</span> Claude Runner Dashboard</h1>
  <div>
    <span class="live-dot" id="live-dot"></span>
    <span class="badge badge-starting" id="status-badge">Starting</span>
  </div>
</div>
<div class="container">
  <div class="stats">
    <div class="stat-card"><div class="stat-label">Turns</div><div class="stat-value" id="s-turns">0</div></div>
    <div class="stat-card"><div class="stat-label">Total Cost</div><div class="stat-value cost" id="s-cost">$0.00</div></div>
    <div class="stat-card"><div class="stat-label">Tokens In</div><div class="stat-value tokens" id="s-tin">0</div></div>
    <div class="stat-card"><div class="stat-label">Tokens Out</div><div class="stat-value tokens" id="s-tout">0</div></div>
    <div class="stat-card"><div class="stat-label">Tool Calls</div><div class="stat-value tools" id="s-tools">0</div></div>
    <div class="stat-card"><div class="stat-label">Duration</div><div class="stat-value" id="s-dur">0s</div></div>
    <div class="stat-card"><div class="stat-label">Retries</div><div class="stat-value" id="s-retries">0</div></div>
    <div class="stat-card"><div class="stat-label">Tasks</div><div class="stat-value" id="s-tasks">0</div></div>
  </div>

  <div class="budget-bar" id="budget-section" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <span class="stat-label">Budget</span>
      <span id="budget-text" style="font-size:0.85rem">$0.00 / $0.00</span>
    </div>
    <div class="budget-track"><div class="budget-fill budget-ok" id="budget-fill" style="width:0%"></div></div>
  </div>

  <div id="tasks-section" style="display:none">
    <div class="section-title">Parallel Tasks</div>
    <div class="tasks-grid" id="tasks-grid"></div>
  </div>

  <div class="chart-container">
    <div class="section-title">Token Usage Per Turn</div>
    <canvas id="token-chart" height="200"></canvas>
  </div>

  <div>
    <div class="section-title">Turn History</div>
    <table>
      <thead><tr>
        <th>#</th><th>Task</th><th>Duration</th><th>Tokens In</th><th>Tokens Out</th>
        <th>Cost</th><th>Tools</th><th>Status</th><th>Preview</th>
      </tr></thead>
      <tbody id="turns-body"></tbody>
    </table>
  </div>
</div>
<div class="footer">Claude Runner Dashboard &mdash; Auto-refreshing via SSE</div>

<script>
const $ = (id) => document.getElementById(id);
const turns = [];
const taskMap = {};
let statusData = null;

function fmtCost(usd) {
  if (!usd) return '$0.00';
  return usd < 0.01 ? '$' + (usd*100).toFixed(2) + 'c' : '$' + usd.toFixed(4);
}
function fmtDur(ms) {
  if (!ms) return '0s';
  const s = Math.floor(ms/1000);
  if (s < 60) return s + '.' + Math.floor((ms%1000)/100) + 's';
  const m = Math.floor(s/60);
  if (m < 60) return m + 'm ' + (s%60) + 's';
  return Math.floor(m/60) + 'h ' + (m%60) + 'm';
}
function fmtNum(n) { return n ? n.toLocaleString() : '0'; }

function updateStats() {
  if (!statusData) return;
  const a = statusData.aggregates;
  $('s-turns').textContent = a.totalTurns;
  $('s-cost').textContent = fmtCost(a.totalCost);
  $('s-tin').textContent = fmtNum(a.totalTokensIn);
  $('s-tout').textContent = fmtNum(a.totalTokensOut);
  $('s-tools').textContent = a.totalToolCalls;
  $('s-dur').textContent = fmtDur(a.elapsedMs);
  $('s-retries').textContent = a.totalRetries;
  $('s-tasks').textContent = a.taskCount;

  // Status badge
  const badge = $('status-badge');
  const st = statusData.sessionStatus.toLowerCase();
  badge.textContent = statusData.sessionStatus;
  badge.className = 'badge badge-' + (st.includes('run') || st.includes('progress') ? 'running' :
    st.includes('complete') ? 'complete' : st.includes('error') ? 'error' : 'starting');

  // Budget
  const cfg = statusData.config;
  if (cfg.costBudget > 0) {
    $('budget-section').style.display = '';
    const pct = Math.min((a.totalCost / cfg.costBudget) * 100, 100);
    $('budget-text').textContent = fmtCost(a.totalCost) + ' / ' + fmtCost(cfg.costBudget);
    const fill = $('budget-fill');
    fill.style.width = pct + '%';
    fill.className = 'budget-fill ' + (pct < 60 ? 'budget-ok' : pct < 85 ? 'budget-warn' : 'budget-danger');
  }

  // Tasks
  if (statusData.tasks && statusData.tasks.length > 1) {
    $('tasks-section').style.display = '';
    $('tasks-grid').innerHTML = statusData.tasks.map(t => {
      const stCls = 'task-status-' + (t.status || 'pending');
      return '<div class="task-card">' +
        '<div class="task-name">' + esc(t.name) + '</div>' +
        '<span class="task-status ' + stCls + '">' + (t.status||'pending') + '</span>' +
        '<div class="task-meta" style="margin-top:8px">' +
          'Turn ' + t.iteration + ' &bull; ' + fmtCost(t.totalCost) +
          ' &bull; ' + fmtNum(t.totalTokensIn) + '/' + fmtNum(t.totalTokensOut) + ' tokens' +
          ' &bull; ' + t.totalToolCalls + ' tools' +
        '</div>' +
        (t.lastEvaluation ? '<div class="task-meta" style="margin-top:4px">' + esc(t.lastEvaluation.summary||'') + '</div>' : '') +
      '</div>';
    }).join('');
  }
}

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function addTurnRow(t) {
  turns.push(t);
  const tb = $('turns-body');
  const dotCls = 'status-dot-' + (t.status||'success');
  const row = document.createElement('tr');
  row.innerHTML =
    '<td>' + t.turnNumber + '</td>' +
    '<td>' + esc((t.taskId||'').slice(0,8)) + '</td>' +
    '<td>' + fmtDur(t.durationMs) + '</td>' +
    '<td>' + fmtNum(t.tokensIn) + '</td>' +
    '<td>' + fmtNum(t.tokensOut) + '</td>' +
    '<td>' + fmtCost(t.costUsd) + '</td>' +
    '<td>' + (t.toolCalls||0) + '</td>' +
    '<td><span class="status-dot ' + dotCls + '"></span>' + (t.status||'success') + '</td>' +
    '<td class="text-preview" title="' + esc(t.textPreview||'') + '">' + esc((t.textPreview||'').slice(0,80)) + '</td>';
  tb.insertBefore(row, tb.firstChild);
  drawChart();
}

function drawChart() {
  const canvas = $('token-chart');
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = 200 * dpr;
  ctx.scale(dpr, dpr);
  const W = rect.width, H = 200;
  ctx.clearRect(0, 0, W, H);

  if (turns.length === 0) return;

  const maxTokens = Math.max(...turns.map(t => Math.max(t.tokensIn||0, t.tokensOut||0)), 1);
  const barW = Math.max(4, Math.min(40, (W - 60) / turns.length - 2));
  const startX = 50;

  // Y-axis labels
  ctx.fillStyle = '#8b949e'; ctx.font = '10px monospace'; ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const y = H - 20 - (i/4) * (H - 40);
    const val = Math.round(maxTokens * i / 4);
    ctx.fillText(val >= 1000 ? (val/1000).toFixed(0)+'k' : val, 45, y + 3);
    ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(50, y); ctx.lineTo(W, y); ctx.stroke();
  }

  // Bars
  const shown = turns.slice(-Math.floor((W - 60) / (barW + 2)));
  shown.forEach((t, i) => {
    const x = startX + i * (barW + 2);
    const hIn = ((t.tokensIn||0) / maxTokens) * (H - 40);
    const hOut = ((t.tokensOut||0) / maxTokens) * (H - 40);
    ctx.fillStyle = 'rgba(88,166,255,0.6)';
    ctx.fillRect(x, H - 20 - hIn, barW/2, hIn);
    ctx.fillStyle = 'rgba(124,107,247,0.6)';
    ctx.fillRect(x + barW/2, H - 20 - hOut, barW/2, hOut);
  });

  // Legend
  ctx.fillStyle = 'rgba(88,166,255,0.8)'; ctx.fillRect(W-120, 8, 10, 10);
  ctx.fillStyle = '#8b949e'; ctx.textAlign = 'left'; ctx.fillText('In', W-106, 17);
  ctx.fillStyle = 'rgba(124,107,247,0.8)'; ctx.fillRect(W-70, 8, 10, 10);
  ctx.fillStyle = '#8b949e'; ctx.fillText('Out', W-56, 17);
}

// SSE connection with auto-reconnect
function connectSSE() {
  const es = new EventSource('/api/events');
  $('live-dot').style.background = '#3fb950';

  es.addEventListener('turn', (e) => { addTurnRow(JSON.parse(e.data)); });
  es.addEventListener('status', (e) => { statusData = JSON.parse(e.data); updateStats(); });
  es.addEventListener('taskStatus', (e) => { if (statusData) updateStats(); });
  es.addEventListener('log', (e) => {});

  es.onerror = () => {
    $('live-dot').style.background = '#f85149';
    es.close();
    setTimeout(connectSSE, 3000);
  };
}

// Initial data fetch
fetch('/api/status').then(r=>r.json()).then(d => {
  statusData = d;
  updateStats();
  if (d.recentTurns) d.recentTurns.forEach(addTurnRow);
});

connectSSE();
window.addEventListener('resize', drawChart);
// Update duration every second
setInterval(() => {
  if (statusData && statusData.aggregates) {
    statusData.aggregates.elapsedMs += 1000;
    $('s-dur').textContent = fmtDur(statusData.aggregates.elapsedMs);
  }
}, 1000);
</script>
</body>
</html>`;
}

/**
 * Starts the web dashboard HTTP server. Sets up three routes:
 *
 *   GET /           → Serves the inline HTML dashboard
 *   GET /api/status → Returns the current metrics snapshot as JSON
 *   GET /api/events → Opens an SSE stream for real-time updates
 *
 * The server binds to the configured dashboardPort (default 3456). If the
 * port is in use, it logs a warning and continues without the dashboard.
 * The server is stored in the dashboardServer variable for graceful shutdown.
 */
function startDashboard() {
  if (config.noDashboard) return;

  const server = createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (url.pathname === "/api/events") {
      // SSE endpoint — keep connection open and push events
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
        "Access-Control-Allow-Origin": "*",
      });
      res.write(`event: status\ndata: ${JSON.stringify(metrics.getSnapshot())}\n\n`);
      sseClients.add(res);
      req.on("close", () => sseClients.delete(res));
      return;
    }

    if (url.pathname === "/api/status") {
      // JSON snapshot endpoint for polling-based clients
      res.writeHead(200, {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
      });
      res.end(JSON.stringify(metrics.getSnapshot()));
      return;
    }

    // Serve the inline HTML dashboard for all other paths
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(getDashboardHTML());
  });

  server.on("error", (err) => {
    if (err.code === "EADDRINUSE") {
      print(`${c.yellow}Dashboard port ${config.dashboardPort} in use, trying ${config.dashboardPort + 1}...${c.reset}`);
      config.dashboardPort++;
      server.listen(config.dashboardPort);
    } else {
      log(`Dashboard error: ${err.message}`);
    }
  });

  server.listen(config.dashboardPort, () => {
    print(`${c.cyan}Dashboard: http://localhost:${config.dashboardPort}${c.reset}`);
  });

  dashboardServer = server;
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 13: INTERACTIVE MODE (STDIN PROMPT INJECTION)
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Queue for user-injected prompts. When a user types a prompt in the terminal
 * during execution, it's pushed here and consumed by the next turn iteration
 * instead of the auto-generated continuation prompt.
 *
 * @type {string[]}
 */
const injectedPrompts = [];

/**
 * Flag indicating whether the runner should pause after the current turn.
 * Set by the "pause" interactive command, cleared by "resume".
 *
 * @type {boolean}
 */
let isPaused = false;

/**
 * Sets up an interactive readline interface on stdin. Allows the user to
 * type commands while the runner is executing turns. Supported commands:
 *
 *   inject <text>  — Queue a prompt to be used in the next turn instead
 *                    of the auto-generated continuation prompt
 *   status         — Print current session status to terminal
 *   pause          — Pause execution after the current turn completes
 *   resume         — Resume execution after a pause
 *   budget <USD>   — Update the cost budget mid-run
 *   abort          — Initiate graceful shutdown immediately
 *   help           — Show available interactive commands
 *
 * The readline interface uses a minimal "▸ " prompt to avoid cluttering
 * the streaming output. It's configured not to interfere with the
 * claude subprocess's stdout rendering.
 */
function setupInteractiveMode() {
  // Only set up if stdin is a TTY (not piped)
  if (!process.stdin.isTTY) return;

  const rl = createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: `${c.dim}▸ ${c.reset}`,
    terminal: false,
  });

  rl.on("line", (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    const [cmd, ...rest] = trimmed.split(/\s+/);
    const arg = rest.join(" ");

    switch (cmd.toLowerCase()) {
      case "inject":
        if (!arg) {
          print(`${c.yellow}Usage: inject <prompt text>${c.reset}`);
        } else {
          injectedPrompts.push(arg);
          print(`${c.green}Prompt queued (${injectedPrompts.length} pending)${c.reset}`);
          broadcastSSE("log", { message: `Prompt injected: ${arg.slice(0, 80)}...` });
        }
        break;

      case "status": {
        const agg = metrics.getAggregates();
        print(`\n${c.bold}Current Status${c.reset}`);
        print(`  Session: ${metrics.sessionStatus}`);
        print(`  Turns: ${agg.totalTurns} | Cost: ${formatCost(agg.totalCost)} | Tools: ${agg.totalToolCalls}`);
        print(`  Tokens: ${agg.totalTokensIn} in / ${agg.totalTokensOut} out`);
        print(`  Duration: ${formatDuration(agg.elapsedMs)}`);
        print(`  Retries: ${agg.totalRetries} | Retry queue: ${retryQueue.size}`);
        if (config.costBudget > 0) {
          print(`  Budget: ${formatCost(agg.totalCost)} / ${formatCost(config.costBudget)}`);
        }
        print(`  Paused: ${isPaused}`);
        break;
      }

      case "pause":
        isPaused = true;
        print(`${c.yellow}Pausing after current turn...${c.reset}`);
        broadcastSSE("log", { message: "Pause requested" });
        break;

      case "resume":
        isPaused = false;
        print(`${c.green}Resuming execution${c.reset}`);
        broadcastSSE("log", { message: "Resumed" });
        break;

      case "budget":
        if (arg) {
          config.costBudget = parseFloat(arg);
          print(`${c.green}Budget updated to ${formatCost(config.costBudget)}${c.reset}`);
        } else {
          print(`${c.yellow}Usage: budget <USD amount>${c.reset}`);
        }
        break;

      case "abort":
        print(`${c.red}Aborting...${c.reset}`);
        gracefulShutdown("user-abort");
        break;

      case "help":
        print(`\n${c.bold}Interactive Commands${c.reset}`);
        print(`  inject <text>  — Queue a prompt for the next turn`);
        print(`  status         — Show current session status`);
        print(`  pause          — Pause after current turn`);
        print(`  resume         — Resume execution`);
        print(`  budget <USD>   — Update cost budget`);
        print(`  abort          — Graceful shutdown`);
        print(`  help           — Show this help\n`);
        break;

      default:
        // Treat bare text as an inject command for convenience
        injectedPrompts.push(trimmed);
        print(`${c.green}Prompt queued: "${trimmed.slice(0, 60)}..."${c.reset}`);
    }
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 14: SUMMARY REPORT GENERATOR
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Generates a comprehensive Markdown report summarizing the entire session.
 * The report includes:
 *
 *   - Session metadata (date, model, duration, config)
 *   - Aggregate statistics (turns, cost, tokens, tools, retries)
 *   - Budget utilization (if a budget was set)
 *   - Per-task breakdown with individual stats and final evaluation
 *   - Turn-by-turn history table with timing, tokens, cost, and status
 *   - Performance analysis (average turn duration, throughput, cost efficiency)
 *
 * The report is written to config.reportFile (default: claude-session-report.md).
 */
function generateReport() {
  const agg = metrics.getAggregates();
  const tasks = Array.from(metrics.tasks.values());
  const allTurns = metrics.turns;

  // Compute performance stats
  const turnDurations = allTurns.filter((t) => t.durationMs > 0).map((t) => t.durationMs);
  const avgDuration = turnDurations.length > 0
    ? turnDurations.reduce((a, b) => a + b, 0) / turnDurations.length
    : 0;
  const minDuration = turnDurations.length > 0 ? Math.min(...turnDurations) : 0;
  const maxDuration = turnDurations.length > 0 ? Math.max(...turnDurations) : 0;
  const tokensPerSecond = agg.elapsedMs > 0
    ? ((agg.totalTokensIn + agg.totalTokensOut) / (agg.elapsedMs / 1000)).toFixed(1)
    : 0;
  const costPerTurn = agg.totalTurns > 0 ? agg.totalCost / agg.totalTurns : 0;

  let md = `# Claude Session Report

**Generated:** ${new Date().toISOString()}
**Model:** ${config.model}
**Duration:** ${formatDuration(agg.elapsedMs)}
**Status:** ${metrics.sessionStatus}

---

## Summary

| Metric | Value |
|--------|-------|
| Total Turns | ${agg.totalTurns} |
| Total Cost | ${formatCost(agg.totalCost)} |
| Tokens In | ${agg.totalTokensIn.toLocaleString()} |
| Tokens Out | ${agg.totalTokensOut.toLocaleString()} |
| Total Tokens | ${(agg.totalTokensIn + agg.totalTokensOut).toLocaleString()} |
| Tool Calls | ${agg.totalToolCalls} |
| Retries | ${agg.totalRetries} |
| Tasks | ${agg.taskCount} |
`;

  // Budget section
  if (config.costBudget > 0) {
    const pct = ((agg.totalCost / config.costBudget) * 100).toFixed(1);
    md += `
## Budget

| | |
|--|--|
| Budget | ${formatCost(config.costBudget)} |
| Spent | ${formatCost(agg.totalCost)} |
| Utilization | ${pct}% |
| Remaining | ${formatCost(Math.max(0, config.costBudget - agg.totalCost))} |
`;
  }

  // Per-task breakdown
  if (tasks.length > 0) {
    md += `\n## Tasks\n\n`;
    for (const task of tasks) {
      const dur = task.startTime && task.endTime
        ? formatDuration(task.endTime - task.startTime)
        : "N/A";
      md += `### ${task.name}\n\n`;
      md += `- **Status:** ${task.status}\n`;
      md += `- **Turns:** ${task.iteration}\n`;
      md += `- **Cost:** ${formatCost(task.totalCost)}\n`;
      md += `- **Tokens:** ${task.totalTokensIn.toLocaleString()} in / ${task.totalTokensOut.toLocaleString()} out\n`;
      md += `- **Tool Calls:** ${task.totalToolCalls}\n`;
      md += `- **Duration:** ${dur}\n`;
      if (task.lastEvaluation) {
        md += `- **Summary:** ${task.lastEvaluation.summary || "N/A"}\n`;
        if (task.lastEvaluation.remaining && task.lastEvaluation.remaining !== "None") {
          md += `- **Remaining:** ${task.lastEvaluation.remaining}\n`;
        }
      }
      md += `\n`;
    }
  }

  // Turn history table
  md += `## Turn History\n\n`;
  md += `| # | Task | Duration | Tokens In | Tokens Out | Cost | Tools | Status | Retries |\n`;
  md += `|---|------|----------|-----------|------------|------|-------|--------|--------|\n`;
  for (const turn of allTurns) {
    md += `| ${turn.turnNumber} `;
    md += `| ${(turn.taskId || "").slice(0, 8)} `;
    md += `| ${formatDuration(turn.durationMs)} `;
    md += `| ${turn.tokensIn.toLocaleString()} `;
    md += `| ${turn.tokensOut.toLocaleString()} `;
    md += `| ${formatCost(turn.costUsd)} `;
    md += `| ${turn.toolCalls} `;
    md += `| ${turn.status} `;
    md += `| ${turn.retryCount} |\n`;
  }

  // Performance analysis
  md += `\n## Performance\n\n`;
  md += `| Metric | Value |\n`;
  md += `|--------|-------|\n`;
  md += `| Avg Turn Duration | ${formatDuration(avgDuration)} |\n`;
  md += `| Min Turn Duration | ${formatDuration(minDuration)} |\n`;
  md += `| Max Turn Duration | ${formatDuration(maxDuration)} |\n`;
  md += `| Tokens/Second | ${tokensPerSecond} |\n`;
  md += `| Cost/Turn | ${formatCost(costPerTurn)} |\n`;
  md += `| Cost/1K Tokens | ${formatCost(agg.totalTokensIn + agg.totalTokensOut > 0 ? (agg.totalCost / ((agg.totalTokensIn + agg.totalTokensOut) / 1000)) : 0)} |\n`;

  md += `\n---\n*Generated by continuous-claude-stream.mjs*\n`;

  writeFileSync(config.reportFile, md);
  print(`${c.green}Report saved to ${config.reportFile}${c.reset}`);
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 15: SIGNAL HANDLING AND GRACEFUL SHUTDOWN
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Flag to prevent multiple concurrent shutdown sequences. Set to true
 * when gracefulShutdown is first called.
 *
 * @type {boolean}
 */
let isShuttingDown = false;

/**
 * Performs a graceful shutdown of the runner. Called on SIGINT, SIGTERM,
 * budget exhaustion, or user abort. The shutdown sequence:
 *
 *   1. Sets the isShuttingDown flag to prevent re-entry
 *   2. Updates session status in the metrics collector
 *   3. Saves the current state to disk for later --continue
 *   4. Generates the summary report
 *   5. Closes all SSE connections to dashboard clients
 *   6. Shuts down the dashboard HTTP server
 *   7. Prints a final summary to the terminal
 *   8. Exits the process with code 0 (clean) or 1 (error)
 *
 * @param {string} reason - Human-readable reason for the shutdown (e.g. "SIGINT", "budget-exceeded")
 */
function gracefulShutdown(reason) {
  if (isShuttingDown) return;
  isShuttingDown = true;

  print(`\n${c.yellow}${c.bold}Shutting down (${reason})...${c.reset}`);
  log(`Graceful shutdown: ${reason}`);

  metrics.sessionStatus = `SHUTDOWN (${reason})`;
  broadcastSSE("status", metrics.getSnapshot());

  // Save state for --continue
  const agg = metrics.getAggregates();
  const tasks = Array.from(metrics.tasks.values());
  saveState({
    sessionId: config.sessionId,
    task: config.task,
    tasks: tasks.map((t) => ({
      id: t.id,
      name: t.name,
      task: t.task,
      status: t.status,
      sessionId: t.sessionId,
      iteration: t.iteration,
      totalCost: t.totalCost,
      totalToolCalls: t.totalToolCalls,
      lastEvaluation: t.lastEvaluation,
    })),
    iteration: agg.totalTurns,
    totalCost: agg.totalCost,
    totalToolCalls: agg.totalToolCalls,
    status: metrics.sessionStatus,
    shutdownReason: reason,
    timestamp: new Date().toISOString(),
  });

  // Generate the final report
  try {
    generateReport();
  } catch (e) {
    log(`Report generation failed: ${e.message}`);
  }

  // Close dashboard connections
  for (const client of sseClients) {
    try { client.end(); } catch { /* ignore */ }
  }
  sseClients.clear();

  if (dashboardServer) {
    dashboardServer.close();
  }

  // Print final summary
  divider();
  print(`\n${c.bold}Final Summary${c.reset}`);
  print(`  Reason: ${reason}`);
  print(`  Turns: ${agg.totalTurns}`);
  print(`  Tool calls: ${agg.totalToolCalls}`);
  print(`  Total cost: ${formatCost(agg.totalCost)}`);
  print(`  Duration: ${formatDuration(agg.elapsedMs)}`);
  print(`  Retries: ${agg.totalRetries}`);
  print(`  Report: ${config.reportFile}`);
  print(`  Session: ${config.sessionId || "N/A"}`);

  if (metrics.sessionStatus.includes("SHUTDOWN")) {
    print(`\n${c.yellow}Resume with: node continuous-claude-stream.mjs --continue${c.reset}`);
  }

  process.exit(reason === "complete" ? 0 : 1);
}

/**
 * Registers signal handlers for SIGINT (Ctrl+C) and SIGTERM (kill).
 * Both signals trigger the same graceful shutdown sequence, ensuring
 * state is saved and the report is generated before exit.
 */
function setupSignalHandlers() {
  process.on("SIGINT", () => gracefulShutdown("SIGINT"));
  process.on("SIGTERM", () => gracefulShutdown("SIGTERM"));
  process.on("uncaughtException", (err) => {
    log(`Uncaught exception: ${err.stack}`);
    print(`${c.red}Uncaught exception: ${err.message}${c.reset}`);
    gracefulShutdown("uncaught-exception");
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 16: SINGLE TASK RUNNER
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Runs a single task through its full lifecycle of turns. This is the core
 * execution loop that drives one Claude session from start to completion.
 *
 * The loop performs these steps on each iteration:
 *   1. Check for injected prompts (interactive mode)
 *   2. Check the retry queue for ready items
 *   3. Build the turn prompt (first turn vs continuation)
 *   4. Execute the turn via runClaudeTurn with timing
 *   5. Record metrics for the turn
 *   6. Check budget constraints
 *   7. Evaluate progress (is the task complete?)
 *   8. Handle errors with retry queue or fail
 *   9. Save state for resumability
 *   10. Pause between turns
 *
 * @param {string} taskId    - Unique identifier for this task
 * @param {string} taskName  - Human-readable task name for display
 * @param {string} taskText  - The full task description/prompt
 * @param {Object} [opts={}] - Optional overrides
 * @param {string} [opts.sessionId]    - Session ID for continuation
 * @param {number} [opts.startIteration] - Starting turn number (for resume)
 * @returns {Promise<Object>} Final result with status, turns completed, and cost
 */
async function runSingleTask(taskId, taskName, taskText, opts = {}) {
  metrics.addTask(taskId, taskName, taskText);
  metrics.updateTaskStatus(taskId, "running");

  const task = metrics.tasks.get(taskId);
  let iteration = opts.startIteration || 0;
  let taskSessionId = opts.sessionId || null;
  let status = "IN_PROGRESS";

  while (iteration < config.maxIterations && status === "IN_PROGRESS") {
    // ── Check for shutdown ──
    if (isShuttingDown) break;

    // ── Check for pause ──
    while (isPaused && !isShuttingDown) {
      await sleep(500);
    }

    iteration++;
    const isFirstTurn = iteration === 1 && !opts.sessionId;

    print(
      `\n${c.blue}${c.bold}[${taskName} Turn ${iteration}/${config.maxIterations}]${c.reset} ` +
      `${c.dim}(cost: ${formatCost(task.totalCost)})${c.reset}`
    );

    // ── Build prompt ──
    // Priority: injected prompt > retry queue > auto-generated
    let prompt;
    let retryItem = null;

    if (injectedPrompts.length > 0) {
      prompt = injectedPrompts.shift();
      print(`${c.magenta}Using injected prompt: "${prompt.slice(0, 60)}..."${c.reset}`);
    } else {
      // Check retry queue for ready items belonging to this task
      const readyRetries = retryQueue.getReady().filter((r) => r.taskId === taskId);
      if (readyRetries.length > 0) {
        retryItem = readyRetries[0];
        prompt = retryItem.prompt;
        print(`${c.yellow}Retrying (attempt ${retryItem.retryCount}/${config.maxRetries})${c.reset}`);
      } else if (isFirstTurn) {
        prompt = `${taskText}\n\nWork on this task step by step. Be thorough — read existing code before modifying, run tests after changes, and verify your work. After completing each major step, briefly state what you did and what comes next.`;
      } else {
        prompt = `Continue working on this task:\n\n"${taskText}"\n\nPick up where you left off. Do the next logical step. Be thorough — verify your changes work before moving on.`;
      }
    }

    // ── Execute turn with timing ──
    const turnStartTime = Date.now();
    try {
      const result = await runClaudeTurn(prompt, {
        continueSession: !isFirstTurn,
        sessionId: taskSessionId,
      });

      const turnDuration = Date.now() - turnStartTime;

      // Update session ID from result
      if (result.sessionId) {
        taskSessionId = result.sessionId;
        task.sessionId = result.sessionId;
        // Update global session ID for the primary task
        if (!config.sessionId) config.sessionId = result.sessionId;
      }

      // Record metrics for this turn
      const textPreview = result.text.split("\n").slice(0, 3).join(" ").slice(0, 200);
      metrics.recordTurn(taskId, {
        tokensIn: result.tokensIn,
        tokensOut: result.tokensOut,
        costUsd: result.costUsd,
        toolCalls: result.toolCalls,
        durationMs: turnDuration,
        status: "success",
        retryCount: retryItem ? retryItem.retryCount : 0,
        textPreview,
      });

      // Print turn summary
      if (textPreview.trim()) {
        print(`${c.dim}${textPreview.slice(0, 300)}${result.text.length > 300 ? "..." : ""}${c.reset}`);
      }
      print(
        `${c.dim}  tools: ${result.toolCalls} | tokens: ${result.tokensIn}→${result.tokensOut} ` +
        `| cost: ${formatCost(result.costUsd || 0)} | time: ${formatDuration(turnDuration)}${c.reset}`
      );
    } catch (e) {
      const turnDuration = Date.now() - turnStartTime;
      print(`${c.red}Error: ${e.message}${c.reset}`);
      log(`Turn ${iteration} error: ${e.stack}`);

      // Record the failed turn in metrics
      metrics.recordTurn(taskId, {
        durationMs: turnDuration,
        status: "error",
        error: e.message,
        retryCount: retryItem ? retryItem.retryCount : 0,
      });

      // Check if this error is retryable and we have retries left
      const currentRetryCount = retryItem ? retryItem.retryCount : 0;
      if (retryQueue.isRetryable(e.message) && currentRetryCount < config.maxRetries) {
        const retryEntry = retryItem || {
          taskId,
          prompt,
          options: { continueSession: !isFirstTurn, sessionId: taskSessionId },
          retryCount: currentRetryCount,
        };
        const delay = retryQueue.enqueue(retryEntry);
        print(`${c.yellow}Queued for retry in ${formatDuration(delay)} (attempt ${retryEntry.retryCount}/${config.maxRetries})${c.reset}`);
        // Don't count this as a "real" iteration
        iteration--;
        // Wait for the retry delay
        await sleep(delay);
        continue;
      }

      // Non-retryable error or max retries exceeded
      print(`${c.red}${c.bold}Permanent failure — stopping task${c.reset}`);
      status = "ERROR";
      break;
    }

    divider();

    // ── Check budget ──
    if (isBudgetExceeded()) {
      status = "BUDGET_EXCEEDED";
      metrics.updateTaskStatus(taskId, "blocked", {
        summary: "Cost budget exceeded",
        remaining: "Budget needs to be increased to continue",
      });
      break;
    }

    // ── Evaluate progress ──
    print(`${c.dim}Evaluating progress...${c.reset}`);
    const evaluation = await evaluateProgress(taskText, {
      sessionId: taskSessionId,
    });
    status = evaluation.status;

    switch (status) {
      case "COMPLETE":
        print(`${c.green}${c.bold}Task complete!${c.reset}`);
        print(`${c.dim}${evaluation.summary}${c.reset}`);
        break;
      case "BLOCKED":
        print(`${c.yellow}${c.bold}Blocked: ${evaluation.summary}${c.reset}`);
        print(`${c.dim}Remaining: ${evaluation.remaining}${c.reset}`);
        break;
      case "ERROR":
        print(`${c.red}${c.bold}Error: ${evaluation.summary}${c.reset}`);
        break;
      case "IN_PROGRESS":
        print(`${c.blue}Progress: ${evaluation.summary}${c.reset}`);
        print(`${c.dim}Next: ${evaluation.nextStep}${c.reset}`);
        break;
    }

    metrics.updateTaskStatus(taskId, status === "COMPLETE" ? "complete" : status === "ERROR" ? "error" : status === "BLOCKED" ? "blocked" : "running", evaluation);

    // ── Save state ──
    saveState({
      sessionId: taskSessionId || config.sessionId,
      task: config.task,
      taskId,
      iteration,
      totalCost: metrics.getAggregates().totalCost,
      totalToolCalls: metrics.getAggregates().totalToolCalls,
      status,
      lastEvaluation: evaluation,
      timestamp: new Date().toISOString(),
    });

    // ── Pause between turns ──
    if (status === "IN_PROGRESS" && iteration < config.maxIterations) {
      await sleep(config.pauseBetweenTurns);
    }
  }

  // Update final task status
  const finalStatus = status === "COMPLETE" ? "complete" : status === "IN_PROGRESS" ? "running" : "error";
  metrics.updateTaskStatus(taskId, finalStatus);

  return { status, iterations: iteration, cost: task.totalCost, sessionId: taskSessionId };
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 17: PARALLEL TASK RUNNER
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Executes multiple tasks concurrently using Promise.allSettled. Each task
 * runs in its own independent Claude session with its own turn loop.
 * Progress for all tasks is shown interleaved in the terminal and unified
 * in the web dashboard.
 *
 * Parallel tasks share the same:
 *   - Cost budget (aggregate cost across all tasks is checked)
 *   - Retry queue (each task's retries are tracked by taskId)
 *   - Metrics collector (unified dashboard view)
 *   - Interactive mode (injected prompts go to the first active task)
 *
 * @param {string[]} taskTexts - Array of task description strings
 * @returns {Promise<Object[]>} Array of results, one per task (from Promise.allSettled)
 */
async function runParallelTasks(taskTexts) {
  print(`\n${c.bold}${c.magenta}Running ${taskTexts.length} tasks in parallel${c.reset}`);
  divider();

  const promises = taskTexts.map((text, index) => {
    const taskId = randomUUID();
    const taskName = `Task ${index + 1}`;
    print(`${c.cyan}${taskName}: ${text.slice(0, 80)}${text.length > 80 ? "..." : ""}${c.reset}`);
    return runSingleTask(taskId, taskName, text);
  });

  const results = await Promise.allSettled(promises);

  // Summarize parallel results
  divider();
  print(`\n${c.bold}Parallel Task Results${c.reset}`);
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      const r = result.value;
      const icon = r.status === "COMPLETE" ? c.green + "✓" : r.status === "ERROR" ? c.red + "✗" : c.yellow + "◎";
      print(`  ${icon} Task ${index + 1}:${c.reset} ${r.status} (${r.iterations} turns, ${formatCost(r.cost)})`);
    } else {
      print(`  ${c.red}✗ Task ${index + 1}: FAILED — ${result.reason?.message || "Unknown error"}${c.reset}`);
    }
  });

  return results;
}

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 18: MAIN ORCHESTRATOR
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Main entry point. Orchestrates the entire runner lifecycle:
 *
 *   1. Parse CLI arguments
 *   2. Set up signal handlers for graceful shutdown
 *   3. Initialize the log file
 *   4. Start the web dashboard
 *   5. Set up interactive mode (stdin)
 *   6. Load previous state if --continue
 *   7. Route to parallel or single task execution
 *   8. Generate the summary report on completion
 *   9. Perform clean shutdown
 *
 * This function is the top-level async coordinator that ties all subsystems
 * together. Errors bubble up here and trigger graceful shutdown.
 */
async function main() {
  // Parse CLI arguments into config
  parseArgs();

  // Register signal handlers early so state is always saved
  setupSignalHandlers();

  // Initialize log file with session header
  writeFileSync(
    config.logFile,
    `# Continuous Claude — ${new Date().toISOString()}\n`
  );

  // Start the web dashboard
  startDashboard();

  // Set up interactive stdin commands
  setupInteractiveMode();

  // Load previous state if resuming
  let state = config.continueMode ? loadState() : null;
  if (state) {
    config.sessionId = state.sessionId;
    config.task = state.task || config.task;
    print(
      `${c.yellow}Resuming session ${state.sessionId} (turn ${(state.iteration || 0) + 1})${c.reset}`
    );
  }

  // Print startup banner
  print(`${c.bold}Continuous Claude${c.reset}`);
  if (config.task) {
    print(
      `${c.dim}Task: ${config.task.slice(0, 100)}${config.task.length > 100 ? "..." : ""}${c.reset}`
    );
  }
  if (config.parallelTasks.length > 0) {
    print(`${c.dim}Parallel tasks: ${config.parallelTasks.length}${c.reset}`);
  }
  print(
    `${c.dim}Model: ${config.model} | Max: ${config.maxIterations} turns | Log: ${config.logFile}${c.reset}`
  );
  if (config.costBudget > 0) {
    print(`${c.dim}Budget: ${formatCost(config.costBudget)}${c.reset}`);
  }
  if (!config.noDashboard) {
    print(`${c.dim}Dashboard: http://localhost:${config.dashboardPort}${c.reset}`);
  }
  divider();

  metrics.sessionStatus = "RUNNING";
  broadcastSSE("status", metrics.getSnapshot());

  // ── Execute tasks ──
  let finalStatus;

  if (config.parallelTasks.length > 0) {
    // Parallel execution mode
    const tasks = config.parallelTasks;
    // If there's also a main task, include it
    if (config.task) tasks.unshift(config.task);
    await runParallelTasks(tasks);

    // Determine overall status from parallel results
    const taskStatuses = Array.from(metrics.tasks.values()).map((t) => t.status);
    if (taskStatuses.every((s) => s === "complete")) {
      finalStatus = "COMPLETE";
    } else if (taskStatuses.some((s) => s === "error")) {
      finalStatus = "ERROR";
    } else {
      finalStatus = "IN_PROGRESS";
    }
  } else {
    // Single task execution
    const taskId = randomUUID();
    const result = await runSingleTask(
      taskId,
      "Main Task",
      config.task,
      {
        sessionId: config.sessionId,
        startIteration: state?.iteration || 0,
      }
    );
    finalStatus = result.status;
    if (result.sessionId) config.sessionId = result.sessionId;
  }

  // ── Finalize ──
  metrics.sessionStatus = finalStatus;
  broadcastSSE("status", metrics.getSnapshot());

  // Generate the report
  try {
    generateReport();
  } catch (e) {
    log(`Report generation failed: ${e.message}`);
  }

  // Print final summary
  const agg = metrics.getAggregates();
  divider();
  print(`\n${c.bold}Session Summary${c.reset}`);
  print(`  Status: ${finalStatus}`);
  print(`  Turns: ${agg.totalTurns}`);
  print(`  Tool calls: ${agg.totalToolCalls}`);
  print(`  Total cost: ${formatCost(agg.totalCost)}`);
  print(`  Duration: ${formatDuration(agg.elapsedMs)}`);
  print(`  Retries: ${agg.totalRetries}`);
  print(`  Tokens: ${agg.totalTokensIn.toLocaleString()} in / ${agg.totalTokensOut.toLocaleString()} out`);
  print(`  Session: ${config.sessionId || "N/A"}`);
  print(`  Report: ${config.reportFile}`);
  print(`  Log: ${config.logFile}`);

  if (finalStatus === "IN_PROGRESS") {
    print(
      `\n${c.yellow}Resume with: node continuous-claude-stream.mjs --continue${c.reset}`
    );
  }

  // Clean shutdown
  if (dashboardServer) dashboardServer.close();
  for (const client of sseClients) {
    try { client.end(); } catch { /* ignore */ }
  }
  process.exit(finalStatus === "COMPLETE" ? 0 : 1);
}

// ── Launch ──
main().catch((e) => {
  console.error(`${c.red}Fatal: ${e.message}${c.reset}`);
  log(`Fatal: ${e.stack}`);
  gracefulShutdown("fatal-error");
});
