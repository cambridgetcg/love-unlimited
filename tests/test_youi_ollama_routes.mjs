// Ollama routes (youi-web/ollama-routes.mjs): a model the local Ollama serves
// from Ollama Cloud counts as Ollama Cloud, gated by models:ollama-cloud and
// labelled REMOTE, though it goes through localhost; only exact names count as
// installed; names are translated between the daemon's ":cloud"/"-cloud" form
// and the direct API's; think is sent only where the model lists the value;
// and a token count native Ollama leaves out is null.
//
// Every Ollama here is a fake on 127.0.0.1: no daemon, no ollama.com, no key.

import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  OLLAMA_LOCAL_PICKS, isOllamaCloudName, ollamaDaemonName, ollamaDirectName, ollamaRoute, ollamaThinkValue, readOllamaTags,
} from "../youi-web/ollama-routes.mjs";
import { createMindsRegistry } from "../youi-web/minds.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;

// ── Names and routes ────────────────────────────────────────────────

test("daemon and direct names translate both ways", () => {
  assert.equal(isOllamaCloudName("glm-5.3:cloud"), true);
  assert.equal(isOllamaCloudName("gpt-oss:120b-cloud"), true);
  assert.equal(isOllamaCloudName("gemma4:31b"), false);
  assert.equal(ollamaDirectName("glm-5.3:cloud"), "glm-5.3");
  assert.equal(ollamaDirectName("gpt-oss:120b-cloud"), "gpt-oss:120b");
  assert.equal(ollamaDirectName("deepseek-v4-pro:0813-cloud"), "deepseek-v4-pro:0813");
  assert.equal(ollamaDirectName("gemma4:12b"), "gemma4:12b");
  assert.equal(ollamaDaemonName("glm-5.3"), "glm-5.3:cloud");
  assert.equal(ollamaDaemonName("gemma4:31b"), "gemma4:31b-cloud", "a tagged name takes -cloud, never a second tag");
  assert.equal(ollamaDaemonName("kimi-k2.7-code:cloud"), "kimi-k2.7-code:cloud");
});

test("/api/tags remote stubs are cloud, and only an exact name is installed", () => {
  const tags = readOllamaTags({
    models: [
      { name: "gemma4:12b" },
      { name: "kimi-k2.7-code:cloud", remote_host: "https://ollama.com:443", remote_model: "kimi-k2.7-code" },
      { name: "plain-name", remote_model: "glm-5.3-flash" },
      { model: "qwen3.5:4b" },
      { name: 7 },
    ],
  });
  assert.deepEqual(tags.models, ["gemma4:12b", "kimi-k2.7-code:cloud", "plain-name", "qwen3.5:4b"]);
  assert.deepEqual(tags.remote, ["kimi-k2.7-code:cloud", "plain-name"]);

  const running = { running: true, ...tags };
  assert.deepEqual(ollamaRoute("gemma4:12b", running), { provider: "ollama_local", via: "local", model: "gemma4:12b" });
  assert.deepEqual(ollamaRoute("kimi-k2.7-code:cloud", running), { provider: "ollama_cloud", via: "daemon", model: "kimi-k2.7-code:cloud" });
  assert.deepEqual(ollamaRoute("plain-name", running), { provider: "ollama_cloud", via: "daemon", model: "plain-name" },
    "remote_model alone makes a listed model cloud");
  // gemma4:12b is pulled; gemma4:31b is not local on that account.
  assert.deepEqual(ollamaRoute("gemma4:31b", running), { provider: "ollama_cloud", via: "daemon", model: "gemma4:31b-cloud" });
  assert.deepEqual(ollamaRoute("gemma4", running).provider, "ollama_cloud");
  // With a key, a direct-form name goes direct; a daemon-form name still goes through the running daemon.
  const keyed = { ...running, keyConfigured: true };
  assert.deepEqual(ollamaRoute("gemma4:31b", keyed), { provider: "ollama_cloud", via: "direct", model: "gemma4:31b" });
  assert.deepEqual(ollamaRoute("glm-5.3:cloud", keyed).via, "daemon");
  // No daemon: the direct key route, with the suffix stripped.
  assert.deepEqual(ollamaRoute("glm-5.3:cloud", { keyConfigured: true }), { provider: "ollama_cloud", via: "direct", model: "glm-5.3" });
  assert.deepEqual(ollamaRoute("gpt-oss:20b-cloud"), { provider: "ollama_cloud", via: "direct", model: "gpt-oss:20b" });
  // A local pick stays local whether it is pulled or not, key or no key.
  for (const pick of OLLAMA_LOCAL_PICKS) {
    for (const local of [{}, running, keyed, { keyConfigured: true }]) {
      assert.deepEqual(ollamaRoute(pick, local), { provider: "ollama_local", via: "local", model: pick });
    }
  }
});

