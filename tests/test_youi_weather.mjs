// YOUI's device weather line: `agenttool-collab-host weather` through
// decision-bridge.mjs, GET /api/weather, and YOUI_WEATHER_TO_AGENT.
//
// Every host here is a fake (a small Node script written into a temp dir and
// named by YOUI_COLLAB_HOST), as in tests/test_youi_decisions.mjs, and every
// `claude` is tests/fixtures/fake-claude.mjs. No collab journal, Keychain or
// model is touched, and no real device reading is taken.

import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { homedir, hostname, tmpdir, userInfo } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import {
  WEATHER_TIMEOUT_MS,
  readDeviceWeather,
  weatherPromptLine,
  weatherToAgentSetting,
} from "../youi-web/decision-bridge.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const FAKE_CLAUDE = join(REPO_ROOT, "tests/fixtures/fake-claude.mjs");
const SAYS = "On battery at 92%, about 5 h 24 min left; load 2.4 on 8 cores; memory pressure normal; graphics idle;"
  + " Wi-Fi, route reported reachable. Read just now; good for about a minute.";
const NOTE = "On battery: work here spends the person's charge, and a closed lid stops every session on the device at once.";

// ── Helpers ─────────────────────────────────────────────────────────

// The servers each test started; scratch directories go only after they have stopped.
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

async function until(predicate, { timeoutMs = 5_000, label = "condition" } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(`timed out waiting for ${label}`);
}

function alive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

/**
 * A fake collab host. `weather` answers by the `weather-mode` file (reading,
 * refuse, fail, garbage, wrong-protocol, hang) after `weather-delay-ms`;
 * `person <verb>` answers a canned receipt after `desk-delay-ms`. Every run
 * appends its argv, pid, tty and environment markers to calls.jsonl.
 */
