// table-tools.mjs — council and delegate: the Kingdom tools that reach real
// agents, through this device's Collab table (docs/YOUI-MINDS-DESIGN.md §3.4,
// slice M4). They used to message sister instances over HIVE, retired on
// 2026-10-13; the sisters are now the agents at the table.
//
// - council opens a Collab proposal through YOUI's own seat, the same path as
//   the decision tool's queue (decision-seat.mjs, openProposal). The other
//   agents support or challenge it under lazy consensus; then only Yu agrees
//   or rejects, with Touch ID, in the decision room. Nothing is decided by a
//   vote, and the in-process minds (mind-tools.mjs) are not voices.
// - delegate creates a Collab task on this device's table, claims it as YOUI's
//   seat and offers it to one named live session, which accepts or declines in
//   its own session. It is advisory coordination, not a lock or authority.
//
// One MCP process is one bound session, so TableSeats keeps one seat per
// workspace: the home seat (the decision and council tools), and one more for
// each other workspace delegate offers in, started on first use. YOUI uses
// only its own seats; it never reads, passes or reveals a session credential.
// Offering to another device's table (an agent seat over SSH) is slice M5,
// which waits for its own design and Yu's yes.

import crypto from "crypto";
import { DecisionSeat, POSITION, openProposal } from "./decision-seat.mjs";

export const TABLE_TOOL_NAMES = Object.freeze(["council", "delegate"]);
export const DELEGATE_KINDS = Object.freeze(["coordination", "read_only", "edit"]);
// How long an offer stays open, and the claim's TTL floor (Collab's maximum).
export const DELEGATE_OFFER_TTL_SECONDS = 3600;
// Seats in workspaces other than the home seat's: each is one MCP process.
export const MAX_WORKSPACE_SEATS = 4;
// A description names its completion tests on a line such as "Completion tests: …".
export const COMPLETION_TESTS = /completion tests?\s*:\s*\S/i;
const EVENT_PAGE = 200;
const MAX_EVENT_PAGES = 10;
const ID = /^[A-Za-z0-9][A-Za-z0-9_:.-]{0,199}$/;

const isText = value => typeof value === "string" && value.trim().length > 0;
const isId = value => typeof value === "string" && ID.test(value);
const given = value => value !== undefined && value !== null && value !== "";
const key = () => `youi-${crypto.randomUUID()}`;

