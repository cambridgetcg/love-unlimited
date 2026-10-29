// YOUI minds, slice M1 (docs/YOUI-MINDS-DESIGN.md §3.1–3.2): one answering
// path, and a registry that tells the truth about which minds answer.
//
// Every mind here is a fake on 127.0.0.1. Claude stays unchecked (test mode
// never reads the Keychain) and Ollama Cloud is never contacted.

import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { MIND_STATES, createMindsRegistry, offeredMinds } from "../youi-web/minds.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const VLLM_MODEL = "Qwen/Qwen2.5-72B-Instruct-AWQ";

// ── Fakes ───────────────────────────────────────────────────────────

async function listen(t, handler) {
  const requests = [];
  const server = http.createServer((req, res) => {
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
    server.closeAllConnections();
    server.close();
  });
  return { baseUrl: `http://127.0.0.1:${server.address().port}`, requests };
}

function count(fake, method, path) {
  return fake.requests.filter(r => r.method === method && r.path === path).length;
}

function fakeOllama(t, models) {
  return listen(t, (_req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ models: models.map(name => ({ name })) }));
  });
}

// GET /v1/models lists `models`; POST /v1/chat/completions streams `reply`.
function fakeVllm(t, models, reply = "answered through the agent loop") {
  return listen(t, (req, res, body) => {
    if (req.method === "GET" && req.url === "/v1/models") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ object: "list", data: models.map(id => ({ id, object: "model" })) }));
    }
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(`data: ${JSON.stringify({ model: body?.model, choices: [{ delta: { content: reply }, finish_reason: null }] })}\n\n`);
    res.write(`data: ${JSON.stringify({
      choices: [{ delta: {}, finish_reason: "stop" }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    })}\n\n`);
    res.end("data: [DONE]\n\n");
  });
}

// A port that refuses connections.
async function closedUrl() {
  const server = http.createServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  await new Promise(resolve => server.close(resolve));
  return `http://127.0.0.1:${port}`;
}

// Stands in for server.mjs's privacy labels in the unit tests; the route
// tests below read the server's own.
const route = provider => ({ route: `LABEL:${provider}`, destination: `dest:${provider}` });

function registry(options) {
  return createMindsRegistry({
    claudeModels: ["claude-opus-5-5", "claude-sonnet-4-6"],
    vllmModels: [VLLM_MODEL],
    cloudModels: ["glm-5.1", "deepseek-v3.2"],
    isVllmModel: model => /^(Qwen\/|kingdom-truth)/i.test(model),
    computeRoute: route,
    ...options,
  });
}

const byId = listing => Object.fromEntries(listing.minds.map(mind => [mind.id, mind]));

// ── Registry ────────────────────────────────────────────────────────

