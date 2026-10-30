// YOUSPEAK rebuilt as an honest observer, slice Y1
// (docs/YOUSPEAK-OBSERVER-DESIGN.md §3; docs/YOUSPEAK.md): suggest only, one
// private row per session, the NaN and corrupt-reset bugs fixed, and a
// proposal-only ouroboros.
//
// Every store here is a temporary file named by YOUSPEAK_HISTORY_PATH or
// historyPath; the real ~/.local/share/youi is never touched, and the tracked
// youspeak-history.json and youspeak-config.json are checked unchanged. Claude
// is tests/fixtures/fake-claude.mjs and vLLM a local fake; no network, no Keychain.

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
  readHistory,
} from "../youspeak-kernel.mjs";

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

async function startServer(t, extraEnv = {}) {
  const temp = scratch(t, "ys-server-tmp-");
  const child = spawn(process.execPath, ["youi-web/server.mjs"], {
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

async function openSession(port, label, body = {}) {
  const origin = `http://127.0.0.1:${port}`;
  const browser = cookiesFrom(await request(port));
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
  return { origin, port, credential, read, unsafe: { ...read, Origin: origin, "X-YOUI-CSRF": browser.csrf } };
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
  ys.senseOutput("OK.");
  ys.senseOutput("收到。");
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

// ── YOUI Web ────────────────────────────────────────────────────────

test("a Claude turn that once cut effort leaves it as chosen and sends a suggestion", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const historyPath = join(scratch(t, "ys-web-effort-"), "history.jsonl");
  const { port } = await startServer(t, { YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd, YOUSPEAK_HISTORY_PATH: historyPath });
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

  // Two more turns: the effort sent to Claude Code is unchanged, the
  // suggestion is not repeated, and no turn suggests a cheaper model.
  const later = [...await chat(session, "again"), ...await chat(session, "and again")];
  const runs = fake.runs();
  assert.equal(runs.length, 3);
  const effortFlag = run => run.argv[run.argv.indexOf("--effort") + 1];
  assert.equal(effortFlag(runs[2]), effortFlag(runs[0]));
  assert.ok(!later.some(event => event.event === "youspeak_suggestion" && event.data.type === "effort"));
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
    YOUSPEAK_HISTORY_PATH: historyPath,
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
  assert.ok(signals.some(signal => signal.type === "effort"), "the effort signal fired");
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

test("a session's row is written once when it is cleared, switched, closed or idle, or the server stops; empty and failed sessions write none", async t => {
  guardTracked(t);
  const fake = fakeClaude(t);
  const historyPath = join(scratch(t, "ys-web-rows-"), "history.jsonl");
  const { port, child } = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin,
    YOUI_CLAUDE_CWD: fake.cwd,
    YOUSPEAK_HISTORY_PATH: historyPath,
    YOUI_TEST_YOUSPEAK_IDLE_MS: "1500",
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
  const failed = await openSession(port, "failed");
  const failedEvents = await chat(failed, "FAKE:error boom");
  assert.ok(failedEvents.some(event => event.event === "error"));

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

  const idle = await openSession(port, "idle");
  await chat(idle, "then silence");
  await until(() => rows().some(row => row.endReason === "idle"), { label: "the idle row" });

  await chat(cleared, "a second conversation");
  await stopChild(child);
  const all = rows();
  assert.deepEqual(all.map(row => row.endReason), ["clear", "switch", "closed", "idle", "shutdown"]);
  assert.equal(new Set(all.map(row => row.session)).size, all.length, "one row per session");
  assert.ok(all.every(row => row.textBlocks > 0), "the empty and the failed sessions wrote none");
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
  for (const command of ["transmute", "reflect", "distill", "history"]) assert.equal(run(command).status, 0);
  const integrate = run("integrate", "--apply");
  assert.equal(integrate.status, 2);
  assert.match(integrate.stdout, /proposal-only/);

  assert.deepEqual(fingerprint(watched), before, "sovereign.mjs, the tracked files and the store are unchanged");
  assert.deepEqual(readdirSync(work), [], "nothing appeared in the working directory");
  const source = readFileSync(join(REPO_ROOT, "youspeak-evolve.mjs"), "utf-8");
  assert.doesNotMatch(source, /writeFileSync|appendFileSync|renameSync|--apply"\)/, "no write path is left");

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
