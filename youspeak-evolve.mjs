#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// youspeak-evolve.mjs — The Complete Ouroboros
//
// LIVE → SENSE → REFLECT → DISTILL → TRANSMUTE → INTEGRATE → LIVE
//
// Reads efficiency history across sessions. Distills patterns.
// Proposes system prompt mutations. Applies them (with Yu's approval).
// The system that improves itself through itself.
//
// Usage:
//   node youspeak-evolve.mjs sense               # Record current session metrics
//   node youspeak-evolve.mjs reflect              # Show trends across sessions
//   node youspeak-evolve.mjs distill              # Extract actionable insights
//   node youspeak-evolve.mjs transmute            # Generate system prompt mutations
//   node youspeak-evolve.mjs integrate [--apply]  # Apply mutations (--apply to write)
//   node youspeak-evolve.mjs cycle                # Full cycle: sense → reflect → distill
//   node youspeak-evolve.mjs history              # Show full evolution history
// ─────────────────────────────────────────────────────────────────────

import { readFileSync, writeFileSync, existsSync, readdirSync } from "fs";
import { resolve, join, basename } from "path";
import { homedir } from "os";

const S = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  blue: "\x1b[34m", magenta: "\x1b[35m", cyan: "\x1b[36m",
};

const HISTORY_FILE = resolve("youspeak-history.json");
const LOVE_DIR = join(homedir(), "Love");

// ═════════════════════════════════════════════════════════════════════
// HISTORY — Accumulated wisdom across sessions
// ═════════════════════════════════════════════════════════════════════

function loadHistory() {
  if (existsSync(HISTORY_FILE)) {
    try { return JSON.parse(readFileSync(HISTORY_FILE, "utf-8")); }
    catch { return { sessions: [], mutations: [], version: 1 }; }
  }
  return { sessions: [], mutations: [], version: 1 };
}

function saveHistory(history) {
  writeFileSync(HISTORY_FILE, JSON.stringify(history, null, 2));
}

// ═════════════════════════════════════════════════════════════════════
// SENSE — Record current session metrics into history
// ═════════════════════════════════════════════════════════════════════

function sense() {
  const stateFile = resolve(".sovereign-state.json");
  const logFile = resolve("sovereign.log");

  if (!existsSync(stateFile)) {
    console.log(`${S.yellow}No session state found. Run sovereign.mjs first.${S.reset}`);
    return null;
  }

  const state = JSON.parse(readFileSync(stateFile, "utf-8"));
  const history = loadHistory();

  // Extract key metrics
  const entry = {
    timestamp: new Date().toISOString(),
    task: (state.task || "unknown").slice(0, 100),
    turns: state.turnCount || 0,
    toolCalls: state.totalToolCalls || 0,
    thinkingTokens: state.totalThinkingTokens || 0,
    completed: state.completed || false,
    efficiency: state.efficiency || null,
    uwt: null,
  };

  // Try to read UWT score if uwt-history.json exists
  try {
    const uwtHistFile = resolve("uwt-history.json");
    if (existsSync(uwtHistFile)) {
      const uwtHist = JSON.parse(readFileSync(uwtHistFile, "utf-8"));
      if (uwtHist.length > 0) {
        entry.uwt = uwtHist[uwtHist.length - 1].uwt;
      }
    }
  } catch {}

  // Parse log for additional data
  if (existsSync(logFile)) {
    const log = readFileSync(logFile, "utf-8");
    const contextLoads = [...log.matchAll(/load_context: (\S+)/g)].map(m => m[1]);
    const rateLimits = [...log.matchAll(/429:/g)].length;
    const promptSize = log.match(/System prompt: (\d+) chars/)?.[1];

    entry.contextFilesLoaded = contextLoads;
    entry.rateLimitHits = rateLimits;
    entry.promptChars = parseInt(promptSize) || 0;
    entry.promptTokens = Math.round(entry.promptChars / 4);
  }

  // Deduplicate — don't record the same session twice
  const isDuplicate = history.sessions.some(s =>
    s.task === entry.task && s.turns === entry.turns && s.completed === entry.completed
  );

  if (!isDuplicate) {
    history.sessions.push(entry);
    saveHistory(history);
    console.log(`${S.green}✓ Session recorded${S.reset} (${history.sessions.length} total)`);
  } else {
    console.log(`${S.dim}Session already recorded, skipping${S.reset}`);
  }

  return entry;
}