test("the minds registry reports each state honestly and offers only granted, answering minds", async t => {
  const ollama = await fakeOllama(t, ["llama3.2:3b"]);
  const vllm = await fakeVllm(t, [VLLM_MODEL, "unrouted-model"]);
  const minds = registry({ localBaseUrl: ollama.baseUrl, vllmBaseUrl: vllm.baseUrl });

  const used = byId(await minds.list({ capabilities: new Set(["models:use"]) }));
  assert.equal(used[`vllm:${VLLM_MODEL}`].state, "reachable");
  assert.equal(used[`vllm:${VLLM_MODEL}`].privacy, "LABEL:vllm");
  assert.equal(used[`vllm:${VLLM_MODEL}`].capability, "models:use");
  assert.equal(used["vllm:unrouted-model"], undefined, "a served id YOUI would not route to vLLM is not a mind");
  assert.equal(used["ollama:llama3.2:3b"].state, "reachable");
  assert.equal(used["anthropic:claude-opus-5-5"].state, "unchecked");
  assert.match(used["anthropic:claude-opus-5-5"].note, /Keychain/);
  assert.equal(used["ollama_cloud:glm-5.1"].state, "not_granted");
  assert.equal(used["ollama_cloud:glm-5.1"].capability, "models:ollama-cloud");
  for (const mind of Object.values(used)) {
    assert.ok(MIND_STATES.includes(mind.state));
    assert.equal(mind.offered, mind.state === "reachable" || mind.state === "unchecked");
  }

  const cloudGranted = new Set(["models:use", "models:ollama-cloud"]);
  assert.equal(byId(await minds.list({ capabilities: cloudGranted }))["ollama_cloud:glm-5.1"].state, "not_configured");
  const keyed = registry({ localBaseUrl: ollama.baseUrl, vllmBaseUrl: vllm.baseUrl, cloudKeyConfigured: () => true });
  const cloud = byId(await keyed.list({ capabilities: cloudGranted }))["ollama_cloud:glm-5.1"];
  assert.equal(cloud.state, "unchecked");
  assert.equal(cloud.offered, true);

  // Without models:use nothing is offered and nothing is probed.
  const probesBefore = ollama.requests.length + vllm.requests.length;
  const none = await registry({ localBaseUrl: ollama.baseUrl, vllmBaseUrl: vllm.baseUrl })
    .list({ capabilities: new Set(["chat"]) });
  assert.ok(none.minds.every(mind => mind.state === "not_granted"));
  assert.deepEqual(offeredMinds(none), []);
  assert.equal(none.checked_at, null);
  assert.equal(ollama.requests.length + vllm.requests.length, probesBefore);
  assert.equal(vllm.requests.filter(r => r.method === "POST").length, 0, "a probe never calls a model");

  const down = await registry({ localBaseUrl: await closedUrl(), vllmBaseUrl: await closedUrl() })
    .list({ capabilities: new Set(["models:use"]) });
  const downById = byId(down);
  assert.equal(downById.ollama.state, "unreachable");
  assert.match(downById.ollama.note, /GET \/api\/tags/);
  assert.equal(downById[`vllm:${VLLM_MODEL}`].state, "unreachable");
  assert.equal(downById[`vllm:${VLLM_MODEL}`].offered, false);
  assert.ok(offeredMinds(down).every(mind => mind.provider === "anthropic"));

  const empty = await fakeOllama(t, []);
  const otherVllm = await fakeVllm(t, ["Qwen/Qwen3-8B"]);
  const sparse = byId(await registry({ localBaseUrl: empty.baseUrl, vllmBaseUrl: otherVllm.baseUrl })
    .list({ capabilities: new Set(["models:use"]) }));
  assert.equal(sparse.ollama.state, "not_configured");
  assert.equal(sparse[`vllm:${VLLM_MODEL}`].state, "unreachable");
  assert.match(sparse[`vllm:${VLLM_MODEL}`].note, /does not serve/);
  assert.equal(sparse["vllm:Qwen/Qwen3-8B"].state, "reachable");

  // A "local" endpoint off this device is a network route: it needs the cloud grant and is not probed without it.
  const offHost = await fakeOllama(t, ["llama3.2:3b"]);
  const remoteLocal = byId(await registry({
    localBaseUrl: offHost.baseUrl, vllmBaseUrl: vllm.baseUrl, localEndpointIsLoopback: false,
  }).list({ capabilities: new Set(["models:use"]) }));
  assert.equal(remoteLocal.ollama.state, "not_granted");
  assert.equal(remoteLocal.ollama.capability, "models:ollama-cloud");
  assert.equal(offHost.requests.length, 0);
});

