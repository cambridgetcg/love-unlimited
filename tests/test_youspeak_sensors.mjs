// YOUSPEAK slice Y2 (docs/YOUSPEAK-OBSERVER-DESIGN.md §4; docs/YOUSPEAK.md):
// sensors on the usage the provider reports, every figure labelled reported
// or estimated, the cache-stability signal, the per-turn context block, minds
// in the row, and one shared rule module.
//
// The one recorded live stream is tests/fixtures/claude-code-stream-2.1.314.jsonl
// (and its --resume, …-resume.jsonl): Claude Code 2.1.314, Claude Haiku 5.5,
// recorded on 2026-10-31 and replayed here by tests/fixtures/replay-claude.mjs.
// Paths, socket paths and message and request ids were scrubbed. Its
// rate_limit_event values are synthetic: only that event's shape is from the
// live stream (the repository is public, and the recorded values were the
// account's own), and they are one made-up consistent set (RATE_LIMIT_INFO
// below). Its session id and event uuids are placeholders.
// Everything else uses tests/fixtures/fake-claude.mjs and local fakes of vLLM
// and Ollama.
// No network, no Keychain; stores are temporary files named by historyPath or
// YOUSPEAK_TEST_HISTORY_PATH, and the tracked youspeak-history.json and
// youspeak-config.json are checked unchanged.

import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir, userInfo } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import {
  EVIDENCE,
  FILLER_RATIO_FLOOR,
  HISTORY_SCHEMA,
  KNOWN_CONTEXT_WINDOWS,
  createKernel,
  readHistory,
  weakerEvidence,
} from "../youspeak-kernel.mjs";
import { YOUSPEAK_FILE_CACHE_LINE, YOUSPEAK_RULE, youspeakRule } from "../youspeak-rule.mjs";
import { claudeCodeObservation, claudeCodeSpend, runClaudeCode, usageFromClaudeCode } from "../youi-web/claude-code.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const FAKE = join(REPO_ROOT, "tests/fixtures/fake-claude.mjs");
const REPLAY = join(REPO_ROOT, "tests/fixtures/replay-claude.mjs");
const SHIFT_DATE = pathToFileURL(join(REPO_ROOT, "tests/fixtures/shift-date.mjs")).href;
const FIXTURE = join(REPO_ROOT, "tests/fixtures/claude-code-stream-2.1.314.jsonl");
const FIXTURE_RESUME = join(REPO_ROOT, "tests/fixtures/claude-code-stream-2.1.314-resume.jsonl");
const TRACKED = ["youspeak-history.json", "youspeak-config.json"].map(name => join(REPO_ROOT, name));
const LABELS = [EVIDENCE.reported, EVIDENCE.estimated];
// The fixtures' rate_limit_event.rate_limit_info: synthetic values in the live
// stream's shape. The top-level status and resetsAt are those of the window
// rateLimitType names, each surpassedThreshold is at or below its utilization,
// and the overage fields agree with one another.
const RATE_LIMIT_INFO = Object.freeze({
  status: "allowed",
  resetsAt: 1800003600,
  rateLimitType: "five_hour",
  utilization: 0.2,
  overageStatus: "allowed",
  overageDisabledReason: null,
  isUsingOverage: false,
  surpassedThreshold: 0.1,
  unifiedWindows: {
    five_hour: { utilization: 0.2, resetsAt: 1800003600, status: "allowed" },
    seven_day: { utilization: 0.3, resetsAt: 1800500000, status: "allowed", surpassedThreshold: 0.25 },
  },
  overageEnabled: true,
});

// ── Helpers ─────────────────────────────────────────────────────────

function fingerprint(paths) {
  return paths.map(path => ({
    path,
    hash: existsSync(path) ? createHash("sha256").update(readFileSync(path)).digest("hex") : null,
    mtime: existsSync(path) ? statSync(path).mtimeMs : null,
  }));
}

const testServers = new WeakMap();
async function stopServers(t) {
  await Promise.all((testServers.get(t) || []).map(child => new Promise(resolve => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve();
    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch {}
      resolve();
    }, 5_000);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill("SIGTERM");
  })));
}

function guardTracked(t) {
  const before = fingerprint(TRACKED);
  t.after(async () => {
    await stopServers(t);
    assert.deepEqual(fingerprint(TRACKED), before, "the tracked youspeak-history.json and youspeak-config.json are unchanged");
  });
}

function scratch(t, prefix) {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  t.after(async () => {
    await stopServers(t);
    rmSync(dir, { recursive: true, force: true });
  });
  return dir;
}

// A `claude` wrapper around a script, with the script's environment set.
function wrapper(dir, script, env) {
  const bin = join(dir, "claude");
  writeFileSync(bin, [
    `#!${process.execPath}`,
    ...Object.entries(env).map(([name, value]) => `process.env.${name} = ${JSON.stringify(value)};`),
    `import(${JSON.stringify(pathToFileURL(script).href)});`,
    "",
  ].join("\n"));
  chmodSync(bin, 0o755);
  return bin;
}

function fakeClaude(t) {
  const dir = scratch(t, "ys2-fake-claude-");
  const log = join(dir, "calls.jsonl");
  const bin = wrapper(dir, FAKE, { FAKE_CLAUDE_LOG: log, FAKE_CLAUDE_SESSIONS: join(dir, "sessions") });
  const steps = name => (existsSync(log) ? readFileSync(log, "utf-8").split("\n").filter(Boolean).map(line => JSON.parse(line)) : [])
    .filter(entry => entry.step === name);
  return { bin, cwd: join(dir, "claude-code-cwd"), runs: () => steps("start"), messages: () => steps("message") };
}

// The recorded stream, run through YOUI's runner exactly as a turn runs it.
async function replay(t, fixture, options = {}) {
  const dir = scratch(t, "ys2-replay-");
  const bin = wrapper(dir, REPLAY, { FAKE_CLAUDE_REPLAY: fixture });
  return runClaudeCode({
    bin,
    cwd: join(dir, "cwd"),
    env: { PATH: process.env.PATH, HOME: process.env.HOME },
    model: "claude-haiku-5-5",
    systemPrompt: "You are a test assistant. Answer in one short word.",
    message: "Reply with the single word ready.",
    persist: false,
    ...options,
  });
}

async function startServer(t, extraEnv = {}, nodeArgs = []) {
  const temp = scratch(t, "ys2-server-tmp-");
  const child = spawn(process.execPath, [...nodeArgs, "youi-web/server.mjs"], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      PORT: "0",
      YOUI_TEST: "1",
      TRUTH_DETECTOR_ENABLED: "0",
      AUTONOMOUS: "0",
      KINGDOM_AGENT: "alpha",
      YOUI_MODEL: "",
      LOVE_HOME: REPO_ROOT,
      YOUI_WORKDIR: REPO_ROOT,
      TMPDIR: temp,
      ...extraEnv,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (!testServers.has(t)) testServers.set(t, []);
  testServers.get(t).push(child);
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk.toString("utf-8"); });
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`YOUI test server timed out: ${stderr.slice(-500)}`)), 10_000);
    let stdout = "";
    child.stdout.on("data", chunk => {
      stdout += chunk.toString("utf-8");
      const match = stdout.match(/YOUI_TEST_READY\s+(\d+)\s+(\S+)/);
      if (!match) return;
      clearTimeout(timer);
      resolve(Number(match[1]));
    });
    child.once("exit", code => {
      clearTimeout(timer);
      reject(new Error(`YOUI test server exited ${code}: ${stderr.slice(-500)}`));
    });
  });
  return { port, child };
}