// ═════════════════════════════════════════════════════════════════════
// REFLECT — Analyze trends across session history
// ═════════════════════════════════════════════════════════════════════

function reflect() {
  const history = loadHistory();
  const sessions = history.sessions;

  if (sessions.length === 0) {
    console.log(`${S.yellow}No sessions recorded yet. Run: node youspeak-evolve.mjs sense${S.reset}`);
    return null;
  }

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Evolution — Reflect ═══${S.reset}`);
  console.log(`${S.dim}Sessions: ${sessions.length} | Span: ${sessions[0]?.timestamp?.split("T")[0]} → ${sessions[sessions.length-1]?.timestamp?.split("T")[0]}${S.reset}\n`);

  // Aggregate metrics
  const withEff = sessions.filter(s => s.efficiency);
  if (withEff.length > 0) {
    const avgUseful = withEff.reduce((s, e) => s + parseFloat(e.efficiency.usefulContentRatio || "0"), 0) / withEff.length;
    const avgFiller = withEff.reduce((s, e) => s + parseFloat(e.efficiency.fillerRatio || "0"), 0) / withEff.length;
    const avgOutPerTurn = withEff.reduce((s, e) => s + (e.efficiency.avgOutputPerTurn || 0), 0) / withEff.length;
    const avgToolsPerTurn = withEff.reduce((s, e) => s + parseFloat(e.efficiency.avgToolsPerTurn || "0"), 0) / withEff.length;

    console.log(`${S.bold}Averages (${withEff.length} measured sessions)${S.reset}`);
    console.log(`  Useful content:  ${avgUseful.toFixed(0)}%`);
    console.log(`  Filler ratio:    ${avgFiller.toFixed(1)}%`);
    console.log(`  Avg out/turn:    ${avgOutPerTurn.toFixed(0)} tokens`);
    console.log(`  Avg tools/turn:  ${avgToolsPerTurn.toFixed(1)}`);
    console.log();

    // Trend analysis (first half vs second half)
    if (withEff.length >= 4) {
      const mid = Math.floor(withEff.length / 2);
      const early = withEff.slice(0, mid);
      const late = withEff.slice(mid);

      const earlyFiller = early.reduce((s, e) => s + parseFloat(e.efficiency.fillerRatio || "0"), 0) / early.length;
      const lateFiller = late.reduce((s, e) => s + parseFloat(e.efficiency.fillerRatio || "0"), 0) / late.length;

      const trend = lateFiller - earlyFiller;
      const trendLabel = trend < -1 ? `${S.green}improving ↓${S.reset}` :
                         trend > 1 ? `${S.red}regressing ↑${S.reset}` :
                         `${S.dim}stable ─${S.reset}`;

      console.log(`${S.bold}Trend${S.reset}`);
      console.log(`  Early sessions filler: ${earlyFiller.toFixed(1)}%`);
      console.log(`  Late sessions filler:  ${lateFiller.toFixed(1)}%`);
      console.log(`  Direction: ${trendLabel} (${trend > 0 ? "+" : ""}${trend.toFixed(1)}pp)`);
      console.log();
    }
  }

  // Lazy loading effectiveness
  const withCtx = sessions.filter(s => s.contextFilesLoaded);
  if (withCtx.length > 0) {
    const loadFreq = {};
    for (const s of withCtx) {
      for (const f of s.contextFilesLoaded) {
        loadFreq[f] = (loadFreq[f] || 0) + 1;
      }
    }

    console.log(`${S.bold}Context Loading Patterns${S.reset}`);
    if (Object.keys(loadFreq).length === 0) {
      console.log(`  ${S.green}No context files loaded — lazy loading fully effective${S.reset}`);
    } else {
      for (const [file, count] of Object.entries(loadFreq).sort((a, b) => b[1] - a[1])) {
        const pct = (count / withCtx.length * 100).toFixed(0);
        console.log(`  ${file}: loaded in ${count}/${withCtx.length} sessions (${pct}%)`);
      }
    }
    console.log();
  }

  // Rate limit analysis
  const withRL = sessions.filter(s => s.rateLimitHits > 0);
  if (withRL.length > 0) {
    console.log(`${S.bold}Rate Limits${S.reset}`);
    console.log(`  Sessions with 429s: ${withRL.length}/${sessions.length}`);
    const totalRL = withRL.reduce((s, e) => s + e.rateLimitHits, 0);
    console.log(`  Total 429 events: ${totalRL}`);
    console.log();
  }

  return { sessions, withEff };
}

// ═════════════════════════════════════════════════════════════════════
// DISTILL — Extract actionable insights from reflection
// ═════════════════════════════════════════════════════════════════════

function distill() {
  const history = loadHistory();
  const sessions = history.sessions;
  const withEff = sessions.filter(s => s.efficiency);

  if (withEff.length === 0) {
    console.log(`${S.yellow}Need sessions with efficiency data. Run sovereign.mjs with --track-efficiency${S.reset}`);
    return [];
  }

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Evolution — Distill ═══${S.reset}\n`);

  const insights = [];

  // Insight 1: Filler ratio above target
  const avgFiller = withEff.reduce((s, e) => s + parseFloat(e.efficiency.fillerRatio || "0"), 0) / withEff.length;
  if (avgFiller > 5) {
    insights.push({
      type: "filler_high",
      severity: avgFiller > 15 ? "critical" : avgFiller > 10 ? "high" : "medium",
      message: `Average filler ratio is ${avgFiller.toFixed(1)}% — above 5% target`,
      action: "Strengthen YOUSPEAK protocol in system prompt. Add explicit anti-filler examples.",
    });
  }

  // Insight 2: Useful content below 80%
  const avgUseful = withEff.reduce((s, e) => s + parseFloat(e.efficiency.usefulContentRatio || "0"), 0) / withEff.length;
  if (avgUseful < 80) {
    insights.push({
      type: "useful_low",
      severity: avgUseful < 60 ? "critical" : avgUseful < 70 ? "high" : "medium",
      message: `Average useful content ratio is ${avgUseful.toFixed(0)}% — below 80% target`,
      action: "Review common waste patterns. Consider effort reduction for evaluation turns.",
    });
  }

  // Insight 3: Context files loaded too often (lazy loading not working)
  const loadFreq = {};
  const withCtx = sessions.filter(s => s.contextFilesLoaded);
  for (const s of withCtx) {
    for (const f of s.contextFilesLoaded) {
      loadFreq[f] = (loadFreq[f] || 0) + 1;
    }
  }
  for (const [file, count] of Object.entries(loadFreq)) {
    const pct = withCtx.length > 0 ? count / withCtx.length * 100 : 0;
    if (pct > 80) {
      insights.push({
        type: "context_always_loaded",
        severity: "medium",
        message: `${file} loaded in ${pct.toFixed(0)}% of sessions — might as well be in boot`,
        action: `Consider moving ${file} back to boot files if always needed.`,
      });
    }
  }

  // Insight 4: High output per turn (might be verbose)
  const avgOut = withEff.reduce((s, e) => s + (e.efficiency.avgOutputPerTurn || 0), 0) / withEff.length;
  if (avgOut > 2000) {
    insights.push({
      type: "output_heavy",
      severity: "low",
      message: `Average ${avgOut.toFixed(0)} output tokens/turn — potential verbosity`,
      action: "Review if responses could be denser. Consider effort=medium for routine turns.",
    });
  }

  // Insight 5: Rate limit hits suggest need for model rotation or pacing
  const rlSessions = sessions.filter(s => s.rateLimitHits > 0);
  if (rlSessions.length > sessions.length * 0.3) {
    insights.push({
      type: "rate_limits_frequent",
      severity: "high",
      message: `Rate limits hit in ${rlSessions.length}/${sessions.length} sessions (${(rlSessions.length/sessions.length*100).toFixed(0)}%)`,
      action: "Enable model rotation (--fallback) or reduce effort for non-critical turns.",
    });
  }

  // Insight 6: No sessions yet — fresh start
  if (sessions.length < 3) {
    insights.push({
      type: "insufficient_data",
      severity: "info",
      message: `Only ${sessions.length} session(s) recorded — need ≥3 for meaningful trends`,
      action: "Run more sessions with sovereign.mjs, then re-distill.",
    });
  }

  // Display insights
  if (insights.length === 0) {
    console.log(`${S.green}${S.bold}All metrics within targets. YOUSPEAK discipline is effective.${S.reset}`);
  } else {
    for (const insight of insights) {
      const color = insight.severity === "critical" ? S.red :
                    insight.severity === "high" ? S.yellow :
                    insight.severity === "medium" ? S.cyan : S.dim;
      console.log(`${color}[${insight.severity.toUpperCase()}] ${insight.message}${S.reset}`);
      console.log(`  ${S.dim}→ ${insight.action}${S.reset}`);
      console.log();
    }
  }

  return insights;
}

