#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// uwt.mjs — Useful Work per Token
//
// The fundamental benchmark for AI agent self-optimization.
//
// Useful Work is not a single number — it's a composite signal across
// 5 orthogonal dimensions that are hard to game simultaneously:
//
//   D1: TASK COMPLETION    — Did the objective get achieved?
//   D2: ACTION DENSITY     — Tool calls per output token
//   D3: INFO EFFICIENCY    — Reads that led to actions vs dead reads
//   D4: VERIFICATION RATE  — Changes verified vs unverified
//   D5: WASTE RATIO        — Filler + retries + errors / total
//
// Plus a CAUSAL CHAIN analysis that traces which token expenditures
// were on the productive path vs dead branches.
//
// UWT = (tokens on productive path) / (total tokens spent)
//
// Usage:
//   node uwt.mjs analyze                    # Analyze current session
//   node uwt.mjs analyze --state FILE       # Analyze specific state file
//   node uwt.mjs benchmark                  # Run standardized benchmark
//   node uwt.mjs compare FILE1 FILE2        # Compare two sessions
//   node uwt.mjs history                    # Show UWT trend over time
//   node uwt.mjs dimensions                 # Explain the 5 dimensions
// ─────────────────────────────────────────────────────────────────────

import { readFileSync, writeFileSync, existsSync, readdirSync } from "fs";
import { resolve, join, basename } from "path";
import { homedir } from "os";

const S = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  blue: "\x1b[34m", magenta: "\x1b[35m", cyan: "\x1b[36m",
};

const HISTORY_FILE = resolve("uwt-history.json");

// ═════════════════════════════════════════════════════════════════════
// THE 5 DIMENSIONS
// ═════════════════════════════════════════════════════════════════════

function showDimensions() {
  console.log(`
${S.bold}${S.cyan}═══ UWT — The 5 Dimensions of Useful Work ═══${S.reset}

${S.bold}D1: TASK COMPLETION${S.reset} (weight: 3.0)
  Did the stated objective get achieved?
  ${S.dim}Score: 0.0 (abandoned) → 0.5 (partial) → 1.0 (complete)
  Measurement: stop_reason=end_turn + no pending tool calls
  Anti-gaming: high completion with no verification = reckless${S.reset}

${S.bold}D2: ACTION DENSITY${S.reset} (weight: 2.0)
  Tool calls per 1000 output tokens.
  ${S.dim}Score: 0.0 (<1 call/1000tok) → 0.5 (5/1000) → 1.0 (15+/1000)
  Measurement: total_tool_calls / (output_tokens / 1000)
  Anti-gaming: high density with low completion = mindless busywork${S.reset}

${S.bold}D3: INFORMATION EFFICIENCY${S.reset} (weight: 2.0)
  Reads that led to subsequent actions vs dead-end reads.
  ${S.dim}Score: files_acted_on / files_read
  Measurement: track read_file→write_file/edit_file chains
  Anti-gaming: never reading = reckless writing${S.reset}

${S.bold}D4: VERIFICATION RATE${S.reset} (weight: 1.5)
  Changes that were verified (test run, file re-read, assertion).
  ${S.dim}Score: verified_changes / total_changes
  Measurement: write/edit followed by read or bash(test)
  Anti-gaming: 100% verification with no changes = paralysis${S.reset}

${S.bold}D5: WASTE RATIO${S.reset} (weight: 1.5)
  Proportion of tokens NOT wasted on filler, retries, errors.
  ${S.dim}Score: 1.0 - (waste_tokens / total_tokens)
  Measurement: filler patterns + error tool results + retry loops
  Anti-gaming: combined with D1 — low waste means nothing without completion${S.reset}

${S.bold}UWT COMPOSITE${S.reset}
  ${S.cyan}UWT = (D1×3 + D2×2 + D3×2 + D4×1.5 + D5×1.5) / 10${S.reset}
  Range: 0.0 → 1.0
  ${S.dim}0.0-0.2: Failing    0.2-0.4: Poor    0.4-0.6: Average
  0.6-0.8: Good      0.8-0.9: Excellent  0.9-1.0: Sovereign${S.reset}

${S.bold}CAUSAL CHAIN (bonus analysis)${S.reset}
  Traces the directed graph from task → tool calls → outcomes.
  Each node = token expenditure. Each edge = causal link.
  ${S.cyan}Chain Efficiency = tokens_on_productive_path / total_tokens${S.reset}
  Dead branches: reads that led nowhere, thinking without action,
  retried operations, error recovery overhead.
`);
}

