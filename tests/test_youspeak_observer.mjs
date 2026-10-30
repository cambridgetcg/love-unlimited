// YOUSPEAK rebuilt as an honest observer, slice Y1
// (docs/YOUSPEAK-OBSERVER-DESIGN.md §3; docs/YOUSPEAK.md): suggest only, one
// private row per session, the NaN and corrupt-reset bugs fixed, and a
// proposal-only ouroboros.
//
// Every store here is a temporary file named by historyPath, by
// YOUSPEAK_TEST_HISTORY_PATH for a test server (which ignores
// YOUSPEAK_HISTORY_PATH, so an exported one is never written by these tests),
// or by YOUSPEAK_HISTORY_PATH for the read-only CLIs; the real
// ~/.local/share/youi is never touched, and the tracked youspeak-history.json
// and youspeak-config.json are checked unchanged. Claude is
// tests/fixtures/fake-claude.mjs and vLLM a local fake; no network, no Keychain.

import assert from "node:assert/strict";
import http from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import {
  HISTORY_SCHEMA,
  LEGACY_HISTORY_PATH,
  appendHistoryRow,
  createKernel,
  detectFiller,
  isGradable,
  readHistory,
  readHistoryCached,
  trendDirection,
  trendsFromRows,
} from "../youspeak-kernel.mjs";
import { SessionRegistry } from "../youi-web/security.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const KERNEL_URL = pathToFileURL(join(REPO_ROOT, "youspeak-kernel.mjs")).href;
const FAKE = join(REPO_ROOT, "tests/fixtures/fake-claude.mjs");
const TRACKED = ["youspeak-history.json", "youspeak-config.json"].map(name => join(REPO_ROOT, name));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// ── Helpers ─────────────────────────────────────────────────────────

function sha(path) {
  return existsSync(path) ? createHash("sha256").update(readFileSync(path)).digest("hex") : null;
}

function fingerprint(paths) {
  return paths.map(path => ({ path, hash: sha(path), mtime: existsSync(path) ? statSync(path).mtimeMs : null }));
}

// The tracked files are the same after the test (and its servers' shutdown) as before it.
function guardTracked(t) {
  const before = fingerprint(TRACKED);
  t.after(async () => {
    await stopServers(t);
    assert.deepEqual(fingerprint(TRACKED), before, "the tracked youspeak-history.json and youspeak-config.json are unchanged");
  });
}

const testServers = new WeakMap();
async function stopServers(t) {
  await Promise.all((testServers.get(t) || []).map(child => stopChild(child)));
}
function stopChild(child, signal = "SIGTERM") {
  return new Promise(resolve => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve();
    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch {}
      resolve();
    }, 5_000);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill(signal);
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

function fakeClaude(t) {
  const dir = scratch(t, "ys-fake-claude-");
  const bin = join(dir, "claude");
  writeFileSync(bin, [
    `#!${process.execPath}`,
    `process.env.FAKE_CLAUDE_LOG = ${JSON.stringify(join(dir, "calls.jsonl"))};`,
    `process.env.FAKE_CLAUDE_SESSIONS = ${JSON.stringify(join(dir, "sessions"))};`,
    `import(${JSON.stringify(pathToFileURL(FAKE).href)});`,
    "",
  ].join("\n"));
  chmodSync(bin, 0o755);
  return {
    bin,
    cwd: join(dir, "claude-code-cwd"),
    runs() {
      const log = join(dir, "calls.jsonl");
      if (!existsSync(log)) return [];
      return readFileSync(log, "utf-8").split("\n").filter(Boolean).map(line => JSON.parse(line)).filter(entry => entry.step === "start");
    },
  };
}

async function startFakeVllm(t) {
  const queued = [];
  const waiters = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      let body = {};
      try { body = JSON.parse(Buffer.concat(chunks).toString("utf-8")); } catch {}
      const sse = events => {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        for (const event of events) res.write(`data: ${JSON.stringify(event)}\n\n`);
        res.end("data: [DONE]\n\n");
      };
      const entry = {
        body,
        respondText(text) {
          sse([
            { id: "fake", model: body.model, choices: [{ delta: { content: text }, finish_reason: null }] },
            { choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
          ]);
        },
        respondTool(name, input) {
          sse([
            { id: "fake-tool", model: body.model, choices: [{ delta: { tool_calls: [{ index: 0, id: `call_${Date.now()}`, function: { name, arguments: JSON.stringify(input) } }] }, finish_reason: null }] },
            { choices: [{ delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } },
          ]);
        },
      };
      const waiter = waiters.shift();
      if (waiter) waiter(entry);
      else queued.push(entry);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  t.after(() => server.close());
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    next() {
      if (queued.length > 0) return Promise.resolve(queued.shift());
      return new Promise(resolve => waiters.push(resolve));
    },
  };
}

async function startServer(t, extraEnv = {}, nodeArgs = []) {
  const temp = scratch(t, "ys-server-tmp-");
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
  return { port, child, stderr: () => stderr };
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

function cookiesFrom(response) {
  const pairs = (response.headers["set-cookie"] || []).map(value => value.split(";", 1)[0]);
  const csrfPair = pairs.find(value => value.startsWith("youi_csrf="));
  return {
    header: pairs.join("; "),
    csrf: csrfPair ? decodeURIComponent(csrfPair.slice("youi_csrf=".length)) : "",
  };
}

async function openSession(port, label, body = {}, browser = null) {
  const origin = `http://127.0.0.1:${port}`;
  browser ??= cookiesFrom(await request(port));
  const page = `page-${label}-0000000000000001`;
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: { Cookie: browser.header, Origin: origin, "X-YOUI-CSRF": browser.csrf, "X-YOUI-Page": page },
    body: { label, agent: "alpha", ...body },
  });
  assert.equal(created.status, 201, created.text);
  const credential = { id: created.json.session.id, token: created.json.sessionToken, page };
  const read = {
    Cookie: browser.header,
    "X-YOUI-Session": credential.id,
    "X-YOUI-Session-Token": credential.token,
    "X-YOUI-Page": credential.page,
  };
  return { origin, port, browser, credential, read, unsafe: { ...read, Origin: origin, "X-YOUI-CSRF": browser.csrf } };
}

function sseEvents(stream) {
  return stream.split("\n\n").filter(Boolean).map(chunk => ({
    event: chunk.match(/^event: (.+)$/m)?.[1],
    data: JSON.parse(chunk.match(/^data: (.+)$/m)?.[1] || "null"),
  }));
}