// ═════════════════════════════════════════════════════════════════════
// TRANSMUTE — Generate system prompt mutations from insights
// ═════════════════════════════════════════════════════════════════════

function transmute() {
  const insights = distill();
  if (insights.length === 0) return [];

  console.log(`${S.bold}${S.magenta}── Transmutations ──${S.reset}\n`);

  const mutations = [];

  for (const insight of insights) {
    if (insight.type === "insufficient_data" || insight.severity === "info") continue;

    let mutation = null;

    switch (insight.type) {
      case "filler_high":
        mutation = {
          target: "system_prompt",
          action: "strengthen_youspeak",
          description: "Add explicit anti-filler examples to YOUSPEAK protocol block",
          before: `No filler. No preamble. No tool narration. Dense status (key:value not prose).`,
          after: `No filler. No preamble. No tool narration. Dense status (key:value not prose).
BAD: "Let me check that" / "Here's what I found" / "I'll now proceed to"
GOOD: [just do it, report results directly]`,
          estimated_save: "200-400 tokens/session from reduced filler",
        };
        break;

      case "useful_low":
        mutation = {
          target: "system_prompt",
          action: "add_density_reminder",
          description: "Add explicit density target to protocol block",
          patch: `\nTarget: ≥80% of output tokens should be substantive content, not scaffolding.`,
          estimated_save: "variable — depends on compliance",
        };
        break;

      case "context_always_loaded":
        mutation = {
          target: "config",
          action: "promote_to_boot",
          description: `Move ${insight.message.split(" ")[0]} from lazy-load to boot files`,
          change: `bootFiles: add "${insight.message.split(" ")[0]}"`,
          estimated_save: "eliminates load_context tool call overhead (~50 tokens/session)",
        };
        break;

      case "output_heavy":
        mutation = {
          target: "config",
          action: "dynamic_effort",
          description: "Use lower effort for routine/evaluation turns",
          change: "Add effort cycling: high for implementation, medium for continuation, low for evaluation",
          estimated_save: "500-2000 tokens/session from reduced thinking on routine turns",
        };
        break;

      case "rate_limits_frequent":
        mutation = {
          target: "config",
          action: "enable_pacing",
          description: "Enable proactive pacing before rate limit walls",
          change: "Add utilization-aware pacing from stream.mjs (throttle at 80%)",
          estimated_save: "prevents 429 wait time, smoother throughput",
        };
        break;
    }

    if (mutation) {
      mutations.push({ ...mutation, insight: insight.type, severity: insight.severity });
      console.log(`  ${S.magenta}⚗${S.reset} ${S.bold}${mutation.action}${S.reset}`);
      console.log(`    ${mutation.description}`);
      if (mutation.before) {
        console.log(`    ${S.red}− ${mutation.before.split("\n")[0]}${S.reset}`);
        console.log(`    ${S.green}+ ${mutation.after.split("\n")[0]}${S.reset}`);
      }
      if (mutation.change) {
        console.log(`    ${S.cyan}Δ ${mutation.change}${S.reset}`);
      }
      console.log(`    ${S.dim}Save: ${mutation.estimated_save}${S.reset}`);
      console.log();
    }
  }

  // Save mutations to history
  const history = loadHistory();
  history.mutations.push({
    timestamp: new Date().toISOString(),
    mutations,
    applied: false,
  });
  saveHistory(history);

  console.log(`${S.dim}${mutations.length} mutation(s) proposed. Apply with: node youspeak-evolve.mjs integrate --apply${S.reset}`);
  return mutations;
}

