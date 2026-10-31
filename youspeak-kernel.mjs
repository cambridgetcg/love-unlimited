#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────
// YOUSPEAK KERNEL — the Discipline's observer
//
// YOUSPEAK observes and suggests. It never acts: no caller applies what
// decide() returns (Yu, 2026-10-30: suggest only; docs/YOUSPEAK.md).
//
// Three functions:
//   SENSE  — What the provider reports, and YOUI's own estimates (no model calls)
//   DECIDE — Threshold signals, each with a suggestion for the person or agent
//   REPORT — Structured data for the page, the terminal and the ouroboros
//
// Every figure carries an evidential label (EVIDENCE): "reported" when the
// provider stated it (Claude Code's usage, cost, context window and rate-limit
// state; Ollama's and vLLM's token counts), "estimated" when YOUI computed it
// itself (the L1 filler estimate, chars/4, its own counts, a price table, a
// window from its own table). A figure built from an estimate is an estimate:
// a label may understate, never overstate. A figure nobody reported is null,
// never 0.
//
// What it senses (docs/YOUSPEAK.md §2):
//   USAGE       — Reported input, cache read, cache creation, output, thinking, cost per turn
//   L1 OUTPUT   — English filler estimate (regex over chars/4)
//   L2 THINKING — Thinking tokens, only where the provider reports them
//   L3 ACTION   — Tool call counts and repeated reads
//   L4 CONTEXT  — The latest request's input + cache (reported), or chars/4
//                 (estimated), against the model's window where known
//   L5 SYSTEM   — Rate-limit state as reported, turns
//   CACHE       — Whether the cached system prompt changed within a conversation
//   MINDS       — Other minds' calls, tokens and cost, apart from the agent's own
//
// History: one row per session, appended when the session ends, to an
// untracked per-user store (default ~/.local/share/youi/youspeak/history.jsonl,
// or YOUSPEAK_HISTORY_PATH). Rows hold aggregates only, never message text,
// file paths or tool inputs. The tracked youspeak-history.json is legacy and
// only ever read.
//
// Import:
//   import { createKernel } from './youspeak-kernel.mjs';
//   const ys = createKernel({ agent, route, model, surface });
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
  statSync,
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
// Routes with no Claude effort setting and no Claude 5-hour window: YOUI's own
// loop for Ollama and vLLM models (sovereign on Ollama passes "ollama" to decide()).
const OPEN_MODEL_ROUTES = Object.freeze(["ollama", "vllm"]);
// Where a suggestion is shown, which decides the remedy it names.
export const SURFACES = Object.freeze(["web", "terminal", "sovereign"]);
// The two evidential labels every figure carries.
export const EVIDENCE = Object.freeze({ reported: "reported", estimated: "estimated" });
const EVIDENCE_LABELS = Object.values(EVIDENCE);
// The context windows YOUI knows for its Claude models, used only when the
// provider has not reported the model's own (Claude Code's
// modelUsage.contextWindow); a window from this table is an estimate. Any other
// model's window is unknown (null), and no context signal is raised for it.
export const KNOWN_CONTEXT_WINDOWS = Object.freeze({
  "claude-opus-5-5": 1_000_000,
  "claude-opus-4-7": 1_000_000,
  "claude-opus-4-6": 1_000_000,
  "claude-sonnet-4-6": 1_000_000,
  "claude-haiku-5-5": 1_000_000,
  "claude-haiku-4-5-20251001": 200_000,
});
// The share of a known window at which the context signals fire.
export const CONTEXT_PRUNE_SHARE = 0.8;
export const CONTEXT_EVICT_SHARE = 0.5;
// One tolerance for "improving" or "regressing", shared with youspeak-evolve.mjs:
// a change in the useful ratio within one percentage point is steady.
export const TREND_TOLERANCE = 0.01;
// L1 grades a text block only when it has at least this many estimated tokens
// (chars/4, so 80 characters) and is not mostly non-ASCII.
export const MIN_GRADED_TOKENS = 20;
// The git-tracked file earlier versions wrote after every message. Read only, as legacy.
export const LEGACY_HISTORY_PATH = join(dirname(fileURLToPath(import.meta.url)), "youspeak-history.json");

/** The weaker of two labels (an estimate wins); null when neither has a figure. */
export function weakerEvidence(a, b) {
  if (!a) return b || null;
  if (!b) return a;
  return a === EVIDENCE.estimated || b === EVIDENCE.estimated ? EVIDENCE.estimated : EVIDENCE.reported;
}

/** The window YOUI knows for a model, or null. */
export function knownContextWindow(model) {
  return typeof model === "string" && Object.hasOwn(KNOWN_CONTEXT_WINDOWS, model) ? KNOWN_CONTEXT_WINDOWS[model] : null;
}

const isNumber = value => typeof value === "number" && Number.isFinite(value);

/** Where the store lives: YOUSPEAK_HISTORY_PATH, else ~/.local/share/youi/youspeak/history.jsonl. */
export function defaultHistoryPath(env = process.env) {
  if (env.YOUSPEAK_HISTORY_PATH) return resolve(env.YOUSPEAK_HISTORY_PATH);
  return join(homedir(), ".local", "share", "youi", "youspeak", "history.jsonl");
}

// ═════════════════════════════════════════════════════════════════════
// L1 — OUTPUT: Filler estimate (English only; chars/4 is not a tokenizer)
// ═════════════════════════════════════════════════════════════════════

// An opener ("Sure, ...", "Okay. ...", "Perfect! ...") is filler only at the
// start of the text, a line or a sentence (ASCII or CJK sentence punctuation),
// so "make sure", "not sure", 「唔 sure」 and 「仲未 sure」 are not counted.
const SENTENCE_START = String.raw`(?<=(?:^|[.!?。！？])\s*)`;

