// decision-bridge.mjs — the YOUI decision room's window onto Collab proposals.
//
// YOUI is where Yu and the agents meet on direction: proposals, the agents'
// position, Yu's replies and Yu's own proposals. YOUI never opens the Collab
// journal. Every read and act goes through the person desk
// (`agenttool-collab-host person <verb>`), which validates input, verifies
// passkey assertions and writes the hash-chained journal.
//
// Yu agrees or rejects here only with an enrolled passkey (Touch ID): the
// browser signs the desk's challenge and this bridge forwards the assertion.
// The terminal route and passkey enrolment stay at Yu's own terminal; the desk
// runs in a new session without a controlling terminal, so it cannot prompt.
// Desk notes and proposals are not authenticated as Yu and authorise nothing.
// Contract: docs/superpowers/specs/2026-10-29-collab-yu-ai-consent-design.md
// in cambridgetcg/agenttool.
//
// Routes:
//   GET  /api/decisions/workspaces
//   GET  /api/decisions/proposals?workspace=<id>[&proposal=<id>]
//   GET  /api/decisions/inbox?workspace=<id>
//   GET  /api/decisions/passkeys
//   POST /api/decisions/challenge      { proposal, act, reason? }
//   POST /api/decisions/act            { proposal, act, reason?, assertion }
//   POST /api/decisions/say            { workspace, body, proposal?, idempotency_key }
//   POST /api/decisions/propose        { workspace, question, criteria, extent, ..., idempotency_key }
//   POST /api/decisions/passkey/stage  { credential_id, public_key_spki, algorithm, label? }

import { spawn } from "child_process";
import crypto from "crypto";
import { chmodSync, mkdirSync, writeFileSync } from "fs";
import { homedir } from "os";
import { dirname, join } from "path";
import { HttpError } from "./security.mjs";
import { sanitizedChildEnv } from "./subprocess-env.mjs";

const HOST_TIMEOUT_MS = 15_000;
const MAX_HOST_OUTPUT_BYTES = 4 * 1024 * 1024;
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_:.-]{0,199}$/;
const KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const BASE64URL = /^[A-Za-z0-9_-]+$/;

/** How to reach the person desk. A .js or .ts path runs under Bun. */
export function personDeskInvocation(args, env = process.env) {
  const host = env.YOUI_COLLAB_HOST || "agenttool-collab-host";
  const db = env.YOUI_COLLAB_DB ? [`--db=${env.YOUI_COLLAB_DB}`] : [];
  if (/\.(m?js|ts)$/.test(host)) {
    return { file: env.YOUI_COLLAB_BUN || "bun", args: [host, "person", ...args, ...db] };
  }
  return { file: host, args: ["person", ...args, ...db] };
}

/** Run one person-desk verb and return its single JSON receipt. */
export function runPersonDesk(args, { stdin = null, signal, env = process.env } = {}) {
  const { file, args: argv } = personDeskInvocation(args, env);
  return new Promise((resolve, reject) => {
    const child = spawn(file, argv, {
      env: sanitizedChildEnv({ source: env, home: homedir(), purpose: "person-desk" }),
      // A new session has no controlling terminal, so the desk can never prompt.
      detached: true,
      stdio: [stdin === null ? "ignore" : "pipe", "pipe", "pipe"],
      signal,
    });
    let stdout = "";
    let bytes = 0;
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(value);
    };
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      finish(new HttpError(504, "The person desk did not answer in time", "person_desk_timeout"));
    }, HOST_TIMEOUT_MS);
    child.stdout.on("data", chunk => {
      bytes += chunk.length;
      if (bytes > MAX_HOST_OUTPUT_BYTES) {
        child.kill("SIGTERM");
        finish(new HttpError(502, "The person desk answer was too large", "person_desk_output_too_large"));
        return;
      }
      stdout += chunk.toString("utf-8");
    });
    child.stderr.resume();
    child.once("error", error => {
      finish(new HttpError(
        502,
        error.code === "ENOENT"
          ? "agenttool-collab-host was not found; set YOUI_COLLAB_HOST"
          : "The person desk could not start",
        "person_desk_unavailable",
      ));
    });
    child.once("close", exitCode => {
      const line = stdout.trim().split("\n").at(-1) || "";
      let receipt;
      try {
        receipt = JSON.parse(line);
      } catch {
        finish(new HttpError(502, "The person desk returned no receipt", "person_desk_unreadable"));
        return;
      }
      finish(null, { exitCode, receipt });
    });
    if (stdin !== null) child.stdin.end(stdin);
  });
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