function request(port, { path = "/", method = "GET", headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(typeof body === "string" ? body : JSON.stringify(body));
    const requestHeaders = { ...headers };
    if (payload) {
      requestHeaders["Content-Type"] ||= "application/json";
      requestHeaders["Content-Length"] = payload.length;
    }
    const req = http.request({ hostname: "127.0.0.1", port, path, method, headers: requestHeaders }, res => {
      const chunks = [];
      res.on("data", chunk => chunks.push(chunk));
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf-8");
        let json = null;
        try { json = JSON.parse(text); } catch {}
        resolve({ status: res.statusCode, headers: res.headers, text, json });
      });
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function openSession(port, label, body = {}) {
  const origin = `http://127.0.0.1:${port}`;
  const first = await request(port);
  const pairs = (first.headers["set-cookie"] || []).map(value => value.split(";", 1)[0]);
  const csrfPair = pairs.find(value => value.startsWith("youi_csrf="));
  const browser = { header: pairs.join("; "), csrf: csrfPair ? decodeURIComponent(csrfPair.slice("youi_csrf=".length)) : "" };
  const page = `page-${label}-0000000000000001`;
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: { Cookie: browser.header, Origin: origin, "X-YOUI-CSRF": browser.csrf, "X-YOUI-Page": page },
    body: { label, agent: "alpha", ...body },
  });
  assert.equal(created.status, 201, created.text);
  const read = {
    Cookie: browser.header,
    "X-YOUI-Session": created.json.session.id,
    "X-YOUI-Session-Token": created.json.sessionToken,
    "X-YOUI-Page": page,
  };
  return { origin, port, read, unsafe: { ...read, Origin: origin, "X-YOUI-CSRF": browser.csrf } };
}