const FILLER_PATTERNS = [
  // Preamble
  { re: new RegExp(`${SENTENCE_START}(sure|okay|alright|great|absolutely|certainly|of course)[!.,]?\\s`, "gim"), name: "affirmative", w: 1 },
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
  { re: new RegExp(`${SENTENCE_START}perfect[!.]?\\s`, "gim"), name: "perfect", w: 1 },
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

/**
 * Whether L1 grades this text block: at least MIN_GRADED_TOKENS estimated
 * tokens, and at least half of its non-space characters ASCII. A short reply,
 * or one mostly in Cantonese or another script, gets grade null: the English
 * patterns cannot judge it, and it is left out of the useful ratio and of the
 * filler_regression signal.
 */
export function isGradable(text) {
  const value = typeof text === "string" ? text : "";
  if (Math.ceil(value.length / 4) < MIN_GRADED_TOKENS) return false;
  let visible = 0;
  let nonAscii = 0;
  for (const ch of value) {
    if (/\s/.test(ch)) continue;
    visible++;
    if (ch.codePointAt(0) > 0x7f) nonAscii++;
  }
  return visible > 0 && nonAscii * 2 <= visible;
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

// The fields a row may carry, by type. A field may be absent or null; a field
// of the wrong type makes the row unreadable (it is reported and skipped), so
// trends and the ouroboros never meet one.
const ROW_STRING_FIELDS = ["route", "model", "agent", "startedAt", "endedAt", "closedAt", "endReason", "outputGrade", "budgetBurned",
  "rateLimitStatus"];
const ROW_NUMBER_FIELDS = ["elapsed", "turns", "usefulRatio", "outputTokensEstimate", "fillerTokensEstimate", "textBlocks",
  "gradedBlocks", "thinkingTokens", "thinkAvgRatio", "toolCalls", "redundantReads", "toolErrors", "contextPeakTokens",
  "rateLimitHits", "signalCount",
  // Y2: what the provider reported, the window, the cache and the minds.
  "usageTurns", "inputTokens", "cacheReadTokens", "cacheCreationTokens", "outputTokens", "costUsd", "contextWindow",
  "fiveHourUtilization", "sevenDayUtilization", "promptChanges", "cacheMisses",
  "mindCalls", "mindFailedCalls", "mindInputTokens", "mindCacheReadTokens", "mindCacheCreationTokens", "mindOutputTokens",
  "mindCostUsd"];
// `evidence` maps a row's figures to their labels ("reported" or "estimated").
const ROW_OBJECT_FIELDS = ["routes", "models", "exchanges", "evidence"];
const isPlainObject = value => Boolean(value) && typeof value === "object" && !Array.isArray(value);

function isRowShape(row) {
  return isPlainObject(row) && row.schema === HISTORY_SCHEMA && typeof row.session === "string" && row.session.length > 0;
}

function hasValidFields(row) {
  for (const key of ROW_STRING_FIELDS) if (row[key] != null && typeof row[key] !== "string") return false;
  for (const key of ROW_NUMBER_FIELDS) if (row[key] != null && !Number.isFinite(row[key])) return false;
  for (const key of ROW_OBJECT_FIELDS) if (row[key] != null && !isPlainObject(row[key])) return false;
  if (row.signals != null && !(Array.isArray(row.signals) && row.signals.every(name => typeof name === "string"))) return false;
  if (row.evidence != null && !Object.values(row.evidence).every(label => EVIDENCE_LABELS.includes(label))) return false;
  return true;
}

function isValidRow(row) {
  return isRowShape(row) && hasValidFields(row);
}

/** The day a row belongs to (YYYY-MM-DD), or "?"; never throws. */
export function rowDay(row) {
  const stamp = row?.endedAt || row?.startedAt;
  return typeof stamp === "string" && stamp ? stamp.split("T")[0] : "?";
}

/** "improving", "regressing" or "steady", from early and late useful ratios, within TREND_TOLERANCE. */
export function trendDirection(early, late) {
  const change = late - early;
  if (change > TREND_TOLERANCE) return "improving";
  if (change < -TREND_TOLERANCE) return "regressing";
  return "steady";
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
  let mistyped = 0;
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
    if (isRowShape(row)) mistyped += 1;
    else if (isPlainObject(row) && row.schema !== undefined && row.schema !== HISTORY_SCHEMA) otherSchema += 1;
    if (partialTail && index === lines.length - 1) tailBroken = true;
  });
  if (result.skipped > 0) {
    const parts = [`${result.skipped} of ${result.lines} lines in the YOUSPEAK store could not be read and were skipped`];
    if (tailBroken) parts.push("the last line is incomplete (a write was cut short)");
    if (otherSchema > 0) parts.push(`${otherSchema} have a schema other than ${HISTORY_SCHEMA}`);
    if (mistyped > 0) parts.push(`${mistyped} have a field of the wrong type`);
    result.warnings.push(`${parts.join("; ")}. They are left in place; nothing was reset.`);
  }
  return result;
}

// The last read of each store, kept while the file's size, mtime and inode are
// unchanged, so a finished turn's report does not re-read and re-parse it.
const historyCache = new Map();

/** readHistory(path), served from memory while the file is unchanged. Treat the result as read only. */
export function readHistoryCached(path = defaultHistoryPath()) {
  if (!path) return readHistory(path);
  let stamp;
  try {
    const stat = statSync(path);
    stamp = `${stat.size}:${stat.mtimeMs}:${stat.ino}`;
  } catch {
    historyCache.delete(path);
    return readHistory(path);
  }
  const cached = historyCache.get(path);
  if (cached?.stamp === stamp) return cached.result;
  const result = readHistory(path);
  historyCache.set(path, { stamp, result });
  return result;
}

/**
 * Appends one row as one line: a single write with O_APPEND, so concurrent
 * writers never interleave inside a line and no writer reads and rewrites the
 * file. A directory YOUI creates is 0700, and the file is 0600. The store must
 * be a regular file owned by this user: a symlink (O_NOFOLLOW), a device, a
 * FIFO or another user's file is refused with an error and left as it is. If
 * the file ends in a partial line (a write cut short), the row starts on a new
 * line instead of being glued to it.
 */
export function appendHistoryRow(path, row) {
  if (!path) throw new Error("no YOUSPEAK store path");
  if (!isValidRow(row)) throw new Error("not a YOUSPEAK history row");
  const dir = dirname(path);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true, mode: 0o700 });
  const flags = fsConstants.O_RDWR | fsConstants.O_APPEND | fsConstants.O_CREAT
    | fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK;
  const fd = openSync(path, flags, 0o600);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile()) throw new Error("the YOUSPEAK store is not a regular file; nothing was written");
    if (typeof process.getuid === "function" && stat.uid !== process.getuid()) {
      throw new Error("the YOUSPEAK store belongs to another user; nothing was written");
    }
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
  return {
    source: "store",
    sessions: recent.length,
    span: `${rowDay(recent[0])} → ${rowDay(recent.at(-1))}`,
    avgUsefulRatio: +avg(recent, "usefulRatio").toFixed(2),
    // Only sessions whose provider reported thinking; null when none did.
    avgThinkRatio: (() => {
      const reported = recent.filter(row => isNumber(row.thinkAvgRatio));
      return reported.length ? +avg(reported, "thinkAvgRatio").toFixed(2) : null;
    })(),
    avgRedundantReads: +avg(recent, "redundantReads").toFixed(1),
    avgToolErrors: +avg(recent, "toolErrors").toFixed(1),
    fillerTrend: {
      early: +avg(early, "usefulRatio").toFixed(2),
      late: +avg(late, "usefulRatio").toFixed(2),
      direction: trendDirection(avg(early, "usefulRatio"), avg(late, "usefulRatio")),
    },
    rateLimitTrend: {
      early: +avg(early, "rateLimitHits").toFixed(1),
      late: +avg(late, "rateLimitHits").toFixed(1),
    },
  };
}

// Who a suggestion names: "Alpha" for agent "alpha", else "the agent".
function agentName(agent) {
  return typeof agent === "string" && /^[a-z]+$/.test(agent) && agent !== "unknown" && agent !== "sovereign"
    ? agent[0].toUpperCase() + agent.slice(1)
    : "the agent";
}

