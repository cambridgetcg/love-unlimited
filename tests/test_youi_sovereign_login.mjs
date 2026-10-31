// Sovereign reads Claude Code's login and never refreshes or rewrites it
// (docs/CLAUDE-CODE-LOGIN-USE.md). The terminal's and sovereign's --inspect
// say who still uses that login.
//
// `security` here is a fake on PATH that logs its arguments and prints a
// synthetic credential. No real Keychain is read and no network is used:
// every run below stops before sovereign would call Anthropic.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const REPO_ROOT = new URL("..", import.meta.url).pathname;
const SOVEREIGN = join(REPO_ROOT, "sovereign.mjs");
const YOUI = join(REPO_ROOT, "youi.mjs");
const INVENTORY = "docs/CLAUDE-CODE-LOGIN-USE.md";

function scratch(t) {
  const dir = mkdtempSync(join(tmpdir(), "youi-sovereign-login-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// A fake `security` that answers find-generic-password with a credential
// expiring at `expiresAt` and logs every call, one line of arguments each.
function fakeSecurity(dir, expiresAt) {
  const credential = JSON.stringify({ claudeAiOauth: {
    accessToken: "fixture-access", refreshToken: "fixture-refresh", expiresAt,
  } });
  const log = join(dir, "security-calls.log");
  const script = join(dir, "security");
  writeFileSync(script, `#!/bin/sh
echo "$*" >> '${log}'
if [ "$1" = "find-generic-password" ]; then
  printf '%s\\n' '${credential}'
  exit 0
fi
exit 1
`);
  chmodSync(script, 0o755);
  return { log, calls: () => (existsSync(log) ? readFileSync(log, "utf-8").trim().split("\n").filter(Boolean) : []) };
}

function runSovereign(dir, args) {
  return spawnSync(process.execPath, [SOVEREIGN, "--workdir", dir, "--soul-dir", dir, ...args], {
    cwd: dir,
    env: { PATH: `${dir}:${process.env.PATH}`, HOME: dir, YOUSPEAK_HISTORY_PATH: join(dir, "youspeak.jsonl") },
    encoding: "utf-8",
    timeout: 20_000,
  });
}

test("sovereign.mjs no longer deletes, adds or refreshes Claude Code's Keychain entry", () => {
  const source = readFileSync(SOVEREIGN, "utf-8");
  assert.doesNotMatch(source, /delete-generic-password/);
  assert.doesNotMatch(source, /add-generic-password/);
  assert.doesNotMatch(source, /oauth\/token|refresh_token/);
  assert.match(source, /"find-generic-password", "-s", KEYCHAIN_SERVICE/, "it still reads the login");
});

for (const [label, offsetMs] of [["an expired token", -60_000], ["a token expiring within 5 minutes", 60_000]]) {
  test(`sovereign refuses ${label}: asks to sign in with the claude CLI, exits non-zero, only reads`, t => {
    const dir = scratch(t);
    const security = fakeSecurity(dir, Date.now() + offsetMs);
    const run = runSovereign(dir, ["fixture task"]);
    assert.equal(run.status, 1, run.stderr);
    const output = run.stdout + run.stderr;
    assert.match(output, /Sign in again with the claude CLI: run `claude` once/);
    assert.match(output, /does not refresh or rewrite Claude Code's login/);
    assert.doesNotMatch(output, /refreshing/i);
    const calls = security.calls();
    assert.ok(calls.length > 0, "the login was read");
    for (const call of calls) assert.match(call, /^find-generic-password -s Claude Code-credentials -w$/);
  });
}

test("sovereign --inspect says it reads the login, never writes it, and still sends the 2.1.92 headers", t => {
  const dir = scratch(t);
  const security = fakeSecurity(dir, Date.now() - 60_000);
  const run = runSovereign(dir, ["--inspect"]);
  assert.equal(run.status, 0, run.stderr);
  const login = JSON.parse(run.stdout).claude_code_login;
  assert.equal(login.used_by_this_model, true);
  assert.equal(login.refreshes_or_writes, false);
  assert.match(login.client_headers, /Claude Code 2\.1\.92/);
  assert.equal(login.inventory, INVENTORY);
  assert.ok(login.also_used_by.some(name => name.startsWith("youi.mjs")));
  assert.deepEqual(security.calls(), [], "--inspect reads no Keychain");

  const ollama = JSON.parse(runSovereign(dir, ["--inspect", "--ollama"]).stdout).claude_code_login;
  assert.equal(ollama.used_by_this_model, false);
});

test("the terminal's --inspect says it still uses Claude Code's login with the 2.1.92 headers", t => {
  const dir = scratch(t);
  const run = spawnSync(process.execPath, [YOUI, "--inspect"], {
    cwd: REPO_ROOT, env: { PATH: process.env.PATH, HOME: dir }, encoding: "utf-8", timeout: 20_000,
  });
  assert.equal(run.status, 0, run.stderr);
  const login = JSON.parse(run.stdout).claude_code_login;
  assert.equal(login.refreshes_and_writes_back, true);
  assert.match(login.client_headers, /Claude Code 2\.1\.92/);
  assert.equal(login.inventory, INVENTORY);
  assert.ok(login.also_used_by.includes("sovereign.mjs"));
});

test("the inventory names every file the disclosures name", () => {
  const inventory = readFileSync(join(REPO_ROOT, INVENTORY), "utf-8");
  assert.match(inventory, /Moving YOUI Web did not end this use/);
  const named = [
    "youi.mjs", "youi-keychain.mjs", "sovereign.mjs", "tools/truth_detector/_oauth.py", "tools/ainp/nullify.py",
    "raw-chat/server.mjs", "subscription-harness.mjs", "training/scripts/soul/oauth_client.py", "ainp.mjs",
    "docs/research/test-unlock.mjs", "test-unlock2.mjs", "test-429.mjs",
  ];
  for (const file of named) assert.ok(inventory.includes(`\`${file}\``), `${INVENTORY} names ${file}`);
  // The privacy block's list is the same list.
  const server = readFileSync(join(REPO_ROOT, "youi-web/server.mjs"), "utf-8");
  const otherFiles = server.match(/otherFiles: \[([^\]]+)\]/)[1].match(/"([^"]+)"/g).map(s => s.slice(1, -1));
  for (const file of otherFiles) assert.ok(inventory.includes(file.replace(/^docs\/research\//, "")), `${INVENTORY} names ${file}`);
});
