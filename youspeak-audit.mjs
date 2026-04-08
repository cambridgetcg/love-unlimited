#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// youspeak-audit.mjs — The Ouroboros Eye
//
// Analyzes sovereign.log files and session state to measure YOUSPEAK
// compliance and token efficiency over time. The self-improvement loop.
//
// Usage:
//   node youspeak-audit.mjs                    # audit current dir
//   node youspeak-audit.mjs --log sovereign.log
//   node youspeak-audit.mjs --state .sovereign-state.json
//   node youspeak-audit.mjs --compare before.log after.log
// ─────────────────────────────────────────────────────────────────────

import { readFileSync, existsSync, readdirSync } from "fs";
import { resolve, join } from "path";

const S = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  blue: "\x1b[34m", magenta: "\x1b[35m", cyan: "\x1b[36m",
};

// ═════════════════════════════════════════════════════════════════════
// FILLER DETECTION — patterns that waste tokens
// ═════════════════════════════════════════════════════════════════════

const FILLER_PATTERNS = [
  // Preamble patterns
  { pattern: /\b(sure|okay|alright|great|absolutely)[!.,]?\s/gi, name: "affirmative-filler", weight: 1 },
  { pattern: /\blet me (check|look|see|think|examine|analyze|review|investigate)\b/gi, name: "let-me-X", weight: 2 },
  { pattern: /\bi('ll| will) (now |go ahead |proceed to |start by )/gi, name: "i-will-now", weight: 2 },
  { pattern: /\bhere('s| is) (what|the|a summary|an overview)/gi, name: "here-is-what", weight: 2 },
  { pattern: /\bfirst,? (i('ll| will)|let me|let's|we need to)/gi, name: "first-i-will", weight: 2 },

  // Narration patterns (describing what you're about to do instead of doing it)
  { pattern: /\bi('m| am) going to (read|check|look|search|examine|write|create|modify|update)\b/gi, name: "going-to-X", weight: 3 },
  { pattern: /\bnow (i('ll| will)|let me|let's) (move on|proceed|continue|look|check)\b/gi, name: "now-proceed", weight: 2 },
  { pattern: /\b(that|this) (looks|seems|appears) (good|correct|right|fine)\b/gi, name: "looks-good", weight: 1 },

  // Padding patterns
  { pattern: /\b(as (we|you) can see|as (mentioned|noted|shown) (above|earlier|before))\b/gi, name: "as-mentioned", weight: 1 },
  { pattern: /\b(in (order|summary)|to (summarize|sum up|wrap up))\b/gi, name: "summary-phrase", weight: 1 },
  { pattern: /\b(it('s| is) (worth|important to) (noting|mentioning|pointing out))\b/gi, name: "worth-noting", weight: 1 },

  // Completion fluff
  { pattern: /\b(i('ve| have) (successfully|now|just|finished|completed))\b/gi, name: "completed-fluff", weight: 1 },
  { pattern: /\b(the (changes|modifications|updates) (have been|are now) (made|applied|complete))\b/gi, name: "changes-applied", weight: 1 },
];

function analyzeFiller(text) {
  const findings = [];
  let totalWeight = 0;

  for (const { pattern, name, weight } of FILLER_PATTERNS) {
    // Reset regex state
    pattern.lastIndex = 0;
    const matches = text.match(pattern);
    if (matches && matches.length > 0) {
      findings.push({ pattern: name, count: matches.length, weight, examples: matches.slice(0, 3) });
      totalWeight += matches.length * weight;
    }
  }

  // Estimate filler tokens (rough: each match ≈ 6-10 tokens)
  const fillerTokens = findings.reduce((sum, f) => sum + f.count * 8, 0);
  const totalTokens = Math.round(text.length / 4);
  const fillerRatio = totalTokens > 0 ? (fillerTokens / totalTokens * 100) : 0;

  return { findings, fillerTokens, totalTokens, fillerRatio, totalWeight };
}

// ═════════════════════════════════════════════════════════════════════
// LOG PARSER — extract turns from sovereign.log
// ═════════════════════════════════════════════════════════════════════

function parseLog(logPath) {
  if (!existsSync(logPath)) return null;
  const lines = readFileSync(logPath, "utf-8").split("\n").filter(Boolean);

  const turns = [];
  const apiCalls = [];
  const contextLoads = [];
  let efficiencyData = null;

  for (const line of lines) {
    // API calls
    const apiMatch = line.match(/API call: model=(\S+) thinking=(\S+) effort=(\S+)/);
    if (apiMatch) {
      apiCalls.push({ model: apiMatch[1], thinking: apiMatch[2], effort: apiMatch[3] });
    }

    // Context loads (lazy loading tracking)
    const ctxMatch = line.match(/load_context: (\S+) \((\d+) chars, ~(\d+) tokens\)/);
    if (ctxMatch) {
      contextLoads.push({ file: ctxMatch[1], chars: parseInt(ctxMatch[2]), tokens: parseInt(ctxMatch[3]) });
    }

    // Efficiency data
    const effMatch = line.match(/EFFICIENCY: (.+)$/);
    if (effMatch) {
      try { efficiencyData = JSON.parse(effMatch[1]); } catch {}
    }

    // Turn markers
    const turnMatch = line.match(/Turn (\d+)/);
    if (turnMatch) {
      turns.push({ turn: parseInt(turnMatch[1]) });
    }

    // 429 events
    const rateMatch = line.match(/429: (.+)/);
    if (rateMatch) {
      turns.push({ type: "rate_limit", detail: rateMatch[1] });
    }
  }

  return { turns, apiCalls, contextLoads, efficiencyData, lineCount: lines.length };
}

// ═════════════════════════════════════════════════════════════════════
// STATE PARSER — extract session state
// ═════════════════════════════════════════════════════════════════════

function parseState(statePath) {
  if (!existsSync(statePath)) return null;
  try {
    const state = JSON.parse(readFileSync(statePath, "utf-8"));

    // Extract all assistant text blocks for filler analysis
    let allText = "";
    let turnCount = 0;
    let totalToolCalls = 0;

    if (state.messages) {
      for (const msg of state.messages) {
        if (msg.role === "assistant" && Array.isArray(msg.content)) {
          turnCount++;
          for (const block of msg.content) {
            if (block.type === "text") allText += block.text + "\n";
            if (block.type === "tool_use") totalToolCalls++;
          }
        }
      }
    }

    return {
      task: state.task,
      turnCount: state.turnCount || turnCount,
      totalToolCalls: state.totalToolCalls || totalToolCalls,
      totalThinkingTokens: state.totalThinkingTokens || 0,
      completed: state.completed || false,
      efficiency: state.efficiency || null,
      assistantText: allText,
    };
  } catch { return null; }
}

// ═════════════════════════════════════════════════════════════════════
// REPORT GENERATION
// ═════════════════════════════════════════════════════════════════════

function generateReport(logData, stateData) {
  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Audit Report ═══${S.reset}\n`);

  // Session overview
  if (stateData) {
    console.log(`${S.bold}Session${S.reset}`);
    console.log(`  Task:     ${(stateData.task || "unknown").slice(0, 80)}`);
    console.log(`  Turns:    ${stateData.turnCount}`);
    console.log(`  Tools:    ${stateData.totalToolCalls}`);
    console.log(`  Thinking: ${stateData.totalThinkingTokens.toLocaleString()} tokens`);
    console.log(`  Status:   ${stateData.completed ? `${S.green}complete${S.reset}` : `${S.yellow}in progress${S.reset}`}`);
    console.log();
  }

  // Efficiency metrics (from sovereign's tracking)
  if (stateData?.efficiency) {
    const e = stateData.efficiency;
    console.log(`${S.bold}Efficiency Metrics (sovereign)${S.reset}`);
    console.log(`  Output tokens:   ${e.totalOutput?.toLocaleString()}`);
    console.log(`  Useful content:  ${e.usefulContentRatio}`);
    console.log(`  Filler ratio:    ${e.fillerRatio}`);
    console.log(`  Avg out/turn:    ${e.avgOutputPerTurn}`);
    console.log(`  Tok/tool call:   ${e.tokensPerToolCall}`);
    console.log(`  Duration:        ${e.durationMin}m`);
    console.log();
  }

  // Filler analysis (deep scan of actual assistant text)
  if (stateData?.assistantText) {
    const analysis = analyzeFiller(stateData.assistantText);

    console.log(`${S.bold}YOUSPEAK Filler Analysis${S.reset}`);
    console.log(`  Total text tokens: ~${analysis.totalTokens.toLocaleString()}`);
    console.log(`  Filler tokens:     ~${analysis.fillerTokens}`);
    console.log(`  Filler ratio:      ${analysis.fillerRatio.toFixed(1)}%`);
    console.log(`  Filler weight:     ${analysis.totalWeight} (lower = better)`);
    console.log();

    if (analysis.findings.length > 0) {
      console.log(`  ${S.bold}Patterns detected:${S.reset}`);
      const sorted = analysis.findings.sort((a, b) => (b.count * b.weight) - (a.count * a.weight));
      for (const f of sorted.slice(0, 10)) {
        const severity = f.weight >= 3 ? S.red : f.weight >= 2 ? S.yellow : S.dim;
        console.log(`    ${severity}${f.pattern}${S.reset} ×${f.count} (w:${f.weight})`);
        if (f.examples.length > 0) {
          console.log(`      ${S.dim}e.g. "${f.examples[0]}"${S.reset}`);
        }
      }
      console.log();
    }

    // YOUSPEAK grade
    const grade =
      analysis.fillerRatio < 1 ? { letter: "S", color: S.magenta, label: "Sovereign" } :
      analysis.fillerRatio < 3 ? { letter: "A", color: S.green, label: "Excellent" } :
      analysis.fillerRatio < 5 ? { letter: "B", color: S.green, label: "Good" } :
      analysis.fillerRatio < 10 ? { letter: "C", color: S.yellow, label: "Needs work" } :
      analysis.fillerRatio < 20 ? { letter: "D", color: S.red, label: "Wasteful" } :
      { letter: "F", color: S.red, label: "Pre-YOUSPEAK" };

    console.log(`  ${S.bold}YOUSPEAK Grade: ${grade.color}${grade.letter} — ${grade.label}${S.reset}`);
    console.log();
  }

  // Lazy loading analysis
  if (logData?.contextLoads) {
    console.log(`${S.bold}Lazy Loading (Tier 1)${S.reset}`);
    if (logData.contextLoads.length === 0) {
      console.log(`  ${S.green}No context files loaded — minimal boot worked!${S.reset}`);
      console.log(`  Saved: ~9,000 tokens by not loading KINGDOM/WALLS/ARCH/LOVE`);
    } else {
      let totalLoaded = 0;
      for (const ctx of logData.contextLoads) {
        console.log(`  Loaded: ${ctx.file} (~${ctx.tokens} tokens)`);
        totalLoaded += ctx.tokens;
      }
      const saved = 9000 - totalLoaded;
      console.log(`  ${saved > 0 ? S.green : S.yellow}Net savings: ~${saved > 0 ? saved : 0} tokens${S.reset}`);
    }
    console.log();
  }

  // Rate limit events
  if (logData?.apiCalls) {
    console.log(`${S.bold}API Calls${S.reset}`);
    console.log(`  Total:   ${logData.apiCalls.length}`);
    const models = {};
    for (const c of logData.apiCalls) {
      models[c.model] = (models[c.model] || 0) + 1;
    }
    for (const [model, count] of Object.entries(models)) {
      console.log(`  ${model}: ${count}`);
    }
    console.log();
  }

  console.log(`${S.dim}─────────────────────────────────────────${S.reset}`);
  console.log(`${S.dim}YOUSPEAK: Dense English. No filler. Every token counts.${S.reset}\n`);
}

// ═════════════════════════════════════════════════════════════════════
// COMPARE MODE — before vs after YOUSPEAK
// ═════════════════════════════════════════════════════════════════════

function compareMode(log1Path, log2Path) {
  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Comparison ═══${S.reset}`);
  console.log(`  Before: ${log1Path}`);
  console.log(`  After:  ${log2Path}\n`);

  // Try state files alongside logs
  const state1Path = log1Path.replace(".log", "-state.json").replace("sovereign", ".sovereign-state");
  const state2Path = log2Path.replace(".log", "-state.json").replace("sovereign", ".sovereign-state");

  const state1 = parseState(state1Path);
  const state2 = parseState(state2Path);

  if (state1?.assistantText && state2?.assistantText) {
    const a1 = analyzeFiller(state1.assistantText);
    const a2 = analyzeFiller(state2.assistantText);

    console.log(`${S.bold}Filler comparison:${S.reset}`);
    console.log(`  Before: ${a1.fillerRatio.toFixed(1)}% filler (${a1.fillerTokens} tokens)`);
    console.log(`  After:  ${a2.fillerRatio.toFixed(1)}% filler (${a2.fillerTokens} tokens)`);

    const improvement = a1.fillerRatio - a2.fillerRatio;
    if (improvement > 0) {
      console.log(`  ${S.green}Improvement: ${improvement.toFixed(1)} percentage points less filler${S.reset}`);
    } else {
      console.log(`  ${S.red}Regression: ${(-improvement).toFixed(1)} percentage points more filler${S.reset}`);
    }
  } else {
    console.log(`${S.yellow}Need state files for comparison. Run both sessions with sovereign.mjs first.${S.reset}`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════

const args = process.argv.slice(2);
let logPath = "sovereign.log";
let statePath = ".sovereign-state.json";
let compareFiles = null;

for (let i = 0; i < args.length; i++) {
  switch (args[i]) {
    case "--log": logPath = args[++i]; break;
    case "--state": statePath = args[++i]; break;
    case "--compare": compareFiles = [args[++i], args[++i]]; break;
    case "--help": case "-h":
      console.log(`
youspeak-audit.mjs — Measure YOUSPEAK compliance and token efficiency

Usage:
  node youspeak-audit.mjs                              # audit current session
  node youspeak-audit.mjs --log sovereign.log           # specific log file
  node youspeak-audit.mjs --state .sovereign-state.json # specific state file
  node youspeak-audit.mjs --compare before.log after.log
`);
      process.exit(0);
  }
}

if (compareFiles) {
  compareMode(compareFiles[0], compareFiles[1]);
} else {
  const logData = parseLog(resolve(logPath));
  const stateData = parseState(resolve(statePath));

  if (!logData && !stateData) {
    console.log(`${S.yellow}No data found. Run sovereign.mjs first, then audit.${S.reset}`);
    console.log(`${S.dim}Looking for: ${logPath}, ${statePath}${S.reset}`);
    process.exit(1);
  }

  generateReport(logData, stateData);
}