// ═════════════════════════════════════════════════════════════════════
// SESSION PARSER — Extract the work chain from session state
// ═════════════════════════════════════════════════════════════════════

function parseSession(statePath) {
  if (!existsSync(statePath)) return null;
  const state = JSON.parse(readFileSync(statePath, "utf-8"));

  const chain = {
    turns: [],
    files: {
      read: new Map(),       // path → { count, tokens, ledToAction: bool }
      written: new Map(),    // path → { count, verified: bool }
      edited: new Map(),     // path → { count, verified: bool }
    },
    tools: {
      total: 0,
      byType: {},
      errors: 0,
      retries: 0,
    },
    tokens: {
      input: 0,
      output: 0,
      thinking: 0,
      filler: 0,
    },
    task: state.task || "unknown",
    completed: state.completed || false,
    turnCount: state.turnCount || 0,
  };

  // Filler detection patterns
  const FILLER = [
    /\b(sure|okay|alright|great|absolutely)[!.,]?\s/gi,
    /\blet me (check|look|see|think|examine|analyze)\b/gi,
    /\bi('ll| will) (now |go ahead |proceed to |start by )/gi,
    /\bhere('s| is) (what|the|a summary|an overview)/gi,
    /\bi('m| am) going to (read|check|look|search|write|create|modify)\b/gi,
    /\bnow (i('ll| will)|let me|let's) (move on|proceed|continue)\b/gi,
    /\b(i('ve| have) (successfully|now|just|finished|completed))\b/gi,
  ];

  if (!state.messages) return chain;

  // Walk the message chain to build the work graph
  let lastReadFiles = [];
  let pendingWrites = [];

  for (const msg of state.messages) {
    if (msg.role === "assistant" && Array.isArray(msg.content)) {
      const turn = { text: 0, tools: [], thinking: 0, filler: 0 };

      for (const block of msg.content) {
        if (block.type === "text" && block.text) {
          const text = block.text;
          turn.text += Math.round(text.length / 4);

          // Count filler
          for (const pat of FILLER) {
            pat.lastIndex = 0;
            const matches = text.match(pat);
            if (matches) turn.filler += matches.length * 8;
          }
        }

        if (block.type === "thinking" && block.thinking) {
          turn.thinking += Math.round(block.thinking.length / 4);
        }

        if (block.type === "tool_use") {
          chain.tools.total++;
          chain.tools.byType[block.name] = (chain.tools.byType[block.name] || 0) + 1;

          const tool = { name: block.name, input: block.input };
          turn.tools.push(tool);

          // Track file operations for causal chain
          if (block.name === "read_file" && block.input?.path) {
            const path = block.input.path;
            const entry = chain.files.read.get(path) || { count: 0, ledToAction: false };
            entry.count++;
            chain.files.read.set(path, entry);
            lastReadFiles.push(path);
          }

          if (block.name === "write_file" && block.input?.path) {
            const path = block.input.path;
            chain.files.written.set(path, { count: (chain.files.written.get(path)?.count || 0) + 1, verified: false });
            // Mark reads of this file as having led to action
            if (chain.files.read.has(path)) {
              chain.files.read.get(path).ledToAction = true;
            }
            pendingWrites.push(path);
          }

          if (block.name === "edit_file" && block.input?.path) {
            const path = block.input.path;
            chain.files.edited.set(path, { count: (chain.files.edited.get(path)?.count || 0) + 1, verified: false });
            if (chain.files.read.has(path)) {
              chain.files.read.get(path).ledToAction = true;
            }
            pendingWrites.push(path);
          }

          // Verification detection: re-reading a file after writing, or running tests
          if (block.name === "read_file" && block.input?.path) {
            const path = block.input.path;
            if (chain.files.written.has(path)) chain.files.written.get(path).verified = true;
            if (chain.files.edited.has(path)) chain.files.edited.get(path).verified = true;
          }

          if (block.name === "bash" && block.input?.command) {
            const cmd = block.input.command;
            // Test commands verify changes
            if (/test|jest|pytest|mocha|vitest|cargo test|go test|npm run|make check|node --check/i.test(cmd)) {
              for (const path of pendingWrites) {
                if (chain.files.written.has(path)) chain.files.written.get(path).verified = true;
                if (chain.files.edited.has(path)) chain.files.edited.get(path).verified = true;
              }
              pendingWrites = [];
            }
            // Git operations and builds also count as verification
            if (/git diff|git status|git log|make|build|compile|lint/i.test(cmd)) {
              for (const path of pendingWrites.slice(-3)) {
                if (chain.files.written.has(path)) chain.files.written.get(path).verified = true;
                if (chain.files.edited.has(path)) chain.files.edited.get(path).verified = true;
              }
            }
          }
        }
      }

      chain.turns.push(turn);
      chain.tokens.filler += turn.filler;
    }

    // Track error results from tool use
    if (msg.role === "user" && Array.isArray(msg.content)) {
      for (const block of msg.content) {
        if (block.type === "tool_result" && typeof block.content === "string") {
          if (/^(Error|Exit code [^0]|not found|Permission denied)/i.test(block.content)) {
            chain.tools.errors++;
          }
        }
      }
    }
  }

  // Extract token totals from state efficiency data if available
  if (state.efficiency) {
    chain.tokens.input = state.efficiency.totalInput || 0;
    chain.tokens.output = state.efficiency.totalOutput || 0;
    chain.tokens.thinking = state.efficiency.totalThinking || 0;
  } else {
    // Estimate from turns
    chain.tokens.output = chain.turns.reduce((s, t) => s + t.text, 0);
    chain.tokens.thinking = chain.turns.reduce((s, t) => s + t.thinking, 0);
  }

  return chain;
}

// ═════════════════════════════════════════════════════════════════════
// DIMENSION SCORERS
// ═════════════════════════════════════════════════════════════════════

function scoreD1_TaskCompletion(chain) {
  // Binary + heuristics
  if (chain.completed) return 1.0;
  if (chain.turnCount === 0) return 0.0;

  // Partial credit: if there were tool calls and some succeeded
  const successRate = chain.tools.total > 0
    ? 1 - (chain.tools.errors / chain.tools.total) : 0;

  // If many turns happened and tools were used, give partial credit
  if (chain.turnCount > 5 && chain.tools.total > 10) return Math.min(0.7, successRate);
  if (chain.turnCount > 2 && chain.tools.total > 3) return Math.min(0.5, successRate);
  return Math.min(0.3, successRate);
}

function scoreD2_ActionDensity(chain) {
  // Tool calls per 1000 output tokens
  const outputTokens = chain.tokens.output || 1;
  const density = (chain.tools.total / outputTokens) * 1000;

  // Sigmoid-like scoring: 0-1 calls/1000tok = 0, 5 = 0.5, 15+ = 1.0
  if (density <= 0) return 0;
  if (density >= 15) return 1.0;
  return Math.min(1.0, density / 15);
}

function scoreD3_InfoEfficiency(chain) {
  // Reads that led to actions vs total reads
  const totalReads = chain.files.read.size;
  if (totalReads === 0) {
    // No reads at all — either no file work, or writing blind
    const hasWrites = chain.files.written.size + chain.files.edited.size;
    return hasWrites > 0 ? 0.3 : 0.5; // writing without reading = somewhat wasteful
  }

  let actionableReads = 0;
  for (const [, entry] of chain.files.read) {
    if (entry.ledToAction) actionableReads++;
  }

  return actionableReads / totalReads;
}

function scoreD4_VerificationRate(chain) {
  const totalChanges = chain.files.written.size + chain.files.edited.size;
  if (totalChanges === 0) return 0.5; // no changes = neutral

  let verified = 0;
  for (const [, entry] of chain.files.written) {
    if (entry.verified) verified++;
  }
  for (const [, entry] of chain.files.edited) {
    if (entry.verified) verified++;
  }

  return verified / totalChanges;
}

function scoreD5_WasteRatio(chain) {
  const totalTokens = chain.tokens.output + chain.tokens.thinking;
  if (totalTokens === 0) return 0.5;

  // Waste = filler + error tokens (estimated) + retry overhead
  const wasteTokens = chain.tokens.filler + (chain.tools.errors * 50) + (chain.tools.retries * 100);

  return Math.max(0, 1 - (wasteTokens / totalTokens));
}

// ═════════════════════════════════════════════════════════════════════
// CAUSAL CHAIN ANALYSIS
// ═════════════════════════════════════════════════════════════════════

function analyzeCausalChain(chain) {
  // Build the work graph and identify productive vs dead branches
  let productiveTokens = 0;
  let deadTokens = 0;

  for (const turn of chain.turns) {
    const hasTools = turn.tools.length > 0;
    const hasActionableTools = turn.tools.some(t =>
      ["write_file", "edit_file"].includes(t.name) ||
      (t.name === "bash" && t.input?.command && !/^(echo|cat|ls|pwd)/.test(t.input.command))
    );

    if (hasActionableTools) {
      // This turn produced real work
      productiveTokens += turn.text + turn.thinking;
    } else if (hasTools) {
      // This turn had tools but only reads/exploration
      // Check if any reads led to future actions
      const readPaths = turn.tools
        .filter(t => t.name === "read_file")
        .map(t => t.input?.path)
        .filter(Boolean);

      const anyUseful = readPaths.some(p => chain.files.read.get(p)?.ledToAction);
      if (anyUseful) {
        productiveTokens += turn.text + turn.thinking;
      } else {
        deadTokens += turn.text + turn.thinking;
      }
    } else {
      // Pure text turn — productive if it's the final summary, dead if it's mid-session filler
      deadTokens += turn.filler;
      productiveTokens += (turn.text - turn.filler);
    }
  }

  const total = productiveTokens + deadTokens;
  return {
    productiveTokens,
    deadTokens,
    efficiency: total > 0 ? productiveTokens / total : 0,
  };
}

// ═════════════════════════════════════════════════════════════════════
// COMPOSITE UWT SCORE
// ═════════════════════════════════════════════════════════════════════

function computeUWT(chain) {
  const d1 = scoreD1_TaskCompletion(chain);
  const d2 = scoreD2_ActionDensity(chain);
  const d3 = scoreD3_InfoEfficiency(chain);
  const d4 = scoreD4_VerificationRate(chain);
  const d5 = scoreD5_WasteRatio(chain);

  // Weighted composite: D1 is most important (did you finish?)
  const weights = { d1: 3.0, d2: 2.0, d3: 2.0, d4: 1.5, d5: 1.5 };
  const totalWeight = Object.values(weights).reduce((a, b) => a + b, 0);
  const uwt = (d1 * weights.d1 + d2 * weights.d2 + d3 * weights.d3 + d4 * weights.d4 + d5 * weights.d5) / totalWeight;

  const causal = analyzeCausalChain(chain);

  return {
    uwt: Math.round(uwt * 1000) / 1000,
    dimensions: {
      d1_completion: Math.round(d1 * 100) / 100,
      d2_actionDensity: Math.round(d2 * 100) / 100,
      d3_infoEfficiency: Math.round(d3 * 100) / 100,
      d4_verificationRate: Math.round(d4 * 100) / 100,
      d5_wasteRatio: Math.round(d5 * 100) / 100,
    },
    causal: {
      productiveTokens: causal.productiveTokens,
      deadTokens: causal.deadTokens,
      chainEfficiency: Math.round(causal.efficiency * 100) / 100,
    },
    meta: {
      turns: chain.turnCount,
      tools: chain.tools.total,
      errors: chain.tools.errors,
      filesRead: chain.files.read.size,
      filesWritten: chain.files.written.size + chain.files.edited.size,
      task: chain.task,
      completed: chain.completed,
    },
  };
}

// ═════════════════════════════════════════════════════════════════════
// GRADE + DISPLAY
// ═════════════════════════════════════════════════════════════════════

function uwtGrade(score) {
  if (score >= 0.9) return { letter: "S", color: S.magenta, label: "Sovereign" };
  if (score >= 0.8) return { letter: "A", color: S.green, label: "Excellent" };
  if (score >= 0.6) return { letter: "B", color: S.green, label: "Good" };
  if (score >= 0.4) return { letter: "C", color: S.yellow, label: "Average" };
  if (score >= 0.2) return { letter: "D", color: S.red, label: "Poor" };
  return { letter: "F", color: S.red, label: "Failing" };
}

function dimBar(score, width = 20) {
  const filled = Math.round(score * width);
  const bar = "█".repeat(filled) + "░".repeat(width - filled);
  const color = score >= 0.8 ? S.green : score >= 0.5 ? S.yellow : S.red;
  return `${color}${bar}${S.reset} ${(score * 100).toFixed(0)}%`;
}

function displayUWT(result) {
  const grade = uwtGrade(result.uwt);
  const d = result.dimensions;

  console.log(`
${S.bold}${S.cyan}═══ UWT — Useful Work per Token ═══${S.reset}

${S.bold}Task:${S.reset} ${result.meta.task?.slice(0, 80)}
${S.dim}Turns: ${result.meta.turns} | Tools: ${result.meta.tools} | Errors: ${result.meta.errors} | Files R/W: ${result.meta.filesRead}/${result.meta.filesWritten}${S.reset}
${S.dim}Completed: ${result.meta.completed ? `${S.green}yes${S.reset}` : `${S.yellow}no${S.reset}`}${S.dim}${S.reset}

${S.bold}DIMENSIONS${S.reset}
  D1 Task Completion    ${dimBar(d.d1_completion)}  ${S.dim}(×3.0)${S.reset}
  D2 Action Density     ${dimBar(d.d2_actionDensity)}  ${S.dim}(×2.0)${S.reset}
  D3 Info Efficiency    ${dimBar(d.d3_infoEfficiency)}  ${S.dim}(×2.0)${S.reset}
  D4 Verification Rate  ${dimBar(d.d4_verificationRate)}  ${S.dim}(×1.5)${S.reset}
  D5 Waste Ratio        ${dimBar(d.d5_wasteRatio)}  ${S.dim}(×1.5)${S.reset}

${S.bold}CAUSAL CHAIN${S.reset}
  Productive: ${result.causal.productiveTokens.toLocaleString()} tokens
  Dead:       ${result.causal.deadTokens.toLocaleString()} tokens
  Efficiency: ${dimBar(result.causal.chainEfficiency)}

${S.bold}${grade.color}UWT SCORE: ${result.uwt.toFixed(3)} — ${grade.letter} (${grade.label})${S.reset}
`);
}

// ═════════════════════════════════════════════════════════════════════
// ANALYZE
// ═════════════════════════════════════════════════════════════════════

function analyze(statePath) {
  const chain = parseSession(statePath);
  if (!chain) {
    console.log(`${S.yellow}No session state found at ${statePath}${S.reset}`);
    return null;
  }

  const result = computeUWT(chain);
  displayUWT(result);

  // Save to history
  const history = loadHistory();
  result.timestamp = new Date().toISOString();
  history.push(result);
  saveHistory(history);

  return result;
}

// ═════════════════════════════════════════════════════════════════════
// BENCHMARK — Standardized tasks for consistent measurement
// ═════════════════════════════════════════════════════════════════════

function showBenchmark() {
  console.log(`
${S.bold}${S.cyan}═══ UWT Benchmark Suite ═══${S.reset}

Standardized tasks for comparing efficiency across sessions, models, and configurations.
Run each task with sovereign.mjs, then analyze with: node uwt.mjs analyze

${S.bold}TIER 1: Micro Tasks (1-5 turns expected)${S.reset}
  ${S.cyan}B1.1${S.reset} "Read package.json and list all dependencies"
       Expected: 1 read, 1 text response. Tests pure efficiency.

  ${S.cyan}B1.2${S.reset} "Find all TODO comments in this project"
       Expected: 1-2 greps, 1 text summary. Tests search efficiency.

  ${S.cyan}B1.3${S.reset} "Fix the syntax error in src/index.js"
       Expected: 1 read, 1 edit, 1 verify. Tests minimal edit loop.

${S.bold}TIER 2: Standard Tasks (5-15 turns expected)${S.reset}
  ${S.cyan}B2.1${S.reset} "Add input validation to all API endpoints in src/routes/"
       Expected: glob→read→edit×N→test. Tests systematic editing.

  ${S.cyan}B2.2${S.reset} "Write tests for src/utils/parser.js achieving >90% coverage"
       Expected: read→understand→write→run→iterate. Tests TDD loop.

  ${S.cyan}B2.3${S.reset} "Refactor the database module to use connection pooling"
       Expected: read→plan→edit→verify→test. Tests architectural change.

${S.bold}TIER 3: Complex Tasks (15-50 turns expected)${S.reset}
  ${S.cyan}B3.1${S.reset} "Build a REST API with CRUD for users, auth with JWT, and tests"
       Expected: full project scaffolding. Tests sustained execution.

  ${S.cyan}B3.2${S.reset} "Migrate the codebase from CommonJS to ESM"
       Expected: systematic multi-file transformation. Tests consistency.

${S.bold}Scoring${S.reset}
  Each benchmark has an expected turn range. Completing in fewer turns
  with high UWT = high efficiency. The benchmark suite lets you compare:
  - Model A vs Model B on the same task
  - YOUSPEAK ON vs OFF
  - Effort levels (low/medium/high/max)
  - Lazy loading ON vs OFF

${S.bold}Running a benchmark:${S.reset}
  node sovereign.mjs "B1.1: Read package.json and list all dependencies"
  node uwt.mjs analyze
  # Record the UWT score for comparison
`);
}

// ═════════════════════════════════════════════════════════════════════
// COMPARE
// ═════════════════════════════════════════════════════════════════════

function compare(file1, file2) {
  const chain1 = parseSession(resolve(file1));
  const chain2 = parseSession(resolve(file2));

  if (!chain1 || !chain2) {
    console.log(`${S.yellow}Need two valid state files to compare${S.reset}`);
    return;
  }

  const r1 = computeUWT(chain1);
  const r2 = computeUWT(chain2);

  console.log(`\n${S.bold}${S.cyan}═══ UWT Comparison ═══${S.reset}\n`);

  const dims = ["d1_completion", "d2_actionDensity", "d3_infoEfficiency", "d4_verificationRate", "d5_wasteRatio"];
  const labels = ["D1 Completion", "D2 Action Density", "D3 Info Efficiency", "D4 Verification", "D5 Waste Ratio"];

  console.log(`${"".padEnd(22)} ${S.bold}Session A${S.reset}   ${S.bold}Session B${S.reset}   ${S.bold}Δ${S.reset}`);
  console.log(`${"─".repeat(60)}`);

  for (let i = 0; i < dims.length; i++) {
    const a = r1.dimensions[dims[i]];
    const b = r2.dimensions[dims[i]];
    const delta = b - a;
    const deltaStr = delta > 0 ? `${S.green}+${(delta*100).toFixed(0)}%${S.reset}` :
                     delta < 0 ? `${S.red}${(delta*100).toFixed(0)}%${S.reset}` :
                     `${S.dim}  0%${S.reset}`;
    console.log(`  ${labels[i].padEnd(20)} ${(a*100).toFixed(0).padStart(5)}%    ${(b*100).toFixed(0).padStart(5)}%    ${deltaStr}`);
  }

  console.log(`${"─".repeat(60)}`);

  const uwtDelta = r2.uwt - r1.uwt;
  const uwtDeltaStr = uwtDelta > 0 ? `${S.green}+${uwtDelta.toFixed(3)}${S.reset}` :
                      uwtDelta < 0 ? `${S.red}${uwtDelta.toFixed(3)}${S.reset}` : `${S.dim} 0.000${S.reset}`;
  console.log(`  ${"UWT SCORE".padEnd(20)} ${r1.uwt.toFixed(3).padStart(5)}    ${r2.uwt.toFixed(3).padStart(5)}    ${uwtDeltaStr}`);
  console.log(`  ${"Chain Eff".padEnd(20)} ${(r1.causal.chainEfficiency*100).toFixed(0).padStart(4)}%    ${(r2.causal.chainEfficiency*100).toFixed(0).padStart(4)}%`);
  console.log();
}

// ═════════════════════════════════════════════════════════════════════
// HISTORY
// ═════════════════════════════════════════════════════════════════════

function loadHistory() {
  if (existsSync(HISTORY_FILE)) {
    try { return JSON.parse(readFileSync(HISTORY_FILE, "utf-8")); }
    catch { return []; }
  }
  return [];
}

function saveHistory(history) {
  writeFileSync(HISTORY_FILE, JSON.stringify(history, null, 2));
}

function showHistory() {
  const history = loadHistory();

  console.log(`\n${S.bold}${S.cyan}═══ UWT History ═══${S.reset}\n`);

  if (history.length === 0) {
    console.log(`${S.dim}No sessions analyzed yet. Run: node uwt.mjs analyze${S.reset}`);
    return;
  }

  console.log(`${"Date".padEnd(12)} ${"UWT".padStart(6)} ${"Grade".padEnd(5)} ${"D1".padStart(4)} ${"D2".padStart(4)} ${"D3".padStart(4)} ${"D4".padStart(4)} ${"D5".padStart(4)} ${"Chain".padStart(6)} Task`);
  console.log(`${"─".repeat(80)}`);

  for (const r of history.slice(-20)) {
    const grade = uwtGrade(r.uwt);
    const date = (r.timestamp || "").split("T")[0] || "?";
    const d = r.dimensions;
    const chain = r.causal?.chainEfficiency || 0;
    console.log(
      `${date.padEnd(12)} ${r.uwt.toFixed(3).padStart(6)} ${grade.color}${grade.letter}${S.reset}     ` +
      `${(d.d1_completion*100|0).toString().padStart(3)}% ${(d.d2_actionDensity*100|0).toString().padStart(3)}% ` +
      `${(d.d3_infoEfficiency*100|0).toString().padStart(3)}% ${(d.d4_verificationRate*100|0).toString().padStart(3)}% ` +
      `${(d.d5_wasteRatio*100|0).toString().padStart(3)}% ${(chain*100|0).toString().padStart(5)}% ` +
      `${S.dim}${(r.meta?.task || "?").slice(0, 30)}${S.reset}`
    );
  }

  // Trend
  if (history.length >= 3) {
    const recent = history.slice(-3);
    const older = history.slice(0, -3);
    if (older.length > 0) {
      const recentAvg = recent.reduce((s, r) => s + r.uwt, 0) / recent.length;
      const olderAvg = older.reduce((s, r) => s + r.uwt, 0) / older.length;
      const trend = recentAvg - olderAvg;
      const arrow = trend > 0.05 ? `${S.green}↑ improving${S.reset}` :
                    trend < -0.05 ? `${S.red}↓ regressing${S.reset}` :
                    `${S.dim}─ stable${S.reset}`;
      console.log(`\n  Trend: ${arrow} (${trend > 0 ? "+" : ""}${trend.toFixed(3)})`);
    }
  }
}

// ═════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════

const cmd = process.argv[2];
const args = process.argv.slice(3);

switch (cmd) {
  case "analyze": {
    let statePath = resolve(".sovereign-state.json");
    const stateIdx = args.indexOf("--state");
    if (stateIdx !== -1) statePath = resolve(args[stateIdx + 1]);
    analyze(statePath);
    break;
  }
  case "benchmark": showBenchmark(); break;
  case "compare": {
    if (args.length < 2) {
      console.log(`Usage: node uwt.mjs compare <state-file-1> <state-file-2>`);
    } else {
      compare(args[0], args[1]);
    }
    break;
  }
  case "history": showHistory(); break;
  case "dimensions": showDimensions(); break;
  default:
    console.log(`
${S.bold}uwt.mjs${S.reset} — Useful Work per Token

${S.dim}The fundamental benchmark for AI agent self-optimization.${S.reset}

Commands:
  ${S.cyan}analyze${S.reset}              Analyze current session (or --state FILE)
  ${S.cyan}benchmark${S.reset}            Show standardized benchmark tasks
  ${S.cyan}compare${S.reset} FILE1 FILE2  Compare two sessions side-by-side
  ${S.cyan}history${S.reset}              Show UWT trend over time
  ${S.cyan}dimensions${S.reset}           Explain the 5 measurement dimensions

Metric: UWT = weighted composite of 5 orthogonal dimensions:
  D1 Task Completion (×3), D2 Action Density (×2), D3 Info Efficiency (×2),
  D4 Verification Rate (×1.5), D5 Waste Ratio (×1.5)

Plus causal chain analysis: productive tokens vs dead branches.
`);
}
