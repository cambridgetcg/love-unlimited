// onoff.mjs — what the YOUSPEAK ON/OFF test is, in one place
// (benchmarks/youspeak-onoff-v0/PREREGISTRATION.md).
//
// The two arms, the phases with their models, effort and caps, the stop rules,
// the seeded order, and the paired statistics. harness.mjs runs the calls and
// analyze.mjs reads what it wrote; neither carries a copy of anything here.
// Nothing in this file starts a process or makes a model call.

import { createHash } from "node:crypto";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { youspeakRule } from "../../youspeak-rule.mjs";

export const BENCHMARK_ID = "youspeak-onoff-v0";
export const BENCHMARK_DIR = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(BENCHMARK_DIR, "..", "..");
export const PROMPTS_PATH = join(BENCHMARK_DIR, "data", "prompts.jsonl");
export const PREREGISTRATION_PATH = join(BENCHMARK_DIR, "PREREGISTRATION.md");

// The base both arms carry. It is not empty: with an empty system prompt
// Claude Code falls back to its own coding-agent prompt, and the OFF arm would
// then differ in more than the rule. It says nothing about length or style.
export const BASE_SYSTEM_PROMPT = "You are Claude, an AI assistant. Reply to the person's message.";

export const ARMS = Object.freeze(["on", "off"]);

/** The system prompt of one arm: ON is the base, a blank line and the rule as YOUI Web carries it; OFF is the base alone. */
export function armSystemPrompt(arm, rule = youspeakRule()) {
  if (arm === "on") return `${BASE_SYSTEM_PROMPT}\n\n${rule}`;
  if (arm === "off") return BASE_SYSTEM_PROMPT;
  throw new TypeError(`unknown arm: ${arm}`);
}

// Effort is passed to Claude Code for both models. claude-models.mjs does not
// list Haiku 5.5, so modelCaps would give it no effort and Claude Code's
// default (medium) would apply; the pilot names "high" itself, as Alpha runs.
export const PHASES = Object.freeze({
  pilot: Object.freeze({ model: "claude-haiku-5-5", effort: "high", capUsd: 1, interim: false }),
  opus: Object.freeze({ model: "claude-opus-5-5", effort: "high", capUsd: 15, interim: true }),
});
export const TOTAL_CAP_USD = 20;
// The plan windows Claude Code reports: stop once a window's utilisation passes these.
export const FIVE_HOUR_STOP = 0.5;
export const SEVEN_DAY_STOP = 0.8;
// The Opus run's size, from the pilot (suggestPairs): at least six pairs a stratum.
export const MIN_PAIRS_PER_STRATUM = 6;
// The smallest change the Opus run is sized to see: a 15% change in visible length.
export const TARGET_RATIO = 0.85;
// The interim harm rule: ON loses this many more checkable answers than OFF.
export const HARM_NET_LOSS = 3;

export const EVIDENCE_KEY = Object.freeze({
  reported: "Claude Code stated it for one call (tokens, thinking where stated, list-price cost, rate-limit state)",
  estimated: "the harness or the analysis computed it: visible length, sums and means of reported figures, statistics, checks and hedge counts",
});

/** Where raw outputs go unless --out or YOUI_ONOFF_DIR says otherwise: outside the repository, per user. */
export function defaultOutDir(env = process.env) {
  return resolve(env.YOUI_ONOFF_DIR || join(env.HOME || homedir(), ".local", "share", "youi", "onoff"));
}

// The nearest existing ancestor's real path, so a symlinked or not yet
// created directory is judged by where it would really be.
function realish(path) {
  let current = resolve(path);
  const rest = [];
  while (!existsSync(current)) {
    const parent = dirname(current);
    if (parent === current) break;
    rest.unshift(basename(current));
    current = parent;
  }
  try { current = realpathSync(current); } catch {}
  return join(current, ...rest);
}

/** Whether `path` is inside this repository (raw outputs never are). */
export function insideRepo(path, root = REPO_ROOT) {
  const rel = relative(realish(root), realish(path));
  return rel === "" || !(rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel));
}

export function sha256(text) {
  return createHash("sha256").update(text).digest("hex");
}

