#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// test-max-plan.mjs — Interactive test suite for Claude Max plan behavior
//
// Empirically probes rate limits, caching, model switching, effort
// levels, and throughput to discover exact Max plan constraints and
// find optimal strategies for long-running sessions.
//
// Usage:
//   node test-max-plan.mjs                    # interactive menu
//   node test-max-plan.mjs --test quota       # run specific test
//   node test-max-plan.mjs --all              # run all tests
//   node test-max-plan.mjs --report           # show past results
//
// ─────────────────────────────────────────────────────────────────────

import { spawn } from "child_process";
import { createInterface } from "readline";
import { readFileSync, writeFileSync, appendFileSync, existsSync } from "fs";

// ═════════════════════════════════════════════════════════════════════
// CONSTANTS
// ═════════════════════════════════════════════════════════════════════

const RESULTS_FILE = "test-results.json";
const LOG_FILE = "test-max-plan.log";

// ═════════════════════════════════════════════════════════════════════
// TERMINAL
// ═════════════════════════════════════════════════════════════════════

const c = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  blue: "\x1b[34m", cyan: "\x1b[36m", magenta: "\x1b[35m",
  underline: "\x1b[4m",
};

function print(msg = "") { console.log(msg); }
function header(msg) { print(`\n${c.bold}${c.blue}${msg}${c.reset}`); }
function success(msg) { print(`  ${c.green}OK${c.reset} ${msg}`); }
function warn(msg) { print(`  ${c.yellow}!!${c.reset} ${msg}`); }
function fail(msg) { print(`  ${c.red}FAIL${c.reset} ${msg}`); }
function info(msg) { print(`  ${c.dim}${msg}${c.reset}`); }
function divider() { print(`${c.dim}${"─".repeat(64)}${c.reset}`); }

function log(msg) {
  appendFileSync(LOG_FILE, `[${new Date().toISOString()}] ${msg}\n`);
}

// ═════════════════════════════════════════════════════════════════════
// RESULTS PERSISTENCE
// ═════════════════════════════════════════════════════════════════════

function loadResults() {
  try {
    return existsSync(RESULTS_FILE) ? JSON.parse(readFileSync(RESULTS_FILE, "utf-8")) : {};
  } catch { return {}; }
}

function saveResult(testName, data) {
  const results = loadResults();
  if (!results[testName]) results[testName] = [];
  results[testName].push({ ...data, timestamp: new Date().toISOString() });
  writeFileSync(RESULTS_FILE, JSON.stringify(results, null, 2));
}

// ═════════════════════════════════════════════════════════════════════
// CLAUDE RUNNER
// ═════════════════════════════════════════════════════════════════════

/**
 * Run a single claude turn and capture full telemetry.
 * Returns timing, tokens, cost, rate limit events, and session info.
 */
