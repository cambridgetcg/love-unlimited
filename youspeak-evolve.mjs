#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// youspeak-evolve.mjs — the ouroboros, proposal-only
//
// LIVE → SENSE → REFLECT → DISTILL → PROPOSE → (a person reviews) → LIVE
//
// Reads YOUSPEAK's store (one row per session; youspeak-kernel.mjs), distils
// insights, and prints proposed changes as full diffs, so a reviewer sees
// exactly what would change. It never writes: not source, not config, not
// history (Yu, 2026-10-30: proposal-only; docs/YOUSPEAK.md). A proposal is
// applied, if at all, by a person or agent editing the file and committing it.
//
// Usage:
//   node youspeak-evolve.mjs reflect     # Trends across sessions
//   node youspeak-evolve.mjs distill     # Insights
//   node youspeak-evolve.mjs transmute   # Insights, then proposed diffs (alias: propose)
//   node youspeak-evolve.mjs cycle       # reflect → distill → propose
//   node youspeak-evolve.mjs history     # The store's latest sessions, and the legacy record
//
// The store is ~/.local/share/youi/youspeak/history.jsonl, or YOUSPEAK_HISTORY_PATH.
// ─────────────────────────────────────────────────────────────────────

import { readFileSync, existsSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import {
  defaultHistoryPath,
  readHistory,
  readLegacyHistory,
  rowDay,
  trendDirection,
  TREND_TOLERANCE,
} from "./youspeak-kernel.mjs";

const S = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
  blue: "\x1b[34m", magenta: "\x1b[35m", cyan: "\x1b[36m",
};

const REPO_DIR = dirname(fileURLToPath(import.meta.url));
// The rule the system-prompt proposals are drawn against: the one module
// every prompt built in code takes its rule from (Y2): YOUI Web, the terminal,
// sovereign and the three unused harnesses. The eleven instances/*/CLAUDE.md
// copies are separate Markdown, which these diffs do not reach.
const RULE_FILE = "youspeak-rule.mjs";
const RULE_PATH = join(REPO_DIR, RULE_FILE);
// Where the rule's text starts in that file, and where it ends.
const RULE_START = "export const YOUSPEAK_RULE = `";
const RULE_END = "`;";

// ═════════════════════════════════════════════════════════════════════
// HISTORY — the store, read only
// ═════════════════════════════════════════════════════════════════════

function loadStore() {
  const store = readHistory(defaultHistoryPath());
  // A corrupt or partial store is reported, never reset or rewritten.
  for (const warning of store.warnings) console.log(`${S.yellow}⚠ ${warning}${S.reset}`);
  return store;
}

const measuredRows = rows => rows.filter(s => typeof s.usefulRatio === "number");
const day = rowDay;

// ═════════════════════════════════════════════════════════════════════
// REFLECT — Trends across sessions (one row each)
// ═════════════════════════════════════════════════════════════════════