// What a suggestion tells the person to do, in the words of the surface they
// are using: YOUI Web's Settings and Clear, the terminal's /effort and /switch
// (its /clear only clears the screen), sovereign's flags for the next run.
function remedies(surface, agent) {
  const who = agentName(agent);
  const id = typeof agent === "string" && /^[a-z]+$/.test(agent) && agent !== "unknown" ? agent : "<agent>";
  switch (surface) {
    case "terminal":
      return {
        lower: (_from, to) => `type /effort ${to}`,
        keeps: from => `${who} keeps ${from} until you do`,
        fresh: `type /switch ${id} to start a fresh conversation (/clear only clears the screen)`,
        pause: "pause, or lower effort with /effort, until the window resets",
        pauseOpen: "pause until the provider's rate limit resets",
      };
    case "sovereign":
      return {
        lower: (_from, to) => `run the next task with --effort ${to}`,
        keeps: from => `this run keeps ${from}`,
        fresh: "start the next run without --continue",
        pause: "stop the run with Ctrl+C (its state is saved) and resume it with --continue once the window resets",
        pauseOpen: "stop the run with Ctrl+C (its state is saved) and resume it with --continue once the provider's rate limit resets",
      };
    default:
      return {
        lower: (from, to) => `lower effort from ${from} to ${to} in Settings`,
        keeps: from => `${who} keeps ${from} until you do`,
        fresh: "start a new conversation (Clear)",
        pause: "pause, or lower effort in Settings, until the window resets",
        pauseOpen: "pause until the provider's rate limit resets",
      };
  }
}

/**
 * Ends a process's open YOUSPEAK session when the process exits: on the 'exit'
 * event, and on SIGHUP (a closed terminal window, a dropped SSH session) and
 * SIGTERM, which otherwise end Node without emitting 'exit'. Each signal exits
 * with 128 + its number (129, 143), so the 'exit' hook runs. end(reason) gets
 * "exit", "hangup" or "terminated" and must be idempotent, as endSession is.
 * The listeners stay installed (process.on, not once): a closed window under
 * zsh delivers SIGHUP twice within a millisecond, and with `once` the second
 * one met Node's default action and killed the process mid-write.
 * SIGKILL and power loss still end the process with nothing written.
 */
export function endSessionOnExit(end) {
  let reason = "exit";
  let stopping = false;
  process.once("exit", () => { try { end(reason); } catch {} });
  for (const [signal, code, why] of [["SIGHUP", 129, "hangup"], ["SIGTERM", 143, "terminated"]]) {
    process.on(signal, () => {
      if (stopping) return;
      stopping = true;
      reason = why;
      process.exit(code);
    });
  }
}

// ═════════════════════════════════════════════════════════════════════
// KERNEL FACTORY
// ═════════════════════════════════════════════════════════════════════

/**
 * opts.agent, opts.route (one of ROUTES), opts.model: what the row names.
 * opts.surface (one of SURFACES): where suggestions are shown, which decides
 * the remedy they name; omitted → "terminal" for route terminal-legacy,
 * "sovereign" for route sovereign, else "web".
 * opts.historyPath: the store; omitted → defaultHistoryPath(), null → no store
 * (endSession writes nothing). opts.legacyPath: the tracked legacy file read
 * for storeStatus(); null → none. opts.now: a clock, for tests.
 *
 * Timing: startedAt is the session's first activity (a message starting, or
 * the first thing sensed), not when the kernel was created; endedAt is its
 * last activity; elapsed is the span between them. When the session was cut
 * (an idle timeout, a clear, the process ending) is the row's closedAt.
 */
