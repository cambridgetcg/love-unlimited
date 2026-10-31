#!/usr/bin/env node
// harness.mjs — runs the YOUSPEAK ON/OFF test through the Claude Code
// installed on this device, one call at a time
// (benchmarks/youspeak-onoff-v0/PREREGISTRATION.md).
//
// Every call goes through runClaudeCode (youi-web/claude-code.mjs) as a YOUI
// mind call does: a one-off run with no saved session, no tools and no MCP,
// from an empty 0700 working directory, with YOUI's environment (HOME and
// PATH kept, no credential, CLAUDE_CODE_DISABLE_CLAUDE_MDS and the other
// CLAUDE_CODE_* switches set by claudeCodeChildEnv). The arms differ only in
// the system prompt (onoff.mjs). Before each call it applies every stop rule
// to the costs and rate-limit reports so far; it never retries.
//
// Raw outputs (answer texts, per-call figures, rate-limit reports) go only to
// a directory outside the repository: ~/.local/share/youi/onoff/ unless
// --out or YOUI_ONOFF_DIR names another; one inside the repository is
// refused. summary.json there holds aggregates only. The caps and the
// rate-limit holds count every run under that directory (live runs together,
// dry runs together), so a new --run-id starts with the spend before it.
//
// Without --live it is a dry run on tests/fixtures/replay-claude.mjs (the
// recorded stream; --fake fake for tests/fixtures/fake-claude.mjs), which
// makes no network call and spends nothing. --live runs the real Claude Code
// and refuses unless the registration (this directory, the rule and the
// runner) is committed and Claude Code meets YOUI's version floor.

import { execFileSync } from "node:child_process";
import { randomInt } from "node:crypto";
import { appendFileSync, chmodSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  claudeCodeObservation,
  claudeCodeSpend,
  compareVersions,
  readClaudeVersion,
  resolveClaudeBinary,
  runClaudeCode,
} from "../../youi-web/claude-code.mjs";
import { claudeCodeMinVersion } from "../../youi-web/claude-models.mjs";
import { sanitizedChildEnv } from "../../youi-web/subprocess-env.mjs";
import { youspeakRule } from "../../youspeak-rule.mjs";
import {
  BASE_SYSTEM_PROMPT,
  BENCHMARK_ID,
  EVIDENCE_KEY,
  PHASES,
  PREREGISTRATION_PATH,
  PROMPTS_PATH,
  REPO_ROOT,
  TOTAL_CAP_USD,
  armSystemPrompt,
  capStops,
  completedPairs,
  defaultOutDir,
  insideRepo,
  loadPrompts,
  planOrder,
  rateLimitStops,
  sha256,
  staleStopHolds,
  visibleChars,
} from "./onoff.mjs";

export const REPLAY_FIXTURE = join(REPO_ROOT, "tests", "fixtures", "claude-code-stream-2.1.314.jsonl");
const FAKES = {
  replay: join(REPO_ROOT, "tests", "fixtures", "replay-claude.mjs"),
  fake: join(REPO_ROOT, "tests", "fixtures", "fake-claude.mjs"),
};
// What the registration is: a live run refuses while any of these has
// uncommitted changes, and a live resume while any differs from the commit
// the run began at.
const REGISTERED_PATHS = [
  "benchmarks/youspeak-onoff-v0", "youspeak-rule.mjs", "youi-web/claude-code.mjs",
  "youi-web/claude-models.mjs", "youi-web/subprocess-env.mjs",
];
// Reports that say nothing could be checked.
const UNREPORTED = new Set(["rate_limit_unreported", "rate_limit_window_unreported", "overage_unreported"]);
const RATE_REASONS = new Set([
  "overage", "status_not_allowed", "five_hour_over_0.5", "seven_day_over_0.8", ...UNREPORTED,
]);
const SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"];

const isNumber = value => typeof value === "number" && Number.isFinite(value);
// A sum of US$ figures, without floating-point dust (to a billionth of a dollar).
const usd = value => Number(value.toFixed(9));

