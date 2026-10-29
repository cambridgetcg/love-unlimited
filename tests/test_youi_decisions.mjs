// YOUI decision room: /api/decisions/* reach the Collab person desk and nothing else.
//
// The fake desk below records the argv and stdin it receives, so these tests
// check what YOUI forwards without a Collab journal. The last test runs the
// real desk end to end when YOUI_DECISIONS_REAL_HOST names a built
// agenttool-collab-host (TypeScript or bundle) from cambridgetcg/agenttool.

import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import http from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const REPO_ROOT = new URL("../", import.meta.url).pathname;

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

async function startServer(t, extraEnv = {}) {
  const child = spawn(process.execPath, ["youi-web/server.mjs"], {
    cwd: REPO_ROOT,
    env: { ...process.env, PORT: "0", YOUI_TEST: "1", TRUTH_DETECTOR_ENABLED: "0", AUTONOMOUS: "0", ...extraEnv },
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
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`YOUI exited ${code}: ${stderr.slice(-500)}`)); });
  });
  t.after(() => { if (!child.killed) child.kill("SIGTERM"); });
  return port;
}

/** A browser on `host` (127.0.0.1 or localhost) with a YOUI session. */
async function browserSession(port, host = "127.0.0.1") {
  const origin = `http://${host}:${port}`;
  const hostHeader = `${host}:${port}`;
  const bootstrap = await request(port, { headers: { Host: hostHeader } });
  const pairs = (bootstrap.headers["set-cookie"] || []).map(value => value.split(";", 1)[0]);
  const cookie = pairs.join("; ");
  const csrf = decodeURIComponent((pairs.find(pair => pair.startsWith("youi_csrf=")) || "=").split("=")[1]);
  const page = `page-decisions-${host.replaceAll(".", "")}-0000000001`;
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: { Host: hostHeader, Cookie: cookie, Origin: origin, "X-YOUI-CSRF": csrf, "X-YOUI-Page": page },
    body: { label: "decisions" },
  });
  assert.equal(created.status, 201, created.text);
  const headers = {
    Host: hostHeader,
    Cookie: cookie,
    "X-YOUI-Session": created.json.session.id,
    "X-YOUI-Session-Token": created.json.sessionToken,
    "X-YOUI-Page": page,
  };
  return {
    get: path => request(port, { path, headers }),
    post: (path, body) => request(port, {
      path, method: "POST", body, headers: { ...headers, Origin: origin, "X-YOUI-CSRF": csrf },
    }),
  };
}

