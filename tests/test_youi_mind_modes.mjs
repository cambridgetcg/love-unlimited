// YOUI's mind modes (docs/YOUI-MINDS-DESIGN.md §7): SPLIT 分, HIVE 合 and
// 分分合合, carried as a per-turn hint, beside direct and orchestrate.
//
// Every mind and agent model here is a fake on 127.0.0.1 (as in
// tests/test_youi_minds.mjs), and every `claude` is
// tests/fixtures/fake-claude.mjs. These tests check what YOUI sends; what a
// live model does with a hint is not tested here (no live model is called).

import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { CHAT_MODES, MIND_MODES, isMindMode, mindModeTurn } from "../youi-web/mind-modes.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const FAKE_CLAUDE = join(REPO_ROOT, "tests/fixtures/fake-claude.mjs");
const VLLM_MODEL = "Qwen/Qwen2.5-72B-Instruct-AWQ";
const ALL_MODES = ["direct", "orchestrate", "split", "hive", "fenhe"];
const WITH_MINDS = "chat,status:read,sessions:manage,settings:write,models:use,orchestrator:run";

// ── Helpers ─────────────────────────────────────────────────────────

const testServers = new WeakMap();
async function stopServers(t) {
  await Promise.all((testServers.get(t) || []).map(child => new Promise(resolve => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve();
    const timer = setTimeout(() => { try { child.kill("SIGKILL"); } catch {} resolve(); }, 5_000);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
    child.kill("SIGTERM");
  })));
}

function scratch(t, prefix) {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  t.after(async () => {
    await stopServers(t);
    rmSync(dir, { recursive: true, force: true });
  });
  return dir;
}

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

function closedUrl() {
  return new Promise(resolve => {
    const server = http.createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(`http://127.0.0.1:${port}`));
    });
  });
}

// GET /v1/models lists `models`; each POST /v1/chat/completions streams the next
// scripted reply, { text } or { tool: { id, name, input } }.
function scriptedVllm(t, models, script) {
  let next = 0;
  return listen(t, (req, res, body) => {
    if (req.method === "GET" && req.url === "/v1/models") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ object: "list", data: models.map(id => ({ id, object: "model" })) }));
    }
    const step = script[next++] || { text: "nothing more scripted" };
    const delta = step.tool
      ? { tool_calls: [{ index: 0, id: step.tool.id, function: { name: step.tool.name, arguments: JSON.stringify(step.tool.input) } }] }
      : { content: step.text };
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(`data: ${JSON.stringify({ model: body?.model, choices: [{ delta, finish_reason: null }] })}\n\n`);
    res.write(`data: ${JSON.stringify({
      choices: [{ delta: {}, finish_reason: step.tool ? "tool_calls" : "stop" }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    })}\n\n`);
    res.end("data: [DONE]\n\n");
  });
}

// A local Ollama serving `models`: GET /api/tags lists them; POST /api/chat answers `reply`.
function ollamaMind(t, models, reply) {
  return listen(t, (req, res, body) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    if (req.method === "POST" && req.url === "/api/chat") {
      return res.end(JSON.stringify({
        model: body?.model, message: { role: "assistant", content: reply },
        done: true, done_reason: "stop", prompt_eval_count: 7, eval_count: 9,
      }));
    }
    res.end(JSON.stringify({ models: models.map(name => ({ name })) }));
  });
}

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

function spawnServer(t, extraEnv) {
  const temp = scratch(t, "youi-modes-tmp-");
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
      OLLAMA_API_KEY: "",
      YOUI_CLAUDE_BIN: "",
      YOUI_CLAUDE_CWD: join(temp, "claude-cwd"),
      YOUI_COLLAB_HOST: join(temp, "no-collab-host"),
      TMPDIR: temp,
      ...extraEnv,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (!testServers.has(t)) testServers.set(t, []);
  testServers.get(t).push(child);
  t.after(() => stopServers(t));
  return child;
}

async function startServer(t, extraEnv = {}) {
  const child = spawnServer(t, {
    OLLAMA_LOCAL_BASE_URL: await closedUrl(), OLLAMA_VLLM_BASE_URL: await closedUrl(), ...extraEnv,
  });
  let stderr = "";
  child.stderr.on("data", chunk => { stderr += chunk.toString("utf-8"); });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`YOUI test server timed out: ${stderr.slice(-500)}`)), 10_000);
    let stdout = "";
    child.stdout.on("data", chunk => {
      stdout += chunk.toString("utf-8");
      const match = stdout.match(/YOUI_TEST_READY\s+(\d+)\s+(\S+)/);
      if (!match) return;
      clearTimeout(timer);
      resolve(Number(match[1]));
    });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`YOUI exited ${code}: ${stderr.slice(-500)}`)); });
  });
}

