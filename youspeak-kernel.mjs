#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// YOUSPEAK KERNEL — the Discipline's observer
//
// YOUSPEAK observes and suggests. It never acts: no caller applies what
// decide() returns (Yu, 2026-10-30: suggest only; docs/YOUSPEAK.md).
//
// Three functions:
//   SENSE  — Zero-cost estimates across 5 layers (no model calls)
//   DECIDE — Threshold signals, each with a suggestion for the person or agent
//   REPORT — Structured data for the page, the terminal and the ouroboros
//
// Five measurement layers (what each measures today is in docs/YOUSPEAK.md):
//   L1 OUTPUT   — English filler estimate (regex over chars/4)
//   L2 THINKING — Thinking tokens, where a caller reports them
//   L3 ACTION   — Tool call counts and repeated reads
//   L4 CONTEXT  — chars/4 estimate of the conversation against 1M
//   L5 SYSTEM   — Rate-limit state as reported, turns
//
// History: one row per session, appended when the session ends, to an
// untracked per-user store (default ~/.local/share/youi/youspeak/history.jsonl,
// or YOUSPEAK_HISTORY_PATH). Rows hold aggregates only, never message text,
// file paths or tool inputs. The tracked youspeak-history.json is legacy and
// only ever read.
//
// Import:
//   import { createKernel } from './youspeak-kernel.mjs';
//   const ys = createKernel({ agent, route, model });
//   ... ys.senseOutput(text) ... ys.endSession({ reason: "exit" });
//
// ─────────────────────────────────────────────────────────────────────

import {
  closeSync,
  existsSync,
  fchmodSync,
  fstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
  writeSync,
  constants as fsConstants,
} from "fs";
import { randomUUID } from "crypto";
import { dirname, join, resolve } from "path";
import { homedir } from "os";
import { fileURLToPath } from "url";

// The schema of one history row. A row with another schema is reported and skipped.
export const HISTORY_SCHEMA = 1;
// Routes a row may name.
export const ROUTES = Object.freeze(["claude-code", "ollama", "vllm", "terminal-legacy", "sovereign"]);
// The git-tracked file earlier versions wrote after every message. Read only, as legacy.
export const LEGACY_HISTORY_PATH = join(dirname(fileURLToPath(import.meta.url)), "youspeak-history.json");

/** Where the store lives: YOUSPEAK_HISTORY_PATH, else ~/.local/share/youi/youspeak/history.jsonl. */
export function defaultHistoryPath(env = process.env) {
  if (env.YOUSPEAK_HISTORY_PATH) return resolve(env.YOUSPEAK_HISTORY_PATH);
  return join(homedir(), ".local", "share", "youi", "youspeak", "history.jsonl");
}

// ═════════════════════════════════════════════════════════════════════
// L1 — OUTPUT: Filler estimate (English only; chars/4 is not a tokenizer)
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

/** The L1 estimate for one text: every field is a number, whatever the length. */
export function detectFiller(text) {
  const value = typeof text === "string" ? text : "";
  const totalTokens = Math.ceil(value.length / 4);
  const patterns = [];
  let totalWeight = 0;
  let fillerTokens = 0;

  if (value.length > 0) {
    for (const { re, name, w } of FILLER_PATTERNS) {
      re.lastIndex = 0;
      const matches = value.match(re);
      if (matches?.length) {
        patterns.push({ name, count: matches.length, weight: w });
        totalWeight += matches.length * w;
        fillerTokens += matches.length * 8; // ~8 tokens per filler phrase
      }
    }
  }

  return {
    count: patterns.reduce((s, p) => s + p.count, 0),
    // The flat 8 tokens a phrase can exceed a short text; filler is never more than the text.
    tokens: Math.min(fillerTokens, totalTokens),
    totalTokens,
    patterns,
    weight: totalWeight,
  };
}

function gradeRatio(ratio) {
  if (typeof ratio !== "number" || !Number.isFinite(ratio)) return null;
  if (ratio >= 0.98) return "S";  // Sovereign
  if (ratio >= 0.95) return "A";
  if (ratio >= 0.85) return "B";
  if (ratio >= 0.70) return "C";
  return "D";
}