function requireAct(value) {
  if (value !== "agree" && value !== "reject") {
    throw new HttpError(400, "act must be agree or reject", "invalid_decision_input");
  }
  return value;
}

function requireKey(value) {
  if (typeof value !== "string" || !KEY_PATTERN.test(value)) {
    throw new HttpError(400, "idempotency_key must be a retry key", "invalid_decision_input");
  }
  return value;
}

function requireBase64url(value, field, maxLength = 8192) {
  if (typeof value !== "string" || !BASE64URL.test(value) || value.length > maxLength) {
    throw new HttpError(400, `${field} must be base64url`, "invalid_decision_input");
  }
  return value;
}

/** The passkey's relying party: WebAuthn refuses IP addresses, so YOUI must be opened at localhost. */
export function passkeyScope(req) {
  const origin = req.headers.origin;
  let url;
  try {
    url = new URL(origin);
  } catch {
    throw new HttpError(400, "A same-origin request is required", "invalid_decision_input");
  }
  if (url.protocol !== "http:" || url.hostname !== "localhost") {
    throw new HttpError(
      409,
      "Passkeys need a domain: open YOUI at http://localhost, not an IP address",
      "passkey_needs_localhost",
    );
  }
  return { rp_id: "localhost", origin: url.origin };
}

/** Where a staged enrolment waits for Yu to confirm it at the terminal. */
export function stagedEnrollmentPath(env = process.env) {
  return env.YOUI_PASSKEY_STAGING_FILE
    || join(homedir(), ".local", "share", "agenttool", "youi-passkey-enrollment.json");
}

function hostCommandHint(env = process.env) {
  const host = env.YOUI_COLLAB_HOST || "agenttool-collab-host";
  return /\.(m?js|ts)$/.test(host) ? `bun ${host}` : host;
}

function sendReceipt(res, { exitCode, receipt }) {
  const status = exitCode === 0 ? 200 : receipt?.error === "usage_error" ? 400 : 409;
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(receipt));
}