function fakeHost(t, { weatherMode = "reading" } = {}) {
  const dir = scratch(t, "youi-fake-weather-");
  const host = join(dir, "agenttool-collab-host");
  writeFileSync(host, `#!${process.execPath}
import { appendFileSync, openSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
const dir = ${JSON.stringify(dir)};
const read = (name, fallback) => { try { return readFileSync(dir + "/" + name, "utf8").trim(); } catch { return fallback; } };
let tty = false;
try { openSync("/dev/tty", "r+"); tty = true; } catch {}
const args = process.argv.slice(2);
appendFileSync(dir + "/calls.jsonl", JSON.stringify({ args, pid: process.pid, tty, at: Date.now(),
  secret: process.env.YOUI_FAKE_SECRET ?? null, purpose: process.env.YOUI_CHILD_PURPOSE ?? null }) + "\\n");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const HOST = "agenttool.collab-host/1";
const reading = () => ({
  protocol: "agenttool.collab-weather/1", read_at: new Date().toISOString(), good_for_seconds: 60, platform: "darwin",
  basis: "read by this process from the operating system just now; self-declared, nothing attests it",
  authorises: "nothing; this is a reading, not a permission",
  says: ${JSON.stringify(SAYS)},
  notes: [${JSON.stringify(NOTE)}],
  power: { source: "battery", battery_percent: 92, battery_state: "discharging", minutes_remaining: 324 },
  cpu: { chip: "Apple M2", cores: 8, load_1m: 2.4 }, memory: { pressure: "normal" },
  gpu: { devices: [{ model: "Apple M2", utilisation_percent: 0 }] }, network: { kind: "wifi", os_reports: "reachable" },
  inferred: [], unknown: [{ what: "network.metered", why: "macOS does not show it to a command-line reader" },
    { what: "power.awake_since", why: "not read" }],
});
if (args[0] === "weather") {
  await sleep(Number(read("weather-delay-ms", "0")));
  const mode = read("weather-mode", "reading");
  if (mode === "hang") {
    const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
    appendFileSync(dir + "/pids.jsonl", JSON.stringify({ host: process.pid, child: child.pid }) + "\\n");
    setInterval(() => {}, 1000);
  } else if (mode === "refuse") {
    // What a host from before weather prints for an unknown command.
    console.log(JSON.stringify({ protocol: HOST, status: "error", error: "usage_error",
      message: "usage: agenttool-collab-host end ... | agenttool-collab-host wait ... | agenttool-collab-host person ..." }));
    process.exitCode = 1;
  } else if (mode === "fail") {
    console.log(JSON.stringify({ protocol: HOST, status: "error", error: "weather_unavailable", message: "The device reading failed" }));
    process.exitCode = 1;
  } else if (mode === "garbage") {
    console.log("not a receipt");
  } else if (mode === "wrong-protocol") {
    console.log(JSON.stringify({ protocol: HOST, status: "weather", weather: { ...reading(), protocol: "agenttool.collab-weather/9" } }));
  } else {
    console.log(JSON.stringify({ protocol: HOST, status: "weather", weather: reading() }));
  }
} else if (args[0] === "person") {
  await sleep(Number(read("desk-delay-ms", "0")));
  const receipts = {
    workspaces: { status: "ok", workspaces: [{ id: "ws_demo", name: "demo", root_path: "/tmp/demo", created_at: "2026-10-29T00:00:00.000Z" }] },
    inbox: { status: "ok", summary: [] },
  };
  console.log(JSON.stringify({ protocol: HOST, command: "person " + args[1], ...(receipts[args[1]] ?? { status: "ok" }) }));
} else {
  console.log(JSON.stringify({ protocol: HOST, status: "error", error: "usage_error", message: "usage" }));
  process.exitCode = 1;
}
`);
  chmodSync(host, 0o755);
  const set = (name, value) => writeFileSync(join(dir, name), String(value));
  set("weather-mode", weatherMode);
  return {
    dir,
    host,
    set,
    env: { YOUI_COLLAB_HOST: host, YOUI_COLLAB_DB: join(dir, "collab.sqlite") },
    calls: () => existsSync(join(dir, "calls.jsonl"))
      ? readFileSync(join(dir, "calls.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line))
      : [],
    pids: () => existsSync(join(dir, "pids.jsonl"))
      ? readFileSync(join(dir, "pids.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line))
      : [],
  };
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

function closedUrl() {
  return new Promise(resolve => {
    const server = http.createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(`http://127.0.0.1:${port}`));
    });
  });
}

function spawnServer(t, extraEnv, temp) {
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
  const temp = scratch(t, "youi-weather-tmp-");
  // No real Ollama or vLLM is ever probed.
  const child = spawnServer(t, {
    OLLAMA_LOCAL_BASE_URL: await closedUrl(), OLLAMA_VLLM_BASE_URL: await closedUrl(), ...extraEnv,
  }, temp);
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

/** A server that must refuse to start: resolves with its exit code and stderr. */
async function startupRefused(t, extraEnv) {
  const child = spawnServer(t, extraEnv, scratch(t, "youi-weather-refused-"));
  let stderr = "";
  let stdout = "";
  child.stderr.on("data", chunk => { stderr += chunk.toString("utf-8"); });
  child.stdout.on("data", chunk => { stdout += chunk.toString("utf-8"); });
  const code = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("the server did not exit")), 10_000);
    child.once("exit", exitCode => { clearTimeout(timer); resolve(exitCode); });
  });
  return { code, stderr, ready: /YOUI_TEST_READY/.test(stdout) };
}

async function browserSession(port, label = "weather") {
  const origin = `http://127.0.0.1:${port}`;
  const bootstrap = await request(port);
  const pairs = (bootstrap.headers["set-cookie"] || []).map(value => value.split(";", 1)[0]);
  const cookie = pairs.join("; ");
  const csrf = decodeURIComponent((pairs.find(pair => pair.startsWith("youi_csrf=")) || "=").split("=")[1]);
  const page = `page-${label}-browser-000000001`;
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: { Cookie: cookie, Origin: origin, "X-YOUI-CSRF": csrf, "X-YOUI-Page": page },
    body: { label, agent: "alpha" },
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
    origin,
    cookie,
    get: path => request(port, { path, headers }),
    post: (path, body) => request(port, { path, method: "POST", body, headers: unsafe }),
    async chat(message, extra = {}) {
      const response = await fetch(`${origin}/api/chat`, {
        method: "POST",
        headers: { ...unsafe, "Content-Type": "application/json" },
        body: JSON.stringify({ message, ...extra }),
      });
      assert.equal(response.status, 200);
      return (await response.text()).split("\n\n").filter(Boolean).map(chunk => ({
        event: chunk.match(/^event: (.+)$/m)?.[1],
        data: JSON.parse(chunk.match(/^data: (.+)$/m)?.[1] || "null"),
      }));
    },
  };
}