// ═════════════════════════════════════════════════════════════════════
// STORE — one JSON line per session, appended; never rewritten
// ═════════════════════════════════════════════════════════════════════

function isValidRow(row) {
  return row && typeof row === "object" && !Array.isArray(row)
    && row.schema === HISTORY_SCHEMA
    && typeof row.session === "string" && row.session.length > 0;
}

/**
 * Reads the store. Lines that are not a valid row (corrupt, partial, or of
 * another schema) are counted and reported in `warnings`, and left in place:
 * nothing here, or anywhere in YOUSPEAK, rewrites the file.
 */
export function readHistory(path = defaultHistoryPath()) {
  const result = { path, exists: false, rows: [], lines: 0, skipped: 0, warnings: [] };
  if (!path || !existsSync(path)) return result;
  result.exists = true;
  let text;
  try {
    text = readFileSync(path, "utf-8");
  } catch (error) {
    result.warnings.push(`The YOUSPEAK store could not be read (${error.code || error.message}); nothing was changed.`);
    return result;
  }
  const lines = text.split("\n");
  const partialTail = text.length > 0 && !text.endsWith("\n");
  let tailBroken = false;
  let otherSchema = 0;
  lines.forEach((line, index) => {
    if (!line.trim()) return;
    result.lines += 1;
    let row = null;
    try { row = JSON.parse(line); } catch {}
    if (isValidRow(row)) {
      result.rows.push(row);
      return;
    }
    result.skipped += 1;
    if (row && typeof row === "object" && row.schema !== undefined && row.schema !== HISTORY_SCHEMA) otherSchema += 1;
    if (partialTail && index === lines.length - 1) tailBroken = true;
  });
  if (result.skipped > 0) {
    const parts = [`${result.skipped} of ${result.lines} lines in the YOUSPEAK store could not be read and were skipped`];
    if (tailBroken) parts.push("the last line is incomplete (a write was cut short)");
    if (otherSchema > 0) parts.push(`${otherSchema} have a schema other than ${HISTORY_SCHEMA}`);
    result.warnings.push(`${parts.join("; ")}. They are left in place; nothing was reset.`);
  }
  return result;
}

/**
 * Appends one row as one line: a single write with O_APPEND, so concurrent
 * writers never interleave inside a line and no writer reads and rewrites the
 * file. The directory is created 0700 and the file 0600. If the file ends in
 * a partial line (a write cut short), the row starts on a new line instead of
 * being glued to it.
 */
export function appendHistoryRow(path, row) {
  if (!path) throw new Error("no YOUSPEAK store path");
  if (!isValidRow(row)) throw new Error("not a YOUSPEAK history row");
  const dir = dirname(path);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true, mode: 0o700 });
  const flags = fsConstants.O_RDWR | fsConstants.O_APPEND | fsConstants.O_CREAT;
  const fd = openSync(path, flags, 0o600);
  try {
    const stat = fstatSync(fd);
    if ((stat.mode & 0o777) !== 0o600) fchmodSync(fd, 0o600);
    let prefix = "";
    if (stat.size > 0) {
      const last = Buffer.alloc(1);
      readSync(fd, last, 0, 1, stat.size - 1);
      if (last[0] !== 0x0a) prefix = "\n";
    }
    const bytes = Buffer.from(`${prefix}${JSON.stringify(row)}\n`, "utf-8");
    const written = writeSync(fd, bytes);
    if (written !== bytes.length) throw new Error(`short write to the YOUSPEAK store (${written} of ${bytes.length} bytes)`);
  } finally {
    closeSync(fd);
  }
}

/** A read-only summary of the tracked legacy file, or null when there is none. */
export function readLegacyHistory(path = LEGACY_HISTORY_PATH) {
  if (!path || !existsSync(path)) return null;
  try {
    const data = JSON.parse(readFileSync(path, "utf-8"));
    const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
    const mutations = Array.isArray(data?.mutations) ? data.mutations : [];
    const first = sessions[0]?.timestamp?.split("T")[0] || null;
    const last = sessions.at(-1)?.timestamp?.split("T")[0] || null;
    return {
      rows: sessions.length,
      mutations: mutations.length,
      span: first && last ? `${first} → ${last}` : null,
      note: "legacy: cumulative per-message snapshots with no session id, read only; not comparable with the store",
    };
  } catch (error) {
    return { rows: 0, mutations: 0, span: null, note: `legacy file unreadable (${error.message}); left as it is` };
  }
}