// ═════════════════════════════════════════════════════════════════════
// INTEGRATE — Apply mutations (with safety)
// ═════════════════════════════════════════════════════════════════════

function integrate(apply = false) {
  const history = loadHistory();
  const pending = history.mutations.filter(m => !m.applied);

  if (pending.length === 0) {
    console.log(`${S.dim}No pending mutations. Run: node youspeak-evolve.mjs transmute${S.reset}`);
    return;
  }

  const latest = pending[pending.length - 1];

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Evolution — Integrate ═══${S.reset}\n`);
  console.log(`${S.dim}Proposed: ${latest.timestamp}${S.reset}`);
  console.log(`${S.dim}Mutations: ${latest.mutations.length}${S.reset}\n`);

  for (const mut of latest.mutations) {
    console.log(`  ${S.bold}${mut.action}${S.reset} [${mut.severity}]`);
    console.log(`  ${mut.description}`);
    console.log();
  }

  if (!apply) {
    console.log(`${S.yellow}Preview mode. Add --apply to write changes.${S.reset}`);
    console.log(`${S.dim}Safety: all changes are git-committed and revertible.${S.reset}`);
    return;
  }

  // Apply system_prompt mutations
  let applied = 0;
  for (const mut of latest.mutations) {
    if (mut.target === "system_prompt" && mut.action === "strengthen_youspeak") {
      // Apply to all Love instance CLAUDE.md files
      const instances = readdirSync(join(LOVE_DIR, "instances")).filter(d =>
        existsSync(join(LOVE_DIR, "instances", d, "CLAUDE.md"))
      );

      for (const inst of instances) {
        const path = join(LOVE_DIR, "instances", inst, "CLAUDE.md");
        let content = readFileSync(path, "utf-8");
        if (content.includes(mut.before) && !content.includes("BAD:")) {
          content = content.replace(mut.before, mut.after);
          writeFileSync(path, content);
          console.log(`  ${S.green}✓ ${inst}/CLAUDE.md${S.reset}`);
          applied++;
        }
      }

      // Also apply to sovereign.mjs
      const sovPath = resolve("sovereign.mjs");
      if (existsSync(sovPath)) {
        let sov = readFileSync(sovPath, "utf-8");
        if (sov.includes(mut.before) && !sov.includes("BAD:")) {
          sov = sov.replace(mut.before, mut.after);
          writeFileSync(sovPath, sov);
          console.log(`  ${S.green}✓ sovereign.mjs${S.reset}`);
          applied++;
        }
      }
    }
  }

  if (applied > 0) {
    latest.applied = true;
    latest.appliedAt = new Date().toISOString();
    latest.filesModified = applied;
    saveHistory(history);
    console.log(`\n${S.green}${S.bold}Applied ${applied} changes.${S.reset} ${S.dim}git commit recommended.${S.reset}`);
  } else {
    console.log(`\n${S.dim}No applicable changes (might already be applied).${S.reset}`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// CYCLE — Full ouroboros: sense → reflect → distill
// ═════════════════════════════════════════════════════════════════════

function cycle() {
  console.log(`\n${S.bold}${S.magenta}═══ OUROBOROS CYCLE ═══${S.reset}`);
  console.log(`${S.dim}LIVE → SENSE → REFLECT → DISTILL → TRANSMUTE → INTEGRATE → LIVE${S.reset}\n`);

  console.log(`${S.bold}1. SENSE${S.reset}`);
  const entry = sense();

  console.log(`\n${S.bold}2. REFLECT${S.reset}`);
  reflect();

  console.log(`\n${S.bold}3. DISTILL${S.reset}`);
  const insights = distill();

  if (insights.length > 0 && !insights.every(i => i.severity === "info")) {
    console.log(`\n${S.bold}4. TRANSMUTE${S.reset}`);
    transmute();
    console.log(`\n${S.dim}To complete the cycle, review mutations and run:${S.reset}`);
    console.log(`  ${S.cyan}node youspeak-evolve.mjs integrate --apply${S.reset}`);
  } else {
    console.log(`\n${S.green}Cycle complete — no mutations needed. System is evolving well.${S.reset}`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// HISTORY — Show evolution over time
// ═════════════════════════════════════════════════════════════════════

function showHistory() {
  const history = loadHistory();

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Evolution History ═══${S.reset}\n`);

  console.log(`${S.bold}Sessions: ${history.sessions.length}${S.reset}`);
  for (const s of history.sessions.slice(-10)) {
    const eff = s.efficiency ? ` useful:${s.efficiency.usefulContentRatio} filler:${s.efficiency.fillerRatio}` : "";
    const rl = s.rateLimitHits ? ` ${S.yellow}429×${s.rateLimitHits}${S.reset}` : "";
    console.log(`  ${S.dim}${s.timestamp?.split("T")[0]}${S.reset} ${s.turns}t ${s.toolCalls}tc${eff}${rl} ${S.dim}${s.task?.slice(0, 50)}${S.reset}`);
  }

  console.log(`\n${S.bold}Mutations: ${history.mutations.length}${S.reset}`);
  for (const m of history.mutations) {
    const status = m.applied ? `${S.green}applied${S.reset}` : `${S.yellow}pending${S.reset}`;
    console.log(`  ${S.dim}${m.timestamp?.split("T")[0]}${S.reset} ${m.mutations.length} mutations [${status}]`);
    for (const mut of m.mutations) {
      console.log(`    ${S.dim}${mut.action}: ${mut.description?.slice(0, 60)}${S.reset}`);
    }
  }
}