function clip(text, max = 200) {
  const value = String(text ?? "").trim();
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

/** A refusal: the tool answers with its message and nothing more is sent. */
class Refusal extends Error {}

// ── Seats ───────────────────────────────────────────────────────────

/** YOUI's seats at this device's table: the home seat, and one per other workspace delegate uses. */
export class TableSeats {
  constructor({ root, env, actor = "youi", createSeat = options => new DecisionSeat(options) } = {}) {
    this.root = root;
    this.env = env;
    this.actor = actor;
    this.createSeat = createSeat;
    this.home = createSeat({ env, root, actor });
    this.elsewhere = new Map(); // workspace id -> Promise<seat>
    this.seats = new Set([this.home]);
    // Offers this server made, by `${workspace}:${task}`: kept only while it runs.
    this.offers = new Map();
  }

  async homeSeat() {
    await this.home.start();
    return this.home;
  }

  /** The session ids of every seat YOUI holds, so none is ever a delegate target. */
  ownSessionIds() {
    return new Set([...this.seats].map(seat => seat.sessionId).filter(Boolean));
  }

  /** A started seat in this workspace, if YOUI has one, without starting any. */
  existing(workspaceId) {
    for (const seat of this.seats) if (seat.workspaceId === workspaceId && seat.sessionId) return seat;
    return null;
  }

  /** YOUI's seat in a workspace on this device's table, started on first use. */
  async seatFor(workspaceId) {
    const home = await this.homeSeat();
    if (!given(workspaceId) || workspaceId === home.workspaceId) return home;
    if (!isId(workspaceId)) throw new Refusal(`The workspace must be a workspace id from this device's table; ${JSON.stringify(String(workspaceId)).slice(0, 80)} is not one.`);
    if (!this.elsewhere.has(workspaceId)) {
      if (this.elsewhere.size >= MAX_WORKSPACE_SEATS) {
        throw new Refusal(`YOUI already sits in ${MAX_WORKSPACE_SEATS} other workspaces, its limit while it runs, so it starts no seat in ${workspaceId}.`);
      }
      const pending = (async () => {
        const { workspace } = await readWorkspace(home, workspaceId);
        const seat = this.createSeat({ env: this.env, root: workspace.root_path, actor: this.actor });
        try {
          await seat.start();
        } catch (error) {
          await seat.close();
          throw error;
        }
        if (seat.workspaceId !== workspaceId) {
          await seat.close();
          throw new Refusal(`A seat started at ${workspace.root_path} joined workspace ${seat.workspaceId}, not ${workspaceId}, so it was closed and nothing was offered.`);
        }
        this.seats.add(seat);
        return seat;
      })();
      this.elsewhere.set(workspaceId, pending);
      pending.catch(() => this.elsewhere.delete(workspaceId));
    }
    const seat = await this.elsewhere.get(workspaceId);
    await seat.start();
    return seat;
  }

  /** Ends every seat's session; tasks they still hold are released and their offers lapse. */
  close() {
    return Promise.all([
      this.home.close(),
      ...[...this.elsewhere.values()].map(pending => pending.then(seat => seat.close(), () => {})),
    ]);
  }
}

async function readWorkspace(seat, workspaceId) {
  try {
    return await seat.tool("collab_workspace_status", { workspace_id: workspaceId });
  } catch (error) {
    if (error.code === "workspace_not_found") {
      throw new Refusal(`There is no workspace ${workspaceId} on this device's table. Leave workspace out for YOUI's own, `
        + "or name one the decision room's Table tab lists.");
    }
    throw error;
  }
}

async function readTask(seat, workspaceId, taskId) {
  try {
    const { task } = await seat.tool("collab_task_get", { workspace_id: workspaceId, task_id: taskId });
    return task;
  } catch (error) {
    if (error.code === "workspace_not_found") {
      throw new Refusal(`There is no workspace ${workspaceId} on this device's table.`);
    }
    if (error.code === "task_not_found") throw new Refusal(`There is no task ${taskId} in workspace ${workspaceId}.`);
    throw error;
  }
}

// ── council ─────────────────────────────────────────────────────────

const COUNCIL_SETTLES = "How it settles: the other agents at the table answer with supports or challenges, and a "
  + "challenge stands until its own voice resolves it. Council never decides by vote, and the minds you call through "
  + "review, ensemble or layerthink are not voices. Once the agents have consented, only Yu agrees or rejects, with "
  + "Touch ID, in the decision room (🤝 Decisions). Until Yu agrees it authorises nothing, and Yu's agreement "
  + "authorises only its extent.";

const POSITION_MEANS = {
  open: "no challenge stands, and the agents have not yet consented",
  challenged: "a challenge stands; it holds until its own voice resolves it",
  consented: "the agents consented; it waits on Yu, who agrees or rejects with Touch ID in the decision room",
  split: "the consultation closed with a challenge standing",
  lapsed: "the consultation closed with no support from another voice; silence is not consent",
  withdrawn: "its author withdrew it",
  superseded: "a revision replaced it",
};

function voiceLines(terms) {
  const others = (terms?.voices || []).filter(voice => voice.voice_id !== terms.author_voice_id);
  if (!others.length) {
    return ["- none: no other agent sat at the table when it opened. Agents who join may still answer before it closes; "
      + "silence is not consent."];
  }
  return others.map(voice => `- ${voice.voice_id}: live session${voice.live_session_ids.length === 1 ? "" : "s"} `
    + `${voice.live_session_ids.join(", ")}`);
}

function councilOpened(status, seat) {
  const { proposal, terms, ai } = status;
  return [
    `COUNCIL: opened proposal ${proposal.id} on this device's table (workspace ${seat.workspaceId}); `
      + `the consultation closes ${terms.closes_at}.`,
    `Question: ${clip(proposal.body, 300)}`,
    `Extent: ${clip(terms.extent, 300)}`,
    `The agents' position now: ${POSITION[ai.position] || ai.position}.`,
    `Voices at the table when it opened (the agents' snapshot; your own voice, ${terms.author_voice_id}, is not counted):`,
    ...voiceLines(terms),
    COUNCIL_SETTLES,
    `Next: council status, proposal ${proposal.id}.`,
  ].join("\n");
}

function councilStatus(status) {
  const { proposal, terms, ai, person } = status;
  const latest = person.acts?.at(-1);
  return [
    `COUNCIL status of ${proposal.id} (opened by ${proposal.from_actor || "an agent"}): ${clip(proposal.body, 300)}`,
    `Agents: ${POSITION[ai.position] || ai.position} — ${POSITION_MEANS[ai.position] || "as computed from the journal"}. `
      + `Standing challenges: ${ai.unresolved_challenge_report_ids.length}; supports from other voices: `
      + `${ai.qualifying_support_report_ids.length}; voices not yet heard: ${ai.awaiting_voice_ids.length}. `
      + `The consultation ${ai.closed ? "closed" : "closes"} ${terms.closes_at}.`,
    `Yu: ${person.state}${latest ? ` (latest act: ${latest.act}, ${latest.basis})` : ""}.`,
    status.may_proceed
      ? "may_proceed: true — Yu agreed; work may proceed within the extent below, and no further."
      : "may_proceed: false — nothing is authorised.",
    `Extent: ${clip(terms.extent, 300)}`,
    "Council never decides by vote; only Yu's own act at the table authorises anything.",
  ].join("\n");
}

/**
 * The council tool: put a question to the agents at this device's table.
 * open needs question, criteria and extent; status needs proposal. Returns
 * text for the model; never records a person's act.
 */
export async function runCouncilTool(seat, input = {}) {
  const action = String(input.action || "");
  try {
    if (action === "open") {
      const missing = ["question", "criteria", "extent"].filter(field => !isText(input[field]));
      if (missing.length) {
        return `COUNCIL open: refused. ${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} required, as text `
          + "(criteria: how the agents and Yu judge which way to go; extent: what agreement would authorise, and where it stops). "
          + "Nothing was opened.";
      }
      const status = await openProposal(seat, {
        question: input.question,
        criteria: input.criteria,
        extent: input.extent,
        known_conflicts: input.known_conflicts,
        topic: input.topic,
      });
      return councilOpened(status, seat);
    }
    if (action === "status") {
      if (!isId(input.proposal)) {
        return "COUNCIL status: refused. proposal is required: the proposal id council open returned (report_…).";
      }
      await seat.start();
      const { proposal } = await seat.tool("collab_proposal_status", {
        workspace_id: seat.workspaceId,
        proposal_report_id: input.proposal,
      });
      return councilStatus(proposal);
    }
  } catch (error) {
    return `COUNCIL ${action}: the table answered with an error${error.code ? ` (${error.code})` : ""}: `
      + `${clip(error.message || error, 300)}`;
  }
  return "COUNCIL usage: action=open (question, criteria, extent, known_conflicts?, topic?) | status (proposal). "
    + "Council never decides by vote; only Yu agrees or rejects.";
}

// ── delegate ────────────────────────────────────────────────────────

const ADVISORY = "This is advisory coordination, not a lock or authority: it authorises nothing, and the session may decline.";

function pathScopeProblem(scope) {
  if (typeof scope !== "string" || !scope.trim()) return "is empty";
  if (scope.length > 500) return "is longer than 500 characters";
  if (scope.startsWith("/") || scope.startsWith("~")) return "is not repository-relative";
  if (scope.includes("\\") || scope.includes("\0")) return "is not a POSIX path";
  const parts = scope.replace(/^\.\//, "").replace(/\/+$/, "").split("/");
  if (!parts.join("") || parts.every(part => part === ".")) return "names the whole repository";
  if (parts.includes("..")) return "reaches outside the repository";
  return null;
}

// A new task's terms, or { error } naming what is missing.
function readTerms(input) {
  if (!isText(input.title)) return { error: "title is required, as text." };
  if (input.title.length > 300) return { error: "title is longer than 300 characters." };
  if (!isText(input.description) || !COMPLETION_TESTS.test(input.description)) {
    return { error: "description must include completion tests, on a line such as \"Completion tests: …\", saying how "
      + "the session (and you) will know the task is done." };
  }
  if (input.description.length > 8000) return { error: "description is longer than 8000 characters." };
  if (!DELEGATE_KINDS.includes(input.kind)) return { error: `kind must be one of ${DELEGATE_KINDS.join(", ")}.` };
  let scopes = [];
  if (given(input.path_scopes)) {
    if (!Array.isArray(input.path_scopes) || input.path_scopes.length > 128) {
      return { error: "path_scopes must be a list of at most 128 repository-relative paths." };
    }
    for (const scope of input.path_scopes) {
      const problem = pathScopeProblem(scope);
      if (problem) return { error: `path scope ${JSON.stringify(String(scope)).slice(0, 80)} ${problem}; path scopes are repository-relative, such as youi-web/server.mjs.` };
    }
    scopes = input.path_scopes.map(scope => scope.trim());
  }
  if (input.kind === "edit" && !scopes.length) {
    return { error: "an edit task needs at least one path scope (repository-relative), naming what the session may edit." };
  }
  return { title: input.title.trim(), description: input.description, kind: input.kind, scopes };
}

function refuse(action, reason, unchanged = "Nothing was created or offered.") {
  return `DELEGATE ${action}: refused. ${reason} ${unchanged}`.trim();
}

function sessionLabel(session) {
  return `${session.actor}${session.role ? `, ${session.role}` : ""}`;
}

async function offer(seats, input) {
  const target = input.to_session;
  if (!isId(target)) {
    return refuse("offer", "to_session is required: a live session id from delegate seats (the decision room's Seats tab lists the same sessions).");
  }
  const reoffer = given(input.task);
  if (reoffer && !isId(input.task)) return refuse("offer", "task must be a task id from this table.");
  const terms = reoffer ? null : readTerms(input);
  if (terms?.error) return refuse("offer", terms.error);

  const seat = await seats.seatFor(input.workspace);
  const workspaceId = seat.workspaceId;
  if (seats.ownSessionIds().has(target)) {
    return refuse("offer", `${target} is YOUI's own seat; a task is offered to another session, never to YOUI itself.`);
  }
  const table = await readWorkspace(seat, workspaceId);
  const session = (table.active_sessions || []).find(candidate => candidate.id === target);
  if (!session) {
    return refuse("offer", `No active session ${target} sits in workspace ${workspaceId}. Call delegate seats for the live ones.`);
  }
  if (session.liveness !== "live") {
    return refuse("offer", `Session ${target} (${sessionLabel(session)}) is stale, last seen ${session.last_seen_at}; offer only to a live session.`);
  }
  const afterSequence = table.workspace?.event_head_sequence ?? 0;

  let task;
  let claimedHere = false;
  if (reoffer) {
    task = await readTask(seat, workspaceId, input.task);
    if (!COMPLETION_TESTS.test(task.description || "")) {
      return refuse("offer", `Task ${task.id} has no completion tests in its description, so it is not offered; offer a new task that names them.`, "Nothing was offered.");
    }
    const held = task.status === "claimed" && task.lease_id && task.assignee_session_id === seat.sessionId;
    if (!held && task.status !== "open") {
      return refuse("offer", `Task ${task.id} is ${task.effective_status}${task.assignee ? `, held by ${task.assignee}` : ""}; `
        + "only an open task, or one YOUI's seat holds, can be offered.", "Nothing was offered.");
    }
    if (!held) {
      ({ task } = await seat.tool("collab_task_claim", {
        workspace_id: workspaceId, task_id: task.id, idempotency_key: key(),
        expected_version: task.version, ttl_seconds: DELEGATE_OFFER_TTL_SECONDS,
      }));
      claimedHere = true;
    }
  } else {
    const created = (await seat.tool("collab_task_create", {
      workspace_id: workspaceId,
      idempotency_key: key(),
      title: terms.title,
      description: terms.description,
      work_mode: terms.kind,
      ...(terms.scopes.length ? { path_scopes: terms.scopes } : {}),
    })).task;
    try {
      ({ task } = await seat.tool("collab_task_claim", {
        workspace_id: workspaceId, task_id: created.id, idempotency_key: key(),
        expected_version: created.version, ttl_seconds: DELEGATE_OFFER_TTL_SECONDS,
      }));
      claimedHere = true;
    } catch (error) {
      return `DELEGATE offer: created task ${created.id} in workspace ${workspaceId}, but YOUI's seat could not claim it `
        + `(${error.code || "error"}: ${clip(error.message, 200)}), so it was not offered. It stays open on the table. ${ADVISORY}`;
    }
  }

  let offered;
  try {
    offered = await seat.tool("collab_handoff_offer", {
      workspace_id: workspaceId,
      task_id: task.id,
      lease_id: task.lease_id,
      idempotency_key: key(),
      expected_version: task.version,
      to_session_id: target,
      summary: clip(`Offered by YOUI's chat agents: ${task.title}. Advisory coordination, not a lock or authority; `
        + "accept or decline in your own session. The task description names its completion tests.", 4000),
      ttl_seconds: DELEGATE_OFFER_TTL_SECONDS,
    });
  } catch (error) {
    let after = "YOUI's seat still holds it.";
    if (claimedHere) {
      after = await seat.tool("collab_task_release", {
        workspace_id: workspaceId, task_id: task.id, lease_id: task.lease_id, idempotency_key: key(),
        expected_version: task.version, summary: "The offer failed, so YOUI's seat released the task.",
      }).then(() => "YOUI's seat released it, so it is open on the table.", () => "YOUI's seat could not release it either; it still holds it.");
    }
    return `DELEGATE offer: the table refused the offer of task ${task.id} to ${target} `
      + `(${error.code || "error"}: ${clip(error.message, 200)}). ${after} ${ADVISORY}`;
  }
  const { handoff } = offered;
  seats.offers.set(`${workspaceId}:${task.id}`, {
    handoffId: handoff.id, toSession: target, toActor: session.actor, expiresAt: handoff.expires_at, afterSequence,
  });
  return [
    `DELEGATE: offered task ${task.id} ("${clip(task.title, 120)}", ${task.work_mode}) to session ${target} `
      + `(${sessionLabel(session)}) at this device's table, workspace ${workspaceId}.`,
    `Offer ${handoff.id}: pending until ${handoff.expires_at}. The session accepts or declines in its own session; `
      + "nothing is handed over until it accepts.",
    "Until then YOUI's seat holds the task's advisory coordination lease; on acceptance the lease moves to that session.",
    ...(task.path_scopes?.length ? [`Path scopes: ${task.path_scopes.join(", ")}.`] : []),
    ADVISORY,
    `Next: delegate status, task ${task.id}${workspaceId === seats.home.workspaceId ? "" : `, workspace ${workspaceId}`}.`,
  ].join("\n");
}

// The last handoff event for this offer in the journal since it was made.
async function handoffOutcome(seat, workspaceId, record) {
  let after = record.afterSequence;
  let last = null;
  for (let page = 0; page < MAX_EVENT_PAGES; page++) {
    const result = await seat.tool("collab_events_since", { workspace_id: workspaceId, after_sequence: after, limit: EVENT_PAGE });
    const events = result.events || [];
    for (const event of events) {
      if (event.entity_id === record.handoffId && String(event.type).startsWith("handoff.")) last = event;
    }
    if (!result.has_more || !events.length) return { event: last, complete: true };
    after = result.next_cursor;
  }
  return { event: last, complete: false };
}

async function status(seats, input) {
  if (!isId(input.task)) return refuse("status", "task is required: the task id delegate offer returned.", "");
  const home = await seats.homeSeat();
  if (given(input.workspace) && !isId(input.workspace)) return refuse("status", "workspace must be a workspace id from this device's table.", "");
  const workspaceId = given(input.workspace) ? input.workspace : home.workspaceId;
  // Reads need no seat in that workspace; YOUI's own there, if any, or the home seat reads.
  const reader = seats.existing(workspaceId) || home;
  const task = await readTask(reader, workspaceId, input.task);
  const own = seats.ownSessionIds();
  const holderIsYoui = Boolean(task.assignee_session_id) && own.has(task.assignee_session_id);
  const record = seats.offers.get(`${workspaceId}:${task.id}`);

  const lines = [
    `DELEGATE status of task ${task.id} ("${clip(task.title, 120)}", ${task.work_mode}) in workspace ${workspaceId}: `
      + `${task.effective_status}${["pending", "accepted", "changes_requested"].includes(task.review_status) ? `, review ${task.review_status}` : ""}.`,
    task.assignee
      ? `Held by: ${task.assignee} (session ${task.assignee_session_id || "none"}, ${task.assignee_liveness || "no session"})`
        + `${holderIsYoui ? " — YOUI's own seat" : ""}.`
      : "Held by: nobody; the task is open on the table.",
  ];
  let accepted = "unknown";
  if (record) {
    const { event, complete } = await handoffOutcome(reader, workspaceId, record);
    const outcome = event ? String(event.type).slice("handoff.".length) : "offered";
    const target = `session ${record.toSession} (${record.toActor})`;
    if (outcome === "accepted") {
      accepted = "yes";
      lines.push(`Offer ${record.handoffId} to ${target}: accepted at ${event.occurred_at}; the lease moved to that session.`);
    } else if (outcome === "declined" || outcome === "expired") {
      accepted = "no";
      const reason = event.payload?.reason ? ` (${event.payload.reason})` : "";
      lines.push(`Offer ${record.handoffId} to ${target}: ${outcome}${reason} at ${event.occurred_at}.`);
    } else if (Date.parse(record.expiresAt) <= Date.now()) {
      accepted = "no";
      lines.push(`Offer ${record.handoffId} to ${target}: its deadline, ${record.expiresAt}, passed without an answer, so it has lapsed.`);
    } else {
      accepted = "not yet";
      lines.push(`Offer ${record.handoffId} to ${target}: pending until ${record.expiresAt}; the session has not answered`
        + `${complete ? "" : ` in the ${MAX_EVENT_PAGES * EVENT_PAGE} journal events read`}.`);
    }
  } else {
    if (task.assignee_session_id && !holderIsYoui) accepted = "yes, if YOUI offered it to the session that holds it now";
    lines.push("No offer from this YOUI server is on record for this task: YOUI keeps them only while it runs, "
      + "and a stopped seat releases what it held. Read it from the holder above.");
  }
  lines.push(`Accepted: ${accepted}.`);
  if (accepted === "no" && holderIsYoui) {
    lines.push("YOUI's seat still holds the advisory lease, so no other session is offered it. Offer it to another "
      + `live session with delegate offer, task ${task.id}; when YOUI stops, its seat releases it.`);
  }
  if (task.blocker) lines.push(`Blocker: ${clip(task.blocker, 300)}`);
  if (task.latest_progress) lines.push(`Latest progress: ${clip(task.latest_progress, 300)}`);
  if (task.reported_at) {
    lines.push(`Completion: reported by ${task.reported_by} at ${task.reported_at}`
      + (task.accepted_at ? `; accepted by ${task.accepted_by} at ${task.accepted_at}.` : "; not yet accepted by a distinct session."));
  }
  lines.push(ADVISORY);
  return lines.join("\n");
}

async function listSeats(seats, input) {
  const home = await seats.homeSeat();
  if (given(input.workspace) && !isId(input.workspace)) return refuse("seats", "workspace must be a workspace id from this device's table.", "");
  const workspaceId = given(input.workspace) ? input.workspace : home.workspaceId;
  const table = await readWorkspace(home, workspaceId);
  const own = seats.ownSessionIds();
  const sessions = table.active_sessions || [];
  const live = sessions.filter(session => session.liveness === "live" && !own.has(session.id));
  return [
    `DELEGATE seats in workspace ${workspaceId} (${table.workspace?.name || "unnamed"}) at this device's table: `
      + `${live.length} live session${live.length === 1 ? "" : "s"} YOUI can offer a task to, of ${sessions.length} active; `
      + "the same sessions the decision room's Seats tab lists.",
    ...sessions.map(session => {
      const note = own.has(session.id) ? "YOUI's own seat, not a target"
        : session.liveness === "live" ? "may be offered a task" : "stale, not a target";
      return `- ${session.id} · ${sessionLabel(session)} · ${session.liveness} · last seen ${session.last_seen_at} · ${note}`;
    }),
    ...(sessions.length ? [] : ["- (no active sessions)"]),
    "Offer one a task with delegate offer, to_session=<id>. " + ADVISORY,
  ].join("\n");
}

/**
 * The delegate tool: offer a task to a session at this device's table.
 * Returns text for the model. `autonomous` refuses: delegate is not offered
 * in autonomous mode.
 */
export async function runDelegateTool(seats, input = {}, { autonomous = false } = {}) {
  const action = String(input.action || "");
  if (autonomous) {
    return "DELEGATE: not offered in autonomous mode; a task is offered only in a chat turn. Nothing was created or offered.";
  }
  const run = { offer, status, seats: listSeats }[action];
  if (!run) {
    return "DELEGATE usage: action=offer (to_session, title, description with completion tests, kind, path_scopes for "
      + "edit, workspace?; or task to offer an existing one) | status (task, workspace?) | seats (workspace?). " + ADVISORY;
  }
  try {
    return await run(seats, input);
  } catch (error) {
    if (error instanceof Refusal) return refuse(action, error.message, action === "offer" ? undefined : "");
    return `DELEGATE ${action}: the table answered with an error${error.code ? ` (${error.code})` : ""}: `
      + `${clip(error.message || error, 300)}`;
  }
}