// YOUI Web's Claude Code environment (server.mjs claudeCodeEnv), from `source`.
function claudeEnv(source) {
  return sanitizedChildEnv({
    source,
    home: source.HOME || homedir(),
    purpose: "claude-code",
    extra: { CLAUDE_CONFIG_DIR: source.CLAUDE_CONFIG_DIR },
  });
}

function git(args) {
  try {
    return execFileSync("git", args, { cwd: REPO_ROOT, encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return null;
  }
}

// Whether the registered paths differ between `commit` and HEAD (null: git could not tell).
export function registrationChangedSince(commit) {
  if (!commit) return null;
  try {
    execFileSync("git", ["diff", "--quiet", commit, "HEAD", "--", ...REGISTERED_PATHS], { cwd: REPO_ROOT, stdio: "ignore" });
    return false;
  } catch (error) {
    return error?.status === 1 ? true : null;
  }
}

function readJsonl(path) {
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf-8").split("\n").filter(line => line.trim()).map(line => JSON.parse(line));
}

function writePrivate(path, text) {
  writeFileSync(path, text, { mode: 0o600 });
  chmodSync(path, 0o600);
}

function privateDir(path) {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  chmodSync(path, 0o700);
  return path;
}

// A `claude` for the dry run: node itself, with the stand-in's environment set inside.
function dryRunBinary(dir, fake, { replayFile, phaseDir }) {
  const env = fake === "replay"
    ? { FAKE_CLAUDE_REPLAY: replayFile }
    : { FAKE_CLAUDE_LOG: join(phaseDir, "fake-claude.jsonl"), FAKE_CLAUDE_SESSIONS: join(phaseDir, "fake-sessions") };
  const bin = join(privateDir(dir), "claude");
  writeFileSync(bin, [
    `#!${process.execPath}`,
    ...Object.entries(env).map(([name, value]) => `process.env.${name} = ${JSON.stringify(value)};`),
    `import(${JSON.stringify(pathToFileURL(FAKES[fake]).href)});`,
    "",
  ].join("\n"), { mode: 0o700 });
  chmodSync(bin, 0o700);
  return bin;
}

const maxCost = calls => calls.reduce((a, call) => Math.max(a, isNumber(call.reported?.costUsd) ? call.reported.costUsd : 0), 0);
/**
 * What `calls` count against a cap: each reported cost, and a call whose cost
 * went unreported (timed out, killed, or a result without one) at the
 * costliest reported call among them, so it is never counted as free.
 */
export function chargedCost(calls) {
  const most = maxCost(calls);
  return calls.reduce((a, call) => a + (isNumber(call.reported?.costUsd) ? call.reported.costUsd : most), 0);
}

// Whether a result states the cost that claudeCodeSpend takes from it: every
// model's costUSD when it names per-result figures, else its total.
function costStated(result) {
  const since = result?.model_usage_since_last_result;
  if (since && typeof since === "object") {
    const entries = Object.values(since);
    return entries.length > 0 && entries.every(entry => isNumber(entry?.costUSD));
  }
  return isNumber(result?.total_cost_usd);
}

/** Why the run stops after `call`, from what it reported (rate limits, failure, unreported cost, a new version). */
export function stopsAfterCall(call, expectedVersion = null) {
  const reasons = rateLimitStops(call.rateLimits);
  if (!call.ok) reasons.push("call_failed");
  if (!isNumber(call.reported?.costUsd)) reasons.push("cost_unreported");
  if (expectedVersion && call.claudeCodeVersion && call.claudeCodeVersion !== expectedVersion) reasons.push("claude_code_version_changed");
  return reasons;
}

/**
 * The spend ledger: every call of every run under `base` of the same kind
 * (live runs together, dry runs together). A different --out is a different
 * ledger.
 */
export function ledgerCalls(base, live) {
  if (!existsSync(base)) return [];
  return readdirSync(base, { withFileTypes: true })
    .filter(entry => entry.isDirectory() && entry.name.endsWith("-dry-run") !== live)
    .flatMap(entry => Object.keys(PHASES).flatMap(phase => readJsonl(join(base, entry.name, phase, "calls.jsonl"))));
}

/**
 * The rate-limit stops that still hold before the first call of an
 * invocation, from the ledger's latest call and its latest call whose reports
 * could be read. A failed or interrupted last call that reported nothing
 * holds nothing by itself (the next call's own report is checked before the
 * one after it); a successful call that reported nothing holds, as nothing
 * about the account could be told from it.
 */
export function pendingHolds(ledger, nowMs = Date.now()) {
  const ordered = [...ledger].sort((a, b) => String(a.startedAt).localeCompare(String(b.startedAt)));
  const last = ordered.at(-1);
  if (!last) return [];
  const holds = (call, keepUnreported) => rateLimitStops(call.rateLimits)
    .filter(reason => RATE_REASONS.has(reason) && (keepUnreported || !UNREPORTED.has(reason)))
    .filter(reason => staleStopHolds(reason, call.rateLimits, nowMs));
  const read = ordered.findLast(call => !rateLimitStops(call.rateLimits).some(reason => UNREPORTED.has(reason)));
  return [...new Set([...holds(last, last.ok), ...(read && read !== last ? holds(read, false) : [])])];
}

function armAggregate(calls) {
  const ok = calls.filter(call => call.ok);
  const avg = values => (values.length ? values.reduce((a, b) => a + b, 0) / values.length : null);
  const thinking = ok.map(call => call.reported?.thinking).filter(isNumber);
  return {
    calls: calls.length,
    answered: ok.length,
    meanVisibleChars: avg(ok.map(call => call.visibleChars)),
    meanOutputTokens: avg(ok.map(call => call.reported?.output).filter(isNumber)),
    meanThinkingTokens: avg(thinking),
    thinkingStatedCalls: thinking.length,
    costUsd: usd(chargedCost(calls)),
  };
}

/** The aggregates-only summary of a phase: no prompt, no answer text, no rate-limit value. */
export function buildSummary({ plan, calls, ledgerPhaseUsd, totalSpentUsd, stop }) {
  const byModel = {};
  for (const call of calls) {
    for (const [model, entry] of Object.entries(call.reported?.byModel || {})) {
      byModel[model] = usd((byModel[model] || 0) + (isNumber(entry?.costUsd) ? entry.costUsd : 0));
    }
  }
  return {
    schema: "youspeak-onoff-summary/v0",
    benchmark: BENCHMARK_ID,
    phase: plan.phase,
    mode: plan.mode,
    model: plan.model,
    effort: plan.effort,
    claudeCodeVersions: [...new Set(calls.map(call => call.claudeCodeVersion).filter(Boolean))],
    registration: plan.registration,
    seed: plan.seed,
    pairsPlanned: plan.pairs,
    pairsComplete: completedPairs(calls).length,
    calls: calls.length,
    failedCalls: calls.filter(call => !call.ok).length,
    interimReached: plan.interimReached || null,
    interimPassed: plan.interimPassed || null,
    deviations: plan.deviations || [],
    stop,
    cost: {
      evidence: "estimated",
      basis: plan.mode === "live"
        ? "the sum of Claude Code's reported per-call cost at list price (on a subscription it is not a bill); phaseUsd is this run, ledgerPhaseUsd and totalUsd every live run under the output directory"
        : "the sum of the stand-in's per-call cost figures: a dry run spends nothing; ledgerPhaseUsd and totalUsd count every dry run under the output directory",
      phaseUsd: usd(chargedCost(calls)),
      ledgerPhaseUsd: usd(ledgerPhaseUsd),
      totalUsd: usd(totalSpentUsd),
      unreportedCostCalls: calls.filter(call => !isNumber(call.reported?.costUsd)).length,
      unreportedBasis: "a call whose cost went unreported counts at the costliest reported call beside it",
      byModel,
      capUsd: plan.capUsd,
      totalCapUsd: plan.totalCapUsd,
    },
    arms: { evidence: "estimated", on: armAggregate(calls.filter(c => c.arm === "on")), off: armAggregate(calls.filter(c => c.arm === "off")) },
    evidenceKey: EVIDENCE_KEY,
    note: "Aggregates only. Answer texts and per-call figures stay in calls.jsonl beside this file, outside the repository.",
  };
}

/**
 * Run (or resume) one phase. Resolves with the summary it wrote; throws only
 * for a refusal before any call (a cap raised, an output directory inside the
 * repository, an unregistered or changed live run, a Claude Code below the
 * floor). `signal` stops it as Ctrl-C does.
 */
export async function runOnOff({
  phase,
  live = false,
  fake = "replay",
  replayFile = REPLAY_FIXTURE,
  outDir = null,
  runId = "v0",
  seed = null,
  pairs = null,
  resume = false,
  pastInterim = false,
  capUsd = null,
  totalCapUsd = null,
  env = process.env,
  promptsPath = PROMPTS_PATH,
  timeoutMs = 10 * 60_000,
  signal = null,
  log = () => {},
} = {}) {
  const settings = PHASES[phase];
  if (!settings) throw new Error(`--phase must be one of ${Object.keys(PHASES).join(", ")}`);
  if (!live && !FAKES[fake]) throw new Error(`--fake must be one of ${Object.keys(FAKES).join(", ")}`);
  // The pre-registered caps can be lowered (a test, a smaller run), never raised.
  const cap = capUsd ?? settings.capUsd;
  const totalCap = totalCapUsd ?? TOTAL_CAP_USD;
  if (!(cap > 0) || cap > settings.capUsd) throw new Error(`the ${phase} cap is at most US$${settings.capUsd}; it can be lowered, never raised`);
  if (!(totalCap > 0) || totalCap > TOTAL_CAP_USD) throw new Error(`the total cap is at most US$${TOTAL_CAP_USD}; it can be lowered, never raised`);

  const base = outDir || defaultOutDir(env);
  const root = join(base, live ? runId : `${runId}-dry-run`);
  if (insideRepo(root)) throw new Error(`raw outputs never go inside the repository: ${root}`);
  const phaseDir = privateDir(join(root, phase));
  const planPath = join(phaseDir, "plan.json");
  const callsPath = join(phaseDir, "calls.jsonl");
  const summaryPath = join(phaseDir, "summary.json");
  const mode = live ? "live" : `dry-run:${fake}`;

  const { prompts, sha256: promptsSha256 } = loadPrompts(promptsPath);
  const rule = youspeakRule();
  const registration = {
    promptsSha256,
    ruleSha256: sha256(rule),
    baseSha256: sha256(BASE_SYSTEM_PROMPT),
    preregistrationSha256: existsSync(PREREGISTRATION_PATH) ? sha256(readFileSync(PREREGISTRATION_PATH, "utf-8")) : null,
  };
  const dirty = git(["status", "--porcelain", "--", ...REGISTERED_PATHS]);
  if (live && dirty !== "") {
    throw new Error("a live run needs the registration committed: "
      + `${dirty === null ? "git could not be read" : "uncommitted changes"} in ${REGISTERED_PATHS.join(", ")}`);
  }

  let plan;
  if (existsSync(planPath)) {
    if (!resume) throw new Error(`${phaseDir} already holds a run; pass --resume to continue it, or start another --run-id (the spend under ${base} carries over)`);
    plan = JSON.parse(readFileSync(planPath, "utf-8"));
    const changed = ["promptsSha256", "ruleSha256", "baseSha256", "preregistrationSha256"]
      .filter(key => plan.registration?.[key] !== registration[key]);
    if (changed.length > 0) throw new Error(`the registration changed since this run began (${changed.join(", ")}); start another --run-id (the spend under ${base} carries over)`);
    if (live) {
      const moved = registrationChangedSince(plan.registration?.commit);
      if (moved !== false) {
        throw new Error(`the registration changed since this run began: ${moved === null ? "git could not compare it" : `${REGISTERED_PATHS.join(", ")} differ from ${plan.registration.commit}`}; start another --run-id (the spend under ${base} carries over)`);
      }
    }
    if (plan.mode !== mode || plan.model !== settings.model || plan.effort !== settings.effort) {
      throw new Error(`this run is ${plan.mode} on ${plan.model} (${plan.effort}); it cannot be resumed as ${mode}`);
    }
    if (pairs !== null && pairs !== plan.pairs) throw new Error(`this run has ${plan.pairs} pairs; --pairs cannot change on resume`);
    // A cap the run was started below stays, unless lowered or named again.
    plan.capUsd = capUsd !== null ? cap : Math.min(plan.capUsd ?? cap, cap);
    plan.totalCapUsd = totalCapUsd !== null ? totalCap : Math.min(plan.totalCapUsd ?? totalCap, totalCap);
  } else {
    if (resume) throw new Error(`nothing to resume in ${phaseDir}`);
    const count = pairs ?? prompts.length;
    if (!Number.isInteger(count) || count < 1 || count > prompts.length) throw new Error(`--pairs must be 1 to ${prompts.length}`);
    const chosen = Number.isInteger(seed) ? seed : randomInt(2 ** 31);
    plan = {
      schema: "youspeak-onoff-plan/v0",
      benchmark: BENCHMARK_ID,
      phase,
      mode,
      model: settings.model,
      effort: settings.effort,
      capUsd: cap,
      totalCapUsd: totalCap,
      seed: chosen,
      pairs: count,
      interimAt: settings.interim && count >= 2 ? Math.floor(count / 2) : null,
      interimReached: null,
      interimPassed: null,
      deviations: [],
      registration: { ...registration, commit: git(["rev-parse", "HEAD"]), uncommitted: dirty !== "" },
      createdAt: new Date().toISOString(),
      order: planOrder(prompts, chosen),
    };
  }
  const savePlan = () => writePrivate(planPath, `${JSON.stringify(plan, null, 2)}\n`);
  savePlan();

  // The binary, and YOUI's version floor for the model.
  const childEnv = claudeEnv(env);
  let bin;
  if (live) {
    const found = resolveClaudeBinary({ env });
    if (!found.path) throw new Error(found.error);
    bin = found.path;
  } else {
    bin = dryRunBinary(join(phaseDir, "dry-run-bin"), fake, { replayFile, phaseDir });
  }
  if (live || fake === "fake") {
    const version = await readClaudeVersion(bin, { env: childEnv });
    const floor = claudeCodeMinVersion(settings.model);
    if (!version.ok) throw new Error(`could not read Claude Code's version: ${version.error}`);
    if (compareVersions(version.version, floor) < 0) throw new Error(`Claude Code ${version.version} is below ${floor}, the oldest YOUI runs for ${settings.model}`);
    // Claude Code updates itself: a resume on another version is a deviation, recorded.
    if (plan.claudeCodeVersion && plan.claudeCodeVersion !== version.version) {
      plan.deviations = [...(plan.deviations || []),
        { at: new Date().toISOString(), kind: "claude_code_version", from: plan.claudeCodeVersion, to: version.version }];
      log(`Claude Code is now ${version.version}; this run began on ${plan.claudeCodeVersion} (recorded as a deviation)`);
    }
    plan.claudeCodeVersion = version.version;
    savePlan();
  }
  const claudeCwd = join(phaseDir, "claude-cwd");

  const calls = readJsonl(callsPath);
  const byId = new Map(prompts.map(prompt => [prompt.id, prompt]));
  const done = new Set(completedPairs(calls).map(pair => pair.promptId));
  const attempts = new Map();
  for (const call of calls) attempts.set(call.promptId, Math.max(attempts.get(call.promptId) || 0, call.attempt));

  // A rate-limit stop from an earlier invocation, in any phase or run of the
  // ledger, holds until its window resets.
  let pending = pendingHolds(ledgerCalls(base, live));

  const controller = new AbortController();
  const onSignal = () => controller.abort(new Error("interrupted"));
  for (const name of SIGNALS) process.once(name, onSignal);
  if (signal?.aborted) onSignal();
  signal?.addEventListener("abort", onSignal, { once: true });
  let stop = null;
  try {
    outer:
    for (const step of plan.order.slice(0, plan.pairs)) {
      if (done.has(step.id)) continue;
      const prompt = byId.get(step.id);
      // The one interim look, before the pair that would pass the halfway
      // point. --past-interim counts only once the run has stopped there, so
      // the look cannot be skipped in advance.
      if (plan.interimAt !== null && !plan.interimPassed && done.size >= plan.interimAt) {
        if (pastInterim && plan.interimReached) {
          plan.interimPassed = { at: new Date().toISOString(), pairs: done.size };
        } else {
          plan.interimReached ||= { at: new Date().toISOString(), pairs: done.size };
          pending.push("interim_look");
        }
        savePlan();
      }
      const attempt = (attempts.get(step.id) || 0) + 1;
      attempts.set(step.id, attempt);
      const arms = step.first === "on" ? ["on", "off"] : ["off", "on"];
      for (const [position, arm] of arms.entries()) {
        const all = ledgerCalls(base, live);
        const phaseCalls = all.filter(call => call.phase === phase);
        const reasons = [
          ...pending,
          ...capStops({ spentUsd: chargedCost(phaseCalls), maxCallUsd: maxCost(phaseCalls), capUsd: plan.capUsd, name: "phase_cap" }),
          ...capStops({ spentUsd: chargedCost(all), maxCallUsd: maxCost(all), capUsd: plan.totalCapUsd, name: "total_cap" }),
        ];
        if (controller.signal.aborted) reasons.push("interrupted");
        if (reasons.length > 0) {
          stop = { reasons: [...new Set(reasons)], at: new Date().toISOString(), beforePrompt: step.id, beforeArm: arm };
          break outer;
        }

        const infos = [];
        const startedAt = new Date();
        let outcome = null;
        let thrown = null;
        try {
          outcome = await runClaudeCode({
            bin,
            cwd: claudeCwd,
            env: childEnv,
            model: settings.model,
            effort: settings.effort,
            systemPrompt: armSystemPrompt(arm, rule),
            persist: false,
            message: prompt.input,
            signal: controller.signal,
            timeoutMs,
            onEvent: event => { if (event.type === "rate_limit") infos.push(event.info); },
          });
        } catch (error) {
          // Stopped after its result: the result still says what the call cost.
          outcome = error?.claudeCodeOutcome || null;
          thrown = error;
        }
        const spend = outcome?.result ? claudeCodeSpend(outcome.result, null, { oneOff: true }) : null;
        const observed = outcome ? claudeCodeObservation(outcome, spend, settings.model) : null;
        const ok = !thrown && outcome?.ok === true && outcome.result?.stop_reason !== "refusal";
        const call = {
          phase,
          mode,
          model: settings.model,
          effort: settings.effort,
          promptId: step.id,
          stratum: step.stratum,
          arm,
          position,
          attempt,
          startedAt: startedAt.toISOString(),
          durationMs: Date.now() - startedAt.getTime(),
          ok,
          error: thrown ? String(thrown.message || thrown) : (outcome?.error || null),
          claudeCodeVersion: outcome?.init?.claude_code_version || null,
          text: outcome?.text || "",
          visibleChars: visibleChars(outcome?.text || ""),
          reported: {
            input: observed?.input ?? null,
            cacheRead: observed?.cacheRead ?? null,
            cacheCreation: observed?.cacheCreation ?? null,
            output: observed?.output ?? null,
            thinking: observed?.thinking ?? null,
            costUsd: costStated(outcome?.result) ? observed?.costUsd ?? null : null,
            byModel: spend?.byModel || null,
          },
          rateLimits: infos.length > 0 ? infos : (outcome?.rateLimit ? [outcome.rateLimit] : []),
        };
        appendFileSync(callsPath, `${JSON.stringify(call)}\n`, { mode: 0o600 });
        log(`${step.id} ${arm}: ${ok ? "answered" : `failed (${call.error})`}`);
        plan.claudeCodeVersion ||= call.claudeCodeVersion || undefined;
        pending = stopsAfterCall(call, plan.claudeCodeVersion);
        if (controller.signal.aborted) pending.push("interrupted");
      }
      // Done only when this attempt's two arms both answered (on one version).
      if (completedPairs(readJsonl(callsPath)).some(pair => pair.promptId === step.id && pair.attempt === attempt)) done.add(step.id);
    }
    // The last call's stops, when nothing was left to run.
    if (!stop && pending.length > 0) stop = { reasons: [...new Set(pending)], at: new Date().toISOString(), beforePrompt: null, beforeArm: null };
  } finally {
    for (const name of SIGNALS) process.removeListener(name, onSignal);
    signal?.removeEventListener("abort", onSignal);
  }
  savePlan();

  const ledger = ledgerCalls(base, live);
  const summary = buildSummary({
    plan, calls: readJsonl(callsPath), stop,
    ledgerPhaseUsd: chargedCost(ledger.filter(call => call.phase === phase)), totalSpentUsd: chargedCost(ledger),
  });
  writePrivate(summaryPath, `${JSON.stringify(summary, null, 2)}\n`);
  return { summary, phaseDir, summaryPath };
}

// ── CLI ──────────────────────────────────────────────────────────────

const USAGE = `node benchmarks/youspeak-onoff-v0/harness.mjs --phase pilot|opus [options]

  --live                run the real Claude Code (spends plan limits); without it, a dry run
  --fake replay|fake    the dry run's stand-in (default replay: the recorded stream)
  --replay-file PATH    the stream the replay stand-in plays
  --out DIR             where raw outputs go (default ~/.local/share/youi/onoff, or YOUI_ONOFF_DIR)
  --run-id ID           one run's directory under --out (default v0; a dry run adds -dry-run);
                        the caps count every run under --out
  --seed N              the order's seed for a new phase (default: random, recorded)
  --pairs N             how many prompts this phase runs (default: all)
  --resume              continue a phase that stopped
  --past-interim        go on after the interim look
  --cap-usd X           lower the phase cap (a resume keeps a lower one unless named again)
  --total-cap-usd X     lower the total cap (likewise)`;

export function parseArgs(argv) {
  const options = {};
  const number = (name, value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) throw new Error(`${name} needs a number`);
    return parsed;
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const value = () => {
      const next = argv[++index];
      if (next === undefined) throw new Error(`${arg} needs a value`);
      return next;
    };
    switch (arg) {
      case "--phase": options.phase = value(); break;
      case "--live": options.live = true; break;
      case "--fake": options.fake = value(); break;
      case "--replay-file": options.replayFile = value(); break;
      case "--out": options.outDir = value(); break;
      case "--run-id": options.runId = value(); break;
      case "--seed": options.seed = number(arg, value()); break;
      case "--pairs": options.pairs = number(arg, value()); break;
      case "--resume": options.resume = true; break;
      case "--past-interim": options.pastInterim = true; break;
      case "--cap-usd": options.capUsd = number(arg, value()); break;
      case "--total-cap-usd": options.totalCapUsd = number(arg, value()); break;
      case "--help": case "-h": options.help = true; break;
      default: throw new Error(`unknown option: ${arg}`);
    }
  }
  return options;
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help || !options.phase) {
      console.log(USAGE);
      process.exit(options.help ? 0 : 2);
    }
    const { summary, summaryPath } = await runOnOff({ ...options, log: line => console.error(line) });
    console.log(JSON.stringify({ summaryPath, pairsComplete: summary.pairsComplete, calls: summary.calls, stop: summary.stop,
      phaseUsd: summary.cost.phaseUsd, totalUsd: summary.cost.totalUsd }, null, 2));
  } catch (error) {
    console.error(`onoff: ${error.message}`);
    process.exit(1);
  }
}