async function chat(session, message) {
  const response = await fetch(`${session.origin}/api/chat`, {
    method: "POST",
    headers: { ...session.unsafe, "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  assert.equal(response.status, 200);
  return sseEvents(await response.text());
}

function post(session, path, body) {
  return request(session.port, { path, method: "POST", headers: session.unsafe, body });
}

function storeLines(path) {
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf-8").split("\n").filter(Boolean);
}

const LONG_ANSWER = "The cache key at server.mjs:42 leaves out the session id, so two tabs share one entry; adding it fixes the stale reads.";

async function until(predicate, { timeoutMs = 5_000, label = "condition" } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(`timed out waiting for ${label}`);
}

// ── The kernel ──────────────────────────────────────────────────────

test("short text never turns the L1 totals into NaN", () => {
  for (const text of ["OK.", "收到。", "a", "Sure. "]) {
    const filler = detectFiller(text);
    for (const key of ["count", "tokens", "totalTokens", "weight"]) {
      assert.ok(Number.isFinite(filler[key]), `${key} is a number for ${JSON.stringify(text)}`);
    }
    assert.ok(filler.tokens <= filler.totalTokens, "filler is never more than the text");
  }
  const ys = createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  assert.equal(ys.senseOutput("OK.").grade, null, "too short to grade");
  assert.equal(ys.senseOutput("收到。").grade, null);
  ys.senseOutput("Sure! Let me check that. Here is what I found in the file, which is long enough to grade honestly.");
  const output = ys.report().output;
  assert.ok(Number.isFinite(output.totalTokens) && output.totalTokens > 0);
  assert.ok(Number.isFinite(output.usefulRatio));
  assert.ok(output.usefulRatio < 1, "the filler in the long reply still counts after two short ones");
  assert.notEqual(output.grade, "S");
  assert.doesNotMatch(ys.statusLine(), /NaN/);
});

test("a session with no output has no grade, and neither it nor one that only failed writes a row", t => {
  const dir = scratch(t, "ys-kernel-empty-");
  const historyPath = join(dir, "history.jsonl");
  const empty = createKernel({ agent: "alpha", route: "claude-code", model: "claude-opus-5-5", historyPath, legacyPath: null });
  empty.senseTurn(null);
  assert.equal(empty.report().output.grade, null, "no output, no grade (not S)");
  assert.equal(empty.report().output.usefulRatio, null);
  assert.match(empty.statusLine(), /^YS:—\(—\)/);
  assert.deepEqual(empty.endSession({ reason: "clear" }), { written: false, skipped: "no output", error: null, row: null });

  const failed = createKernel({ agent: "alpha", historyPath, legacyPath: null });
  failed.noteRoute("claude-code", "claude-opus-5-5");
  failed.senseOutput("partial answer before the error");
  failed.noteExchange({ ok: false });
  assert.equal(failed.endSession({ reason: "clear" }).skipped, "only failed");
  assert.equal(existsSync(historyPath), false, "nothing was written");
});

test("decide() suggests and never downgrades the model, and the kernel has no pruning", () => {
  const ys = createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  // What made the old L2 rule fire: a low thinking/output ratio on an Opus model.
  for (let i = 0; i < 5; i++) ys.senseThinking({ thinking_tokens: 0, output_tokens: 100 });
  const quiet = ys.decide("high", "claude-opus-5-5", null);
  assert.ok(!quiet.some(signal => signal.type === "model" || /downgrade|sonnet/i.test(`${signal.action} ${signal.reason}`)),
    "no model-downgrade signal");

  const pressed = ys.decide("high", "claude-opus-5-5", { fiveHour: { utilization: 0.97 } });
  const effort = pressed.find(signal => signal.type === "effort");
  assert.equal(effort.to, "medium");
  assert.match(effort.suggestion, /Nothing was changed/);
  assert.match(effort.suggestion, /Alpha keeps high/);
  assert.deepEqual(ys.newSuggestions(pressed).map(signal => signal.type), ["effort"]);
  assert.deepEqual(ys.newSuggestions(pressed), [], "each suggestion is shown once");
  assert.equal(ys.pruneContext, undefined, "pruneContext is gone");
  assert.equal(ys.persist, undefined, "per-message persist is gone");
});

test("a session writes one row, with its id and route, aggregates only, to a private file", t => {
  const dir = scratch(t, "ys-kernel-row-");
  const historyPath = join(dir, "youi", "youspeak", "history.jsonl");
  const ys = createKernel({ agent: "alpha", historyPath, legacyPath: null });
  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.noteRoute("ollama", "glm-5.1");
  ys.senseOutput("The secret answer is forty-two, kept out of the store.");
  ys.senseToolCall("read_file", { path: "/private/secret/path.txt" }, null);
  ys.senseToolCall("read_file", { path: "/private/secret/path.txt" }, null);
  ys.senseTurn(null);
  ys.noteExchange({ ok: true });
  const outcome = ys.endSession({ reason: "clear" });
  assert.equal(outcome.written, true);
  assert.equal(ys.endSession({ reason: "shutdown" }).skipped, "already ended", "at most one row per session");

  const lines = storeLines(historyPath);
  assert.equal(lines.length, 1);
  const row = JSON.parse(lines[0]);
  assert.equal(row.schema, HISTORY_SCHEMA);
  assert.match(row.session, UUID);
  assert.equal(row.session, ys.report().session);
  assert.equal(row.route, "claude-code", "the route most turns took");
  assert.equal(row.model, "claude-opus-5-5");
  assert.deepEqual(row.routes, { "claude-code": 2, ollama: 1 });
  assert.equal(row.agent, "alpha");
  assert.equal(row.endReason, "clear");
  assert.ok(Date.parse(row.startedAt) <= Date.parse(row.endedAt));
  assert.equal(row.toolCalls, 2);
  assert.equal(row.redundantReads, 1);
  for (const field of ["turns", "outputGrade", "usefulRatio", "thinkingTokens", "thinkAvgRatio", "toolErrors",
    "contextPeakTokens", "budgetBurned", "rateLimitHits", "signalCount"]) {
    assert.ok(field in row, `the row keeps ${field}`);
  }
  assert.doesNotMatch(lines[0], /secret|forty-two|path\.txt/, "no message text, paths or tool inputs");
  assert.equal(statSync(historyPath).mode & 0o777, 0o600);
  assert.equal(statSync(join(dir, "youi", "youspeak")).mode & 0o777, 0o700);
  assert.equal(statSync(join(dir, "youi")).mode & 0o777, 0o700);
});

test("concurrent writers append whole rows, never interleaved", async t => {
  const dir = scratch(t, "ys-kernel-race-");
  const historyPath = join(dir, "history.jsonl");
  const writers = 8;
  const perWriter = 25;
  const script = `
    import { appendHistoryRow } from ${JSON.stringify(KERNEL_URL)};
    const [writer, count, path] = process.argv.slice(1);
    for (let i = 0; i < Number(count); i++) {
      appendHistoryRow(path, { schema: 1, session: writer + "-" + i, pad: writer.repeat(4000) });
    }
  `;
  await Promise.all(Array.from({ length: writers }, (_, index) => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["--input-type=module", "-e", script, `w${index}`, String(perWriter), historyPath], { stdio: "ignore" });
    child.once("error", reject);
    child.once("exit", code => (code === 0 ? resolve() : reject(new Error(`writer ${index} exited ${code}`))));
  })));
  const store = readHistory(historyPath);
  assert.deepEqual(store.warnings, []);
  assert.equal(store.rows.length, writers * perWriter);
  assert.equal(new Set(store.rows.map(row => row.session)).size, writers * perWriter);
  for (const row of store.rows) assert.equal(row.pad, row.session.split("-")[0].repeat(4000), "each line is one writer's whole row");
});