async function session(port, label, body = {}) {
  const origin = `http://127.0.0.1:${port}`;
  const bootstrap = await request(port);
  const pairs = (bootstrap.headers["set-cookie"] || []).map(value => value.split(";", 1)[0]);
  const cookie = pairs.join("; ");
  const csrf = decodeURIComponent((pairs.find(pair => pair.startsWith("youi_csrf=")) || "=").split("=")[1]);
  const page = `page-${label}-modes-0000000001`;
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: { Cookie: cookie, Origin: origin, "X-YOUI-CSRF": csrf, "X-YOUI-Page": page },
    body: { label, agent: "alpha", ...body },
  });
  assert.equal(created.status, 201, created.text);
  const headers = {
    Cookie: cookie,
    "X-YOUI-Session": created.json.session.id,
    "X-YOUI-Session-Token": created.json.sessionToken,
    "X-YOUI-Page": page,
  };
  const unsafe = { ...headers, Origin: origin, "X-YOUI-CSRF": csrf };
  return {
    get: path => request(port, { path, headers }),
    post: (path, body) => request(port, { path, method: "POST", body, headers: unsafe }),
    // The page sends its mode with every message, as `chatMode` in the body.
    async chat(message, chatMode) {
      const response = await fetch(`${origin}/api/chat`, {
        method: "POST",
        headers: { ...unsafe, "Content-Type": "application/json" },
        body: JSON.stringify({ message, ...(chatMode ? { chatMode } : {}) }),
      });
      assert.equal(response.status, 200);
      return (await response.text()).split("\n\n").filter(Boolean).map(chunk => ({
        event: chunk.match(/^event: (.+)$/m)?.[1],
        data: JSON.parse(chunk.match(/^data: (.+)$/m)?.[1] || "null"),
      }));
    },
  };
}

// YOUI's own loop sends one system message: the stable prompt, then the per-turn block.
const CONTEXT_SPLIT = "\n\n---\n\n# This turn\n";
function promptParts(agentRequest) {
  const system = agentRequest.body.messages[0].content;
  const at = system.indexOf(CONTEXT_SPLIT);
  assert.ok(at > 0, "the per-turn block follows the stable prompt");
  return { stable: system.slice(0, at), context: system.slice(at + "\n\n---\n\n".length), tools: JSON.stringify(agentRequest.body.tools) };
}

// ── The hint itself ─────────────────────────────────────────────────

test("chatMode takes direct, orchestrate and the three mind modes; nothing else is a mind mode", () => {
  assert.deepEqual(CHAT_MODES, ALL_MODES);
  assert.deepEqual(Object.keys(MIND_MODES), ["split", "hive", "fenhe"]);
  assert.deepEqual(Object.values(MIND_MODES).map(mode => mode.label), ["SPLIT 分", "HIVE 合", "分分合合"]);
  for (const mode of ["split", "hive", "fenhe"]) assert.equal(isMindMode(mode), true);
  for (const mode of ["direct", "orchestrate", "SPLIT", "swarm", "__proto__", "constructor", "", null, undefined, 3]) {
    assert.equal(isMindMode(mode), false, String(mode));
  }
});

