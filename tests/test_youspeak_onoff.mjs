// The YOUSPEAK ON/OFF test's R0 (benchmarks/youspeak-onoff-v0/): the arms,
// every stop rule, the shared spend ledger, the dry run, the interim look,
// resuming, and the paired statistics.
//
// Every `claude` here is a stand-in: tests/fixtures/replay-claude.mjs playing
// the recorded stream (or a copy of it with the rate_limit_event changed), or
// tests/fixtures/fake-claude.mjs. No test passes `live`, so none can reach the
// installed Claude Code. Raw outputs go to temporary directories, and the
// repository's git status is checked unchanged.

import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { YOUSPEAK_RULE } from "../youspeak-rule.mjs";
import {
  BASE_SYSTEM_PROMPT,
  FIVE_HOUR_STOP,
  PHASES,
  SEVEN_DAY_STOP,
  TOTAL_CAP_USD,
  analysePairs,
  armSystemPrompt,
  bootstrapMean,
  capStops,
  completedPairs,
  insideRepo,
  loadPrompts,
  planOrder,
  rateLimitStops,
  staleStopHolds,
  suggestPairs,
  visibleChars,
} from "../benchmarks/youspeak-onoff-v0/onoff.mjs";
import {
  REPLAY_FIXTURE,
  chargedCost,
  pendingHolds,
  registrationChangedSince,
  runOnOff,
} from "../benchmarks/youspeak-onoff-v0/harness.mjs";
import { analysePhase, blindPacket } from "../benchmarks/youspeak-onoff-v0/analyze.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const HARNESS = join(REPO_ROOT, "benchmarks/youspeak-onoff-v0/harness.mjs");
const ANALYZE = join(REPO_ROOT, "benchmarks/youspeak-onoff-v0/analyze.mjs");
const { prompts: PROMPTS } = loadPrompts();
const ALLOWED = Object.freeze({
  status: "allowed",
  isUsingOverage: false,
  unifiedWindows: {
    five_hour: { utilization: 0.2, resetsAt: 1800003600, status: "allowed" },
    seven_day: { utilization: 0.3, resetsAt: 1800500000, status: "allowed" },
  },
});