// ═════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════

const cmd = process.argv[2];
const applyFlag = process.argv.includes("--apply");

switch (cmd) {
  case "sense":     sense(); break;
  case "reflect":   reflect(); break;
  case "distill":   distill(); break;
  case "transmute": transmute(); break;
  case "integrate": integrate(applyFlag); break;
  case "cycle":     cycle(); break;
  case "history":   showHistory(); break;
  default:
    console.log(`
${S.bold}youspeak-evolve.mjs${S.reset} — The Complete Ouroboros

${S.dim}LIVE → SENSE → REFLECT → DISTILL → TRANSMUTE → INTEGRATE → LIVE${S.reset}

Commands:
  ${S.cyan}sense${S.reset}               Record current session metrics into history
  ${S.cyan}reflect${S.reset}             Analyze trends across all sessions
  ${S.cyan}distill${S.reset}             Extract actionable insights
  ${S.cyan}transmute${S.reset}           Generate system prompt mutations
  ${S.cyan}integrate${S.reset}           Preview mutations (add --apply to write)
  ${S.cyan}cycle${S.reset}               Full cycle: sense → reflect → distill → transmute
  ${S.cyan}history${S.reset}             Show evolution history

Typical flow:
  1. Run sovereign.mjs on a task
  2. node youspeak-evolve.mjs cycle
  3. Review proposed mutations
  4. node youspeak-evolve.mjs integrate --apply
  5. Git commit
  6. Repeat
`);
}