async function chat(session, message) {
  const response = await fetch(`${session.origin}/api/chat`, {
    method: "POST",
    headers: { ...session.unsafe, "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  assert.equal(response.status, 200);
  return (await response.text()).split("\n\n").filter(Boolean).map(chunk => ({
    event: chunk.match(/^event: (.+)$/m)?.[1],
    data: JSON.parse(chunk.match(/^data: (.+)$/m)?.[1] || "null"),
  }));
}

const post = (session, path, body) => request(session.port, { path, method: "POST", headers: session.unsafe, body });
const youspeak = async session => (await request(session.port, { path: "/api/youspeak", headers: session.read })).json;

// Every figure in a report section has a label, and a label only where there is a figure.
function assertLabelled(section, name) {
  assert.ok(section && typeof section.evidence === "object", `${name} carries evidence`);
  for (const [key, value] of Object.entries(section)) {
    if (["evidence", "byName", "gradeDistribution", "last", "lastChange", "lastMiss", "budgetNow", "rateLimit"].includes(key)) continue;
    if (typeof value === "number" || typeof value === "string") {
      assert.ok(LABELS.includes(section.evidence[key]), `${name}.${key} (${value}) is labelled, not ${section.evidence[key]}`);
    } else if (value === null) {
      assert.equal(section.evidence[key] ?? null, null, `${name}.${key} has no figure, so no label`);
    }
  }
  for (const label of Object.values(section.evidence)) assert.ok(label === null || LABELS.includes(label), `${name}: ${label}`);
}

// ── The recorded live stream ────────────────────────────────────────

test("the recorded Claude Code 2.1.314 stream gives the sensors the real fields, labelled reported", async t => {
  const outcome = await replay(t, FIXTURE);
  assert.equal(outcome.ok, true, outcome.error);
  assert.equal(outcome.text, "ready");
  // The request's own usage, from message_start and message_delta.
  assert.deepEqual(outcome.requests, [{
    id: "msg_fixture000000000000001",
    usage: { input: 2, cacheRead: 0, cacheCreation: 604, output: 4 },
    cacheMiss: null,
    cacheDiagnostics: true,
  }]);
  // The result's figures under 2.1.314's names.
  const result = outcome.result;
  assert.deepEqual(Object.keys(result.usage).filter(key => /token|cache/.test(key)).sort(),
    ["cache_creation", "cache_creation_input_tokens", "cache_read_input_tokens", "input_tokens", "output_tokens", "output_tokens_details"]);
  assert.deepEqual(result.usage.cache_creation, { ephemeral_1h_input_tokens: 604, ephemeral_5m_input_tokens: 0 });
  assert.equal(result.usage.output_tokens_details.thinking_tokens, 0);
  assert.equal(result.total_cost_usd, 0.000123);
  const since = result.model_usage_since_last_result["claude-haiku-5-5"];
  for (const field of ["inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "thinkingTokens",
    "costUSD", "contextWindow", "cacheCreationEphemeral1hInputTokens", "cacheCreationEphemeral5mInputTokens"]) {
    assert.equal(typeof since[field], "number", `model_usage_since_last_result states ${field}`);
  }
  assert.deepEqual(result.modelUsage, result.model_usage_since_last_result, "a first turn: the running total is the turn");
  // rate_limit_event: the live stream's shape; its values are the fixture's synthetic set.
  assert.deepEqual(outcome.rateLimit, RATE_LIMIT_INFO);
  assert.equal(outcome.rateLimit.status, "allowed");
  assert.equal(outcome.rateLimit.rateLimitType, "five_hour");
  assert.equal(outcome.rateLimit.unifiedWindows.five_hour.utilization, 0.2);
  assert.equal(outcome.rateLimit.unifiedWindows.seven_day.utilization, 0.3);

  // The observation the server gives the kernel: C2's per-turn spend, the latest request's context.
  const spend = claudeCodeSpend(result, null);
  const observed = claudeCodeObservation(outcome, spend, "claude-haiku-5-5");
  assert.deepEqual({ ...observed, requests: undefined }, {
    model: "claude-haiku-5-5",
    input: 2, cacheRead: 0, cacheCreation: 604, output: 4, thinking: 0,
    tokensEvidence: "reported",
    costUsd: 0.000123, costEvidence: "reported",
    contextTokens: 606, contextEvidence: "reported",
    contextWindow: 1_000_000, contextWindowEvidence: "reported",
    cacheMiss: null,
    cacheDiagnostics: true,
    requests: undefined,
  });

  const ys = createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  ys.noteRoute("claude-code", "claude-haiku-5-5");
  ys.senseUsage(observed);
  ys.senseRateLimitState(outcome.rateLimit);
  const report = ys.report();
  assert.equal(report.usage.inputTokens, 2);
  assert.equal(report.usage.cacheReadTokens, 0);
  assert.equal(report.usage.cacheCreationTokens, 604);
  assert.equal(report.usage.outputTokens, 4);
  assert.equal(report.usage.thinkingTokens, 0, "thinking as reported: 0 here, stated by the stream");
  assert.equal(report.usage.costUsd, 0.000123);
  for (const key of ["inputTokens", "cacheReadTokens", "cacheCreationTokens", "outputTokens", "thinkingTokens", "costUsd"]) {
    assert.equal(report.usage.evidence[key], "reported", key);
  }
  assert.equal(report.context.tokens, 606, "the latest request's input + cache read + cache creation");
  assert.equal(report.context.evidence.tokens, "reported");
  assert.equal(report.context.window, 1_000_000);
  assert.equal(report.context.evidence.window, "reported", "the model's own window, as Claude Code states it");
  assert.equal(report.context.estimatedTokens, null, "no chars/4 estimate on this route");
  assert.equal(report.system.rateLimit.status, "allowed");
  assert.deepEqual(report.system.rateLimit.fiveHour, { utilization: 0.2, status: "allowed", resetsAt: 1800003600 });
  assert.equal(report.system.rateLimit.sevenDay.utilization, 0.3);
  assert.equal(report.system.evidence.rateLimit, "reported");
  assert.match(ys.statusLine(), /ctx:1k \$0\.0001(?!~)/, "reported figures carry no ~");

  // The resumed turn: per-result figures (2.1.311+) are the turn's own; the cache miss is reported.
  const resumed = await replay(t, FIXTURE_RESUME);
  const resumedSpend = claudeCodeSpend(resumed.result, spend.next);
  assert.equal(resumed.result.total_cost_usd, 0.000254, "the running total");
  assert.equal(resumedSpend.costUsd, 0.000131, "the turn's own, from model_usage_since_last_result");
  const second = claudeCodeObservation(resumed, resumedSpend, "claude-haiku-5-5");
  assert.deepEqual(second.cacheMiss, { type: "messages_changed", missedTokens: 554 });
  assert.equal(second.cacheCreation, 644);
  ys.senseUsage(second);
  const after = ys.report();
  assert.equal(after.usage.cacheCreationTokens, 604 + 644);
  assert.equal(after.usage.costUsd, 0.000254);
  assert.equal(after.cache.misses, 1);
  assert.deepEqual(after.cache.lastMiss, { type: "messages_changed", missedTokens: 554 });
  assert.equal(after.cache.evidence.lastMiss, "reported");
  assert.equal(after.cache.evidence.misses, "reported");
  assert.equal(after.cache.promptChanges, 0, "a reported miss is not a system-prompt change YOUI made");
  assert.ok(!ys.decide("high", "claude-haiku-5-5", null).some(signal => signal.type === "cache"),
    "a miss alone raises no suggestion: nothing YOUI sent changed");
});

test("a Claude Code build that states no thinking or window gives null, never 0", () => {
  // The shape of a build before 2.1.311: no per-result figures, no thinkingTokens or contextWindow.
  const result = {
    subtype: "success",
    total_cost_usd: 0.002,
    usage: { input_tokens: 3, cache_read_input_tokens: 50, cache_creation_input_tokens: 0, output_tokens: 9 },
    modelUsage: { "claude-opus-5-5": { inputTokens: 3, cacheReadInputTokens: 50, outputTokens: 9, costUSD: 0.002 } },
  };
  assert.deepEqual(usageFromClaudeCode(result.usage), {
    input_tokens: 53, output_tokens: 9, thinking_tokens: null,
    uncached_input_tokens: 3, cache_read_input_tokens: 50, cache_creation_input_tokens: 0,
  });
  const observed = claudeCodeObservation({ result, requests: [] }, claudeCodeSpend(result, null), "claude-opus-5-5");
  assert.equal(observed.thinking, null);
  assert.equal(observed.contextWindow, null);
  assert.equal(observed.contextTokens, null, "no request events, no context figure");
  assert.equal(observed.input, 3);
  // With no spend at all (no result), the result's usage is the only source, and still says no thinking.
  assert.equal(claudeCodeObservation({ result, requests: [] }, null, "claude-opus-5-5").thinking, null);
  const ys = createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.senseUsage(observed);
  assert.equal(ys.report().thinking.totalTokens, null);
  assert.equal(ys.report().context.evidence.window, "estimated", "then YOUI's own table");
});

test("the fixtures hold nothing identifying", () => {
  for (const file of [FIXTURE, FIXTURE_RESUME]) {
    const text = readFileSync(file, "utf-8");
    assert.doesNotMatch(text, /\/Users\/|\/home\/|\/private\/|@(?!builtin)|account|org_id|organization/i, file);
    // This machine's own names, read at run time so the source spells none.
    for (const own of [userInfo().username, tmpdir()]) {
      if (own) assert.ok(!text.includes(own), `${file} holds a local name`);
    }
    const events = text.split("\n").filter(Boolean).map(line => JSON.parse(line));
    const init = events.find(event => event.subtype === "init");
    assert.equal(init.cwd, "<scrubbed: a scratch directory>");
    assert.equal(init.apiKeySource, "none", "the subscription login, not a key");
    // The rate-limit state is the synthetic set, in both fixtures, and nothing else.
    const limits = events.filter(event => event.type === "rate_limit_event");
    assert.equal(limits.length, 1, file);
    assert.deepEqual(limits[0].rate_limit_info, RATE_LIMIT_INFO, `${file}: the synthetic rate-limit values`);
    // Session ids and event uuids are placeholders.
    for (const event of events) {
      if (event.session_id !== undefined) assert.equal(event.session_id, "00000000-0000-4000-8000-000000000001", file);
      if (event.uuid !== undefined) assert.match(event.uuid, /^00000000-0000-4000-8000-0000000[0-9]{5}$/, file);
    }
  }
});

// ── Labels, and no figure where nothing was reported ────────────────

test("every figure the kernel reports is labelled, and what nobody reported is null, not 0", t => {
  const dir = scratch(t, "ys2-labels-");
  const historyPath = join(dir, "history.jsonl");
  const ys = createKernel({ agent: "alpha", historyPath, legacyPath: null });

  // Before anything is reported: no thinking, no context, no rate-limit state.
  ys.noteRoute("vllm", "Qwen/Qwen2.5-72B-Instruct-AWQ");
  ys.senseThinking({ output_tokens: 100 });
  ys.senseTurn(null);
  let report = ys.report();
  assert.equal(report.thinking.totalTokens, null, "a usage block without thinking records none");
  assert.equal(report.thinking.avgRatio, null, "the old thinking/output ratio has no source here");
  assert.equal(report.system.rateLimit, null);
  assert.equal(report.usage.costUsd, null);
  assert.match(ys.statusLine(), /think:— /);

  // A vLLM turn: tokens reported (a prompt of 900 that vLLM said was 600 cached, as the server
  // splits it), cost by YOUI's price table, context chars/4, window unknown.
  ys.senseUsage({ model: "Qwen/Qwen2.5-72B-Instruct-AWQ", input: 300, cacheRead: 600, output: 40, costUsd: 0, costEvidence: EVIDENCE.estimated });
  ys.senseOutput("The cache key at server.mjs:42 leaves out the session id, so two tabs share one entry; adding it fixes it.");
  ys.senseToolCall("read_file", { path: "a.txt" }, null);
  ys.senseContext([{ role: "user", content: "x".repeat(4_000_000) }], 400);
  report = ys.report();
  assert.equal(report.context.window, null, "no 1M assumed for a model YOUI does not know");
  assert.equal(report.context.windowUtilization, null);
  assert.ok(!ys.decide("high", "Qwen/Qwen2.5-72B-Instruct-AWQ", null, { route: "vllm" }).some(signal => signal.type === "context"),
    "no context signal against an unknown window, however large the estimate");
  assert.equal(report.usage.evidence.inputTokens, "reported");
  assert.equal(report.usage.evidence.costUsd, "estimated");
  // Z9: vLLM reports no cache diagnostics, so no miss count, not a reported 0.
  assert.equal(report.cache.misses, null, "nobody reported cache misses on this route");
  assert.equal(report.cache.missedTokens, null);
  assert.equal(report.cache.evidence.misses, null, "and no label");

  // A Claude Code turn adds reported cost: the session's cost is still an estimate, as its weakest part.
  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.senseUsage({ model: "claude-opus-5-5", input: 10, cacheRead: 1000, cacheCreation: 100, output: 20, thinking: 7,
    costUsd: 0.0123, contextTokens: 850_000, cacheDiagnostics: true });
  ys.senseMind({ ok: true, input: 10, cacheRead: 1000, cacheCreation: 100, output: 20, costUsd: 0.0123 });
  ys.senseMind({ ok: false, input: 5, output: 1, costUsd: 0.00001, costEvidence: EVIDENCE.estimated });
  ys.senseRateLimitState({ status: "allowed", unifiedWindows: { five_hour: { utilization: 0.3 } } });
  report = ys.report();
  assert.equal(report.usage.evidence.costUsd, "estimated", "reported + estimated is an estimate");
  assert.equal(report.thinking.totalTokens, 7);
  assert.equal(report.thinking.evidence.totalTokens, "reported");
  assert.equal(report.cache.misses, 0, "Claude Code states its diagnostics: no miss is a reported 0");
  assert.equal(report.cache.evidence.misses, "reported");
  assert.equal(report.context.window, KNOWN_CONTEXT_WINDOWS["claude-opus-5-5"]);
  assert.equal(report.context.evidence.window, "estimated", "YOUI's own table when Claude Code did not state it");
  const prune = ys.decide("high", "claude-opus-5-5", null, { route: "claude-code" }).find(signal => signal.action === "prune_recommended");
  assert.ok(prune, "850k reported of a 1M window");
  assert.equal(prune.evidence, "estimated", "a reported context against YOUI's window is an estimate");
  assert.match(prune.reason, /850k of a 1000k window \(estimated\)/);

  for (const name of ["usage", "output", "thinking", "action", "context", "cache", "minds", "system"]) assertLabelled(report[name], name);
  assert.equal(weakerEvidence(EVIDENCE.reported, null), EVIDENCE.reported);
  assert.equal(weakerEvidence(EVIDENCE.reported, EVIDENCE.estimated), EVIDENCE.estimated);

  // The row: every figure it holds is labelled; minds apart from the agent's own.
  ys.noteExchange({ ok: true });
  assert.equal(ys.endSession({ reason: "test" }).written, true);
  const [row] = readHistory(historyPath).rows;
  assert.equal(row.mindCalls, 2);
  assert.equal(row.mindFailedCalls, 1);
  assert.equal(row.mindInputTokens, 15);
  assert.equal(row.mindCacheReadTokens, 1000);
  assert.equal(row.mindOutputTokens, 21);
  assert.equal(row.evidence.mindCostUsd, "estimated");
  assert.equal(row.inputTokens, 310, "uncached input of the agent's own turns only: vLLM's 300 and Claude's 10");
  assert.equal(row.cacheReadTokens, 1600);
  assert.equal(row.cacheMisses, 0);
  assert.equal(row.thinkingTokens, 7);
  assert.equal(row.fiveHourUtilization, 0.3);
  for (const [key, value] of Object.entries(row)) {
    if (typeof value !== "number") continue;
    if (["schema", "signalCount"].includes(key)) continue;
    assert.ok(LABELS.includes(row.evidence[key]), `the row labels ${key}`);
  }
  assert.doesNotMatch(JSON.stringify(row), /server\.mjs|a\.txt|cache key/, "still no text, paths or tool inputs");
});

test("the terminal and sovereign: a budget window no header reported, and cache misses, are null, not 0", () => {
  // youi.mjs and sovereign.mjs start each window like this until a rate-limit header arrives;
  // on sovereign's Ollama route, or with a key whose responses carry no unified headers, none does.
  const unreported = () => ({
    fiveHour: { utilization: 0, reset: 0, status: "unknown" },
    sevenDay: { utilization: 0, reset: 0, status: "unknown" },
    isUsingOverage: false,
  });
  for (const route of ["terminal-legacy", "sovereign"]) {
    const ys = createKernel({ agent: "alpha", route, historyPath: null, legacyPath: null });
    ys.noteRoute(route, "claude-opus-5-5");
    ys.senseOutput("The cache key at server.mjs:42 leaves out the session id, so two tabs share one entry; adding it fixes it.");
    ys.senseTurn(unreported());
    ys.senseTurn(unreported());
    let report = ys.report();
    assert.equal(report.system.budgetNow, null, `${route}: no window was reported`);
    assert.equal(report.system.budgetBurned, null, `${route}: so nothing burned is claimed`);
    assert.equal(report.system.evidence.budgetNow, null);
    assert.equal(report.cache.misses, null, `${route}: no cache diagnostics on this route`);
    assert.equal(ys.row().budgetBurned, null);
    assert.equal(ys.row().cacheMisses, null);
    assert.equal(ys.row().evidence.cacheMisses, undefined);
    assert.ok(!ys.decide("high", "claude-opus-5-5", unreported()).some(signal => signal.type === "effort"));
    // Once a header has stated the window, it is a reported figure.
    const stated = unreported();
    stated.fiveHour = { utilization: 0.4, reset: 0, status: "allowed" };
    ys.senseTurn(stated);
    stated.fiveHour = { utilization: 0.5, reset: 0, status: "allowed" };
    ys.senseTurn(stated);
    report = ys.report();
    assert.equal(report.system.budgetNow.fiveHour, 0.5);
    assert.equal(report.system.budgetNow.sevenDay, null, "the seven-day window was still not reported");
    assert.equal(report.system.budgetBurned, "10%");
  }
});

// ── L1: a labelled estimate, no longer graded (design §4) ───────────

test("L1 is a labelled estimate with no grade: nothing computes, stores or shows a letter, and filler_regression reads the ratio", t => {
  const historyPath = join(scratch(t, "ys2-nograde-"), "history.jsonl");
  const ys = createKernel({ agent: "alpha", historyPath, legacyPath: null });
  ys.noteRoute("claude-code", "claude-opus-5-5");
  const padded = "Sure! Let me check that. Here is what I found in the file, which is long enough to measure honestly.";
  const block = ys.senseOutput(padded);
  assert.equal("grade" in block, false, "a block has no grade");
  assert.equal(block.graded, true);
  assert.ok(block.usefulRatio < FILLER_RATIO_FLOOR);
  assert.equal(block.evidence, "estimated");
  const report = ys.report();
  assert.equal("grade" in report.output, false);
  assert.equal("gradeDistribution" in report.output, false);
  assert.equal(report.output.evidence.usefulRatio, "estimated");
  assert.match(ys.statusLine(), /^YS:\d+%~ /, "the ratio, marked as an estimate, and no letter");
  assert.doesNotMatch(ys.statusLine(), /YS:[SABCD]\b/);

  // filler_regression: 3 of the last 5 measured blocks under the floor (85%), by their ratio.
  const clean = "The cache key at server.mjs:42 leaves out the session id, so two tabs share one entry; adding it fixes it.";
  // One filler phrase: about 76% useful (under the floor), and about 87% (over it).
  const mild = "Let me check the cache layer. The key at server.mjs:42 leaves out the session id, so two tabs share one entry and read each other.";
  const near = "Let me check the cache layer. The key at server.mjs:42 leaves out the session id, so two tabs share one entry and read each other;"
    + " adding the id to the key, and a test that opens two tabs at once and checks each sees its own entry, fixes it.";
  const ratio = text => createKernel({ historyPath: null, legacyPath: null }).senseOutput(text).usefulRatio;
  assert.ok(ratio(mild) > 0.7 && ratio(mild) < FILLER_RATIO_FLOOR, `mild: ${ratio(mild)}`);
  assert.ok(ratio(near) > FILLER_RATIO_FLOOR && ratio(near) < 0.95, `near: ${ratio(near)}`);
  for (const text of [mild, clean, mild, clean]) ys.senseOutput(text);
  assert.ok(ys.decide("high", "claude-opus-5-5", null).some(signal => signal.action === "filler_regression"), "3 of 5 under 85%");
  const steady = createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  for (const text of [near, near, near, mild, clean]) steady.senseOutput(text);
  assert.ok(!steady.decide("high", "claude-opus-5-5", null).some(signal => signal.action === "filler_regression"),
    "blocks just over the floor do not count; 1 of 5 under it is not a regression");
  const signal = ys.decide("high", "claude-opus-5-5", null).find(entry => entry.action === "filler_regression");
  assert.match(signal.reason, /3\/5 recent measured text blocks under 85% useful \(English-only estimate\)/);
  assert.doesNotMatch(signal.reason, /grade/i);

  // Rows are schema 2, with no grade; a schema 1 row (Y1, with its grade) is still read.
  ys.noteExchange({ ok: true });
  assert.equal(ys.endSession({ reason: "test" }).written, true);
  writeFileSync(historyPath, `${readFileSync(historyPath, "utf-8")}${JSON.stringify({
    schema: 1, session: "a-y1-row", outputGrade: "B", usefulRatio: 0.9, endedAt: "2026-10-30T09:00:00.000Z",
  })}\n`);
  const store = readHistory(historyPath);
  assert.deepEqual(store.warnings, []);
  assert.equal(store.rows.length, 2);
  const [row, older] = store.rows;
  assert.equal(row.schema, 2);
  assert.equal(HISTORY_SCHEMA, 2);
  assert.equal("outputGrade" in row, false);
  assert.equal("outputGrade" in row.evidence, false);
  assert.equal(row.evidence.usefulRatio, "estimated");
  assert.equal(older.schema, 1, "a Y1 row is still read");

  // No surface shows a letter: the page header, the terminal's exit line and daily note, sovereign's report.
  const source = file => readFileSync(join(REPO_ROOT, file), "utf-8");
  assert.doesNotMatch(source("youi-web/public/index.html"), /ys-grade|tokensSaved|overallGrade|metrics\.grade\b|\? 'S' :/);
  assert.doesNotMatch(source("youi.mjs"), /output\.grade\b|gradeDistribution|Grade:/);
  assert.doesNotMatch(source("sovereign.mjs"), /output\.grade\b|Grade:/);
  assert.doesNotMatch(source("youi-web/server.mjs"), /output\.grade\b|gradeDistribution|overallGrade|tokensSaved/);
  assert.doesNotMatch(source("youspeak-kernel.mjs"), /gradeRatio|outputGrade: r\./);
});

// ── Sovereign's Ollama route states no thinking ─────────────────────

test("sovereign's Ollama usage block states no thinking, so its thinking is null, not a reported 0", () => {
  // The converter sovereign.mjs uses for Ollama's /v1 answers (sovereign.mjs runs on import, so it is read, not imported).
  const source = readFileSync(join(REPO_ROOT, "sovereign.mjs"), "utf-8");
  const at = source.indexOf("function openAIResponseToAnthropic(");
  assert.ok(at >= 0);
  const body = source.slice(at, source.indexOf("\n}\n", at) + 2);
  const convert = new Function("crypto", `${body}\nreturn openAIResponseToAnthropic;`)(globalThis.crypto);
  const response = convert({ choices: [{ message: { content: "ok" }, finish_reason: "stop" }], usage: { prompt_tokens: 12, completion_tokens: 3 } }, "glm-5.1");
  assert.equal(response.usage.thinking_tokens, undefined, "no made-up thinking count");
  const ys = createKernel({ agent: "sovereign", route: "sovereign", historyPath: null, legacyPath: null });
  ys.noteRoute("sovereign", "glm-5.1");
  ys.senseThinking(response.usage);
  const report = ys.report();
  assert.equal(report.thinking.totalTokens, null);
  assert.equal(report.thinking.avgRatio, null);
  assert.equal(report.thinking.evidence.totalTokens, null);
  assert.equal(ys.row().thinkingTokens, null);
});

// ── A route or model switch ─────────────────────────────────────────

test("a switch to a route or model that reports no context drops the stale figure; the row keeps the peak with its own window", () => {
  const ys = createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.senseUsage({ model: "claude-opus-5-5", input: 5, cacheRead: 899_000, cacheCreation: 995, output: 10,
    contextTokens: 900_000, contextWindow: 1_000_000, cacheDiagnostics: true });
  assert.equal(ys.report().context.tokens, 900_000);
  // An Ollama turn with no tool round: no context figure of its own.
  ys.noteRoute("ollama", "glm-5.1");
  ys.senseUsage({ model: "glm-5.1", input: 40, output: 9, costUsd: 0, costEvidence: EVIDENCE.estimated });
  const report = ys.report();
  assert.equal(report.context.tokens, null, "Opus's 900k is not glm's current context");
  assert.equal(report.context.evidence.tokens, null);
  assert.equal(report.context.window, null, "and glm's window is unknown");
  assert.equal(report.context.peakTokens, 900_000, "the peak is kept");
  assert.ok(!ys.decide("high", "glm-5.1", null, { route: "ollama" }).some(signal => signal.type === "context"));
  const row = ys.row();
  assert.equal(row.contextPeakTokens, 900_000);
  assert.equal(row.contextWindow, 1_000_000, "the window the peak was against, not the latest model's");
  assert.equal(row.evidence.contextWindow, "reported");
  // The same route and model again keeps the figure it has.
  const same = createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  same.noteRoute("claude-code", "claude-opus-5-5");
  same.senseUsage({ model: "claude-opus-5-5", input: 5, contextTokens: 1000 });
  same.noteRoute("claude-code", "claude-opus-5-5");
  assert.equal(same.report().context.tokens, 1000);
});

// ── The cache-stability signal ──────────────────────────────────────

test("the cache-stability signal fires when the system prompt changes within a conversation, and names what changed", () => {
  const ys = createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  const parts = (environment = "e1", tools = "t1") => [
    { name: "the enabled tools list", hash: tools },
    { name: "the environment block", hash: environment },
  ];
  ys.noteRoute("claude-code", "claude-opus-5-5");
  assert.equal(ys.senseSystemPrompt({ conversation: "c1", hash: "h1", parts: parts() }).changed, false, "the first turn");
  ys.noteRoute("claude-code", "claude-opus-5-5");
  assert.equal(ys.senseSystemPrompt({ conversation: "c1", hash: "h1", parts: parts() }).changed, false, "the same prompt");
  assert.ok(!ys.decide("high", "claude-opus-5-5", null).some(signal => signal.type === "cache"));

  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.senseUsage({ model: "claude-opus-5-5", input: 5, cacheMiss: { type: "system_changed", missedTokens: 11_000 } });
  const changed = ys.senseSystemPrompt({ conversation: "c1", hash: "h2", parts: parts("e2") });
  assert.deepEqual(changed, { changed: true, parts: ["the environment block"] });
  const signal = ys.decide("high", "claude-opus-5-5", null).find(entry => entry.type === "cache");
  assert.equal(signal.action, "prompt_changed");
  assert.match(signal.reason, /The system prompt changed between turns of this conversation \(the environment block\), so the cache was rewritten/);
  assert.match(signal.reason, /Claude reported a cache miss \(system_changed, 11000 tokens\)/);
  // Z17: the change is YOUI's own comparison of hashes, so the signal is an estimate even with a reported miss.
  assert.equal(signal.evidence, "estimated");
  assert.match(signal.suggestion, /^Nothing was changed\./);
  assert.deepEqual(ys.newSuggestions([signal]).map(entry => entry.action), ["prompt_changed"]);

  // The next message starts clean; a new conversation is not compared with the old one.
  ys.noteRoute("claude-code", "claude-opus-5-5");
  assert.ok(!ys.decide("high", "claude-opus-5-5", null).some(entry => entry.type === "cache"));
  assert.equal(ys.senseSystemPrompt({ conversation: "c2", hash: "h3", parts: parts("e3", "t3") }).changed, false);
  assert.equal(ys.report().cache.promptChanges, 1);
});

test("effort, date, mode and model live in the per-turn context block, so the system prompt file stays the same; a real change raises the signal", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const dir = scratch(t, "ys2-cache-");
  const shift = join(dir, "date-shift");
  writeFileSync(shift, "0");
  const elsewhere = join(dir, "elsewhere");
  mkdirSync(elsewhere);
  const { port } = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin,
    YOUI_CLAUDE_CWD: fake.cwd,
    YOUSPEAK_TEST_HISTORY_PATH: join(dir, "history.jsonl"),
    YOUI_TEST_DATE_SHIFT_FILE: shift,
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,orchestrator:run,youspeak:read,workspace:select",
  }, ["--import", SHIFT_DATE]);
  const session = await openSession(port, "cache");

  const first = await chat(session, "first");
  assert.equal((await post(session, "/api/settings", { effort: "low", chatMode: "orchestrate" })).status, 200);
  writeFileSync(shift, String(24 * 60 * 60 * 1000));
  const second = await chat(session, "second");
  // Back to direct, and another model, in the same Claude Code conversation.
  assert.equal((await post(session, "/api/settings", { chatMode: "direct", model: "claude-sonnet-4-6" })).status, 200);
  const third = await chat(session, "FAKE:two-requests");

  const runs = fake.runs();
  const messages = fake.messages();
  assert.equal(runs.length, 3);
  assert.equal(runs[1].systemPrompt.content, runs[0].systemPrompt.content, "the same system prompt file, byte for byte");
  assert.equal(runs[2].systemPrompt.content, runs[0].systemPrompt.content, "a mode or model switch leaves it the same too");
  assert.ok(runs[2].argv.includes("--resume"), "the model switch kept the conversation");
  assert.doesNotMatch(runs[0].systemPrompt.content, /Effort:|- Date:|Git branch:|Model:|- Mode:|# Minds/);
  assert.match(runs[0].systemPrompt.content, /# YOUSPEAK — the Discipline/);
  // Z20: how to call minds is fixed text, so it is in the stable system prompt, said for either mode.
  assert.match(runs[0].systemPrompt.content, /In orchestrate mode, each turn's context block lists the minds[^\n]*callable through your tools: review/);
  assert.match(runs[0].systemPrompt.content, /In direct mode, work alone, and call other minds only when the person asks\./);
  const [one, two, three] = messages.map(entry => entry.context);
  assert.notEqual(one, two, "different context blocks");
  assert.match(one, /- Thinking: adaptive \| Effort: high/);
  assert.match(two, /- Thinking: adaptive \| Effort: low/);
  const date = block => block.match(/- Date: (\d{4}-\d{2}-\d{2})/)[1];
  assert.equal(Date.parse(date(two)) - Date.parse(date(one)), 24 * 60 * 60 * 1000, "the date moved a day");
  // Z2: every block states its mode, so a direct turn takes back an earlier orchestrate hint.
  assert.match(one, /^- Mode: direct \(work alone; call other minds only when the person asks\)$/m);
  assert.match(two, /^- Mode: orchestrate /m);
  assert.match(two, /# Minds \(orchestrate mode\)/, "the minds offered are per turn");
  assert.doesNotMatch(two, /callable through your tools|minds per call/, "the fixed guide is not repeated in each block");
  assert.match(three, /^- Mode: direct \(work alone; call other minds only when the person asks\)$/m);
  assert.doesNotMatch(three, /# Minds/);
  // Z21: the model is per turn, and its switch shows only there.
  assert.match(one, /^- Model: claude-opus-5-5$/m);
  assert.match(three, /^- Model: claude-sonnet-4-6$/m);
  assert.equal(messages[0].message, "first");
  assert.equal(messages[1].message, "second");
  for (const events of [first, second, third]) {
    assert.ok(!events.some(event => event.event === "youspeak_suggestion" && event.data.type === "cache"), "no cache signal");
  }
  let report = await youspeak(session);
  assert.equal(report.cache.promptChanges, 0);
  assert.equal(report.cache.misses, 0, "Claude Code states its cache diagnostics: no miss is a reported 0");
  assert.equal(report.cache.evidence.misses, "reported");
  // Z22: the context is the latest request's (6 + 2000 + 50), not the first one's (4 + 5000).
  assert.equal(report.context.tokens, 6 + 2000 + 50, "the latest request: input + cache read + cache creation");
  assert.equal(report.context.evidence.tokens, "reported");
  // Z22: the server senses the rate-limit state Claude Code reported (the fake's 25% and 50%).
  assert.equal(report.system.rateLimit?.status, "allowed");
  assert.equal(report.system.rateLimit.fiveHour.utilization, 0.25);
  assert.equal(report.system.rateLimit.sevenDay.utilization, 0.5);
  assert.equal(report.system.evidence.rateLimit, "reported");

  // A setting that is part of the system prompt does change it, and the signal says what.
  assert.equal((await post(session, "/api/settings", { workdir: elsewhere })).status, 200);
  const fourth = await chat(session, "FAKE:cache-miss");
  assert.notEqual(fake.runs()[3].systemPrompt.content, runs[0].systemPrompt.content);
  const suggestion = fourth.find(event => event.event === "youspeak_suggestion" && event.data.type === "cache")?.data;
  assert.ok(suggestion, "the cache signal is suggested");
  assert.match(suggestion.reason, /changed between turns of this conversation \(the environment block[^)]*\)/);
  assert.match(suggestion.reason, /Claude reported a cache miss \(system_changed, 1100 tokens\)/);
  assert.equal(suggestion.evidence, "estimated", "YOUI's own comparison, even with a reported miss");
  report = await youspeak(session);
  assert.equal(report.cache.promptChanges, 1);
  assert.equal(report.cache.misses, 1);
  assert.equal(report.usage.evidence.inputTokens, "reported");
  assert.equal(report.context.tokens, 3 + 1000 + 100, "the fake's latest request: input + cache read + cache creation");
  assert.equal(report.context.window, 1_000_000);
  assert.equal(report.context.evidence.window, "reported");
  assert.equal(report.usage.costUsd, 0.0492, "four turns' deltas, not the running total added up");
  for (const name of ["usage", "output", "thinking", "action", "context", "cache", "minds", "system"]) assertLabelled(report[name], name);
  assert.ok(report.evidenceKey.reported && report.evidenceKey.estimated, "the page can say what the labels mean");
  // Z23 and Z13: no grade and no "tokens saved"; the older top-level fields are labelled too.
  for (const gone of ["tokensSaved", "grades", "overallGrade"]) assert.equal(gone in report, false, `no ${gone}`);
  assert.equal("grade" in report.output, false);
  assert.equal("gradeDistribution" in report.output, false);
  for (const key of ["usefulRatio", "totalOutput", "totalFiller", "gradedTokens", "gradedBlocks", "textBlocks", "toolCalls", "actionDensity"]) {
    assert.ok(key in report, key);
    assert.equal(report.evidence[key], report[key] === null ? null : "estimated", `the top-level ${key} is labelled`);
  }
});

// ── Minds feed the observer ─────────────────────────────────────────

test("Claude minds report their usage to the observer, apart from the agent's turns, and the row carries them", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const historyPath = join(scratch(t, "ys2-minds-"), "history.jsonl");
  const { port } = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin,
    YOUI_CLAUDE_CWD: fake.cwd,
    YOUSPEAK_TEST_HISTORY_PATH: historyPath,
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,orchestrator:run,youspeak:read",
  });
  const session = await openSession(port, "minds");
  const input = { question: "Which is heavier?", minds: ["anthropic:claude-sonnet-4-6", "anthropic:claude-haiku-4-5-20251001"] };
  const events = await chat(session, `FAKE:call ensemble ${JSON.stringify(input)}`);
  assert.equal(events.find(event => event.event === "minds_used").data.minds.length, 2);

  const report = await youspeak(session);
  // Each mind run: 10 uncached, 1000 cache read, 100 cache creation, 20 out, $0.0123 (the fake's result).
  assert.equal(report.minds.calls, 2);
  assert.equal(report.minds.failedCalls, 0);
  assert.equal(report.minds.inputTokens, 20);
  assert.equal(report.minds.cacheReadTokens, 2000);
  assert.equal(report.minds.cacheCreationTokens, 200);
  assert.equal(report.minds.outputTokens, 40);
  assert.equal(report.minds.costUsd, 0.0246);
  assert.equal(report.minds.evidence.costUsd, "reported");
  // The agent's own turn is counted apart.
  assert.equal(report.usage.turns, 1);
  assert.equal(report.usage.inputTokens, 10);
  assert.equal(report.usage.costUsd, 0.0123);
  // The bridge's tool call is sensed (L3) as before.
  assert.equal(report.action.totalCalls, 1);
  assert.deepEqual(report.action.byName, { ensemble: 1 });

  assert.equal((await post(session, "/api/clear")).status, 200);
  const [row] = readHistory(historyPath).rows;
  assert.equal(row.mindCalls, 2);
  assert.equal(row.mindInputTokens, 20);
  assert.equal(row.mindOutputTokens, 40);
  assert.equal(row.mindCostUsd, 0.0246);
  assert.equal(row.evidence.mindCostUsd, "reported");
  assert.equal(row.costUsd, 0.0123);
  assert.equal(row.toolCalls, 1);
});

// ── Ollama and vLLM: what they did not report stays null; their minds are counted ──

const VLLM_MODEL = "Qwen/Qwen2.5-72B-Instruct-AWQ";

// A local HTTP fake; `handler(req, res, body)` answers. Open responses are cut when the test ends.
async function listen(t, handler) {
  const requests = [];
  const open = new Set();
  const server = http.createServer((req, res) => {
    open.add(res);
    res.on("close", () => open.delete(res));
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      let body = null;
      try { body = JSON.parse(Buffer.concat(chunks).toString("utf-8")); } catch {}
      requests.push({ method: req.method, path: req.url, body });
      handler(req, res, body);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  t.after(() => {
    for (const res of open) res.destroy();
    server.closeAllConnections();
    server.close();
  });
  return { baseUrl: `http://127.0.0.1:${server.address().port}`, requests };
}

// vLLM: GET /v1/models lists VLLM_MODEL; each POST streams the next step of `script`:
// { text, usage } (usage null: no usage chunk at all), { tool: { id, name, input } },
// or { text, error } (the text, then an error chunk mid-stream, then [DONE]).
function scriptedVllm(t, script) {
  let next = 0;
  return listen(t, (req, res, body) => {
    if (req.method === "GET" && req.url === "/v1/models") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ object: "list", data: [{ id: VLLM_MODEL, object: "model" }] }));
    }
    const step = script[next++] || { text: "nothing more scripted" };
    const delta = step.tool
      ? { tool_calls: [{ index: 0, id: step.tool.id, function: { name: step.tool.name, arguments: JSON.stringify(step.tool.input) } }] }
      : { content: step.text };
    const usage = step.usage === undefined ? { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } : step.usage;
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(`data: ${JSON.stringify({ model: body?.model, choices: [{ delta, finish_reason: null }] })}\n\n`);
    if (step.error) {
      res.write(`data: ${JSON.stringify({ error: { object: "error", message: step.error, type: "InternalServerError", code: 500 } })}\n\n`);
    } else {
      res.write(`data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: step.tool ? "tool_calls" : "stop" }] })}\n\n`);
      if (usage) res.write(`data: ${JSON.stringify({ choices: [], usage })}\n\n`);
    }
    res.end("data: [DONE]\n\n");
  });
}

