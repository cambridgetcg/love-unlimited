// The cached system prompt and the tool list, pinned to their bytes.
//
// docs/YOUSPEAK.md §2.1: what changes from turn to turn goes in the per-turn
// [YOUI context] block, and the system prompt and the tools stay the same
// bytes, so Claude's prompt cache holds. Other tests compare these bytes
// within one build (mode against mode, the weather switch on against off).
// This one compares them with digests committed here, so a change that is the
// same in every mode and setting is caught too.
//
// The digests were taken from the build before the device weather line and
// the mind modes (798e1c3) and match this build. If a change to the system
// prompt or a tool is meant, it rewrites every conversation's cache once:
// say so where the change is described, then put the new digests here.
//
// What is pinned is YOUI's own text: the system prompt from "# Enabled tools
// for this session" on (the soul part before it is the Love home's and the
// identity's files, which their owners edit), the MCP bridge's tools/list on
// the Claude Code route, and the tool schemas of YOUI's own loop. The Love
// home, working directory, home directory and platform are replaced by names.
// Every model is a fake on 127.0.0.1 and `claude` is
// tests/fixtures/fake-claude.mjs; the Love home is an empty temp directory.

import assert from "node:assert/strict";
import crypto from "node:crypto";
import http from "node:http";
import { spawn } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const FAKE_CLAUDE = join(REPO_ROOT, "tests/fixtures/fake-claude.mjs");
const VLLM_MODEL = "Qwen/Qwen2.5-72B-Instruct-AWQ";
const CAPABILITIES = "chat,status:read,sessions:manage,settings:write,models:use,orchestrator:run";
const STATIC_FROM = "# Enabled tools for this session";

// sha256 of each normalised text, from 798e1c3 and this build alike.
const PINNED = {
  claudeSystem: "4322fd303542f1426de6e1bd0b7bf0cd380fe55781b0e3425d6a136be2aeb2f0",
  claudeTools: "a89a56c1122756d14562753ac73b834dfb70f6bfbb5cbeb73f8f880797078e58",
  loopSystem: "4e5e9b4c58db84aca0d83db501ee467ab287d45f362b791eb466adfcf0ca7e13",
  loopTools: "c88171030caeb62fe00bedd841b8713d3efe33c65e6067a536f96a27c5e1a32e",
};

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

function closedUrl() {
  return new Promise(resolve => {
    const server = http.createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(`http://127.0.0.1:${port}`));
    });
  });
}

// A vLLM serving one model, answering every chat request with "ok" and keeping the requests.
async function fakeVllm(t) {
  const requests = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      if (req.method === "GET" && req.url === "/v1/models") {
        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ object: "list", data: [{ id: VLLM_MODEL, object: "model" }] }));
      }
      try { requests.push(JSON.parse(Buffer.concat(chunks).toString("utf-8"))); } catch { requests.push(null); }
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: "ok" }, finish_reason: null }] })}\n\n`);
      res.write(`data: ${JSON.stringify({
        choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
      })}\n\n`);
      res.end("data: [DONE]\n\n");
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

async function startServer(t, env) {
  const temp = scratch(t, "youi-static-tmp-");
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
      OLLAMA_API_KEY: "",
      OLLAMA_LOCAL_BASE_URL: await closedUrl(),
      YOUI_COLLAB_HOST: join(temp, "no-collab-host"),
      TMPDIR: temp,
      ...env,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (!testServers.has(t)) testServers.set(t, []);
  testServers.get(t).push(child);
  t.after(() => stopServers(t));
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
  const page = `page-${label}-static-000000001`;
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
    Origin: origin,
    "X-YOUI-CSRF": csrf,
  };
  return async (message, chatMode) => {
    const response = await fetch(`${origin}/api/chat`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ message, chatMode }),
    });
    assert.equal(response.status, 200);
    return response.text();
  };
}

const sha256 = text => crypto.createHash("sha256").update(text).digest("hex");

// ── The pin ─────────────────────────────────────────────────────────

test("the system prompt and the tools are the bytes they were before weather and the mind modes, in any mode, switch on", async t => {
  const dir = scratch(t, "youi-static-");
  const love = join(dir, "love");
  const work = join(dir, "work");
  mkdirSync(love);
  mkdirSync(work);
  const log = join(dir, "claude.jsonl");
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
  const vllm = await fakeVllm(t);
  const port = await startServer(t, {
    LOVE_HOME: love,
    YOUI_WORKDIR: work,
    YOUI_CLAUDE_BIN: bin,
    YOUI_CLAUDE_CWD: join(dir, "claude-code-cwd"),
    OLLAMA_VLLM_BASE_URL: vllm.baseUrl,
    YOUI_CAPABILITIES: CAPABILITIES,
    // A build from before them ignores both; this one must still send the same bytes.
    YOUI_WEATHER_TO_AGENT: "on",
  });
  const claude = await session(port, "claude");
  const loop = await session(port, "loop", { model: VLLM_MODEL });
  const modes = ["direct", "fenhe"];
  for (const mode of modes) {
    await claude("FAKE:list", mode);
    await loop(`hello in ${mode}`, mode);
  }

  const normal = text => text
    .split(love).join("<love-home>")
    .split(work).join("<workdir>")
    .split(homedir()).join("<home>")
    .replace(`- Platform: ${process.platform}\n`, "- Platform: <platform>\n");
  const fromStatic = text => {
    const at = text.indexOf(STATIC_FROM);
    assert.ok(at > 0, "the static text follows the soul part");
    return normal(text.slice(at));
  };
  const entries = existsSync(log) ? readFileSync(log, "utf-8").split("\n").filter(Boolean).map(line => JSON.parse(line)) : [];
  const runs = entries.filter(entry => entry.step === "start");
  const lists = entries.filter(entry => entry.step === "tools-list");
  assert.equal(runs.length, modes.length);
  assert.equal(lists.length, modes.length);
  assert.equal(vllm.requests.length, modes.length);
  modes.forEach((mode, index) => {
    const system = vllm.requests[index].messages[0].content;
    const stable = system.slice(0, system.indexOf("\n\n---\n\n# This turn\n"));
    const seen = {
      claudeSystem: fromStatic(runs[index].systemPrompt.content),
      claudeTools: normal(JSON.stringify(lists[index].listed.body.result.tools)),
      loopSystem: fromStatic(stable),
      loopTools: normal(JSON.stringify(vllm.requests[index].tools)),
    };
    for (const [name, text] of Object.entries(seen)) {
      assert.equal(sha256(text), PINNED[name],
        `${mode}: ${name} is not the pinned bytes (${text.length} chars). A change here rewrites every conversation's`
        + " prompt cache (docs/YOUSPEAK.md §2.1). If it is meant, say so where the change is described and update PINNED.");
    }
  });
});
