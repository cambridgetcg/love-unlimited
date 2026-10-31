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
// refused. summary.json there holds aggregates only.
//
// Without --live it is a dry run on tests/fixtures/replay-claude.mjs (the
// recorded stream; --fake fake for tests/fixtures/fake-claude.mjs), which
// makes no network call and spends nothing. --live runs the real Claude Code
// and refuses unless the registration (this directory, the rule and the
// runner) is committed and Claude Code meets YOUI's version floor.

import { execFileSync } from "node:child_process";
import { randomInt } from "node:crypto";
import { appendFileSync, chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
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
// What the registration is: a live run refuses while any of these has uncommitted changes.
const REGISTERED_PATHS = ["benchmarks/youspeak-onoff-v0", "youspeak-rule.mjs", "youi-web/claude-code.mjs"];
const RATE_REASONS = new Set([
  "overage", "status_not_allowed", "five_hour_over_0.5", "seven_day_over_0.8",
  "rate_limit_unreported", "rate_limit_window_unreported",
]);

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

const sumCost = calls => calls.reduce((a, call) => a + (isNumber(call.reported?.costUsd) ? call.reported.costUsd : 0), 0);
const maxCost = calls => calls.reduce((a, call) => Math.max(a, isNumber(call.reported?.costUsd) ? call.reported.costUsd : 0), 0);

/** Why the run stops after `call`, from what it reported (rate limits, failure, unreported cost). */
export function stopsAfterCall(call) {
  const reasons = rateLimitStops(call.rateLimits);
  if (!call.ok) reasons.push("call_failed");
  if (!isNumber(call.reported?.costUsd)) reasons.push("cost_unreported");
  return reasons;
}

// Every phase's calls under one run root (the total cap's ledger).
function rootCalls(root) {
  return Object.keys(PHASES).flatMap(phase => readJsonl(join(root, phase, "calls.jsonl")));
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
    costUsd: usd(sumCost(calls)),
  };
}

/** The aggregates-only summary of a phase: no prompt, no answer text, no rate-limit value. */
export function buildSummary({ plan, calls, totalSpentUsd, stop }) {
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
    stop,
    cost: {
      evidence: "estimated",
      basis: plan.mode === "live"
        ? "the sum of Claude Code's reported per-call cost at list price (on a subscription it is not a bill)"
        : "the sum of the stand-in's per-call cost figures: a dry run spends nothing",
      phaseUsd: usd(sumCost(calls)),
      totalUsd: usd(totalSpentUsd),
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
 * repository, an unregistered live run, a Claude Code below the floor).
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

  const root = join(outDir || defaultOutDir(env), live ? runId : `${runId}-dry-run`);
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
    if (!resume) throw new Error(`${phaseDir} already holds a run; pass --resume to continue it, or another --run-id`);
    plan = JSON.parse(readFileSync(planPath, "utf-8"));
    const changed = ["promptsSha256", "ruleSha256", "baseSha256", "preregistrationSha256"]
      .filter(key => plan.registration?.[key] !== registration[key]);
    if (changed.length > 0) throw new Error(`the registration changed since this run began (${changed.join(", ")}); start another --run-id`);
    if (plan.mode !== mode || plan.model !== settings.model || plan.effort !== settings.effort) {
      throw new Error(`this run is ${plan.mode} on ${plan.model} (${plan.effort}); it cannot be resumed as ${mode}`);
    }
    if (pairs !== null && pairs !== plan.pairs) throw new Error(`this run has ${plan.pairs} pairs; --pairs cannot change on resume`);
    plan.capUsd = cap;
    plan.totalCapUsd = totalCap;
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
    plan.claudeCodeVersion = version.version;
    savePlan();
  }
  const claudeCwd = join(phaseDir, "claude-cwd");

  const calls = readJsonl(callsPath);
  const byId = new Map(prompts.map(prompt => [prompt.id, prompt]));
  const done = new Set(completedPairs(calls).map(pair => pair.promptId));
  const attempts = new Map();
  for (const call of calls) attempts.set(call.promptId, Math.max(attempts.get(call.promptId) || 0, call.attempt));

  // A rate-limit stop from an earlier invocation holds until its window resets.
  const last = calls.at(-1);
  let pending = last
    ? rateLimitStops(last.rateLimits).filter(reason => RATE_REASONS.has(reason) && staleStopHolds(reason, last.rateLimits))
    : [];

  const controller = new AbortController();
  const onSignal = () => controller.abort(new Error("interrupted"));
  process.once("SIGINT", onSignal);
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
        const all = rootCalls(root);
        const phaseCalls = readJsonl(callsPath);
        const reasons = [
          ...pending,
          ...capStops({ spentUsd: sumCost(phaseCalls), maxCallUsd: maxCost(phaseCalls), capUsd: cap, name: "phase_cap" }),
          ...capStops({ spentUsd: sumCost(all), maxCallUsd: maxCost(all), capUsd: totalCap, name: "total_cap" }),
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
            costUsd: observed?.costUsd ?? null,
            byModel: spend?.byModel || null,
          },
          rateLimits: infos.length > 0 ? infos : (outcome?.rateLimit ? [outcome.rateLimit] : []),
        };
        appendFileSync(callsPath, `${JSON.stringify(call)}\n`, { mode: 0o600 });
        log(`${step.id} ${arm}: ${ok ? "answered" : `failed (${call.error})`}`);
        pending = stopsAfterCall(call);
        if (controller.signal.aborted) pending.push("interrupted");
      }
      done.add(step.id);
    }
    // The last call's stops, when nothing was left to run.
    if (!stop && pending.length > 0) stop = { reasons: [...new Set(pending)], at: new Date().toISOString(), beforePrompt: null, beforeArm: null };
  } finally {
    process.removeListener("SIGINT", onSignal);
  }

  const summary = buildSummary({ plan, calls: readJsonl(callsPath), totalSpentUsd: sumCost(rootCalls(root)), stop });
  writePrivate(summaryPath, `${JSON.stringify(summary, null, 2)}\n`);
  return { summary, phaseDir, summaryPath };
}

// ── CLI ──────────────────────────────────────────────────────────────

const USAGE = `node benchmarks/youspeak-onoff-v0/harness.mjs --phase pilot|opus [options]

  --live                run the real Claude Code (spends plan limits); without it, a dry run
  --fake replay|fake    the dry run's stand-in (default replay: the recorded stream)
  --replay-file PATH    the stream the replay stand-in plays
  --out DIR             where raw outputs go (default ~/.local/share/youi/onoff, or YOUI_ONOFF_DIR)
  --run-id ID           one run's directory under --out (default v0; a dry run adds -dry-run)
  --seed N              the order's seed for a new phase (default: random, recorded)
  --pairs N             how many prompts this phase runs (default: all)
  --resume              continue a phase that stopped
  --past-interim        go on after the interim look
  --cap-usd X           lower the phase cap
  --total-cap-usd X     lower the total cap`;

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
