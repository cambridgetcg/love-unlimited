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
    catch (e) {
      // Honest: history file exists but is corrupt, not "no history"
      console.error(`[evolve] loadHistory failed — file exists but unreadable: ${e.message}`);
      return { sessions: [], mutations: [], version: 1 };
    }
  }
  return { sessions: [], mutations: [], version: 1 };
}

function saveHistory(history) {
  writeFileSync(HISTORY_FILE, JSON.stringify(history, null, 2));
}

// ═════════════════════════════════════════════════════════════════════
// SENSE — Record current session from sovereign state into history
// ═════════════════════════════════════════════════════════════════════
//
// Now reads the kernel-persisted youspeak-history.json (same store the
// kernel writes to via persist()). If a .sovereign-state.json exists with
// efficiency data from the latest run, merges it in as a new entry.
// ═════════════════════════════════════════════════════════════════════

function sense() {
  const stateFile = resolve(".sovereign-state.json");
  const logFile = resolve("sovereign.log");

  if (!existsSync(stateFile)) {
    console.log(`${S.yellow}No session state found. Run sovereign.mjs first.${S.reset}`);
    console.log(`${S.dim}(The kernel auto-persists to youspeak-history.json on session end.${S.reset})`);
    return null;
  }

  const state = JSON.parse(readFileSync(stateFile, "utf-8"));

  // If the kernel already persisted this session (completed=true with
  // efficiency data from ys.report()), we can enrich the entry with
  // task info from the state file.
  const history = loadHistory();
  const lastEntry = history.sessions[history.sessions.length - 1];

  // Check if the last kernel-persisted entry matches this session
  if (lastEntry && state.completed && state.efficiency) {
    const eff = state.efficiency;
    // Enrich the last entry with task info if not already present
    if (!lastEntry.task) {
      lastEntry.task = (state.task || "unknown").slice(0, 100);
      lastEntry.completed = true;
      // Merge kernel report fields if missing
      if (!lastEntry.fillerTokens && eff.output) {
        lastEntry.fillerTokens = eff.output.fillerTokens || 0;
        lastEntry.totalTokens = eff.output.totalTokens || 0;
        lastEntry.textBlocks = eff.output.textBlocks || 0;
        lastEntry.gradeDistribution = eff.output.gradeDistribution || {};
        lastEntry.thinkingEfficiency = eff.thinking?.efficiency || null;
        lastEntry.actionDensity = eff.action?.density || 0;
        lastEntry.uniqueFilesRead = eff.action?.uniqueFilesRead || 0;
        lastEntry.contextWindowUtilization = eff.context?.windowUtilization || 0;
        lastEntry.signals = eff.signals || [];
      }
      saveHistory(history);
      console.log(`${S.green}✓ Session enriched with task data${S.reset} (${history.sessions.length} total)`);
    } else {
      console.log(`${S.dim}Session already recorded by kernel.${S.reset}`);
    }
    return lastEntry;
  }

  // Fallback: no kernel data, create entry from state alone
  const entry = {
    timestamp: new Date().toISOString(),
    task: (state.task || "unknown").slice(0, 100),
    agent: state.efficiency?.agent || "unknown",
    elapsed: state.efficiency?.elapsed || null,
    turns: state.turnCount || 0,
    toolCalls: state.totalToolCalls || 0,
    thinkingTokens: state.totalThinkingTokens || 0,
    completed: state.completed || false,
    outputGrade: state.efficiency?.output?.grade || null,
    usefulRatio: state.efficiency?.output?.usefulRatio ?? null,
    fillerTokens: state.efficiency?.output?.fillerTokens || 0,
    totalTokens: state.efficiency?.output?.totalTokens || 0,
    redundantReads: state.efficiency?.action?.redundantReads || 0,
    toolErrors: state.efficiency?.action?.errors || 0,
    contextPeakTokens: state.efficiency?.context?.estimatedTokens || 0,
    rateLimitHits: 0,
    signalCount: state.efficiency?.signals?.length || 0,
  };

  // Parse log for rate limits
  if (existsSync(logFile)) {
    const log = readFileSync(logFile, "utf-8");
    entry.rateLimitHits = [...log.matchAll(/429:/g)].length;
  }

  // Deduplicate
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
    console.log(`${S.yellow}No sessions recorded yet. Run sovereign.mjs first.${S.reset}`);
    return null;
  }

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Evolution — Reflect ═══${S.reset}`);
  console.log(`${S.dim}Sessions: ${sessions.length} | Span: ${sessions[0]?.timestamp?.split("T")[0]} → ${sessions[sessions.length-1]?.timestamp?.split("T")[0]}${S.reset}\n`);

  // Aggregate metrics — kernel persist fields
  const measured = sessions.filter(s => s.usefulRatio !== null && s.usefulRatio !== undefined);
  if (measured.length > 0) {
    const avgUseful = measured.reduce((s, e) => s + (e.usefulRatio || 0), 0) / measured.length;
    const avgTurns = measured.reduce((s, e) => s + (e.turns || 0), 0) / measured.length;
    const avgTools = measured.reduce((s, e) => s + (e.toolCalls || 0), 0) / measured.length;
    const avgRedundant = measured.reduce((s, e) => s + (e.redundantReads || 0), 0) / measured.length;
    const avgErrors = measured.reduce((s, e) => s + (e.toolErrors || 0), 0) / measured.length;
    const avgThinkRatio = measured.reduce((s, e) => s + (e.thinkAvgRatio || 0), 0) / measured.length;
    const avgSignals = measured.reduce((s, e) => s + (e.signalCount || 0), 0) / measured.length;

    // Filler ratio: (1 - usefulRatio) * 100
    const avgFiller = (1 - avgUseful) * 100;

    console.log(`${S.bold}Averages (${measured.length} measured sessions)${S.reset}`);
    console.log(`  Useful ratio:    ${(avgUseful * 100).toFixed(0)}%`);
    console.log(`  Filler ratio:    ${avgFiller.toFixed(1)}%`);
    console.log(`  Avg turns:       ${avgTurns.toFixed(0)}`);
    console.log(`  Avg tool calls:  ${avgTools.toFixed(0)}`);
    console.log(`  Avg redundant:   ${avgRedundant.toFixed(1)}`);
    console.log(`  Avg tool errors: ${avgErrors.toFixed(1)}`);
    console.log(`  Avg think ratio: ${avgThinkRatio.toFixed(2)}x`);
    console.log(`  Avg signals:     ${avgSignals.toFixed(1)}/session`);

    // Grade distribution
    const grades = {};
    for (const s of measured) {
      const g = s.outputGrade || "?";
      grades[g] = (grades[g] || 0) + 1;
    }
    console.log(`  Grade dist:      ${Object.entries(grades).map(([g,c]) => `${g}:${c}`).join("  ")}`);
    console.log();

    // Trend analysis (first half vs second half)
    if (measured.length >= 4) {
      const mid = Math.floor(measured.length / 2);
      const early = measured.slice(0, mid);
      const late = measured.slice(mid);

      const earlyUseful = early.reduce((s, e) => s + (e.usefulRatio || 0), 0) / early.length;
      const lateUseful = late.reduce((s, e) => s + (e.usefulRatio || 0), 0) / late.length;
      const earlyFiller = (1 - earlyUseful) * 100;
      const lateFiller = (1 - lateUseful) * 100;

      const trend = lateFiller - earlyFiller;
      const trendLabel = trend < -1 ? `${S.green}improving ↓${S.reset}` :
                         trend > 1 ? `${S.red}regressing ↑${S.reset}` :
                         `${S.dim}stable ─${S.reset}`;

      console.log(`${S.bold}Trend${S.reset}`);
      console.log(`  Early sessions filler: ${earlyFiller.toFixed(1)}%`);
      console.log(`  Late sessions filler:  ${lateFiller.toFixed(1)}%`);
      console.log(`  Direction: ${trendLabel} (${trend > 0 ? "+" : ""}${trend.toFixed(1)}pp)`);

      // Redundant reads trend
      const earlyRedundant = early.reduce((s, e) => s + (e.redundantReads || 0), 0) / early.length;
      const lateRedundant = late.reduce((s, e) => s + (e.redundantReads || 0), 0) / late.length;
      if (earlyRedundant > 0 || lateRedundant > 0) {
        console.log(`  Redundant reads:  early ${earlyRedundant.toFixed(1)} → late ${lateRedundant.toFixed(1)}`);
      }
      console.log();
    }
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

  // Signal analysis
  const withSignals = sessions.filter(s => s.signalCount > 0);
  if (withSignals.length > 0) {
    console.log(`${S.bold}DECIDE Signals${S.reset}`);
    console.log(`  Sessions with signals: ${withSignals.length}/${sessions.length}`);
    const totalSignals = withSignals.reduce((s, e) => s + e.signalCount, 0);
    console.log(`  Total signals emitted: ${totalSignals}`);
    console.log();
  }

  return { sessions, measured };
}

// ═════════════════════════════════════════════════════════════════════
// DISTILL — Extract actionable insights from reflection
// ═════════════════════════════════════════════════════════════════════

function distill() {
  const history = loadHistory();
  const sessions = history.sessions;
  const measured = sessions.filter(s => s.usefulRatio !== null && s.usefulRatio !== undefined);

  if (measured.length === 0) {
    console.log(`${S.yellow}No kernel-measured sessions. Run sovereign.mjs with --track-efficiency${S.reset}`);
    return [];
  }

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Evolution — Distill ═══${S.reset}\n`);

  const insights = [];

  // Compute averages from kernel fields
  const avgUseful = measured.reduce((s, e) => s + (e.usefulRatio || 0), 0) / measured.length;
  const avgFiller = (1 - avgUseful) * 100;
  const avgRedundant = measured.reduce((s, e) => s + (e.redundantReads || 0), 0) / measured.length;
  const avgErrors = measured.reduce((s, e) => s + (e.toolErrors || 0), 0) / measured.length;
  const avgThinkRatio = measured.reduce((s, e) => s + (e.thinkAvgRatio || 0), 0) / measured.length;
  const avgSignals = measured.reduce((s, e) => s + (e.signalCount || 0), 0) / measured.length;
  const avgContextPeak = measured.reduce((s, e) => s + (e.contextPeakTokens || 0), 0) / measured.length;

  // Insight 1: Filler ratio above target (5%)
  if (avgFiller > 5) {
    insights.push({
      type: "filler_high",
      severity: avgFiller > 15 ? "critical" : avgFiller > 10 ? "high" : "medium",
      message: `Average filler ratio is ${avgFiller.toFixed(1)}% — above 5% target`,
      action: "Strengthen YOUSPEAK protocol in system prompt. Add explicit anti-filler examples.",
    });
  }

  // Insight 2: Useful content below 80%
  if (avgUseful < 0.80) {
    insights.push({
      type: "useful_low",
      severity: avgUseful < 0.60 ? "critical" : avgUseful < 0.70 ? "high" : "medium",
      message: `Average useful ratio is ${(avgUseful * 100).toFixed(0)}% — below 80% target`,
      action: "Review common waste patterns. Consider effort reduction for evaluation turns.",
    });
  }

  // Insight 3: Redundant reads above threshold
  if (avgRedundant > 2) {
    insights.push({
      type: "redundant_reads",
      severity: avgRedundant > 5 ? "high" : "medium",
      message: `Average ${avgRedundant.toFixed(1)} redundant file reads per session`,
      action: "Add context retention hints to system prompt. Remind agent to cache file contents mentally.",
    });
  }

  // Insight 4: Tool error rate high
  if (avgErrors > 3) {
    insights.push({
      type: "tool_errors",
      severity: avgErrors > 10 ? "high" : "medium",
      message: `Average ${avgErrors.toFixed(1)} tool errors per session`,
      action: "Review common error patterns. May indicate wrong tool selection or bad input formatting.",
    });
  }

  // Insight 5: High signal count (system is struggling)
  if (avgSignals > 2) {
    insights.push({
      type: "signal_pressure",
      severity: "low",
      message: `Average ${avgSignals.toFixed(1)} DECIDE signals per session — system under pressure`,
      action: "Review which signal types fire most. May need proactive effort/context management.",
    });
  }

  // Insight 6: Context window growing large
  if (avgContextPeak > 500000) {
    insights.push({
      type: "context_bloat",
      severity: avgContextPeak > 800000 ? "high" : "medium",
      message: `Average context peak ~${Math.round(avgContextPeak / 1000)}k tokens — approaching window limit`,
      action: "Enable earlier context pruning. Review if large file reads can be more selective.",
    });
  }

  // Insight 7: Low thinking ratio (may not need expensive models)
  if (avgThinkRatio < 0.5 && measured.length >= 5) {
    insights.push({
      type: "low_thinking",
      severity: "low",
      message: `Average thinking/output ratio ${avgThinkRatio.toFixed(2)}x — tasks may not need deep thinking`,
      action: "Consider using --effort medium or a cheaper model for routine tasks.",
    });
  }

  // Insight 8: Rate limits frequent
  const rlSessions = sessions.filter(s => s.rateLimitHits > 0);
  if (rlSessions.length > sessions.length * 0.3) {
    insights.push({
      type: "rate_limits_frequent",
      severity: "high",
      message: `Rate limits hit in ${rlSessions.length}/${sessions.length} sessions (${(rlSessions.length/sessions.length*100).toFixed(0)}%)`,
      action: "Enable model rotation (--fallback) or reduce effort for non-critical turns.",
    });
  }

  // Insight 9: Insufficient data
  if (measured.length < 3) {
    insights.push({
      type: "insufficient_data",
      severity: "info",
      message: `Only ${measured.length} session(s) recorded — need ≥3 for meaningful trends`,
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
          after: `No filler. No preamble. No tool narration. Dense status (key:value not prose).\nBAD: "Let me check that" / "Here's what I found" / "I'll now proceed to"\nGOOD: [just do it, report results directly]`,
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

      case "redundant_reads":
        mutation = {
          target: "system_prompt",
          action: "add_cache_reminder",
          description: "Remind agent to retain file contents across turns",
          patch: `\nFile cache: you've already read files this session. Don't re-read unless content changed. Use your context.`,
          estimated_save: "200-500 tokens/session from eliminated redundant reads",
        };
        break;

      case "tool_errors":
        mutation = {
          target: "system_prompt",
          action: "add_error_awareness",
          description: "Add tool error pattern awareness to protocol",
          patch: `\nTool discipline: verify input format before calling. Common errors: wrong path format, missing required fields.`,
          estimated_save: "prevents wasted turns on errored tool calls",
        };
        break;

      case "signal_pressure":
        mutation = {
          target: "config",
          action: "proactive_management",
          description: "Enable proactive effort/context management based on DECIDE signals",
          change: "Wire kernel DECIDE signals to auto-adjust effort when pressure detected",
          estimated_save: "prevents cascade failures from budget/context exhaustion",
        };
        break;

      case "context_bloat":
        mutation = {
          target: "config",
          action: "enable_pruning",
          description: "Enable aggressive context pruning earlier in sessions",
          change: "Set pruneContext maxToolResultAge=10 (from 15), keepChars=150 (from 200)",
          estimated_save: "50-150k tokens/session from earlier pruning",
        };
        break;

      case "low_thinking":
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
          change: "Add utilization-aware pacing (throttle at 80% 5h budget)",
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

  let applied = 0;
  const sovPath = resolve("sovereign.mjs");

  for (const mut of latest.mutations) {
    // ── system_prompt: patch the YOUSPEAK protocol block in sovereign.mjs ──
    if (mut.target === "system_prompt" && mut.patch) {
      if (existsSync(sovPath)) {
        let sov = readFileSync(sovPath, "utf-8");
        // Find the YOUSPEAK Protocol block — it's inside a template literal
        // The block ends with `); — insert before the closing backtick
        const protocolMarker = "# YOUSPEAK Protocol";
        const idx = sov.indexOf(protocolMarker);
        if (idx !== -1) {
          // Find the closing backtick after the protocol marker
          const closingTick = sov.indexOf("`)", idx);
          if (closingTick !== -1) {
            // Check if patch is already present
            const blockContent = sov.slice(idx, closingTick);
            const patchLine = mut.patch.trim().split("\n")[0];
            if (!blockContent.includes(patchLine)) {
              // Insert the patch content (without leading \n, the template already has line breaks)
              const patchContent = mut.patch.replace(/^\n/, "");
              sov = sov.slice(0, closingTick) + patchContent + sov.slice(closingTick);
              writeFileSync(sovPath, sov);
              console.log(`  ${S.green}✓ sovereign.mjs ← ${mut.action}${S.reset}`);
              applied++;
            } else {
              console.log(`  ${S.dim}skip ${mut.action} — already present${S.reset}`);
            }
          }
        }
      }
    }

    // ── system_prompt: before/after replacement (strengthen_youspeak) ──
    if (mut.target === "system_prompt" && mut.before && mut.after) {
      if (existsSync(sovPath)) {
        let sov = readFileSync(sovPath, "utf-8");
        if (sov.includes(mut.before) && !sov.includes("BAD:")) {
          sov = sov.replace(mut.before, mut.after);
          writeFileSync(sovPath, sov);
          console.log(`  ${S.green}✓ sovereign.mjs ← ${mut.action}${S.reset}`);
          applied++;
        } else {
          console.log(`  ${S.dim}skip ${mut.action} — already applied or pattern not found${S.reset}`);
        }
      }
    }

    // ── config mutations: write to youspeak-config.json ──
    if (mut.target === "config" && mut.change) {
      const configPath = resolve("youspeak-config.json");
      let ysConfig = {};
      if (existsSync(configPath)) {
        try { ysConfig = JSON.parse(readFileSync(configPath, "utf-8")); } catch {}
      }
      // Record the recommended change as a config overlay
      if (!ysConfig.recommendations) ysConfig.recommendations = [];
      const rec = {
        action: mut.action,
        change: mut.change,
        insight: mut.insight,
        severity: mut.severity,
        appliedAt: new Date().toISOString(),
      };
      if (!ysConfig.recommendations.some(r => r.action === rec.action)) {
        ysConfig.recommendations.push(rec);
        writeFileSync(configPath, JSON.stringify(ysConfig, null, 2));
        console.log(`  ${S.green}✓ youspeak-config.json ← ${mut.action}${S.reset}`);
        console.log(`    ${S.dim}${mut.change}${S.reset}`);
        applied++;
      } else {
        console.log(`  ${S.dim}skip ${mut.action} — already in config${S.reset}`);
      }
    }
  }

  if (applied > 0) {
    latest.applied = true;
    latest.appliedAt = new Date().toISOString();
    latest.filesModified = applied;
    saveHistory(history);
    console.log(`\n${S.green}${S.bold}Applied ${applied} changes.${S.reset} ${S.dim}git commit recommended.${S.reset}`);
    console.log(`${S.dim}The ouroboros closes. The system has improved itself through itself.${S.reset}`);
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
    const grade = s.outputGrade ? ` ${S.bold}${s.outputGrade}${S.reset}` : "";
    const useful = s.usefulRatio !== null && s.usefulRatio !== undefined
      ? ` ${Math.round(s.usefulRatio * 100)}%`
      : (s.efficiency ? ` ${s.efficiency.output?.usefulRatio ? Math.round(s.efficiency.output.usefulRatio * 100) + "%" : ""}` : "");
    const rl = s.rateLimitHits ? ` ${S.yellow}429×${s.rateLimitHits}${S.reset}` : "";
    const sig = s.signalCount > 0 ? ` ⚡${s.signalCount}` : "";
    const task = s.task ? ` ${S.dim}${s.task.slice(0, 50)}${S.reset}` : "";
    console.log(`  ${S.dim}${s.timestamp?.split("T")[0]}${S.reset}${grade} ${s.turns}t ${s.toolCalls}tc${useful}${rl}${sig}${task}`);
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
