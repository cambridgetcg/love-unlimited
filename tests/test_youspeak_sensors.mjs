// YOUSPEAK slice Y2 (docs/YOUSPEAK-OBSERVER-DESIGN.md §4; docs/YOUSPEAK.md):
// sensors on the usage the provider reports, every figure labelled reported
// or estimated, the cache-stability signal, the per-turn context block, minds
// in the row, and one shared rule module.
//
// The one recorded live stream is tests/fixtures/claude-code-stream-2.1.314.jsonl
// (and its --resume, …-resume.jsonl): Claude Code 2.1.314, Claude Haiku 5.5,
// recorded on 2026-10-31 and replayed here by tests/fixtures/replay-claude.mjs.
// Everything else uses tests/fixtures/fake-claude.mjs and a local fake vLLM.
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
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import {
  EVIDENCE,
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
  // rate_limit_event, as reported.
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
    assert.doesNotMatch(text, /\/Users\/|\/home\/|\/private\/|macair|claude-501|@(?!builtin)|account|org_id|organization/i, file);
    const init = text.split("\n").filter(Boolean).map(line => JSON.parse(line)).find(event => event.subtype === "init");
    assert.equal(init.cwd, "<scrubbed: a scratch directory>");
    assert.equal(init.apiKeySource, "none", "the subscription login, not a key");
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

  // A vLLM turn: tokens reported, cost by YOUI's price table, context chars/4, window unknown.
  ys.senseUsage({ model: "Qwen/Qwen2.5-72B-Instruct-AWQ", input: 900, output: 40, costUsd: 0, costEvidence: EVIDENCE.estimated });
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

  // A Claude Code turn adds reported cost: the session's cost is still an estimate, as its weakest part.
  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.senseUsage({ model: "claude-opus-5-5", input: 10, cacheRead: 1000, cacheCreation: 100, output: 20, thinking: 7,
    costUsd: 0.0123, contextTokens: 850_000 });
  ys.senseMind({ ok: true, input: 10, cacheRead: 1000, cacheCreation: 100, output: 20, costUsd: 0.0123 });
  ys.senseMind({ ok: false, input: 5, output: 1, costUsd: 0.00001, costEvidence: EVIDENCE.estimated });
  ys.senseRateLimitState({ status: "allowed", unifiedWindows: { five_hour: { utilization: 0.3 } } });
  report = ys.report();
  assert.equal(report.usage.evidence.costUsd, "estimated", "reported + estimated is an estimate");
  assert.equal(report.thinking.totalTokens, 7);
  assert.equal(report.thinking.evidence.totalTokens, "reported");
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
  assert.equal(row.inputTokens, 910, "the agent's own turns only");
  assert.equal(row.thinkingTokens, 7);
  assert.equal(row.fiveHourUtilization, 0.3);
  for (const [key, value] of Object.entries(row)) {
    if (typeof value !== "number") continue;
    if (["schema", "signalCount"].includes(key)) continue;
    assert.ok(LABELS.includes(row.evidence[key]), `the row labels ${key}`);
  }
  assert.doesNotMatch(JSON.stringify(row), /server\.mjs|a\.txt|cache key/, "still no text, paths or tool inputs");
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
  assert.equal(signal.evidence, "reported");
  assert.match(signal.suggestion, /^Nothing was changed\./);
  assert.deepEqual(ys.newSuggestions([signal]).map(entry => entry.action), ["prompt_changed"]);

  // The next message starts clean; a new conversation is not compared with the old one.
  ys.noteRoute("claude-code", "claude-opus-5-5");
  assert.ok(!ys.decide("high", "claude-opus-5-5", null).some(entry => entry.type === "cache"));
  assert.equal(ys.senseSystemPrompt({ conversation: "c2", hash: "h3", parts: parts("e3", "t3") }).changed, false);
  assert.equal(ys.report().cache.promptChanges, 1);
});

