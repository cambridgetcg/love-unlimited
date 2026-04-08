#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// YOUSPEAK KERNEL — The Sensory Organ of Kingdom Compute
//
// YOUSPEAK doesn't optimize. YOUSPEAK observes.
// The system optimizes itself based on what YOUSPEAK sees.
//
// Three functions:
//   SENSE  — Zero-cost metrics across 5 layers
//   DECIDE — Threshold-based adaptive signals (no LLM calls)
//   REPORT — Structured data for dashboards, audits, evolution
//
// Five measurement layers:
//   L1 OUTPUT   — Filler detection, useful token ratio
//   L2 THINKING — Thinking/output ratio, thinking efficiency
//   L3 ACTION   — Tool call patterns, redundancy, action density
//   L4 CONTEXT  — Window utilization, message growth, stale content
//   L5 SYSTEM   — Budget burn rate, session efficiency, cross-session trends
//
// Import:
//   import { createKernel } from './youspeak-kernel.mjs';
//   const ys = createKernel();
//
// ─────────────────────────────────────────────────────────────────────

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { join } from "path";
import { homedir } from "os";

// ═════════════════════════════════════════════════════════════════════
// L1 — OUTPUT: Filler Detection
// ═════════════════════════════════════════════════════════════════════

const FILLER_PATTERNS = [
  // Preamble
  { re: /\b(sure|okay|alright|great|absolutely|certainly|of course)[!.,]?\s/gi, name: "affirmative", w: 1 },
  { re: /\blet me (check|look|see|think|examine|analyze|review|investigate)\b/gi, name: "let-me", w: 2 },
  { re: /\bi('ll| will) (now |go ahead |proceed to |start by )/gi, name: "i-will-now", w: 2 },
  { re: /\bhere('s| is) (what|the|a summary|an overview)/gi, name: "here-is", w: 2 },
  { re: /\bfirst,? (i('ll| will)|let me|let's|we need to)/gi, name: "first-i-will", w: 2 },
  // Narration
  { re: /\bi('m| am) going to (read|check|look|search|examine|write|create|modify|update)\b/gi, name: "going-to", w: 3 },
  { re: /\bnow (i('ll| will)|let me|let's) (move on|proceed|continue|look|check)\b/gi, name: "now-proceed", w: 2 },
  { re: /\b(that|this) (looks|seems|appears) (good|correct|right|fine)\b/gi, name: "looks-good", w: 1 },
  // Padding
  { re: /\b(as (we|you) can see|as (mentioned|noted|shown) (above|earlier|before))\b/gi, name: "as-mentioned", w: 1 },
  { re: /\b(in (order|summary)|to (summarize|sum up|wrap up))\b/gi, name: "summary-phrase", w: 1 },
  { re: /\b(it('s| is) (worth|important to) (noting|mentioning|pointing out))\b/gi, name: "worth-noting", w: 1 },
  // Completion fluff
  { re: /\b(i('ve| have) (successfully|now|just|finished|completed))\b/gi, name: "completed-fluff", w: 1 },
  { re: /\b(the (changes|modifications|updates) (have been|are now) (made|applied|complete))\b/gi, name: "changes-applied", w: 1 },
  { re: /\bperfect[!.]?\s/gi, name: "perfect", w: 1 },
  { re: /\blooking at (this|the|your)/gi, name: "looking-at", w: 1 },
];

function detectFiller(text) {
  if (!text || text.length < 10) return { count: 0, tokens: 0, patterns: [], weight: 0 };
  const patterns = [];
  let totalWeight = 0;
  let fillerTokens = 0;

  for (const { re, name, w } of FILLER_PATTERNS) {
    re.lastIndex = 0;
    const matches = text.match(re);
    if (matches?.length) {
      patterns.push({ name, count: matches.length, weight: w });
      totalWeight += matches.length * w;
      fillerTokens += matches.length * 8; // ~8 tokens per filler phrase
    }
  }

  const totalTokens = Math.ceil(text.length / 4);
  return { count: patterns.reduce((s, p) => s + p.count, 0), tokens: fillerTokens, totalTokens, patterns, weight: totalWeight };
}

function gradeRatio(ratio) {
  if (ratio >= 0.98) return "S";  // Sovereign
  if (ratio >= 0.95) return "A";
  if (ratio >= 0.85) return "B";
  if (ratio >= 0.70) return "C";
  return "D";
}

// ═════════════════════════════════════════════════════════════════════
// KERNEL FACTORY
// ═════════════════════════════════════════════════════════════════════

export function createKernel(opts = {}) {
  const historyDir = opts.historyDir || join(homedir(), "Love", "memory", "youspeak");
  const historyFile = join(historyDir, "sessions.json");

  // ─── Accumulated session state ──────────────────────────
  const session = {
    startedAt: Date.now(),
    agent: opts.agent || "unknown",

    // L1 Output
    output: {
      totalTokens: 0,
      fillerTokens: 0,
      textBlocks: 0,
      grades: [],
    },

    // L2 Thinking
    thinking: {
      totalTokens: 0,
      perTurn: [],           // { thinking, output, ratio }
    },

    // L3 Action
    action: {
      toolCalls: 0,
      toolsByName: {},       // { bash: 5, read_file: 3, ... }
      readPaths: new Set(),  // paths read this session
      redundantReads: 0,     // same path read twice
      toolErrors: 0,
    },

    // L4 Context
    context: {
      messagesCount: 0,
      estimatedTokens: 0,   // rough estimate of context window usage
      systemPromptTokens: 0,
      oldestToolResultAge: 0, // turns since oldest uncompressed tool result
      pruneEvents: 0,
    },

    // L5 System
    system: {
      turns: 0,
      rateLimitHits: 0,
      modelSwitches: 0,
      effortChanges: 0,
      budgetAtStart: null,
      budgetNow: null,
    },

    // DECIDE signals emitted
    signals: [],
  };

  // ─── L1: SENSE OUTPUT ──────────────────────────────────
  function senseOutput(text) {
    if (!text?.trim()) return null;
    const filler = detectFiller(text);
    const useful = filler.totalTokens > 0
      ? Math.max(0, (filler.totalTokens - filler.tokens) / filler.totalTokens)
      : 1.0;
    const grade = gradeRatio(useful);

    session.output.totalTokens += filler.totalTokens;
    session.output.fillerTokens += filler.tokens;
    session.output.textBlocks++;
    session.output.grades.push(grade);

    return {
      fillerCount: filler.count,
      fillerTokens: filler.tokens,
      totalTokens: filler.totalTokens,
      usefulRatio: Math.round(useful * 100) / 100,
      grade,
      patterns: filler.patterns.slice(0, 5),
      weight: filler.weight,
    };
  }

  // ─── L2: SENSE THINKING ────────────────────────────────
  function senseThinking(usage) {
    const thinkTok = usage.thinking_tokens || 0;
    const outTok = usage.output_tokens || 0;
    session.thinking.totalTokens += thinkTok;

    const ratio = outTok > 0 ? +(thinkTok / outTok).toFixed(2) : 0;
    const entry = { thinking: thinkTok, output: outTok, ratio };
    session.thinking.perTurn.push(entry);

    return entry;
  }

  // ─── L3: SENSE ACTION ─────────────────────────────────
  function senseToolCall(name, input, result) {
    session.action.toolCalls++;
    session.action.toolsByName[name] = (session.action.toolsByName[name] || 0) + 1;

    let redundant = false;
    if (name === "read_file" && input?.path) {
      if (session.action.readPaths.has(input.path)) {
        session.action.redundantReads++;
        redundant = true;
      }
      session.action.readPaths.add(input.path);
    }

    // Detect errors
    if (typeof result === "string" && (result.startsWith("Error:") || result.startsWith("Exit 1"))) {
      session.action.toolErrors++;
    }

    return { name, redundant };
  }

  // ─── L4: SENSE CONTEXT ────────────────────────────────
  function senseContext(messages, systemPromptLength) {
    session.context.messagesCount = messages.length;
    session.context.systemPromptTokens = Math.ceil((systemPromptLength || 0) / 4);

    // Estimate total context tokens
    let est = session.context.systemPromptTokens;
    let oldestToolResult = 0;
    let turnIndex = 0;

    for (const msg of messages) {
      if (typeof msg.content === "string") {
        est += Math.ceil(msg.content.length / 4);
      } else if (Array.isArray(msg.content)) {
        for (const block of msg.content) {
          if (block.type === "text") est += Math.ceil((block.text?.length || 0) / 4);
          else if (block.type === "tool_use") est += Math.ceil(JSON.stringify(block.input || {}).length / 4) + 20;
          else if (block.type === "tool_result") {
            const len = typeof block.content === "string" ? block.content.length : JSON.stringify(block.content || "").length;
            est += Math.ceil(len / 4);
            if (oldestToolResult === 0) oldestToolResult = turnIndex;
          }
          else if (block.type === "thinking") est += Math.ceil((block.thinking?.length || 0) / 4);
        }
      }
      turnIndex++;
    }

    session.context.estimatedTokens = est;
    session.context.oldestToolResultAge = messages.length > 0 ? messages.length - oldestToolResult : 0;

    return {
      estimatedTokens: est,
      messagesCount: messages.length,
      systemPromptTokens: session.context.systemPromptTokens,
      windowUtilization: +(est / 1_000_000).toFixed(4), // fraction of 1M context
    };
  }

  // ─── L5: SENSE SYSTEM ─────────────────────────────────
  function senseTurn(budget) {
    session.system.turns++;
    if (budget) {
      if (!session.system.budgetAtStart) {
        session.system.budgetAtStart = {
          fiveHour: budget.fiveHour?.utilization || 0,
          sevenDay: budget.sevenDay?.utilization || 0,
        };
      }
      session.system.budgetNow = {
        fiveHour: budget.fiveHour?.utilization || 0,
        sevenDay: budget.sevenDay?.utilization || 0,
        isOverage: budget.isUsingOverage || false,
      };
    }
  }

  function senseRateLimit() {
    session.system.rateLimitHits++;
  }

  // ═══════════════════════════════════════════════════════
  // DECIDE — Threshold signals (no LLM calls)
  // ═══════════════════════════════════════════════════════

  function decide(currentEffort, currentModel, budget) {
    const signals = [];

    // ── Effort adjustment based on budget pressure ──
    const bu5 = budget?.fiveHour?.utilization || 0;
    if (bu5 > 0.85 && currentEffort === "max") {
      signals.push({ type: "effort", action: "reduce", from: "max", to: "high",
        reason: `Budget 5h at ${(bu5*100).toFixed(0)}% — reduce effort to extend runway` });
    }
    if (bu5 > 0.95 && (currentEffort === "max" || currentEffort === "high")) {
      signals.push({ type: "effort", action: "reduce", from: currentEffort, to: "medium",
        reason: `Budget 5h at ${(bu5*100).toFixed(0)}% — critical, reduce to medium` });
    }

    // ── Model suggestion based on thinking patterns ──
    if (session.thinking.perTurn.length >= 3) {
      const recent = session.thinking.perTurn.slice(-3);
      const avgRatio = recent.reduce((s, t) => s + t.ratio, 0) / recent.length;
      // If thinking ratio is very low, task might not need opus
      if (avgRatio < 0.5 && currentModel.includes("opus")) {
        signals.push({ type: "model", action: "suggest_downgrade", to: "sonnet",
          reason: `Low thinking/output ratio (${avgRatio.toFixed(1)}x) — sonnet may suffice` });
      }
    }

    // ── Context window pressure ──
    if (session.context.estimatedTokens > 800_000) {
      signals.push({ type: "context", action: "prune_recommended",
        reason: `Context at ~${Math.round(session.context.estimatedTokens/1000)}k tokens — approaching 1M limit`,
        utilization: session.context.estimatedTokens / 1_000_000 });
    }
    if (session.context.estimatedTokens > 500_000 && session.context.oldestToolResultAge > 20) {
      signals.push({ type: "context", action: "evict_old_results",
        reason: `${session.context.oldestToolResultAge} messages since oldest tool result — stale content accumulating` });
    }

    // ── Redundant tool calls ──
    if (session.action.redundantReads > 2) {
      signals.push({ type: "action", action: "redundant_reads",
        count: session.action.redundantReads,
        reason: `${session.action.redundantReads} redundant file reads — model re-reading known files` });
    }

    // ── Tool error rate ──
    if (session.action.toolCalls > 5 && session.action.toolErrors / session.action.toolCalls > 0.3) {
      signals.push({ type: "action", action: "high_error_rate",
        rate: +(session.action.toolErrors / session.action.toolCalls).toFixed(2),
        reason: `${session.action.toolErrors}/${session.action.toolCalls} tool calls errored — task may be confused` });
    }

    // ── Output filler trend ──
    if (session.output.grades.length >= 5) {
      const recent = session.output.grades.slice(-5);
      const badCount = recent.filter(g => g === "C" || g === "D").length;
      if (badCount >= 3) {
        signals.push({ type: "output", action: "filler_regression",
          reason: `${badCount}/5 recent text blocks grade C or D — YOUSPEAK discipline slipping` });
      }
    }

    // ── Rate limit frequency ──
    if (session.system.rateLimitHits >= 2 && session.system.turns > 0) {
      signals.push({ type: "system", action: "rate_limit_pressure",
        hits: session.system.rateLimitHits, turns: session.system.turns,
        reason: `${session.system.rateLimitHits} rate limits in ${session.system.turns} turns` });
    }

    session.signals = signals;
    return signals;
  }

  // ═══════════════════════════════════════════════════════
  // CONTEXT PRUNING — Zero-LLM-cost message compression
  // ═══════════════════════════════════════════════════════

  function pruneContext(messages, opts = {}) {
    const maxAge = opts.maxToolResultAge || 15;     // turns before truncation
    const keepChars = opts.keepChars || 200;        // chars to keep from old results
    const protectRecent = opts.protectRecent || 5;  // always protect last N messages

    if (messages.length <= protectRecent) return { messages, pruned: 0 };

    let pruned = 0;
    const cutoff = messages.length - protectRecent;

    for (let i = 0; i < cutoff; i++) {
      const msg = messages[i];
      if (!Array.isArray(msg.content)) continue;

      for (let j = 0; j < msg.content.length; j++) {
        const block = msg.content[j];
        if (block.type === "tool_result" && typeof block.content === "string" && block.content.length > keepChars + 50) {
          const original = block.content.length;
          block.content = block.content.slice(0, keepChars) + `\n... [pruned ${original - keepChars} chars by YOUSPEAK]`;
          pruned++;
        }
        // Also prune verbose thinking blocks from old turns
        if (block.type === "thinking" && typeof block.thinking === "string" && block.thinking.length > 500) {
          block.thinking = block.thinking.slice(0, 200) + "\n... [pruned by YOUSPEAK]";
          pruned++;
        }
      }
    }

    session.context.pruneEvents += pruned;
    return { messages, pruned };
  }

  // ═══════════════════════════════════════════════════════
  // REPORT — Structured metrics for dashboards
  // ═══════════════════════════════════════════════════════

  function report() {
    const elapsed = Date.now() - session.startedAt;
    const elapsedMin = +(elapsed / 60000).toFixed(1);

    // L1 Output
    const usefulRatio = session.output.totalTokens > 0
      ? (session.output.totalTokens - session.output.fillerTokens) / session.output.totalTokens
      : 1.0;
    const overallGrade = gradeRatio(usefulRatio);

    // L2 Thinking
    const avgThinkRatio = session.thinking.perTurn.length > 0
      ? session.thinking.perTurn.reduce((s, t) => s + t.ratio, 0) / session.thinking.perTurn.length
      : 0;
    const thinkingEfficiency = session.thinking.totalTokens > 0
      ? +(session.output.totalTokens / session.thinking.totalTokens).toFixed(3)
      : null;

    // L3 Action
    const actionDensity = session.output.textBlocks > 0
      ? +(session.action.toolCalls / session.output.textBlocks).toFixed(2)
      : 0;

    // L5 System
    const budgetBurned = session.system.budgetAtStart && session.system.budgetNow
      ? +((session.system.budgetNow.fiveHour - session.system.budgetAtStart.fiveHour) * 100).toFixed(1)
      : null;
    const tokensPerTurn = session.system.turns > 0
      ? Math.round((session.output.totalTokens + session.thinking.totalTokens) / session.system.turns)
      : 0;

    return {
      // Header
      agent: session.agent,
      elapsed: elapsedMin,
      turns: session.system.turns,

      // L1 Output
      output: {
        usefulRatio: Math.round(usefulRatio * 100) / 100,
        grade: overallGrade,
        totalTokens: session.output.totalTokens,
        fillerTokens: session.output.fillerTokens,
        textBlocks: session.output.textBlocks,
        gradeDistribution: countGrades(session.output.grades),
      },

      // L2 Thinking
      thinking: {
        totalTokens: session.thinking.totalTokens,
        avgRatio: +avgThinkRatio.toFixed(2),
        efficiency: thinkingEfficiency,
        turns: session.thinking.perTurn.length,
      },

      // L3 Action
      action: {
        totalCalls: session.action.toolCalls,
        byName: session.action.toolsByName,
        redundantReads: session.action.redundantReads,
        errors: session.action.toolErrors,
        density: actionDensity,
        uniqueFilesRead: session.action.readPaths.size,
      },

      // L4 Context
      context: {
        estimatedTokens: session.context.estimatedTokens,
        windowUtilization: +(session.context.estimatedTokens / 1_000_000).toFixed(4),
        messagesCount: session.context.messagesCount,
        systemPromptTokens: session.context.systemPromptTokens,
        pruneEvents: session.context.pruneEvents,
      },

      // L5 System
      system: {
        budgetBurned: budgetBurned !== null ? `${budgetBurned}%` : null,
        budgetNow: session.system.budgetNow,
        rateLimitHits: session.system.rateLimitHits,
        tokensPerTurn,
      },

      // Signals
      signals: session.signals,
    };
  }

  function countGrades(arr) {
    const d = {};
    arr.forEach(g => d[g] = (d[g] || 0) + 1);
    return d;
  }

  // ═══════════════════════════════════════════════════════
  // PERSIST — Save session to history for cross-session trends
  // ═══════════════════════════════════════════════════════

  function persist() {
    try {
      mkdirSync(historyDir, { recursive: true });
      let history = { sessions: [] };
      if (existsSync(historyFile)) {
        try { history = JSON.parse(readFileSync(historyFile, "utf-8")); } catch {}
      }

      const r = report();
      history.sessions.push({
        timestamp: new Date().toISOString(),
        agent: r.agent,
        elapsed: r.elapsed,
        turns: r.turns,
        outputGrade: r.output.grade,
        usefulRatio: r.output.usefulRatio,
        thinkingTokens: r.thinking.totalTokens,
        thinkAvgRatio: r.thinking.avgRatio,
        toolCalls: r.action.totalCalls,
        redundantReads: r.action.redundantReads,
        toolErrors: r.action.errors,
        contextPeakTokens: r.context.estimatedTokens,
        budgetBurned: r.system.budgetBurned,
        rateLimitHits: r.system.rateLimitHits,
        signalCount: r.signals.length,
      });

      // Keep last 200 sessions
      if (history.sessions.length > 200) {
        history.sessions = history.sessions.slice(-200);
      }

      writeFileSync(historyFile, JSON.stringify(history, null, 2));
      return true;
    } catch (e) {
      return false;
    }
  }

  // ═══════════════════════════════════════════════════════
  // TRENDS — Cross-session analysis from history
  // ═══════════════════════════════════════════════════════

  function trends(n = 20) {
    if (!existsSync(historyFile)) return null;
    try {
      const history = JSON.parse(readFileSync(historyFile, "utf-8"));
      const recent = history.sessions.slice(-n);
      if (recent.length < 2) return null;

      const avg = (arr, key) => arr.reduce((s, e) => s + (e[key] || 0), 0) / arr.length;
      const mid = Math.floor(recent.length / 2);
      const early = recent.slice(0, mid);
      const late = recent.slice(mid);

      return {
        sessions: recent.length,
        span: `${recent[0].timestamp?.split("T")[0]} → ${recent[recent.length-1].timestamp?.split("T")[0]}`,
        avgUsefulRatio: +avg(recent, "usefulRatio").toFixed(2),
        avgThinkRatio: +avg(recent, "thinkAvgRatio").toFixed(2),
        avgRedundantReads: +avg(recent, "redundantReads").toFixed(1),
        avgToolErrors: +avg(recent, "toolErrors").toFixed(1),
        fillerTrend: {
          early: +avg(early, "usefulRatio").toFixed(2),
          late: +avg(late, "usefulRatio").toFixed(2),
          direction: avg(late, "usefulRatio") > avg(early, "usefulRatio") ? "improving" : "regressing",
        },
        rateLimitTrend: {
          early: +avg(early, "rateLimitHits").toFixed(1),
          late: +avg(late, "rateLimitHits").toFixed(1),
        },
      };
    } catch { return null; }
  }

  // ─── COMPACT STATUS LINE ───────────────────────────────
  function statusLine() {
    const r = report();
    const g = r.output.grade;
    const ur = Math.round(r.output.usefulRatio * 100);
    const tr = r.thinking.avgRatio;
    const tc = r.action.totalCalls;
    const rr = r.action.redundantReads;
    const ctx = Math.round(r.context.estimatedTokens / 1000);
    const sig = r.signals.length;
    return `YS:${g}(${ur}%) think:${tr}x tools:${tc}${rr > 0 ? ` dup:${rr}` : ""} ctx:${ctx}k${sig > 0 ? ` ⚡${sig}` : ""}`;
  }

  // ─── PUBLIC API ────────────────────────────────────────
  return {
    // SENSE
    senseOutput,
    senseThinking,
    senseToolCall,
    senseContext,
    senseTurn,
    senseRateLimit,
    // DECIDE
    decide,
    // CONTEXT
    pruneContext,
    // REPORT
    report,
    statusLine,
    trends,
    // PERSIST
    persist,
    // Direct access
    session,
  };
}

// ═════════════════════════════════════════════════════════════════════
// CLI — Direct invocation for status/trends
// ═════════════════════════════════════════════════════════════════════

if (process.argv[1]?.endsWith("youspeak-kernel.mjs")) {
  const cmd = process.argv[2];
  const S = { reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
    red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
    cyan: "\x1b[36m", magenta: "\x1b[35m" };

  if (cmd === "trends" || cmd === "history") {
    const ys = createKernel();
    const t = ys.trends(parseInt(process.argv[3]) || 20);
    if (!t) {
      console.log(`${S.dim}No session history yet.${S.reset}`);
      process.exit(0);
    }

    console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK TRENDS ═══${S.reset}`);
    console.log(`${S.dim}${t.span} (${t.sessions} sessions)${S.reset}\n`);
    console.log(`  Useful ratio:     ${S.bold}${t.avgUsefulRatio >= 0.9 ? S.green : t.avgUsefulRatio >= 0.8 ? S.yellow : S.red}${Math.round(t.avgUsefulRatio*100)}%${S.reset}`);
    console.log(`  Think/output:     ${t.avgThinkRatio}x avg`);
    console.log(`  Redundant reads:  ${t.avgRedundantReads}/session avg`);
    console.log(`  Tool errors:      ${t.avgToolErrors}/session avg`);
    console.log();
    const dir = t.fillerTrend.direction === "improving" ? S.green + "↑ improving" : S.red + "↓ regressing";
    console.log(`  Filler trend:     early ${Math.round(t.fillerTrend.early*100)}% → late ${Math.round(t.fillerTrend.late*100)}% ${dir}${S.reset}`);
    console.log(`  Rate limits:      early ${t.rateLimitTrend.early}/session → late ${t.rateLimitTrend.late}/session`);
    console.log();

  } else {
    console.log(`
${S.bold}YOUSPEAK KERNEL${S.reset} — The Sensory Organ of Kingdom Compute

Usage:
  ${S.cyan}node youspeak-kernel.mjs trends${S.reset}     Show cross-session trends
  ${S.cyan}node youspeak-kernel.mjs trends 50${S.reset}  Show trends for last 50 sessions

Import:
  ${S.cyan}import { createKernel } from './youspeak-kernel.mjs';${S.reset}
  ${S.cyan}const ys = createKernel();${S.reset}

Five measurement layers:
  L1 OUTPUT   — Filler detection, useful token ratio
  L2 THINKING — Thinking/output ratio, thinking efficiency
  L3 ACTION   — Tool call patterns, redundancy, action density
  L4 CONTEXT  — Window utilization, message growth, stale content
  L5 SYSTEM   — Budget burn rate, session efficiency
`);
  }
}
