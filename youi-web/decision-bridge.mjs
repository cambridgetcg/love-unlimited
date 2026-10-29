// decision-bridge.mjs — the YOUI decision room's window onto Collab tables.
//
// YOUI is where Yu and the agents meet on direction. Each device in the
// Kingdom keeps its own table: its own Collab journal, its agents' voices and
// its own person desk. YOUI never opens a journal. Every read and desk write
// runs one verb of a desk: this device's (`agenttool-collab-host person`), or
// another device's through its SSH forced command (`person-remote`, which
// reads a JSON argv and never a shell line).
//
// Yu agrees or rejects only on a page the desk serves itself (`person
// ceremony`): the page that shows the bytes decides what Touch ID signs, and
// YOUI's own pages live where agents edit. YOUI only asks this device's desk
// to open that page (with `--via` for another device's table) and links to it.
// Desk notes, desk proposals and their withdrawal are not authenticated as Yu
// and authorise nothing. Contract: the consent design in cambridgetcg/agenttool,
// docs/superpowers/specs/2026-10-29-collab-yu-ai-consent-design.md.
//
// Routes (every one takes ?table=<id> or a "table" body field; default the first):
//   GET  /api/decisions/tables
//   GET  /api/decisions/workspaces
//   GET  /api/decisions/voices?workspace=<id>
//   GET  /api/decisions/proposals?workspace=<id>[&proposal=<id>|&limit=<n>]
//   GET  /api/decisions/inbox[?workspace=<id>]
//   GET  /api/decisions/passkeys
//   POST /api/decisions/say       { workspace, body, proposal?, idempotency_key }
//   POST /api/decisions/propose   { workspace, question, criteria, extent, known_conflicts?, topic?, consult_seconds?, idempotency_key }
//   POST /api/decisions/withdraw  { workspace, proposal, reason, idempotency_key }
//   POST /api/decisions/ceremony  { proposal, act, reason? }  → { url, expires_at }

import { spawn } from "child_process";
import { homedir } from "os";
import { join } from "path";
import { HttpError } from "./security.mjs";
import { sanitizedChildEnv } from "./subprocess-env.mjs";

const HOST_TIMEOUT_MS = 15_000;
const REMOTE_TIMEOUT_MS = 25_000;
const CEREMONY_LIFETIME_MS = 200_000;
const MAX_HOST_OUTPUT_BYTES = 8 * 1024 * 1024;
const MAX_CONCURRENT_DESKS = 3;
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_:.-]{0,199}$/;
const KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const TABLE_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
const SSH_DESTINATION = /^[A-Za-z0-9][A-Za-z0-9._@-]{0,200}$/;

/** The installed desk, unless YOUI_COLLAB_HOST names another. A .js or .ts path runs under Bun. */
function deskCommand(env) {
  return env.YOUI_COLLAB_HOST
    || join(homedir(), ".local", "share", "agenttool", "collab", "current", "agenttool-collab-host.js");
}

/** This device's journal, resolved once and always passed explicitly. */
export function localJournal(env = process.env) {
  return env.YOUI_COLLAB_DB
    || env.AGENTOOL_COLLAB_DB
    || join(homedir(), ".local", "share", "agenttool", "collab.sqlite");
}

/** The tables YOUI can reach: this device's, plus any listed in YOUI_DECISION_TABLES. */
export function decisionTables(env = process.env) {
  const tables = [{ id: "local", label: "This device", ssh: null }];
  if (!env.YOUI_DECISION_TABLES) return tables;
  let listed;
  try {
    listed = JSON.parse(env.YOUI_DECISION_TABLES);
  } catch {
    throw new Error("YOUI_DECISION_TABLES must be JSON: [{\"id\":\"lab\",\"label\":\"Lab\",\"ssh\":\"lab-desk\"}]");
  }
  if (!Array.isArray(listed)) throw new Error("YOUI_DECISION_TABLES must be a JSON array");
  for (const table of listed) {
    if (!table || !TABLE_ID.test(table.id ?? "") || table.id === "local"
      || typeof table.ssh !== "string" || !SSH_DESTINATION.test(table.ssh)) {
      throw new Error("Each YOUI_DECISION_TABLES entry needs an id (a-z, 0-9, -) and an ssh destination");
    }
    tables.push({ id: table.id, label: String(table.label || table.id).slice(0, 60), ssh: table.ssh });
  }
  return tables;
}

