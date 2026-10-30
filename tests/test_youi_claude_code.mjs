// YOUI reaches Claude through Claude Code (docs/YOUI-CLAUDE-CODE-DESIGN.md),
// slice C1: the runner, the loopback MCP bridge, and the direct route gone.
//
// Every `claude` here is tests/fixtures/fake-claude.mjs behind a wrapper; it
// talks to nothing but YOUI's own bridge on 127.0.0.1. No Keychain is read.

import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import {
  chmodSync,
  existsSync,
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
import test from "node:test";
import { claudeCodeArgs, claudeCodeChildEnv, prepareClaudeCwd, resolveClaudeBinary } from "../youi-web/claude-code.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const FAKE = join(REPO_ROOT, "tests/fixtures/fake-claude.mjs");
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// ── Helpers ─────────────────────────────────────────────────────────

function scratch(t, prefix) {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

/** A `claude` wrapper around the fake, logging to its own file. */
function fakeClaude(t, { version = "2.1.314" } = {}) {
  const dir = scratch(t, "youi-fake-claude-");
  const log = join(dir, "calls.jsonl");
  const bin = join(dir, "claude");
  writeFileSync(bin, [
    "#!/bin/sh",
    `FAKE_CLAUDE_LOG='${log}' FAKE_CLAUDE_VERSION='${version}' exec '${process.execPath}' '${FAKE}' "$@"`,
    "",
  ].join("\n"));
  chmodSync(bin, 0o755);
  return {
    bin,
    cwd: join(dir, "claude-code-cwd"),
    entries() {
      if (!existsSync(log)) return [];
      return readFileSync(log, "utf-8").split("\n").filter(Boolean).map(line => JSON.parse(line));
    },
    runs() {
      return this.entries().filter(entry => entry.step === "start");
    },
    step(name) {
      return this.entries().filter(entry => entry.step === name);
    },
  };
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

const SECRETS = {
  ANTHROPIC_API_KEY: "sk-ant-must-not-pass",
  ANTHROPIC_AUTH_TOKEN: "must-not-pass",
  CLAUDE_CODE_OAUTH_TOKEN: "must-not-pass",
  GITHUB_TOKEN: "must-not-pass",
  OLLAMA_API_KEY: "must-not-pass",
};

async function startServer(t, extraEnv = {}) {
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
      ...SECRETS,
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
  const credential = { id: created.json.session.id, token: created.json.sessionToken, page };
  return {
    origin,
    browser,
    credential,
    read: sessionHeaders(browser, credential),
    unsafe: sessionHeaders(browser, credential, { origin, unsafe: true }),
  };
}

function sseEvents(stream) {
  return stream.split("\n\n").filter(Boolean).map(chunk => ({
    event: chunk.match(/^event: (.+)$/m)?.[1],
    data: JSON.parse(chunk.match(/^data: (.+)$/m)?.[1] || "null"),
  }));
}

async function chat(session, message, extra = {}, { signal } = {}) {
  const response = await fetch(`${session.origin}/api/chat`, {
    method: "POST",
    headers: { ...session.unsafe, "Content-Type": "application/json" },
    body: JSON.stringify({ message, ...extra }),
    signal,
  });
  assert.equal(response.status, 200);
  return sseEvents(await response.text());
}

function flagValue(argv, name) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : undefined;
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
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function bridgeCall(mcp, body, headers = {}) {
  const url = new URL(mcp.url);
  return request(Number(url.port), {
    path: url.pathname,
    method: "POST",
    headers: { ...mcp.headers, ...headers },
    body,
  });
}

// ── The runner on its own ───────────────────────────────────────────

test("the runner finds only an explicit fake in test mode, and keeps credentials and settings out", t => {
  assert.equal(resolveClaudeBinary({ env: { PATH: process.env.PATH }, testMode: true }).path, null,
    "test mode never resolves the installed claude on PATH");
  assert.match(resolveClaudeBinary({ env: { YOUI_CLAUDE_BIN: "/nonexistent/claude" } }).error, /not an executable/);

  const env = claudeCodeChildEnv({
    PATH: "/usr/bin", HOME: "/Users/someone", ...SECRETS, AWS_SECRET_ACCESS_KEY: "x", LANG: "en_GB.UTF-8",
  });
  assert.deepEqual(Object.keys(env).sort(), [
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_CODE_DISABLE_CLAUDE_MDS", "HOME", "LANG", "PATH",
  ]);

  const args = claudeCodeArgs({ model: "claude-haiku-4-5-20251001", systemPromptFile: "/p", persist: false });
  assert.ok(args.includes("--no-session-persistence"));
  assert.ok(!args.includes("--effort"), "no effort for a model that takes none");
  assert.ok(!args.includes("--mcp-config") && !args.includes("--allowedTools"));

  const dir = scratch(t, "youi-claude-cwd-");
  writeFileSync(join(dir, "CLAUDE.md"), "planted");
  assert.throws(() => prepareClaudeCwd(dir), /must stay empty/);
});

// ── A chat turn through Claude Code ─────────────────────────────────

test("a Claude turn runs Claude Code with YOUI's flags, from its own empty directory, with no credentials", async t => {
  const fake = fakeClaude(t);
  const port = await startServer(t, { YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd });
  const session = await openSession(port, "claude-flags");

  const events = await chat(session, "hello there");
  const names = events.map(e => e.event);
  assert.ok(names.includes("text_delta"), "partial text streams to the page");
  assert.equal(events.find(e => e.event === "text_done").data.content, "Echo: hello there");
  assert.ok(events.find(e => e.event === "text_done").data.youspeak, "YOUSPEAK still measures the text");
  const usage = events.find(e => e.event === "usage").data;
  assert.equal(usage.provider, "claude");
  assert.equal(usage.model, "claude-opus-5-5");
  assert.deepEqual([usage.input_tokens, usage.output_tokens, usage.thinking_tokens], [1110, 20, 7]);
  assert.equal(usage.budget.managedBy, "Claude Code");
  assert.equal(usage.budget.fiveHour, null, "no utilisation YOUI cannot see");
  assert.equal(names.at(-1), "done");
  assert.ok(!names.includes("error"));

  const [first] = fake.runs();
  const argv = first.argv;
  assert.deepEqual(argv.slice(0, 7), [
    "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
  ]);
  assert.equal(flagValue(argv, "--model"), "claude-opus-5-5");
  assert.equal(flagValue(argv, "--effort"), "high", "Alpha's default effort");
  assert.equal(flagValue(argv, "--setting-sources"), "project");
  assert.equal(flagValue(argv, "--system-prompt-snapshot"), "off");
  assert.ok(argv.includes("--strict-mcp-config"));
  assert.equal(flagValue(argv, "--tools"), "", "Claude Code's built-in tools are off");
  assert.equal(flagValue(argv, "--allowedTools"), "mcp__youi");
  assert.equal(flagValue(argv, "--permission-prompts"), "none");
  const claudeSession = flagValue(argv, "--session-id");
  assert.match(claudeSession, UUID);
  assert.ok(!argv.includes("--resume"));
  assert.ok(!argv.includes("--system-prompt"), "the prompt goes in a file, never on the command line");
  assert.ok(!argv.some(arg => /claude-cli|2\.1\.92/.test(arg)), "no client version is ever passed");

  // Its own working directory: created 0700 and still empty.
  assert.equal(first.cwd, fake.cwd);
  assert.equal(statSync(fake.cwd).mode & 0o777, 0o700);
  assert.deepEqual(readdirSync(fake.cwd), []);

  // No credential crosses; its own login is found through HOME.
  for (const name of Object.keys(SECRETS)) assert.equal(first.env[name], undefined, `${name} must not reach Claude Code`);
  assert.equal(first.env.CLAUDE_CODE_DISABLE_AUTO_MEMORY, "1");
  assert.equal(first.env.CLAUDE_CODE_DISABLE_CLAUDE_MDS, "1");
  assert.equal(first.env.HOME, process.env.HOME);
  assert.ok(first.env.PATH);

  // The prompt and the bridge's token were 0600 files, gone after the turn.
  assert.equal(first.systemPrompt.mode, 0o600);
  assert.match(first.systemPrompt.content, /# Environment/);
  assert.match(first.systemPrompt.content, /named mcp__youi__<name>/);
  assert.doesNotMatch(first.systemPrompt.content, /billing-header|cc_entrypoint/);
  assert.equal(first.mcp.mode, 0o600);
  const config = JSON.parse(first.mcp.content);
  assert.deepEqual(Object.keys(config.mcpServers), ["youi"]);
  assert.equal(config.mcpServers.youi.type, "http");
  assert.match(config.mcpServers.youi.url, new RegExp(`^http://127\\.0\\.0\\.1:${port}/mcp/[0-9a-f]{32}$`));
  assert.match(config.mcpServers.youi.headers.Authorization, /^Bearer [A-Za-z0-9_-]{40,}$/);
  assert.equal(existsSync(first.systemPrompt.path), false);
  assert.equal(existsSync(first.mcp.path), false);

  // One stream-json user line on stdin; stdin closes after the result.
  const [sent] = fake.step("message");
  assert.deepEqual(sent.line, { type: "user", message: { role: "user", content: "hello there" }, parent_tool_use_id: null });
  await until(() => fake.step("stdin-closed").length === 1, { label: "stdin to close" });

  // The next turn resumes the same Claude Code session; /api/clear starts another.
  await chat(session, "again");
  const second = fake.runs()[1].argv;
  assert.equal(flagValue(second, "--resume"), claudeSession);
  assert.ok(!second.includes("--session-id"));
  const current = await request(port, { path: "/api/sessions/current?include=messages", headers: session.read });
  assert.deepEqual(current.json.session.messages, [
    { role: "user", content: "hello there" },
    { role: "assistant", content: [{ type: "text", text: "Echo: hello there" }] },
    { role: "user", content: "again" },
    { role: "assistant", content: [{ type: "text", text: "Echo: again" }] },
  ]);
  const cleared = await request(port, { path: "/api/clear", method: "POST", headers: session.unsafe });
  assert.equal(cleared.status, 200);
  await chat(session, "fresh start");
  const third = fake.runs()[2].argv;
  assert.match(flagValue(third, "--session-id"), UUID);
  assert.notEqual(flagValue(third, "--session-id"), claudeSession);

  // Status says how Claude is reached, and that the budget is Claude Code's.
  const status = await request(port, { path: "/api/status", headers: session.read });
  assert.equal(status.json.claudeCode.available, true);
  assert.equal(status.json.claudeCode.source, "YOUI_CLAUDE_BIN");
  assert.equal(status.json.privacy.direct.route, "REMOTE_MODEL:anthropic");
  assert.match(status.json.privacy.direct.destination, /Claude Code/);
});

test("raw mode gives Claude Code no YOUI prompt, no tools and no saved session", async t => {
  const fake = fakeClaude(t);
  const port = await startServer(t, { YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd });
  const session = await openSession(port, "claude-raw");
  const events = await chat(session, "just you", { raw: true });
  assert.match(events.find(e => e.event === "raw_mode").data.note, /not kept by Claude Code/);
  assert.equal(events.find(e => e.event === "text_done").data.content, "Echo: just you");
  const [run] = fake.runs();
  assert.ok(run.argv.includes("--no-session-persistence"));
  for (const flag of ["--mcp-config", "--allowedTools", "--session-id", "--resume"]) {
    assert.ok(!run.argv.includes(flag), `raw mode passes no ${flag}`);
  }
  assert.equal(flagValue(run.argv, "--tools"), "");
  assert.equal(run.systemPrompt.content, "You are Claude.");
  const current = await request(port, { path: "/api/sessions/current?include=messages", headers: session.read });
  assert.deepEqual(current.json.session.messages, [], "raw mode leaves the history untouched");
});

// ── The bridge ──────────────────────────────────────────────────────

test("the bridge lists exactly the session's tools and runs a call inside that session", async t => {
  const fake = fakeClaude(t);
  const port = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin,
    YOUI_CLAUDE_CWD: fake.cwd,
    YOUI_CAPABILITIES: "chat,status:read,sessions:manage,settings:write,models:use,tools:filesystem:read",
  });
  const session = await openSession(port, "bridge-tools");
  const other = await openSession(port, "bridge-other");

  const listed = await chat(session, "FAKE:list");
  assert.equal(listed.find(e => e.event === "text_done").data.content, "Tools: read_file,glob,grep,ollama");
  const [connect] = fake.step("bridge-connect");
  assert.equal(connect.discover.body.error.code, -32601, "the server/discover probe falls back");
  assert.equal(connect.init.body.result.protocolVersion, "2025-11-25");
  assert.deepEqual(connect.init.body.result.serverInfo, { name: "youi", version: "1.0.0" });
  assert.equal(connect.initialized, 202);
  const [list] = fake.step("tools-list");
  const readFile = list.listed.body.result.tools.find(tool => tool.name === "read_file");
  assert.deepEqual(readFile.inputSchema.required, ["path"]);

  const called = await chat(session, 'FAKE:call read_file {"path":"CLAUDE.md","limit":1}\nFAKE:call bash {"command":"echo hi"}');
  const toolCalls = called.filter(e => e.event === "tool_call").map(e => e.data);
  assert.deepEqual(toolCalls.map(call => [call.id, call.name]), [["toolu_fake_1", "read_file"], ["toolu_fake_2", "bash"]]);
  assert.deepEqual(toolCalls[0].input, { path: "CLAUDE.md", limit: 1 });
  const results = called.filter(e => e.event === "tool_result").map(e => e.data);
  assert.equal(results[0].id, "toolu_fake_1");
  assert.match(results[0].result, /^1\t# love-unlimited/);
  const [readCall, bashCall] = fake.step("tools-call");
  assert.equal(readCall.called.body.result.isError, false);
  assert.match(readCall.called.body.result.content[0].text, /# love-unlimited/);
  assert.equal(bashCall.called.body.result.isError, true);
  assert.match(bashCall.called.body.result.content[0].text, /Permission denied: tool "bash" is not enabled/);
  assert.ok(called.findIndex(e => e.event === "tool_call") < called.findIndex(e => e.event === "tool_executing"));

  // The calls counted against the session that made them, not the server or another session.
  const mine = await request(port, { path: "/api/status", headers: session.read });
  const theirs = await request(port, { path: "/api/status", headers: other.read });
  assert.equal(mine.json.totalToolCalls, 1, "only the enabled tool ran, in this session's scope");
  assert.equal(theirs.json.totalToolCalls, 0);

  // A session without the filesystem grant is offered less.
  const narrow = await startServer(t, {
    YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd, YOUI_CAPABILITIES: "chat,sessions:manage,models:use",
  });
  const narrowSession = await openSession(narrow, "bridge-narrow");
  const narrowList = await chat(narrowSession, "FAKE:list");
  assert.equal(narrowList.find(e => e.event === "text_done").data.content, "Tools: ollama");
});

test("the bridge refuses browsers, wrong tokens and ended turns, and an abort ends Claude Code", async t => {
  const fake = fakeClaude(t);
  const port = await startServer(t, { YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd });
  const session = await openSession(port, "bridge-guard");

  const controller = new AbortController();
  const pending = chat(session, "FAKE:sleep", {}, { signal: controller.signal }).catch(error => error);
  const sleeping = await until(() => fake.step("sleeping")[0], { label: "the fake to start" });
  const run = fake.runs().find(entry => entry.pid === sleeping.pid);
  const mcp = JSON.parse(run.mcp.content).mcpServers.youi;
  const list = { jsonrpc: "2.0", id: 1, method: "tools/list" };

  const good = await bridgeCall(mcp, list);
  assert.equal(good.status, 200);
  assert.ok(good.json.result.tools.length > 0);
  const wrong = await bridgeCall(mcp, list, { Authorization: "Bearer wrong-token-wrong-token-wrong-token" });
  assert.equal(wrong.status, 401);
  const none = await bridgeCall(mcp, list, { Authorization: "" });
  assert.equal(none.status, 401);
  const otherTurn = await bridgeCall({ ...mcp, url: mcp.url.replace(/[0-9a-f]{32}$/, "0".repeat(32)) }, list);
  assert.equal(otherTurn.status, 401);
  const browser = await bridgeCall(mcp, list, { Origin: `http://127.0.0.1:${port}` });
  assert.equal(browser.status, 403);
  const cookie = await bridgeCall(mcp, list, { Cookie: session.browser.header });
  assert.equal(cookie.status, 403);
  const url = new URL(mcp.url);
  const get = await request(port, { path: url.pathname, headers: mcp.headers });
  assert.equal(get.status, 405);
  assert.equal(get.headers.allow, "POST");
  const unknown = await bridgeCall(mcp, { jsonrpc: "2.0", id: 2, method: "resources/list" });
  assert.equal(unknown.json.error.code, -32601);
  const ping = await bridgeCall(mcp, { jsonrpc: "2.0", id: 3, method: "ping" });
  assert.deepEqual(ping.json.result, {});
  const parse = await bridgeCall(mcp, "{not json");
  assert.equal(parse.status, 400);
  assert.equal(parse.json.error.code, -32700);
  const badHost = await request(port, { path: url.pathname, method: "POST", headers: { ...mcp.headers, Host: "evil.example" }, body: list });
  assert.equal(badHost.status, 421, "the server's Host check comes first");
  // The browser's own session credentials open nothing here.
  const { Cookie: _cookie, ...sessionOnly } = session.read;
  const viaSession = await request(port, { path: url.pathname, method: "POST", headers: sessionOnly, body: list });
  assert.equal(viaSession.status, 401);

  controller.abort();
  await pending;
  await until(() => !alive(sleeping.pid), { label: "the fake to be killed" });
  const after = await bridgeCall(mcp, list);
  assert.equal(after.status, 401, "the token ends with its turn");

  // The session is free again.
  const next = await chat(session, "still here");
  assert.equal(next.find(e => e.event === "text_done").data.content, "Echo: still here");
});

test("an error result, a bridge that never connects and a missing Claude Code become clear errors", async t => {
  const fake = fakeClaude(t);
  const port = await startServer(t, { YOUI_CLAUDE_BIN: fake.bin, YOUI_CLAUDE_CWD: fake.cwd });
  const session = await openSession(port, "claude-errors");

  const failed = await chat(session, "FAKE:error Claude Code 2.1.92 does not support this model");
  const error = failed.find(e => e.event === "error");
  assert.equal(error.data.message, "Claude Code: Claude Code 2.1.92 does not support this model");
  assert.equal(failed.at(-1).event, "done");

  const thought = await chat(session, "FAKE:thinking weighing it up");
  assert.equal(thought.find(e => e.event === "thinking").data.content, "weighing it up");

  const started = Date.now();
  const down = await chat(session, "FAKE:bridge-down");
  assert.match(down.find(e => e.event === "error").data.message, /could not connect to YOUI's tool bridge \(status failed\)/);
  assert.ok(Date.now() - started < 10_000, "the run is stopped, not waited out");
  const downRun = fake.runs().at(-1);
  await until(() => !alive(downRun.pid), { label: "the stopped fake to exit" });

  const missing = await startServer(t, { YOUI_CLAUDE_BIN: "" });
  const lone = await openSession(missing, "claude-missing");
  const none = await chat(lone, "hello");
  assert.match(none.find(e => e.event === "error").data.message, /through Claude Code, which is not available: YOUI_TEST=1 never runs the installed Claude Code/);
});

// ── What is gone ────────────────────────────────────────────────────

test("YOUI Web never reads the Keychain, sends Claude Code's headers or calls Anthropic itself", () => {
  const dir = join(REPO_ROOT, "youi-web");
  const files = [
    ...readdirSync(dir).filter(name => name.endsWith(".mjs")).map(name => join(dir, name)),
    join(dir, "public/index.html"),
  ];
  const forbidden = [
    /youi-keychain/, /KeychainCredentialStore/, /find-generic-password/,
    /api\.anthropic\.com/, /platform\.claude\.com/, /\bAPI_URL\b/,
    /claude-cli\//, /["']x-app["']/i, /anthropic-beta/i, /X-Claude-Code-Session-Id/i,
    /oauth-2025-04-20/, /claude-code-20250219/, /billing-header/, /parseBudgetHeaders/,
    /refreshOAuthToken|getAccessToken|readKeychainCredential/,
  ];
  for (const file of files) {
    const text = readFileSync(file, "utf-8");
    for (const pattern of forbidden) {
      assert.doesNotMatch(text, pattern, `${file.slice(REPO_ROOT.length)} must not contain ${pattern}`);
    }
  }
});
