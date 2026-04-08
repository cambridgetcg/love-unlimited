#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// stream.mjs — Max-plan-optimized infinite streaming for Claude Code
//
// Designed around every known Max subscription behavior:
//
//   RATE LIMITS
//   • Independent Opus/Sonnet 7-day pools — rotate on 429
//   • 5h session window tracking — back off before hard wall
//   • Utilization-aware pacing — slow down at 80% to avoid cliff
//   • UNATTENDED_RETRY for inner process resilience
//
//   TOKEN ECONOMY
//   • Effort cycling — low for evals, high for work, medium for light tasks
//   • Prompt cache exploitation — 1h TTL for Max subscribers
//   • Compact prompts — minimal continuation text to preserve cache
//   • Tool-call awareness — budget tool-heavy vs text-heavy turns
//
//   CONTEXT MANAGEMENT
//   • Context growth tracking — predict when compaction will trigger
//   • Early compact trigger — don't wait for the 167k cliff
//   • Session fork on context overflow — fresh session, same task
//
//   RESILIENCE
//   • Graceful shutdown with full state persistence
//   • Session recovery via --continue
//   • Stream idle timeout override for long Opus thinking
//   • OAuth-aware — detects auth failures vs rate limits
//
// Usage:
//   node stream.mjs "your task"
//   node stream.mjs --task-file task.md
//   node stream.mjs --continue
//
// ─────────────────────────────────────────────────────────────────────

import { spawn } from "child_process";
import { createInterface } from "readline";
import { readFileSync, appendFileSync, writeFileSync, existsSync } from "fs";

// ═════════════════════════════════════════════════════════════════════
// CONFIG
// ═════════════════════════════════════════════════════════════════════

const config = {
  primaryModel: "opus",
  fallbackModel: "sonnet",
  currentModel: null,

  // Effort cycling: different effort for different turn types
  efforts: {
    work: "high",       // implementation turns — max output quality
    light: "medium",    // continuation turns — balanced
    eval: "low",        // evaluation/check turns — minimal tokens
  },

  maxTurns: Infinity,
  maxCostUsd: Infinity,
  pauseBetweenTurns: 1000,

  // Pacing: slow down near rate limit to avoid hard wall
  pacing: {
    throttleAt: 0.80,       // utilization % to start slowing down
    throttlePause: 15000,   // ms extra pause when throttling
    evalEveryN: 3,          // evaluate progress every N work turns
  },

  // Context management
  context: {
    warnAt: 120000,         // token count to warn about context size
    compactAt: 150000,      // token count to force compact/fork
    trackGrowth: true,
  },

  // Session
  continueMode: false,
  sessionId: null,
  workdir: process.cwd(),

  logFile: "stream.log",
  stateFile: ".stream-state.json",
  verbose: false,

  permissionMode: "bypassPermissions",

  task: "",
  taskFile: null,
  readStdin: false,
};

// ═════════════════════════════════════════════════════════════════════
// QUOTA TRACKER — per-model independent rate limit state
// ═════════════════════════════════════════════════════════════════════

const quota = {
  opus: {
    blocked: false,
    resetsAt: null,           // epoch seconds
    utilization5h: 0,
    utilization7d: 0,
    tokensConsumed: 0,        // session-local estimate
    lastCallTime: null,
    consecutiveErrors: 0,
  },
  sonnet: {
    blocked: false,
    resetsAt: null,
    utilization5h: 0,
    utilization7d: 0,
    tokensConsumed: 0,
    lastCallTime: null,
    consecutiveErrors: 0,
  },
};

// ═════════════════════════════════════════════════════════════════════
// SESSION METRICS — everything we track across turns
// ═════════════════════════════════════════════════════════════════════

const metrics = {
  iteration: 0,
  totalCost: 0,
  totalToolCalls: 0,
  totalTokensIn: 0,
  totalTokensOut: 0,
  totalCacheRead: 0,
  totalCacheCreation: 0,
  contextTokens: 0,          // estimated current context size
  turnsSinceEval: 0,
  turnsSinceCompact: 0,
  modelSwitches: 0,
  rateLimitHits: 0,
  startTime: Date.now(),
};