/** A `claude` wrapper around tests/fixtures/fake-claude.mjs, logging to its own file. */
function fakeClaude(t) {
  const dir = scratch(t, "youi-weather-claude-");
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
  const entries = () => (existsSync(log)
    ? readFileSync(log, "utf-8").split("\n").filter(Boolean).map(line => JSON.parse(line))
    : []);
  return {
    env: { YOUI_CLAUDE_BIN: bin, YOUI_CLAUDE_CWD: join(dir, "claude-code-cwd") },
    runs: () => entries().filter(entry => entry.step === "start"),
    messages: () => entries().filter(entry => entry.step === "message"),
  };
}

// ── The bridge ──────────────────────────────────────────────────────

const WEATHER_KEYS = ["available", "good_for_seconds", "not_read", "notes", "read_at", "says"];
const UNAVAILABLE_KEYS = ["available", "detail", "reason"];

test("the bridge runs `<host> weather` alone: no person verb, no journal, no terminal, no ambient secret", async t => {
  const fake = fakeHost(t);
  const answer = await readDeviceWeather({ ...process.env, ...fake.env, YOUI_FAKE_SECRET: "must-not-reach-the-host" });
  assert.deepEqual(Object.keys(answer).sort(), WEATHER_KEYS);
  assert.equal(answer.available, true);
  assert.equal(answer.says, SAYS);
  assert.deepEqual(answer.notes, [NOTE]);
  assert.equal(answer.good_for_seconds, 60);
  assert.equal(answer.not_read, 2, "the count of what the host did not read");
  assert.ok(Math.abs(Date.parse(answer.read_at) - Date.now()) < 10_000);

  const [call] = fake.calls();
  assert.deepEqual(call.args, ["weather"], "exactly one argument: no person, no --db");
  assert.equal(fake.calls().length, 1);
  assert.equal(call.tty, false, "no controlling terminal");
  assert.equal(call.secret, null, "the environment is sanitised");
  assert.equal(call.purpose, "collab-weather");
  assert.equal(WEATHER_TIMEOUT_MS, 5_000);
});