test("think is sent only when the model's /api/show lists the value", () => {
  assert.equal(ollamaThinkValue("low", ["low", "medium", "high"]), "low");
  assert.equal(ollamaThinkValue("medium", ["low", "high", "max"]), undefined);
  assert.equal(ollamaThinkValue("low", [false, true]), undefined);
  assert.equal(ollamaThinkValue("none", [false, true]), false);
  assert.equal(ollamaThinkValue("none", ["low", "high"]), undefined);
  assert.equal(ollamaThinkValue("low", null), undefined, "no metadata: nothing is sent");
  assert.equal(ollamaThinkValue(null, ["low"]), undefined);
});

// ── Fakes ───────────────────────────────────────────────────────────

async function listen(t, handler) {
  const requests = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      let body = null;
      try { body = JSON.parse(Buffer.concat(chunks).toString("utf-8")); } catch {}
      requests.push({ method: req.method, path: req.url, headers: req.headers, body });
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

async function closedUrl() {
  const server = http.createServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  await new Promise(resolve => server.close(resolve));
  return `http://127.0.0.1:${port}`;
}

const THINKING = {
  "gemma4:12b": { values: ["low", "medium", "high"], default: "medium" },
  "kimi-k2.7-code:cloud": { values: [false, true], default: true },
};

// A signed-in local Ollama: gemma4:12b pulled, kimi-k2.7-code:cloud a remote
// stub. /api/show states thinking values; /api/chat answers, stating token
// counts except for the stub, which leaves them out.
function fakeDaemon(t) {
  return listen(t, (req, res, body) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    if (req.method === "POST" && req.url === "/api/show") {
      return res.end(JSON.stringify(THINKING[body?.model] ? { thinking: THINKING[body.model] } : {}));
    }
    if (req.method === "POST" && req.url === "/api/chat") {
      const counts = body?.model === "kimi-k2.7-code:cloud" ? {} : { prompt_eval_count: 1000, eval_count: 200, prompt_eval_cached_count: 600 };
      return res.end(JSON.stringify({
        model: body?.model, message: { role: "assistant", content: `answered by ${body?.model}` },
        done: true, done_reason: "stop", ...counts,
      }));
    }
    res.end(JSON.stringify({
      models: [
        { name: "gemma4:12b" },
        { name: "kimi-k2.7-code:cloud", remote_host: "https://ollama.com:443", remote_model: "kimi-k2.7-code" },
      ],
    }));
  });
}

// ── Registry ────────────────────────────────────────────────────────