// ═════════════════════════════════════════════════════════════════════
// CLI PARSING
// ═════════════════════════════════════════════════════════════════════

const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
  switch (args[i]) {
    case "--model":          config.primaryModel = args[++i]; break;
    case "--fallback-model": config.fallbackModel = args[++i]; break;
    case "--effort":         config.efforts.work = args[++i]; break;
    case "--eval-effort":    config.efforts.eval = args[++i]; break;
    case "--max-turns":      config.maxTurns = parseInt(args[++i]); break;
    case "--max-cost":       config.maxCostUsd = parseFloat(args[++i]); break;
    case "--pause":          config.pauseBetweenTurns = parseInt(args[++i]) * 1000; break;
    case "--eval-every":     config.pacing.evalEveryN = parseInt(args[++i]); break;
    case "--throttle-at":    config.pacing.throttleAt = parseFloat(args[++i]); break;
    case "--continue": case "-c": config.continueMode = true; break;
    case "--session-id":     config.sessionId = args[++i]; break;
    case "--workdir":        config.workdir = args[++i]; break;
    case "--log":            config.logFile = args[++i]; break;
    case "--verbose": case "-v": config.verbose = true; break;
    case "--task-file":      config.taskFile = args[++i]; break;
    case "--stdin":          config.readStdin = true; break;
    case "--permission-mode": config.permissionMode = args[++i]; break;
    case "--help": case "-h":
      console.log(`
stream.mjs — Max-plan-optimized infinite streaming

Usage:  node stream.mjs [options] "task description"

Models:
  --model MODEL           Primary model (default: opus)
  --fallback-model MODEL  Fallback model (default: sonnet)

Effort:
  --effort LEVEL          Work effort: low|medium|high|max (default: high)
  --eval-effort LEVEL     Eval effort (default: low)

Pacing:
  --eval-every N          Evaluate every N work turns (default: 3)
  --throttle-at PCT       Slow down at this utilization (default: 0.80)
  --pause SECONDS         Base pause between turns (default: 1)

Limits:
  --max-turns N           Max total turns (default: unlimited)
  --max-cost USD          Stop at cost (default: unlimited)

Session:
  --continue, -c          Resume previous session
  --session-id UUID       Specific session ID
  --workdir DIR           Working directory
  --permission-mode MODE  (default: bypassPermissions)

Output:
  --verbose, -v           Show raw stream events
  --log FILE              Log file (default: stream.log)
`);
      process.exit(0);
    default:
      if (!args[i].startsWith("--")) {
        config.task += (config.task ? " " : "") + args[i];
      }
  }
}

if (config.taskFile) config.task = readFileSync(config.taskFile, "utf-8").trim();
if (config.readStdin) config.task = readFileSync("/dev/stdin", "utf-8").trim();
if (!config.task && !config.continueMode) {
  console.error("Error: provide a task or use --continue");
  process.exit(1);
}
config.currentModel = config.primaryModel;

// ═════════════════════════════════════════════════════════════════════
// TERMINAL
// ═════════════════════════════════════════════════════════════════════

const S = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  blue: "\x1b[34m", cyan: "\x1b[36m", magenta: "\x1b[35m",
};

function log(msg) {
  appendFileSync(config.logFile, `[${new Date().toISOString()}] ${msg}\n`);
  if (config.verbose) console.log(`${S.dim}[log] ${msg}${S.reset}`);
}