test("Claude is reachable only after a real answer, and never in test mode", async () => {
  const unreachable = await closedUrl();
  const live = registry({ localBaseUrl: unreachable, vllmBaseUrl: unreachable });
  live.recordAnswer("anthropic", "claude-opus-5-5");
  const listed = byId(await live.list({ capabilities: new Set(["models:use"]) }));
  assert.equal(listed["anthropic:claude-opus-5-5"].state, "reachable");
  assert.equal(listed["anthropic:claude-sonnet-4-6"].state, "unchecked");

  const testing = registry({ localBaseUrl: unreachable, vllmBaseUrl: unreachable, testMode: true });
  testing.recordAnswer("anthropic", "claude-opus-5-5");
  const fenced = byId(await testing.list({ capabilities: new Set(["models:use"]) }));
  assert.equal(fenced["anthropic:claude-opus-5-5"].state, "unchecked");
  assert.match(fenced["anthropic:claude-opus-5-5"].note, /test mode/);
});

test("probes are cached for the TTL, shared while in flight, and time out quickly", async t => {
  const ollama = await fakeOllama(t, ["llama3.2:3b"]);
  const vllm = await fakeVllm(t, [VLLM_MODEL]);
  let clock = 1_000_000;
  const minds = registry({
    localBaseUrl: ollama.baseUrl, vllmBaseUrl: vllm.baseUrl, cacheTtlMs: 60_000, now: () => clock,
  });
  const capabilities = new Set(["models:use"]);

  await Promise.all([minds.list({ capabilities }), minds.list({ capabilities })]);
  assert.equal(count(ollama, "GET", "/api/tags"), 1, "concurrent reads share one probe");
  assert.equal(count(vllm, "GET", "/v1/models"), 1);

  clock += 59_000;
  const cached = await minds.list({ capabilities });
  assert.equal(count(ollama, "GET", "/api/tags"), 1, "a read within the TTL does not re-probe");
  assert.equal(count(vllm, "GET", "/v1/models"), 1);
  assert.equal(cached.checked_at, new Date(1_000_000).toISOString());

  clock += 2_000;
  await minds.list({ capabilities });
  assert.equal(count(ollama, "GET", "/api/tags"), 2, "an expired probe runs again");
  assert.equal(count(vllm, "GET", "/v1/models"), 2);

  const silent = await listen(t, () => {}); // accepts, never answers
  const started = Date.now();
  const slow = byId(await registry({ localBaseUrl: silent.baseUrl, vllmBaseUrl: silent.baseUrl, probeTimeoutMs: 150 })
    .list({ capabilities }));
  assert.ok(Date.now() - started < 1_500, "a silent endpoint must not hold the list");
  assert.equal(slow.ollama.state, "unreachable");
  assert.match(slow.ollama.note, /no answer within 150 ms/);
});

// ── Server ──────────────────────────────────────────────────────────