/** Cross-session trends from store rows (one row per session), or null with fewer than two. */
export function trendsFromRows(rows, n = 20) {
  const recent = rows.filter(row => typeof row.usefulRatio === "number").slice(-n);
  if (recent.length < 2) return null;
  const avg = (arr, key) => arr.reduce((s, e) => s + (Number(e[key]) || 0), 0) / arr.length;
  const mid = Math.floor(recent.length / 2);
  const early = recent.slice(0, mid);
  const late = recent.slice(mid);
  const day = row => (row.endedAt || row.startedAt || "").split("T")[0] || "?";
  return {
    source: "store",
    sessions: recent.length,
    span: `${day(recent[0])} → ${day(recent.at(-1))}`,
    avgUsefulRatio: +avg(recent, "usefulRatio").toFixed(2),
    avgThinkRatio: +avg(recent, "thinkAvgRatio").toFixed(2),
    avgRedundantReads: +avg(recent, "redundantReads").toFixed(1),
    avgToolErrors: +avg(recent, "toolErrors").toFixed(1),
    fillerTrend: {
      early: +avg(early, "usefulRatio").toFixed(2),
      late: +avg(late, "usefulRatio").toFixed(2),
      direction: avg(late, "usefulRatio") > avg(early, "usefulRatio") ? "improving"
        : avg(late, "usefulRatio") < avg(early, "usefulRatio") ? "regressing" : "steady",
    },
    rateLimitTrend: {
      early: +avg(early, "rateLimitHits").toFixed(1),
      late: +avg(late, "rateLimitHits").toFixed(1),
    },
  };
}

// Who a suggestion names: "Alpha" for agent "alpha", else "the agent".
function agentName(agent) {
  return typeof agent === "string" && /^[a-z]+$/.test(agent) && agent !== "unknown"
    ? agent[0].toUpperCase() + agent.slice(1)
    : "the agent";
}

// ═════════════════════════════════════════════════════════════════════
// KERNEL FACTORY
// ═════════════════════════════════════════════════════════════════════

/**
 * opts.agent, opts.route (one of ROUTES), opts.model: what the row names.
 * opts.historyPath: the store; omitted → defaultHistoryPath(), null → no store
 * (endSession writes nothing). opts.legacyPath: the tracked legacy file read
 * for storeStatus(); null → none.
 */