test("a corrupt or partial store is reported, never reset", t => {
  const dir = scratch(t, "ys-kernel-corrupt-");
  const historyPath = join(dir, "history.jsonl");
  const good = JSON.stringify({ schema: 1, session: "kept-row", usefulRatio: 0.9, endedAt: "2026-10-30T00:00:00Z" });
  const original = `${good}\nnot json at all\n{"schema":1,"sess`;
  writeFileSync(historyPath, original);

  const store = readHistory(historyPath);
  assert.equal(store.rows.length, 1);
  assert.equal(store.skipped, 2);
  assert.match(store.warnings.join(" "), /2 of 3 lines/);
  assert.match(store.warnings.join(" "), /incomplete/);
  assert.match(store.warnings.join(" "), /nothing was reset/);

  const ys = createKernel({ agent: "alpha", historyPath, legacyPath: null });
  assert.equal(ys.storeStatus().rows, 1);
  assert.equal(ys.storeStatus().warnings.length, 1, "the report carries the warning");
  ys.noteRoute("vllm", "Qwen/Qwen2.5-72B-Instruct-AWQ");
  ys.senseOutput("A real answer.");
  ys.noteExchange({ ok: true });
  assert.equal(ys.endSession({ reason: "idle" }).written, true);

  const after = readFileSync(historyPath, "utf-8");
  assert.ok(after.startsWith(original), "what was there is left exactly as it was");
  const again = readHistory(historyPath);
  assert.equal(again.rows.length, 2, "the new row starts on its own line, not glued to the partial one");
  assert.equal(again.rows[1].route, "vllm");
  assert.equal(again.skipped, 2);
});

test("code-mixed Cantonese and doubt are not graded as filler (the review's lines)", () => {
  const kernel = () => createKernel({ agent: "alpha", route: "claude-code", historyPath: null, legacyPath: null });
  // The lines the review found graded D, each raising filler_regression in five.
  const reviewLines = [
    "我唔 sure 呢個 fix 啱唔啱，你 check 下先。",
    "Okay 我睇咗 server.mjs，問題喺第 42 行。",
    "我仲未 sure 個 root cause，可能係 cache。",
    "Make sure 你 run 咗 test 先 merge。",
  ];
  // Long enough to grade: doubt and "make sure" are not openers.
  const graded = [
    "我唔 sure 呢個 fix 啱唔啱，因為 server.mjs 第 42 行嘅 cache key 冇包 session id，所以兩個 tab 會共用同一個 entry；你 check 下先，我未 run test。",
    "I'm not sure this is right; the cache may be stale, so check server.mjs:42 before merging this change into main.",
    "Make sure you run the tests before merging the branch, because the cache layer in server.mjs:42 is still fragile.",
  ];
  for (const text of reviewLines) {
    assert.equal(kernel().senseOutput(text).grade, null, `too short to grade: ${text}`);
  }
  for (const text of graded) {
    assert.equal(isGradable(text), true);
    assert.equal(kernel().senseOutput(text).grade, "S", `no filler in: ${text}`);
    assert.ok(!detectFiller(text).patterns.some(p => p.name === "affirmative"), "'sure' mid-sentence is not an opener");
  }
  // An opener still counts at the start of the text, a sentence or a line.
  const openers = detectFiller("Sure, here it is. Okay. Make sure it runs.\nGreat question about the cache.").patterns;
  assert.equal(openers.find(p => p.name === "affirmative")?.count, 3);
  // Mostly non-ASCII is not graded, however long.
  assert.equal(isGradable("呢個 fix 我唔肯定，可能係 cache 嘅問題，你睇下先。".repeat(6)), false);

  // Five and more such blocks raise no filler_regression and no suggestion.
  const ys = kernel();
  for (const text of [...reviewLines, ...graded, ...reviewLines]) ys.senseOutput(text);
  const signals = ys.decide("high", "claude-opus-5-5", null);
  assert.ok(!signals.some(signal => signal.action === "filler_regression"));
  assert.deepEqual(ys.newSuggestions(signals), []);
  // English filler, graded, still raises it.
  const padded = kernel();
  for (let i = 0; i < 5; i++) padded.senseOutput("Sure! Let me check that. Here is what I found in the file, which is long enough to grade honestly.");
  assert.ok(padded.decide("high", "claude-opus-5-5", null).some(signal => signal.action === "filler_regression"));
});

test("a row's times are its first and last activity; the cut is closedAt", t => {
  const historyPath = join(scratch(t, "ys-kernel-time-"), "history.jsonl");
  let clock = Date.UTC(2026, 9, 30, 9, 0);
  const now = () => clock;
  const minutes = m => { clock += m * 60_000; };
  const iso = ms => new Date(ms).toISOString();

  // The kernel is created at 09:00; the first message is at 09:01, the last at 09:05.
  let ys = createKernel({ agent: "alpha", historyPath, legacyPath: null, now });
  minutes(1);
  const first = clock;
  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.senseOutput(LONG_ANSWER);
  ys.noteExchange({ ok: true });
  minutes(4);
  const last = clock;
  ys.senseOutput(LONG_ANSWER);
  ys.noteExchange({ ok: true });
  // Thirty minutes of silence, then the idle cut, which starts a fresh kernel.
  minutes(30);
  const cut = clock;
  assert.equal(ys.endSession({ reason: "idle" }).written, true);
  ys = createKernel({ agent: "alpha", historyPath, legacyPath: null, now });
  // Three hours later, one message; cleared five minutes after it.
  minutes(180);
  const back = clock;
  ys.noteRoute("claude-code", "claude-opus-5-5");
  ys.senseOutput(LONG_ANSWER);
  ys.noteExchange({ ok: true });
  minutes(5);
  assert.equal(ys.endSession({ reason: "clear" }).written, true);

  const [idle, cleared] = readHistory(historyPath).rows;
  assert.equal(idle.startedAt, iso(first), "starts at the first message, not when the kernel was made");
  assert.equal(idle.endedAt, iso(last), "ends at the last activity, not at the idle cut");
  assert.equal(idle.closedAt, iso(cut));
  assert.equal(idle.elapsed, 4, "the active span, in minutes");
  assert.equal(cleared.startedAt, iso(back), "the next session starts at its first message, not at the cut");
  assert.equal(cleared.endedAt, iso(back));
  assert.equal(cleared.elapsed, 0);
});