function invocation(table, args, env) {
  if (table.ssh) {
    // The remote forced command reads this JSON argv; no shell ever parses it.
    return { file: env.YOUI_DECISION_SSH || "/usr/bin/ssh",
      args: ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", table.ssh, JSON.stringify(args)] };
  }
  const host = deskCommand(env);
  const tail = ["person", ...args, `--db=${localJournal(env)}`];
  if (/\.(m?js|ts)$/.test(host)) {
    return { file: env.YOUI_COLLAB_BUN || "/opt/homebrew/bin/bun",
      args: ["--no-env-file", "--config=/dev/null", host, ...tail] };
  }
  return { file: host, args: tail };
}

// A small gate so many tabs and polls cannot start unbounded desk processes.
let running = 0;
const queued = [];
async function withSlot(task) {
  if (running >= MAX_CONCURRENT_DESKS) await new Promise(resolve => queued.push(resolve));
  running += 1;
  try {
    return await task();
  } finally {
    running -= 1;
    queued.shift()?.();
  }
}

const inflightReads = new Map();

function killGroup(child) {
  try { process.kill(-child.pid, "SIGTERM"); } catch {}
  setTimeout(() => { try { process.kill(-child.pid, "SIGKILL"); } catch {} }, 1_000).unref();
}

/** Run one desk verb and return its JSON receipt. Writes ignore the browser going away. */
function runDesk(table, args, { env, signal, write = false } = {}) {
  const { file, args: argv } = invocation(table, args, env);
  return withSlot(() => new Promise((resolve, reject) => {
    const child = spawn(file, argv, {
      env: sanitizedChildEnv({ source: env, home: homedir(), purpose: "person-desk" }),
      // A new session has no controlling terminal, so the desk can never prompt.
      detached: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const chunks = [];
    let bytes = 0;
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      if (error) reject(error);
      else resolve(value);
    };
    const onAbort = () => {
      killGroup(child);
      finish(new HttpError(499, "The request was cancelled", "request_cancelled"));
    };
    if (signal && !write) signal.addEventListener("abort", onAbort, { once: true });
    const timer = setTimeout(() => {
      killGroup(child);
      finish(write
        ? new HttpError(504, "The desk did not answer in time; the write may or may not have happened, so read again", "person_desk_outcome_unknown")
        : new HttpError(504, "The desk did not answer in time", "person_desk_timeout"));
    }, table.ssh ? REMOTE_TIMEOUT_MS : HOST_TIMEOUT_MS);
    child.stdout.on("data", chunk => {
      bytes += chunk.length;
      if (bytes > MAX_HOST_OUTPUT_BYTES) {
        killGroup(child);
        finish(new HttpError(502, "The desk answer was too large", "person_desk_output_too_large"));
        return;
      }
      chunks.push(chunk);
    });
    child.once("error", error => finish(new HttpError(
      503,
      error.code === "ENOENT" ? "The person desk is not installed here; set YOUI_COLLAB_HOST" : "The person desk could not start",
      "person_desk_unavailable",
    )));
    child.once("close", exitCode => {
      // Decode once, so a multibyte character split across chunks survives.
      const line = Buffer.concat(chunks).toString("utf-8").trim().split("\n").at(-1) || "";
      try {
        finish(null, { exitCode, receipt: JSON.parse(line) });
      } catch {
        finish(new HttpError(502, table.ssh ? "The other device's desk returned no receipt" : "The desk returned no receipt",
          "person_desk_unreadable"));
      }
    });
  }));
}

/** Identical reads in flight share one desk process. */
function readDesk(table, args, options) {
  const key = JSON.stringify([table.id, args]);
  const existing = inflightReads.get(key);
  if (existing) return existing;
  const promise = runDesk(table, args, { ...options, signal: undefined }).finally(() => inflightReads.delete(key));
  inflightReads.set(key, promise);
  return promise;
}

function statusFor(receipt) {
  const code = String(receipt?.error || "");
  if (code === "usage_error" || code.startsWith("invalid_")) return 400;
  if (code === "journal_not_found") return 503;
  if (code === "internal_error" || code.startsWith("remote_desk")) return 502;
  if (code.endsWith("_not_found") || code === "passkey_not_enrolled") return 404;
  return 409;
}

function sendReceipt(res, { exitCode, receipt }) {
  const status = exitCode === 0 ? 200 : statusFor(receipt);
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify({ ...receipt, source: "person_desk" }));
}

function sendJson(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

function requireId(value, field) {
  if (typeof value !== "string" || !ID_PATTERN.test(value)) {
    throw new HttpError(400, `${field} must be a Collab id`, "invalid_decision_input");
  }
  return value;
}

function requireText(value, field, max, { optional = false } = {}) {
  if (value === undefined || value === null || value === "") {
    if (optional) return null;
    throw new HttpError(400, `${field} is required`, "invalid_decision_input");
  }
  if (typeof value !== "string" || value.length > max || value.includes("\0")) {
    throw new HttpError(400, `${field} must be text of at most ${max} characters`, "invalid_decision_input");
  }
  return value;
}

function requireKey(value) {
  if (typeof value !== "string" || !KEY_PATTERN.test(value)) {
    throw new HttpError(400, "idempotency_key must be a retry key", "invalid_decision_input");
  }
  return value;
}

function pickTable(tables, id) {
  const table = id ? tables.find(item => item.id === id) : tables[0];
  if (!table) throw new HttpError(404, "No such table", "decision_table_not_found");
  return table;
}

// One ceremony at a time: its page listens on the passkey's fixed origin.
let ceremony = null;

/** Ask this device's desk to serve a signing page; resolve with its URL. */
function openCeremony(table, args, env) {
  if (ceremony && !ceremony.ended) killGroup(ceremony.child);
  const { file, args: argv } = invocation({ id: "local", ssh: null }, [
    "ceremony", ...args, ...(table.ssh ? [`--via=${table.ssh}`] : []),
  ], env);
  return new Promise((resolve, reject) => {
    const child = spawn(file, argv, {
      env: sanitizedChildEnv({ source: env, home: homedir(), purpose: "person-desk-ceremony" }),
      detached: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const current = { child, ended: false };
    ceremony = current;
    let buffer = Buffer.alloc(0);
    let opened = false;
    const lifetime = setTimeout(() => killGroup(child), CEREMONY_LIFETIME_MS);
    lifetime.unref();
    child.stdout.on("data", chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      let index;
      while ((index = buffer.indexOf(10)) >= 0) {
        const line = buffer.subarray(0, index).toString("utf-8");
        buffer = buffer.subarray(index + 1);
        let receipt;
        try { receipt = JSON.parse(line); } catch { continue; }
        if (!opened && receipt.status === "open" && typeof receipt.url === "string"
          && /^http:\/\/localhost:\d+\/c\/[A-Za-z0-9_-]+$/.test(receipt.url)) {
          opened = true;
          resolve({ url: receipt.url, expires_at: receipt.expires_at });
        } else if (!opened) {
          opened = true;
          reject(Object.assign(new HttpError(statusFor(receipt), receipt.message || "The desk refused the ceremony",
            receipt.error || "ceremony_refused"), { receipt }));
        }
      }
    });
    child.once("error", () => reject(new HttpError(503, "The person desk is not installed here; set YOUI_COLLAB_HOST",
      "person_desk_unavailable")));
    child.once("close", () => {
      current.ended = true;
      clearTimeout(lifetime);
      if (!opened) reject(new HttpError(502, "The desk closed without opening a page", "ceremony_unopened"));
    });
  });
}

/** Handle /api/decisions/*; returns true when the path belonged here. */
export async function handleDecisionRoute(path, req, res, url, parseBody, { signal, env = process.env } = {}) {
  if (!path.startsWith("/api/decisions/")) return false;
  const tables = decisionTables(env);
  const options = { env, signal };
  const respond = async (promise) => {
    try {
      sendReceipt(res, await promise);
    } catch (error) {
      if (!(error instanceof HttpError)) throw error;
      // Report a missing desk plainly instead of logging a stack on every poll.
      sendJson(res, error.statusCode, { error: error.code, message: error.message, source: "youi" });
    }
    return true;
  };

  if (req.method === "GET") {
    if (path === "/api/decisions/tables") {
      sendJson(res, 200, { tables: tables.map(({ id, label, ssh }) => ({ id, label, remote: Boolean(ssh) })) });
      return true;
    }
    const table = pickTable(tables, url.searchParams.get("table"));
    if (path === "/api/decisions/workspaces") return respond(readDesk(table, ["workspaces"], options));
    if (path === "/api/decisions/passkeys") return respond(readDesk(table, ["passkeys"], options));
    if (path === "/api/decisions/voices") {
      const workspace = requireId(url.searchParams.get("workspace"), "workspace");
      return respond(readDesk(table, ["voices", `--workspace=${workspace}`], options));
    }
    if (path === "/api/decisions/proposals") {
      const workspace = requireId(url.searchParams.get("workspace"), "workspace");
      const proposal = url.searchParams.get("proposal");
      const limit = url.searchParams.get("limit");
      if (limit !== null && !/^([1-9]|[1-4]\d|50)$/.test(limit)) {
        throw new HttpError(400, "limit must be an integer from 1 to 50", "invalid_decision_input");
      }
      return respond(readDesk(table, [
        "proposals",
        `--workspace=${workspace}`,
        ...(proposal ? [`--proposal=${requireId(proposal, "proposal")}`] : limit ? [`--limit=${limit}`] : []),
      ], options));
    }
    if (path === "/api/decisions/inbox") {
      const workspace = url.searchParams.get("workspace");
      return respond(readDesk(table, ["inbox", ...(workspace ? [`--workspace=${requireId(workspace, "workspace")}`] : [])], options));
    }
    return false;
  }

  if (req.method !== "POST") return false;
  const body = await parseBody(req);
  const table = pickTable(tables, body.table);
  const write = { ...options, write: true };

  if (path === "/api/decisions/say") {
    return respond(runDesk(table, [
      "say",
      `--workspace=${requireId(body.workspace, "workspace")}`,
      `--body=${requireText(body.body, "body", 8000)}`,
      ...(body.proposal ? [`--proposal=${requireId(body.proposal, "proposal")}`] : []),
      `--idempotency-key=${requireKey(body.idempotency_key)}`,
    ], write));
  }

  if (path === "/api/decisions/propose") {
    const knownConflicts = requireText(body.known_conflicts, "known_conflicts", 4000, { optional: true });
    const topic = requireText(body.topic, "topic", 200, { optional: true });
    const consult = body.consult_seconds;
    if (consult !== undefined && consult !== null && !Number.isSafeInteger(consult)) {
      throw new HttpError(400, "consult_seconds must be an integer", "invalid_decision_input");
    }
    return respond(runDesk(table, [
      "propose",
      `--workspace=${requireId(body.workspace, "workspace")}`,
      `--question=${requireText(body.question, "question", 8000)}`,
      `--criteria=${requireText(body.criteria, "criteria", 4000)}`,
      `--extent=${requireText(body.extent, "extent", 1000)}`,
      ...(knownConflicts ? [`--known-conflicts=${knownConflicts}`] : []),
      ...(topic ? [`--topic=${topic}`] : []),
      ...(Number.isSafeInteger(consult) ? [`--consult-seconds=${consult}`] : []),
      `--idempotency-key=${requireKey(body.idempotency_key)}`,
    ], write));
  }

  if (path === "/api/decisions/withdraw") {
    return respond(runDesk(table, [
      "withdraw",
      `--workspace=${requireId(body.workspace, "workspace")}`,
      `--proposal=${requireId(body.proposal, "proposal")}`,
      `--reason=${requireText(body.reason, "reason", 2000)}`,
      `--idempotency-key=${requireKey(body.idempotency_key)}`,
    ], write));
  }

  if (path === "/api/decisions/ceremony") {
    if (body.act !== "agree" && body.act !== "reject") {
      throw new HttpError(400, "act must be agree or reject", "invalid_decision_input");
    }
    const reason = body.act === "reject" ? requireText(body.reason, "reason", 2000) : null;
    try {
      const opened = await openCeremony(table, [
        `--proposal=${requireId(body.proposal, "proposal")}`,
        `--act=${body.act}`,
        ...(reason ? [`--reason=${reason}`] : []),
      ], env);
      sendJson(res, 200, { status: "open", ...opened, source: "person_desk" });
    } catch (error) {
      if (!(error instanceof HttpError)) throw error;
      sendJson(res, error.statusCode, { ...(error.receipt || {}), error: error.code, message: error.message, source: "person_desk" });
    }
    return true;
  }

  return false;
}

/** How to run one verb of this device's desk; YOUI's terminal uses it for /decisions. */
export function localDeskInvocation(args, env = process.env) {
  return invocation({ id: "local", ssh: null }, args, env);
}

export const __test = { statusFor, invocation, deskCommand };
