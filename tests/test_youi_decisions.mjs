// YOUI decision room: /api/decisions/* reach a Collab person desk and nothing else.
//
// A fake desk and a fake ssh record the argv they receive, so these tests check
// what YOUI forwards without a Collab journal. The last tests run the real desk
// and MCP end to end when YOUI_DECISIONS_REAL_HOST names a built
// agenttool-collab-host from cambridgetcg/agenttool (TypeScript or bundle).

import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import http from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
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

/** A browser on 127.0.0.1 with a YOUI session. */
async function browserSession(port) {
  const origin = `http://127.0.0.1:${port}`;
  const bootstrap = await request(port);
  const pairs = (bootstrap.headers["set-cookie"] || []).map(value => value.split(";", 1)[0]);
  const cookie = pairs.join("; ");
  const csrf = decodeURIComponent((pairs.find(pair => pair.startsWith("youi_csrf=")) || "=").split("=")[1]);
  const page = "page-decisions-browser-0000000001";
  const created = await request(port, {
    path: "/api/sessions",
    method: "POST",
    headers: { Cookie: cookie, Origin: origin, "X-YOUI-CSRF": csrf, "X-YOUI-Page": page },
    body: { label: "decisions" },
  });
  assert.equal(created.status, 201, created.text);
  const headers = {
    Cookie: cookie,
    "X-YOUI-Session": created.json.session.id,
    "X-YOUI-Session-Token": created.json.sessionToken,
    "X-YOUI-Page": page,
  };
  return {
    get: path => request(port, { path, headers }),
    post: (path, body) => request(port, { path, method: "POST", body, headers: { ...headers, Origin: origin, "X-YOUI-CSRF": csrf } }),
  };
}

const FAKE_BODY = `
import { appendFileSync, openSync } from "node:fs";
let tty = false;
try { openSync("/dev/tty", "r+"); tty = true; } catch {}
const log = (record) => appendFileSync(new URL("./calls.jsonl", import.meta.url), JSON.stringify({ ...record, tty,
  secret: process.env.YOUI_FAKE_SECRET ?? null, home: Boolean(process.env.HOME) }) + "\\n");
const answer = (verb) => {
  const receipts = {
    workspaces: { status: "ok", workspaces: [{ id: "ws_demo", name: "demo", root_path: "/tmp/demo", created_at: "2026-10-29T00:00:00.000Z" }] },
    inbox: { status: "ok", summary: [{ workspace_id: "ws_demo", awaiting_person: 1, addressed_reports: 0, latest_report_id: null }] },
    withdraw: { status: "error", error: "report_author_mismatch", message: "The desk withdraws only proposals it opened" },
  };
  return { protocol: "agenttool.collab-host/1", command: "person " + verb, ...(receipts[verb] ?? { status: "ok" }) };
};
`;