test("the minds registry lists a remote stub as an Ollama Cloud mind, refused without the grant", async t => {
  const daemon = await fakeDaemon(t);
  const minds = createMindsRegistry({
    cloudModels: ["glm-5.3"],
    localBaseUrl: daemon.baseUrl,
    vllmBaseUrl: await closedUrl(),
    computeRoute: (provider, { throughDaemon = false } = {}) => ({ route: `LABEL:${provider}`, destination: throughDaemon ? "daemon" : "direct" }),
  });
  const byId = listing => Object.fromEntries(listing.minds.map(mind => [mind.id, mind]));

  const safe = byId(await minds.list({ capabilities: new Set(["models:use"]) }));
  assert.equal(safe["ollama:gemma4:12b"].state, "reachable");
  assert.equal(safe["ollama:kimi-k2.7-code:cloud"], undefined, "a remote stub is not a local mind");
  assert.equal(safe["ollama_cloud:glm-5.3"].state, "not_granted");
  assert.equal(safe["ollama_cloud:kimi-k2.7-code:cloud"], undefined, "nothing beyond the static list is read without the grant");

  const granted = byId(await minds.list({ capabilities: new Set(["models:use", "models:ollama-cloud"]) }));
  const stub = granted["ollama_cloud:kimi-k2.7-code:cloud"];
  assert.equal(stub.state, "unchecked");
  assert.equal(stub.capability, "models:ollama-cloud");
  assert.equal(stub.privacy, "LABEL:ollama_cloud");
  assert.equal(stub.destination, "daemon");
  // No key, but a running daemon: the static cloud mind is reachable through it.
  assert.equal(granted["ollama_cloud:glm-5.3"].state, "unchecked");
  assert.equal(granted["ollama_cloud:glm-5.3"].destination, "daemon");
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

// `extraEnv` values of null are removed from the child's environment.
async function startServer(t, extraEnv = {}) {
  const scratch = mkdtempSync(join(tmpdir(), "youi-ollama-routes-"));
  t.after(() => rmSync(scratch, { recursive: true, force: true }));
  const env = {
    ...process.env,
    PORT: "0",
    YOUI_TEST: "1",
    TRUTH_DETECTOR_ENABLED: "0",
    AUTONOMOUS: "0",
    KINGDOM_AGENT: "alpha",
    YOUI_MODEL: "",
    OLLAMA_API_KEY: "",
    YOUI_CLAUDE_BIN: "",
    YOUI_CLAUDE_CWD: scratch,
    LOVE_HOME: REPO_ROOT,
    YOUI_WORKDIR: REPO_ROOT,
    YOUSPEAK_TEST_HISTORY_PATH: join(scratch, "history.jsonl"),
    ...extraEnv,
  };
  for (const [key, value] of Object.entries(env)) if (value === null) delete env[key];
  const child = spawn(process.execPath, ["youi-web/server.mjs"], { cwd: REPO_ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
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
  t.after(() => { if (!child.killed) child.kill("SIGTERM"); });
  return port;
}

async function openSession(port, label) {
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
    body: { label, agent: "alpha" },
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

const settings = (session, body) => request(session.port, { path: "/api/settings", method: "POST", headers: session.unsafe, body });

async function chat(session, message, { raw = false } = {}) {
  const response = await fetch(`${session.origin}/api/chat`, {
    method: "POST",
    headers: { ...session.unsafe, "Content-Type": "application/json" },
    body: JSON.stringify({ message, raw }),
  });
  assert.equal(response.status, 200);
  return (await response.text()).split("\n\n").filter(Boolean).map(chunk => ({
    event: chunk.match(/^event: (.+)$/m)?.[1],
    data: JSON.parse(chunk.match(/^data: (.+)$/m)?.[1] || "null"),
  }));
}

const chats = daemon => daemon.requests.filter(r => r.method === "POST" && r.path === "/api/chat");

test("safe profile: a cloud model through the local Ollama is blocked and nothing is sent", async t => {
  const daemon = await fakeDaemon(t);
  const cloud = await listen(t, (_req, res) => { res.writeHead(500); res.end("{}"); });
  const port = await startServer(t, {
    OLLAMA_LOCAL_BASE_URL: daemon.baseUrl,
    OLLAMA_CLOUD_BASE_URL: cloud.baseUrl,
    YOUI_CAPABILITIES: null,
    YOUI_CAPABILITY_PROFILE: "safe",
  });
  const session = await openSession(port, "safe-stub");

  // The stub is listed by the local Ollama, so it is a valid model, but a remote one.
  const picked = await settings(session, { model: "kimi-k2.7-code:cloud" });
  assert.equal(picked.status, 200, picked.text);
  assert.equal(picked.json.privacy.direct.route, "BLOCKED:ollama-cloud-capability-required");
  const events = await chat(session, "this must stay on the device", { raw: true });
  assert.match(JSON.stringify(events.find(e => e.event === "error")), /cloud routing is disabled/i);

  // gemma4:12b is pulled; gemma4:31b is not local just because it shares a base name.
  assert.equal((await settings(session, { model: "gemma4:31b" })).json.privacy.direct.route, "BLOCKED:ollama-cloud-capability-required");
  await chat(session, "nor this", { raw: true });
  assert.equal(chats(daemon).length, 0, "no chat reached the daemon, which would forward it");
  assert.equal(cloud.requests.length, 0);

  const local = await settings(session, { model: "gemma4:12b" });
  assert.equal(local.json.privacy.direct.route, "LOCAL_MODEL:ollama");
  const status = await request(port, { path: "/api/status", headers: session.read });
  assert.ok(!status.json.capabilities.includes("models:ollama-cloud"), "safe stays without the cloud grant");
});

test("developer profile: cloud through the local Ollama is REMOTE, estimated, null where unreported; local gets think and num_ctx", async t => {
  const daemon = await fakeDaemon(t);
  const cloud = await listen(t, (_req, res) => { res.writeHead(500); res.end("{}"); });
  const port = await startServer(t, {
    OLLAMA_LOCAL_BASE_URL: daemon.baseUrl,
    OLLAMA_CLOUD_BASE_URL: cloud.baseUrl,
    YOUI_CAPABILITIES: null,
    YOUI_CAPABILITY_PROFILE: "developer",
  });
  const session = await openSession(port, "developer-stub");
  const status = await request(port, { path: "/api/status", headers: session.read });
  assert.ok(status.json.capabilities.includes("models:ollama-cloud"), "developer holds the cloud grant");

  // 1. The remote stub: REMOTE, through localhost, no key; its answer states no counts.
  const stub = await settings(session, { model: "kimi-k2.7-code:cloud" });
  assert.equal(stub.json.privacy.direct.route, "REMOTE_MODEL:ollama-cloud");
  assert.match(stub.json.privacy.direct.destination, /^Ollama Cloud, forwarded by the Ollama at http:\/\/127\.0\.0\.1:\d+$/);
  let events = await chat(session, "first");
  let sent = chats(daemon).at(-1);
  assert.equal(sent.body.model, "kimi-k2.7-code:cloud");
  assert.equal(sent.headers.authorization, undefined, "the daemon signs; YOUI sends no key");
  assert.equal(sent.body.think, undefined, "the stub lists [false, true]: \"low\" is not sent");
  assert.equal(sent.body.options.num_ctx, undefined, "num_ctx is for local models");
  assert.equal(events.find(e => e.event === "usage").data.provider, "ollama_cloud");
  let report = (await request(port, { path: "/api/youspeak", headers: session.read })).json;
  assert.equal(report.usage.inputTokens, null, "native Ollama stated no prompt_eval_count: not a reported 0");
  assert.equal(report.usage.outputTokens, null);
  assert.equal(report.usage.costUsd, null, "no cost estimate from no counts");

  // 2. A direct-form name, not pulled, no key: through the daemon as its -cloud name, priced as an estimate.
  assert.equal((await settings(session, { model: "gemma4:31b" })).json.privacy.direct.route, "REMOTE_MODEL:ollama-cloud");
  events = await chat(session, "second");
  assert.equal(chats(daemon).at(-1).body.model, "gemma4:31b-cloud");
  report = (await request(port, { path: "/api/youspeak", headers: session.read })).json;
  assert.equal(report.usage.inputTokens, 400, "the uncached part: 1000 less the 600 Ollama said it cached");
  assert.equal(report.usage.cacheReadTokens, 600);
  assert.equal(report.usage.outputTokens, 200);
  // gemma4:31b lists $0.14 in, $0.05 cached, $0.40 out per 1M: 400 uncached, 600 cached, 200 out.
  const listed = (400 * 0.14 + 600 * 0.05 + 200 * 0.40) / 1_000_000;
  assert.ok(Math.abs(report.usage.costUsd - listed) < 1e-12, `${report.usage.costUsd} is the list-price estimate ${listed}`);
  assert.equal(report.usage.evidence.costUsd, "estimated");
  const usage = (await request(port, { path: "/api/usage", headers: session.read })).json;
  assert.equal(usage.activeProvider, "ollama_cloud");
  assert.equal(usage.ollamaCloud.turns, 2);
  assert.equal(usage.ollamaCloud.inputTokens, 1000);
  assert.ok(usage.ollamaCloud.byModel["gemma4:31b"].cost > 0);
  assert.equal(usage.ollama.cost, usage.ollamaCloud.cost, "the page's Ollama total includes the cloud bucket");
  assert.equal(events.find(e => e.event === "usage").data.ollamaCost, usage.ollamaCloud.cost);
  assert.equal(usage.ollama.unpricedCalls, 0);

  // 2b. A cloud model with no listed price (beam): counted as unpriced, its cost null, not a guess.
  assert.equal((await settings(session, { model: "beam" })).json.privacy.direct.route, "REMOTE_MODEL:ollama-cloud");
  events = await chat(session, "unpriced");
  assert.equal(chats(daemon).at(-1).body.model, "beam:cloud");
  report = (await request(port, { path: "/api/youspeak", headers: session.read })).json;
  assert.ok(Math.abs(report.usage.costUsd - listed) < 1e-12, "the session's estimate gains nothing from an unpriced turn");
  const afterBeam = (await request(port, { path: "/api/usage", headers: session.read })).json;
  assert.equal(afterBeam.ollama.unpricedCalls, 1);
  assert.equal(afterBeam.ollama.cost, usage.ollama.cost, "an unpriced call adds nothing to the estimate");
  assert.equal(afterBeam.ollamaCloud.byModel.beam.unpricedCalls, 1);
  assert.equal(afterBeam.ollamaCloud.byModel.beam.cost, 0);
  assert.equal(events.find(e => e.event === "usage").data.ollamaUnpriced, 1);

  // 3. The pulled local model: think "low" (the session default, which it lists) and num_ctx.
  assert.equal((await settings(session, { model: "gemma4:12b" })).json.privacy.direct.route, "LOCAL_MODEL:ollama");
  await chat(session, "third");
  sent = chats(daemon).at(-1);
  assert.equal(sent.body.model, "gemma4:12b");
  assert.equal(sent.body.think, "low");
  assert.equal(sent.body.options.num_ctx, 32768);
  assert.equal(cloud.requests.length, 0, "ollama.com was never contacted");
});

test("with no local Ollama, a daemon name goes to the direct API stripped of its suffix", async t => {
  const cloud = await listen(t, (req, res, body) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    if (req.url === "/api/show") return res.end(JSON.stringify({ thinking: { values: ["low", "high"], default: "high" } }));
    res.end(JSON.stringify({
      model: body?.model,
      choices: [{ message: { role: "assistant", content: "direct" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    }));
  });
  const port = await startServer(t, {
    OLLAMA_LOCAL_BASE_URL: await closedUrl(),
    OLLAMA_CLOUD_BASE_URL: cloud.baseUrl,
    OLLAMA_API_KEY: "test-only-key",
    YOUI_MODEL: "glm-5.3",
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,models:ollama-cloud,youspeak:read",
  });
  const session = await openSession(port, "direct-key");
  const status = await request(port, { path: "/api/status", headers: session.read });
  assert.equal(status.json.privacy.direct.route, "REMOTE_MODEL:ollama-cloud");
  assert.equal(status.json.privacy.direct.destination, cloud.baseUrl);
  const answer = await request(port, {
    path: "/api/ollama/chat", method: "POST", headers: session.unsafe,
    body: { model: "glm-5.3:cloud", prompt: "hello", reasoning_effort: "low" },
  });
  assert.equal(answer.status, 200, answer.text);
  assert.equal(answer.json.ok, true);
  assert.equal(answer.json._provider, "ollama_cloud");
  const sent = cloud.requests.find(r => r.path === "/v1/chat/completions");
  assert.equal(sent.body.model, "glm-5.3", "the direct API gets the name without :cloud");
  assert.equal(sent.body.reasoning_effort, "low", "the model lists \"low\"");
  assert.equal(sent.headers.authorization, "Bearer test-only-key");
  assert.equal(cloud.requests.find(r => r.path === "/api/show").body.model, "glm-5.3");
});