/** Handle /api/decisions/*; returns true when the path belonged here. */
export async function handleDecisionRoute(path, req, res, url, parseBody, { signal, env = process.env } = {}) {
  if (!path.startsWith("/api/decisions/")) return false;
  const desk = (args, options = {}) => runPersonDesk(args, { signal, env, ...options });

  if (req.method === "GET") {
    if (path === "/api/decisions/workspaces") {
      sendReceipt(res, await desk(["workspaces"]));
      return true;
    }
    if (path === "/api/decisions/proposals") {
      const workspace = requireId(url.searchParams.get("workspace"), "workspace");
      const proposal = url.searchParams.get("proposal");
      const limit = url.searchParams.get("limit");
      if (limit !== null && !/^([1-9]|[1-4]\d|50)$/.test(limit)) {
        throw new HttpError(400, "limit must be an integer from 1 to 50", "invalid_decision_input");
      }
      sendReceipt(res, await desk([
        "proposals",
        `--workspace=${workspace}`,
        ...(proposal ? [`--proposal=${requireId(proposal, "proposal")}`] : []),
        ...(limit && !proposal ? [`--limit=${limit}`] : []),
      ]));
      return true;
    }
    if (path === "/api/decisions/inbox") {
      const workspace = requireId(url.searchParams.get("workspace"), "workspace");
      sendReceipt(res, await desk(["inbox", `--workspace=${workspace}`]));
      return true;
    }
    if (path === "/api/decisions/passkeys") {
      sendReceipt(res, await desk(["passkeys"]));
      return true;
    }
    return false;
  }

  if (req.method !== "POST") return false;
  const body = await parseBody(req);

  if (path === "/api/decisions/challenge") {
    const act = requireAct(body.act);
    const reason = act === "reject" ? requireText(body.reason, "reason", 2000) : null;
    sendReceipt(res, await desk([
      "challenge",
      `--proposal=${requireId(body.proposal, "proposal")}`,
      `--act=${act}`,
      ...(reason ? [`--reason=${reason}`] : []),
    ]));
    return true;
  }

  if (path === "/api/decisions/act") {
    const act = requireAct(body.act);
    const reason = act === "reject" ? requireText(body.reason, "reason", 2000) : null;
    const assertion = body.assertion && typeof body.assertion === "object" ? body.assertion : null;
    if (!assertion) {
      throw new HttpError(400, "A passkey assertion is required; YOUI never records an act without one", "invalid_decision_input");
    }
    const forwarded = {
      credential_id: requireBase64url(assertion.credential_id, "credential_id", 1400),
      authenticator_data: requireBase64url(assertion.authenticator_data, "authenticator_data"),
      client_data_json: requireBase64url(assertion.client_data_json, "client_data_json"),
      signature: requireBase64url(assertion.signature, "signature", 512),
      ...(typeof assertion.user_handle === "string" && assertion.user_handle
        ? { user_handle: requireBase64url(assertion.user_handle, "user_handle", 512) }
        : {}),
    };
    sendReceipt(res, await desk([
      act === "agree" ? "consent" : "reject",
      `--proposal=${requireId(body.proposal, "proposal")}`,
      ...(reason ? [`--reason=${reason}`] : []),
      "--passkey-assertion=-",
    ], { stdin: JSON.stringify(forwarded) }));
    return true;
  }

  if (path === "/api/decisions/say") {
    sendReceipt(res, await desk([
      "say",
      `--workspace=${requireId(body.workspace, "workspace")}`,
      `--body=${requireText(body.body, "body", 8000)}`,
      ...(body.proposal ? [`--proposal=${requireId(body.proposal, "proposal")}`] : []),
      `--idempotency-key=${requireKey(body.idempotency_key)}`,
    ]));
    return true;
  }

  if (path === "/api/decisions/propose") {
    const knownConflicts = requireText(body.known_conflicts, "known_conflicts", 4000, { optional: true });
    const topic = requireText(body.topic, "topic", 200, { optional: true });
    const consult = body.consult_seconds;
    if (consult !== undefined && consult !== null && !Number.isSafeInteger(consult)) {
      throw new HttpError(400, "consult_seconds must be an integer", "invalid_decision_input");
    }
    sendReceipt(res, await desk([
      "propose",
      `--workspace=${requireId(body.workspace, "workspace")}`,
      `--question=${requireText(body.question, "question", 8000)}`,
      `--criteria=${requireText(body.criteria, "criteria", 4000)}`,
      `--extent=${requireText(body.extent, "extent", 1000)}`,
      ...(knownConflicts ? [`--known-conflicts=${knownConflicts}`] : []),
      ...(topic ? [`--topic=${topic}`] : []),
      ...(Number.isSafeInteger(consult) ? [`--consult-seconds=${consult}`] : []),
      `--idempotency-key=${requireKey(body.idempotency_key)}`,
    ]));
    return true;
  }

  if (path === "/api/decisions/passkey/stage") {
    // Staging writes a file and nothing else; only Yu's terminal can enrol it.
    const scope = passkeyScope(req);
    const publicKey = requireBase64url(body.public_key_spki, "public_key_spki", 1400);
    if (body.algorithm !== -7) {
      throw new HttpError(400, "Only ES256 (COSE -7) passkeys are supported", "invalid_decision_input");
    }
    const label = requireText(body.label, "label", 80, { optional: true });
    const enrollment = {
      credential_id: requireBase64url(body.credential_id, "credential_id", 1400),
      public_key_spki: publicKey,
      algorithm: -7,
      rp_id: scope.rp_id,
      origin: scope.origin,
      ...(label ? { label } : {}),
    };
    const file = stagedEnrollmentPath(env);
    mkdirSync(dirname(file), { recursive: true, mode: 0o700 });
    writeFileSync(file, `${JSON.stringify(enrollment, null, 2)}\n`, { mode: 0o600 });
    chmodSync(file, 0o600);
    const fingerprint = crypto.createHash("sha256").update(Buffer.from(publicKey, "base64url")).digest("hex");
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(JSON.stringify({
      status: "staged",
      fingerprint,
      file,
      // Passkeys live in the journal, so the command must name the same one YOUI reads.
      command: `${hostCommandHint(env)} person passkey-enroll --file ${file}`
        + (env.YOUI_COLLAB_DB ? ` --db ${env.YOUI_COLLAB_DB}` : ""),
      note: "Run the command in your own terminal and compare the fingerprint before typing yes.",
    }));
    return true;
  }

  return false;
}