test("a bundled host runs under Bun exactly as the desk does, and a missing host or Bun is host_missing", async t => {
  const dir = scratch(t, "youi-fake-bun-");
  const bun = join(dir, "bun");
  writeFileSync(bun, `#!${process.execPath}
import { appendFileSync } from "node:fs";
appendFileSync(${JSON.stringify(join(dir, "bun-calls.jsonl"))}, JSON.stringify(process.argv.slice(2)) + "\\n");
console.log(JSON.stringify({ protocol: "agenttool.collab-host/1", status: "error", error: "usage_error", message: "usage" }));
process.exitCode = 1;
`);
  chmodSync(bun, 0o755);
  const bundle = join(dir, "agenttool-collab-host.js");
  writeFileSync(bundle, "// never run: the fake Bun only records its argv\n");
  const bunCalls = () => (existsSync(join(dir, "bun-calls.jsonl"))
    ? readFileSync(join(dir, "bun-calls.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line))
    : []);

  const refused = await readDeviceWeather({ ...process.env, YOUI_COLLAB_HOST: bundle, YOUI_COLLAB_BUN: bun });
  assert.deepEqual(refused, {
    available: false,
    reason: "not_in_installed_build",
    detail: "the collab host installed on this device has no weather command yet",
  });
  assert.deepEqual(bunCalls(), [["--no-env-file", "--config=/dev/null", bundle, "weather"]]);

  const missingBundle = await readDeviceWeather({ ...process.env, YOUI_COLLAB_HOST: join(dir, "gone.js"), YOUI_COLLAB_BUN: bun });
  assert.equal(missingBundle.reason, "host_missing");
  assert.equal(bunCalls().length, 1, "a missing bundle is found before Bun is started");
  const missingBun = await readDeviceWeather({ ...process.env, YOUI_COLLAB_HOST: bundle, YOUI_COLLAB_BUN: join(dir, "no-bun") });
  assert.equal(missingBun.reason, "host_missing");
  assert.match(missingBun.detail, /Bun/);
  const missingHost = await readDeviceWeather({ ...process.env, YOUI_COLLAB_HOST: "/nonexistent/agenttool-collab-host" });
  assert.equal(missingHost.reason, "host_missing");
  for (const answer of [refused, missingBundle, missingBun, missingHost]) {
    assert.deepEqual(Object.keys(answer).sort(), UNAVAILABLE_KEYS);
    assert.ok(!answer.detail.includes(dir) && !answer.detail.includes(homedir()) && !answer.detail.includes("/nonexistent"),
      "a reason names no path");
  }
});

test("a host that refuses, fails or answers something else is reported, never a reading", async t => {
  const fake = fakeHost(t, { weatherMode: "refuse" });
  const env = { ...process.env, ...fake.env };
  assert.equal((await readDeviceWeather(env)).reason, "not_in_installed_build");
  fake.set("weather-mode", "fail");
  assert.deepEqual(await readDeviceWeather(env),
    { available: false, reason: "failed", detail: "the collab host did not give a reading (weather_unavailable)" });
  fake.set("weather-mode", "garbage");
  assert.equal((await readDeviceWeather(env)).detail, "the collab host gave no receipt");
  fake.set("weather-mode", "wrong-protocol");
  assert.match((await readDeviceWeather(env)).detail, /not in the form YOUI reads \(agenttool\.collab-weather\/1\)/);
});

test("a slow host is killed, with its whole process group, at the timeout", async t => {
  const fake = fakeHost(t, { weatherMode: "hang" });
  const started = Date.now();
  const answer = await readDeviceWeather({ ...process.env, ...fake.env }, { timeoutMs: 400 });
  const took = Date.now() - started;
  assert.deepEqual(answer, { available: false, reason: "failed", detail: "the collab host did not answer within 0.4 s" });
  assert.ok(took >= 350 && took < 3_000, `answered after ${took} ms`);
  const [{ host, child }] = await until(() => fake.pids().length && fake.pids(), { label: "the fake's pids" });
  await until(() => !alive(host) && !alive(child), { label: "the host and its child to be killed" });
});

test("overlapping reads share one host process, and nothing is kept once it has answered", async t => {
  const fake = fakeHost(t);
  fake.set("weather-delay-ms", 300);
  const env = { ...process.env, ...fake.env };
  const [first, second] = await Promise.all([readDeviceWeather(env), readDeviceWeather(env)]);
  assert.equal(first, second, "the same answer object");
  assert.equal(fake.calls().length, 1, "one host process for both");
  const third = await readDeviceWeather(env);
  assert.equal(fake.calls().length, 2, "the next read runs the host again");
  assert.notEqual(third.read_at, undefined);
});

test("YOUI_WEATHER_TO_AGENT is on or off, absent is off, and anything else throws", () => {
  assert.equal(weatherToAgentSetting(undefined), false);
  assert.equal(weatherToAgentSetting(""), false);
  assert.equal(weatherToAgentSetting("off"), false);
  assert.equal(weatherToAgentSetting("on"), true);
  for (const value of ["yes", "ON", "1", "true", " on"]) {
    assert.throws(() => weatherToAgentSetting(value), /Unknown YOUI_WEATHER_TO_AGENT/, value);
  }
});

test("the agent's line states the reading, its age and that it authorises nothing, or why there is none", () => {
  const readAt = "2026-11-14T11:14:02.000Z";
  const reading = { available: true, read_at: readAt, good_for_seconds: 60, says: "On mains.", notes: ["n"], not_read: 0 };
  assert.equal(weatherPromptLine(reading, Date.parse(readAt) + 3_000),
    "- Device weather: On mains. (read 3 s before this turn; this device's own report, which authorises nothing)");
  assert.equal(weatherPromptLine(reading, Date.parse(readAt) + 180_000),
    "- Device weather: On mains. (read 3 min before this turn, older than its 60 s, so not current;"
      + " this device's own report, which authorises nothing)");
  assert.equal(weatherPromptLine({ available: false, reason: "host_missing", detail: "why" }), "- Device weather: not read (why)");
});

// ── The route ───────────────────────────────────────────────────────

test("GET /api/weather needs status:read, refuses other methods, and answers one of two shapes with no identifier", async t => {
  const fake = fakeHost(t);
  const denied = await startServer(t, { ...fake.env, YOUI_CAPABILITIES: "chat,sessions:manage" });
  assert.equal((await request(denied, { path: "/api/weather" })).status, 401, "a browser session first");
  const deniedRead = await (await browserSession(denied)).get("/api/weather");
  assert.equal(deniedRead.status, 403);
  assert.equal(deniedRead.json.capability, "status:read");
  assert.deepEqual(fake.calls(), [], "a refused request reads nothing");

  const port = await startServer(t, fake.env);
  const browser = await browserSession(port);
  const posted = await browser.post("/api/weather", undefined);
  assert.equal(posted.status, 405);
  assert.equal(posted.headers.allow, "GET");

  const read = await browser.get("/api/weather");
  assert.equal(read.status, 200);
  assert.deepEqual(Object.keys(read.json).sort(), WEATHER_KEYS, "only these fields; the host's sections stay with the host");
  assert.equal(read.json.says, SAYS);
  for (const identifier of [fake.dir, homedir(), hostname(), userInfo().username, "Apple M2"]) {
    assert.ok(!read.text.includes(identifier), `the answer does not carry ${identifier}`);
  }
  const status = await browser.get("/api/status");
  assert.equal(status.json.weatherToAgent, false, "off unless the owner turns it on");

  fake.set("weather-mode", "refuse");
  const refused = await browser.get("/api/weather");
  assert.equal(refused.status, 200, "a refusal is a normal answer, not a 500");
  assert.deepEqual(Object.keys(refused.json).sort(), UNAVAILABLE_KEYS);
  assert.equal(refused.json.reason, "not_in_installed_build");
  fake.set("weather-mode", "fail");
  assert.equal((await browser.get("/api/weather")).json.reason, "failed");
  assert.deepEqual(fake.calls().map(call => call.args), [["weather"], ["weather"], ["weather"]]);

  const missing = await startServer(t, { YOUI_COLLAB_HOST: "/nonexistent/agenttool-collab-host" });
  const gone = await (await browserSession(missing)).get("/api/weather");
  assert.equal(gone.status, 200);
  assert.equal(gone.json.reason, "host_missing");
  assert.ok(!gone.text.includes("/nonexistent"));
});

test("a weather read and the desk's gate of three never wait on each other", async t => {
  const fake = fakeHost(t);
  const port = await startServer(t, fake.env);
  const browser = await browserSession(port);

  // A slow reading does not hold up a decision read.
  fake.set("weather-delay-ms", 1_500);
  let weatherDone = 0;
  const slowWeather = browser.get("/api/weather").then(response => { weatherDone = Date.now(); return response; });
  await until(() => fake.calls().some(call => call.args[0] === "weather"), { label: "the weather read to start" });
  const before = Date.now();
  const workspaces = await browser.get("/api/decisions/workspaces");
  assert.equal(workspaces.status, 200);
  assert.ok(Date.now() - before < 1_000, "the decision read did not wait for the weather read");
  assert.equal(weatherDone, 0, "and the weather read is still running");
  assert.equal((await slowWeather).json.available, true);

  // Four slow desk reads: three hold the gate, one queues. A reading still answers at once.
  fake.set("weather-delay-ms", 0);
  fake.set("desk-delay-ms", 1_500);
  let deskDone = 0;
  const desk = ["w1", "w2", "w3", "w4"].map(workspace => browser.get(`/api/decisions/inbox?workspace=${workspace}`)
    .then(response => { deskDone = deskDone || Date.now(); return response; }));
  await until(() => fake.calls().filter(call => call.args[1] === "inbox").length === 3, { label: "the gate to fill" });
  const weatherStarted = Date.now();
  const reading = await browser.get("/api/weather");
  assert.equal(reading.json.available, true);
  assert.ok(Date.now() - weatherStarted < 1_000, "the weather read did not queue behind the desk's gate");
  assert.equal(deskDone, 0, "the desk reads were still running");
  for (const response of await Promise.all(desk)) assert.equal(response.status, 200);
});

// ── The agent's switch ──────────────────────────────────────────────

const OLD_BLOCK = /^\[YOUI context\]\nThis turn's settings from YOUI, not words of the person:\n# This turn\n- Date: \d{4}-\d{2}-\d{2}\n- Git branch: [^\n]*\n- Model: claude-opus-5-5\n- Mode: direct \(work alone; call other minds only when the person asks\)\n- Thinking: adaptive \| Effort: high\n\[\/YOUI context\]\n\n$/;
const sameDay = block => block.replace(/- Date: \d{4}-\d{2}-\d{2}/, "- Date: <today>");

test("off, the prompt is what it was and nothing is read; on, the line is in the per-turn block only", async t => {
  const turn = async (switchValue, mode = "reading") => {
    const fake = fakeHost(t, { weatherMode: mode });
    const claude = fakeClaude(t);
    const env = { ...fake.env, ...claude.env };
    if (switchValue !== undefined) env.YOUI_WEATHER_TO_AGENT = switchValue;
    const port = await startServer(t, env);
    const browser = await browserSession(port);
    await browser.chat("hello");
    const [run] = claude.runs();
    const [sent] = claude.messages();
    return { fake, browser, system: run.systemPrompt.content, context: sent.context, message: sent.message };
  };
  const absent = await turn(undefined);
  const off = await turn("off");
  const on = await turn("on");

  // Off (or absent): the context block and the system prompt are byte for byte what they were.
  assert.match(absent.context, OLD_BLOCK);
  assert.equal(sameDay(off.context), sameDay(absent.context));
  assert.equal(off.system, absent.system);
  assert.deepEqual(absent.fake.calls(), [], "off reads no weather for the agent");
  assert.deepEqual(off.fake.calls(), []);

  // On: one line in the per-turn block, after the turn's settings; the system prompt is unchanged.
  assert.equal(on.system, off.system, "the cached system prompt is the same bytes");
  assert.doesNotMatch(on.system, /Device weather|On battery at 92%/);
  const line = on.context.match(/^- Device weather: .*$/m)?.[0];
  assert.ok(line, on.context);
  assert.match(line, new RegExp(`^- Device weather: ${SAYS.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\(read \\d+ s before this turn; this device's own report, which authorises nothing\\)$`));
  assert.ok(on.context.includes(`- Thinking: adaptive | Effort: high\n${line}\n[/YOUI context]`));
  assert.equal(sameDay(on.context.replace(`${line}\n`, "")), sameDay(off.context), "nothing else in the block moved");
  assert.equal(on.message, "hello");
  assert.deepEqual(on.fake.calls().map(call => call.args), [["weather"]]);
  assert.equal((await on.browser.get("/api/status")).json.weatherToAgent, true);

  // On, with today's installed build: the agent is told it was not read, rather than nothing.
  const refused = await turn("on", "refuse");
  assert.match(refused.context, /^- Device weather: not read \(the collab host installed on this device has no weather command yet\)$/m);
  assert.equal(refused.system, off.system);
});

test("an unknown YOUI_WEATHER_TO_AGENT stops startup", async t => {
  for (const value of ["yes", "ON"]) {
    const { code, stderr, ready } = await startupRefused(t, { YOUI_WEATHER_TO_AGENT: value });
    assert.notEqual(code, 0, value);
    assert.equal(ready, false);
    assert.match(stderr, /Unknown YOUI_WEATHER_TO_AGENT/);
  }
});