function claudeRun(prompt, options = {}) {
  return new Promise((resolve, reject) => {
    const model = options.model || "sonnet";
    const effort = options.effort || "high";

    const cmdArgs = [
      "-p",
      "--model", model,
      "--output-format", "stream-json",
      "--verbose",
    ];

    if (options.permissionMode) {
      cmdArgs.push("--permission-mode", options.permissionMode);
    }
    if (options.continueSession) {
      cmdArgs.push("-c");
    }
    if (options.effort) {
      cmdArgs.push("--effort", effort);
    }
    if (options.maxTurns) {
      cmdArgs.push("--max-turns", String(options.maxTurns));
    }

    cmdArgs.push(prompt);

    const startTime = Date.now();

    const child = spawn("claude", cmdArgs, {
      cwd: process.cwd(),
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, ...(options.env || {}) },
    });

    let text = "";
    let sessionId = null;
    let toolCalls = 0;
    let tokensIn = 0;
    let tokensOut = 0;
    let cacheRead = 0;
    let cacheCreation = 0;
    let costUsd = 0;
    let rateLimitEvents = [];
    let firstTokenTime = null;
    let eventCount = 0;
    let stopReason = null;

    const rl = createInterface({ input: child.stdout });
    rl.on("line", (line) => {
      try {
        const event = JSON.parse(line);
        eventCount++;

        switch (event.type) {
          case "system":
            if (event.session_id) sessionId = event.session_id;
            break;
          case "assistant":
            if (!firstTokenTime) firstTokenTime = Date.now();
            if (event.message?.content) {
              for (const block of event.message.content) {
                if (block.type === "text") text += block.text;
              }
            }
            if (event.message?.stop_reason) stopReason = event.message.stop_reason;
            break;
          case "tool_use":
            toolCalls++;
            break;
          case "rate_limit_event":
            rateLimitEvents.push({
              time: Date.now() - startTime,
              ...event,
            });
            break;
          case "result":
            if (event.result) {
              text = "";
              for (const block of event.result) {
                if (block.type === "text") text += block.text;
              }
            }
            if (event.usage) {
              tokensIn = event.usage.input_tokens || 0;
              tokensOut = event.usage.output_tokens || 0;
              cacheRead = event.usage.cache_read_input_tokens || 0;
              cacheCreation = event.usage.cache_creation_input_tokens || 0;
            }
            if (event.cost_usd) costUsd = event.cost_usd;
            if (event.session_id) sessionId = event.session_id;
            break;
        }
      } catch {}
    });

    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });

    child.on("close", (code) => {
      const duration = Date.now() - startTime;
      const ttft = firstTokenTime ? firstTokenTime - startTime : null;

      resolve({
        text: text.slice(0, 2000),
        sessionId,
        toolCalls,
        tokensIn,
        tokensOut,
        cacheRead,
        cacheCreation,
        costUsd,
        rateLimitEvents,
        duration,
        ttft,
        eventCount,
        stopReason,
        exitCode: code,
        stderr: stderr.slice(0, 500),
        model,
        effort,
      });
    });

    child.on("error", reject);

    // Hard timeout
    const timeout = options.timeout || 300000; // 5min default
    setTimeout(() => {
      child.kill("SIGTERM");
    }, timeout);
  });
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Quota Discovery
// ═════════════════════════════════════════════════════════════════════