test("Ollama and vLLM: a count the response left out is null, vLLM's cached prefix is split out, and an error mid-stream fails the turn", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const vllm = await scriptedVllm(t, [
    // Z8: an OpenAI-compatible stream that ends without its usage chunk.
    { text: "No usage chunk followed this answer.", usage: null },
    // Z7: vLLM states its prefix-cache hits.
    { text: "A cached answer.", usage: { prompt_tokens: 900, completion_tokens: 40, total_tokens: 940, prompt_tokens_details: { cached_tokens: 600 } } },
    // The pre-existing stream bug: an error chunk mid-stream, then [DONE].
    { text: "partial", error: "the engine died" },
  ]);
  const { port } = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin,
    YOUI_CLAUDE_CWD: fake.cwd,
    OLLAMA_VLLM_BASE_URL: vllm.baseUrl,
    YOUSPEAK_TEST_HISTORY_PATH: join(scratch(t, "ys2-vllm-"), "history.jsonl"),
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,youspeak:read",
  });
  const session = await openSession(port, "vllm-usage", { model: VLLM_MODEL });

  await chat(session, "first");
  let report = await youspeak(session);
  assert.equal(report.usage.turns, 1);
  assert.equal(report.usage.inputTokens, null, "the stream stated no prompt_tokens: not a reported 0");
  assert.equal(report.usage.outputTokens, null);
  assert.equal(report.usage.evidence.inputTokens, null);
  assert.equal(report.usage.costUsd, null, "no cost estimate from no counts");
  assert.equal(report.cache.misses, null, "vLLM reports no cache diagnostics");

  await chat(session, "second");
  report = await youspeak(session);
  assert.equal(report.usage.inputTokens, 300, "the uncached part: 900 less the 600 vLLM said it cached");
  assert.equal(report.usage.cacheReadTokens, 600);
  assert.equal(report.usage.outputTokens, 40);
  assert.equal(report.usage.evidence.inputTokens, "reported");
  assert.equal(report.usage.evidence.cacheReadTokens, "reported");

  const third = await chat(session, "third");
  const error = third.find(event => event.event === "error");
  assert.ok(error, "the error the stream carried reaches the page");
  assert.match(error.data.message, /vLLM stream error: the engine died/);
  report = await youspeak(session);
  assert.equal(report.usage.turns, 2, "the failed request reported nothing to count");
  const current = await request(port, { path: "/api/sessions/current?include=messages", headers: session.read });
  assert.ok(!JSON.stringify(current.json.session.messages).includes("partial"), "the cut-off text is not kept as an answer");
});

