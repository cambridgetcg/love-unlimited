// decision-seat.mjs — YOUI's own seat at this device's Collab table.
//
// YOUI's chat agents take part in decisions the way every other agent does:
// as a credential-bound Collab session, through the installed Collab MCP
// server over stdio. Their `decision` tool opens proposals, asks Yu
// something (a report addressed to Yu's inbox), and reads where proposals
// stand; `council` (table-tools.mjs) opens its proposals through the same
// openProposal below. Nothing here records a person's act: only Yu agrees or
// rejects, with Touch ID on the desk's own page. This replaces the old Kingdom
// Decision Queue (tools/decision.py, decisions/queue.json), whose history
// stays readable.

import { spawn } from "child_process";
import crypto from "crypto";
import { homedir } from "os";
import { join } from "path";
import { localJournal } from "./decision-bridge.mjs";
import { sanitizedChildEnv } from "./subprocess-env.mjs";

const CALL_TIMEOUT_MS = 20_000;

function mcpEntry(env) {
  return env.YOUI_COLLAB_MCP
    || join(homedir(), ".local", "share", "agenttool", "collab", "current", "agenttool-collab-mcp.js");
}

/** One stdio MCP client to the Collab server, with one bound session. */
export class DecisionSeat {
  constructor({ env = process.env, root, actor = "youi", role = "youi chat agents" } = {}) {
    this.env = env;
    this.root = root;
    this.actor = actor;
    this.role = role;
    this.child = null;
    this.nextId = 1;
    this.pending = new Map();
    this.ready = null;
    this.workspaceId = null;
    this.sessionId = null;
    this.closing = null;
  }

  start() {
    if (this.ready) return this.ready;
    this.ready = (async () => {
      const entry = mcpEntry(this.env);
      const runtime = /\.(m?js|ts)$/.test(entry)
        ? { file: this.env.YOUI_COLLAB_BUN || "/opt/homebrew/bin/bun", args: ["--no-env-file", "--config=/dev/null", entry] }
        : { file: entry, args: [] };
      this.child = spawn(runtime.file, runtime.args, {
        env: sanitizedChildEnv({
          source: this.env,
          home: homedir(),
          purpose: "collab-seat",
          extra: { AGENTOOL_COLLAB_DB: localJournal(this.env) },
        }),
        stdio: ["pipe", "pipe", "ignore"],
      });
      let buffer = "";
      this.child.stdout.setEncoding("utf-8");
      this.child.stdout.on("data", chunk => {
        buffer += chunk;
        let index;
        while ((index = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, index);
          buffer = buffer.slice(index + 1);
          let message;
          try { message = JSON.parse(line); } catch { continue; }
          const waiter = this.pending.get(message.id);
          if (waiter) {
            this.pending.delete(message.id);
            waiter(message);
          }
        }
      });
      const child = this.child;
      const gone = message => {
        if (this.child !== child) return;
        for (const waiter of this.pending.values()) waiter({ error: { message } });
        this.pending.clear();
        this.ready = null;
        this.child = null;
      };
      child.once("exit", () => gone("The Collab server exited"));
      // A runtime or bundle that cannot start fails the call now, not at its timeout.
      child.once("error", error => gone(`The Collab server could not start (${error.code || error.message})`));
      child.stdin.on("error", () => {});
      await this.request("initialize", {
        protocolVersion: "2025-06-18",
        capabilities: {},
        clientInfo: { name: "youi-decision-seat", version: "1" },
      });
      this.notify("notifications/initialized");
      const started = await this.tool("collab_session_start", { root_path: this.root, actor: this.actor, role: this.role });
      this.workspaceId = started.workspace.id;
      this.sessionId = started.session.id;
      return this;
    })();
    this.ready.catch(() => { this.ready = null; });
    return this.ready;
  }