test("suggestions name the remedies of the surface they are shown on", () => {
  const bigContext = [];
  for (let i = 0; i < 66; i++) {
    bigContext.push({ role: "assistant", content: [{ type: "tool_use", id: `t${i}`, name: "bash", input: { command: "cat f" } }] });
    bigContext.push({ role: "user", content: [{ type: "tool_result", tool_use_id: `t${i}`, content: "x".repeat(50_000) }] });
  }
  const suggestionsOn = opts => {
    const ys = createKernel({ agent: "alpha", model: "claude-opus-4-6", historyPath: null, legacyPath: null, ...opts });
    ys.senseContext(bigContext, 1000);
    ys.senseTurn(null);
    ys.senseRateLimit();
    ys.senseRateLimit();
    const byAction = {};
    for (const signal of ys.decide("high", "claude-opus-4-6", { fiveHour: { utilization: 0.97 } })) byAction[signal.action] = signal.suggestion;
    return byAction;
  };
  const web = suggestionsOn({ surface: "web" });
  assert.match(web.reduce, /lower effort from high to medium in Settings/);
  assert.match(web.prune_recommended, /new conversation \(Clear\)/);
  assert.match(web.evict_old_results, /\(Clear\)/);

  // The terminal: the route implies the surface; /effort and /switch are its real commands.
  const terminal = suggestionsOn({ route: "terminal-legacy" });
  assert.match(terminal.reduce, /type \/effort medium/);
  assert.match(terminal.prune_recommended, /type \/switch alpha to start a fresh conversation \(\/clear only clears the screen\)/);
  assert.match(terminal.evict_old_results, /\/switch alpha/);
  assert.match(terminal.rate_limit_pressure, /\/effort/);
  for (const text of Object.values(terminal)) assert.doesNotMatch(text, /Settings|\(Clear\)/, text);
  const youi = readFileSync(join(REPO_ROOT, "youi.mjs"), "utf-8");
  for (const command of ["/effort", "/switch"]) assert.match(youi, new RegExp(`case "${command}"`), `the terminal has ${command}`);

  const sovereign = suggestionsOn({ route: "sovereign" });
  assert.match(sovereign.reduce, /--effort medium/);
  assert.match(sovereign.prune_recommended, /without --continue/);
  assert.match(sovereign.rate_limit_pressure, /Ctrl\+C/);
  for (const text of Object.values(sovereign)) assert.doesNotMatch(text, /Settings|\(Clear\)|\/switch|\/effort/, text);
  const sovereignSource = readFileSync(join(REPO_ROOT, "sovereign.mjs"), "utf-8");
  for (const flag of ["--effort", "--continue"]) assert.match(sovereignSource, new RegExp(`case "${flag}"`), `sovereign has ${flag}`);
});

test("only the strongest effort suggestion fires, and none on the Ollama or vLLM routes", () => {
  const kernel = () => createKernel({ agent: "alpha", historyPath: null, legacyPath: null });
  const near = kernel().decide("max", "claude-opus-5-5", { fiveHour: { utilization: 0.97 } });
  assert.deepEqual(near.filter(signal => signal.type === "effort").map(signal => signal.to), ["medium"], "max→medium only, not also max→high");
  const pressed = kernel().decide("max", "claude-opus-5-5", { fiveHour: { utilization: 0.9 } });
  assert.deepEqual(pressed.filter(signal => signal.type === "effort").map(signal => signal.to), ["high"]);

  for (const route of ["ollama", "vllm"]) {
    const told = kernel();
    told.senseTurn(null);
    told.senseRateLimit();
    told.senseRateLimit();
    const signals = told.decide("max", "glm-5.1", { fiveHour: { utilization: 0.97 } }, { route });
    assert.ok(!signals.some(signal => signal.type === "effort"), `no effort signal on ${route}`);
    const pressure = signals.find(signal => signal.action === "rate_limit_pressure");
    assert.doesNotMatch(pressure.suggestion, /effort/i, "no 'lower effort' where there is no effort setting");
    // The route noteRoute recorded counts too.
    const noted = kernel();
    noted.noteRoute(route, "glm-5.1");
    assert.ok(!noted.decide("high", "glm-5.1", { fiveHour: { utilization: 0.97 } }).some(signal => signal.type === "effort"));
  }
});

test("a row with a field of the wrong type is reported and skipped, and trends never throw", t => {
  const historyPath = join(scratch(t, "ys-kernel-typed-"), "history.jsonl");
  const good = day => JSON.stringify({ schema: 1, session: `good-${day}`, usefulRatio: 0.9, endedAt: `2026-10-${day}T09:00:00.000Z` });
  writeFileSync(historyPath, [
    good(28),
    JSON.stringify({ schema: 1, session: "bad-time", usefulRatio: 0.5, endedAt: 1_730_000_000_000 }),
    JSON.stringify({ schema: 1, session: "bad-ratio", usefulRatio: "0.9", endedAt: "2026-10-29T09:00:00.000Z" }),
    JSON.stringify({ schema: 1, session: "bad-signals", signals: "effort:reduce" }),
    good(30),
    "",
  ].join("\n"));
  const store = readHistory(historyPath);
  assert.deepEqual(store.rows.map(row => row.session), ["good-28", "good-30"]);
  assert.equal(store.skipped, 3);
  assert.match(store.warnings.join(" "), /3 have a field of the wrong type/);
  const ys = createKernel({ agent: "alpha", historyPath, legacyPath: null });
  assert.equal(ys.trends().span, "2026-10-28 → 2026-10-30");
  // Even a row that reached trendsFromRows without checks gives "?", not an exception.
  assert.equal(trendsFromRows([{ usefulRatio: 0.9, endedAt: 12345 }, { usefulRatio: 0.9 }]).span, "? → ?");
});