function fakeDesk(t) {
  const dir = mkdtempSync(join(tmpdir(), "youi-fake-desk-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const script = join(dir, "fake-desk.mjs");
  writeFileSync(script, `
import { appendFileSync, openSync, readFileSync } from "node:fs";
const args = process.argv.slice(2);
const stdin = args.includes("--passkey-assertion=-") ? readFileSync(0, "utf8") : null;
let tty = false;
try { openSync("/dev/tty", "r+"); tty = true; } catch {}
appendFileSync(new URL("./calls.jsonl", import.meta.url), JSON.stringify({
  args, stdin, tty, secret: process.env.YOUI_FAKE_SECRET ?? null, home: Boolean(process.env.HOME),
}) + "\\n");
const verb = args[1];
const receipts = {
  workspaces: { status: "ok", workspaces: [{ id: "ws_demo", name: "demo", root_path: "/tmp/demo", created_at: "2026-10-29T00:00:00.000Z" }] },
  consent: { status: "error", error: "ai_not_consented", message: "The agents have not consented", position: "open" },
};
process.stdout.write(JSON.stringify({ protocol: "agenttool.collab-host/1", command: "person " + verb, ...(receipts[verb] ?? { status: "ok" }) }) + "\\n");
process.exit(receipts[verb]?.status === "error" ? 1 : 0);
`);
  return {
    env: { YOUI_COLLAB_HOST: script, YOUI_COLLAB_BUN: process.execPath, YOUI_COLLAB_DB: join(dir, "collab.sqlite") },
    calls: () => existsSync(join(dir, "calls.jsonl"))
      ? readFileSync(join(dir, "calls.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line))
      : [],
    dir,
  };
}

const ASSERTION = {
  credential_id: "Y3JlZA",
  authenticator_data: "YXV0aA",
  client_data_json: "Y2xpZW50",
  signature: "c2ln",
  extra: "dropped",
};

test("decision routes need their capability, a session and a same-origin CSRF token", async t => {
  const desk = fakeDesk(t);
  const port = await startServer(t, { ...desk.env, YOUI_CAPABILITIES: "chat,status:read,sessions:manage" });
  const browser = await browserSession(port);
  const read = await browser.get("/api/decisions/workspaces");
  assert.equal(read.status, 403);
  assert.equal(read.json.capability, "decisions:read");
  const write = await browser.post("/api/decisions/say", { workspace: "ws_demo", body: "hi", idempotency_key: "k1" });
  assert.equal(write.status, 403);
  assert.equal(write.json.capability, "decisions:write");
  const anonymous = await request(port, { path: "/api/decisions/workspaces" });
  assert.equal(anonymous.status, 401);
  assert.deepEqual(desk.calls(), []);
});

test("YOUI forwards reads and acts to the person desk and nothing more", async t => {
  const desk = fakeDesk(t);
  const port = await startServer(t, { ...desk.env, YOUI_FAKE_SECRET: "must-not-reach-the-desk" });
  const browser = await browserSession(port);

  const workspaces = await browser.get("/api/decisions/workspaces");
  assert.equal(workspaces.status, 200);
  assert.equal(workspaces.json.workspaces[0].id, "ws_demo");

  assert.equal((await browser.get("/api/decisions/proposals?workspace=ws_demo&limit=99")).status, 400);
  assert.equal((await browser.get("/api/decisions/proposals?workspace=--db=/etc")).status, 400);
  assert.equal((await browser.get("/api/decisions/proposals?workspace=ws_demo&limit=5")).status, 200);

  const noAssertion = await browser.post("/api/decisions/act", { proposal: "report_1", act: "agree" });
  assert.equal(noAssertion.status, 400);
  const refused = await browser.post("/api/decisions/act", { proposal: "report_1", act: "agree", assertion: ASSERTION });
  assert.equal(refused.status, 409);
  assert.equal(refused.json.error, "ai_not_consented");
  const rejected = await browser.post("/api/decisions/act", {
    proposal: "report_1", act: "reject", reason: "Not before Sunday.", assertion: ASSERTION,
  });
  assert.equal(rejected.status, 200);

  const said = await browser.post("/api/decisions/say", {
    workspace: "ws_demo", proposal: "report_1", body: "--db=/tmp/elsewhere is just text", idempotency_key: "note-1",
  });
  assert.equal(said.status, 200);
  const proposed = await browser.post("/api/decisions/propose", {
    workspace: "ws_demo", question: "Q?", criteria: "C", extent: "E", consult_seconds: 3600, idempotency_key: "p-1",
  });
  assert.equal(proposed.status, 200);

  const calls = desk.calls();
  const db = `--db=${desk.env.YOUI_COLLAB_DB}`;
  assert.deepEqual(calls.map(call => call.args), [
    ["person", "workspaces", db],
    ["person", "proposals", "--workspace=ws_demo", "--limit=5", db],
    ["person", "consent", "--proposal=report_1", "--passkey-assertion=-", db],
    ["person", "reject", "--proposal=report_1", "--reason=Not before Sunday.", "--passkey-assertion=-", db],
    ["person", "say", "--workspace=ws_demo", "--body=--db=/tmp/elsewhere is just text", "--proposal=report_1",
      "--idempotency-key=note-1", db],
    ["person", "propose", "--workspace=ws_demo", "--question=Q?", "--criteria=C", "--extent=E",
      "--consult-seconds=3600", "--idempotency-key=p-1", db],
  ]);
  const { extra, ...forwarded } = ASSERTION;
  assert.deepEqual(JSON.parse(calls[2].stdin), forwarded);
  for (const call of calls) {
    assert.equal(call.tty, false, "the desk must run without a controlling terminal");
    assert.equal(call.secret, null, "ambient environment must not reach the desk");
    assert.equal(call.home, true);
  }
});

test("passkey staging needs localhost and writes only a private file", async t => {
  const desk = fakeDesk(t);
  const staging = join(desk.dir, "staged.json");
  const port = await startServer(t, { ...desk.env, YOUI_PASSKEY_STAGING_FILE: staging });
  const { publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const spki = publicKey.export({ type: "spki", format: "der" }).toString("base64url");
  const body = { credential_id: "Y3JlZGVudGlhbA", public_key_spki: spki, algorithm: -7, label: "Touch ID" };

  const byIp = await (await browserSession(port, "127.0.0.1")).post("/api/decisions/passkey/stage", body);
  assert.equal(byIp.status, 409);
  assert.equal(byIp.json.code, "passkey_needs_localhost");
  assert.equal(existsSync(staging), false);

  const local = await browserSession(port, "localhost");
  assert.equal((await local.post("/api/decisions/passkey/stage", { ...body, algorithm: -8 })).status, 400);
  const staged = await local.post("/api/decisions/passkey/stage", body);
  assert.equal(staged.status, 200, staged.text);
  assert.equal(staged.json.fingerprint, createHash("sha256").update(Buffer.from(spki, "base64url")).digest("hex"));
  assert.match(staged.json.command, /person passkey-enroll --file /);
  assert.ok(staged.json.command.endsWith(` --db ${desk.env.YOUI_COLLAB_DB}`), "enrol into the journal YOUI reads");
  assert.equal(statSync(staging).mode & 0o777, 0o600);
  assert.deepEqual(JSON.parse(readFileSync(staging, "utf8")), {
    credential_id: body.credential_id,
    public_key_spki: spki,
    algorithm: -7,
    rp_id: "localhost",
    origin: `http://localhost:${port}`,
    label: "Touch ID",
  });
  assert.deepEqual(desk.calls(), [], "staging never enrols; only Yu's terminal can");
});

test("a missing person desk is reported, not hidden", async t => {
  const port = await startServer(t, { YOUI_COLLAB_HOST: "/nonexistent/agenttool-collab-host" });
  const browser = await browserSession(port);
  const response = await browser.get("/api/decisions/workspaces");
  assert.equal(response.status, 502);
  assert.equal(response.json.code, "person_desk_unavailable");
});

const REAL_HOST = process.env.YOUI_DECISIONS_REAL_HOST;
test("end to end: a passkey agreement through YOUI reaches the real journal", { skip: !REAL_HOST && "set YOUI_DECISIONS_REAL_HOST to a built agenttool-collab-host" }, async t => {
  const dir = mkdtempSync(join(tmpdir(), "youi-real-desk-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const db = join(dir, "collab.sqlite");
  const storeModule = join(REAL_HOST, "..", "..", "src", "store.ts");
  const { publicKey, privateKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const spki = publicKey.export({ type: "spki", format: "der" }).toString("base64url");
  const credentialId = Buffer.from("youi-e2e-credential").toString("base64url");
  // Seed the journal and enrol the key directly: enrolment normally needs Yu's terminal.
  const seed = join(dir, "seed.ts");
  writeFileSync(seed, `
import { mkdirSync } from "node:fs";
import { CollabStore } from ${JSON.stringify(storeModule)};
mkdirSync(${JSON.stringify(join(dir, "repo"))}, { recursive: true });
const store = new CollabStore(${JSON.stringify(db)});
const a = store.startSession({ root_path: ${JSON.stringify(join(dir, "repo"))}, actor: "e2e-author" });
const b = store.startSession({ root_path: ${JSON.stringify(join(dir, "repo"))}, actor: "e2e-peer" });
const p = store.openProposalForSession({ ...a.credential, idempotency_key: "o", question: "Meet in YOUI?", criteria: "It works.", extent: "The decision room." });
store.appendReportForSession({ ...b.credential, idempotency_key: "s", kind: "inference", body: "Yes", relation: "supports", target_report_id: p.proposal.id });
store.enrollPersonPasskey({ credential_id: ${JSON.stringify(credentialId)}, public_key_spki: ${JSON.stringify(spki)}, algorithm: -7, rp_id: "localhost", origin: "http://localhost:PORT", label: "e2e" });
console.log(JSON.stringify({ workspace: a.workspace.id, proposal: p.proposal.id }));
store.close();
`);
  const port = await startServer(t, { YOUI_COLLAB_HOST: REAL_HOST, YOUI_COLLAB_DB: db });
  writeFileSync(seed, readFileSync(seed, "utf8").replace("PORT", String(port)));
  const seeded = spawnSync(process.env.YOUI_COLLAB_BUN || "bun", [seed], { encoding: "utf8" });
  assert.equal(seeded.status, 0, seeded.stderr);
  const { workspace, proposal } = JSON.parse(seeded.stdout.trim().split("\n").at(-1));

  const browser = await browserSession(port, "localhost");
  const inbox = await browser.get(`/api/decisions/inbox?workspace=${workspace}`);
  assert.equal(inbox.json.inbox.awaiting_person[0].proposal.id, proposal);

  const challenge = (await browser.post("/api/decisions/challenge", { proposal, act: "agree" })).json.challenge;
  const sha = bytes => createHash("sha256").update(bytes).digest();
  const authenticatorData = Buffer.concat([sha("localhost"), Buffer.from([0x05]), Buffer.alloc(4)]);
  const clientData = Buffer.from(JSON.stringify({
    type: "webauthn.get", challenge: challenge.challenge, origin: `http://localhost:${port}`, crossOrigin: false,
  }));
  const signature = sign("sha256", Buffer.concat([authenticatorData, sha(clientData)]), privateKey);
  const agreed = await browser.post("/api/decisions/act", {
    proposal,
    act: "agree",
    assertion: {
      credential_id: credentialId,
      authenticator_data: authenticatorData.toString("base64url"),
      client_data_json: clientData.toString("base64url"),
      signature: signature.toString("base64url"),
    },
  });
  assert.equal(agreed.status, 200, agreed.text);
  assert.equal(agreed.json.proposal.may_proceed, true);
  assert.equal(agreed.json.proposal.agreement.basis, "user_verified_passkey");

  const note = await browser.post("/api/decisions/say", { workspace, proposal, body: "Thank you.", idempotency_key: "thanks" });
  assert.equal(note.json.report.from_actor, "person:desk");
});