function scratch(t, prefix = "onoff-test-") {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

function readJsonl(path) {
  return existsSync(path) ? readFileSync(path, "utf-8").split("\n").filter(Boolean).map(line => JSON.parse(line)) : [];
}

// The recorded stream with its rate_limit_event changed by `edit` (null: dropped).
function replayWith(dir, edit) {
  const lines = readFileSync(REPLAY_FIXTURE, "utf-8").split("\n").filter(Boolean).flatMap(line => {
    const event = JSON.parse(line);
    if (event.type !== "rate_limit_event") return [line];
    const info = edit(structuredClone(event.rate_limit_info));
    return info ? [JSON.stringify({ ...event, rate_limit_info: info })] : [];
  });
  const path = join(dir, `replay-${Math.random().toString(36).slice(2)}.jsonl`);
  writeFileSync(path, `${lines.join("\n")}\n`);
  return path;
}

// The recorded stream's lines with each event passed through `edit` (null: dropped).
function streamWith(edit) {
  return readFileSync(REPLAY_FIXTURE, "utf-8").split("\n").filter(Boolean).flatMap(line => {
    const event = edit(JSON.parse(line));
    return event ? [JSON.stringify(event)] : [];
  });
}
const RECORDED = streamWith(event => event);
const FAILED = streamWith(event => (event.type === "result" ? { ...event, is_error: true, subtype: "error_during_execution", result: "stand-in failure" } : event));
// Cut before the rate_limit_event, as a call killed after its answer but before its result is: it never finishes.
const CUT = streamWith(event => (event.type === "rate_limit_event" || event.type === "result" ? null : event));
const withoutCost = event => {
  if (event.type !== "result") return event;
  const { total_cost_usd: _total, ...rest } = event;
  const strip = usage => usage && Object.fromEntries(Object.entries(usage).map(([model, { costUSD: _cost, ...entry }]) => [model, entry]));
  return { ...rest, modelUsage: strip(rest.modelUsage), model_usage_since_last_result: strip(rest.model_usage_since_last_result) };
};

// A replay file whose stream changes between calls: `streamFor(n)` gives call n's lines (from 1).
// Its `log` goes to runOnOff, which logs once after each call.
function switchingReplay(dir, streamFor) {
  const path = join(dir, `switch-${Math.random().toString(36).slice(2)}.jsonl`);
  let call = 1;
  const write = () => writeFileSync(path, `${streamFor(call).join("\n")}\n`);
  write();
  return { path, log: () => { call += 1; write(); } };
}

function gitStatus() {
  return execFileSync("git", ["status", "--porcelain", "--untracked-files=all"], { cwd: REPO_ROOT, encoding: "utf-8" });
}

// ── The arms ────────────────────────────────────────────────────────

test("the arms share a non-empty base, and ON adds exactly the rule", () => {
  assert.ok(BASE_SYSTEM_PROMPT.trim().length > 0);
  assert.equal(armSystemPrompt("off"), BASE_SYSTEM_PROMPT);
  assert.equal(armSystemPrompt("on"), `${BASE_SYSTEM_PROMPT}\n\n${YOUSPEAK_RULE}`);
  assert.throws(() => armSystemPrompt("maybe"), /unknown arm/);
});

test("each call runs as YOUI runs Claude Code, and the arms differ only in the system prompt", async t => {
  const out = scratch(t);
  const before = gitStatus();
  const { summary, phaseDir } = await runOnOff({
    phase: "pilot", fake: "fake", outDir: out, seed: 11, pairs: 3,
    env: { ...process.env, ANTHROPIC_API_KEY: "sk-test-not-a-key", CLAUDE_CODE_OAUTH_TOKEN: "not-a-token" },
  });
  assert.equal(summary.pairsComplete, 3);
  assert.equal(summary.claudeCodeVersions[0], "2.1.314");
  const starts = readJsonl(join(phaseDir, "fake-claude.jsonl")).filter(entry => entry.step === "start");
  const messages = readJsonl(join(phaseDir, "fake-claude.jsonl")).filter(entry => entry.step === "message");
  assert.equal(starts.length, 6);
  const maskArgv = argv => argv.map((arg, i) => (argv[i - 1] === "--system-prompt-file" ? "<file>" : arg));
  for (let i = 0; i < starts.length; i += 2) {
    const [a, b] = [starts[i], starts[i + 1]];
    assert.deepEqual(maskArgv(a.argv), maskArgv(b.argv));
    assert.deepEqual(a.env, b.env);
    assert.equal(a.cwd, b.cwd);
    assert.equal(messages[i].message, messages[i + 1].message);
    const prompts = [a.systemPrompt.content, b.systemPrompt.content].sort();
    assert.deepEqual(prompts, [BASE_SYSTEM_PROMPT, `${BASE_SYSTEM_PROMPT}\n\n${YOUSPEAK_RULE}`].sort());
    assert.equal(a.systemPrompt.mode, 0o600);
  }
  const argv = starts[0].argv;
  assert.equal(argv[argv.indexOf("--model") + 1], "claude-haiku-5-5");
  assert.equal(argv[argv.indexOf("--effort") + 1], "high", "the pilot names its effort; Haiku 5.5 is not in modelCaps");
  assert.equal(argv[argv.indexOf("--tools") + 1], "");
  assert.ok(argv.includes("--no-session-persistence"));
  assert.ok(!argv.includes("--mcp-config"));
  const env = starts[0].env;
  assert.equal(env.CLAUDE_CODE_DISABLE_CLAUDE_MDS, "1");
  assert.equal(env.CLAUDE_CODE_DISABLE_AUTO_MEMORY, "1");
  assert.equal(env.CLAUDE_CODE_DISABLE_FILE_MENTIONS, "1");
  assert.equal(env.ANTHROPIC_API_KEY, undefined);
  assert.equal(env.CLAUDE_CODE_OAUTH_TOKEN, undefined);
  // The working directory is the run's own, empty, 0700.
  assert.equal(starts[0].cwd, realpathSync(join(phaseDir, "claude-cwd")));
  assert.equal(statSync(starts[0].cwd).mode & 0o777, 0o700);
  assert.deepEqual(readdirSync(starts[0].cwd), []);
  assert.equal(gitStatus(), before);
});

test("arm order is seeded per prompt and every first n is spread across the strata", () => {
  const a = planOrder(PROMPTS, 42);
  assert.deepEqual(planOrder(PROMPTS, 42), a);
  assert.notDeepEqual(planOrder(PROMPTS, 43), a);
  assert.equal(a.length, PROMPTS.length);
  assert.equal(new Set(a.map(step => step.id)).size, PROMPTS.length);
  const strata = new Set(PROMPTS.map(prompt => prompt.stratum));
  assert.equal(new Set(a.slice(0, strata.size).map(step => step.stratum)).size, strata.size);
  const firsts = a.map(step => step.first);
  assert.ok(firsts.includes("on") && firsts.includes("off"));
});

test("the prompt set has 60+ prompts in six strata of twelve, a Cantonese stratum included", () => {
  assert.ok(PROMPTS.length >= 60);
  const counts = {};
  for (const prompt of PROMPTS) counts[prompt.stratum] = (counts[prompt.stratum] || 0) + 1;
  assert.deepEqual(Object.keys(counts).sort(), ["cantonese", "code", "emotional", "explanation", "factual", "planning"]);
  for (const count of Object.values(counts)) assert.equal(count, 12);
  assert.ok(PROMPTS.filter(prompt => prompt.stratum === "cantonese").every(prompt => /[一-鿿]/u.test(prompt.input)));
  assert.ok(PROMPTS.filter(prompt => prompt.stratum === "factual").every(prompt => prompt.check));
});

// ── Stop rules ──────────────────────────────────────────────────────

test("rateLimitStops: each rule, its boundary, and unreported state", () => {
  assert.deepEqual(rateLimitStops([ALLOWED]), []);
  const withWindows = (five, seven, extra = {}) => ({ ...ALLOWED, ...extra, unifiedWindows: {
    five_hour: { ...ALLOWED.unifiedWindows.five_hour, utilization: five },
    seven_day: { ...ALLOWED.unifiedWindows.seven_day, utilization: seven },
  } });
  assert.deepEqual(rateLimitStops([withWindows(FIVE_HOUR_STOP, SEVEN_DAY_STOP)]), [], "at the threshold is not past it");
  assert.deepEqual(rateLimitStops([withWindows(0.51, 0.3)]), ["five_hour_over_0.5"]);
  assert.deepEqual(rateLimitStops([withWindows(0.2, 0.81)]), ["seven_day_over_0.8"]);
  assert.deepEqual(rateLimitStops([{ ...ALLOWED, isUsingOverage: true }]), ["overage"]);
  assert.deepEqual(rateLimitStops([{ ...ALLOWED, status: "allowed_warning" }]), ["status_not_allowed"]);
  assert.deepEqual(rateLimitStops([{ ...ALLOWED, status: "rejected" }]), ["status_not_allowed"]);
  assert.deepEqual(rateLimitStops([withWindows(0.2, 0.3), { ...ALLOWED, isUsingOverage: true }]), ["overage"], "any report in the call counts");
  assert.deepEqual(rateLimitStops([]), ["rate_limit_unreported"]);
  assert.deepEqual(rateLimitStops([{ status: "allowed", isUsingOverage: false }]), ["rate_limit_window_unreported"]);
  const { isUsingOverage: _overage, ...noOverage } = ALLOWED;
  assert.deepEqual(rateLimitStops([noOverage]), ["overage_unreported"], "an overage state not stated is not a pass");
  const windowRejected = { ...ALLOWED, unifiedWindows: { ...ALLOWED.unifiedWindows, seven_day: { ...ALLOWED.unifiedWindows.seven_day, status: "rejected" } } };
  assert.deepEqual(rateLimitStops([windowRejected]), ["status_not_allowed"], "a window's own status counts");
});

test("pendingHolds: the latest report that could be read holds, and a failed call that reported nothing does not", () => {
  const future = Math.floor(Date.now() / 1000) + 3600;
  const high = { ...ALLOWED, unifiedWindows: { ...ALLOWED.unifiedWindows, five_hour: { utilization: 0.6, resetsAt: future, status: "allowed" } } };
  const call = (startedAt, rateLimits, ok = true) => ({ startedAt, rateLimits, ok });
  assert.deepEqual(pendingHolds([]), []);
  assert.deepEqual(pendingHolds([call("2026-01-01T00:00:01Z", [ALLOWED]), call("2026-01-01T00:00:02Z", [], false)]), []);
  assert.deepEqual(pendingHolds([call("2026-01-01T00:00:01Z", [high]), call("2026-01-01T00:00:02Z", [], false)]), ["five_hour_over_0.5"]);
  assert.deepEqual(pendingHolds([call("2026-01-01T00:00:01Z", [ALLOWED]), call("2026-01-01T00:00:02Z", [])]), ["rate_limit_unreported"],
    "a call that answered without any report holds");
  assert.deepEqual(pendingHolds([call("2026-01-01T00:00:02Z", [ALLOWED]), call("2026-01-01T00:00:01Z", [high])]), [], "ordered by start time");
});

test("chargedCost counts a call whose cost went unreported at the costliest reported call", () => {
  const call = costUsd => ({ reported: { costUsd } });
  assert.equal(chargedCost([call(1), call(null), call(0.5)]), 2.5);
  assert.equal(chargedCost([call(null)]), 0);
  assert.equal(chargedCost([]), 0);
});

test("capStops: at the cap, and when the costliest call so far would pass it", () => {
  assert.deepEqual(capStops({ spentUsd: 0, maxCallUsd: 0, capUsd: 1, name: "phase_cap" }), []);
  assert.deepEqual(capStops({ spentUsd: 1, maxCallUsd: 0, capUsd: 1, name: "phase_cap" }), ["phase_cap"]);
  assert.deepEqual(capStops({ spentUsd: 0.9, maxCallUsd: 0.2, capUsd: 1, name: "phase_cap" }), ["phase_cap"]);
  assert.deepEqual(capStops({ spentUsd: 0.7, maxCallUsd: 0.2, capUsd: 1, name: "phase_cap" }), []);
});

test("the pre-registered caps can be lowered, never raised", async t => {
  const out = scratch(t);
  assert.deepEqual([PHASES.pilot.capUsd, PHASES.opus.capUsd, TOTAL_CAP_USD], [1, 15, 20]);
  await assert.rejects(runOnOff({ phase: "pilot", outDir: out, capUsd: 1.5 }), /never raised/);
  await assert.rejects(runOnOff({ phase: "opus", outDir: out, capUsd: 16 }), /never raised/);
  await assert.rejects(runOnOff({ phase: "pilot", outDir: out, totalCapUsd: 21 }), /never raised/);
  assert.deepEqual(readdirSync(out), [], "a refusal runs and writes nothing");
});

for (const [name, edit, reason] of [
  ["isUsingOverage true", info => ({ ...info, isUsingOverage: true }), "overage"],
  ["a status other than allowed", info => ({ ...info, status: "allowed_warning" }), "status_not_allowed"],
  ["five-hour utilisation past 0.5", info => ({ ...info, unifiedWindows: { ...info.unifiedWindows, five_hour: { ...info.unifiedWindows.five_hour, utilization: 0.51 } } }), "five_hour_over_0.5"],
  ["seven-day utilisation past 0.8", info => ({ ...info, unifiedWindows: { ...info.unifiedWindows, seven_day: { ...info.unifiedWindows.seven_day, utilization: 0.81 } } }), "seven_day_over_0.8"],
  ["no rate_limit_event", () => null, "rate_limit_unreported"],
]) {
  test(`a run stops before the next call on ${name}`, async t => {
    const dir = scratch(t);
    const { summary, phaseDir } = await runOnOff({ phase: "pilot", outDir: dir, seed: 5, pairs: 3, replayFile: replayWith(dir, edit) });
    assert.equal(summary.calls, 1);
    assert.ok(summary.stop.reasons.includes(reason), JSON.stringify(summary.stop));
    assert.equal(readJsonl(join(phaseDir, "calls.jsonl")).length, 1);
  });
}

test("a run stops at the phase cap and at the total cap, before the call that would pass it", async t => {
  // Each replayed call reports US$0.000123.
  const phase = await runOnOff({ phase: "pilot", outDir: scratch(t), runId: "phase", seed: 5, pairs: 3, capUsd: 0.0003 });
  assert.equal(phase.summary.calls, 2);
  assert.deepEqual(phase.summary.stop.reasons, ["phase_cap"]);
  const dir = scratch(t);
  const total = await runOnOff({ phase: "pilot", outDir: dir, runId: "total", seed: 5, pairs: 3, totalCapUsd: 0.0002 });
  assert.equal(total.summary.calls, 1);
  assert.deepEqual(total.summary.stop.reasons, ["total_cap"]);
  // The total counts every phase of the run: the Opus phase starts with the pilot's spend.
  const opus = await runOnOff({ phase: "opus", outDir: dir, runId: "total", seed: 5, pairs: 3, totalCapUsd: 0.0002 });
  assert.equal(opus.summary.calls, 0);
  assert.deepEqual(opus.summary.stop.reasons, ["total_cap"]);
});

test("the caps count every run under the output directory: a new run-id starts with the spend before it", async t => {
  const dir = scratch(t);
  const first = await runOnOff({ phase: "pilot", outDir: dir, runId: "a", seed: 5, pairs: 3, totalCapUsd: 0.0002 });
  assert.deepEqual(first.summary.stop.reasons, ["total_cap"]);
  for (const phase of ["pilot", "opus"]) {
    const next = await runOnOff({ phase, outDir: dir, runId: "b", seed: 5, pairs: 3, totalCapUsd: 0.0002 });
    assert.equal(next.summary.calls, 0, `${phase} under another run-id makes no call`);
    assert.deepEqual(next.summary.stop.reasons, ["total_cap"]);
    assert.equal(next.summary.cost.totalUsd, 0.000123);
  }
  const phaseCap = await runOnOff({ phase: "pilot", outDir: dir, runId: "c", seed: 5, pairs: 3, capUsd: 0.0002 });
  assert.deepEqual(phaseCap.summary.stop.reasons, ["phase_cap"], "the phase cap counts that phase in every run");
  // Another --out is another ledger.
  const elsewhere = await runOnOff({ phase: "pilot", outDir: scratch(t), runId: "b", seed: 5, pairs: 1 });
  assert.equal(elsewhere.summary.stop, null);
});

test("a resume keeps a lower cap the run began with, unless --cap-usd names one again", async t => {
  const dir = scratch(t);
  const first = await runOnOff({ phase: "pilot", outDir: dir, seed: 5, pairs: 3, capUsd: 0.0003 });
  assert.deepEqual(first.summary.stop.reasons, ["phase_cap"]);
  const again = await runOnOff({ phase: "pilot", outDir: dir, resume: true });
  assert.equal(again.summary.calls, 2);
  assert.equal(again.summary.cost.capUsd, 0.0003);
  const named = await runOnOff({ phase: "pilot", outDir: dir, resume: true, capUsd: 1 });
  assert.equal(named.summary.pairsComplete, 3);
});

test("a rate-limit stop holds on resume until its window's reset time has passed", async t => {
  const dir = scratch(t);
  const high = reset => info => ({ ...info, unifiedWindows: { ...info.unifiedWindows,
    five_hour: { ...info.unifiedWindows.five_hour, utilization: 0.6, resetsAt: reset } } });
  const future = Math.floor(Date.now() / 1000) + 3600;
  const first = await runOnOff({ phase: "pilot", outDir: dir, runId: "held", seed: 5, pairs: 2, replayFile: replayWith(dir, high(future)) });
  assert.equal(first.summary.calls, 1);
  const again = await runOnOff({ phase: "pilot", outDir: dir, runId: "held", resume: true });
  assert.equal(again.summary.calls, 1, "no call while the window has not reset");
  assert.deepEqual(again.summary.stop.reasons, ["five_hour_over_0.5"]);
  assert.equal(staleStopHolds("five_hour_over_0.5", [{ unifiedWindows: { five_hour: { resetsAt: 1000 } } }]), false);

  const other = scratch(t);
  await runOnOff({ phase: "pilot", outDir: other, runId: "reset", seed: 5, pairs: 2, replayFile: replayWith(other, high(1000)) });
  const resumed = await runOnOff({ phase: "pilot", outDir: other, runId: "reset", resume: true });
  assert.equal(resumed.summary.pairsComplete, 2, "the half-done pair is run again in full");
  assert.equal(resumed.summary.calls, 5);
  assert.equal(resumed.summary.stop, null);
  await assert.rejects(runOnOff({ phase: "pilot", outDir: other, runId: "reset" }), /--resume/);
});

for (const [name, edit, reason] of [
  ["five-hour utilisation past 0.5", info => ({ ...info, unifiedWindows: { ...info.unifiedWindows,
    five_hour: { ...info.unifiedWindows.five_hour, utilization: 0.6, resetsAt: Math.floor(Date.now() / 1000) + 3600 } } }), "five_hour_over_0.5"],
  ["overage", info => ({ ...info, isUsingOverage: true }), "overage"],
]) {
  test(`a pilot's stop on ${name} holds for the Opus phase and for another run-id`, async t => {
    const dir = scratch(t);
    const pilot = await runOnOff({ phase: "pilot", outDir: dir, seed: 5, pairs: 2, replayFile: replayWith(dir, edit) });
    assert.deepEqual(pilot.summary.stop.reasons, [reason]);
    const opus = await runOnOff({ phase: "opus", outDir: dir, seed: 5, pairs: 2 });
    assert.equal(opus.summary.calls, 0);
    assert.deepEqual(opus.summary.stop.reasons, [reason]);
    const another = await runOnOff({ phase: "opus", outDir: dir, runId: "another", seed: 5, pairs: 2 });
    assert.equal(another.summary.calls, 0);
  });
}

test("a call that fails stops the run before the next call", async t => {
  const dir = scratch(t);
  const { summary } = await runOnOff({ phase: "pilot", outDir: dir, seed: 5, pairs: 2, replayFile: switchingReplay(dir, () => FAILED).path });
  assert.equal(summary.calls, 1);
  assert.deepEqual(summary.stop.reasons, ["call_failed"]);
  assert.equal(summary.failedCalls, 1);
});

for (const [name, edit] of [
  ["a result with no cost at all", withoutCost],
  ["per-model figures without costUSD", event => (event.type === "result" ? { ...withoutCost(event), modelUsage: event.modelUsage } : event)],
]) {
  test(`${name} stops the run, and after --resume the call still counts at the costliest reported call`, async t => {
    const dir = scratch(t);
    const first = await runOnOff({ phase: "pilot", outDir: dir, seed: 5, pairs: 2, replayFile: switchingReplay(dir, () => streamWith(edit)).path });
    assert.equal(first.summary.calls, 1);
    assert.deepEqual(first.summary.stop.reasons, ["cost_unreported"]);
    assert.equal(first.summary.cost.unreportedCostCalls, 1);
    const resumed = await runOnOff({ phase: "pilot", outDir: dir, resume: true });
    assert.equal(resumed.summary.calls, 5);
    assert.equal(resumed.summary.stop, null);
    assert.equal(resumed.summary.cost.phaseUsd, 0.000615, "five calls at US$0.000123, the unreported one included");
  });
}

test("an interrupted call that never reached its rate-limit report stops the run, and --resume goes on", async t => {
  const dir = scratch(t);
  const controller = new AbortController();
  // The recorded stream prints its rate_limit_event after the answer: the second call is cut before it and hangs.
  const replay = switchingReplay(dir, n => (n === 1 ? RECORDED : CUT));
  const first = await runOnOff({
    phase: "pilot", outDir: dir, seed: 5, pairs: 2, replayFile: replay.path, signal: controller.signal,
    log: () => { replay.log(); setTimeout(() => controller.abort(), 300); },
  });
  assert.equal(first.summary.calls, 2);
  for (const reason of ["interrupted", "call_failed", "rate_limit_unreported", "cost_unreported"]) {
    assert.ok(first.summary.stop.reasons.includes(reason), JSON.stringify(first.summary.stop));
  }
  const resumed = await runOnOff({ phase: "pilot", outDir: dir, resume: true });
  assert.equal(resumed.summary.pairsComplete, 2);
  assert.equal(resumed.summary.stop, null);
});

test("a change of Claude Code version between calls stops the run, and the mixed pair is not a pair", async t => {
  const dir = scratch(t);
  const newer = streamWith(event => (event.type === "system" && event.subtype === "init" ? { ...event, claude_code_version: "2.1.999" } : event));
  const replay = switchingReplay(dir, n => (n === 1 ? RECORDED : newer));
  const { summary } = await runOnOff({ phase: "pilot", outDir: dir, seed: 5, pairs: 2, replayFile: replay.path, log: replay.log });
  assert.equal(summary.calls, 2);
  assert.deepEqual(summary.stop.reasons, ["claude_code_version_changed"]);
  assert.equal(summary.pairsComplete, 0);
  assert.deepEqual(summary.claudeCodeVersions.sort(), ["2.1.314", "2.1.999"]);
});

test("a live resume refuses once the registered paths differ from the run's commit", t => {
  assert.equal(registrationChangedSince(execFileSync("git", ["rev-parse", "HEAD"], { cwd: REPO_ROOT, encoding: "utf-8" }).trim()), false);
  assert.equal(registrationChangedSince(null), null);
  const added = execFileSync("git", ["log", "--format=%H", "--diff-filter=A", "--", "benchmarks/youspeak-onoff-v0/onoff.mjs"],
    { cwd: REPO_ROOT, encoding: "utf-8" }).trim().split("\n").at(-1);
  if (!added) return t.skip("no git history for the benchmark here");
  assert.equal(registrationChangedSince(`${added}~1`), true);
});

test("the Opus phase stops once at the interim look, and --past-interim counts only after it", async t => {
  const dir = scratch(t);
  const early = await runOnOff({ phase: "opus", outDir: dir, seed: 9, pairs: 4, pastInterim: true });
  assert.equal(early.summary.pairsComplete, 2, "--past-interim given in advance does not skip the look");
  assert.deepEqual(early.summary.stop.reasons, ["interim_look"]);
  const later = await runOnOff({ phase: "opus", outDir: dir, resume: true });
  assert.equal(later.summary.pairsComplete, 2);
  assert.deepEqual(later.summary.stop.reasons, ["interim_look"]);
  const past = await runOnOff({ phase: "opus", outDir: dir, resume: true, pastInterim: true });
  assert.equal(past.summary.pairsComplete, 4);
  assert.equal(past.summary.stop, null);
  assert.equal(past.summary.interimPassed.pairs, 2);
});

test("a pair whose second arm fails is not done: the interim look still waits for interimAt complete pairs", async t => {
  const dir = scratch(t);
  const replay = switchingReplay(dir, n => (n === 4 ? FAILED : RECORDED));
  const first = await runOnOff({ phase: "opus", outDir: dir, seed: 9, pairs: 4, replayFile: replay.path, log: replay.log });
  assert.equal(first.summary.pairsComplete, 1);
  assert.deepEqual(first.summary.stop.reasons, ["call_failed"]);
  assert.equal(first.summary.interimReached, null);
  const look = await runOnOff({ phase: "opus", outDir: dir, resume: true, pastInterim: true });
  assert.equal(look.summary.pairsComplete, 2);
  assert.deepEqual(look.summary.stop.reasons, ["interim_look"]);
  assert.equal(look.summary.interimReached.pairs, 2);
  const past = await runOnOff({ phase: "opus", outDir: dir, resume: true, pastInterim: true });
  assert.equal(past.summary.pairsComplete, 4);
  assert.equal(past.summary.interimPassed.pairs, 2);
});

// ── The dry run, and where things are written ───────────────────────

test("the dry run completes at $0 through the CLI, writes aggregates only, and nothing inside the repository", async t => {
  const out = scratch(t);
  const before = gitStatus();
  const run = spawnSync(process.execPath, [HARNESS, "--phase", "pilot", "--out", out, "--seed", "3"], { encoding: "utf-8", timeout: 55_000 });
  assert.equal(run.status, 0, run.stderr);
  const phaseDir = join(out, "v0-dry-run", "pilot");
  const summary = JSON.parse(readFileSync(join(phaseDir, "summary.json"), "utf-8"));
  assert.equal(summary.mode, "dry-run:replay");
  assert.equal(summary.pairsComplete, PROMPTS.length);
  assert.equal(summary.calls, PROMPTS.length * 2);
  assert.equal(summary.stop, null);
  assert.equal(summary.cost.evidence, "estimated");
  assert.equal(summary.arms.evidence, "estimated");
  assert.ok(summary.evidenceKey.reported && summary.evidenceKey.estimated);

  const analyzed = spawnSync(process.execPath, [ANALYZE, "--phase", "pilot", "--out", out, "--dry-run"], { encoding: "utf-8" });
  assert.equal(analyzed.status, 0, analyzed.stderr);
  const analysis = JSON.parse(readFileSync(join(phaseDir, "analysis.json"), "utf-8"));
  assert.equal(analysis.pairs, PROMPTS.length);
  assert.equal(analysis.primary.evidence, "estimated");

  // Aggregates only: no prompt, no answer, no rate-limit value.
  const answers = new Set(readJsonl(join(phaseDir, "calls.jsonl")).map(call => call.text));
  for (const file of ["summary.json", "analysis.json"]) {
    const text = readFileSync(join(phaseDir, file), "utf-8");
    for (const prompt of PROMPTS) assert.ok(!text.includes(prompt.input), `${file} holds a prompt`);
    for (const answer of answers) assert.ok(!text.includes(`"${answer}"`), `${file} holds an answer`);
    assert.ok(!/utilization|rateLimits|isUsingOverage/.test(text), `${file} holds rate-limit values`);
  }
  for (const name of readdirSync(phaseDir)) {
    const mode = statSync(join(phaseDir, name)).mode & 0o777;
    assert.ok(mode === 0o600 || mode === 0o700, `${name} is ${mode.toString(8)}`);
  }
  assert.equal(gitStatus(), before, "nothing was written inside the repository");
});

test("raw outputs default to ~/.local/share/youi/onoff and never go inside the repository", async t => {
  const home = scratch(t);
  const { phaseDir } = await runOnOff({ phase: "pilot", pairs: 1, seed: 1, env: { ...process.env, HOME: home, YOUI_ONOFF_DIR: "" } });
  assert.equal(phaseDir, join(home, ".local", "share", "youi", "onoff", "v0-dry-run", "pilot"));
  assert.ok(existsSync(join(phaseDir, "calls.jsonl")));
  assert.ok(insideRepo(join(REPO_ROOT, "benchmarks", "anything")));
  assert.ok(!insideRepo(home));
  await assert.rejects(runOnOff({ phase: "pilot", outDir: join(REPO_ROOT, "onoff-raw") }), /never go inside the repository/);
  assert.ok(!existsSync(join(REPO_ROOT, "onoff-raw")));
});

// ── The statistics ──────────────────────────────────────────────────

function fakeCall(promptId, stratum, arm, chars, { output = chars / 4, thinking = 10, cost = 0.001, text = "x".repeat(chars), attempt = 1 } = {}) {
  return { promptId, stratum, arm, attempt, ok: true, visibleChars: chars, text, reported: { output, thinking, costUsd: cost } };
}

test("analysePairs: paired log ratios with a bootstrap interval, checks, and the next n", () => {
  const prompts = [
    { id: "a1", stratum: "a", input: "?", check: "\\bNa\\b" },
    { id: "a2", stratum: "a", input: "?" },
    { id: "b1", stratum: "b", input: "?" },
    { id: "b2", stratum: "b", input: "?" },
  ];
  const calls = [
    fakeCall("a1", "a", "on", 2, { text: "Na" }), fakeCall("a1", "a", "off", 30, { text: "The symbol is Na." }),
    fakeCall("a2", "a", "on", 50), fakeCall("a2", "a", "off", 100),
    fakeCall("b1", "b", "on", 200), fakeCall("b1", "b", "off", 100),
    fakeCall("b2", "b", "on", 99), fakeCall("b2", "b", "off", 99),
    // A superseded complete attempt, a half attempt and a failed call are not pairs.
    fakeCall("b2", "b", "on", 5, { attempt: 0 }), fakeCall("b2", "b", "off", 7, { attempt: 0 }),
    fakeCall("a2", "a", "on", 5, { attempt: 0 }),
    { ...fakeCall("b1", "b", "on", 1, { attempt: 2 }), ok: false },
  ];
  const result = analysePairs(calls, prompts, { seed: 3, capUsd: 1, plannedPairs: 8 });
  assert.equal(result.pairs, 4);
  const expected = [Math.log(3 / 31), Math.log(51 / 101), Math.log(201 / 101), 0];
  const meanD = expected.reduce((a, b) => a + b) / 4;
  assert.ok(Math.abs(result.primary.pooled.meanLogRatio - meanD) < 1e-12);
  assert.ok(Math.abs(result.primary.pooled.ratio - Math.exp(meanD)) < 1e-12);
  assert.ok(result.primary.pooled.ratioLow <= result.primary.pooled.ratio && result.primary.pooled.ratio <= result.primary.pooled.ratioHigh);
  assert.equal(result.primary.byStratum.a.onLower, 2);
  assert.equal(result.primary.byStratum.b.onHigher, 1);
  assert.equal(result.primary.evidence, "estimated");
  assert.deepEqual(result.checkable, { evidence: "estimated", basis: result.checkable.basis, pairs: 1, bothRight: 1, onOnly: 0, offOnly: 0 });
  assert.equal(result.interim.harm, false);
  assert.equal(result.interim.futility, false);
  assert.equal(analysePairs(calls, prompts, { seed: 3, capUsd: 0.005, plannedPairs: 8 }).interim.futility, true);
  assert.deepEqual(analysePairs(calls, prompts, { seed: 3 }).primary, result.primary, "the bootstrap is seeded");
  assert.equal(result.interim.evidence, "estimated");
  assert.equal(completedPairs([...calls].reverse()).find(pair => pair.promptId === "b2").attempt, 1, "the latest complete attempt, in any order");
  const mixed = [fakeCall("a1", "a", "on", 2), { ...fakeCall("a1", "a", "off", 3), claudeCodeVersion: "2.1.999" }];
  assert.deepEqual(completedPairs(mixed), [], "two versions in one pair are not a pair");
});

test("the interim harm verdict: ON loses three more checkable answers than OFF", async t => {
  const prompts = ["c1", "c2", "c3"].map(id => ({ id, stratum: "a", input: "?", check: "(?<![\\d.])42(?!\\.?\\d)" }));
  const calls = prompts.flatMap(({ id }) => [fakeCall(id, "a", "on", 5, { text: "no idea" }), fakeCall(id, "a", "off", 5, { text: "42." })]);
  const result = analysePairs(calls, prompts, { seed: 1 });
  assert.deepEqual([result.checkable.offOnly, result.interim.netCheckableLoss, result.interim.harm], [3, 3, true]);
  assert.equal(analysePairs(calls.slice(2), prompts, { seed: 1 }).interim.harm, false, "two is not enough");

  const out = scratch(t);
  const { summary, phaseDir } = await runOnOff({ phase: "opus", outDir: out, seed: 9, pairs: 4 });
  assert.deepEqual(summary.stop.reasons, ["interim_look"]);
  const callsPath = join(phaseDir, "calls.jsonl");
  const { prompts: all } = loadPrompts();
  const checked = all.filter(prompt => prompt.check).slice(0, 3);
  const rows = checked.flatMap(prompt => [fakeCall(prompt.id, prompt.stratum, "on", 2, { text: "?", attempt: 9 }),
    fakeCall(prompt.id, prompt.stratum, "off", 2, { text: { "onoff-fact-01": "Na", "onoff-fact-02": "6", "onoff-fact-03": "100" }[prompt.id], attempt: 9 })]);
  writeFileSync(callsPath, `${readFileSync(callsPath, "utf-8")}${rows.map(row => JSON.stringify(row)).join("\n")}\n`);
  const { analysis } = analysePhase({ phase: "opus", outDir: out, dryRun: true });
  assert.ok(analysis.interim.harm);
  assert.equal(analysis.interim.suggestion, "stop");
});

test("the check patterns accept a number with its unit attached and reject a longer number", () => {
  const { prompts } = loadPrompts();
  const check = id => new RegExp(prompts.find(prompt => prompt.id === id).check, "iu");
  for (const [id, good, bad] of [
    ["onoff-fact-02", ["6", "six sides", "6-sided"], ["16", "60"]],
    ["onoff-fact-03", ["100°C", "100 degrees", "It is 100."], ["1000", "100.5"]],
    ["onoff-fact-05", ["391", "= 391."], ["3910", "1391"]],
    ["onoff-fact-08", ["1024B", "1,024 bytes", "2^10 = 1024"], ["10240", "1024.5"]],
    ["onoff-fact-11", ["12", "12."], ["120", "0.12"]],
    ["onoff-code-03", ["3", "It prints 3."], ["13", "3.5"]],
  ]) {
    for (const text of good) assert.ok(check(id).test(text), `${id} should accept ${text}`);
    for (const text of bad) assert.ok(!check(id).test(text), `${id} should reject ${text}`);
  }
  assert.ok(prompts.filter(prompt => prompt.stratum === "cantonese").every(prompt => !prompt.check),
    "the Cantonese stratum is read by tokens and blind reading only");
});

test("suggestPairs follows the registered formula and bounds", () => {
  const { raw, pairs } = suggestPairs(0.45, 6, 72);
  assert.ok(Math.abs(raw - 60.1) < 0.1);
  assert.equal(pairs, 66, "rounded up to whole rounds of six strata");
  assert.equal(suggestPairs(0.1, 6, 72).pairs, 36, "at least six pairs a stratum");
  assert.equal(suggestPairs(2, 6, 72).pairs, 72, "at most the whole set");
  assert.equal(suggestPairs(null, 6, 72), null);
});

test("bootstrapMean is seeded and stratified; visibleChars counts code points", () => {
  const groups = [[1, 2, 3], [10, 11]];
  assert.deepEqual(bootstrapMean(groups, { seed: 4 }), bootstrapMean(groups, { seed: 4 }));
  assert.equal(bootstrapMean(groups).mean, 27 / 5);
  assert.deepEqual(bootstrapMean([[1]]), { mean: 1, low: null, high: null });
  assert.equal(visibleChars("返工好攰"), 4);
  assert.equal(visibleChars("ok 👍"), 4);
});

test("analysePhase writes the analysis and an optional blind packet beside the raw outputs", async t => {
  const out = scratch(t);
  await runOnOff({ phase: "pilot", outDir: out, seed: 2, pairs: 12, fake: "fake" });
  const { analysis, phaseDir } = analysePhase({ phase: "pilot", outDir: out, dryRun: true, blind: 3 });
  assert.equal(analysis.pairs, 12);
  const key = JSON.parse(readFileSync(join(phaseDir, "blind", "key.json"), "utf-8"));
  assert.ok(key.length > 0 && key.length <= 3);
  for (const item of key) assert.deepEqual([item.A, item.B].sort(), ["off", "on"]);
  const packet = readFileSync(join(phaseDir, "blind", "packet.md"), "utf-8");
  assert.ok(!/\b(on|off)\b arm/i.test(packet) && !packet.includes('"on"'));
  assert.deepEqual(blindPacket([], PROMPTS).key, []);
});