  notify(method, params = {}) {
    this.child?.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params })}\n`);
  }

  request(method, params) {
    if (!this.child) return Promise.reject(new Error("The Collab server is not running"));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("The Collab server did not answer in time"));
      }, CALL_TIMEOUT_MS);
      this.pending.set(id, message => {
        clearTimeout(timer);
        if (message.error) reject(new Error(message.error.message || "Collab error"));
        else resolve(message.result);
      });
      this.child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    });
  }

  async tool(name, args) {
    const result = await this.request("tools/call", { name, arguments: args });
    const content = result?.structuredContent ?? {};
    if (result?.isError) {
      const error = new Error(content.message || content.error || "Collab refused");
      error.code = content.error;
      throw error;
    }
    return content;
  }

  /** Ends the session and stops the server; resolves once it has exited. */
  close() {
    const child = this.child;
    if (!child) return Promise.resolve();
    this.closing ??= (async () => {
      // A lease this seat still holds is a delegate offer nobody accepted yet.
      // Ending the session releases it and the offer lapses, instead of leaving
      // the task claimed by a seat that has gone.
      await this.tool("collab_session_end", { reason: "YOUI server stopped", release_leases: true }).catch(() => {});
      const exited = new Promise(resolve => {
        if (this.child !== child) resolve();
        else child.once("exit", resolve).once("error", resolve);
      });
      child.kill();
      await exited;
      this.closing = null;
    })();
    return this.closing;
  }
}

/** The agents' position under consent/v1, in words. */
export const POSITION = {
  open: "agents talking", challenged: "challenged", consented: "agents consented",
  split: "agents split", lapsed: "lapsed", withdrawn: "withdrawn", superseded: "superseded",
};

/** The chat tool. Returns text for the model; never records a person's act. */
export async function runDecisionTool(seat, input = {}) {
  const action = String(input.action || "");
  if (action === "resolve") {
    return "DECISION: only Yu resolves a decision, with Touch ID in YOUI's decision room (🤝 Decisions). "
      + "No agent can agree or reject for Yu.";
  }
  if (!["queue", "add", "propose", "ask", "list", "pending"].includes(action)) {
    return "DECISION usage: action=queue (question, extent, criteria) | ask (question) | list";
  }
  await seat.start();
  const key = `youi-${crypto.randomUUID()}`;
  if (action === "ask") {
    if (!input.question) return "DECISION ask: question required";
    const { report } = await seat.tool("collab_report_append", {
      workspace_id: seat.workspaceId,
      idempotency_key: key,
      kind: "observation",
      body: String(input.question),
      to_person: true,
    });
    return `DECISION: asked Yu (report ${report.id}). It is in the decision room's inbox; it informs and authorises nothing.`;
  }
  if (action === "list" || action === "pending") {
    const { proposals = [] } = await seat.tool("collab_proposal_status", { workspace_id: seat.workspaceId, limit: 20 });
    if (proposals.length === 0) return "DECISION: no proposals on this table.";
    return proposals.map(status => `- ${status.proposal.id}: ${status.proposal.body.slice(0, 120)} `
      + `[${POSITION[status.ai.position] || status.ai.position}; Yu ${status.person.state}`
      + `${status.may_proceed ? "; may proceed within its extent" : ""}]`).join("\n");
  }
  if (!input.question || !input.extent) {
    return "DECISION queue: question and extent are required (extent = what agreement would authorise, and where it stops)";
  }
  const question = input.recommendation
    ? `${input.question}\nRecommendation: ${input.recommendation}`
    : String(input.question);
  const proposal = await openProposal(seat, {
    question,
    criteria: input.criteria || input.context || "As argued in the question.",
    extent: input.extent,
    known_conflicts: input.known_conflicts,
    topic: input.topic,
  }, key);
  return `DECISION: opened proposal ${proposal.proposal.id} on this device's table. `
    + "The agents settle it first; then Yu agrees or rejects with Touch ID in the decision room.";
}

/**
 * Opens a proposal on this seat's table: the decision tool's queue and council
 * both come here. Returns the proposal's consent status as Collab reports it,
 * with its terms and the snapshot of voices taken at open.
 */
export async function openProposal(seat, { question, criteria, extent, known_conflicts, topic }, key = `youi-${crypto.randomUUID()}`) {
  await seat.start();
  const { proposal } = await seat.tool("collab_proposal_open", {
    workspace_id: seat.workspaceId,
    idempotency_key: key,
    question: String(question),
    criteria: String(criteria),
    extent: String(extent),
    ...(known_conflicts ? { known_conflicts: String(known_conflicts) } : {}),
    ...(topic ? { topic: String(topic) } : {}),
  });
  return proposal;
}