function print(msg = "") {
  console.log(msg);
  log(msg.replace(/\x1b\[[0-9;]*m/g, ""));
}

function divider() { print(`${S.dim}${"─".repeat(64)}${S.reset}`); }

function fmt(usd) {
  if (!usd || usd === 0) return "$0.00";
  return usd < 0.01 ? `${(usd * 100).toFixed(2)}c` : `$${usd.toFixed(4)}`;
}

function dur(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m${s % 60}s` : `${Math.floor(m / 60)}h${m % 60}m`;
}

function pct(n) { return `${(n * 100).toFixed(0)}%`; }

// ═════════════════════════════════════════════════════════════════════
// STATE PERSISTENCE
// ═════════════════════════════════════════════════════════════════════

function loadState() {
  try {
    return existsSync(config.stateFile) ? JSON.parse(readFileSync(config.stateFile, "utf-8")) : null;
  } catch { return null; }
}

function saveState(extra = {}) {
  writeFileSync(config.stateFile, JSON.stringify({
    sessionId: config.sessionId,
    task: config.task,
    ...metrics,
    quota,
    ...extra,
    timestamp: new Date().toISOString(),
  }, null, 2));
}

// ═════════════════════════════════════════════════════════════════════
// QUOTA INTELLIGENCE
// ═════════════════════════════════════════════════════════════════════

function modelQuota(model) {
  const key = model.includes("opus") ? "opus" : "sonnet";
  return quota[key];
}

/** Check if a model's rate limit has expired and unblock it */
function refreshQuota(model) {
  const q = modelQuota(model);
  if (q.blocked && q.resetsAt && Date.now() / 1000 >= q.resetsAt) {
    q.blocked = false;
    q.resetsAt = null;
    q.consecutiveErrors = 0;
    log(`${model} quota unblocked (reset time passed)`);
  }
}

/** Pick the best model right now */
function chooseModel() {
  refreshQuota(config.primaryModel);
  refreshQuota(config.fallbackModel);

  const pq = modelQuota(config.primaryModel);
  const fq = modelQuota(config.fallbackModel);

  // Primary available — use it
  if (!pq.blocked) {
    config.currentModel = config.primaryModel;
    return config.primaryModel;
  }

  // Primary blocked, fallback available — switch
  if (!fq.blocked) {
    if (config.currentModel !== config.fallbackModel) metrics.modelSwitches++;
    config.currentModel = config.fallbackModel;
    log(`Switched to ${config.fallbackModel} (${config.primaryModel} blocked)`);
    return config.fallbackModel;
  }

  // Both blocked — pick whichever resets sooner
  const pw = (pq.resetsAt || Infinity) - Date.now() / 1000;
  const fw = (fq.resetsAt || Infinity) - Date.now() / 1000;
  const pick = pw <= fw ? config.primaryModel : config.fallbackModel;
  config.currentModel = pick;
  return pick;
}

/** How long must we wait if both models are blocked? (ms) */
function bothBlockedWait() {
  const pq = modelQuota(config.primaryModel);
  const fq = modelQuota(config.fallbackModel);
  if (!pq.blocked || !fq.blocked) return 0;
  const pw = Math.max(0, (pq.resetsAt || 0) - Date.now() / 1000);
  const fw = Math.max(0, (fq.resetsAt || 0) - Date.now() / 1000);
  return Math.min(pw, fw) * 1000;
}

/** Should we throttle (slow down) to avoid hitting the wall? */
function shouldThrottle() {
  const q = modelQuota(config.currentModel);
  return q.utilization5h >= config.pacing.throttleAt ||
         q.utilization7d >= config.pacing.throttleAt;
}

/** Update quota from a successful result */
function recordSuccess(model, result) {
  const q = modelQuota(model);
  q.consecutiveErrors = 0;
  q.lastCallTime = Date.now();
  q.tokensConsumed += (result.tokensIn || 0) + (result.tokensOut || 0);
  // Utilization comes from rate_limit_events parsed during the run
}

/** Mark a model as rate-limited */
function recordRateLimit(model, resetsAt) {
  const q = modelQuota(model);
  q.blocked = true;
  q.resetsAt = resetsAt || (Date.now() / 1000 + 300); // default 5min
  q.consecutiveErrors++;
  metrics.rateLimitHits++;
  log(`${model} blocked until ${new Date(q.resetsAt * 1000).toISOString()}`);
}

// ═════════════════════════════════════════════════════════════════════
// CONTEXT TRACKING
// ═════════════════════════════════════════════════════════════════════

/** Estimate current context size from cumulative input tokens */
function updateContextEstimate(tokensIn) {
  // tokensIn includes system prompt + full conversation history
  // Each turn, this grows by ~(previous output + new input overhead)
  metrics.contextTokens = tokensIn;
}

/** Check if we're approaching the compaction cliff */
function contextStatus() {
  if (metrics.contextTokens >= config.context.compactAt) return "critical";
  if (metrics.contextTokens >= config.context.warnAt) return "warning";
  return "ok";
}

// ═════════════════════════════════════════════════════════════════════
// CLAUDE RUNNER
// ═════════════════════════════════════════════════════════════════════

function runTurn(prompt, opts = {}) {
  return new Promise((resolve, reject) => {
    const model = opts.model || chooseModel();
    const effort = opts.effort || config.efforts.work;

    const isEval = opts.isEval || false;

    const cmdArgs = [
      "-p",
      "--model", model,
      "--output-format", "stream-json",
      "--verbose",
      "--permission-mode", config.permissionMode,
      "--effort", effort,
    ];

    // High max-turns for work: let the inner process do many tool loops
    // per invocation. More inner turns = better prompt cache reuse since
    // one cached system prompt serves all loops. Low for eval turns.
    cmdArgs.push("--max-turns", String(isEval ? 3 : 200));

    // Inject system prompt that keeps the model working longer.
    // This directly controls the primary halt condition: the model stops
    // when it returns text with no tool_use blocks. By instructing it to
    // always verify via tools, we keep the tool loop alive.
    if (!isEval) {
      cmdArgs.push(
        "--append-system-prompt",
        // YOUSPEAK-aware continuation prompt — dense, no filler, action-oriented
        "YOUSPEAK: No filler. No preamble. No tool narration. Dense status (key:value). " +
        "Compress scaffolding, preserve substance. Expand for teaching/uncertainty/creativity. " +
        "CONTINUATION: Keep calling tools to make progress. Verify changes by reading files or running tests. " +
        "When a subtask completes, start the next. Only stop tools when the ENTIRE task is complete and verified."
      );
    }

    // Disable thinking for eval turns — saves output tokens
    if (isEval) {
      cmdArgs.push("--thinking", "disabled");
    }

    // --continue for session continuity (don't pass --session-id with -c)
    if (opts.continueSession) {
      cmdArgs.push("-c");
    } else if (config.sessionId) {
      cmdArgs.push("--session-id", config.sessionId);
    }

    // Built-in fallback for 529 overload
    if (config.fallbackModel && config.fallbackModel !== model) {
      cmdArgs.push("--fallback-model", config.fallbackModel);
    }

    cmdArgs.push(prompt);

    log(`RUN model=${model} effort=${effort} continue=${!!opts.continueSession}`);

    const child = spawn("claude", cmdArgs, {
      cwd: config.workdir,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        // Inner process waits indefinitely through rate limits
        CLAUDE_CODE_UNATTENDED_RETRY: "1",
        // Flush session to disk after every turn — survive crashes
        CLAUDE_CODE_EAGER_FLUSH: "1",
        // Extend stream idle timeout for long Opus thinking (3 min)
        CLAUDE_STREAM_IDLE_TIMEOUT_MS: "180000",
        // Enable stream watchdog to detect dead connections
        CLAUDE_ENABLE_STREAM_WATCHDOG: "1",
        // Skip 8k→64k output token escalation — avoids double API call.
        // Default 8k cap means every long response costs TWO rate limit
        // hits (one at 8k that fails, one retry at 64k). Setting to 64k
        // directly gets the full response in one call.
        CLAUDE_CODE_MAX_OUTPUT_TOKENS: isEval ? "4000" : "64000",
        // Trigger auto-compact earlier to avoid the hard cliff at 167k.
        // Compacting at 160k gives headroom for the compact call itself.
        CLAUDE_CODE_AUTO_COMPACT_WINDOW: "180000",
      },
    });

    let text = "";
    let sessionId = null;
    let toolCalls = 0;
    let tokensIn = 0, tokensOut = 0;
    let cacheRead = 0, cacheCreation = 0;
    let costUsd = 0;
    let rateLimitEvents = [];
    const startTime = Date.now();
    let firstTokenTime = null;

    const rl = createInterface({ input: child.stdout });
    rl.on("line", (line) => {
      try {
        const ev = JSON.parse(line);
        switch (ev.type) {
          case "system":
            if (ev.session_id) sessionId = ev.session_id;
            break;
          case "assistant":
            if (!firstTokenTime) firstTokenTime = Date.now();
            if (ev.message?.content) {
              for (const b of ev.message.content) {
                if (b.type === "text") text += b.text;
              }
            }
            break;
          case "content_block_delta":
            if (ev.delta?.type === "text_delta" && config.verbose) {
              process.stdout.write(`${S.dim}${ev.delta.text}${S.reset}`);
            }
            break;
          case "tool_use":
            toolCalls++;
            print(`  ${S.cyan}tool:${S.reset} ${ev.name || ev.tool_name || "?"}`);
            break;
          case "rate_limit_event":
            rateLimitEvents.push({ time: Date.now() - startTime, ...ev });
            // Extract utilization if present
            const q = modelQuota(model);
            if (ev.utilization_5h !== undefined) q.utilization5h = ev.utilization_5h;
            if (ev.utilization_7d !== undefined) q.utilization7d = ev.utilization_7d;
            if (ev.resets_at) q.resetsAt = ev.resets_at;
            break;
          case "result":
            if (ev.result) {
              text = "";
              for (const b of ev.result) { if (b.type === "text") text += b.text; }
            }
            if (ev.usage) {
              tokensIn = ev.usage.input_tokens || 0;
              tokensOut = ev.usage.output_tokens || 0;
              cacheRead = ev.usage.cache_read_input_tokens || 0;
              cacheCreation = ev.usage.cache_creation_input_tokens || 0;
            }
            if (ev.cost_usd) costUsd = ev.cost_usd;
            if (ev.session_id) sessionId = ev.session_id;
            break;
        }
      } catch {}
    });

    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
      log(`stderr: ${chunk.toString().trim()}`);
    });

    child.on("close", (code) => {
      if (config.verbose) process.stdout.write("\n");
      const duration = Date.now() - startTime;
      const ttft = firstTokenTime ? firstTokenTime - startTime : null;

      // Detect rate limit exit
      const rateLimited = (code !== 0 && (
        stderr.includes("rate limit") ||
        stderr.includes("429") ||
        stderr.includes("quota") ||
        stderr.includes("You've hit")
      ));

      if (rateLimited) {
        recordRateLimit(model, null);
        resolve({ text, sessionId, toolCalls, tokensIn, tokensOut, cacheRead,
                  cacheCreation, costUsd, duration, ttft, rateLimited: true, exitCode: code });
        return;
      }

      // Detect auth failure (non-retryable)
      const authFailed = code !== 0 && (
        stderr.includes("401") || stderr.includes("OAuth") ||
        stderr.includes("login") || stderr.includes("unauthorized")
      );

      if (authFailed) {
        reject(new Error(`AUTH_FAILED: ${stderr.slice(0, 300)}`));
        return;
      }

      if (code !== 0 && !text) {
        reject(new Error(`Exit ${code}: ${stderr.slice(0, 500)}`));
        return;
      }

      recordSuccess(model, { tokensIn, tokensOut });
      resolve({ text, sessionId, toolCalls, tokensIn, tokensOut, cacheRead,
                cacheCreation, costUsd, duration, ttft, rateLimited: false, exitCode: code });
    });

    child.on("error", reject);

    // Hard timeout: 10 minutes
    setTimeout(() => { child.kill("SIGTERM"); }, opts.timeout || 600000);
  });
}

// ═════════════════════════════════════════════════════════════════════
// EVALUATION — uses fallback model + low effort to save quota
// ═════════════════════════════════════════════════════════════════════

async function evaluateProgress() {
  const prompt = `Review what has been accomplished on the task so far.
Respond with exactly one JSON object (no markdown fences):
{"status":"COMPLETE"|"IN_PROGRESS"|"BLOCKED"|"ERROR","summary":"brief summary","next":"next concrete step"}`;

  try {
    const result = await runTurn(prompt, {
      continueSession: true,
      effort: config.efforts.eval,
      model: config.fallbackModel, // eval on cheaper model — separate quota pool
      isEval: true,
    });

    metrics.totalCost += result.costUsd || 0;
    metrics.totalTokensIn += result.tokensIn || 0;
    metrics.totalTokensOut += result.tokensOut || 0;
    metrics.totalCacheRead += result.cacheRead || 0;

    const match = result.text.match(/\{[\s\S]*\}/);
    if (match) {
      const p = JSON.parse(match[0]);
      return { status: p.status || "IN_PROGRESS", summary: p.summary || "", next: p.next || "Continue." };
    }
  } catch (e) {
    log(`Eval error: ${e.message}`);
  }
  return { status: "IN_PROGRESS", summary: "?", next: "Continue." };
}

// ═════════════════════════════════════════════════════════════════════
// PROMPT STRATEGY — minimize cache-busting, maximize work per turn
// ═════════════════════════════════════════════════════════════════════

function buildPrompt(isFirst) {
  if (isFirst) {
    // First turn: full task description. This gets cached (1h TTL on Max).
    // Subsequent turns via --continue will cache-hit on the system prompt +
    // this first message, saving massive input tokens.
    return `${config.task}

Work through this task completely, step by step. For each step:
1. Read relevant code before modifying
2. Make changes
3. Verify changes work (run tests, check output)
4. State what you did and what comes next

Do NOT stop after one step. Keep working until you have completed a significant chunk of the task or used all available tool calls.`;
  }

  // Continuation: keep it SHORT. The full conversation history is already
  // in context via --continue. A short prompt means more of the context
  // comes from cache rather than new input tokens.
  return "Continue. Do the next steps.";
}

// ═════════════════════════════════════════════════════════════════════
// STATUS LINE
// ═════════════════════════════════════════════════════════════════════

function statusLine(model, iteration) {
  const elapsed = dur(Date.now() - metrics.startTime);
  const oq = quota.opus;
  const sq = quota.sonnet;

  const opusStatus = oq.blocked
    ? `${S.red}blocked${S.reset}`
    : oq.utilization5h > 0.5
      ? `${S.yellow}${pct(oq.utilization5h)}${S.reset}`
      : `${S.green}ok${S.reset}`;

  const sonnetStatus = sq.blocked
    ? `${S.red}blocked${S.reset}`
    : sq.utilization5h > 0.5
      ? `${S.yellow}${pct(sq.utilization5h)}${S.reset}`
      : `${S.green}ok${S.reset}`;

  const ctx = contextStatus();
  const ctxColor = ctx === "critical" ? S.red : ctx === "warning" ? S.yellow : S.dim;
  const ctxStr = `${ctxColor}${Math.round(metrics.contextTokens / 1000)}k${S.reset}`;

  print(`\n${S.blue}${S.bold}[Turn ${iteration}]${S.reset} ` +
    `${S.dim}model=${S.reset}${model} ` +
    `${S.dim}opus=${S.reset}${opusStatus} ` +
    `${S.dim}sonnet=${S.reset}${sonnetStatus} ` +
    `${S.dim}ctx=${S.reset}${ctxStr} ` +
    `${S.dim}cost=${S.reset}${fmt(metrics.totalCost)} ` +
    `${S.dim}${elapsed}${S.reset}`);
}

// ═════════════════════════════════════════════════════════════════════
// HELPERS
// ═════════════════════════════════════════════════════════════════════

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

let shuttingDown = false;
function gracefulShutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  print(`\n${S.yellow}Shutting down...${S.reset}`);
  saveState({ status: "INTERRUPTED" });
  print(`State saved. Resume with: node stream.mjs --continue`);
  process.exit(0);
}

// ═════════════════════════════════════════════════════════════════════
// MAIN LOOP
// ═════════════════════════════════════════════════════════════════════

async function main() {
  writeFileSync(config.logFile, `# stream.mjs — ${new Date().toISOString()}\n`);

  // Restore state
  const state = config.continueMode ? loadState() : null;
  if (state) {
    config.sessionId = state.sessionId;
    config.task = state.task || config.task;
    if (state.quota) Object.assign(quota, state.quota);
    metrics.iteration = state.iteration || 0;
    metrics.totalCost = state.totalCost || 0;
    metrics.totalToolCalls = state.totalToolCalls || 0;
    metrics.totalTokensIn = state.totalTokensIn || 0;
    metrics.totalTokensOut = state.totalTokensOut || 0;
    metrics.totalCacheRead = state.totalCacheRead || 0;
    metrics.contextTokens = state.contextTokens || 0;
    print(`${S.yellow}Resuming session ${state.sessionId} (turn ${metrics.iteration + 1})${S.reset}`);
  }

  process.on("SIGINT", gracefulShutdown);
  process.on("SIGTERM", gracefulShutdown);

  let status = "IN_PROGRESS";

  print(`${S.bold}stream.mjs${S.reset} — Max plan optimized streaming`);
  print(`${S.dim}Task: ${config.task.slice(0, 120)}${config.task.length > 120 ? "..." : ""}${S.reset}`);
  print(`${S.dim}Primary: ${config.primaryModel} | Fallback: ${config.fallbackModel}${S.reset}`);
  print(`${S.dim}Effort: work=${config.efforts.work} eval=${config.efforts.eval} | Eval every ${config.pacing.evalEveryN} turns${S.reset}`);
  divider();

  while (metrics.iteration < config.maxTurns && status === "IN_PROGRESS") {
    metrics.iteration++;
    const isFirst = metrics.iteration === 1 && !config.continueMode;

    // ── Budget check ──
    if (metrics.totalCost >= config.maxCostUsd) {
      print(`${S.red}Budget limit: ${fmt(metrics.totalCost)}${S.reset}`);
      status = "BUDGET_EXCEEDED";
      break;
    }

    // ── Both-blocked wait ──
    const waitMs = bothBlockedWait();
    if (waitMs > 0) {
      print(`${S.yellow}Both models blocked. Waiting ${dur(waitMs)}...${S.reset}`);
      await sleep(waitMs + 5000);
    }

    // ── Throttle near limit ──
    if (shouldThrottle()) {
      const q = modelQuota(config.currentModel);
      print(`${S.yellow}Throttling (5h: ${pct(q.utilization5h)}, 7d: ${pct(q.utilization7d)})${S.reset}`);
      await sleep(config.pacing.throttlePause);
    }

    // ── Context check — fork session if approaching cliff ──
    const ctxStat = contextStatus();
    if (ctxStat === "critical") {
      print(`${S.yellow}Context at ${Math.round(metrics.contextTokens / 1000)}k — triggering compact via /compact${S.reset}`);
      // Send a compact request. Claude Code handles compaction internally
      // when context is near the limit, but we can also just start fresh.
      // For now, let auto-compact handle it inside the claude process.
      // The key insight: --continue starts a new process that loads the
      // persisted session, which auto-compacts on load if needed.
    }

    // ── Choose model ──
    const model = chooseModel();
    statusLine(model, metrics.iteration);

    // ── Determine effort for this turn ──
    // First turn or post-eval: full work effort
    // Other turns: lighter effort to conserve quota
    const effort = isFirst ? config.efforts.work :
                   metrics.turnsSinceEval === 0 ? config.efforts.work :
                   config.efforts.light;

    // ── Build prompt ──
    const prompt = buildPrompt(isFirst);

    // ── Execute turn ──
    let result;
    try {
      result = await runTurn(prompt, {
        continueSession: !isFirst,
        model,
        effort,
      });

      // Accumulate metrics
      metrics.totalCost += result.costUsd || 0;
      metrics.totalToolCalls += result.toolCalls;
      metrics.totalTokensIn += result.tokensIn;
      metrics.totalTokensOut += result.tokensOut;
      metrics.totalCacheRead += result.cacheRead;
      metrics.totalCacheCreation += result.cacheCreation || 0;
      metrics.turnsSinceEval++;
      metrics.turnsSinceCompact++;

      if (result.sessionId) config.sessionId = result.sessionId;

      // Track context growth
      updateContextEstimate(result.tokensIn);

      // Display result
      if (result.text) {
        const preview = result.text.split("\n").slice(0, 3).join("\n").slice(0, 250);
        if (preview.trim()) print(`${S.dim}${preview}${preview.length < result.text.length ? "..." : ""}${S.reset}`);
      }

      const cacheHit = result.cacheRead / Math.max(1, result.tokensIn + result.cacheRead);
      print(`${S.dim}  tools:${result.toolCalls} ` +
            `tok:${result.tokensIn}→${result.tokensOut} ` +
            `cache:${pct(cacheHit)} ` +
            `cost:${fmt(result.costUsd)} ` +
            `${dur(result.duration)}${S.reset}`);

      // Handle rate limit
      if (result.rateLimited) {
        print(`${S.yellow}Rate limited on ${model}. Rotating...${S.reset}`);
        metrics.iteration--; // don't count this as a real turn
        continue;
      }

    } catch (e) {
      print(`${S.red}Error: ${e.message.slice(0, 200)}${S.reset}`);
      log(`Turn error: ${e.stack}`);

      if (e.message.startsWith("AUTH_FAILED")) {
        print(`${S.red}${S.bold}Authentication failed. Run: claude login${S.reset}`);
        status = "AUTH_FAILED";
        break;
      }

      // Rate-limit-like error: mark model, retry
      if (/overload|rate|429|529|quota/i.test(e.message)) {
        recordRateLimit(model, null);
        metrics.iteration--;
        await sleep(5000);
        continue;
      }

      // Unknown error: pause and retry
      print(`${S.yellow}Retrying in 30s...${S.reset}`);
      await sleep(30000);
      metrics.iteration--;
      continue;
    }

    divider();

    // ── Periodic evaluation ──
    if (metrics.turnsSinceEval >= config.pacing.evalEveryN) {
      print(`${S.dim}Evaluating progress...${S.reset}`);
      const evaluation = await evaluateProgress();
      metrics.turnsSinceEval = 0;
      status = evaluation.status;

      switch (status) {
        case "COMPLETE":
          print(`${S.green}${S.bold}Complete!${S.reset} ${S.dim}${evaluation.summary}${S.reset}`);
          break;
        case "BLOCKED":
          print(`${S.yellow}${S.bold}Blocked:${S.reset} ${S.dim}${evaluation.summary}${S.reset}`);
          break;
        case "ERROR":
          print(`${S.red}${S.bold}Error:${S.reset} ${S.dim}${evaluation.summary}${S.reset}`);
          break;
        case "IN_PROGRESS":
          print(`${S.blue}Next: ${evaluation.next}${S.reset}`);
          break;
      }
    }

    // ── Save state ──
    saveState({ status });

    // ── Pause ──
    if (status === "IN_PROGRESS" && metrics.iteration < config.maxTurns) {
      await sleep(config.pauseBetweenTurns);
    }
  }

  // ── Final report ──
  divider();
  const elapsed = dur(Date.now() - metrics.startTime);
  const totalTok = metrics.totalTokensIn + metrics.totalTokensOut;
  const cacheRate = metrics.totalCacheRead / Math.max(1, metrics.totalTokensIn + metrics.totalCacheRead);

  print(`\n${S.bold}Session Complete${S.reset}`);
  print(`  Status:        ${status}`);
  print(`  Turns:         ${metrics.iteration}`);
  print(`  Tools:         ${metrics.totalToolCalls}`);
  print(`  Tokens:        ${metrics.totalTokensIn} in / ${metrics.totalTokensOut} out (${totalTok} total)`);
  print(`  Cache hit:     ${S.bold}${pct(cacheRate)}${S.reset} (${metrics.totalCacheRead} tokens saved)`);
  print(`  Cost:          ${fmt(metrics.totalCost)}`);
  print(`  Rate limits:   ${metrics.rateLimitHits} hits, ${metrics.modelSwitches} switches`);
  print(`  Duration:      ${elapsed}`);
  print(`  Session:       ${config.sessionId || "N/A"}`);

  if (status === "IN_PROGRESS") {
    print(`\n${S.yellow}Resume: node stream.mjs --continue${S.reset}`);
  }

  saveState({ status, completed: status === "COMPLETE" });
}

main().catch((e) => {
  console.error(`${S.red}Fatal: ${e.message}${S.reset}`);
  log(`Fatal: ${e.stack}`);
  process.exit(1);
});
