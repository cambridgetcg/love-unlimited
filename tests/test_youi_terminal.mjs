// The YOUI terminal (youi.mjs) on the real Claude Code (docs/YOUI-CLAUDE-CODE-DESIGN.md §4):
// its loopback tool bridge and fail-closed version gate (youi-claude-bridge.mjs),
// its tool profiles through the bridge, and its exit paths.
//
// Every `claude` here is tests/fixtures/fake-claude.mjs behind a wrapper; it
// talks to nothing but the terminal's own bridge on 127.0.0.1. No Keychain is
// read and no model is called.

import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { createMcpBridge } from "../youi-web/mcp-bridge.mjs";
import { createVersionGate, startBridgeListener } from "../youi-claude-bridge.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const FAKE = join(REPO_ROOT, "tests/fixtures/fake-claude.mjs");
const SOURCE = readFileSync(join(REPO_ROOT, "youi.mjs"), "utf-8");

// ── Helpers ─────────────────────────────────────────────────────────

const terminals = new WeakMap();

function scratch(t, prefix) {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  t.after(async () => {
    await Promise.all((terminals.get(t) || []).map(child => new Promise(resolve => {
      if (child.exitCode !== null || child.signalCode !== null) return resolve();
      child.once("exit", resolve);
      child.kill("SIGKILL");
    })));
    rmSync(dir, { recursive: true, force: true });
  });
  return dir;
}

/** A `claude` wrapper around the fake (node itself, so the environment it records is the terminal's). */
function fakeClaude(dir, { version = "2.1.314" } = {}) {
  const log = join(dir, "calls.jsonl");
  const sessions = join(dir, "sessions");
  const bin = join(dir, "claude");
  writeFileSync(bin, [
    `#!${process.execPath}`,
    `process.env.FAKE_CLAUDE_LOG = ${JSON.stringify(log)};`,
    `process.env.FAKE_CLAUDE_VERSION = ${JSON.stringify(version)};`,
    `process.env.FAKE_CLAUDE_SESSIONS = ${JSON.stringify(sessions)};`,
    `import(${JSON.stringify(pathToFileURL(FAKE).href)});`,
    "",
  ].join("\n"));
  chmodSync(bin, 0o755);
  return {
    bin,
    sessions,
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

const SECRETS = {
  ANTHROPIC_API_KEY: "sk-ant-must-not-pass",
  CLAUDE_CODE_OAUTH_TOKEN: "must-not-pass",
  GITHUB_TOKEN: "must-not-pass",
  // One no credential-name pattern catches: only the allowlist keeps it out.
  STRIPE_SECRET_KEY: "sk_live_must-not-pass",
};

const plain = text => text.replace(/\x1b\[[0-9;]*[A-Za-z]/g, "").replace(/\r/g, "\n");

/**
 * youi.mjs on a fake Claude Code, with its own HOME, soul directory, working
 * directory (holding a.txt), Claude Code working directory and YOUSPEAK store.
 */
function startTerminal(t, { args = [], env = {}, version } = {}) {
  const dir = scratch(t, "youi-terminal-");
  const fake = fakeClaude(dir, { version });
  const workdir = join(dir, "work");
  mkdirSync(workdir);
  writeFileSync(join(workdir, "a.txt"), "alpha line\n");
  writeFileSync(join(dir, "outside.txt"), "outside the workspace\n");
  const historyPath = join(dir, "history.jsonl");
  const child = spawn(process.execPath, [join(REPO_ROOT, "youi.mjs"), "--soul-dir", join(dir, "soul"), "--workdir", workdir, ...args], {
    cwd: dir,
    env: {
      PATH: process.env.PATH,
      HOME: dir,
      YOUI_TEST: "1",
      YOUI_CLAUDE_BIN: fake.bin,
      YOUI_CLAUDE_CWD: join(dir, "claude-code-cwd"),
      YOUSPEAK_HISTORY_PATH: historyPath,
      ...SECRETS,
      ...env,
    },
    stdio: ["pipe", "pipe", "pipe"],
  });
  terminals.set(t, [...(terminals.get(t) || []), child]);
  let output = "";
  child.stdout.on("data", chunk => { output += chunk; });
  child.stderr.on("data", chunk => { output += chunk; });
  const exited = new Promise(resolve => child.once("exit", (code, signal) => resolve({ code, signal })));
  return {
    dir,
    fake,
    child,
    exited,
    output: () => plain(output),
    send(line) {
      child.stdin.write(`${line}\n`);
    },
    async waitFor(pattern, { timeoutMs = 15_000 } = {}) {
      const started = Date.now();
      while (!pattern.test(plain(output))) {
        if (Date.now() - started > timeoutMs) assert.fail(`timed out waiting for ${pattern}; output:\n${plain(output)}`);
        if (child.exitCode !== null) assert.fail(`the terminal exited (${child.exitCode}) before ${pattern}; output:\n${plain(output)}`);
        await new Promise(resolve => setTimeout(resolve, 25));
      }
    },
    rows() {
      if (!existsSync(historyPath)) return [];
      return readFileSync(historyPath, "utf-8").split("\n").filter(Boolean).map(line => JSON.parse(line));
    },
  };
}

// The prompt the terminal prints once it is ready for the next line.
const READY = /Alpha › $/;
async function ask(terminal, line, done = /\[\d+\] \d+in \d+out/g) {
  const before = (terminal.output().match(done) || []).length;
  terminal.send(line);
  const started = Date.now();
  while ((terminal.output().match(done) || []).length <= before) {
    if (Date.now() - started > 15_000) assert.fail(`no answer to ${line}; output:\n${terminal.output()}`);
    if (terminal.child.exitCode !== null) assert.fail(`the terminal exited before answering ${line}:\n${terminal.output()}`);
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

async function gone(pid, timeoutMs = 5_000) {
  const started = Date.now();
  while (alive(pid)) {
    if (Date.now() - started > timeoutMs) return false;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  return true;
}

function bridgeRequest(port, { path, headers = {}, body = { jsonrpc: "2.0", id: 1, method: "ping" } }) {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body));
    const req = http.request({
      hostname: "127.0.0.1",
      port,
      path,
      method: "POST",
      headers: { Host: `127.0.0.1:${port}`, "Content-Type": "application/json", "Content-Length": payload.length, ...headers },
    }, res => {
      const chunks = [];
      res.on("data", chunk => chunks.push(chunk));
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf-8");
        let json = null;
        try { json = JSON.parse(text); } catch {}
        resolve({ status: res.statusCode, json });
      });
    });
    req.on("error", reject);
    req.end(payload);
  });
}

