// Sovereign reads Claude Code's login and never refreshes or rewrites it
// (docs/CLAUDE-CODE-LOGIN-USE.md). The terminal's and sovereign's --inspect
// say who still uses that login.
//
// `security` here is a fake on PATH that logs its arguments and prints a
// synthetic credential from a file in the scratch directory. `fetch` is
// replaced by a preload that logs each request and answers from a plan; a
// request beyond the plan throws, so no run reaches the network.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const REPO_ROOT = new URL("..", import.meta.url).pathname;
const SOVEREIGN = join(REPO_ROOT, "sovereign.mjs");
const YOUI = join(REPO_ROOT, "youi.mjs");
const INVENTORY = "docs/CLAUDE-CODE-LOGIN-USE.md";
const SIGN_IN = /Sign in again with the claude CLI: start `claude`, use \/login if it asks/;
const STARTUP_EXPIRY = /has expired or expires within 5 minutes/;

function scratch(t) {
  const dir = mkdtempSync(join(tmpdir(), "youi-sovereign-login-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

function credential(accessToken, expiresAt) {
  return { claudeAiOauth: { accessToken, refreshToken: "fixture-refresh", expiresAt } };
}

// Keychain entries live in <dir>/keychain/<account>.json ("_empty" for the
// empty account name); null removes one. The fetch preload uses the same layout
// to stand for Claude Code renewing its login between two requests.
function setEntry(dir, account, value) {
  const file = join(dir, "keychain", `${account || "_empty"}.json`);
  if (value === null) rmSync(file, { force: true });
  else writeFileSync(file, JSON.stringify(value));
}

// A fake `security` that answers find-generic-password from those files and
// logs every call, one line of arguments each.
function fakeSecurity(dir, entries) {
  mkdirSync(join(dir, "keychain"), { recursive: true });
  for (const [account, value] of Object.entries(entries)) setEntry(dir, account, value);
  const log = join(dir, "security-calls.log");
  const script = join(dir, "security");
  writeFileSync(script, `#!/bin/sh
echo "$*" >> '${log}'
if [ "$1" = "find-generic-password" ]; then
  file='${join(dir, "keychain")}'/"\${5:-_empty}.json"
  if [ -f "$file" ]; then cat "$file"; echo; exit 0; fi
  echo "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain." >&2
  exit 44
fi
exit 1
`);
  chmodSync(script, 0o755);
  return { calls: () => (existsSync(log) ? readFileSync(log, "utf-8").trim().split("\n").filter(Boolean) : []) };
}

// The fetch preload: each request takes the next plan entry, logs its URL and
// bearer, applies the entry's Keychain change, then answers with its status.
const FETCH_STUB = `
import { appendFileSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
const dir = process.env.SOVEREIGN_TEST_DIR;
const plan = JSON.parse(readFileSync(join(dir, "fetch-plan.json"), "utf-8"));
let n = 0;
globalThis.fetch = async (url, init = {}) => {
  const bearer = String(init.headers?.Authorization || "").replace(/^Bearer /, "");
  appendFileSync(join(dir, "fetch-calls.log"), JSON.stringify({ url: String(url), bearer }) + "\\n");
  const step = plan[n++];
  if (!step) throw new Error("fixture: network blocked");
  for (const [account, value] of Object.entries(step.keychain || {})) {
    const file = join(dir, "keychain", (account || "_empty") + ".json");
    if (value === null) rmSync(file, { force: true }); else writeFileSync(file, JSON.stringify(value));
  }
  const body = step.status === 200
    ? { content: step.content, stop_reason: step.content.some(b => b.type === "tool_use") ? "tool_use" : "end_turn",
        usage: { input_tokens: 1, output_tokens: 1 } }
    : { type: "error", error: { type: "authentication_error", message: "fixture" } };
  return new Response(JSON.stringify(body), { status: step.status, headers: { "content-type": "application/json" } });
};
`;

function runSovereign(dir, args, { plan = [], env = {} } = {}) {
  writeFileSync(join(dir, "fetch-plan.json"), JSON.stringify(plan));
  writeFileSync(join(dir, "fetch-stub.mjs"), FETCH_STUB);
  return spawnSync(process.execPath,
    ["--import", pathToFileURL(join(dir, "fetch-stub.mjs")).href, SOVEREIGN, "--workdir", dir, "--soul-dir", dir, ...args], {
      cwd: dir,
      env: { PATH: `${dir}:${process.env.PATH}`, HOME: dir, YOUSPEAK_HISTORY_PATH: join(dir, "youspeak.jsonl"),
        SOVEREIGN_TEST_DIR: dir, ...env },
      encoding: "utf-8",
      timeout: 20_000,
    });
}

function fetches(dir) {
  const log = join(dir, "fetch-calls.log");
  return existsSync(log) ? readFileSync(log, "utf-8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line)) : [];
}

function onlyReads(calls) {
  assert.ok(calls.length > 0, "the login was read");
  for (const call of calls) assert.match(call, /^find-generic-password -s Claude Code-credentials -a .*-w$/);
}

function savedState(dir) {
  return JSON.parse(readFileSync(join(dir, ".sovereign-state.json"), "utf-8"));
}

function youspeakReasons(dir) {
  const file = join(dir, "youspeak.jsonl");
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf-8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line).endReason);
}

const HOUR = 3_600_000;
const TEXT = [{ type: "text", text: "fixture done" }];

test("sovereign.mjs no longer deletes, adds or refreshes Claude Code's Keychain entry", () => {
  const source = readFileSync(SOVEREIGN, "utf-8");
  assert.doesNotMatch(source, /delete-generic-password/);
  assert.doesNotMatch(source, /add-generic-password/);
  assert.doesNotMatch(source, /oauth\/token|refresh_token/);
  assert.match(source, /keychain\.readCredential\(\)/, "it still reads the login");
  assert.doesNotMatch(source, /keychain\.updateTokens/);
});

for (const [label, offsetMs] of [["an expired token", -60_000], ["a token expiring within 5 minutes", 60_000]]) {
  test(`sovereign refuses ${label} at start: asks to sign in with the claude CLI, exits 1, only reads, sends nothing`, t => {
    const dir = scratch(t);
    const security = fakeSecurity(dir, { "": credential("fixture-access", Date.now() + offsetMs) });
    const run = runSovereign(dir, ["fixture task"]);
    assert.equal(run.status, 1, run.stderr);
    const output = run.stdout + run.stderr;
    assert.match(output, STARTUP_EXPIRY);
    assert.match(output, SIGN_IN);
    assert.match(output, /does not refresh or rewrite Claude Code's login/);
    assert.doesNotMatch(output, /refreshing/i);
    onlyReads(security.calls());
    assert.deepEqual(fetches(dir), [], "no request was sent");
  });
}

test("sovereign refuses a missing login at start the same way", t => {
  const dir = scratch(t);
  const security = fakeSecurity(dir, {});
  const run = runSovereign(dir, ["fixture task"]);
  assert.equal(run.status, 1, run.stderr);
  assert.match(run.stdout + run.stderr, /Claude Code's login is not in the Keychain\..*Sign in again/s);
  onlyReads(security.calls());
  assert.deepEqual(fetches(dir), []);
});

test("sovereign reads your account's entry first, and says when it fell back to a stale empty-account one", t => {
  const dir = scratch(t);
  const security = fakeSecurity(dir, { "": credential("fixture-stale", Date.now() - HOUR) });
  const run = runSovereign(dir, ["fixture task"], { env: { USER: "fixture-user" } });
  assert.equal(run.status, 1, run.stderr);
  assert.match(run.stdout + run.stderr, /read from the entry with an empty account name/);
  const calls = security.calls();
  assert.match(calls[0], /-a fixture-user -w$/);
  assert.match(calls[1], /-a {2}-w$/);
  onlyReads(calls);
  assert.deepEqual(fetches(dir), []);
});

test("on a 401 with the same token, sovereign saves its state and exits 1 without retrying", t => {
  const dir = scratch(t);
  const security = fakeSecurity(dir, { "": credential("fixture-access", Date.now() + HOUR) });
  const run = runSovereign(dir, ["fixture task"], { plan: [{ status: 401 }] });
  assert.equal(run.status, 1, run.stderr);
  const output = run.stdout + run.stderr;
  assert.match(output, /refused Claude Code's login \(401\)/);
  assert.match(output, SIGN_IN);
  assert.match(output, /Resume with: node sovereign\.mjs --continue/);
  assert.deepEqual(fetches(dir).map(f => f.bearer), ["fixture-access"], "no retry with the same token");
  const state = savedState(dir);
  assert.equal(state.turnCount, 0);
  assert.notEqual(state.completed, true);
  assert.deepEqual(youspeakReasons(dir), [], "a run with no output writes no YOUSPEAK row");
  onlyReads(security.calls());
});

test("on a 401 after Claude Code renewed its login, sovereign retries once with the new token", t => {
  const dir = scratch(t);
  const security = fakeSecurity(dir, { "": credential("fixture-access", Date.now() + HOUR) });
  const renewed = credential("fixture-renewed", Date.now() + 2 * HOUR);
  const run = runSovereign(dir, ["fixture task"],
    { plan: [{ status: 401, keychain: { "": renewed } }, { status: 200, content: TEXT }] });
  assert.equal(run.status, 0, run.stderr);
  assert.match(run.stdout, /renewed its login since, retrying once/);
  assert.match(run.stdout, /fixture done/);
  assert.deepEqual(fetches(dir).map(f => f.bearer), ["fixture-access", "fixture-renewed"]);
  onlyReads(security.calls());
});

for (const [label, step2] of [
  ["the renewed token is refused too", { status: 401 }],
]) {
  test(`on a 401, sovereign retries only once: ${label}`, t => {
    const dir = scratch(t);
    fakeSecurity(dir, { "": credential("fixture-access", Date.now() + HOUR) });
    const renewed = credential("fixture-renewed", Date.now() + 2 * HOUR);
    const run = runSovereign(dir, ["fixture task"], { plan: [{ status: 401, keychain: { "": renewed } }, step2] });
    assert.equal(run.status, 1, run.stderr);
    assert.deepEqual(fetches(dir).map(f => f.bearer), ["fixture-access", "fixture-renewed"]);
    assert.equal(savedState(dir).turnCount, 0);
  });
}

for (const [label, change, why] of [
  ["expired", credential("fixture-expired", Date.now() - HOUR), STARTUP_EXPIRY],
  ["missing", null, /not in the Keychain/],
]) {
  test(`on a 401, an entry now ${label} stops sovereign with state saved and exit 1`, t => {
    const dir = scratch(t);
    const security = fakeSecurity(dir, { "": credential("fixture-access", Date.now() + HOUR) });
    const run = runSovereign(dir, ["fixture task"], { plan: [{ status: 401, keychain: { "": change } }] });
    assert.equal(run.status, 1, run.stderr);
    assert.match(run.stdout + run.stderr, why);
    assert.match(run.stdout, /Resume with: node sovereign\.mjs --continue/);
    assert.equal(fetches(dir).length, 1);
    assert.notEqual(savedState(dir).completed, true);
    onlyReads(security.calls());
  });
}

test("a 401 after a tool turn keeps that turn, and --continue picks the task up", t => {
  const dir = scratch(t);
  writeFileSync(join(dir, "fixture.txt"), "fixture line\n");
  fakeSecurity(dir, { "": credential("fixture-access", Date.now() + HOUR) });
  const toolTurn = [{ type: "tool_use", id: "toolu_fixture", name: "read_file", input: { path: "fixture.txt" } }];
  const stopped = runSovereign(dir, ["fixture task"], { plan: [{ status: 200, content: toolTurn }, { status: 401 }] });
  assert.equal(stopped.status, 1, stopped.stderr);
  const state = savedState(dir);
  assert.equal(state.turnCount, 1);
  assert.deepEqual(state.messages.map(m => m.role), ["user", "assistant", "user"]);
  assert.deepEqual(youspeakReasons(dir), ["sign-in-needed"]);

  rmSync(join(dir, "fetch-calls.log"));
  const resumed = runSovereign(dir, ["--continue"], { plan: [{ status: 200, content: TEXT }] });
  assert.equal(resumed.status, 0, resumed.stderr);
  assert.match(resumed.stdout, /Resuming from turn 1/);
  assert.equal(fetches(dir).length, 1);
});

function sovereignInspect(dir, args = [], env = {}) {
  const run = runSovereign(dir, ["--inspect", ...args], { env });
  assert.equal(run.status, 0, run.stderr);
  return JSON.parse(run.stdout);
}

function terminalInspect(dir) {
  const run = spawnSync(process.execPath, [YOUI, "--inspect"], {
    cwd: REPO_ROOT, env: { PATH: process.env.PATH, HOME: dir }, encoding: "utf-8", timeout: 20_000,
  });
  assert.equal(run.status, 0, run.stderr);
  return JSON.parse(run.stdout);
}

test("sovereign --inspect says it reads the login, never writes it, and still sends the 2.1.92 headers", t => {
  const dir = scratch(t);
  const security = fakeSecurity(dir, { "": credential("fixture-access", Date.now() - 60_000) });
  const manifest = sovereignInspect(dir);
  const login = manifest.claude_code_login;
  assert.equal(login.used_by_this_model, true);
  assert.equal(login.refreshes_or_writes, false);
  assert.match(login.client_headers, /Claude Code 2\.1\.92/);
  assert.equal(login.inventory, INVENTORY);
  assert.ok(login.also_used_by.some(name => name.startsWith("youi.mjs")));
  assert.deepEqual(manifest.provider, {
    id: "anthropic", origin: "https://api.anthropic.com", execution: "remote", transport: "https", local_only: false,
  });
  assert.deepEqual(security.calls(), [], "--inspect reads no Keychain");
  assert.deepEqual(fetches(dir), []);

  const ollama = sovereignInspect(dir, ["--ollama"], { OLLAMA_BASE_URL: "http://127.0.0.1:11434" });
  assert.equal(ollama.claude_code_login.used_by_this_model, false);
  assert.equal(ollama.claude_code_login.client_headers, null);
  assert.deepEqual(ollama.provider, {
    id: "ollama", origin: "http://127.0.0.1:11434", execution: "local", transport: "http", local_only: true,
  });
});

test("the terminal's --inspect says it still uses Claude Code's login with the 2.1.92 headers", t => {
  const login = terminalInspect(scratch(t)).claude_code_login;
  assert.equal(login.refreshes_and_writes_back, true);
  assert.match(login.client_headers, /Claude Code 2\.1\.92/);
  assert.equal(login.inventory, INVENTORY);
  assert.ok(login.also_used_by.includes("sovereign.mjs"));
});

// Each disclosure keeps its own copy of the list. Every file the inventory's
// tables list must be in each copy (the runtimes and the truth detector are
// left out of the copies that give them their own sentence), and every file
// the copies name must be in the tables.
test("the inventory and the three disclosures name the same files", t => {
  const dir = scratch(t);
  const inventory = readFileSync(join(REPO_ROOT, INVENTORY), "utf-8");
  assert.match(inventory, /Moving YOUI Web did not end the use of Claude Code's login/);
  // First cell of each table row; a bare name shares the folder of the row's first file.
  const listed = [];
  for (const row of inventory.split("\n").filter(line => /^\| `/.test(line))) {
    const files = [...row.split("|")[1].matchAll(/`([^`]+)`/g)].map(m => m[1]);
    const folder = dirname(files[0]);
    for (const file of files) listed.push(file.includes("/") || folder === "." ? file : `${folder}/${file}`);
  }
  for (const file of [
    "youi.mjs", "youi-keychain.mjs", "sovereign.mjs", "tools/truth_detector/_oauth.py", "tools/ainp/nullify.py",
    "raw-chat/server.mjs", "subscription-harness.mjs", "training/scripts/soul/oauth_client.py",
    "training/scripts/claude_mode_one_gen.py", "training/scripts/eval_adapter.py", "training/scripts/judge_gate.py",
    "ainp.mjs", "docs/research/test-unlock.mjs", "docs/research/test-unlock2.mjs", "docs/research/test-429.mjs",
  ]) assert.ok(listed.includes(file), `${INVENTORY} lists ${file}`);

  const server = readFileSync(join(REPO_ROOT, "youi-web/server.mjs"), "utf-8");
  const copies = [
    ["privacy.claudeCodeLogin.otherFiles", server.match(/otherFiles: \[([^\]]+)\]/)[1].match(/"([^"]+)"/g).map(s => s.slice(1, -1)),
      ["youi.mjs", "youi-keychain.mjs", "sovereign.mjs", "tools/truth_detector/"]],
    ["youi.mjs --inspect also_used_by", terminalInspect(dir).claude_code_login.also_used_by, ["youi.mjs", "youi-keychain.mjs"]],
    ["sovereign.mjs --inspect also_used_by", sovereignInspect(dir).claude_code_login.also_used_by, ["sovereign.mjs"]],
  ];
  const mentions = (text, file) => text.includes(file) || text.includes(basename(file))
    || (file.startsWith("tools/truth_detector/") && text.includes("tools/truth_detector"));
  for (const [name, copy, ownSentence] of copies) {
    const text = copy.join(" ; ");
    for (const file of listed) {
      if (ownSentence.some(own => file === own || (own.endsWith("/") && file.startsWith(own)))) continue;
      assert.ok(mentions(text, file), `${name} names ${file}`);
    }
    for (const entry of copy) {
      const first = entry.split(/[ ,]/)[0];
      assert.ok(listed.some(file => file === first || file.startsWith(`${first}/`)), `${INVENTORY} lists ${first} (from ${name})`);
    }
  }
});