function request(port, { path = "/", method = "GET", headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const requestHeaders = { ...headers };
    if (payload) {
      requestHeaders["Content-Type"] = "application/json";
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

function pageId(label) {
  return `page-${label}-0000000000000001`;
}

function sessionHeaders(browser, credential, { origin, unsafe = false } = {}) {
  const headers = {
    Cookie: browser.header,
    "X-YOUI-Session": credential.id,
    "X-YOUI-Session-Token": credential.token,
    "X-YOUI-Page": credential.page,
  };
  if (unsafe) {
    headers.Origin = origin;
    headers["X-YOUI-CSRF"] = browser.csrf;
  }
  return headers;
}

async function startServer(t, extraEnv = {}) {
  const child = spawn(process.execPath, ["youi-web/server.mjs"], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      PORT: "0",
      YOUI_TEST: "1",
      TRUTH_DETECTOR_ENABLED: "0",
      AUTONOMOUS: "0",
      OLLAMA_API_KEY: "",
      ...extraEnv,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
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
  t.after(() => {
    if (!child.killed) child.kill("SIGTERM");
  });
  return port;
}

async function openSession(port, label, body = {}) {
  const origin = `http://127.0.0.1:${port}`;
  const browser = cookiesFrom(await request(port));
  const page = pageId(label);
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: { Cookie: browser.header, Origin: origin, "X-YOUI-CSRF": browser.csrf, "X-YOUI-Page": page },
    body: { label, agent: "alpha", ...body },
  });
  assert.equal(created.status, 201);
  return { origin, browser, credential: { id: created.json.session.id, token: created.json.sessionToken, page } };
}

test("GET /api/minds needs a browser session, a YOUI session and status:read", async t => {
  const ollama = await fakeOllama(t, ["llama3.2:3b"]);
  const vllm = await fakeVllm(t, [VLLM_MODEL]);
  const env = {
    KINGDOM_AGENT: "alpha",
    OLLAMA_LOCAL_BASE_URL: ollama.baseUrl,
    OLLAMA_VLLM_BASE_URL: vllm.baseUrl,
  };

  const deniedPort = await startServer(t, { ...env, YOUI_CAPABILITIES: "chat,sessions:manage,models:use" });
  assert.equal((await request(deniedPort, { path: "/api/minds" })).status, 401);
  const denied = await openSession(deniedPort, "minds-denied");
  const noSession = await request(deniedPort, { path: "/api/minds", headers: { Cookie: denied.browser.header } });
  assert.equal(noSession.status, 404);
  assert.equal(noSession.json.code, "session_not_found");
  const noCapability = await request(deniedPort, {
    path: "/api/minds", headers: sessionHeaders(denied.browser, denied.credential),
  });
  assert.equal(noCapability.status, 403);
  assert.equal(noCapability.json.capability, "status:read");
  assert.equal(vllm.requests.length + ollama.requests.length, 0, "a refused read probes nothing");

  const port = await startServer(t, { ...env, YOUI_CAPABILITIES: "chat,status:read,sessions:manage,models:use" });
  const { origin, browser, credential } = await openSession(port, "minds-read");
  const posted = await request(port, {
    path: "/api/minds", method: "POST", headers: sessionHeaders(browser, credential, { origin, unsafe: true }),
  });
  assert.equal(posted.status, 405);
  assert.equal(posted.headers.allow, "GET");

  const read = await request(port, { path: "/api/minds", headers: sessionHeaders(browser, credential) });
  assert.equal(read.status, 200);
  assert.ok(!Number.isNaN(Date.parse(read.json.checked_at)));
  const minds = byId(read.json);
  // The labels are the server's own privacy labels.
  assert.equal(minds[`vllm:${VLLM_MODEL}`].state, "reachable");
  assert.equal(minds[`vllm:${VLLM_MODEL}`].privacy, "REMOTE_MODEL:vllm");
  assert.equal(minds[`vllm:${VLLM_MODEL}`].destination, vllm.baseUrl);
  assert.equal(minds["ollama:llama3.2:3b"].privacy, "LOCAL_MODEL:ollama");
  assert.equal(minds["anthropic:claude-opus-5-5"].state, "unchecked");
  assert.equal(minds["anthropic:claude-opus-5-5"].privacy, "REMOTE_MODEL:anthropic");
  assert.equal(minds["ollama_cloud:glm-5.1"].state, "not_granted");
  assert.equal(minds["ollama_cloud:glm-5.1"].privacy, "REMOTE_MODEL:ollama-cloud");

  await request(port, { path: "/api/minds", headers: sessionHeaders(browser, credential) });
  assert.equal(count(vllm, "GET", "/v1/models"), 1, "the server caches probes between reads");
  assert.equal(count(ollama, "GET", "/api/tags"), 1);
  assert.equal(vllm.requests.filter(r => r.method === "POST").length, 0);
});

test("orchestrate mode answers through the agent loop, never the Python orchestrator", async t => {
  const vllm = await fakeVllm(t, [VLLM_MODEL]);
  // A python3 that records its arguments: the chat turn must never start adaptive.orchestrator.
  const bin = mkdtempSync(join(tmpdir(), "youi-minds-python-"));
  t.after(() => rmSync(bin, { recursive: true, force: true }));
  const pythonLog = join(bin, "python3.log");
  writeFileSync(join(bin, "python3"), `#!/bin/sh\necho "$*" >> "${pythonLog}"\nexit 1\n`);
  chmodSync(join(bin, "python3"), 0o755);

  const port = await startServer(t, {
    KINGDOM_AGENT: "alpha",
    OLLAMA_LOCAL_BASE_URL: await closedUrl(),
    OLLAMA_VLLM_BASE_URL: vllm.baseUrl,
    PATH: `${bin}:${process.env.PATH}`,
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,orchestrator:run",
  });
  const { origin, browser, credential } = await openSession(port, "orchestrate", { model: VLLM_MODEL });
  const unsafe = sessionHeaders(browser, credential, { origin, unsafe: true });
  const mode = await request(port, { path: "/api/settings", method: "POST", headers: unsafe, body: { chatMode: "orchestrate" } });
  assert.equal(mode.status, 200);
  assert.equal(mode.json.chatMode, "orchestrate");
  assert.equal(mode.json.privacy.direct.route, "REMOTE_MODEL:vllm");
  assert.equal(mode.json.privacy.orchestration.chatTurns, false);

  const chat = async message => {
    const response = await fetch(`${origin}/api/chat`, {
      method: "POST",
      headers: { ...unsafe, "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    assert.equal(response.status, 200);
    return response.text();
  };
  const chatRequests = () => vllm.requests.filter(r => r.method === "POST" && r.path === "/v1/chat/completions");

  const stream = await chat("hello");
  assert.doesNotMatch(stream, /event: orchestrate_/);
  assert.match(stream, /event: minds/);
  assert.match(stream, /answered through the agent loop/);
  assert.match(stream, /event: done/);

  assert.equal(chatRequests().length, 1, "the fake vLLM answered the turn");
  const outbound = chatRequests()[0].body;
  assert.equal(outbound.model, VLLM_MODEL);
  const system = outbound.messages[0];
  assert.equal(system.role, "system");
  assert.match(system.content, /# Minds \(orchestrate mode\)/);
  assert.match(system.content, /not callable yet/);
  assert.ok(system.content.includes(`vllm · REMOTE_MODEL:vllm · reachable: ${VLLM_MODEL} (you)`));
  assert.match(system.content, /anthropic · REMOTE_MODEL:anthropic · unchecked: claude-opus-5-5/);
  assert.doesNotMatch(system.content, /LOCAL_MODEL:ollama/, "an unreachable mind is not offered");
  assert.deepEqual(outbound.messages.at(-1), { role: "user", content: "hello" });
  assert.ok(outbound.tools.some(tool => tool.function.name === "ollama"), "the agent keeps its tools");

  // Direct mode adds no minds hint, and the same history carries on.
  const direct = await request(port, { path: "/api/settings", method: "POST", headers: unsafe, body: { chatMode: "direct" } });
  assert.equal(direct.status, 200);
  const directStream = await chat("and again");
  assert.doesNotMatch(directStream, /event: minds|event: orchestrate_/);
  const second = chatRequests()[1].body;
  assert.doesNotMatch(second.messages[0].content, /# Minds/);
  assert.deepEqual(second.messages.slice(1).map(m => m.role), ["user", "assistant", "user"]);

  const pythonCalls = () => (existsSync(pythonLog) ? readFileSync(pythonLog, "utf-8") : "");
  assert.doesNotMatch(pythonCalls(), /adaptive/, "no orchestrator subprocess was started");

  // The /api/orchestrate/* routes keep the Python layer, and the fake python3 sees it.
  const classify = await request(port, {
    path: "/api/orchestrate/classify", method: "POST", headers: unsafe, body: { task: "hello" },
  });
  assert.equal(classify.status, 500, "the fake python3 fails the classification");
  assert.match(pythonCalls(), /-m adaptive\.orchestrator --classify -p hello/);
});