export function createKernel(opts = {}) {
  const historyPath = opts.historyPath === undefined ? defaultHistoryPath() : opts.historyPath;
  const legacyPath = opts.legacyPath === undefined ? LEGACY_HISTORY_PATH : opts.legacyPath;
  const now = typeof opts.now === "function" ? opts.now : () => Date.now();

  // ─── Accumulated session state ──────────────────────────
  const session = {
    id: randomUUID(),
    startedAt: now(),
    lastActivityAt: now(),
    agent: opts.agent || "unknown",
    route: ROUTES.includes(opts.route) ? opts.route : null,
    model: typeof opts.model === "string" ? opts.model : null,
    // Turns per route and per model, as callers report them (noteRoute).
    routes: {},
    models: {},
    // Messages that ended with an answer, or with only an error (noteExchange).
    exchanges: { ok: 0, failed: 0 },
    // Signal keys (type:action) seen this session, and those already suggested.
    signalKeys: new Set(),
    suggested: new Set(),
    ended: null,

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
      readPaths: new Set(),  // kept in memory only, never written
      redundantReads: 0,     // same path read twice
      toolErrors: 0,
    },

    // L4 Context
    context: {
      messagesCount: 0,
      estimatedTokens: 0,   // rough estimate of context window usage
      peakTokens: 0,
      systemPromptTokens: 0,
      oldestToolResultAge: 0, // messages since the oldest tool result
    },

    // L5 System
    system: {
      turns: 0,
      rateLimitHits: 0,
      budgetAtStart: null,
      budgetNow: null,
    },

    // DECIDE signals from the latest decide()
    signals: [],
  };

  function touch() { session.lastActivityAt = now(); }

  // ─── ROUTE / EXCHANGE: what the row says about the session ─────
  function noteRoute(route, model) {
    const r = ROUTES.includes(route) ? route : null;
    if (r) session.routes[r] = (session.routes[r] || 0) + 1;
    if (typeof model === "string" && model) session.models[model] = (session.models[model] || 0) + 1;
    touch();
  }

  function noteExchange({ ok }) {
    if (ok) session.exchanges.ok++;
    else session.exchanges.failed++;
    touch();
  }

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
    touch();

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
  function senseThinking(usage = {}) {
    const thinkTok = Number(usage?.thinking_tokens) || 0;
    const outTok = Number(usage?.output_tokens) || 0;
    session.thinking.totalTokens += thinkTok;

    const ratio = outTok > 0 ? +(thinkTok / outTok).toFixed(2) : 0;
    const entry = { thinking: thinkTok, output: outTok, ratio };
    session.thinking.perTurn.push(entry);
    touch();

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
    touch();

    return { name, redundant };
  }

  // ─── L4: SENSE CONTEXT ────────────────────────────────
  function senseContext(messages, systemPromptLength) {
    session.context.messagesCount = messages.length;
    session.context.systemPromptTokens = Math.ceil((systemPromptLength || 0) / 4);

    // Estimate total context tokens
    let est = session.context.systemPromptTokens;
    let oldestToolResult = -1;
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
            if (oldestToolResult < 0) oldestToolResult = turnIndex;
          }
          else if (block.type === "thinking") est += Math.ceil((block.thinking?.length || 0) / 4);
        }
      }
      turnIndex++;
    }

    session.context.estimatedTokens = est;
    session.context.peakTokens = Math.max(session.context.peakTokens, est);
    session.context.oldestToolResultAge = oldestToolResult >= 0 ? messages.length - oldestToolResult : 0;
    touch();

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
    touch();
  }

  function senseRateLimit() {
    session.system.rateLimitHits++;
    touch();
  }

  // ═══════════════════════════════════════════════════════
  // DECIDE — Threshold signals (no LLM calls). Suggestions only:
  // each signal says what the person or the agent could do, and no
  // caller applies it.
  // ═══════════════════════════════════════════════════════

  function decide(currentEffort, _currentModel, budget) {
    const signals = [];
    const who = agentName(session.agent);

    // ── Effort, under rate-limit pressure (as reported) ──
    const bu5 = budget?.fiveHour?.utilization || 0;
    if (bu5 > 0.85 && currentEffort === "max") {
      signals.push({ type: "effort", action: "reduce", from: "max", to: "high",
        reason: `Budget 5h at ${(bu5*100).toFixed(0)}%`,
        suggestion: `Nothing was changed. You could lower effort from max to high in Settings to stretch the 5-hour window; ${who} keeps max until you do.` });
    }
    if (bu5 > 0.95 && (currentEffort === "max" || currentEffort === "high")) {
      signals.push({ type: "effort", action: "reduce", from: currentEffort, to: "medium",
        reason: `Budget 5h at ${(bu5*100).toFixed(0)}%, close to the limit`,
        suggestion: `Nothing was changed. You could lower effort from ${currentEffort} to medium in Settings, or pause until the window resets; ${who} keeps ${currentEffort} until you do.` });
    }

    // (The L2 rule that suggested a cheaper model from the thinking/output
    // ratio is gone: nothing reported thinking tokens, so it always fired.)

    // ── Context window pressure (chars/4 estimate against 1M) ──
    if (session.context.estimatedTokens > 800_000) {
      signals.push({ type: "context", action: "prune_recommended",
        reason: `Context at ~${Math.round(session.context.estimatedTokens/1000)}k tokens (estimate), approaching 1M`,
        utilization: session.context.estimatedTokens / 1_000_000,
        suggestion: `Nothing was pruned. You could start a new conversation (Clear), or ask ${who} to summarise what matters so far.` });
    }
    if (session.context.estimatedTokens > 500_000 && session.context.oldestToolResultAge > 20) {
      signals.push({ type: "context", action: "evict_old_results",
        reason: `${session.context.oldestToolResultAge} messages since the oldest tool result; old results are piling up`,
        suggestion: `Nothing was pruned. You could start a new conversation (Clear) once ${who} has noted what it still needs.` });
    }

    // ── Redundant tool calls ──
    if (session.action.redundantReads > 2) {
      signals.push({ type: "action", action: "redundant_reads",
        count: session.action.redundantReads,
        reason: `${session.action.redundantReads} repeated file reads`,
        suggestion: `${who} could rely on files it has already read this session unless they changed.` });
    }

    // ── Tool error rate ──
    if (session.action.toolCalls > 5 && session.action.toolErrors / session.action.toolCalls > 0.3) {
      signals.push({ type: "action", action: "high_error_rate",
        rate: +(session.action.toolErrors / session.action.toolCalls).toFixed(2),
        reason: `${session.action.toolErrors}/${session.action.toolCalls} tool calls errored`,
        suggestion: `${who} could stop and re-check the approach; you could restate the goal.` });
    }

    // ── Output filler trend (English-only estimate) ──
    if (session.output.grades.length >= 5) {
      const recent = session.output.grades.slice(-5);
      const badCount = recent.filter(g => g === "C" || g === "D").length;
      if (badCount >= 3) {
        signals.push({ type: "output", action: "filler_regression",
          reason: `${badCount}/5 recent text blocks grade C or D (English-only estimate)`,
          suggestion: `${who} could drop preamble and narration and say what matters.` });
      }
    }

    // ── Rate limit frequency ──
    if (session.system.rateLimitHits >= 2 && session.system.turns > 0) {
      signals.push({ type: "system", action: "rate_limit_pressure",
        hits: session.system.rateLimitHits, turns: session.system.turns,
        reason: `${session.system.rateLimitHits} rate limits in ${session.system.turns} turns`,
        suggestion: "You could pause, or lower effort, until the window resets." });
    }

    for (const signal of signals) session.signalKeys.add(`${signal.type}:${signal.action}`);
    session.signals = signals;
    return signals;
  }

  /** The signals not yet suggested this session (each type:action[:to] once), marked as suggested. */
  function newSuggestions(signals) {
    const fresh = [];
    for (const signal of signals || []) {
      const key = `${signal.type}:${signal.action}${signal.to ? `:${signal.to}` : ""}`;
      if (session.suggested.has(key)) continue;
      session.suggested.add(key);
      fresh.push(signal);
    }
    return fresh;
  }

  // ═══════════════════════════════════════════════════════
  // REPORT — Structured metrics for dashboards
  // ═══════════════════════════════════════════════════════

  function report() {
    const elapsed = now() - session.startedAt;
    const elapsedMin = +(elapsed / 60000).toFixed(1);

    // L1 Output: no text, no grade.
    const usefulRatio = session.output.totalTokens > 0
      ? Math.max(0, (session.output.totalTokens - session.output.fillerTokens) / session.output.totalTokens)
      : null;
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
      session: session.id,
      agent: session.agent,
      elapsed: elapsedMin,
      turns: session.system.turns,

      // L1 Output
      output: {
        usefulRatio: usefulRatio === null ? null : Math.round(usefulRatio * 100) / 100,
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
        peakTokens: session.context.peakTokens,
        windowUtilization: +(session.context.estimatedTokens / 1_000_000).toFixed(4),
        messagesCount: session.context.messagesCount,
        systemPromptTokens: session.context.systemPromptTokens,
      },

      // L5 System
      system: {
        budgetBurned: budgetBurned !== null ? `${budgetBurned}%` : null,
        budgetNow: session.system.budgetNow,
        rateLimitHits: session.system.rateLimitHits,
        tokensPerTurn,
      },

      // Signals (suggestions only)
      signals: session.signals,
    };
  }

  function countGrades(arr) {
    const d = {};
    arr.forEach(g => { if (g) d[g] = (d[g] || 0) + 1; });
    return d;
  }

  function hasActivity() {
    return session.system.turns > 0 || session.output.textBlocks > 0
      || session.action.toolCalls > 0 || session.exchanges.ok + session.exchanges.failed > 0;
  }

  // The route or model used for the most turns (the latest wins a tie), else the one given at creation.
  function primary(counts, fallback) {
    let best = fallback;
    let bestCount = 0;
    for (const [key, count] of Object.entries(counts)) {
      if (count >= bestCount) { best = key; bestCount = count; }
    }
    return best;
  }

  /** The row this session would write: aggregates only. */
  function row(reason = null) {
    const r = report();
    return {
      schema: HISTORY_SCHEMA,
      session: session.id,
      route: primary(session.routes, session.route),
      model: primary(session.models, session.model),
      agent: session.agent,
      routes: { ...session.routes },
      models: { ...session.models },
      startedAt: new Date(session.startedAt).toISOString(),
      endedAt: new Date(now()).toISOString(),
      endReason: reason,
      elapsed: r.elapsed,
      turns: r.turns,
      exchanges: { ...session.exchanges },
      outputGrade: r.output.grade,
      usefulRatio: r.output.usefulRatio,
      outputTokensEstimate: r.output.totalTokens,
      fillerTokensEstimate: r.output.fillerTokens,
      textBlocks: r.output.textBlocks,
      thinkingTokens: r.thinking.totalTokens,
      thinkAvgRatio: r.thinking.avgRatio,
      toolCalls: r.action.totalCalls,
      redundantReads: r.action.redundantReads,
      toolErrors: r.action.errors,
      contextPeakTokens: session.context.peakTokens,
      budgetBurned: r.system.budgetBurned,
      rateLimitHits: r.system.rateLimitHits,
      signalCount: session.signalKeys.size,
      signals: [...session.signalKeys].sort(),
    };
  }

  // ═══════════════════════════════════════════════════════
  // END — one row per session, written once, when the session ends
  // ═══════════════════════════════════════════════════════

  /**
   * Ends this session: appends its row to the store unless it produced no
   * output or only failed. At most once per kernel; later calls do nothing.
   * Returns { written, skipped, error, row }.
   */
  function endSession({ reason = null, route, model } = {}) {
    if (session.ended) return { written: false, skipped: "already ended", error: null, row: null };
    if (route && !Object.keys(session.routes).length && ROUTES.includes(route)) session.route = route;
    if (model && !Object.keys(session.models).length) session.model = model;
    let outcome;
    if (session.output.textBlocks === 0 && session.action.toolCalls === 0) {
      outcome = { written: false, skipped: "no output", error: null, row: null };
    } else if (session.exchanges.ok === 0 && session.exchanges.failed > 0) {
      outcome = { written: false, skipped: "only failed", error: null, row: null };
    } else if (!historyPath) {
      outcome = { written: false, skipped: "no store", error: null, row: null };
    } else {
      const entry = row(reason);
      try {
        appendHistoryRow(historyPath, entry);
        outcome = { written: true, skipped: null, error: null, row: entry };
      } catch (error) {
        outcome = { written: false, skipped: null, error: error.message, row: entry };
      }
    }
    session.ended = { at: now(), reason, ...outcome };
    return outcome;
  }

  // ═══════════════════════════════════════════════════════
  // TRENDS — Cross-session analysis from the store
  // ═══════════════════════════════════════════════════════

  function trends(n = 20) {
    if (!historyPath) return null;
    return trendsFromRows(readHistory(historyPath).rows, n);
  }

  /** What the store holds and anything wrong with it; the legacy file is summarised separately. */
  function storeStatus() {
    const store = historyPath ? readHistory(historyPath) : null;
    return {
      enabled: Boolean(historyPath),
      rows: store ? store.rows.length : 0,
      warnings: store ? store.warnings : [],
      legacy: readLegacyHistory(legacyPath),
    };
  }

  // ─── COMPACT STATUS LINE ───────────────────────────────
  function statusLine() {
    const r = report();
    const g = r.output.grade ?? "—";
    const ur = r.output.usefulRatio === null ? "—" : `${Math.round(r.output.usefulRatio * 100)}%`;
    const tr = r.thinking.avgRatio;
    const tc = r.action.totalCalls;
    const rr = r.action.redundantReads;
    const ctx = Math.round(r.context.estimatedTokens / 1000);
    const sig = r.signals.length;
    return `YS:${g}(${ur}) think:${tr}x tools:${tc}${rr > 0 ? ` dup:${rr}` : ""} ctx:${ctx}k${sig > 0 ? ` ⚡${sig}` : ""}`;
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
    noteRoute,
    noteExchange,
    // DECIDE (suggestions only)
    decide,
    newSuggestions,
    // REPORT
    report,
    statusLine,
    trends,
    storeStatus,
    // END
    row,
    endSession,
    hasActivity,
    get lastActivityAt() { return session.lastActivityAt; },
    get ended() { return session.ended; },
    historyPath,
    // Direct access
    session,
  };
}