test("Ollama minds report to the observer when they answer, fail or are stopped, in the session that called them", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const review = id => ({ tool: { id, name: "review", input: { draft: "A short draft.", criteria: "Is it clear?", reviewer: "ollama:mind-a:1b" } } });
  const vllm = await scriptedVllm(t, [review("call-1"), { text: "Revised." }, review("call-2"), { text: "Kept it." }, review("call-3")]);
  // The local Ollama mind: answers, then fails, then never answers.
  let chats = 0;
  const ollama = await listen(t, (req, res, body) => {
    if (req.method === "POST" && req.url === "/api/chat") {
      chats += 1;
      if (chats === 1) {
        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ model: body?.model, message: { role: "assistant", content: "APPROVED" },
          done: true, done_reason: "stop", prompt_eval_count: 7, eval_count: 9 }));
      }
      if (chats === 2) {
        res.writeHead(500, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: "model crashed" }));
      }
      return; // hangs until the turn is stopped
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ models: [{ name: "mind-a:1b" }] }));
  });
  const { port } = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin,
    YOUI_CLAUDE_CWD: fake.cwd,
    OLLAMA_VLLM_BASE_URL: vllm.baseUrl,
    OLLAMA_LOCAL_BASE_URL: ollama.baseUrl,
    YOUSPEAK_TEST_HISTORY_PATH: join(scratch(t, "ys2-ollama-minds-"), "history.jsonl"),
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,orchestrator:run,youspeak:read",
  });
  const session = await openSession(port, "ollama-minds", { model: VLLM_MODEL });
  const mindChats = () => ollama.requests.filter(r => r.method === "POST" && r.path === "/api/chat").length;

  // It answers: the tokens Ollama reported, the cost by YOUI's price table (0 for a local model).
  let events = await chat(session, "review my draft");
  assert.equal(events.find(event => event.event === "minds_used")?.data.minds[0].ok, true);
  let report = await youspeak(session);
  assert.equal(report.minds.calls, 1);
  assert.equal(report.minds.failedCalls, 0);
  assert.equal(report.minds.inputTokens, 7);
  assert.equal(report.minds.outputTokens, 9);
  assert.equal(report.minds.evidence.inputTokens, "reported");
  assert.equal(report.minds.costUsd, 0);
  assert.equal(report.minds.evidence.costUsd, "estimated");
  assert.equal(report.usage.inputTokens, 2, "the agent's own two vLLM requests, apart from the mind");

  // It fails (an HTTP error): a failed call, no tokens.
  events = await chat(session, "review it again");
  assert.equal(events.find(event => event.event === "minds_used")?.data.minds[0].ok, false);
  report = await youspeak(session);
  assert.equal(report.minds.calls, 2);
  assert.equal(report.minds.failedCalls, 1);
  assert.equal(report.minds.inputTokens, 7, "the failed call added no tokens");

  // It is stopped: the person stops the turn while the mind has not answered.
  const controller = new AbortController();
  const pending = fetch(`${session.origin}/api/chat`, {
    method: "POST",
    headers: { ...session.unsafe, "Content-Type": "application/json" },
    body: JSON.stringify({ message: "and once more" }),
    signal: controller.signal,
  }).then(response => response.text()).catch(() => null);
  const deadline = Date.now() + 15_000;
  while (mindChats() < 3) {
    assert.ok(Date.now() < deadline, "the third mind call reached Ollama");
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  controller.abort();
  await pending;
  while ((await youspeak(session)).minds.calls < 3) {
    assert.ok(Date.now() < deadline, "the stopped call is counted");
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  report = await youspeak(session);
  assert.equal(report.minds.calls, 3, "the stopped call is counted");
  assert.equal(report.minds.failedCalls, 2, "as a failed one");
  assert.equal(report.minds.inputTokens, 7);
});