test("each mode's paragraph carries what the person asked for, with its reason", () => {
  const turn = mode => mindModeTurn(mode, { callable: true, offered: 3, callsPerMessage: 6 });
  const split = turn("split");
  assert.equal(split.applies, true);
  assert.equal(split.notice, null);
  for (const pattern of [/same question independently/, /none of them sees another's answer/, /one by one, each under its mind's name/,
    /in its own words as far as length allows/, /say where they differ/, /Do not fuse them or pick a winner/,
    /your own answer last, marked as your own/, /Why: Agreement between minds that read each other is worth less, and the person asked to see the difference\./]) {
    assert.match(split.section, pattern);
  }
  const hive = turn("hive");
  for (const pattern of [/Consult the minds as you see fit/, /answer with one voice/, /name which minds contributed/, /"Not merged:" line/,
    /not the retired HIVE message bus/, /Why: One answer is easier to act on, and a merge that hides a disagreement misstates it\./]) {
    assert.match(hive.section, pattern);
  }
  const fenhe = turn("fenhe");
  for (const pattern of [/^Split: .*Merge: .*Split again: .*Merge: /m, /criticise, each alone and none seeing another's criticism/,
    /mind-call budget \(6 calls; each split costs one call per mind\)/,
    /say how many split-and-merge rounds ran \(the whole of 分分合合 is two\)/,
    /Why: The second split tests the merge\./]) {
    assert.match(fenhe.section, pattern);
  }
  assert.match(mindModeTurn("fenhe", { callable: true, offered: 2, callsPerMessage: 20 }).section, /\(20 calls;/);
  for (const each of [split, hive, fenhe]) {
    assert.match(each.section, new RegExp(`^# Mind mode: ${each.label} \\(the person's choice for this message\\)\\n`));
    assert.match(each.modeLine, new RegExp(`^${each.label} \\(the person asked .*; how, below\\)$`));
  }
});

test("a mode that cannot apply says why, in one sentence the page shows as it is", () => {
  const cases = [
    [{ callable: false, offered: 3, callsPerMessage: 6 }, "this session lacks orchestrator:run, so the tools that call other minds are not offered"],
    [{ callable: true, offered: 0, callsPerMessage: 6 }, "no mind is granted and reachable for this session right now"],
    [{ callable: true, offered: 1, callsPerMessage: 6 }, "only one mind is granted and reachable for this session right now, and the mode needs two"],
  ];
  // SPLIT and HIVE need one split of two minds; 分分合合 needs two, so four calls.
  const ceilings = {
    split: [[{ callable: true, offered: 3, callsPerMessage: 1 }, "this message may make only 1 mind call, and the mode needs two"]],
    hive: [[{ callable: true, offered: 3, callsPerMessage: 1 }, "this message may make only 1 mind call, and the mode needs two"]],
    fenhe: [1, 2, 3].map(calls => [{ callable: true, offered: 3, callsPerMessage: calls },
      `this message may make only ${calls} mind call${calls === 1 ? "" : "s"}, and the mode needs four (two splits of two minds)`]),
  };
  for (const mode of ["split", "hive", "fenhe"]) {
    for (const [turn, reason] of [...cases, ...ceilings[mode]]) {
      const hint = mindModeTurn(mode, turn);
      assert.equal(hint.applies, false);
      assert.equal(hint.notice, `${MIND_MODES[mode].label} could not apply to this message: ${reason}.`);
      assert.ok(hint.section.startsWith(`# Mind mode: ${MIND_MODES[mode].label} could not apply\n${hint.notice} `), hint.section);
      assert.match(hint.section, /adds no tool and no permission/);
      assert.equal(hint.modeLine, `${MIND_MODES[mode].label}, which could not apply to this message (why, below)`);
    }
  }
  // At its own minimum each mode applies.
  for (const [mode, calls] of [["split", 2], ["hive", 2], ["fenhe", 4]]) {
    const hint = mindModeTurn(mode, { callable: true, offered: 2, callsPerMessage: calls });
    assert.equal(hint.applies, true, `${mode} at ${calls} calls`);
    assert.equal(hint.notice, null);
  }
  assert.throws(() => mindModeTurn("orchestrate", { callable: true, offered: 3, callsPerMessage: 6 }), /Not a mind mode/);
});

// ── Through YOUI's own loop ─────────────────────────────────────────

test("settings accept the three modes and refuse unknown ones as before; the choice stays with the session", async t => {
  const port = await startServer(t, { YOUI_CAPABILITIES: WITH_MINDS });
  const browser = await session(port, "settings");
  for (const mode of ["split", "hive", "fenhe", "orchestrate", "direct"]) {
    const set = await browser.post("/api/settings", { chatMode: mode });
    assert.equal(set.status, 200, set.text);
    assert.equal(set.json.chatMode, mode);
    assert.equal((await browser.get("/api/status")).json.chatMode, mode);
  }
  await browser.post("/api/settings", { chatMode: "hive" });
  for (const mode of ["SPLIT", "swarm", "hive:send", "__proto__", 7]) {
    const refused = await browser.post("/api/settings", { chatMode: mode });
    assert.equal(refused.status, 400, String(mode));
    assert.match(refused.json.error, /invalid chatMode/);
  }
  assert.equal((await browser.get("/api/status")).json.chatMode, "hive", "a refused value changes nothing");
  assert.equal((await browser.get("/api/sessions/current")).json.session.chatMode, "hive");
});

test("each mode puts its paragraph in the per-turn block; the stable prompt and the tools are the same bytes in every mode", async t => {
  const question = "Is a kilo of iron heavier than a kilo of feathers?";
  const ensemble = id => ({ tool: { id, name: "ensemble", input: { question, minds: ["ollama:mind-a:1b", "ollama:mind-b:1b"] } } });
  const vllm = await scriptedVllm(t, [VLLM_MODEL], [
    ensemble("call-direct"), { text: "fused" },    // direct
    { text: "orchestrated" },                       // orchestrate
    ensemble("call-split"), { text: "apart" },      // split
    { text: "one voice" },                          // hive
    { text: "two rounds" },                         // fenhe
  ]);
  const ollama = await ollamaMind(t, ["mind-a:1b", "mind-b:1b"], "They weigh the same.");
  const port = await startServer(t, {
    OLLAMA_LOCAL_BASE_URL: ollama.baseUrl, OLLAMA_VLLM_BASE_URL: vllm.baseUrl, YOUI_CAPABILITIES: WITH_MINDS,
  });
  const browser = await session(port, "modes", { model: VLLM_MODEL });
  const events = {};
  for (const mode of ALL_MODES) events[mode] = await browser.chat(`ask in ${mode}`, mode);
  const agentRequests = vllm.requests.filter(r => r.method === "POST" && r.path === "/v1/chat/completions");
  assert.equal(agentRequests.length, 7);
  // The first request of each message: direct 0, orchestrate 2, split 3, hive 5, fenhe 6.
  const parts = Object.fromEntries(ALL_MODES.map((mode, index) => [mode, promptParts(agentRequests[[0, 2, 3, 5, 6][index]])]));

  for (const mode of ALL_MODES) {
    assert.equal(parts[mode].stable, parts.direct.stable, `${mode}: the stable prompt is the same bytes`);
    assert.equal(parts[mode].tools, parts.direct.tools, `${mode}: the same tools, with the same descriptions`);
    assert.doesNotMatch(parts[mode].stable, /Mind mode|SPLIT 分|HIVE 合|分分合合/);
  }
  assert.doesNotMatch(parts.direct.context, /Mind mode|# Minds/);
  assert.doesNotMatch(parts.orchestrate.context, /Mind mode/);
  assert.match(parts.orchestrate.context, /^# Minds \(orchestrate mode\)\nOrchestrate mode is on/m);
  for (const mode of ["split", "hive", "fenhe"]) {
    const { label } = MIND_MODES[mode];
    const { context } = parts[mode];
    const hint = mindModeTurn(mode, { callable: true, offered: 3, callsPerMessage: 6 });
    assert.ok(context.includes(`- Mode: ${hint.modeLine}\n`), context);
    assert.ok(context.includes(`\n\n---\n\n${hint.section}\n\n---\n\n# Minds (for ${label})\n`), context);
    assert.ok(context.includes(`vllm · REMOTE_MODEL:vllm · reachable: ${VLLM_MODEL} (you)`));
    assert.ok(context.includes("ollama · LOCAL_MODEL:ollama · reachable: mind-a:1b, mind-b:1b"));
    assert.ok(!events[mode].some(event => event.event === "notice"), `${mode} applied, so no notice`);
    assert.ok(events[mode].some(event => event.event === "minds"), "the minds panel refreshes, as in orchestrate");
  }
  // The person's message reaches the agent only; each mind gets the agent's question alone.
  const mindRequests = ollama.requests.filter(r => r.method === "POST" && r.path === "/api/chat");
  assert.equal(mindRequests.length, 4, "two ensembles of two");
  for (const { body } of mindRequests) {
    assert.equal(body.messages.at(-1).content, question);
    assert.doesNotMatch(JSON.stringify(body), /ask in|Mind mode/);
  }

  // minds_used has the same shape in a mode as in direct mode, and carries no answer text.
  const used = mode => events[mode].filter(event => event.event === "minds_used").map(event => event.data);
  const [direct] = used("direct");
  const [split] = used("split");
  assert.deepEqual(Object.keys(split).sort(), ["id", "minds", "tool"]);
  assert.deepEqual(Object.keys(split).sort(), Object.keys(direct).sort());
  for (const mind of [...split.minds, ...direct.minds]) {
    assert.deepEqual(Object.keys(mind).sort(), ["destination", "error", "model", "ok", "privacy", "provider", "role", "usage"]);
  }
  assert.doesNotMatch(JSON.stringify(split), /They weigh the same/);
  assert.deepEqual(split.minds.map(({ provider, model, ok }) => [provider, model, ok]),
    direct.minds.map(({ provider, model, ok }) => [provider, model, ok]));
});

test("when the mind tools are not offered, or too few minds or calls remain, the mode says it could not apply and widens nothing", async t => {
  // No orchestrator:run.
  const vllm = await scriptedVllm(t, [VLLM_MODEL], []);
  const ollama = await ollamaMind(t, ["mind-a:1b", "mind-b:1b"], "unused");
  const port = await startServer(t, {
    OLLAMA_LOCAL_BASE_URL: ollama.baseUrl, OLLAMA_VLLM_BASE_URL: vllm.baseUrl,
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use",
  });
  const browser = await session(port, "no-orchestrator", { model: VLLM_MODEL });
  await browser.chat("alone", "direct");
  const events = await browser.chat("split please", "split");
  const requests = () => vllm.requests.filter(r => r.method === "POST" && r.path === "/v1/chat/completions");
  const [direct, split] = requests().map(promptParts);
  const notices = events.filter(event => event.event === "notice").map(event => event.data.message);
  const sentence = "SPLIT 分 could not apply to this message: this session lacks orchestrator:run, so the tools that call other minds are not offered.";
  assert.deepEqual(notices, [sentence], "the page is shown the same sentence");
  assert.ok(split.context.includes(`# Mind mode: SPLIT 分 could not apply\n${sentence} `));
  assert.match(split.context, /^- Mode: SPLIT 分, which could not apply to this message \(why, below\)$/m);
  assert.doesNotMatch(split.context, /# Minds/, "no minds are listed for a mode that did not apply");
  assert.equal(split.stable, direct.stable);
  assert.equal(split.tools, direct.tools, "the same tools as a direct turn: the mode widened nothing");
  assert.ok(!JSON.parse(split.tools).some(tool => tool.function.name === "ensemble"));
  assert.equal(ollama.requests.filter(r => r.method === "POST").length, 0, "no mind was called");

  // Only the agent's own model answers, so one mind is offered.
  const lonely = await scriptedVllm(t, [VLLM_MODEL], []);
  const onePort = await startServer(t, { OLLAMA_VLLM_BASE_URL: lonely.baseUrl, YOUI_CAPABILITIES: WITH_MINDS });
  const one = await (await session(onePort, "one-mind", { model: VLLM_MODEL })).chat("hive please", "hive");
  assert.deepEqual(one.filter(event => event.event === "notice").map(event => event.data.message),
    ["HIVE 合 could not apply to this message: only one mind is granted and reachable for this session right now, and the mode needs two."]);

  // A ceiling of three mind calls per message: enough for one split, so SPLIT
  // applies, but not for 分分合合's second split.
  const capped = await scriptedVllm(t, [VLLM_MODEL], []);
  const minds = await ollamaMind(t, ["mind-a:1b", "mind-b:1b"], "unused");
  const cappedPort = await startServer(t, {
    OLLAMA_LOCAL_BASE_URL: minds.baseUrl, OLLAMA_VLLM_BASE_URL: capped.baseUrl,
    YOUI_CAPABILITIES: WITH_MINDS, YOUI_MIND_CALLS_PER_MESSAGE: "3",
  });
  const threeCalls = await session(cappedPort, "three-calls", { model: VLLM_MODEL });
  const fenhe = await threeCalls.chat("fenhe please", "fenhe");
  assert.deepEqual(fenhe.filter(event => event.event === "notice").map(event => event.data.message),
    ["分分合合 could not apply to this message: this message may make only 3 mind calls, and the mode needs four (two splits of two minds)."]);
  const [fenheTurn] = capped.requests.filter(r => r.method === "POST" && r.path === "/v1/chat/completions").map(promptParts);
  assert.doesNotMatch(fenheTurn.context, /Split again/, "the agent is not asked for a split the turn cannot make");
  assert.doesNotMatch(fenheTurn.context, /# Minds/);
  const splitEvents = await threeCalls.chat("split please", "split");
  assert.ok(!splitEvents.some(event => event.event === "notice"), "SPLIT needs one split of two, so it applies");
});

// ── Through Claude Code ─────────────────────────────────────────────

test("on a Claude turn the system prompt file and the bridge's tool list are the same bytes in every mode", async t => {
  const dir = scratch(t, "youi-modes-claude-");
  const log = join(dir, "calls.jsonl");
  const bin = join(dir, "claude");
  writeFileSync(bin, [
    `#!${process.execPath}`,
    `process.env.FAKE_CLAUDE_LOG = ${JSON.stringify(log)};`,
    `process.env.FAKE_CLAUDE_VERSION = "2.1.314";`,
    `process.env.FAKE_CLAUDE_SESSIONS = ${JSON.stringify(join(dir, "sessions"))};`,
    `import(${JSON.stringify(pathToFileURL(FAKE_CLAUDE).href)});`,
    "",
  ].join("\n"));
  chmodSync(bin, 0o755);
  const entries = step => (existsSync(log) ? readFileSync(log, "utf-8").split("\n").filter(Boolean).map(line => JSON.parse(line)) : [])
    .filter(entry => entry.step === step);
  const port = await startServer(t, { YOUI_CLAUDE_BIN: bin, YOUI_CLAUDE_CWD: join(dir, "claude-code-cwd"), YOUI_CAPABILITIES: WITH_MINDS });
  const browser = await session(port, "claude-modes");
  for (const mode of ALL_MODES) await browser.chat("FAKE:list", mode);

  const runs = entries("start");
  const lists = entries("tools-list").map(entry => JSON.stringify(entry.listed.body.result.tools));
  const contexts = entries("message").map(entry => entry.context);
  assert.equal(runs.length, 5);
  assert.equal(lists.length, 5);
  for (const index of [1, 2, 3, 4]) {
    assert.equal(runs[index].systemPrompt.content, runs[0].systemPrompt.content, `${ALL_MODES[index]}: the same system prompt file`);
    assert.equal(lists[index], lists[0], `${ALL_MODES[index]}: the same tool names, descriptions and schemas`);
  }
  assert.ok(JSON.parse(lists[0]).some(tool => tool.name === "ensemble" && tool.description));
  assert.doesNotMatch(runs[0].systemPrompt.content, /Mind mode|SPLIT 分|HIVE 合|分分合合/);
  // Claude minds are offered here (Claude Code is present and new enough), so each mode applies.
  for (const [index, mode] of [[2, "split"], [3, "hive"], [4, "fenhe"]]) {
    assert.match(contexts[index], new RegExp(`# Mind mode: ${MIND_MODES[mode].label} \\(the person's choice for this message\\)`));
    assert.match(contexts[index], new RegExp(`# Minds \\(for ${MIND_MODES[mode].label}\\)`));
  }
  assert.match(contexts[0], /- Mode: direct \(work alone; call other minds only when the person asks\)/);
  assert.match(contexts[1], /- Mode: orchestrate \(the minds offered this turn are listed below\)/);
});

// ── The name, the page ──────────────────────────────────────────────

test("the retired HIVE and fleet grants are still refused at startup", async t => {
  for (const grant of ["hive:read", "hive:send", "fleet:manage"]) {
    const child = spawnServer(t, { YOUI_CAPABILITIES: `chat,${grant}` });
    let stderr = "";
    child.stderr.on("data", chunk => { stderr += chunk.toString("utf-8"); });
    const code = await new Promise(resolve => child.once("exit", resolve));
    assert.notEqual(code, 0, grant);
    assert.match(stderr, new RegExp(`Unknown YOUI capabilities: ${grant}`));
  }
});

test("the page offers the three modes by name and renders the weather line as text only", () => {
  const page = readFileSync(join(REPO_ROOT, "youi-web/public/index.html"), "utf-8");
  for (const [id, label] of [["split", "SPLIT 分"], ["hive", "HIVE 合"], ["fenhe", "分分合合"]]) {
    assert.match(page, new RegExp(`<button id="mode-${id}" class="mode-btn" onclick="setChatMode\\('${id}'\\)"[^>]*>\\s*<span class="mode-label">${label}</span>`));
  }
  assert.match(page, /const CHAT_MODES = \['direct', 'orchestrate', 'split', 'hive', 'fenhe'\];/);
  const weather = page.slice(page.indexOf("async function fetchWeather()"), page.indexOf("function startWeather("));
  assert.ok(weather.length > 500);
  assert.doesNotMatch(weather, /innerHTML|insertAdjacentHTML|outerHTML/);
  assert.match(weather, /textContent/);
  assert.match(page, /const WEATHER_POLL_MS = 60_000;/);
});