function reflect() {
  const sessions = loadStore().rows;

  if (sessions.length === 0) {
    console.log(`${S.yellow}No sessions in the store yet. Each YOUI, terminal or sovereign session writes one row when it ends.${S.reset}`);
    return null;
  }

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Evolution — Reflect ═══${S.reset}`);
  console.log(`${S.dim}Sessions: ${sessions.length} | Span: ${day(sessions[0])} → ${day(sessions.at(-1))}${S.reset}\n`);

  const measured = measuredRows(sessions);
  if (measured.length > 0) {
    const avg = key => measured.reduce((s, e) => s + (Number(e[key]) || 0), 0) / measured.length;
    const avgUseful = avg("usefulRatio");

    console.log(`${S.bold}Averages (${measured.length} sessions with output)${S.reset}`);
    console.log(`  Useful ratio:    ${(avgUseful * 100).toFixed(0)}% ${S.dim}(English-only estimate)${S.reset}`);
    console.log(`  Filler ratio:    ${((1 - avgUseful) * 100).toFixed(1)}%`);
    console.log(`  Avg turns:       ${avg("turns").toFixed(0)}`);
    console.log(`  Avg tool calls:  ${avg("toolCalls").toFixed(0)}`);
    console.log(`  Avg redundant:   ${avg("redundantReads").toFixed(1)}`);
    console.log(`  Avg tool errors: ${avg("toolErrors").toFixed(1)}`);
    console.log(`  Avg signals:     ${avg("signalCount").toFixed(1)}/session`);

    const routes = {};
    for (const s of sessions) routes[s.route || "?"] = (routes[s.route || "?"] || 0) + 1;
    console.log(`  Routes:          ${Object.entries(routes).map(([r, c]) => `${r}:${c}`).join("  ")}`);
    console.log();

    if (measured.length >= 4) {
      const mid = Math.floor(measured.length / 2);
      const mean = (arr, key) => arr.reduce((s, e) => s + (Number(e[key]) || 0), 0) / arr.length;
      const earlyUseful = mean(measured.slice(0, mid), "usefulRatio");
      const lateUseful = mean(measured.slice(mid), "usefulRatio");
      const earlyFiller = (1 - earlyUseful) * 100;
      const lateFiller = (1 - lateUseful) * 100;
      const trend = lateFiller - earlyFiller;
      // The kernel's tolerance (TREND_TOLERANCE, one percentage point), so both say the same.
      const direction = trendDirection(earlyUseful, lateUseful);
      const trendLabel = direction === "improving" ? `${S.green}improving ↓${S.reset}` :
                         direction === "regressing" ? `${S.red}regressing ↑${S.reset}` :
                         `${S.dim}steady ─ (within ±${TREND_TOLERANCE * 100}pp)${S.reset}`;
      console.log(`${S.bold}Trend${S.reset}`);
      console.log(`  Early sessions filler: ${earlyFiller.toFixed(1)}%`);
      console.log(`  Late sessions filler:  ${lateFiller.toFixed(1)}%`);
      console.log(`  Direction: ${trendLabel} (${trend > 0 ? "+" : ""}${trend.toFixed(1)}pp)`);
      console.log();
    }
  }

  const withRL = sessions.filter(s => s.rateLimitHits > 0);
  if (withRL.length > 0) {
    console.log(`${S.bold}Rate Limits${S.reset}`);
    console.log(`  Sessions with 429s: ${withRL.length}/${sessions.length}`);
    console.log(`  Total 429 events: ${withRL.reduce((s, e) => s + e.rateLimitHits, 0)}`);
    console.log();
  }

  const withSignals = sessions.filter(s => s.signalCount > 0);
  if (withSignals.length > 0) {
    const kinds = {};
    for (const s of withSignals) for (const key of s.signals || []) kinds[key] = (kinds[key] || 0) + 1;
    console.log(`${S.bold}Suggestions shown${S.reset}`);
    console.log(`  Sessions with signals: ${withSignals.length}/${sessions.length}`);
    console.log(`  By kind: ${Object.entries(kinds).map(([k, c]) => `${k}:${c}`).join("  ") || "—"}`);
    console.log();
  }

  return { sessions, measured };
}

// ═════════════════════════════════════════════════════════════════════
// DISTILL — Insights from the store
// ═════════════════════════════════════════════════════════════════════

function distill() {
  const sessions = loadStore().rows;
  const measured = measuredRows(sessions);

  if (measured.length === 0) {
    console.log(`${S.yellow}No sessions with output in the store yet.${S.reset}`);
    return [];
  }

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK Evolution — Distill ═══${S.reset}\n`);

  const insights = [];
  const avg = key => measured.reduce((s, e) => s + (Number(e[key]) || 0), 0) / measured.length;
  const avgUseful = avg("usefulRatio");
  const avgFiller = (1 - avgUseful) * 100;
  const avgRedundant = avg("redundantReads");
  const avgErrors = avg("toolErrors");
  const avgSignals = avg("signalCount");
  // The context peak against the model's window, from rows that know it (Y2):
  // no window is assumed for the others.
  const windowed = measured.filter(s => typeof s.contextPeakTokens === "number" && typeof s.contextWindow === "number" && s.contextWindow > 0);
  const avgPeakShare = windowed.length
    ? windowed.reduce((sum, s) => sum + s.contextPeakTokens / s.contextWindow, 0) / windowed.length
    : null;

  if (avgFiller > 5) {
    insights.push({
      type: "filler_high",
      severity: avgFiller > 15 ? "critical" : avgFiller > 10 ? "high" : "medium",
      message: `Average filler estimate is ${avgFiller.toFixed(1)}%, above the 5% target`,
      action: "Strengthen the YOUSPEAK rule with explicit anti-filler examples.",
    });
  }

  if (avgUseful < 0.80) {
    insights.push({
      type: "useful_low",
      severity: avgUseful < 0.60 ? "critical" : avgUseful < 0.70 ? "high" : "medium",
      message: `Average useful-ratio estimate is ${(avgUseful * 100).toFixed(0)}%, below the 80% target`,
      action: "Review common waste patterns.",
    });
  }

  if (avgRedundant > 2) {
    insights.push({
      type: "redundant_reads",
      severity: avgRedundant > 5 ? "high" : "medium",
      message: `Average ${avgRedundant.toFixed(1)} repeated file reads per session`,
      action: "Remind the agent to use files it has already read.",
    });
  }

  if (avgErrors > 3) {
    insights.push({
      type: "tool_errors",
      severity: avgErrors > 10 ? "high" : "medium",
      message: `Average ${avgErrors.toFixed(1)} tool errors per session`,
      action: "Review common error patterns: wrong tool, or badly formed input.",
    });
  }

  if (avgSignals > 2) {
    insights.push({
      type: "signal_pressure",
      severity: "low",
      message: `Average ${avgSignals.toFixed(1)} kinds of signal per session`,
      action: "Review which suggestions fire most, and whether people act on them.",
    });
  }

  if (avgPeakShare !== null && avgPeakShare > 0.5) {
    insights.push({
      type: "context_bloat",
      severity: avgPeakShare > 0.8 ? "high" : "medium",
      message: `Average context peak at ${Math.round(avgPeakShare * 100)}% of the model's window (${windowed.length} session(s) whose window was known)`,
      action: "Review whether large reads could be narrower, or conversations restarted sooner.",
    });
  }

  // (The low-thinking insight is gone with the L2 model-downgrade rule: no
  // caller reported thinking tokens, so it always fired.)

  const rlSessions = sessions.filter(s => s.rateLimitHits > 0);
  if (rlSessions.length > sessions.length * 0.3) {
    insights.push({
      type: "rate_limits_frequent",
      severity: "high",
      message: `Rate limits hit in ${rlSessions.length}/${sessions.length} sessions (${(rlSessions.length / sessions.length * 100).toFixed(0)}%)`,
      action: "Consider pacing work before the 5-hour window fills.",
    });
  }

  if (measured.length < 3) {
    insights.push({
      type: "insufficient_data",
      severity: "info",
      message: `Only ${measured.length} session(s) with output in the store; need ≥3 for meaningful trends`,
      action: "Run more sessions, then distil again.",
    });
  }

  if (insights.length === 0) {
    console.log(`${S.green}${S.bold}All estimates within targets.${S.reset}`);
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
// PROPOSE — Proposed changes, printed as full diffs; nothing is written
// ═════════════════════════════════════════════════════════════════════

// What each insight proposes. `system_prompt` proposals become a diff of the
// rule in youspeak-rule.mjs (YOUSPEAK_RULE), which every prompt built in code
// carries (not the instances' CLAUDE.md copies); `note` proposals are
// described in full and need a code change someone writes and reviews on its own.
function proposalFor(insight) {
  switch (insight.type) {
    case "filler_high":
      return {
        target: "system_prompt",
        action: "strengthen_youspeak",
        description: "Add explicit anti-filler examples to the YOUSPEAK rule",
        before: `No filler. No preamble. No tool narration. Dense status (key:value not prose).`,
        after: `No filler. No preamble. No tool narration. Dense status (key:value not prose).\nBAD: "Let me check that" / "Here's what I found" / "I'll now proceed to"\nGOOD: [just do it, report results directly]`,
      };
    case "useful_low":
      return {
        target: "system_prompt",
        action: "add_density_reminder",
        description: "Add an explicit density target to the YOUSPEAK rule",
        patch: `\nTarget: ≥80% of output tokens should be substantive content, not scaffolding.`,
      };
    case "redundant_reads":
      return {
        target: "system_prompt",
        action: "add_cache_reminder",
        description: "Remind the agent to retain file contents across turns",
        patch: `\nFile cache: you've already read files this session. Don't re-read unless content changed. Use your context.`,
      };
    case "tool_errors":
      return {
        target: "system_prompt",
        action: "add_error_awareness",
        description: "Add tool-error awareness to the YOUSPEAK rule",
        patch: `\nTool discipline: verify input format before calling. Common errors: wrong path format, missing required fields.`,
      };
    case "signal_pressure":
      return {
        target: "note",
        action: "review_suggestions",
        description: "Review which YOUSPEAK suggestions fire most and whether they help",
        change: "Look at the store's `signals` per session. YOUSPEAK suggests only; it does not adjust effort or context itself.",
      };
    case "context_bloat":
      return {
        target: "note",
        action: "narrower_reads",
        description: "Keep conversations smaller",
        change: "Prefer narrower reads, and start a new conversation once the work so far is summarised. YOUSPEAK does not prune history.",
      };
    case "rate_limits_frequent":
      return {
        target: "note",
        action: "pacing",
        description: "Pace work before the 5-hour window fills",
        change: "Plan heavy work for when the window has room; lower effort by hand if needed. Nothing reads youspeak-config.json, so a config entry would not change anything.",
      };
    default:
      return null;
  }
}

// Text inserted into a template literal is escaped as it would have to be written.
const templateSafe = text => text.replace(/\\/g, "\\\\").replace(/`/g, "\\`").replace(/\$\{/g, "\\${");

// The source with the proposal applied, in memory only, or { skip } with why
// not, or { describe } when the change it needs is not a plain diff of the rule.
function proposedSource(source, proposal) {
  const opening = source.indexOf(RULE_START);
  if (opening === -1) return { skip: `no YOUSPEAK_RULE in ${RULE_FILE}` };
  const start = opening + RULE_START.length;
  const end = source.indexOf(RULE_END, start);
  if (end === -1) return { skip: `YOUSPEAK_RULE in ${RULE_FILE} has no end` };
  const block = source.slice(start, end);
  if (proposal.patch) {
    const text = templateSafe(proposal.patch);
    if (block.includes(text.trim())) return { skip: `already in the rule in ${RULE_FILE}` };
    // Sovereign's file-cache line lives in the module outside the shared rule:
    // only sovereign's prompt has it. Adding it to the rule would give
    // sovereign the line twice, so the change is described, not diffed.
    if (source.includes(text.trim())) {
      return {
        describe: `Only sovereign's prompt has this line (YOUSPEAK_FILE_CACHE_LINE in ${RULE_FILE}, added by youspeakRule({ fileCache: true })); `
          + "YOUI Web and the terminal do not. To give it to every prompt built in code, move that line into YOUSPEAK_RULE "
          + "and drop sovereign's fileCache option, in one reviewed change.",
      };
    }
    // The patch begins with a newline, so it starts a line of its own.
    return { after: source.slice(0, end) + text + source.slice(end) };
  }
  if (proposal.before && proposal.after) {
    const after = templateSafe(proposal.after);
    if (block.includes(after)) return { skip: `already in the rule in ${RULE_FILE}` };
    const at = block.indexOf(proposal.before);
    if (at === -1) return { skip: `the line it replaces is not in the rule in ${RULE_FILE}` };
    const from = start + at;
    return { after: source.slice(0, from) + after + source.slice(from + proposal.before.length) };
  }
  return { skip: "nothing to change" };
}

/** A unified diff (one hunk, three lines of context) between two texts. */
function unifiedDiff(name, before, after) {
  if (before === after) return "";
  const a = before.split("\n");
  const b = after.split("\n");
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p++;
  let s = 0;
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
  const aEnd = a.length - s;
  const bEnd = b.length - s;
  const from = Math.max(0, p - 3);
  const tail = Math.min(3, s);
  const lines = [
    `--- a/${name}`,
    `+++ b/${name} (proposed, not applied)`,
    `@@ -${from + 1},${aEnd + tail - from} +${from + 1},${bEnd + tail - from} @@`,
    ...a.slice(from, p).map(line => ` ${line}`),
    ...a.slice(p, aEnd).map(line => `-${line}`),
    ...b.slice(p, bEnd).map(line => `+${line}`),
    ...a.slice(aEnd, aEnd + tail).map(line => ` ${line}`),
  ];
  return lines.join("\n");
}

function transmute() {
  const insights = distill();
  const actionable = insights.filter(i => i.type !== "insufficient_data" && i.severity !== "info");
  if (actionable.length === 0) return [];

  console.log(`${S.bold}${S.magenta}── Proposals (nothing is written) ──${S.reset}\n`);

  const source = existsSync(RULE_PATH) ? readFileSync(RULE_PATH, "utf-8") : null;
  // Only what is actually proposed is counted: a diff, or a described change.
  // A system_prompt proposal that printed "No diff" proposes nothing.
  const proposals = [];
  for (const insight of actionable) {
    const proposal = proposalFor(insight);
    if (!proposal) continue;
    console.log(`  ${S.magenta}⚗${S.reset} ${S.bold}${proposal.action}${S.reset} ${S.dim}[${insight.severity}]${S.reset}`);
    console.log(`    ${proposal.description}`);
    if (proposal.target === "system_prompt") {
      const edit = source ? proposedSource(source, proposal) : { skip: `${RULE_FILE} not found` };
      if (edit.skip) {
        console.log(`    ${S.dim}No diff: ${edit.skip}.${S.reset}`);
      } else if (edit.describe) {
        console.log(`    ${S.cyan}Proposal (description only):${S.reset} ${edit.describe}`);
        proposals.push({ ...proposal, insight: insight.type, severity: insight.severity, kind: "description" });
      } else {
        console.log(`    ${S.dim}Proposed diff (full text; not applied):${S.reset}`);
        console.log(unifiedDiff(RULE_FILE, source, edit.after));
        proposals.push({ ...proposal, insight: insight.type, severity: insight.severity, kind: "diff" });
      }
    } else {
      console.log(`    ${S.cyan}Proposal (description only):${S.reset} ${proposal.change}`);
      proposals.push({ ...proposal, insight: insight.type, severity: insight.severity, kind: "description" });
    }
    console.log();
  }

  const diffs = proposals.filter(p => p.kind === "diff").length;
  const described = proposals.length - diffs;
  console.log(`${S.dim}${diffs} proposed diff(s) and ${described} described proposal(s). Nothing was written: to adopt one, edit the file by hand, review it and commit it.${S.reset}`);
  if (diffs > 1) {
    console.log(`${S.dim}Each diff is drawn against the unmodified ${RULE_FILE}, so after one is applied the others no longer apply as printed: run transmute again for fresh diffs.${S.reset}`);
  }
  if (diffs > 0) {
    console.log(`${S.dim}A diff of ${RULE_FILE} reaches every prompt built in code; the eleven instances/*/CLAUDE.md copies of the rule are separate text that it does not change.${S.reset}`);
  }
  return proposals;
}

// ═════════════════════════════════════════════════════════════════════
// CYCLE — reflect → distill → propose
// ═════════════════════════════════════════════════════════════════════

function cycle() {
  console.log(`\n${S.bold}${S.magenta}═══ OUROBOROS CYCLE (proposal-only) ═══${S.reset}`);
  console.log(`${S.dim}LIVE → SENSE → REFLECT → DISTILL → PROPOSE → a person reviews → LIVE${S.reset}\n`);

  console.log(`${S.bold}1. REFLECT${S.reset}`);
  reflect();

  console.log(`\n${S.bold}2. DISTILL + PROPOSE${S.reset}`);
  transmute();
}

// ═════════════════════════════════════════════════════════════════════
// HISTORY — the store's latest sessions, and the legacy record
// ═════════════════════════════════════════════════════════════════════

function showHistory() {
  const store = loadStore();

  console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK History ═══${S.reset}\n`);
  console.log(`${S.bold}Sessions in the store: ${store.rows.length}${S.reset}`);
  for (const s of store.rows.slice(-10)) {
    // The useful ratio is an estimate ("~"); a schema 1 row's grade is not shown (L1 is no longer graded).
    const useful = typeof s.usefulRatio === "number" ? ` useful ~${Math.round(s.usefulRatio * 100)}%` : " useful —";
    const rl = s.rateLimitHits ? ` ${S.yellow}429×${s.rateLimitHits}${S.reset}` : "";
    const sig = s.signalCount > 0 ? ` ⚡${s.signalCount}` : "";
    console.log(`  ${S.dim}${day(s)}${S.reset} ${s.route || "?"} ${s.model || "?"} ${s.turns}t ${s.toolCalls}tc${useful}${rl}${sig}`);
  }

  const legacy = readLegacyHistory();
  if (legacy) {
    console.log(`\n${S.bold}Legacy record${S.reset} ${S.dim}(youspeak-history.json, tracked, read only)${S.reset}`);
    console.log(`  ${legacy.rows} rows${legacy.span ? `, ${legacy.span}` : ""}; ${legacy.note}.`);
    console.log(`  ${legacy.mutations} mutation record(s). The June 2026 entries marked "applied" in youspeak-config.json`);
    console.log(`  never took effect: nothing reads that file. Only add_cache_reminder reached a prompt: sovereign's, as the`);
    console.log(`  file-cache line youspeak-rule.mjs now keeps for sovereign alone (YOUSPEAK_FILE_CACHE_LINE).`);
  }
}

// ═════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════

const cmd = process.argv[2];

switch (cmd) {
  case "reflect":   reflect(); break;
  case "distill":   distill(); break;
  case "transmute":
  case "propose":   transmute(); break;
  case "cycle":     cycle(); break;
  case "history":   showHistory(); break;
  case "integrate":
    console.log(`${S.yellow}integrate was removed: the ouroboros is proposal-only and never writes source, config or history.${S.reset}`);
    console.log(`${S.dim}Run \`node youspeak-evolve.mjs transmute\` to see proposed diffs; adopt one by editing, reviewing and committing it.${S.reset}`);
    process.exitCode = 2;
    break;
  case "sense":
    console.log(`${S.yellow}sense was removed: each session writes its own row to the store when it ends.${S.reset}`);
    process.exitCode = 2;
    break;
  default:
    console.log(`
${S.bold}youspeak-evolve.mjs${S.reset} — the ouroboros, proposal-only

${S.dim}LIVE → SENSE → REFLECT → DISTILL → PROPOSE → a person reviews → LIVE${S.reset}

Commands:
  ${S.cyan}reflect${S.reset}             Trends across sessions in the store
  ${S.cyan}distill${S.reset}             Insights
  ${S.cyan}transmute${S.reset}           Insights, then proposed diffs in full (alias: propose)
  ${S.cyan}cycle${S.reset}               reflect → distill → propose
  ${S.cyan}history${S.reset}             The store's latest sessions, and the legacy record

It reads ${defaultHistoryPath()}
(YOUSPEAK_HISTORY_PATH overrides) and writes nothing.
`);
}