async function testQuotaDiscovery() {
  header("TEST 1: Quota Window Discovery");
  info("Sends minimal requests to read rate limit headers and discover quota state.");
  divider();

  for (const model of ["sonnet", "opus"]) {
    print(`\n  ${c.cyan}Model: ${model}${c.reset}`);

    const result = await claudeRun(
      'Respond with exactly: "ping"',
      { model, maxTurns: 1, effort: "low" }
    );

    if (result.exitCode !== 0) {
      fail(`${model}: exit code ${result.exitCode} — ${result.stderr.slice(0, 100)}`);
      continue;
    }

    print(`    Tokens: ${result.tokensIn} in / ${result.tokensOut} out`);
    print(`    Cache read: ${result.cacheRead} | Cache creation: ${result.cacheCreation}`);
    print(`    Duration: ${result.duration}ms | TTFT: ${result.ttft}ms`);
    print(`    Rate limit events: ${result.rateLimitEvents.length}`);

    if (result.rateLimitEvents.length > 0) {
      for (const rl of result.rateLimitEvents) {
        print(`    ${c.yellow}Rate limit at ${rl.time}ms: ${JSON.stringify(rl).slice(0, 200)}${c.reset}`);
      }
    }

    saveResult("quota_discovery", {
      model,
      tokensIn: result.tokensIn,
      tokensOut: result.tokensOut,
      cacheRead: result.cacheRead,
      cacheCreation: result.cacheCreation,
      duration: result.duration,
      ttft: result.ttft,
      rateLimitEvents: result.rateLimitEvents,
    });

    success(`${model}: ${result.tokensIn + result.tokensOut} total tokens, ${result.duration}ms`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Rate Limit Mapping
// ═════════════════════════════════════════════════════════════════════

async function testRateLimitMapping() {
  header("TEST 2: Rate Limit Mapping");
  info("Sends bursts of requests to discover when rate limits trigger.");
  info("Tracks tokens consumed vs time to map the 5h window shape.");
  divider();

  const model = "sonnet"; // Use sonnet — faster, less likely to hit 7d limit
  const bursts = [];
  const maxBursts = 20;

  for (let i = 0; i < maxBursts; i++) {
    print(`\n  ${c.cyan}Burst ${i + 1}/${maxBursts}${c.reset}`);

    const result = await claudeRun(
      `Write a detailed 500-word essay about the number ${i + 1}. Be creative and thorough.`,
      { model, maxTurns: 1, effort: "high" }
    );

    const burst = {
      index: i,
      tokensIn: result.tokensIn,
      tokensOut: result.tokensOut,
      cacheRead: result.cacheRead,
      duration: result.duration,
      ttft: result.ttft,
      rateLimited: result.rateLimitEvents.length > 0,
      rateLimitEvents: result.rateLimitEvents,
      exitCode: result.exitCode,
    };
    bursts.push(burst);

    const totalTokens = bursts.reduce((s, b) => s + b.tokensIn + b.tokensOut, 0);
    print(`    Tokens this burst: ${result.tokensIn + result.tokensOut}`);
    print(`    Total tokens so far: ${totalTokens}`);
    print(`    Duration: ${result.duration}ms | TTFT: ${result.ttft}ms`);

    if (result.rateLimitEvents.length > 0) {
      warn(`Rate limit hit after ${totalTokens} total tokens, ${bursts.length} requests`);
      for (const rl of result.rateLimitEvents) {
        print(`    ${c.yellow}${JSON.stringify(rl).slice(0, 300)}${c.reset}`);
      }
      // Don't stop — keep going to measure the wait
    }

    if (result.exitCode !== 0) {
      fail(`Exit code ${result.exitCode}: ${result.stderr.slice(0, 200)}`);
      // Still continue — we want to measure recovery
    }

    // Brief pause between bursts to not overwhelm
    await sleep(2000);
  }

  saveResult("rate_limit_mapping", { model, bursts });
  divider();

  // Summary
  const totalTokens = bursts.reduce((s, b) => s + b.tokensIn + b.tokensOut, 0);
  const rateLimitedAt = bursts.findIndex(b => b.rateLimited);
  const tokensBeforeLimit = rateLimitedAt >= 0
    ? bursts.slice(0, rateLimitedAt).reduce((s, b) => s + b.tokensIn + b.tokensOut, 0)
    : totalTokens;

  print(`\n  ${c.bold}Summary:${c.reset}`);
  print(`    Total requests: ${bursts.length}`);
  print(`    Total tokens: ${totalTokens}`);
  print(`    First rate limit: ${rateLimitedAt >= 0 ? `burst ${rateLimitedAt + 1} (after ${tokensBeforeLimit} tokens)` : "none hit"}`);
  print(`    Avg TTFT: ${Math.round(bursts.reduce((s, b) => s + (b.ttft || 0), 0) / bursts.length)}ms`);
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Prompt Cache Effectiveness
// ═════════════════════════════════════════════════════════════════════

async function testPromptCaching() {
  header("TEST 3: Prompt Cache Effectiveness");
  info("Sends 5 identical requests to measure cache hit rate.");
  info("Expects 1h cache TTL for Max subscribers.");
  divider();

  const prompt = "Respond with exactly one word: hello";
  const runs = [];

  for (let i = 0; i < 5; i++) {
    print(`\n  ${c.cyan}Request ${i + 1}/5${c.reset}`);

    const result = await claudeRun(prompt, {
      model: "sonnet",
      maxTurns: 1,
      effort: "low",
      // Continue session after first to test cache across turns
      continueSession: i > 0,
    });

    runs.push({
      index: i,
      tokensIn: result.tokensIn,
      tokensOut: result.tokensOut,
      cacheRead: result.cacheRead,
      cacheCreation: result.cacheCreation,
      duration: result.duration,
      ttft: result.ttft,
    });

    const cacheHitRate = result.cacheRead / Math.max(1, result.tokensIn + result.cacheRead);
    print(`    Tokens in: ${result.tokensIn} | Cache read: ${result.cacheRead} | Cache creation: ${result.cacheCreation}`);
    print(`    Cache hit rate: ${(cacheHitRate * 100).toFixed(1)}%`);
    print(`    Duration: ${result.duration}ms | TTFT: ${result.ttft}ms`);

    if (cacheHitRate > 0.5) {
      success(`Strong cache hit: ${(cacheHitRate * 100).toFixed(1)}%`);
    } else if (i > 0) {
      warn(`Low cache hit on request ${i + 1}: ${(cacheHitRate * 100).toFixed(1)}%`);
    }

    await sleep(3000);
  }

  saveResult("prompt_caching", { runs });

  // Summary
  divider();
  print(`\n  ${c.bold}Cache Summary:${c.reset}`);
  const avgCacheHit = runs.slice(1).reduce((s, r) => s + r.cacheRead / Math.max(1, r.tokensIn + r.cacheRead), 0) / Math.max(1, runs.length - 1);
  print(`    Avg cache hit rate (requests 2-5): ${(avgCacheHit * 100).toFixed(1)}%`);
  print(`    First request cache creation: ${runs[0]?.cacheCreation || 0} tokens`);
  print(`    Tokens saved by cache: ~${runs.slice(1).reduce((s, r) => s + r.cacheRead, 0)} tokens`);
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Effort Level Impact
// ═════════════════════════════════════════════════════════════════════

async function testEffortLevels() {
  header("TEST 4: Effort Level Impact");
  info("Sends the same prompt at each effort level to measure token/quality tradeoff.");
  divider();

  const prompt = "Explain how a hash table works. Include time complexity analysis.";
  const levels = ["low", "medium", "high"];
  const runs = [];

  for (const effort of levels) {
    print(`\n  ${c.cyan}Effort: ${effort}${c.reset}`);

    const result = await claudeRun(prompt, {
      model: "sonnet",
      maxTurns: 1,
      effort,
    });

    const run = {
      effort,
      tokensIn: result.tokensIn,
      tokensOut: result.tokensOut,
      duration: result.duration,
      ttft: result.ttft,
      textLength: result.text.length,
      wordCount: result.text.split(/\s+/).length,
    };
    runs.push(run);

    print(`    Tokens out: ${result.tokensOut} | Words: ${run.wordCount}`);
    print(`    Duration: ${result.duration}ms | TTFT: ${result.ttft}ms`);
    print(`    Response preview: ${result.text.slice(0, 100)}...`);

    await sleep(3000);
  }

  saveResult("effort_levels", { runs });

  // Summary
  divider();
  print(`\n  ${c.bold}Effort Comparison:${c.reset}`);
  for (const r of runs) {
    const bar = "█".repeat(Math.round(r.tokensOut / 50));
    print(`    ${r.effort.padEnd(8)} ${String(r.tokensOut).padStart(6)} tokens | ${String(r.wordCount).padStart(5)} words | ${bar}`);
  }

  if (runs.length >= 2) {
    const ratio = runs[runs.length - 1].tokensOut / Math.max(1, runs[0].tokensOut);
    print(`\n    High/Low token ratio: ${ratio.toFixed(1)}x`);
    info(`Using "low" effort for eval turns saves ~${((1 - 1/ratio) * 100).toFixed(0)}% of output tokens`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Model Throughput Comparison
// ═════════════════════════════════════════════════════════════════════

async function testModelThroughput() {
  header("TEST 5: Model Throughput Comparison");
  info("Measures tokens/second for Opus vs Sonnet to compare throughput.");
  divider();

  const prompt = "Write a detailed technical analysis of WebSocket protocol implementation including code examples in TypeScript. Be thorough.";
  const models = ["sonnet", "opus"];
  const runs = [];

  for (const model of models) {
    print(`\n  ${c.cyan}Model: ${model}${c.reset}`);

    const result = await claudeRun(prompt, {
      model,
      maxTurns: 1,
      effort: "high",
      timeout: 600000, // 10min for opus
    });

    const tokPerSec = result.tokensOut / (result.duration / 1000);
    const run = {
      model,
      tokensIn: result.tokensIn,
      tokensOut: result.tokensOut,
      duration: result.duration,
      ttft: result.ttft,
      tokPerSec: Math.round(tokPerSec),
      rateLimitEvents: result.rateLimitEvents.length,
    };
    runs.push(run);

    print(`    Tokens out: ${result.tokensOut}`);
    print(`    Duration: ${(result.duration / 1000).toFixed(1)}s | TTFT: ${result.ttft}ms`);
    print(`    Throughput: ${c.bold}${Math.round(tokPerSec)} tok/s${c.reset}`);
    print(`    Rate limit events: ${result.rateLimitEvents.length}`);

    await sleep(5000);
  }

  saveResult("model_throughput", { runs });

  divider();
  print(`\n  ${c.bold}Throughput Comparison:${c.reset}`);
  for (const r of runs) {
    const bar = "█".repeat(Math.round(r.tokPerSec / 5));
    print(`    ${r.model.padEnd(8)} ${String(r.tokPerSec).padStart(4)} tok/s | ${(r.duration / 1000).toFixed(1)}s | ${bar}`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Session Continuity Overhead
// ═════════════════════════════════════════════════════════════════════

async function testSessionContinuity() {
  header("TEST 6: Session Continuity Overhead");
  info("Measures the cost of --continue vs fresh sessions.");
  info("Reveals how much context accumulates per turn.");
  divider();

  const turns = 5;
  const runs = [];

  // First turn: establish session
  print(`\n  ${c.cyan}Turn 1 (fresh)${c.reset}`);
  let result = await claudeRun(
    "Remember the number 42. Respond with just: Remembered.",
    { model: "sonnet", maxTurns: 1, effort: "low" }
  );

  runs.push({
    turn: 1,
    fresh: true,
    tokensIn: result.tokensIn,
    tokensOut: result.tokensOut,
    cacheRead: result.cacheRead,
    duration: result.duration,
  });
  print(`    Tokens in: ${result.tokensIn} | out: ${result.tokensOut} | cache: ${result.cacheRead}`);

  // Subsequent turns: continue
  for (let i = 2; i <= turns; i++) {
    print(`\n  ${c.cyan}Turn ${i} (--continue)${c.reset}`);
    await sleep(2000);

    result = await claudeRun(
      `What number did I ask you to remember? Respond with just the number.`,
      { model: "sonnet", maxTurns: 1, effort: "low", continueSession: true }
    );

    runs.push({
      turn: i,
      fresh: false,
      tokensIn: result.tokensIn,
      tokensOut: result.tokensOut,
      cacheRead: result.cacheRead,
      duration: result.duration,
    });
    print(`    Tokens in: ${result.tokensIn} | out: ${result.tokensOut} | cache: ${result.cacheRead}`);
    print(`    Response: ${result.text.trim().slice(0, 50)}`);

    // Check context growth
    if (i > 1) {
      const growth = result.tokensIn - runs[i - 2].tokensIn;
      print(`    Context growth: ${c.yellow}+${growth} tokens${c.reset} from previous turn`);
    }
  }

  saveResult("session_continuity", { runs });

  divider();
  print(`\n  ${c.bold}Context Growth:${c.reset}`);
  for (const r of runs) {
    const bar = "█".repeat(Math.round(r.tokensIn / 200));
    print(`    Turn ${r.turn}: ${String(r.tokensIn).padStart(6)} in | ${bar}`);
  }
  const totalGrowth = runs[runs.length - 1].tokensIn - runs[0].tokensIn;
  info(`Total context growth over ${turns} turns: +${totalGrowth} tokens`);
  info(`Avg per turn: +${Math.round(totalGrowth / (turns - 1))} tokens`);
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Tool Call Rate Limit Cost
// ═════════════════════════════════════════════════════════════════════

async function testToolCallCost() {
  header("TEST 7: Tool Call Rate Limit Cost");
  info("Measures how tool calls consume rate limit quota.");
  info("Each tool call = separate API round trip = separate token charge.");
  divider();

  // Request that triggers multiple tool calls
  const result = await claudeRun(
    "Read the file test-max-plan.mjs, then count the number of lines, then tell me the first line.",
    {
      model: "sonnet",
      effort: "low",
      maxTurns: 5,
      permissionMode: "plan",
    }
  );

  print(`    Tool calls: ${result.toolCalls}`);
  print(`    Total tokens in: ${result.tokensIn}`);
  print(`    Total tokens out: ${result.tokensOut}`);
  print(`    Duration: ${(result.duration / 1000).toFixed(1)}s`);
  print(`    Rate limit events: ${result.rateLimitEvents.length}`);
  print(`    Events total: ${result.eventCount}`);

  if (result.toolCalls > 0) {
    const tokPerToolCall = Math.round((result.tokensIn + result.tokensOut) / result.toolCalls);
    print(`\n    ${c.bold}Tokens per tool call: ~${tokPerToolCall}${c.reset}`);
    info("Each tool call re-sends the full context + tool result to the API");
    info("This means tool-heavy sessions consume quota much faster than text-only");
  }

  saveResult("tool_call_cost", {
    toolCalls: result.toolCalls,
    tokensIn: result.tokensIn,
    tokensOut: result.tokensOut,
    duration: result.duration,
    rateLimitEvents: result.rateLimitEvents,
  });
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Rate Limit Recovery Timing
// ═════════════════════════════════════════════════════════════════════

async function testRateLimitRecovery() {
  header("TEST 8: Rate Limit Recovery Timing");
  info("After hitting a rate limit, measures exactly how long until recovery.");
  info("Sends a request every 30s to find the recovery point.");
  divider();

  // First, try to trigger a rate limit by burning tokens
  print(`\n  ${c.cyan}Phase 1: Burning tokens to trigger rate limit...${c.reset}`);

  let rateLimited = false;
  let burnCount = 0;

  while (!rateLimited && burnCount < 10) {
    burnCount++;
    const result = await claudeRun(
      `Write a 1000-word creative story about adventure number ${burnCount}. Be extremely detailed and verbose.`,
      { model: "opus", maxTurns: 1, effort: "high", timeout: 600000 }
    );

    print(`    Burn ${burnCount}: ${result.tokensOut} tokens out, ${result.rateLimitEvents.length} rate limit events`);

    if (result.rateLimitEvents.length > 0 || result.exitCode !== 0) {
      rateLimited = true;
      warn("Rate limit triggered!");
    }

    await sleep(1000);
  }

  if (!rateLimited) {
    info("Could not trigger rate limit within 10 burns. Quota may be high.");
    saveResult("rate_limit_recovery", { triggered: false, burns: burnCount });
    return;
  }

  // Phase 2: Probe for recovery
  print(`\n  ${c.cyan}Phase 2: Probing for recovery (every 30s)...${c.reset}`);

  const probeStart = Date.now();
  let recovered = false;
  let probeCount = 0;

  while (!recovered && probeCount < 40) { // max 20 minutes
    await sleep(30000);
    probeCount++;

    const elapsed = Math.round((Date.now() - probeStart) / 1000);
    print(`    Probe ${probeCount} (${elapsed}s elapsed)...`);

    const result = await claudeRun(
      'Say "ok"',
      { model: "opus", maxTurns: 1, effort: "low", timeout: 30000 }
    );

    if (result.exitCode === 0 && result.rateLimitEvents.length === 0) {
      recovered = true;
      success(`Recovered after ${elapsed}s (${(elapsed / 60).toFixed(1)} minutes)`);
    } else {
      info(`Still rate limited (${elapsed}s)`);
    }
  }

  saveResult("rate_limit_recovery", {
    triggered: true,
    burns: burnCount,
    recoveryTimeSec: Math.round((Date.now() - probeStart) / 1000),
    recovered,
    probes: probeCount,
  });
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Model Rotation Effectiveness
// ═════════════════════════════════════════════════════════════════════

async function testModelRotation() {
  header("TEST 9: Model Rotation Effectiveness");
  info("Alternates between Opus and Sonnet to test independent quota pools.");
  divider();

  const pairs = 6;
  const runs = [];

  for (let i = 0; i < pairs; i++) {
    for (const model of ["opus", "sonnet"]) {
      print(`\n  ${c.cyan}Pair ${i + 1}, Model: ${model}${c.reset}`);

      const result = await claudeRun(
        `Write a paragraph about the number ${i * 2 + (model === "opus" ? 1 : 2)}.`,
        { model, maxTurns: 1, effort: "medium" }
      );

      runs.push({
        pair: i + 1,
        model,
        tokensOut: result.tokensOut,
        duration: result.duration,
        rateLimited: result.rateLimitEvents.length > 0,
      });

      const status = result.rateLimitEvents.length > 0 ? `${c.yellow}RATE LIMITED${c.reset}` : `${c.green}OK${c.reset}`;
      print(`    ${status} — ${result.tokensOut} tokens, ${result.duration}ms`);

      await sleep(2000);
    }
  }

  saveResult("model_rotation", { runs });

  divider();
  const opusLimited = runs.filter(r => r.model === "opus" && r.rateLimited).length;
  const sonnetLimited = runs.filter(r => r.model === "sonnet" && r.rateLimited).length;
  print(`\n  ${c.bold}Results:${c.reset}`);
  print(`    Opus rate limited:   ${opusLimited}/${pairs} requests`);
  print(`    Sonnet rate limited: ${sonnetLimited}/${pairs} requests`);

  if (opusLimited > 0 && sonnetLimited === 0) {
    success("Confirmed: Opus and Sonnet have independent rate limit pools!");
    info("Model rotation is effective for sustained throughput.");
  } else if (opusLimited > 0 && sonnetLimited > 0) {
    warn("Both models rate limited — may share a quota or both exhausted.");
  } else {
    info("No rate limits hit — quota not exhausted during test.");
  }
}

// ═════════════════════════════════════════════════════════════════════
// TEST: Max Output Token Escalation
// ═════════════════════════════════════════════════════════════════════

async function testOutputEscalation() {
  header("TEST 10: Output Token Escalation Behavior");
  info("Requests very long output to trigger 8k→64k escalation.");
  info("Measures the extra API call cost of escalation.");
  divider();

  // Request that should exceed 8k tokens
  const result = await claudeRun(
    "Write an extremely detailed, comprehensive guide to building a full-stack web application from scratch. Cover every single aspect: project setup, database design, API design, authentication, frontend, deployment, monitoring, testing, CI/CD, security. Do not stop until you have covered everything in extreme detail. This should be at least 5000 words.",
    { model: "sonnet", maxTurns: 1, effort: "high", timeout: 300000 }
  );

  print(`    Tokens out: ${c.bold}${result.tokensOut}${c.reset}`);
  print(`    Duration: ${(result.duration / 1000).toFixed(1)}s`);
  print(`    Stop reason: ${result.stopReason || "unknown"}`);
  print(`    Events: ${result.eventCount}`);
  print(`    Text length: ${result.text.length} chars`);

  if (result.tokensOut > 8000) {
    success(`Output exceeded 8k cap (${result.tokensOut} tokens) — escalation likely occurred`);
    info("This means the request used 2 API calls: one at 8k (hit limit), one at 64k");
  } else {
    info(`Output stayed under 8k (${result.tokensOut} tokens) — no escalation needed`);
  }

  saveResult("output_escalation", {
    tokensOut: result.tokensOut,
    tokensIn: result.tokensIn,
    duration: result.duration,
    stopReason: result.stopReason,
    textLength: result.text.length,
  });
}

// ═════════════════════════════════════════════════════════════════════
// SLEEP
// ═════════════════════════════════════════════════════════════════════

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ═════════════════════════════════════════════════════════════════════
// REPORT
// ═════════════════════════════════════════════════════════════════════

function showReport() {
  header("Test Results Report");
  const results = loadResults();

  if (Object.keys(results).length === 0) {
    info("No results yet. Run some tests first.");
    return;
  }

  for (const [testName, entries] of Object.entries(results)) {
    print(`\n  ${c.bold}${testName}${c.reset} (${entries.length} runs)`);
    const latest = entries[entries.length - 1];
    print(`    Latest: ${latest.timestamp}`);
    print(`    ${c.dim}${JSON.stringify(latest).slice(0, 200)}...${c.reset}`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// INTERACTIVE MENU
// ═════════════════════════════════════════════════════════════════════

const TESTS = {
  quota:       { fn: testQuotaDiscovery,     desc: "Discover current quota state" },
  ratelimit:   { fn: testRateLimitMapping,   desc: "Map rate limit thresholds (20 bursts)" },
  cache:       { fn: testPromptCaching,      desc: "Measure prompt cache effectiveness" },
  effort:      { fn: testEffortLevels,       desc: "Compare effort level token cost" },
  throughput:  { fn: testModelThroughput,    desc: "Compare Opus vs Sonnet throughput" },
  continuity:  { fn: testSessionContinuity,  desc: "Measure --continue context overhead" },
  toolcost:    { fn: testToolCallCost,       desc: "Measure tool call quota consumption" },
  recovery:    { fn: testRateLimitRecovery,  desc: "Time rate limit recovery (long test)" },
  rotation:    { fn: testModelRotation,      desc: "Test Opus/Sonnet independent quotas" },
  escalation:  { fn: testOutputEscalation,   desc: "Trigger output token escalation" },
};

async function interactiveMenu() {
  print(`${c.bold}Claude Max Plan Test Suite${c.reset}`);
  print(`${c.dim}Empirically discover rate limits, caching, and throughput${c.reset}\n`);

  const keys = Object.keys(TESTS);
  for (let i = 0; i < keys.length; i++) {
    const t = TESTS[keys[i]];
    print(`  ${c.cyan}${String(i + 1).padStart(2)}.${c.reset} ${keys[i].padEnd(14)} ${c.dim}${t.desc}${c.reset}`);
  }
  print(`  ${c.cyan} R.${c.reset} report         ${c.dim}Show past results${c.reset}`);
  print(`  ${c.cyan} A.${c.reset} all            ${c.dim}Run all tests in order${c.reset}`);
  print(`  ${c.cyan} Q.${c.reset} quit`);

  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const ask = (q) => new Promise(resolve => rl.question(q, resolve));

  while (true) {
    const answer = (await ask(`\n${c.bold}>${c.reset} `)).trim().toLowerCase();

    if (answer === "q" || answer === "quit") {
      rl.close();
      return;
    }

    if (answer === "r" || answer === "report") {
      showReport();
      continue;
    }

    if (answer === "a" || answer === "all") {
      for (const key of keys) {
        await TESTS[key].fn();
        await sleep(3000);
      }
      continue;
    }

    // Number selection
    const num = parseInt(answer);
    if (num >= 1 && num <= keys.length) {
      await TESTS[keys[num - 1]].fn();
      continue;
    }

    // Name selection
    if (TESTS[answer]) {
      await TESTS[answer].fn();
      continue;
    }

    print(`${c.red}Unknown option: ${answer}${c.reset}`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════

async function main() {
  writeFileSync(LOG_FILE, `# test-max-plan.mjs — ${new Date().toISOString()}\n`);

  const args = process.argv.slice(2);

  if (args.includes("--report")) {
    showReport();
    return;
  }

  if (args.includes("--all")) {
    for (const key of Object.keys(TESTS)) {
      await TESTS[key].fn();
      await sleep(3000);
    }
    return;
  }

  const testIdx = args.indexOf("--test");
  if (testIdx !== -1 && args[testIdx + 1]) {
    const name = args[testIdx + 1];
    if (TESTS[name]) {
      await TESTS[name].fn();
      return;
    }
    print(`${c.red}Unknown test: ${name}${c.reset}`);
    print(`Available: ${Object.keys(TESTS).join(", ")}`);
    return;
  }

  await interactiveMenu();
}

main().catch(e => {
  console.error(`${c.red}Fatal: ${e.message}${c.reset}`);
  process.exit(1);
});