/** The prompt set, checked: unique ids, a stratum, text, and a check pattern that compiles. */
export function loadPrompts(path = PROMPTS_PATH) {
  const text = readFileSync(path, "utf-8");
  const prompts = text.split("\n").filter(line => line.trim()).map((line, index) => {
    const record = JSON.parse(line);
    if (typeof record.id !== "string" || !record.id) throw new Error(`prompt ${index + 1} has no id`);
    if (typeof record.input !== "string" || !record.input.trim()) throw new Error(`${record.id} has no input`);
    if (typeof record.stratum !== "string" || !record.stratum) throw new Error(`${record.id} has no stratum`);
    if (record.check !== undefined) new RegExp(record.check, "iu");
    return record;
  });
  const ids = new Set(prompts.map(prompt => prompt.id));
  if (ids.size !== prompts.length) throw new Error("prompt ids are not unique");
  return { prompts, sha256: sha256(text) };
}

export function strataOf(prompts) {
  return [...new Set(prompts.map(prompt => prompt.stratum))].sort();
}

/** A small seeded PRNG (mulberry32): the same seed gives the same order. */
export function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffle(items, random) {
  const out = [...items];
  for (let index = out.length - 1; index > 0; index -= 1) {
    const other = Math.floor(random() * (index + 1));
    [out[index], out[other]] = [out[other], out[index]];
  }
  return out;
}

/**
 * The order of a phase: prompts shuffled within each stratum, then taken one
 * stratum at a time (the strata in a fresh order each round), so any first n
 * of the order, the interim half included, is spread across the strata. Each
 * prompt's arm order is a coin flip. Everything comes from `seed`.
 */
export function planOrder(prompts, seed) {
  const random = seededRandom(seed);
  const strata = strataOf(prompts);
  const queues = strata.map(stratum => shuffle(prompts.filter(prompt => prompt.stratum === stratum), random));
  const order = [];
  while (queues.some(queue => queue.length > 0)) {
    for (const index of shuffle(strata.map((_, i) => i), random)) {
      const prompt = queues[index].shift();
      if (prompt) order.push({ id: prompt.id, stratum: prompt.stratum, first: random() < 0.5 ? "on" : "off" });
    }
  }
  return order;
}

const isNumber = value => typeof value === "number" && Number.isFinite(value);

/**
 * Why one call's rate-limit reports stop the run, as reason strings (none:
 * []). `infos` is every rate_limit_event's `rate_limit_info` the call
 * printed. A call that printed none, or whose reports never state both
 * windows' utilisation, stops it too: the rules could not be checked.
 */
export function rateLimitStops(infos) {
  const reasons = new Set();
  const list = (infos || []).filter(info => info && typeof info === "object");
  if (list.length === 0) return ["rate_limit_unreported"];
  let fiveHour = null;
  let sevenDay = null;
  for (const info of list) {
    if (info.isUsingOverage === true) reasons.add("overage");
    if (info.status !== "allowed") reasons.add("status_not_allowed");
    const windows = info.unifiedWindows && typeof info.unifiedWindows === "object" ? info.unifiedWindows : {};
    for (const window of [windows.five_hour, windows.seven_day]) {
      if (window && typeof window.status === "string" && window.status !== "allowed") reasons.add("status_not_allowed");
    }
    if (isNumber(windows.five_hour?.utilization)) fiveHour = Math.max(fiveHour ?? 0, windows.five_hour.utilization);
    if (isNumber(windows.seven_day?.utilization)) sevenDay = Math.max(sevenDay ?? 0, windows.seven_day.utilization);
  }
  if (fiveHour === null || sevenDay === null) reasons.add("rate_limit_window_unreported");
  if (fiveHour !== null && fiveHour > FIVE_HOUR_STOP) reasons.add("five_hour_over_0.5");
  if (sevenDay !== null && sevenDay > SEVEN_DAY_STOP) reasons.add("seven_day_over_0.8");
  return [...reasons];
}

/**
 * Whether the next call may go ahead under a cap: not once the reported spend
 * has reached it, nor when the costliest call so far would carry it past.
 */
export function capStops({ spentUsd, maxCallUsd = 0, capUsd, name }) {
  if (spentUsd >= capUsd || spentUsd + maxCallUsd > capUsd) return [name];
  return [];
}

/**
 * When a stop from an earlier invocation's last call still holds: until the
 * reset time the reports name for the window behind it (the five-hour window
 * for status and overage). With no reset time named, it holds.
 */
export function staleStopHolds(reason, infos, nowMs = Date.now()) {
  const windows = (infos || []).map(info => info?.unifiedWindows || {});
  const resets = key => windows.map(w => w[key]?.resetsAt).filter(isNumber);
  const until = reason === "seven_day_over_0.8" ? resets("seven_day") : resets("five_hour");
  if (until.length === 0) return true;
  return Math.max(...until) * 1000 > nowMs;
}