test("the store must be a regular file of this user's: a symlink or a device is refused and left alone", t => {
  const dir = scratch(t, "ys-kernel-nofollow-");
  const target = join(dir, "elsewhere.txt");
  writeFileSync(target, "untouched\n");
  const link = join(dir, "history.jsonl");
  symlinkSync(target, link);
  const session = historyPath => {
    const ys = createKernel({ agent: "alpha", historyPath, legacyPath: null });
    ys.noteRoute("claude-code", "claude-opus-5-5");
    ys.senseOutput(LONG_ANSWER);
    ys.noteExchange({ ok: true });
    return ys.endSession({ reason: "clear" });
  };
  const viaLink = session(link);
  assert.equal(viaLink.written, false);
  assert.ok(viaLink.error, "the symlink is refused with an error");
  assert.equal(readFileSync(target, "utf-8"), "untouched\n", "the file the link points to is not written");
  const device = session("/dev/null");
  assert.equal(device.written, false);
  assert.match(device.error, /not a regular file/);
  assert.doesNotMatch(device.error, /EPERM/);
  // An existing 0644 store is made 0600.
  const loose = join(dir, "loose.jsonl");
  writeFileSync(loose, "", { mode: 0o644 });
  chmodSync(loose, 0o644);
  assert.equal(session(loose).written, true);
  assert.equal(statSync(loose).mode & 0o777, 0o600);
});

test("the store is read once while it is unchanged, and the trend tolerance is shared", t => {
  const historyPath = join(scratch(t, "ys-kernel-cache-"), "history.jsonl");
  appendHistoryRow(historyPath, { schema: 1, session: "a", usefulRatio: 0.9, endedAt: "2026-10-30T09:00:00.000Z" });
  const first = readHistoryCached(historyPath);
  assert.equal(readHistoryCached(historyPath), first, "unchanged: the same read");
  appendHistoryRow(historyPath, { schema: 1, session: "b", usefulRatio: 0.905, endedAt: "2026-10-30T10:00:00.000Z" });
  const second = readHistoryCached(historyPath);
  assert.notEqual(second, first);
  assert.equal(second.rows.length, 2);
  // Within one percentage point is steady, in the kernel and in evolve alike.
  assert.equal(trendsFromRows(second.rows).fillerTrend.direction, "steady");
  assert.equal(trendDirection(0.9, 0.92), "improving");
  assert.equal(trendDirection(0.92, 0.9), "regressing");
  const evolve = readFileSync(join(REPO_ROOT, "youspeak-evolve.mjs"), "utf-8");
  assert.match(evolve, /trendDirection\(earlyUseful, lateUseful\)/);
});