test("effort and date live in the per-turn context block, so the system prompt file stays the same; a real change raises the signal", async t => {
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
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,youspeak:read,workspace:select",
  }, ["--import", SHIFT_DATE]);
  const session = await openSession(port, "cache");

  const first = await chat(session, "first");
  assert.equal((await post(session, "/api/settings", { effort: "low", chatMode: "orchestrate" })).status, 200);
  writeFileSync(shift, String(24 * 60 * 60 * 1000));
  const second = await chat(session, "second");

  const runs = fake.runs();
  const messages = fake.messages();
  assert.equal(runs.length, 2);
  assert.equal(runs[1].systemPrompt.content, runs[0].systemPrompt.content, "the same system prompt file, byte for byte");
  assert.doesNotMatch(runs[0].systemPrompt.content, /Effort:|- Date:|Git branch:|# Minds/);
  assert.match(runs[0].systemPrompt.content, /# YOUSPEAK — the Discipline/);
  const [one, two] = messages.map(entry => entry.context);
  assert.notEqual(one, two, "different context blocks");
  assert.match(one, /- Thinking: adaptive \| Effort: high/);
  assert.match(two, /- Thinking: adaptive \| Effort: low/);
  const date = block => block.match(/- Date: (\d{4}-\d{2}-\d{2})/)[1];
  assert.equal(Date.parse(date(two)) - Date.parse(date(one)), 24 * 60 * 60 * 1000, "the date moved a day");
  assert.match(two, /# Minds \(orchestrate mode\)/, "the minds hint is per turn too");
  assert.equal(messages[0].message, "first");
  assert.equal(messages[1].message, "second");
  for (const events of [first, second]) {
    assert.ok(!events.some(event => event.event === "youspeak_suggestion" && event.data.type === "cache"), "no cache signal");
  }
  assert.equal((await youspeak(session)).cache.promptChanges, 0);

  // A setting that is part of the system prompt does change it, and the signal says what.
  assert.equal((await post(session, "/api/settings", { workdir: elsewhere })).status, 200);
  const third = await chat(session, "FAKE:cache-miss");
  assert.notEqual(fake.runs()[2].systemPrompt.content, runs[0].systemPrompt.content);
  const suggestion = third.find(event => event.event === "youspeak_suggestion" && event.data.type === "cache")?.data;
  assert.ok(suggestion, "the cache signal is suggested");
  assert.match(suggestion.reason, /changed between turns of this conversation \(the environment block[^)]*\)/);
  assert.match(suggestion.reason, /Claude reported a cache miss \(system_changed, 1100 tokens\)/);
  const report = await youspeak(session);
  assert.equal(report.cache.promptChanges, 1);
  assert.equal(report.cache.misses, 1);
  assert.equal(report.usage.evidence.inputTokens, "reported");
  assert.equal(report.context.tokens, 3 + 1000 + 100, "the fake's latest request: input + cache read + cache creation");
  assert.equal(report.context.window, 1_000_000);
  assert.equal(report.context.evidence.window, "reported");
  assert.equal(report.usage.costUsd, 0.0369, "three turns' deltas, not the running total added up");
  for (const name of ["usage", "output", "thinking", "action", "context", "cache", "minds", "system"]) assertLabelled(report[name], name);
  assert.ok(report.evidenceKey.reported && report.evidenceKey.estimated, "the page can say what the labels mean");
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

// ── One rule module ─────────────────────────────────────────────────

test("youspeak-rule.mjs is the only source of the rule: YOUI Web, the terminal, sovereign and the harnesses import it", () => {
  // The rule's own phrases, which no hand copy may carry any more.
  const phrases = /No tool narration|Never compress|Zero-pad|Compress scaffolding|Dense status \(key:value|File cache: you've/;
  assert.match(YOUSPEAK_RULE, /^# YOUSPEAK — the Discipline\n/);
  assert.match(YOUSPEAK_RULE, /Never compress doubt: "probably", "unless", "might", "I think" are sacred\. When uncertain, expand\./);
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