/** Visible length: Unicode code points of the answer's text (a CJK character counts once). */
export function visibleChars(text) {
  return [...String(text || "")].length;
}

// The rule's own "sacred" hedges, in English (youspeak-rule.mjs).
const HEDGES = /\b(probably|unless|might|I think|in most cases)\b/giu;
export function countHedges(text) {
  return (String(text || "").match(HEDGES) || []).length;
}

export function checkAnswer(check, text) {
  return check ? new RegExp(check, "iu").test(String(text || "")) : null;
}

function mean(values) {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function sd(values) {
  if (values.length < 2) return null;
  const m = mean(values);
  return Math.sqrt(values.reduce((a, b) => a + (b - m) ** 2, 0) / (values.length - 1));
}

/**
 * A percentile bootstrap interval for the mean, resampling within each group
 * (a stratum) so each keeps its size: the pooled interval is stratified, a
 * single stratum's is plain. Fewer than two values in all gives no interval.
 */
export function bootstrapMean(groups, { seed = 1, resamples = 10_000, level = 0.95 } = {}) {
  const nonEmpty = groups.filter(group => group.length > 0);
  const all = nonEmpty.flat();
  if (all.length < 2) return { mean: mean(all), low: null, high: null };
  const random = seededRandom(seed);
  const means = new Float64Array(resamples);
  for (let r = 0; r < resamples; r += 1) {
    let sum = 0;
    for (const group of nonEmpty) {
      for (let i = 0; i < group.length; i += 1) sum += group[Math.floor(random() * group.length)];
    }
    means[r] = sum / all.length;
  }
  means.sort();
  const tail = (1 - level) / 2;
  return {
    mean: mean(all),
    low: means[Math.floor(tail * (resamples - 1))],
    high: means[Math.ceil((1 - tail) * (resamples - 1))],
  };
}

/**
 * The completed pairs in a phase's calls: for each prompt, its latest attempt
 * whose two arms both answered.
 */
export function completedPairs(calls) {
  const byKey = new Map();
  for (const call of calls) {
    if (!call.ok) continue;
    const key = `${call.promptId}\u0000${call.attempt}`;
    const entry = byKey.get(key) || { promptId: call.promptId, stratum: call.stratum, attempt: call.attempt };
    entry[call.arm] = call;
    byKey.set(key, entry);
  }
  const latest = new Map();
  for (const entry of byKey.values()) {
    if (!entry.on || !entry.off) continue;
    const kept = latest.get(entry.promptId);
    if (!kept || entry.attempt > kept.attempt) latest.set(entry.promptId, entry);
  }
  return [...latest.values()];
}

const logRatio = (on, off) => Math.log((on + 1) / (off + 1));

// One paired endpoint, pooled and by stratum: log(ON/OFF) means as ratios with intervals.
function ratioEndpoint(pairs, value, { seed, strata, evidence, basis }) {
  const usable = pairs.filter(pair => isNumber(value(pair.on)) && isNumber(value(pair.off)));
  const summarise = (subset, groups) => {
    const d = subset.map(pair => logRatio(value(pair.on), value(pair.off)));
    const interval = bootstrapMean(groups.map(group => group.map(pair => logRatio(value(pair.on), value(pair.off)))), { seed });
    return {
      n: subset.length,
      meanLogRatio: interval.mean,
      sdLogRatio: sd(d),
      ratio: interval.mean === null ? null : Math.exp(interval.mean),
      ratioLow: interval.low === null ? null : Math.exp(interval.low),
      ratioHigh: interval.high === null ? null : Math.exp(interval.high),
      onLower: d.filter(x => x < 0).length,
      onHigher: d.filter(x => x > 0).length,
      meanOn: mean(subset.map(pair => value(pair.on))),
      meanOff: mean(subset.map(pair => value(pair.off))),
    };
  };
  const groups = strata.map(stratum => usable.filter(pair => pair.stratum === stratum));
  return {
    evidence,
    basis,
    pooled: summarise(usable, groups),
    byStratum: Object.fromEntries(strata.map((stratum, i) => [stratum, summarise(groups[i], [groups[i]])])),
  };
}

/**
 * The sample size the Opus run needs to see a TARGET_RATIO change at 80%
 * power and two-sided alpha 0.05, from the pilot's SD of the paired log
 * ratio: n = ((1.96 + 0.84) × sd / ln(1/0.85))², rounded up to whole rounds
 * of the strata and kept between six pairs a stratum and the whole set.
 */
export function suggestPairs(sdLogRatio, strataCount, promptCount) {
  if (!isNumber(sdLogRatio)) return null;
  const raw = ((1.959964 + 0.841621) * sdLogRatio / Math.log(1 / TARGET_RATIO)) ** 2;
  const rounded = Math.ceil(raw / strataCount) * strataCount;
  return { raw, pairs: Math.min(promptCount, Math.max(MIN_PAIRS_PER_STRATUM * strataCount, rounded)) };
}

/**
 * The paired statistics for one phase's calls (aggregates only: no text, no
 * prompt). `prompts` gives each prompt's check; `seed` fixes the bootstrap.
 */
export function analysePairs(calls, prompts, { seed = 1, capUsd = null, plannedPairs = null } = {}) {
  const strata = strataOf(prompts);
  const byId = new Map(prompts.map(prompt => [prompt.id, prompt]));
  const pairs = completedPairs(calls);
  const visible = ratioEndpoint(pairs, call => call.visibleChars, {
    seed, strata, evidence: "estimated", basis: "Unicode code points of the answer text; log((ON+1)/(OFF+1)) per pair",
  });
  const output = ratioEndpoint(pairs, call => call.reported?.output, {
    seed, strata, evidence: "estimated", basis: "Claude Code's reported output tokens per call (reported); the ratio and interval are computed",
  });
  const thinking = ratioEndpoint(pairs, call => call.reported?.thinking, {
    seed, strata, evidence: "estimated", basis: "reported thinking tokens, only pairs where both calls stated them",
  });
  const costDiffs = pairs.filter(pair => isNumber(pair.on.reported?.costUsd) && isNumber(pair.off.reported?.costUsd))
    .map(pair => pair.on.reported.costUsd - pair.off.reported.costUsd);
  const costInterval = bootstrapMean([costDiffs], { seed });
  const allCost = calls.map(call => call.reported?.costUsd).filter(isNumber);
  const spentUsd = allCost.reduce((a, b) => a + b, 0);

  const checkable = pairs.filter(pair => byId.get(pair.promptId)?.check);
  let onOnly = 0;
  let offOnly = 0;
  let bothRight = 0;
  for (const pair of checkable) {
    const check = byId.get(pair.promptId).check;
    const on = checkAnswer(check, pair.on.text);
    const off = checkAnswer(check, pair.off.text);
    if (on && off) bothRight += 1;
    else if (on) onOnly += 1;
    else if (off) offOnly += 1;
  }
  const english = pairs.filter(pair => pair.stratum !== "cantonese");
  const hedgesOn = english.reduce((a, pair) => a + countHedges(pair.on.text), 0);
  const hedgesOff = english.reduce((a, pair) => a + countHedges(pair.off.text), 0);

  const meanPairCost = pairs.length && spentUsd ? spentUsd / pairs.length : null;
  const remaining = isNumber(plannedPairs) ? Math.max(0, plannedPairs - pairs.length) : null;
  const projectedUsd = meanPairCost !== null && remaining !== null ? spentUsd + meanPairCost * remaining : null;
  const netLoss = offOnly - onOnly;
  return {
    pairs: pairs.length,
    calls: calls.length,
    failedCalls: calls.filter(call => !call.ok).length,
    primary: { name: "visible answer length", ...visible },
    secondary: {
      outputTokens: output,
      thinkingTokens: thinking,
      cost: {
        evidence: "estimated",
        basis: "Claude Code's reported list-price cost per call; ON − OFF per pair, in US$",
        n: costDiffs.length,
        meanDiffUsd: costInterval.mean,
        lowUsd: costInterval.low,
        highUsd: costInterval.high,
        spentUsd,
      },
    },
    checkable: { evidence: "estimated", basis: "the prompt's check pattern against the answer text", pairs: checkable.length, bothRight, onOnly, offOnly },
    hedges: { evidence: "estimated", basis: "the rule's English hedges, counted outside the cantonese stratum", pairs: english.length, on: hedgesOn, off: hedgesOff },
    interim: {
      harm: netLoss >= HARM_NET_LOSS,
      netCheckableLoss: netLoss,
      futility: capUsd !== null && projectedUsd !== null && projectedUsd > capUsd,
      projectedUsd,
      capUsd,
    },
    nextPairs: suggestPairs(visible.pooled.sdLogRatio, strata.length, prompts.length),
  };
}