test("a process holding a YOUSPEAK session writes its row on SIGHUP, SIGTERM, EOF, /exit and a crash, once", async t => {
  const dir = scratch(t, "ys-exit-");
  // The terminal's pattern (youi.mjs): one session at a time, /switch ends it
  // and starts another, /exit and EOF end it, and endSessionOnExit covers the rest.
  const script = `
    import { createKernel, endSessionOnExit } from ${JSON.stringify(KERNEL_URL)};
    import { createInterface } from "node:readline";
    const [path] = process.argv.slice(1);
    const make = () => createKernel({ agent: "alpha", route: "terminal-legacy", model: "m", historyPath: path, legacyPath: null });
    const work = k => { k.noteRoute("terminal-legacy", "m"); k.senseOutput(${JSON.stringify(LONG_ANSWER)}); k.noteExchange({ ok: true }); };
    let ys = make();
    work(ys);
    endSessionOnExit(reason => ys.endSession({ reason, model: "m" }));
    const rl = createInterface({ input: process.stdin });
    rl.on("line", line => {
      if (line === "/switch") { ys.endSession({ reason: "switch", model: "m" }); ys = make(); work(ys); }
      if (line === "/exit") { ys.endSession({ reason: "exit", model: "m" }); process.exit(0); }
      if (line === "crash") throw new Error("a crash, for the test");
      process.stdout.write("ok\\n");
    });
    rl.on("close", () => { ys.endSession({ reason: "exit", model: "m" }); process.exit(0); });
    process.stdout.write("ready\\n");
  `;
  const run = async (name, steps) => {
    const historyPath = join(dir, `${name}.jsonl`);
    const child = spawn(process.execPath, ["--input-type=module", "-e", script, historyPath], { stdio: ["pipe", "pipe", "ignore"] });
    // A hung child fails the test instead of holding it open.
    const guard = setTimeout(() => child.kill("SIGKILL"), 10_000);
    t.after(() => { clearTimeout(guard); child.kill("SIGKILL"); });
    let out = "";
    child.stdout.on("data", chunk => { out += chunk; });
    const exited = new Promise(resolve => child.once("exit", (code, signal) => resolve({ code, signal })));
    await until(() => out.includes("ready"), { label: "the child" });
    for (const step of steps) {
      if (step.startsWith("SIG")) child.kill(step);
      else if (step === "EOF") child.stdin.end();
      else {
        const acks = out.split("ok\n").length;
        child.stdin.write(`${step}\n`);
        if (step === "/switch") await until(() => out.split("ok\n").length > acks, { label: "the switch" });
      }
    }
    const result = await exited;
    clearTimeout(guard);
    return { ...result, reasons: storeLines(historyPath).map(line => JSON.parse(line).endReason) };
  };
  assert.deepEqual(await run("hup", ["/switch", "SIGHUP"]), { code: 129, signal: null, reasons: ["switch", "hangup"] });
  assert.deepEqual(await run("term", ["SIGTERM"]), { code: 143, signal: null, reasons: ["terminated"] });
  assert.deepEqual(await run("eof", ["EOF"]), { code: 0, signal: null, reasons: ["exit"] });
  assert.deepEqual(await run("exit", ["/switch", "/exit"]), { code: 0, signal: null, reasons: ["switch", "exit"] });
  assert.deepEqual(await run("crash", ["crash"]), { code: 1, signal: null, reasons: ["exit"] });

  // The terminal and sovereign use it; the terminal's /switch waits for a running task.
  const youi = readFileSync(join(REPO_ROOT, "youi.mjs"), "utf-8");
  assert.match(youi, /endSessionOnExit\(reason => endYouspeak\(reason\)\)/);
  assert.match(youi, /if \(taskRunning\) \{[^\n]*\/switch when it has finished/);
  const sovereign = readFileSync(join(REPO_ROOT, "sovereign.mjs"), "utf-8");
  assert.match(sovereign, /endSessionOnExit\(reason => ys\.endSession/);
  assert.match(sovereign, /reason: "interrupted"/);
  assert.match(sovereign, /runYouspeak\?\.endSession\(\{ reason: "fatal"/);
  assert.match(sovereign, /createKernel\(\{ agent: "sovereign", route: "sovereign", surface: "sovereign"/);
});

test("a browser session that expires, or whose client expires, writes its row through onSessionEnd", t => {
  const historyPath = join(scratch(t, "ys-registry-"), "history.jsonl");
  let clock = 0;
  // The server's wiring: onSessionEnd ends the session's YOUSPEAK session.
  const registry = new SessionRegistry({
    stateFactory: () => ({ ys: createKernel({ agent: "alpha", historyPath, legacyPath: null, now: () => clock }) }),
    onSessionEnd: (session, reason) => session.state.ys.endSession({ reason }),
    now: () => clock,
    sessionTtlMs: 1_000,
    clientTtlMs: 10_000,
  });
  const work = ys => { ys.noteRoute("claude-code", "claude-opus-5-5"); ys.senseOutput(LONG_ANSWER); ys.noteExchange({ ok: true }); };
  const client = registry.createClient();
  const { session: stale } = registry.createSession(client);
  work(stale.state.ys);
  clock = 2_000;
  registry.cleanup();
  const other = registry.createClient();
  const { session: orphan } = registry.createSession(other);
  work(orphan.state.ys);
  clock = 20_000;
  registry.cleanup();
  assert.deepEqual(storeLines(historyPath).map(line => JSON.parse(line).endReason), ["expired", "expired"]);
});

// ── YOUI Web ────────────────────────────────────────────────────────

test("a Claude turn that once cut effort leaves it as chosen and sends a suggestion", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const historyPath = join(scratch(t, "ys-web-effort-"), "history.jsonl");
  const { port } = await startServer(t, { YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd, YOUSPEAK_TEST_HISTORY_PATH: historyPath });
  const session = await openSession(port, "effort");

  const first = await chat(session, "FAKE:rate-limit-high");
  const names = first.map(event => event.event);
  const signals = first.find(event => event.event === "youspeak_signals")?.data.signals || [];
  assert.ok(signals.some(signal => signal.type === "effort" && signal.to === "medium"), "the 97% window still raises the effort signal");
  const suggestion = first.find(event => event.event === "youspeak_suggestion")?.data;
  assert.equal(suggestion?.type, "effort");
  assert.match(suggestion.suggestion, /Nothing was changed/);
  assert.ok(!names.includes("youspeak_action"), "nothing is reported as done, because nothing is done");

  const current = await request(port, { path: "/api/sessions/current", headers: session.read });
  assert.equal(current.json.session.effort, "high", "effort stays where Alpha's default put it");

  // Two more turns at 97%: the effort signal is raised again, but the
  // suggestion is not repeated; the effort sent to Claude Code is unchanged,
  // and no turn suggests a cheaper model.
  const later = [...await chat(session, "FAKE:rate-limit-high"), ...await chat(session, "FAKE:rate-limit-high")];
  const runs = fake.runs();
  assert.equal(runs.length, 3);
  const effortFlag = run => run.argv[run.argv.indexOf("--effort") + 1];
  assert.equal(effortFlag(runs[2]), effortFlag(runs[0]));
  const laterSignals = later.filter(event => event.event === "youspeak_signals");
  assert.equal(laterSignals.length, 2, "each later turn sends its signals");
  for (const event of laterSignals) {
    assert.ok(event.data.signals.some(signal => signal.type === "effort" && signal.to === "medium"), "the same signal, raised again");
  }
  assert.ok(!later.some(event => event.event === "youspeak_suggestion"), "but not suggested again");
  const allSignals = [...first, ...later].filter(event => event.event === "youspeak_signals").flatMap(event => event.data.signals);
  assert.ok(!allSignals.some(signal => signal.type === "model"), "no 'sonnet may suffice'");
  assert.equal(storeLines(historyPath).length, 0, "nothing is written per message");
});

test("the vLLM loop neither cuts effort nor prunes history", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const vllm = await startFakeVllm(t);
  const historyPath = join(scratch(t, "ys-web-vllm-"), "history.jsonl");
  const { port } = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin,
    YOUI_CLAUDE_CWD: fake.cwd,
    OLLAMA_VLLM_BASE_URL: vllm.baseUrl,
    YOUI_MAX_BODY_BYTES: "5000000",
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,tools:filesystem:read,youspeak:read",
    YOUSPEAK_TEST_HISTORY_PATH: historyPath,
  });
  // A Claude turn reports the 5-hour window at 97% (the budget is process-wide).
  await chat(await openSession(port, "budget"), "FAKE:rate-limit-high");

  const session = await openSession(port, "vllm", { model: "Qwen/Qwen2.5-72B-Instruct-AWQ" });
  const turn = async (message, respond) => {
    const pending = chat(session, message);
    for (const answer of respond) answer(await vllm.next());
    return pending;
  };
  // An early tool result, long enough that the old pruning would have cut it.
  await turn("read the readme", [
    entry => entry.respondTool("read_file", { path: "README.md" }),
    entry => entry.respondText("read it"),
  ]);
  await turn("hello", [entry => entry.respondText("hi")]);
  await turn("hello again", [entry => entry.respondText("hi again")]);
  // ~850k estimated tokens: past the 800k line where the old code pruned.
  const events = await turn(`${"x".repeat(3_400_000)}`, [
    entry => entry.respondTool("read_file", { path: "README.md" }),
    entry => entry.respondText("done"),
  ]);

  const signals = events.filter(event => event.event === "youspeak_signals").flatMap(event => event.data.signals);
  // The window is at 97%, but this loop sends no effort and has no Claude 5-hour window.
  assert.ok(!signals.some(signal => signal.type === "effort"), "no effort signal on the vLLM route");
  assert.ok(signals.some(signal => signal.action === "prune_recommended"), "the context signal fired");
  const suggested = events.filter(event => event.event === "youspeak_suggestion").map(event => event.data.action);
  assert.ok(suggested.includes("prune_recommended"));
  assert.ok(!events.some(event => event.event === "youspeak_action"));

  const current = await request(port, { path: "/api/sessions/current?include=messages", headers: session.read });
  assert.equal(current.json.session.effort, "high");
  const results = current.json.session.messages
    .flatMap(message => (Array.isArray(message.content) ? message.content : []))
    .filter(block => block.type === "tool_result");
  assert.equal(results.length, 2);
  const readme = readFileSync(join(REPO_ROOT, "README.md"), "utf-8");
  assert.ok(readme.length > 400);
  for (const block of results) {
    assert.doesNotMatch(block.content, /pruned/, "no tool result was cut");
    assert.ok(block.content.length >= readme.length, "the whole file is still there");
  }
});

test("a session's row is written once when it is cleared, switched, closed, reset or idle, or the server stops; empty and failed sessions write none", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const historyPath = join(scratch(t, "ys-web-rows-"), "history.jsonl");
  const { port, child } = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin,
    YOUI_CLAUDE_CWD: fake.cwd,
    YOUSPEAK_TEST_HISTORY_PATH: historyPath,
    YOUI_TEST_YOUSPEAK_IDLE_MS: "1500",
    // youspeak:reset is a developer capability.
    YOUI_CAPABILITY_PROFILE: "developer",
  });
  const rows = () => storeLines(historyPath).map(line => JSON.parse(line));

  const cleared = await openSession(port, "cleared");
  await chat(cleared, "hello");
  assert.equal(rows().length, 0, "no row per message");
  assert.equal((await post(cleared, "/api/clear")).status, 200);
  assert.equal(rows().length, 1);
  assert.equal((await post(cleared, "/api/clear")).status, 200);
  assert.equal(rows().length, 1, "a cleared session with nothing new writes nothing");
  const [first] = rows();
  assert.equal(first.endReason, "clear");
  assert.equal(first.route, "claude-code");
  assert.equal(first.model, "claude-opus-5-5");
  assert.equal(first.agent, "alpha");
  assert.equal(first.exchanges.ok, 1);
  assert.match(first.session, UUID);

  // /api/youspeak shows the store, and no warning while it is sound.
  const summary = await request(port, { path: "/api/youspeak", headers: cleared.read });
  assert.equal(summary.json.store.rows, 1);
  assert.deepEqual(summary.json.store.warnings, []);
  assert.equal(summary.json.overallGrade, null, "the fresh session has no output and no grade");

  await openSession(port, "empty");
  // A turn that produced text and then failed: it has output, so only the
  // only-failed rule keeps it out of the store.
  const failed = await openSession(port, "failed");
  const failedEvents = await chat(failed, "FAKE:text-then-error boom");
  assert.ok(failedEvents.some(event => event.event === "error"));
  assert.ok(failedEvents.some(event => /^text/.test(event.event || "") && event.data?.youspeak), "the failed turn's text was observed");

  const switched = await openSession(port, "switched");
  await chat(switched, "before the switch");
  assert.equal((await post(switched, "/api/switch", { agent: "beta" })).status, 200);
  assert.equal(rows().at(-1).endReason, "switch");
  assert.equal(rows().at(-1).agent, "alpha");

  const closed = await openSession(port, "closed");
  await chat(closed, "about to close");
  const deleted = await request(port, { path: `/api/sessions/${closed.credential.id}`, method: "DELETE", headers: closed.unsafe });
  assert.equal(deleted.status, 200);
  assert.equal(rows().at(-1).endReason, "closed");

  const reset = await openSession(port, "reset");
  await chat(reset, "before the reset");
  assert.equal((await post(reset, "/api/youspeak/reset")).status, 200);
  assert.equal(rows().at(-1).endReason, "reset");

  const idle = await openSession(port, "idle");
  await chat(idle, "then silence");
  await until(() => rows().some(row => row.endReason === "idle"), { label: "the idle row" });
  const idleRow = rows().find(row => row.endReason === "idle");
  assert.ok(Date.parse(idleRow.closedAt) - Date.parse(idleRow.endedAt) >= 1500,
    "the idle row ends at its last activity; the cut, 1.5 s of silence later, is closedAt");
  assert.ok(idleRow.elapsed < 0.1, "elapsed is the active span, not the silence");

  // After the cut, each conversation's next session starts at its next message.
  const back = Date.now();
  await chat(idle, "back after the cut");
  await chat(cleared, "a second conversation");
  await stopChild(child);
  const all = rows();
  // Exactly these: the shutdown's rows are not written again by the exit hook.
  assert.deepEqual(all.map(row => row.endReason), ["clear", "switch", "closed", "reset", "idle", "shutdown", "shutdown"]);
  for (const row of all.filter(row => row.endReason === "shutdown")) {
    assert.ok(Date.parse(row.startedAt) >= back, "a session starts at its first message, not when its kernel was made");
  }
  for (const row of all) assert.ok(Date.parse(row.startedAt) <= Date.parse(row.endedAt) && Date.parse(row.endedAt) <= Date.parse(row.closedAt));
  assert.ok(all.every(row => row.textBlocks > 0), "the empty and the failed sessions wrote none");
});