// ── One rule module ─────────────────────────────────────────────────

test("youspeak-rule.mjs is the only source of the rule: YOUI Web, the terminal, sovereign and the harnesses import it", () => {
  // The rule's own phrases, which no hand copy may carry any more.
  const phrases = /No tool narration|Never compress|Zero-pad|Compress scaffolding|Dense status \(key:value|File cache: you've/;
  assert.match(YOUSPEAK_RULE, /^# YOUSPEAK — the Discipline\n/);
  // Z25: sovereign's "in most cases" is back in the sacred list.
  assert.match(YOUSPEAK_RULE, /Never compress doubt: "probably", "unless", "might", "I think", "in most cases" are sacred\. When uncertain, expand\./);
  assert.match(YOUSPEAK_RULE, /Expand when it matters: teaching, uncertainty, creative work, emotional context/);
  assert.equal(youspeakRule(), YOUSPEAK_RULE);
  assert.equal(youspeakRule({ fileCache: true }), `${YOUSPEAK_RULE}\n${YOUSPEAK_FILE_CACHE_LINE}`, "sovereign's line, kept for sovereign");
  for (const file of ["youi-web/server.mjs", "youi.mjs", "sovereign.mjs", "harness.mjs", "subscription-harness.mjs", "stream.mjs"]) {
    const source = readFileSync(join(REPO_ROOT, file), "utf-8");
    assert.match(source, /^import \{ youspeakRule \} from "\.\.?\/youspeak-rule\.mjs";$/m, `${file} imports the rule`);
    assert.match(source, /youspeakRule\(/, `${file} uses it`);
    assert.doesNotMatch(source, phrases, `${file} carries no copy of the rule`);
  }
  assert.match(readFileSync(join(REPO_ROOT, "sovereign.mjs"), "utf-8"), /youspeakRule\(\{ fileCache: true \}\)/);
});