// ── The source: nothing of the old direct route is left ─────────────

test("youi.mjs reads no Keychain or OAuth token and sends no Claude Code headers of its own", () => {
  const gone = [
    /keychain/i, /oauth/i, /refresh_token|refreshToken/, /device_id/, /billing-header|cc_version|cc_entrypoint/,
    /x-app/i, /claude-cli\//, /2\.1\.92/, /anthropic-beta|anthropic-version/i, /claude-code-20250219|oauth-2025-04-20/,
    /api\.anthropic\.com|platform\.claude\.com/, /X-Claude-Code-Session-Id|x-client-request-id/i, /\bfetch\(/,
    /9d1c250a/, /terminal-legacy/,
  ];
  // --inspect names the other files that still use Claude Code's login, among
  // them youi-keychain.mjs and oauth_client.py. That list is string literals
  // only, so the check reads the source without it.
  const stillUsedBy = /\n {6}still_used_by: \[(?:\s*"[^"\n]*",)+\s*\],/;
  assert.match(SOURCE, stillUsedBy, "--inspect names who still uses Claude Code's login");
  const code = SOURCE.replace(stillUsedBy, "");
  for (const pattern of gone) assert.doesNotMatch(code, pattern, `youi.mjs still has ${pattern}`);
  // It goes through the shared runner and models table instead, on YOUSPEAK's claude-code route.
  assert.match(SOURCE, /from "\.\/youi-web\/claude-code\.mjs"/);
  assert.match(SOURCE, /from "\.\/youi-web\/claude-models\.mjs"/);
  assert.match(SOURCE, /createKernel\(\{ agent, route: "claude-code", surface: "terminal"/);
  assert.match(SOURCE, /ys\.decide\(state\.effort, model, kernelBudget\(\), \{ route: "claude-code" \}\)/);
  assert.match(SOURCE, /defaultModel: "claude-opus-5-5",\n    defaultEffort: "high"/, "Alpha is on Opus 5.5 at high, as in YOUI Web");
});

// ── The version gate ────────────────────────────────────────────────

test("the version gate fails closed: unreadable or too old is refused, and a failed read is not kept", async () => {
  const reads = [];
  let answer = { ok: false, version: null, error: "no answer within 3000 ms" };
  let clock = 0;
  const gate = createVersionGate({
    bin: "/nowhere/claude",
    read: () => { reads.push(clock); return Promise.resolve(answer); },
    now: () => clock,
    ttlMs: 60_000,
  });

  const unreadable = await gate.check("claude-opus-5-5");
  assert.equal(unreadable.ok, false);
  assert.match(unreadable.error, /could not read the Claude Code version \(no answer within 3000 ms\), so it will not run claude-opus-5-5/);
  assert.equal(unreadable.minimum, "2.1.299");

  // Not kept: the next check reads again, at once.
  answer = { ok: true, version: "2.1.298", error: null };
  const old = await gate.check("claude-haiku-4-5-20251001");
  assert.equal(reads.length, 2);
  assert.equal(old.ok, false);
  assert.match(old.error, /Claude Code 2\.1\.298 is older than 2\.1\.299/);

  // A good read is kept for the minute: the version changing within it is not seen.
  clock = 60_000;
  answer = { ok: true, version: "2.1.299", error: null };
  assert.equal((await gate.check("claude-opus-5-5")).ok, true);
  answer = { ok: true, version: "2.1.200", error: null };
  clock = 119_999;
  assert.equal((await gate.check("claude-opus-5-5")).ok, true);
  assert.equal(reads.length, 3);
  clock = 120_000;
  assert.equal((await gate.check("claude-opus-5-5")).ok, false, "read again after the minute");

  // A pre-release of the floor does not meet it; a throwing reader is unreadable.
  const pre = createVersionGate({ read: () => ({ ok: true, version: "2.1.299-beta.1", error: null }) });
  assert.equal((await pre.check("claude-sonnet-4-6")).ok, false);
  const throwing = createVersionGate({ read: () => { throw new Error("spawn failed"); } });
  assert.match((await throwing.check("claude-sonnet-4-6")).error, /spawn failed/);
});

test("the terminal will not start on a Claude Code below the floor, nor without one", async t => {
  const old = startTerminal(t, { version: "2.1.298" });
  assert.deepEqual(await old.exited, { code: 1, signal: null });
  assert.match(old.output(), /Fatal: Claude Code 2\.1\.298 is older than 2\.1\.299, which claude-opus-5-5 needs through YOUI/);
  assert.equal(old.fake.runs().length, 0, "no turn was run");
  assert.equal(old.fake.step("version").length, 1);

  const missing = startTerminal(t, { env: { YOUI_CLAUDE_BIN: "" } });
  assert.deepEqual(await missing.exited, { code: 1, signal: null });
  assert.match(missing.output(), /Fatal: YOUI_TEST=1 never runs the installed Claude Code/);
});

// ── The loopback bridge listener ────────────────────────────────────

test("the bridge listener is loopback only and refuses a wrong bearer, Host or Origin", async t => {
  const calls = [];
  const bridge = createMcpBridge({
    listTools: () => [{ name: "read_file", description: "Read", input_schema: { type: "object", properties: {} } }],
    callTool: (context, name) => { calls.push(name); return { text: "ok", isError: false }; },
  });
  const listener = await startBridgeListener(bridge);
  t.after(() => listener.close());
  const turn = bridge.open({});
  const auth = { Authorization: `Bearer ${turn.token}` };
  const port = listener.port;
  assert.equal(listener.url(turn.path), `http://127.0.0.1:${port}${turn.path}`);

  const good = await bridgeRequest(port, { path: turn.path, headers: auth, body: { jsonrpc: "2.0", id: 1, method: "tools/list" } });
  assert.equal(good.status, 200);
  assert.deepEqual(good.json.result.tools.map(tool => tool.name), ["read_file"]);

  const wrongToken = await bridgeRequest(port, { path: turn.path, headers: { Authorization: `Bearer ${"x".repeat(43)}` } });
  assert.equal(wrongToken.status, 401);
  const noToken = await bridgeRequest(port, { path: turn.path });
  assert.equal(noToken.status, 401);
  const otherTurn = await bridgeRequest(port, { path: `/mcp/${"0".repeat(32)}`, headers: auth });
  assert.equal(otherTurn.status, 401, "a token is bound to its own turn");

  for (const host of ["evil.example", `evil.example:${port}`, `127.0.0.1:${port + 1}`, `[::1]:${port + 1}`]) {
    const refused = await bridgeRequest(port, { path: turn.path, headers: { ...auth, Host: host } });
    assert.equal(refused.status, 421, `Host "${host}" is refused`);
  }
  for (const localHost of [`localhost:${port}`, `127.0.0.1:${port}`]) {
    assert.equal((await bridgeRequest(port, { path: turn.path, headers: { ...auth, Host: localHost } })).status, 200);
  }

  const browser = await bridgeRequest(port, { path: turn.path, headers: { ...auth, Origin: `http://127.0.0.1:${port}` } });
  assert.equal(browser.status, 403);
  assert.equal(browser.json.code, "bridge_browser_refused");
  const cookie = await bridgeRequest(port, { path: turn.path, headers: { ...auth, Cookie: "a=b" } });
  assert.equal(cookie.status, 403);
  assert.equal((await bridgeRequest(port, { path: "/api/status", headers: auth })).status, 404, "nothing but the bridge is served");

  // A closed turn's token stops working.
  bridge.close(turn.id);
  assert.equal((await bridgeRequest(port, { path: turn.path, headers: auth })).status, 401);
  assert.deepEqual(calls, []);
});

// ── A conversation through Claude Code ──────────────────────────────

test("a message runs claude -p on the session's own conversation, with the context block and the reported usage", async t => {
  const terminal = startTerminal(t);
  await terminal.waitFor(READY);
  assert.match(terminal.output(), /via Claude Code 2\.1\.314 \| Model: claude-opus-5-5/);

  await ask(terminal, "hello there 你好，我哋傾吓偈");
  assert.match(terminal.output(), /Echo: hello there 你好，我哋傾吓偈/);
  await ask(terminal, "second message");

  const runs = terminal.fake.runs();
  assert.equal(runs.length, 2);
  const flag = (run, name) => run.argv[run.argv.indexOf(name) + 1];
  assert.equal(flag(runs[0], "--model"), "claude-opus-5-5");
  assert.equal(flag(runs[0], "--effort"), "high");
  assert.equal(flag(runs[0], "--max-turns"), "50");
  const sessionId = flag(runs[0], "--session-id");
  assert.match(sessionId, /^[0-9a-f-]{36}$/);
  assert.equal(flag(runs[1], "--resume"), sessionId, "the second message resumes the same conversation");
  assert.equal(runs[0].cwd, join(terminal.dir, "claude-code-cwd"));

  // Claude Code's environment: its off switches, no credential of any kind.
  for (const run of runs) {
    assert.equal(run.env.CLAUDE_CODE_DISABLE_FILE_MENTIONS, "1");
    assert.equal(run.env.CLAUDE_CODE_DISABLE_CLAUDE_MDS, "1");
    assert.equal(run.env.HOME, terminal.dir);
    for (const name of Object.keys(SECRETS)) assert.equal(run.env[name], undefined, `${name} must not reach Claude Code`);
  }
  // The bridge is on loopback; the prompt carries no billing line and stays the same between messages.
  const server = JSON.parse(runs[0].mcp.content).mcpServers.youi;
  assert.match(server.url, /^http:\/\/127\.0\.0\.1:\d+\/mcp\/[0-9a-f]{32}$/);
  assert.equal(runs[0].mcp.mode, 0o600);
  assert.doesNotMatch(runs[0].systemPrompt.content, /billing|cc_version|Date:/);
  assert.equal(runs[0].systemPrompt.content, runs[1].systemPrompt.content);
  const messages = terminal.fake.step("message");
  assert.match(messages[0].context, /^\[YOUI context\]\n[\s\S]*- Model: claude-opus-5-5\n- Effort: high[\s\S]*\[\/YOUI context\]\n\n$/);
  assert.equal(messages[0].message, "hello there 你好，我哋傾吓偈");

  terminal.send("/exit");
  assert.deepEqual(await terminal.exited, { code: 0, signal: null });
  const [row] = terminal.rows();
  assert.equal(row.route, "claude-code");
  assert.equal(row.model, "claude-opus-5-5");
  assert.equal(row.endReason, "exit");
  assert.deepEqual(row.exchanges, { ok: 2, failed: 0 });
  assert.equal(row.costUsd, 0.0246, "the fake's two turns, as Claude Code reported them");
  assert.equal(row.evidence.costUsd, "reported");
  assert.equal(row.evidence.inputTokens, "reported");
  assert.equal(row.evidence.contextPeakTokens, "reported");
  assert.equal(row.fiveHourUtilization, 0.25);
  assert.equal(row.promptChanges, 0);
});

test("/model maps to the models YOUI offers, /switch starts a new conversation, and a lost one is carried, said first", async t => {
  const terminal = startTerminal(t);
  await terminal.waitFor(READY);
  terminal.send("/model opus-4-7");
  await terminal.waitFor(/Model: claude-opus-4-7/);
  await ask(terminal, "on opus 4.7");
  terminal.send("/model haiku");
  await terminal.waitFor(/Model: claude-haiku-4-5-20251001/);
  await ask(terminal, "on haiku");
  terminal.send("/model gpt");
  await terminal.waitFor(/Usage: \/model opus\|opus-4-7\|opus-4-6\|sonnet\|haiku/);

  // Claude Code loses the conversation: a new one starts, carrying the text, said before it is sent.
  rmSync(terminal.fake.sessions, { recursive: true, force: true });
  await ask(terminal, "after the loss");
  assert.match(terminal.output(), /Claude Code had no record of this conversation\. Starting a new Claude conversation, carrying 4 earlier messages into it as text; they go to Anthropic, and Claude Code stores them\./);

  terminal.send("/switch gamma");
  await terminal.waitFor(/Switched to Gamma/);
  await terminal.waitFor(/Gamma › $/);
  await ask(terminal, "as gamma");

  const runs = terminal.fake.runs();
  const flag = (run, name) => (run.argv.includes(name) ? run.argv[run.argv.indexOf(name) + 1] : null);
  assert.equal(flag(runs[0], "--model"), "claude-opus-4-7");
  assert.equal(flag(runs[1], "--model"), "claude-haiku-4-5-20251001");
  assert.equal(flag(runs[1], "--effort"), null, "Haiku takes no effort");
  assert.equal(flag(runs[2], "--resume"), flag(runs[0], "--session-id"), "the lost conversation was resumed first");
  const fresh = flag(runs[3], "--session-id");
  assert.ok(fresh && fresh !== flag(runs[0], "--session-id"), "then a new one started");
  const carried = terminal.fake.step("message").at(-2).message;
  assert.match(carried, /^\[YOUI [0-9a-f]{16}\] This is a new Claude conversation/);
  assert.match(carried, /on opus 4\.7[\s\S]*Echo: on haiku[\s\S]*after the loss$/);
  const gamma = runs.at(-1);
  assert.equal(flag(gamma, "--model"), "claude-sonnet-4-6");
  assert.ok(flag(gamma, "--session-id") && flag(gamma, "--session-id") !== fresh, "/switch starts a new conversation");
});

// ── Tool profiles through the bridge ────────────────────────────────

test("the observe profile lists and runs only the read tools, scoped to the workspace", async t => {
  const terminal = startTerminal(t);
  await terminal.waitFor(READY);
  await ask(terminal, "FAKE:list");
  assert.match(terminal.output(), /Tools: read_file,glob,grep\n/);
  await ask(terminal, 'FAKE:call read_file {"path":"a.txt"}');
  assert.match(terminal.output(), /▶ read_file a\.txt/);
  assert.match(terminal.output(), /Tool said: read_file: 1\talpha line/);
  await ask(terminal, 'FAKE:call read_file {"path":"../outside.txt"}');
  assert.match(terminal.output(), /Tool said: read_file \(error\): Error: /);
  assert.doesNotMatch(terminal.output(), /outside the workspace/);
  await ask(terminal, 'FAKE:call bash {"command":"echo should-not-run"}');
  assert.match(terminal.output(), /bash \(error\): Permission denied: tool "bash" is not enabled for this session \(profile observe\)/);
  await ask(terminal, 'FAKE:call write_file {"path":"new.txt","content":"x"}');
  assert.match(terminal.output(), /write_file \(error\): Permission denied/);
  assert.equal(existsSync(join(terminal.dir, "work", "new.txt")), false);
  assert.doesNotMatch(terminal.output(), /\n  should-not-run/);
});

test("the build profile runs the shell with the allowlisted environment, and the chat profile gets no bridge", async t => {
  const build = startTerminal(t, { args: ["--profile", "build"] });
  await build.waitFor(READY);
  await ask(build, "FAKE:list");
  assert.match(build.output(), /Tools: bash,read_file,write_file,edit_file,glob,grep\n/);
  await ask(build, 'FAKE:call bash {"command":"echo env=$YOUI_TOOL_ENV stripe=${STRIPE_SECRET_KEY:-none} key=${ANTHROPIC_API_KEY:-none}"}');
  assert.match(build.output(), /bash: env=sanitized stripe=none key=none/);
  await ask(build, 'FAKE:call write_file {"path":"new.txt","content":"made"}');
  assert.equal(readFileSync(join(build.dir, "work", "new.txt"), "utf-8"), "made");

  const chat = startTerminal(t, { args: ["--profile", "chat"] });
  await chat.waitFor(READY);
  await ask(chat, "just talk");
  assert.match(chat.output(), /Echo: just talk/);
  const [run] = chat.fake.runs();
  assert.equal(run.argv.includes("--mcp-config"), false, "no tools, no bridge");
  assert.equal(run.argv.includes("--allowedTools"), false);
});

// A command that starts a long sleep and writes its pid where the test can read it.
const LONG_COMMAND = 'FAKE:call bash {"command":"sleep 30 & echo $! > sleep.pid; wait; echo late"}';
async function sleepPid(terminal) {
  const file = join(terminal.dir, "work", "sleep.pid");
  const started = Date.now();
  while (!existsSync(file) || !readFileSync(file, "utf-8").trim()) {
    if (Date.now() - started > 10_000) assert.fail("the command never started");
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  return Number(readFileSync(file, "utf-8").trim());
}

test("the shell is async: the turn's time limit stops a long command, all of it, and the terminal goes on", async t => {
  const terminal = startTerminal(t, { args: ["--profile", "build"], env: { YOUI_CLAUDE_TURN_TIMEOUT_MS: "1500" } });
  await terminal.waitFor(READY);
  const started = Date.now();
  await ask(terminal, LONG_COMMAND);
  assert.ok(Date.now() - started < 10_000, "stopped at the limit, not after the command");
  assert.match(terminal.output(), /Claude Code did not finish within 2 s; YOUI stopped the turn/);
  assert.ok(await gone(await sleepPid(terminal)), "what the command started was stopped with it");
  await ask(terminal, "still here");
  assert.match(terminal.output(), /Echo: still here/);
});

test("exiting while a shell command runs stops the command and Claude Code", async t => {
  const terminal = startTerminal(t, { args: ["--profile", "build"] });
  await terminal.waitFor(READY);
  terminal.send(LONG_COMMAND);
  const pid = await sleepPid(terminal);
  const [{ pid: claudePid }] = terminal.fake.runs();
  assert.ok(alive(pid) && alive(claudePid));
  terminal.child.kill("SIGTERM");
  assert.deepEqual(await terminal.exited, { code: 143, signal: null });
  assert.ok(await gone(pid), "the command is stopped");
  assert.ok(await gone(claudePid), "Claude Code is stopped");
});

// ── Exit paths ──────────────────────────────────────────────────────

for (const [how, expected, reason] of [
  ["/exit", { code: 0, signal: null }, "exit"],
  ["end of input", { code: 0, signal: null }, "exit"],
  ["Ctrl+C", { code: 0, signal: null }, "exit"],
  ["SIGHUP", { code: 129, signal: null }, "hangup"],
  ["SIGTERM", { code: 143, signal: null }, "terminated"],
  ["SIGINT", { code: 130, signal: null }, "exit"],
]) {
  test(`${how} mid-task stops the running Claude Code and still writes the YOUSPEAK row`, async t => {
    const terminal = startTerminal(t);
    await terminal.waitFor(READY);
    terminal.send("FAKE:text-then-sleep a partial answer before the end");
    await terminal.waitFor(/a partial answer before the end/);
    const started = Date.now();
    while (terminal.fake.step("sleeping").length === 0) {
      if (Date.now() - started > 10_000) assert.fail("the fake never slept");
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    const [{ pid }] = terminal.fake.step("sleeping");
    assert.ok(alive(pid), "Claude Code is running");
    terminal.send("one more task");
    await terminal.waitFor(/A task is running; send this when it has finished\./);

    if (how === "/exit") terminal.send("/exit");
    else if (how === "end of input") terminal.child.stdin.end();
    else if (how === "Ctrl+C") terminal.child.stdin.write("\x03");
    else terminal.child.kill(how);
    assert.deepEqual(await terminal.exited, expected);
    assert.ok(await gone(pid), "no Claude Code outlives the terminal");

    const rows = terminal.rows();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].route, "claude-code");
    assert.equal(rows[0].endReason, reason);
    assert.equal(terminal.fake.runs().length, 1, "the refused message was never sent");
  });
}