test("YOUI Web writes open sessions' rows when its terminal closes (SIGHUP) or it crashes, and a test server ignores YOUSPEAK_HISTORY_PATH", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const dir = scratch(t, "ys-web-exit-");
  // A person's exported store: a test server must never write it.
  const exported = join(dir, "exported-history.jsonl");
  // A crash stands in for any uncaught error: SIGUSR2 throws inside the server process.
  const crashOnUsr2 = ["--import", 'data:text/javascript,process.on("SIGUSR2", () => { throw new Error("a crash, for the test"); })'];
  for (const [how, signal, nodeArgs, reason] of [["hangup", "SIGHUP", [], "shutdown"], ["crash", "SIGUSR2", crashOnUsr2, "exit"]]) {
    const historyPath = join(dir, `${how}.jsonl`);
    const { port, child } = await startServer(t, {
      YOUI_CLAUDE_BIN: fake.bin,
      YOUI_CLAUDE_CWD: fake.cwd,
      YOUSPEAK_HISTORY_PATH: exported,
      YOUSPEAK_TEST_HISTORY_PATH: historyPath,
    }, nodeArgs);
    await chat(await openSession(port, how), "hello");
    assert.equal(storeLines(historyPath).length, 0, "nothing per message");
    const exited = new Promise(resolve => child.once("exit", (code, sig) => resolve({ code, sig })));
    child.kill(signal);
    const { code } = await exited;
    assert.equal(code, how === "crash" ? 1 : 0);
    assert.deepEqual(storeLines(historyPath).map(line => JSON.parse(line).endReason), [reason], `${how}: the open session's row`);
  }
  assert.equal(existsSync(exported), false, "YOUSPEAK_HISTORY_PATH is ignored under YOUI_TEST");

  // With no test store, a test server writes nowhere.
  const { port } = await startServer(t, { YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd, YOUSPEAK_HISTORY_PATH: exported });
  const session = await openSession(port, "nowhere");
  await chat(session, "hello");
  assert.equal((await post(session, "/api/clear")).status, 200);
  const summary = await request(port, { path: "/api/youspeak", headers: session.read });
  assert.equal(summary.json.store.enabled, false);
  assert.equal(existsSync(exported), false);
});

test("a browser session evicted for room writes its row, and the trends endpoint carries store warnings", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const historyPath = join(scratch(t, "ys-web-evict-"), "history.jsonl");
  writeFileSync(historyPath, "not json\n");
  const { port } = await startServer(t, { YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd, YOUSPEAK_TEST_HISTORY_PATH: historyPath });
  const first = await openSession(port, "evict-00");
  await chat(first, "the oldest session");
  // A browser holds at most 24 sessions; the 25th evicts the least recently seen.
  for (let i = 1; i <= 24; i++) await openSession(port, `evict-${String(i).padStart(2, "0")}`, {}, first.browser);
  const rows = readHistory(historyPath).rows;
  assert.deepEqual(rows.map(row => row.endReason), ["evicted"]);

  const latest = await openSession(port, "trends", {}, first.browser);
  const trends = await request(port, { path: "/api/youspeak/trends", headers: latest.read });
  assert.equal(trends.status, 200);
  assert.match(trends.json.warnings.join(" "), /could not be read and were skipped/);
});