// ═════════════════════════════════════════════════════════════════════
// CLI — Direct invocation for trends (read only)
// ═════════════════════════════════════════════════════════════════════

if (process.argv[1]?.endsWith("youspeak-kernel.mjs")) {
  const cmd = process.argv[2];
  const S = { reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
    red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m",
    cyan: "\x1b[36m", magenta: "\x1b[35m" };

  if (cmd === "trends" || cmd === "history") {
    const store = readHistory(defaultHistoryPath());
    for (const warning of store.warnings) console.log(`${S.yellow}⚠ ${warning}${S.reset}`);
    const t = trendsFromRows(store.rows, parseInt(process.argv[3]) || 20);
    if (!t) {
      console.log(`${S.dim}No session history in the store yet (${store.rows.length} row${store.rows.length === 1 ? "" : "s"}).${S.reset}`);
    } else {
      console.log(`\n${S.bold}${S.cyan}═══ YOUSPEAK TRENDS ═══${S.reset}`);
      console.log(`${S.dim}${t.span} (${t.sessions} sessions, one row each)${S.reset}\n`);
      console.log(`  Useful ratio:     ${S.bold}${t.avgUsefulRatio >= 0.9 ? S.green : t.avgUsefulRatio >= 0.8 ? S.yellow : S.red}${Math.round(t.avgUsefulRatio*100)}%${S.reset} ${S.dim}(English-only estimate)${S.reset}`);
      console.log(`  Think/output:     ${t.avgThinkRatio}x avg`);
      console.log(`  Redundant reads:  ${t.avgRedundantReads}/session avg`);
      console.log(`  Tool errors:      ${t.avgToolErrors}/session avg`);
      console.log();
      const dir = t.fillerTrend.direction === "improving" ? S.green + "↑ improving"
        : t.fillerTrend.direction === "regressing" ? S.red + "↓ regressing" : S.dim + "─ steady";
      console.log(`  Filler trend:     early ${Math.round(t.fillerTrend.early*100)}% → late ${Math.round(t.fillerTrend.late*100)}% ${dir}${S.reset}`);
      console.log(`  Rate limits:      early ${t.rateLimitTrend.early}/session → late ${t.rateLimitTrend.late}/session`);
      console.log();
    }
    const legacy = readLegacyHistory();
    if (legacy) {
      console.log(`${S.dim}Legacy youspeak-history.json (read only): ${legacy.rows} rows${legacy.span ? `, ${legacy.span}` : ""}; ${legacy.note}.${S.reset}`);
    }

  } else {
    console.log(`
${S.bold}YOUSPEAK KERNEL${S.reset} — the Discipline's observer (suggests, never acts)

Usage:
  ${S.cyan}node youspeak-kernel.mjs trends${S.reset}     Show cross-session trends from the store
  ${S.cyan}node youspeak-kernel.mjs trends 50${S.reset}  Show trends for the last 50 sessions

Store: ${defaultHistoryPath()} (one row per session; YOUSPEAK_HISTORY_PATH overrides)

Import:
  ${S.cyan}import { createKernel } from './youspeak-kernel.mjs';${S.reset}
  ${S.cyan}const ys = createKernel({ agent, route, model });${S.reset}

Five measurement layers (estimates; see docs/YOUSPEAK.md):
  L1 OUTPUT   — English filler estimate, useful ratio
  L2 THINKING — Thinking tokens where reported
  L3 ACTION   — Tool calls, repeated reads
  L4 CONTEXT  — chars/4 estimate against 1M
  L5 SYSTEM   — Rate-limit state as reported
`);
  }
}