export function createKernel(opts = {}) {
  const historyPath = opts.historyPath === undefined ? defaultHistoryPath() : opts.historyPath;
  const legacyPath = opts.legacyPath === undefined ? LEGACY_HISTORY_PATH : opts.legacyPath;
  const now = typeof opts.now === "function" ? opts.now : () => Date.now();
  const route = ROUTES.includes(opts.route) ? opts.route : null;
  const surface = SURFACES.includes(opts.surface) ? opts.surface
    : route === "terminal-legacy" ? "terminal" : route === "sovereign" ? "sovereign" : "web";

  // A figure with its label: { value, evidence }, both null until something is sensed.
  const figure = () => ({ value: null, evidence: null });
  // Adds a number to a figure; anything that is not a number (not reported) adds nothing.
  const addTo = (target, value, evidence) => {
    if (!isNumber(value)) return;
    target.value = (target.value ?? 0) + value;
    target.evidence = weakerEvidence(target.evidence, evidence);
  };
  const labelOf = (value, evidence) => (value === null || value === undefined ? null : evidence);

  // ─── Accumulated session state ──────────────────────────
  const session = {
    id: randomUUID(),
    // Both null until the first activity (touch).
    startedAt: null,
    lastActivityAt: null,
    agent: opts.agent || "unknown",
    route,
    surface,
    model: typeof opts.model === "string" ? opts.model : null,
    // The model of the latest message (noteRoute), for its known window.
    lastModel: null,
    // Turns per route and per model, as callers report them (noteRoute), and the latest route.
    routes: {},
    models: {},
    lastRoute: null,
    // Messages that ended with an answer, or with only an error (noteExchange).
    exchanges: { ok: 0, failed: 0 },
    // Signal keys (type:action) seen this session, and those already suggested.
    signalKeys: new Set(),
    suggested: new Set(),
    ended: null,

    // USAGE — what the provider reported for the agent's own turns (senseUsage).
    // Input is the uncached part; cache read and cache creation are apart.
    usage: {
      turns: 0,
      input: figure(),
      cacheRead: figure(),
      cacheCreation: figure(),
      output: figure(),
      costUsd: figure(),
      last: null,            // the latest turn's figures, with their labels
    },

    // L1 Output. totalTokens counts every block; the useful ratio uses only
    // graded blocks (gradedTokens, and the filler found in them). All estimates.
    output: {
      totalTokens: 0,
      gradedTokens: 0,
      fillerTokens: 0,
      textBlocks: 0,
      gradedBlocks: 0,
      grades: [],            // one per block; null for a block L1 does not grade
    },

    // L2 Thinking: only turns whose provider reported thinking tokens.
    thinking: {
      totalTokens: null,
      evidence: null,
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

    // L4 Context: the latest figure, reported (Claude Code) or estimated (chars/4).
    context: {
      tokens: null,
      evidence: null,
      peakTokens: null,
      peakEvidence: null,
      window: null,
      windowEvidence: null,
      // chars/4 over the conversation YOUI holds (senseContext), and what it counted.
      estimatedTokens: null,
      messagesCount: 0,
      systemPromptTokens: 0,
      oldestToolResultAge: 0, // messages since the oldest tool result
    },

    // CACHE — the cached system prompt of the Claude Code conversation (senseSystemPrompt),
    // and the cache misses Claude reported (senseUsage).
    cache: {
      conversation: null,
      hash: null,
      parts: null,           // [{ name, hash }], kept in memory only, never written
      changes: 0,
      lastChange: null,      // { parts: [names] }
      changedThisTurn: false,
      misses: 0,
      missedTokens: 0,
      lastMiss: null,        // { type, missedTokens }, as reported
      missThisTurn: null,
    },

    // MINDS — calls to other minds (senseMind), apart from the agent's own turns.
    minds: {
      calls: 0,
      failed: 0,
      input: figure(),
      cacheRead: figure(),
      cacheCreation: figure(),
      output: figure(),
      costUsd: figure(),
    },

    // L5 System
    system: {
      turns: 0,
      rateLimitHits: 0,
      budgetAtStart: null,
      budgetNow: null,
      rateLimit: null,       // the latest rate-limit state, as reported (senseRateLimitState)
    },

    // DECIDE signals from the latest decide()
    signals: [],
  };

  // Activity: the first sets startedAt, each sets lastActivityAt.
  function touch() {
    const t = now();
    if (session.startedAt === null) session.startedAt = t;
    session.lastActivityAt = t;
  }

  // The model whose window applies: the latest message's, else the one given at creation.
  const currentModel = () => session.lastModel || session.model;

  // ─── ROUTE / EXCHANGE: what the row says about the session ─────
  // Called as each message starts: the cache signal of the last one is done with.
  function noteRoute(route, model) {
    const r = ROUTES.includes(route) ? route : null;
    if (r) {
      session.routes[r] = (session.routes[r] || 0) + 1;
      session.lastRoute = r;
    }
    if (typeof model === "string" && model) {
      session.models[model] = (session.models[model] || 0) + 1;
      session.lastModel = model;
    }
    session.cache.changedThisTurn = false;
    session.cache.missThisTurn = null;
    touch();
  }

  /** Marks activity without counting anything, such as a run starting. */
  function noteActivity() {
    touch();
  }

  function noteExchange({ ok }) {
    if (ok) session.exchanges.ok++;
    else session.exchanges.failed++;
    touch();
  }

  // ─── USAGE: what the provider reported for one turn ───
  /**
   * One turn of the agent's own, as the provider reported it. Token figures
   * are numbers, or null when not reported (they then add nothing): input
   * (uncached), cacheRead, cacheCreation, output, thinking. costUsd is what the
   * turn added. contextTokens is the latest request's input + cache read +
   * cache creation; contextWindow the model's window, when the provider stated
   * it (else YOUI's own table is used, as an estimate). cacheMiss is the
   * reported reason a request missed the cache ({ type, missedTokens }).
   * Each figure takes the label given with it (`tokensEvidence`,
   * `costEvidence`, `contextEvidence`, `contextWindowEvidence`).
   */
  function senseUsage({
    model = null,
    input = null,
    cacheRead = null,
    cacheCreation = null,
    output = null,
    thinking = null,
    tokensEvidence = EVIDENCE.reported,
    costUsd = null,
    costEvidence = EVIDENCE.reported,
    contextTokens = null,
    contextEvidence = EVIDENCE.reported,
    contextWindow = null,
    contextWindowEvidence = EVIDENCE.reported,
    cacheMiss = null,
  } = {}) {
    const usage = session.usage;
    usage.turns++;
    addTo(usage.input, input, tokensEvidence);
    addTo(usage.cacheRead, cacheRead, tokensEvidence);
    addTo(usage.cacheCreation, cacheCreation, tokensEvidence);
    addTo(usage.output, output, tokensEvidence);
    addTo(usage.costUsd, costUsd, costEvidence);
    if (isNumber(thinking)) recordThinking(thinking, isNumber(output) ? output : null, tokensEvidence);
    if (isNumber(contextTokens)) setContext(contextTokens, contextEvidence);
    if (isNumber(contextWindow) && contextWindow > 0) setWindow(contextWindow, contextWindowEvidence);
    else setWindow(knownContextWindow(model || currentModel()), EVIDENCE.estimated);
    const miss = cacheMiss && typeof cacheMiss.type === "string"
      ? { type: cacheMiss.type, missedTokens: isNumber(cacheMiss.missedTokens) ? cacheMiss.missedTokens : null }
      : null;
    session.cache.missThisTurn = miss;
    if (miss) {
      session.cache.misses++;
      session.cache.missedTokens += miss.missedTokens || 0;
      session.cache.lastMiss = miss;
    }
    const num = value => (isNumber(value) ? value : null);
    usage.last = {
      input: num(input),
      cacheRead: num(cacheRead),
      cacheCreation: num(cacheCreation),
      output: num(output),
      thinking: num(thinking),
      costUsd: num(costUsd),
      contextTokens: num(contextTokens),
      cacheMiss: miss,
      evidence: {
        tokens: tokensEvidence,
        costUsd: labelOf(num(costUsd), costEvidence),
        contextTokens: labelOf(num(contextTokens), contextEvidence),
        cacheMiss: miss ? EVIDENCE.reported : null,
      },
    };
    touch();
    return usage.last;
  }

  // ─── L1: SENSE OUTPUT ──────────────────────────────────
  // A block L1 does not grade (isGradable) counts as output, with grade null;
  // it adds nothing to the useful ratio and nothing to filler_regression.
  function senseOutput(text) {
    if (!text?.trim()) return null;
    const filler = detectFiller(text);
    const graded = isGradable(text);
    const useful = !graded ? null
      : filler.totalTokens > 0 ? Math.max(0, (filler.totalTokens - filler.tokens) / filler.totalTokens) : 1.0;
    const grade = graded ? gradeRatio(useful) : null;

    session.output.totalTokens += filler.totalTokens;
    session.output.textBlocks++;
    if (graded) {
      session.output.gradedTokens += filler.totalTokens;
      session.output.fillerTokens += filler.tokens;
      session.output.gradedBlocks++;
    }
    session.output.grades.push(grade);
    touch();

    return {
      fillerCount: graded ? filler.count : 0,
      fillerTokens: graded ? filler.tokens : 0,
      totalTokens: filler.totalTokens,
      usefulRatio: graded ? Math.round(useful * 100) / 100 : null,
      grade,
      graded,
      patterns: graded ? filler.patterns.slice(0, 5) : [],
      weight: graded ? filler.weight : 0,
      // Every L1 figure is YOUI's own estimate.
      evidence: EVIDENCE.estimated,
    };
  }

  // ─── L2: SENSE THINKING ────────────────────────────────
  function recordThinking(thinkTok, outTok, evidence) {
    session.thinking.totalTokens = (session.thinking.totalTokens ?? 0) + thinkTok;
    session.thinking.evidence = weakerEvidence(session.thinking.evidence, evidence);
    const ratio = isNumber(outTok) && outTok > 0 ? +(thinkTok / outTok).toFixed(2) : null;
    const entry = { thinking: thinkTok, output: outTok, ratio };
    session.thinking.perTurn.push(entry);
    return entry;
  }

  /**
   * Thinking tokens from a usage block, only where the provider states them
   * (`thinking_tokens`, or `output_tokens_details.thinking_tokens`). A usage
   * block without them records nothing: thinking stays null, never 0.
   */
  function senseThinking(usage = {}) {
    const stated = isNumber(usage?.thinking_tokens) ? usage.thinking_tokens
      : isNumber(usage?.output_tokens_details?.thinking_tokens) ? usage.output_tokens_details.thinking_tokens
        : null;
    const outTok = isNumber(usage?.output_tokens) ? usage.output_tokens : null;
    touch();
    if (stated === null) return { thinking: null, output: outTok, ratio: null };
    return recordThinking(stated, outTok, EVIDENCE.reported);
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
  function setContext(tokens, evidence) {
    const context = session.context;
    context.tokens = tokens;
    context.evidence = evidence;
    if (context.peakTokens === null || tokens >= context.peakTokens) {
      context.peakTokens = tokens;
      context.peakEvidence = evidence;
    }
  }

  function setWindow(window, evidence) {
    session.context.window = isNumber(window) && window > 0 ? window : null;
    session.context.windowEvidence = session.context.window === null ? null : evidence;
  }

  /**
   * YOUI's own estimate of the context (chars/4 over the conversation it
   * holds, tool definitions left out), for a route that reports no context:
   * YOUI's Ollama/vLLM loop, the terminal and sovereign. The window is YOUI's
   * own figure for the latest model, or unknown.
   */
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
    setContext(est, EVIDENCE.estimated);
    setWindow(knownContextWindow(currentModel()), EVIDENCE.estimated);
    session.context.oldestToolResultAge = oldestToolResult >= 0 ? messages.length - oldestToolResult : 0;
    touch();

    const window = session.context.window;
    return {
      estimatedTokens: est,
      messagesCount: messages.length,
      systemPromptTokens: session.context.systemPromptTokens,
      window,
      windowUtilization: window ? +(est / window).toFixed(4) : null,
      evidence: EVIDENCE.estimated,
    };
  }

  // ─── CACHE: SENSE THE SYSTEM PROMPT ───────────────────
  /**
   * The system prompt a Claude Code turn ran with: `hash` of the whole, and
   * `parts` [{ name, hash }] naming its sections. When it differs from the
   * previous turn's in the same `conversation`, the change is recorded and
   * names the parts that changed; a new conversation starts afresh. Only the
   * names and hashes are kept, in memory, never the text.
   */
  function senseSystemPrompt({ conversation = null, hash = null, parts = [] } = {}) {
    const cache = session.cache;
    const list = (Array.isArray(parts) ? parts : [])
      .filter(part => typeof part?.name === "string" && typeof part?.hash === "string")
      .map(part => ({ name: part.name, hash: part.hash }));
    let changedParts = [];
    const changed = Boolean(conversation && cache.conversation === conversation && cache.hash && hash && hash !== cache.hash);
    if (changed) {
      const before = new Map((cache.parts || []).map(part => [part.name, part.hash]));
      const after = new Map(list.map(part => [part.name, part.hash]));
      changedParts = [
        ...list.filter(part => before.get(part.name) !== part.hash).map(part => part.name),
        ...[...before.keys()].filter(name => !after.has(name)),
      ];
      cache.changes++;
      cache.lastChange = { parts: changedParts };
    }
    cache.conversation = conversation;
    cache.hash = hash;
    cache.parts = list;
    cache.changedThisTurn = changed;
    touch();
    return { changed, parts: changedParts };
  }

  // ─── MINDS: SENSE A CALL TO ANOTHER MIND ──────────────
  /**
   * One call to another mind (review, ensemble, decompose, pipeline,
   * layerthink), apart from the agent's own turns: whether it answered, and
   * its tokens and cost with their labels, as for senseUsage.
   */
  function senseMind({
    ok = false,
    input = null,
    cacheRead = null,
    cacheCreation = null,
    output = null,
    tokensEvidence = EVIDENCE.reported,
    costUsd = null,
    costEvidence = EVIDENCE.reported,
  } = {}) {
    const minds = session.minds;
    minds.calls++;
    if (!ok) minds.failed++;
    addTo(minds.input, input, tokensEvidence);
    addTo(minds.cacheRead, cacheRead, tokensEvidence);
    addTo(minds.cacheCreation, cacheCreation, tokensEvidence);
    addTo(minds.output, output, tokensEvidence);
    addTo(minds.costUsd, costUsd, costEvidence);
    touch();
  }

  // ─── L5: SENSE SYSTEM ─────────────────────────────────
  // A window not reported stays null, never 0.
  function utilizationOf(window) {
    const value = window && typeof window === "object" ? window.utilization : window;
    return isNumber(value) ? value : null;
  }

  function senseTurn(budget) {
    session.system.turns++;
    if (budget) {
      const fiveHour = utilizationOf(budget.fiveHour);
      const sevenDay = utilizationOf(budget.sevenDay);
      if (!session.system.budgetAtStart && (fiveHour !== null || sevenDay !== null)) {
        session.system.budgetAtStart = { fiveHour, sevenDay };
      }
      session.system.budgetNow = {
        fiveHour,
        sevenDay,
        isOverage: budget.isUsingOverage === true,
      };
    }
    touch();
  }

  function senseRateLimit() {
    session.system.rateLimitHits++;
    touch();
  }

  /**
   * The rate-limit state as the provider reported it (Claude Code's
   * rate_limit_event `rate_limit_info`): its status, type, utilization and
   * the five-hour and seven-day windows, with no other field. Until one is
   * reported it is null.
   */
  function senseRateLimitState(info) {
    if (!isPlainObject(info)) return session.system.rateLimit;
    const text = value => (typeof value === "string" ? value : null);
    const window = value => (isPlainObject(value) && isNumber(value.utilization)
      ? { utilization: value.utilization, status: text(value.status), resetsAt: isNumber(value.resetsAt) ? value.resetsAt : null }
      : null);
    const windows = isPlainObject(info.unifiedWindows) ? info.unifiedWindows : {};
    session.system.rateLimit = {
      status: text(info.status),
      type: text(info.rateLimitType),
      utilization: isNumber(info.utilization) ? info.utilization : null,
      fiveHour: window(windows.five_hour),
      sevenDay: window(windows.seven_day),
      isUsingOverage: typeof info.isUsingOverage === "boolean" ? info.isUsingOverage : null,
      reportedAt: new Date(now()).toISOString(),
      evidence: EVIDENCE.reported,
    };
    touch();
    return session.system.rateLimit;
  }

  // ═══════════════════════════════════════════════════════
  // DECIDE — Threshold signals (no LLM calls). Suggestions only:
  // each signal says what the person or the agent could do, and no
  // caller applies it.
  // ═══════════════════════════════════════════════════════

  // opts.route: the route this turn took. On "ollama" or "vllm" there is no
  // Claude effort setting and no Claude 5-hour window, so no effort signal is
  // raised and rate-limit pressure does not suggest lowering effort. Omitted →
  // the latest route noteRoute recorded, else the route given at creation.
  function decide(currentEffort, _currentModel, budget, { route: turnRoute } = {}) {
    const signals = [];
    const who = agentName(session.agent);
    const r = remedies(surface, session.agent);
    const onClaude = !OPEN_MODEL_ROUTES.includes(turnRoute ?? session.lastRoute ?? session.route);

    // ── Effort, under rate-limit pressure (as reported); only the strongest applies ──
    const bu5 = utilizationOf(budget?.fiveHour) ?? 0;
    if (onClaude && bu5 > 0.95 && (currentEffort === "max" || currentEffort === "high")) {
      signals.push({ type: "effort", action: "reduce", from: currentEffort, to: "medium",
        reason: `Budget 5h at ${(bu5*100).toFixed(0)}% (reported), close to the limit`,
        evidence: EVIDENCE.reported,
        suggestion: `Nothing was changed. You could ${r.lower(currentEffort, "medium")}, or pause until the window resets; ${r.keeps(currentEffort)}.` });
    } else if (onClaude && bu5 > 0.85 && currentEffort === "max") {
      signals.push({ type: "effort", action: "reduce", from: "max", to: "high",
        reason: `Budget 5h at ${(bu5*100).toFixed(0)}% (reported)`,
        evidence: EVIDENCE.reported,
        suggestion: `Nothing was changed. To stretch the 5-hour window, you could ${r.lower("max", "high")}; ${r.keeps("max")}.` });
    }

    // (The L2 rule that suggested a cheaper model from the thinking/output
    // ratio is gone: nothing reported thinking tokens, so it always fired.)

    // ── Context window pressure: the latest context against the model's window, where known ──
    const context = session.context;
    const share = context.window && isNumber(context.tokens) ? context.tokens / context.window : null;
    const contextEvidence = weakerEvidence(context.evidence, context.windowEvidence);
    if (share !== null && share > CONTEXT_PRUNE_SHARE) {
      signals.push({ type: "context", action: "prune_recommended",
        reason: `Context at ~${Math.round(context.tokens/1000)}k of a ${Math.round(context.window/1000)}k window (${contextEvidence})`,
        utilization: +share.toFixed(4),
        evidence: contextEvidence,
        suggestion: `Nothing was pruned. You could ${r.fresh}, or ask ${who} to summarise what matters so far.` });
    }
    if (share !== null && share > CONTEXT_EVICT_SHARE && context.oldestToolResultAge > 20) {
      signals.push({ type: "context", action: "evict_old_results",
        reason: `${context.oldestToolResultAge} messages since the oldest tool result; old results are piling up`,
        evidence: contextEvidence,
        suggestion: `Nothing was pruned. Once ${who} has noted what it still needs, you could ${r.fresh}.` });
    }

    // ── The cached system prompt changed within a conversation ──
    if (session.cache.changedThisTurn) {
      const names = session.cache.lastChange?.parts?.length ? session.cache.lastChange.parts.join(", ") : "a part YOUI could not name";
      const miss = session.cache.missThisTurn;
      signals.push({ type: "cache", action: "prompt_changed",
        parts: session.cache.lastChange?.parts || [],
        reason: `The system prompt changed between turns of this conversation (${names}), so the cache was rewritten`
          + (miss ? `; Claude reported a cache miss (${miss.type}${isNumber(miss.missedTokens) ? `, ${miss.missedTokens} tokens` : ""})` : ""),
        // The change is YOUI's own comparison; the miss, when there is one, is Claude's.
        evidence: miss ? EVIDENCE.reported : EVIDENCE.estimated,
        suggestion: `Nothing was changed. What changed (${names}) is part of the cached system prompt; changing it mid-conversation`
          + ` rewrites the cache on the next turn. You could change it less often, or ${r.fresh} when you do.` });
    }

    // ── Redundant tool calls ──
    if (session.action.redundantReads > 2) {
      signals.push({ type: "action", action: "redundant_reads",
        count: session.action.redundantReads,
        reason: `${session.action.redundantReads} repeated file reads`,
        evidence: EVIDENCE.estimated,
        suggestion: `${who} could rely on files it has already read this session unless they changed.` });
    }

    // ── Tool error rate ──
    if (session.action.toolCalls > 5 && session.action.toolErrors / session.action.toolCalls > 0.3) {
      signals.push({ type: "action", action: "high_error_rate",
        rate: +(session.action.toolErrors / session.action.toolCalls).toFixed(2),
        reason: `${session.action.toolErrors}/${session.action.toolCalls} tool calls errored`,
        evidence: EVIDENCE.estimated,
        suggestion: `${who} could stop and re-check the approach; you could restate the goal.` });
    }

    // ── Output filler trend (English-only estimate; graded blocks only) ──
    const graded = session.output.grades.filter(Boolean);
    if (graded.length >= 5) {
      const recent = graded.slice(-5);
      const badCount = recent.filter(g => g === "C" || g === "D").length;
      if (badCount >= 3) {
        signals.push({ type: "output", action: "filler_regression",
          reason: `${badCount}/5 recent graded text blocks grade C or D (English-only estimate)`,
          evidence: EVIDENCE.estimated,
          suggestion: `${who} could drop preamble and narration and say what matters.` });
      }
    }

    // ── Rate limit frequency ──
    if (session.system.rateLimitHits >= 2 && session.system.turns > 0) {
      signals.push({ type: "system", action: "rate_limit_pressure",
        hits: session.system.rateLimitHits, turns: session.system.turns,
        reason: `${session.system.rateLimitHits} rate limits in ${session.system.turns} turns`,
        evidence: EVIDENCE.estimated,
        suggestion: `You could ${onClaude ? r.pause : r.pauseOpen}.` });
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
  // REPORT — Structured metrics for dashboards. Each section carries
  // `evidence`: every figure's label, "reported" or "estimated" (null
  // where there is no figure).
  // ═══════════════════════════════════════════════════════

  // Every key of `figures` with the same label, or null where the figure is null.
  const allLabelled = (figures, evidence) =>
    Object.fromEntries(Object.entries(figures).map(([key, value]) => [key, labelOf(value, evidence)]));

  function report() {
    const E = EVIDENCE.estimated;
    // The active span: first activity to last activity (0 before any).
    const elapsed = session.startedAt === null ? 0 : session.lastActivityAt - session.startedAt;
    const elapsedMin = +(elapsed / 60000).toFixed(1);

    // L1 Output: no graded text, no grade.
    const usefulRatio = session.output.gradedTokens > 0
      ? Math.max(0, (session.output.gradedTokens - session.output.fillerTokens) / session.output.gradedTokens)
      : null;
    const overallGrade = gradeRatio(usefulRatio);
    const output = {
      usefulRatio: usefulRatio === null ? null : Math.round(usefulRatio * 100) / 100,
      grade: overallGrade,
      totalTokens: session.output.totalTokens,
      gradedTokens: session.output.gradedTokens,
      fillerTokens: session.output.fillerTokens,
      textBlocks: session.output.textBlocks,
      gradedBlocks: session.output.gradedBlocks,
    };

    // L2 Thinking: only what the provider reported; null when it reported none.
    const ratios = session.thinking.perTurn.map(turn => turn.ratio).filter(isNumber);
    const thinkingEvidence = session.thinking.evidence;
    const thinking = {
      totalTokens: session.thinking.totalTokens,
      avgRatio: ratios.length ? +(ratios.reduce((s, ratio) => s + ratio, 0) / ratios.length).toFixed(2) : null,
      // Output (an estimate) per thinking token.
      efficiency: session.thinking.totalTokens > 0
        ? +(session.output.totalTokens / session.thinking.totalTokens).toFixed(3)
        : null,
      turns: session.thinking.perTurn.length,
    };

    // L3 Action: YOUI's own counts.
    const action = {
      totalCalls: session.action.toolCalls,
      redundantReads: session.action.redundantReads,
      errors: session.action.toolErrors,
      density: session.output.textBlocks > 0 ? +(session.action.toolCalls / session.output.textBlocks).toFixed(2) : 0,
      uniqueFilesRead: session.action.readPaths.size,
    };

    // L4 Context
    const c = session.context;
    const context = {
      tokens: c.tokens,
      peakTokens: c.peakTokens,
      window: c.window,
      windowUtilization: c.window && isNumber(c.tokens) ? +(c.tokens / c.window).toFixed(4) : null,
      estimatedTokens: c.estimatedTokens,
      messagesCount: c.messagesCount,
      systemPromptTokens: c.systemPromptTokens,
    };

    // USAGE: as reported, summed over the turns that reported it.
    const u = session.usage;
    const usage = {
      turns: u.turns,
      inputTokens: u.input.value,
      cacheReadTokens: u.cacheRead.value,
      cacheCreationTokens: u.cacheCreation.value,
      outputTokens: u.output.value,
      thinkingTokens: session.thinking.totalTokens,
      costUsd: u.costUsd.value === null ? null : +u.costUsd.value.toFixed(6),
    };

    // CACHE
    const cache = {
      promptChanges: session.cache.changes,
      lastChange: session.cache.lastChange,
      misses: session.cache.misses,
      missedTokens: session.cache.missedTokens,
      lastMiss: session.cache.lastMiss,
    };

    // MINDS
    const m = session.minds;
    const minds = {
      calls: m.calls,
      failedCalls: m.failed,
      inputTokens: m.input.value,
      cacheReadTokens: m.cacheRead.value,
      cacheCreationTokens: m.cacheCreation.value,
      outputTokens: m.output.value,
      costUsd: m.costUsd.value === null ? null : +m.costUsd.value.toFixed(6),
    };

    // L5 System. The 5-hour window is account-wide, so what moved while this
    // session ran is YOUI's inference from two reported figures.
    const start = session.system.budgetAtStart?.fiveHour;
    const end = session.system.budgetNow?.fiveHour;
    const budgetBurned = isNumber(start) && isNumber(end) ? +((end - start) * 100).toFixed(1) : null;
    const tokensPerTurn = session.system.turns > 0
      ? Math.round((session.output.totalTokens + (session.thinking.totalTokens || 0)) / session.system.turns)
      : 0;
    const system = {
      budgetBurned: budgetBurned !== null ? `${budgetBurned}%` : null,
      budgetNow: session.system.budgetNow,
      rateLimit: session.system.rateLimit,
      rateLimitHits: session.system.rateLimitHits,
      tokensPerTurn,
    };

    return {
      // Header
      session: session.id,
      agent: session.agent,
      elapsed: elapsedMin,
      turns: session.system.turns,

      // What the provider reported for the agent's own turns
      usage: {
        ...usage,
        last: u.last,
        evidence: {
          turns: E,
          inputTokens: u.input.evidence,
          cacheReadTokens: u.cacheRead.evidence,
          cacheCreationTokens: u.cacheCreation.evidence,
          outputTokens: u.output.evidence,
          thinkingTokens: thinkingEvidence,
          costUsd: u.costUsd.evidence,
        },
      },

      // L1 Output (all estimates)
      output: {
        ...output,
        gradeDistribution: countGrades(session.output.grades),
        evidence: allLabelled(output, E),
      },

      // L2 Thinking
      thinking: {
        ...thinking,
        evidence: {
          totalTokens: labelOf(thinking.totalTokens, thinkingEvidence),
          avgRatio: labelOf(thinking.avgRatio, thinkingEvidence),
          efficiency: labelOf(thinking.efficiency, E),
          turns: E,
        },
      },

      // L3 Action
      action: {
        ...action,
        byName: session.action.toolsByName,
        evidence: allLabelled(action, E),
      },

      // L4 Context
      context: {
        ...context,
        evidence: {
          tokens: c.evidence,
          peakTokens: c.peakEvidence,
          window: c.windowEvidence,
          windowUtilization: labelOf(context.windowUtilization, weakerEvidence(c.evidence, c.windowEvidence)),
          estimatedTokens: labelOf(c.estimatedTokens, E),
          messagesCount: E,
          systemPromptTokens: E,
        },
      },

      // The cached system prompt (YOUI's comparison) and the misses Claude reported
      cache: {
        ...cache,
        evidence: {
          promptChanges: E,
          lastChange: labelOf(cache.lastChange, E),
          misses: EVIDENCE.reported,
          missedTokens: EVIDENCE.reported,
          lastMiss: labelOf(cache.lastMiss, EVIDENCE.reported),
        },
      },

      // Other minds, apart from the agent's own turns
      minds: {
        ...minds,
        evidence: {
          calls: E,
          failedCalls: E,
          inputTokens: m.input.evidence,
          cacheReadTokens: m.cacheRead.evidence,
          cacheCreationTokens: m.cacheCreation.evidence,
          outputTokens: m.output.evidence,
          costUsd: m.costUsd.evidence,
        },
      },

      // L5 System
      system: {
        ...system,
        evidence: {
          budgetBurned: labelOf(system.budgetBurned, E),
          budgetNow: labelOf(system.budgetNow, EVIDENCE.reported),
          rateLimit: labelOf(system.rateLimit, EVIDENCE.reported),
          rateLimitHits: E,
          tokensPerTurn: E,
        },
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

  /**
   * The row this session would write: aggregates only. startedAt and endedAt
   * are its first and last activity, elapsed the minutes between them, and
   * closedAt when it was cut (now). `evidence` labels its figures.
   */
  function row(reason = null) {
    const r = report();
    const iso = t => (t === null ? null : new Date(t).toISOString());
    const figures = {
      elapsed: r.elapsed,
      turns: r.turns,
      outputGrade: r.output.grade,
      usefulRatio: r.output.usefulRatio,
      outputTokensEstimate: r.output.totalTokens,
      fillerTokensEstimate: r.output.fillerTokens,
      textBlocks: r.output.textBlocks,
      gradedBlocks: r.output.gradedBlocks,
      thinkingTokens: r.thinking.totalTokens,
      thinkAvgRatio: r.thinking.avgRatio,
      toolCalls: r.action.totalCalls,
      redundantReads: r.action.redundantReads,
      toolErrors: r.action.errors,
      contextPeakTokens: r.context.peakTokens,
      contextWindow: r.context.window,
      usageTurns: r.usage.turns,
      inputTokens: r.usage.inputTokens,
      cacheReadTokens: r.usage.cacheReadTokens,
      cacheCreationTokens: r.usage.cacheCreationTokens,
      outputTokens: r.usage.outputTokens,
      costUsd: r.usage.costUsd,
      promptChanges: r.cache.promptChanges,
      cacheMisses: r.cache.misses,
      mindCalls: r.minds.calls,
      mindFailedCalls: r.minds.failedCalls,
      mindInputTokens: r.minds.inputTokens,
      mindCacheReadTokens: r.minds.cacheReadTokens,
      mindCacheCreationTokens: r.minds.cacheCreationTokens,
      mindOutputTokens: r.minds.outputTokens,
      mindCostUsd: r.minds.costUsd,
      budgetBurned: r.system.budgetBurned,
      rateLimitStatus: r.system.rateLimit?.status ?? null,
      fiveHourUtilization: r.system.rateLimit?.fiveHour?.utilization ?? null,
      sevenDayUtilization: r.system.rateLimit?.sevenDay?.utilization ?? null,
      rateLimitHits: r.system.rateLimitHits,
    };
    const E = EVIDENCE.estimated;
    const labels = {
      elapsed: E,
      turns: E,
      outputGrade: E,
      usefulRatio: E,
      outputTokensEstimate: E,
      fillerTokensEstimate: E,
      textBlocks: E,
      gradedBlocks: E,
      thinkingTokens: r.thinking.evidence.totalTokens,
      thinkAvgRatio: r.thinking.evidence.avgRatio,
      toolCalls: E,
      redundantReads: E,
      toolErrors: E,
      contextPeakTokens: r.context.evidence.peakTokens,
      contextWindow: r.context.evidence.window,
      usageTurns: E,
      inputTokens: r.usage.evidence.inputTokens,
      cacheReadTokens: r.usage.evidence.cacheReadTokens,
      cacheCreationTokens: r.usage.evidence.cacheCreationTokens,
      outputTokens: r.usage.evidence.outputTokens,
      costUsd: r.usage.evidence.costUsd,
      promptChanges: E,
      cacheMisses: EVIDENCE.reported,
      mindCalls: E,
      mindFailedCalls: E,
      mindInputTokens: r.minds.evidence.inputTokens,
      mindCacheReadTokens: r.minds.evidence.cacheReadTokens,
      mindCacheCreationTokens: r.minds.evidence.cacheCreationTokens,
      mindOutputTokens: r.minds.evidence.outputTokens,
      mindCostUsd: r.minds.evidence.costUsd,
      budgetBurned: E,
      rateLimitStatus: EVIDENCE.reported,
      fiveHourUtilization: EVIDENCE.reported,
      sevenDayUtilization: EVIDENCE.reported,
      rateLimitHits: E,
    };
    const evidence = {};
    for (const [key, value] of Object.entries(figures)) {
      if (value !== null && value !== undefined && labels[key]) evidence[key] = labels[key];
    }
    return {
      schema: HISTORY_SCHEMA,
      session: session.id,
      route: primary(session.routes, session.route),
      model: primary(session.models, session.model),
      agent: session.agent,
      routes: { ...session.routes },
      models: { ...session.models },
      startedAt: iso(session.startedAt),
      endedAt: iso(session.lastActivityAt),
      closedAt: iso(now()),
      endReason: reason,
      exchanges: { ...session.exchanges },
      ...figures,
      signalCount: session.signalKeys.size,
      signals: [...session.signalKeys].sort(),
      evidence,
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

  // Both read the store through readHistoryCached: one read while it is unchanged.
  function trends(n = 20) {
    if (!historyPath) return null;
    return trendsFromRows(readHistoryCached(historyPath).rows, n);
  }

  /** What the store holds and anything wrong with it; the legacy file is summarised separately. */
  function storeStatus() {
    const store = historyPath ? readHistoryCached(historyPath) : null;
    return {
      enabled: Boolean(historyPath),
      rows: store ? store.rows.length : 0,
      warnings: store ? store.warnings : [],
      legacy: readLegacyHistory(legacyPath),
    };
  }

  // ─── COMPACT STATUS LINE ───────────────────────────────
  // "~" marks an estimate; a figure with no mark was reported. "—" is no figure.
  function statusLine() {
    const r = report();
    const g = r.output.grade ?? "—";
    const ur = r.output.usefulRatio === null ? "—" : `${Math.round(r.output.usefulRatio * 100)}%~`;
    const tr = r.thinking.avgRatio === null ? "—" : `${r.thinking.avgRatio}x`;
    const tc = r.action.totalCalls;
    const rr = r.action.redundantReads;
    const ctx = r.context.tokens === null ? "—"
      : `${Math.round(r.context.tokens / 1000)}k${r.context.evidence.tokens === EVIDENCE.estimated ? "~" : ""}`;
    const cost = r.usage.costUsd === null ? "" : ` $${r.usage.costUsd.toFixed(4)}${r.usage.evidence.costUsd === EVIDENCE.estimated ? "~" : ""}`;
    const sig = r.signals.length;
    return `YS:${g}(${ur}) think:${tr} tools:${tc}${rr > 0 ? ` dup:${rr}` : ""} ctx:${ctx}${cost}${sig > 0 ? ` ⚡${sig}` : ""}`;
  }

  // ─── PUBLIC API ────────────────────────────────────────
  return {
    // SENSE
    senseUsage,
    senseOutput,
    senseThinking,
    senseToolCall,
    senseContext,
    senseSystemPrompt,
    senseMind,
    senseTurn,
    senseRateLimit,
    senseRateLimitState,
    noteRoute,
    noteExchange,
    noteActivity,
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
    surface,
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
      console.log(`  Think/output:     ${t.avgThinkRatio === null ? "— (no session reported thinking)" : `${t.avgThinkRatio}x avg (reported)`}`);
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
  ${S.cyan}const ys = createKernel({ agent, route, model, surface });${S.reset}

What it senses (each figure labelled reported or estimated; see docs/YOUSPEAK.md):
  USAGE       — Input, cache read and creation, output, thinking, cost, as the provider reports them
  L1 OUTPUT   — English filler estimate, useful ratio (blocks of 20+ tokens, mostly ASCII)
  L2 THINKING — Thinking tokens, only where reported
  L3 ACTION   — Tool calls, repeated reads
  L4 CONTEXT  — The latest request's input + cache (reported) or chars/4, against the model's window where known
  L5 SYSTEM   — Rate-limit state as reported
  CACHE       — Whether the cached system prompt changed within a conversation
  MINDS       — Other minds' calls, tokens and cost
`);
  }
}