test("YOUI Web's chat code applies no YOUSPEAK signal and writes nothing per message", () => {
  for (const file of ["youi-web/server.mjs", "youi.mjs", "sovereign.mjs"]) {
    const source = readFileSync(join(REPO_ROOT, file), "utf-8");
    assert.doesNotMatch(source, /pruneContext/, `${file} prunes nothing`);
    assert.doesNotMatch(source, /\.effort\s*=\s*sig\b|effort_reduced|context_pruned/, `${file} changes no effort`);
    assert.doesNotMatch(source, /\bys\.persist\(|\.ys\.persist\(/, `${file} has no per-message persist`);
  }
  const server = readFileSync(join(REPO_ROOT, "youi-web/server.mjs"), "utf-8");
  assert.doesNotMatch(server, /"YOUSPEAK\.md"/, "the dead <soulDir>/YOUSPEAK.md include is gone");
});

// ── The ouroboros and the audit ─────────────────────────────────────

test("the ouroboros prints full proposed diffs and writes nothing", async t => {
  const dir = scratch(t, "ys-evolve-");
  const historyPath = join(dir, "store", "history.jsonl");
  for (let i = 0; i < 4; i++) {
    const ys = createKernel({ agent: "alpha", historyPath, legacyPath: null });
    ys.noteRoute("claude-code", "claude-opus-5-5");
    ys.senseOutput("Sure! Let me check that. Here is what I found. I am going to read the file. Okay. Perfect! ");
    // Three repeated reads: add_cache_reminder is already in sovereign.mjs, so it prints no diff.
    for (let read = 0; read < 4; read++) ys.senseToolCall("read_file", { path: "README.md" }, null);
    ys.noteExchange({ ok: true });
    assert.equal(ys.endSession({ reason: "test" }).written, true);
  }
  const work = join(dir, "cwd");
  mkdirSync(work);
  const watched = [join(REPO_ROOT, "sovereign.mjs"), ...TRACKED, historyPath];
  const before = fingerprint(watched);

  const run = (...args) => spawnSync(process.execPath, [join(REPO_ROOT, "youspeak-evolve.mjs"), ...args], {
    cwd: work,
    env: { ...process.env, YOUSPEAK_HISTORY_PATH: historyPath },
    encoding: "utf-8",
  });
  const cycle = run("cycle");
  assert.equal(cycle.status, 0, cycle.stderr);
  assert.match(cycle.stdout, /--- a\/sovereign\.mjs/);
  assert.match(cycle.stdout, /\+\+\+ b\/sovereign\.mjs \(proposed, not applied\)/);
  assert.match(cycle.stdout, /^\+BAD: "Let me check that" \/ "Here's what I found" \/ "I'll now proceed to"$/m, "the full proposed text");
  assert.match(cycle.stdout, /^ No filler\. No preamble\./m, "with its context");
  assert.match(cycle.stdout, /^\+Target: ≥80% of output tokens should be substantive content, not scaffolding\.`\);$/m);
  assert.match(cycle.stdout, /Nothing was written/);
  assert.match(cycle.stdout, /No diff: already in sovereign\.mjs's YOUSPEAK Protocol block/);
  assert.match(cycle.stdout, /2 proposed diff\(s\) and 0 described proposal\(s\)/, "a proposal with no diff is not counted");
  assert.match(cycle.stdout, /Each diff is drawn against the unmodified sovereign\.mjs/);
  assert.match(run("reflect").stdout, /steady/, "identical sessions are steady, by the kernel's tolerance");
  for (const command of ["transmute", "reflect", "distill", "history"]) assert.equal(run(command).status, 0);
  const integrate = run("integrate", "--apply");
  assert.equal(integrate.status, 2);
  assert.match(integrate.stdout, /proposal-only/);

  assert.deepEqual(fingerprint(watched), before, "sovereign.mjs, the tracked files and the store are unchanged");
  assert.deepEqual(readdirSync(work), [], "nothing appeared in the working directory");
  // No write path is left: evolve imports only these, and no other way to write or run anything.
  const source = readFileSync(join(REPO_ROOT, "youspeak-evolve.mjs"), "utf-8");
  const imports = [...source.matchAll(/^import\s+([\s\S]*?)\s+from\s+"([^"]+)";/gm)]
    .map(match => [match[2], match[1].replace(/[{}\s]/g, "").split(",").filter(Boolean).sort()]);
  assert.deepEqual(imports, [
    ["fs", ["existsSync", "readFileSync"]],
    ["path", ["dirname", "join"]],
    ["url", ["fileURLToPath"]],
    ["./youspeak-kernel.mjs", ["TREND_TOLERANCE", "defaultHistoryPath", "readHistory", "readLegacyHistory", "rowDay", "trendDirection"]],
  ]);
  assert.doesNotMatch(source, /child_process|\bimport\(|\brequire\(|\bprocess\.binding|writeSync|openSync|appendFile|writeFile|createWriteStream|\brename|unlink|\brmSync|\brm\(|mkdir|copyFile|truncate|chmod|symlink|--apply"\)/,
    "no write, spawn or dynamic import");

  // A corrupt store is reported by the ouroboros and left as it is.
  writeFileSync(historyPath, `${readFileSync(historyPath, "utf-8")}{"broken`);
  const corrupt = fingerprint([historyPath]);
  const reported = run("reflect");
  assert.match(reported.stdout, /could not be read and were skipped/);
  assert.deepEqual(fingerprint([historyPath]), corrupt);
});

test("the audit stays read only", t => {
  const dir = scratch(t, "ys-audit-");
  const state = join(dir, "state.json");
  writeFileSync(state, JSON.stringify({ task: "t", messages: [{ role: "assistant", content: [{ type: "text", text: "Sure! Let me check that." }] }] }));
  const before = fingerprint([state, ...TRACKED]);
  const audit = spawnSync(process.execPath, [join(REPO_ROOT, "youspeak-audit.mjs"), "--state", state, "--log", join(dir, "none.log")], {
    cwd: dir,
    encoding: "utf-8",
  });
  assert.equal(audit.status, 0, audit.stderr);
  assert.match(audit.stdout, /YOUSPEAK Audit Report/);
  assert.deepEqual(fingerprint([state, ...TRACKED]), before);
  assert.deepEqual(readdirSync(dir), ["state.json"]);
  assert.ok(existsSync(LEGACY_HISTORY_PATH), "the legacy file is still there, as a historical record");
});