function fakes(t) {
  const dir = mkdtempSync(join(tmpdir(), "youi-fake-desk-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const desk = join(dir, "fake-desk");
  writeFileSync(desk, `#!${process.execPath}\n${FAKE_BODY}
const args = process.argv.slice(2);
log({ via: "local", args });
const verb = args[1];
if (verb === "ceremony") {
  console.log(JSON.stringify({ status: "open", url: "http://localhost:17778/c/onetimeToken_0123", expires_at: "2026-10-29T00:03:00.000Z" }));
  setTimeout(() => process.exit(0), 1500);
} else {
  const receipt = answer(verb);
  console.log(JSON.stringify(receipt));
  process.exit(receipt.status === "error" ? 1 : 0);
}
`);
  const ssh = join(dir, "fake-ssh");
  writeFileSync(ssh, `#!${process.execPath}\n${FAKE_BODY}
const args = process.argv.slice(2);
const argv = JSON.parse(args.at(-1));
log({ via: "ssh", args });
console.log(JSON.stringify(answer(argv[0])));
`);
  chmodSync(desk, 0o755);
  chmodSync(ssh, 0o755);
  return {
    env: {
      YOUI_COLLAB_HOST: desk,
      YOUI_COLLAB_DB: join(dir, "collab.sqlite"),
      YOUI_DECISION_SSH: ssh,
      YOUI_DECISION_TABLES: JSON.stringify([{ id: "lab", label: "Lab", ssh: "lab-desk" }]),
    },
    calls: () => existsSync(join(dir, "calls.jsonl"))
      ? readFileSync(join(dir, "calls.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line))
      : [],
  };
}

test("decision routes need their capability, a session and a same-origin CSRF token", async t => {
  const fake = fakes(t);
  const port = await startServer(t, { ...fake.env, YOUI_CAPABILITIES: "chat,status:read,sessions:manage" });
  const browser = await browserSession(port);
  const read = await browser.get("/api/decisions/workspaces");
  assert.equal(read.status, 403);
  assert.equal(read.json.capability, "decisions:read");
  const write = await browser.post("/api/decisions/say", { workspace: "ws_demo", body: "hi", idempotency_key: "k1" });
  assert.equal(write.status, 403);
  assert.equal(write.json.capability, "decisions:write");
  assert.equal((await request(port, { path: "/api/decisions/tables" })).status, 401);
  for (const retired of ["/api/decisions/act", "/api/decisions/challenge", "/api/decisions/passkey/stage"]) {
    assert.notEqual((await browser.post(retired, {})).status, 200, retired);
  }
  assert.deepEqual(fake.calls(), []);
});

test("YOUI forwards to this device's desk and, as a JSON argv, to another device's", async t => {
  const fake = fakes(t);
  const port = await startServer(t, { ...fake.env, YOUI_FAKE_SECRET: "must-not-reach-the-desk" });
  const browser = await browserSession(port);

  const tables = await browser.get("/api/decisions/tables");
  assert.deepEqual(tables.json.tables, [
    { id: "local", label: "This device", remote: false },
    { id: "lab", label: "Lab", remote: true },
  ]);
  assert.equal((await browser.get("/api/decisions/workspaces")).json.workspaces[0].id, "ws_demo");
  assert.equal((await browser.get("/api/decisions/proposals?workspace=ws_demo&limit=99")).status, 400);
  assert.equal((await browser.get("/api/decisions/proposals?workspace=--db=/etc")).status, 400);
  assert.equal((await browser.get("/api/decisions/proposals?workspace=ws_demo&limit=5")).status, 200);
  assert.equal((await browser.get("/api/decisions/voices?workspace=ws_demo")).status, 200);
  assert.equal((await browser.get("/api/decisions/inbox?table=lab")).json.summary[0].awaiting_person, 1);
  assert.equal((await browser.get("/api/decisions/inbox?table=nowhere")).status, 404);

  assert.equal((await browser.post("/api/decisions/say", {
    workspace: "ws_demo", proposal: "report_1", body: "--db=/tmp/elsewhere is just text", idempotency_key: "note-1",
  })).status, 200);
  assert.equal((await browser.post("/api/decisions/propose", {
    table: "lab", workspace: "ws_demo", question: "Q?", criteria: "C", extent: "E", consult_seconds: 3600, idempotency_key: "p-1",
  })).status, 200);
  const withdraw = await browser.post("/api/decisions/withdraw", {
    workspace: "ws_demo", proposal: "report_2", reason: "No.", idempotency_key: "w-1",
  });
  assert.equal(withdraw.status, 409);
  assert.equal(withdraw.json.source, "person_desk");

  const db = `--db=${fake.env.YOUI_COLLAB_DB}`;
  const calls = fake.calls();
  assert.deepEqual(calls.filter(call => call.via === "local").map(call => call.args), [
    ["person", "workspaces", db],
    ["person", "proposals", "--workspace=ws_demo", "--limit=5", db],
    ["person", "voices", "--workspace=ws_demo", db],
    ["person", "say", "--workspace=ws_demo", "--body=--db=/tmp/elsewhere is just text", "--proposal=report_1",
      "--idempotency-key=note-1", db],
    ["person", "withdraw", "--workspace=ws_demo", "--proposal=report_2", "--reason=No.", "--idempotency-key=w-1", db],
  ]);
  const remote = calls.filter(call => call.via === "ssh").map(call => call.args);
  assert.deepEqual(remote.map(args => args.slice(0, -1)), [
    ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", "lab-desk"],
    ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", "lab-desk"],
  ]);
  assert.deepEqual(remote.map(args => JSON.parse(args.at(-1))), [
    ["inbox"],
    ["propose", "--workspace=ws_demo", "--question=Q?", "--criteria=C", "--extent=E", "--consult-seconds=3600", "--idempotency-key=p-1"],
  ]);
  for (const call of calls) {
    assert.equal(call.tty, false, "the desk must run without a controlling terminal");
    assert.equal(call.secret, null, "ambient environment must not reach the desk");
    assert.equal(call.home, true);
  }
});

test("Agree and Reject only open the desk's own signing page, here or for another device", async t => {
  const fake = fakes(t);
  const port = await startServer(t, fake.env);
  const browser = await browserSession(port);
  assert.equal((await browser.post("/api/decisions/ceremony", { proposal: "report_1", act: "reject" })).status, 400);
  const local = await browser.post("/api/decisions/ceremony", { proposal: "report_1", act: "agree" });
  assert.equal(local.status, 200, local.text);
  assert.equal(local.json.url, "http://localhost:17778/c/onetimeToken_0123");
  const remote = await browser.post("/api/decisions/ceremony", { table: "lab", proposal: "report_9", act: "reject", reason: "Not yet." });
  assert.equal(remote.status, 200, remote.text);
  const ceremonies = fake.calls().filter(call => call.args[1] === "ceremony").map(call => call.args);
  assert.deepEqual(ceremonies, [
    ["person", "ceremony", "--proposal=report_1", "--act=agree", `--db=${fake.env.YOUI_COLLAB_DB}`],
    ["person", "ceremony", "--proposal=report_9", "--act=reject", "--reason=Not yet.", "--via=lab-desk", `--db=${fake.env.YOUI_COLLAB_DB}`],
  ]);
});

test("a missing person desk is reported plainly", async t => {
  const port = await startServer(t, { YOUI_COLLAB_HOST: "/nonexistent/agenttool-collab-host" });
  const browser = await browserSession(port);
  const response = await browser.get("/api/decisions/workspaces");
  assert.equal(response.status, 503);
  assert.equal(response.json.error, "person_desk_unavailable");
  assert.equal(response.json.source, "youi");
});

const REAL_HOST = process.env.YOUI_DECISIONS_REAL_HOST;
const realSkip = !REAL_HOST && "set YOUI_DECISIONS_REAL_HOST to a built agenttool-collab-host";

function realHostDir() {
  return dirname(REAL_HOST);
}

function seedJournal(dir, { ceremonyPort, credentialId, spki }) {
  const db = join(dir, "collab.sqlite");
  const repo = join(dir, "repo");
  mkdirSync(repo, { recursive: true });
  const storeModule = join(realHostDir(), "..", "src", "store.ts");
  const seed = join(dir, "seed.ts");
  writeFileSync(seed, `
import { CollabStore } from ${JSON.stringify(storeModule)};
const store = new CollabStore(${JSON.stringify(db)});
const a = store.startSession({ root_path: ${JSON.stringify(repo)}, actor: "e2e-author" });
const b = store.startSession({ root_path: ${JSON.stringify(repo)}, actor: "e2e-peer" });
const p = store.openProposalForSession({ ...a.credential, idempotency_key: "o", question: "Meet in YOUI?", criteria: "It works.", extent: "The decision room." });
store.appendReportForSession({ ...b.credential, idempotency_key: "s", kind: "inference", body: "Yes", relation: "supports", target_report_id: p.proposal.id });
store.enrollPersonPasskey({ credential_id: ${JSON.stringify(credentialId)}, public_key_spki: ${JSON.stringify(spki)}, algorithm: -7, rp_id: "localhost", origin: "http://localhost:${ceremonyPort}", label: "e2e" });
console.log(JSON.stringify({ workspace: a.workspace.id, proposal: p.proposal.id }));
store.close();
`);
  const seeded = spawnSync(process.env.YOUI_COLLAB_BUN || "bun", [seed], { encoding: "utf8" });
  assert.equal(seeded.status, 0, seeded.stderr);
  return { db, repo, ...JSON.parse(seeded.stdout.trim().split("\n").at(-1)) };
}

test("end to end: YOUI opens the desk's page, Touch ID signs there, the journal records it", { skip: realSkip }, async t => {
  const dir = mkdtempSync(join(tmpdir(), "youi-real-desk-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const { publicKey, privateKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const spki = publicKey.export({ type: "spki", format: "der" }).toString("base64url");
  const credentialId = Buffer.from("youi-e2e-credential").toString("base64url");
  const ceremonyPort = 20_000 + Math.floor(Math.random() * 20_000);
  const seeded = seedJournal(dir, { ceremonyPort, credentialId, spki });
  const port = await startServer(t, { YOUI_COLLAB_HOST: REAL_HOST, YOUI_COLLAB_DB: seeded.db });
  const browser = await browserSession(port);

  const inbox = await browser.get(`/api/decisions/inbox?workspace=${seeded.workspace}`);
  assert.equal(inbox.json.inbox.awaiting_person[0].proposal.id, seeded.proposal);
  const opened = await browser.post("/api/decisions/ceremony", { proposal: seeded.proposal, act: "agree" });
  assert.equal(opened.status, 200, opened.text);
  const url = new URL(opened.json.url);
  assert.equal(url.port, String(ceremonyPort));

  // Node's fetch will not send a chosen Host header, so use http directly.
  const at = (path, { method = "GET", headers = {}, body } = {}) => request(Number(url.port), {
    path: `${url.pathname}${path}`, method, body, headers: { Host: `localhost:${url.port}`, ...headers },
  });
  const html = (await at("")).text;
  const data = JSON.parse(html.match(/<script type="application\/json" id="data">(.*?)<\/script>/s)[1]);
  const sha = bytes => createHash("sha256").update(bytes).digest();
  const authenticatorData = Buffer.concat([sha("localhost"), Buffer.from([0x05]), Buffer.alloc(4)]);
  const clientData = Buffer.from(JSON.stringify({ type: "webauthn.get", challenge: data.challenge, origin: `http://localhost:${ceremonyPort}`, crossOrigin: false }));
  const signature = sign("sha256", Buffer.concat([authenticatorData, sha(clientData)]), privateKey);
  const posted = (await at("/done", {
    method: "POST",
    headers: { Origin: `http://localhost:${ceremonyPort}` },
    body: { assertion: {
      credential_id: credentialId,
      authenticator_data: authenticatorData.toString("base64url"),
      client_data_json: clientData.toString("base64url"),
      signature: signature.toString("base64url"),
    } },
  })).json;
  assert.equal(posted.ok, true, JSON.stringify(posted));
  const after = await browser.get(`/api/decisions/proposals?workspace=${seeded.workspace}&proposal=${seeded.proposal}`);
  assert.equal(after.json.proposal.may_proceed, true);
  assert.equal(after.json.proposal.agreement.basis, "user_verified_passkey");
});

test("end to end: YOUI's own agents open proposals and ask Yu, and cannot resolve", { skip: realSkip }, async t => {
  const dir = mkdtempSync(join(tmpdir(), "youi-real-seat-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const { publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const seeded = seedJournal(dir, {
    ceremonyPort: 17778, credentialId: "c2VhdA", spki: publicKey.export({ type: "spki", format: "der" }).toString("base64url"),
  });
  const { DecisionSeat, runDecisionTool } = await import("../youi-web/decision-seat.mjs");
  const mcp = join(realHostDir(), /\.ts$/.test(REAL_HOST) ? "agenttool-collab-mcp.ts" : "agenttool-collab-mcp.js");
  const seat = new DecisionSeat({
    env: { ...process.env, YOUI_COLLAB_MCP: mcp, YOUI_COLLAB_DB: seeded.db, YOUI_COLLAB_BUN: process.env.YOUI_COLLAB_BUN || "bun" },
    root: seeded.repo,
    actor: "youi",
  });
  t.after(() => seat.close());
  assert.match(await runDecisionTool(seat, { action: "resolve", decision_id: "x", answer: "yes" }), /only Yu resolves/);
  assert.match(await runDecisionTool(seat, { action: "queue", question: "No extent?" }), /extent are required/);
  assert.match(await runDecisionTool(seat, { action: "queue", question: "Rest on Sundays?", extent: "Scheduling only.", criteria: "Care." }),
    /opened proposal report_/);
  assert.match(await runDecisionTool(seat, { action: "ask", question: "Should the inbox show talks in progress?" }), /asked Yu/);
  const listed = await runDecisionTool(seat, { action: "list" });
  assert.match(listed, /Rest on Sundays\?/);
  assert.match(listed, /Meet in YOUI\?/);
  const inbox = spawnSync(process.env.YOUI_COLLAB_BUN || "bun", [REAL_HOST, "person", "inbox", `--workspace=${seeded.workspace}`, `--db=${seeded.db}`], { encoding: "utf8" });
  const reports = JSON.parse(inbox.stdout.trim().split("\n").at(-1)).inbox.addressed_reports;
  assert.equal(reports[0].from_actor, "youi");
});
