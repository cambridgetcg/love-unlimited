// YOUI minds slice M4 (docs/YOUI-MINDS-DESIGN.md §3.4): council through the
// Collab table, and delegate to sessions on this device's table
// (youi-web/table-tools.mjs).
//
// Most tests drive the tools against a fake table whose seats record every
// Collab tool call. The server tests check the capability wiring with a fake
// agent on 127.0.0.1. The end-to-end tests run the installed Collab MCP bundle
// under Bun against a scratch journal in a temp dir, and are skipped, saying
// why, when either is missing. Nothing here reads the Keychain or the real
// journal at ~/.local/share/agenttool/collab.sqlite.

import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { localJournal } from "../youi-web/decision-bridge.mjs";
import { DecisionSeat, runDecisionTool } from "../youi-web/decision-seat.mjs";
import {
  DELEGATE_OFFER_TTL_SECONDS,
  TableSeats,
  runCouncilTool,
  runDelegateTool,
} from "../youi-web/table-tools.mjs";

const REPO_ROOT = new URL("../", import.meta.url).pathname;
const REAL_JOURNAL = join(homedir(), ".local", "share", "agenttool", "collab.sqlite");

// ── A fake table ────────────────────────────────────────────────────

function collabError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

/**
 * An in-memory Collab table. `seat(options)` stands in for DecisionSeat (the
 * createSeat hook of TableSeats): starting one joins its root's workspace as
 * a live session, and every tool call it makes is recorded in `calls`.
 */
function fakeTable() {
  const calls = [];
  const seats = [];
  const workspaces = new Map();
  const tasks = new Map();
  const handoffs = new Map();
  const proposals = new Map();
  const events = [];
  let counter = 0;
  const next = prefix => `${prefix}_${++counter}`;

  function addWorkspace(id, root) {
    workspaces.set(id, { workspace: { id, name: root.split("/").at(-1), root_path: root, event_head_sequence: 0 }, sessions: [] });
  }
  function addSession(workspaceId, session) {
    workspaces.get(workspaceId).sessions.push({
      liveness: "live", role: null, last_seen_at: "2026-10-29T12:00:00.000Z", ...session,
    });
  }
  function record(workspaceId, type, entityId, payload = {}) {
    const workspace = workspaces.get(workspaceId).workspace;
    workspace.event_head_sequence += 1;
    events.push({
      workspace_id: workspaceId, sequence: workspace.event_head_sequence, type, entity_id: entityId,
      occurred_at: `2026-10-29T12:00:${String(workspace.event_head_sequence).padStart(2, "0")}.000Z`, payload,
    });
  }
  function requireWorkspace(id) {
    const entry = workspaces.get(id);
    if (!entry) throw collabError("workspace_not_found", `Workspace '${id}' was not found`);
    return entry;
  }
  function requireTask(workspaceId, id) {
    requireWorkspace(workspaceId);
    const task = tasks.get(id);
    if (!task || task.workspace_id !== workspaceId) throw collabError("task_not_found", `Task '${id}' was not found`);
    return task;
  }

  function handle(seat, name, args) {
    switch (name) {
      case "collab_workspace_status": {
        const { workspace, sessions } = requireWorkspace(args.workspace_id);
        return { workspace: { ...workspace }, active_sessions: sessions.map(session => ({ ...session })) };
      }
      case "collab_task_create": {
        requireWorkspace(args.workspace_id);
        const task = {
          id: next("task"), workspace_id: args.workspace_id, title: args.title, description: args.description ?? null,
          work_mode: args.work_mode ?? "edit", path_scopes: args.path_scopes ?? [], status: "open", effective_status: "open",
          review_status: "not_required", assignee: null, assignee_session_id: null, assignee_liveness: null,
          lease_id: null, version: 1,
        };
        tasks.set(task.id, task);
        record(args.workspace_id, "task.created", task.id);
        return { task: { ...task } };
      }
      case "collab_task_claim": {
        const task = requireTask(args.workspace_id, args.task_id);
        if (task.version !== args.expected_version) throw collabError("version_conflict", "Stale version");
        Object.assign(task, {
          status: "claimed", effective_status: "claimed", assignee: seat.actor, assignee_session_id: seat.sessionId,
          assignee_liveness: "live", lease_id: next("lease"), version: task.version + 1,
        });
        record(args.workspace_id, "task.claimed", task.id);
        return { task: { ...task } };
      }
      case "collab_task_release": {
        const task = requireTask(args.workspace_id, args.task_id);
        Object.assign(task, { status: "open", effective_status: "open", assignee: null, assignee_session_id: null,
          assignee_liveness: null, lease_id: null, version: task.version + 1 });
        return { task: { ...task } };
      }
      case "collab_handoff_offer": {
        const task = requireTask(args.workspace_id, args.task_id);
        if (task.lease_id !== args.lease_id || task.assignee_session_id !== seat.sessionId) {
          throw collabError("lease_mismatch", "The caller does not hold this lease");
        }
        if (task.version !== args.expected_version) throw collabError("version_conflict", "Stale version");
        if ([...handoffs.values()].some(offer => offer.task_id === task.id && offer.status === "pending")) {
          throw collabError("handoff_pending", "Task already has a pending handoff");
        }
        const handoff = {
          id: next("handoff"), task_id: task.id, from_session_id: seat.sessionId, to_session_id: args.to_session_id,
          status: "pending", expires_at: new Date(Date.now() + args.ttl_seconds * 1000).toISOString(),
        };
        handoffs.set(handoff.id, handoff);
        task.version += 1;
        record(args.workspace_id, "handoff.offered", handoff.id, { task_id: task.id });
        return { handoff: { ...handoff }, task: { ...task } };
      }
      case "collab_task_get":
        return { task: { ...requireTask(args.workspace_id, args.task_id) } };
      case "collab_events_since": {
        const head = requireWorkspace(args.workspace_id).workspace.event_head_sequence;
        const page = events.filter(event => event.workspace_id === args.workspace_id && event.sequence > args.after_sequence)
          .slice(0, args.limit);
        const last = page.at(-1)?.sequence ?? args.after_sequence;
        return { events: page, next_cursor: last, head_sequence: head, has_more: last < head };
      }
      case "collab_proposal_open": {
        const { sessions } = requireWorkspace(args.workspace_id);
        const id = next("report");
        const status = proposalStatus({
          id, body: args.question, fromActor: seat.actor, extent: args.extent, author: seat.sessionId,
          voices: sessions.filter(session => session.liveness === "live")
            .map(session => ({ voice_id: session.id, live_session_ids: [session.id] })),
        });
        proposals.set(id, status);
        return { proposal: status };
      }
      case "collab_proposal_status": {
        requireWorkspace(args.workspace_id);
        const status = proposals.get(args.proposal_report_id);
        if (!status) throw collabError("report_not_found", "No such proposal");
        return { proposal: status };
      }
      default:
        throw new Error(`the fake table has no ${name}`);
    }
  }

  function seat({ root, actor }) {
    const fake = {
      root, actor, workspaceId: null, sessionId: null, starts: 0, closes: 0,
      async start() {
        if (!this.sessionId) {
          const entry = [...workspaces.values()].find(candidate => candidate.workspace.root_path === root);
          this.starts += 1;
          this.workspaceId = entry.workspace.id;
          this.sessionId = next(`session_${actor}`);
          addSession(this.workspaceId, { id: this.sessionId, actor, role: "youi chat agents" });
        }
        return this;
      },
      async tool(name, args) {
        calls.push({ seat: this.sessionId, name, args: structuredClone(args) });
        return handle(this, name, args);
      },
      async close() { this.closes += 1; },
    };
    seats.push(fake);
    return fake;
  }

  /** The named session answers an offer, as collab_handoff_respond would. */
  function respond(handoffId, response, session) {
    const handoff = handoffs.get(handoffId);
    const task = tasks.get(handoff.task_id);
    handoff.status = response === "accept" ? "accepted" : "declined";
    if (response === "accept") {
      Object.assign(task, { assignee: session.actor, assignee_session_id: session.id, lease_id: next("lease"), version: task.version + 1 });
    }
    record(task.workspace_id, `handoff.${handoff.status}`, handoffId, { task_id: task.id });
  }

  const named = name => calls.filter(call => call.name === name);
  return { calls, named, seats, tasks, proposals, addWorkspace, addSession, seat, respond };
}

function proposalStatus({ id, body, fromActor, extent, author, voices = [], position = "open", personState = "awaiting",
  mayProceed = false, closed = false, acts = [], challenges = [], supports = [] }) {
  return {
    rule: "consent/v1",
    proposal: { id, body, from_actor: fromActor },
    terms: { extent, closes_at: "2026-10-30T00:00:00.000Z", voices, author_voice_id: author },
    ai: {
      position, closed, unresolved_challenge_report_ids: challenges, qualifying_support_report_ids: supports,
      awaiting_voice_ids: [],
    },
    person: { state: personState, acts },
    may_proceed: mayProceed,
  };
}

const HOME_ROOT = "/kingdom/love-unlimited";
const OTHER_ROOT = "/kingdom/agenttool";
const PEER = { id: "session_peer", actor: "claude-peer", role: "reviewer" };

function setup() {
  const table = fakeTable();
  table.addWorkspace("ws_home", HOME_ROOT);
  table.addWorkspace("ws_other", OTHER_ROOT);
  table.addSession("ws_home", PEER);
  table.addSession("ws_home", { id: "session_stale", actor: "codex-old", liveness: "stale", last_seen_at: "2026-10-20T00:00:00.000Z" });
  table.addSession("ws_other", { id: "session_far", actor: "claude-far" });
  const seats = new TableSeats({ root: HOME_ROOT, createSeat: options => table.seat(options) });
  return { table, seats };
}

const EDIT_TASK = {
  action: "offer",
  to_session: PEER.id,
  title: "Tighten the seat close",
  description: "Make close() release leases.\nCompletion tests: node --test tests/test_youi_table_tools.mjs passes.",
  kind: "edit",
  path_scopes: ["youi-web/decision-seat.mjs"],
};

// ── council ─────────────────────────────────────────────────────────

test("council open opens a proposal through YOUI's seat and says plainly how it settles", async () => {
  const { table, seats } = setup();
  const text = await runCouncilTool(seats.home, {
    action: "open",
    question: "Should YOUI rest on Sundays?",
    criteria: "Care for Yu's attention.",
    extent: "Scheduling of YOUI's autonomous loop only.",
    known_conflicts: "The lab runs on Sundays.",
    topic: "principle:rest",
  });
  const [opened] = table.named("collab_proposal_open");
  assert.equal(table.calls.length, 1);
  assert.match(opened.args.idempotency_key, /^youi-/);
  assert.deepEqual({ ...opened.args, idempotency_key: "k" }, {
    workspace_id: "ws_home",
    idempotency_key: "k",
    question: "Should YOUI rest on Sundays?",
    criteria: "Care for Yu's attention.",
    extent: "Scheduling of YOUI's autonomous loop only.",
    known_conflicts: "The lab runs on Sundays.",
    topic: "principle:rest",
  });
  assert.match(text, /opened proposal report_\d+ on this device's table \(workspace ws_home\)/);
  assert.match(text, /council never decides by vote/i);
  assert.match(text, /only Yu agrees or rejects, with Touch ID/);
  assert.match(text, /authorises nothing/);
  assert.match(text, /- session_peer: live session session_peer/, "the agents' snapshot of voices");
  assert.doesNotMatch(text, /- session_stale/, "a stale session is not in the snapshot");
  assert.doesNotMatch(text, new RegExp(`- ${seats.home.sessionId}:`), "YOUI's own voice is not counted");
  assert.match(text, /are not voices/);
});

test("council open refuses without criteria or extent and sends nothing; decision queue shares its path", async () => {
  const { table, seats } = setup();
  const refused = await runCouncilTool(seats.home, { action: "open", question: "Rest?", extent: "Scheduling." });
  assert.match(refused, /COUNCIL open: refused\. criteria is required/);
  assert.match(await runCouncilTool(seats.home, { action: "open" }), /question, criteria, extent are required/);
  assert.match(await runCouncilTool(seats.home, { action: "vote", question: "Rest?" }), /COUNCIL usage/);
  assert.equal(table.calls.length, 0);

  // The decision tool's queue opens its proposals through the same function.
  const queued = await runDecisionTool(seats.home, { action: "queue", question: "Rest?", extent: "Scheduling." });
  assert.match(queued, /DECISION: opened proposal report_\d+/);
  const [opened] = table.named("collab_proposal_open");
  assert.equal(opened.args.criteria, "As argued in the question.");
  assert.equal(opened.args.workspace_id, "ws_home");
});

test("council status reads the proposal: the agents' position, Yu's state, may_proceed and the extent", async () => {
  const { table, seats } = setup();
  await seats.homeSeat();
  table.proposals.set("report_talk", proposalStatus({
    id: "report_talk", body: "Rest on Sundays?", fromActor: "youi", extent: "Scheduling only.", author: seats.home.sessionId,
    position: "challenged", challenges: ["report_c1"],
  }));
  table.proposals.set("report_done", proposalStatus({
    id: "report_done", body: "Ship M4?", fromActor: "youi", extent: "Draft PRs only.", author: seats.home.sessionId,
    position: "consented", supports: ["report_s1"], personState: "agreed", mayProceed: true, closed: true,
    acts: [{ act: "agree", basis: "user_verified_passkey" }],
  }));

  const talking = await runCouncilTool(seats.home, { action: "status", proposal: "report_talk" });
  assert.deepEqual(table.named("collab_proposal_status")[0].args, { workspace_id: "ws_home", proposal_report_id: "report_talk" });
  assert.match(talking, /Agents: challenged — a challenge stands/);
  assert.match(talking, /Standing challenges: 1/);
  assert.match(talking, /Yu: awaiting\./);
  assert.match(talking, /may_proceed: false — nothing is authorised/);
  assert.match(talking, /Extent: Scheduling only\./);
  assert.match(talking, /never decides by vote/);

  const agreed = await runCouncilTool(seats.home, { action: "status", proposal: "report_done" });
  assert.match(agreed, /Agents: agents consented/);
  assert.match(agreed, /Yu: agreed \(latest act: agree, user_verified_passkey\)/);
  assert.match(agreed, /may_proceed: true — Yu agreed; work may proceed within the extent below, and no further/);

  const before = table.calls.length;
  assert.match(await runCouncilTool(seats.home, { action: "status" }), /COUNCIL status: refused\. proposal is required/);
  assert.equal(table.calls.length, before);
  assert.match(await runCouncilTool(seats.home, { action: "status", proposal: "report_none" }),
    /COUNCIL status: the table answered with an error \(report_not_found\)/);
});

// ── delegate ────────────────────────────────────────────────────────

test("delegate offer creates a task, claims it as YOUI's seat and offers it to the named session", async () => {
  const { table, seats } = setup();
  const text = await runDelegateTool(seats, EDIT_TASK);
  assert.deepEqual(table.calls.map(call => call.name),
    ["collab_workspace_status", "collab_task_create", "collab_task_claim", "collab_handoff_offer"]);
  const own = seats.home.sessionId;
  assert.ok(table.calls.every(call => call.seat === own), "YOUI uses only its own seat");
  const [, created, claimed, offered] = table.calls;
  assert.deepEqual({ ...created.args, idempotency_key: "k" }, {
    workspace_id: "ws_home", idempotency_key: "k", title: EDIT_TASK.title, description: EDIT_TASK.description,
    work_mode: "edit", path_scopes: ["youi-web/decision-seat.mjs"],
  });
  assert.deepEqual({ ...claimed.args, idempotency_key: "k" }, {
    workspace_id: "ws_home", task_id: "task_2", idempotency_key: "k", expected_version: 1, ttl_seconds: DELEGATE_OFFER_TTL_SECONDS,
  });
  assert.equal(offered.args.task_id, "task_2");
  assert.equal(offered.args.to_session_id, PEER.id);
  assert.equal(offered.args.expected_version, 2);
  assert.match(offered.args.lease_id, /^lease_/);
  assert.equal(offered.args.ttl_seconds, 3600);
  assert.match(offered.args.summary, /Advisory coordination, not a lock or authority; accept or decline in your own session/);
  for (const call of table.calls) {
    assert.doesNotMatch(JSON.stringify(call.args), /token|credential|session_file/i, `${call.name} carries no credential`);
  }

  assert.match(text, /offered task task_2 \("Tighten the seat close", edit\) to session session_peer \(claude-peer, reviewer\)/);
  assert.match(text, /Offer handoff_\d+: pending until /);
  assert.match(text, /accepts or declines in its own session/);
  assert.match(text, /advisory coordination, not a lock or authority/);
  assert.match(text, /the session may decline/);
  assert.match(text, /Path scopes: youi-web\/decision-seat\.mjs\./);
});

test("delegate offer refuses before creating anything", async () => {
  const { table, seats } = setup();
  await seats.homeSeat();
  const cases = [
    [{ description: "Make close() release leases." }, /description must include completion tests/],
    [{ path_scopes: [] }, /an edit task needs at least one path scope/],
    [{ path_scopes: undefined }, /an edit task needs at least one path scope/],
    [{ path_scopes: ["/etc/passwd"] }, /is not repository-relative/],
    [{ path_scopes: ["~/notes"] }, /is not repository-relative/],
    [{ path_scopes: ["youi-web/../../secrets"] }, /reaches outside the repository/],
    [{ path_scopes: ["./"] }, /names the whole repository/],
    [{ kind: "lock" }, /kind must be one of coordination, read_only, edit/],
    [{ title: "" }, /title is required/],
    [{ to_session: undefined }, /to_session is required/],
    [{ to_session: seats.home.sessionId }, /is YOUI's own seat/],
    [{ to_session: "session_stale" }, /Session session_stale \(codex-old\) is stale, last seen 2026-10-20/],
    [{ to_session: "session_nobody" }, /No active session session_nobody sits in workspace ws_home/],
    [{ to_session: "session_far" }, /No active session session_far sits in workspace ws_home/],
    [{ workspace: "ws_unknown" }, /There is no workspace ws_unknown on this device's table/],
  ];
  for (const [change, expected] of cases) {
    const text = await runDelegateTool(seats, { ...EDIT_TASK, ...change });
    assert.match(text, /^DELEGATE offer: refused\./, JSON.stringify(change));
    assert.match(text, expected, JSON.stringify(change));
    assert.match(text, /Nothing was created or offered\./);
  }
  assert.equal(table.named("collab_task_create").length, 0);
  assert.equal(table.named("collab_handoff_offer").length, 0);
  assert.equal(table.seats.length, 1, "an unknown workspace starts no seat");

  // A coordination or read-only task needs no path scope.
  assert.match(await runDelegateTool(seats, { ...EDIT_TASK, kind: "coordination", path_scopes: undefined }), /offered task/);
  assert.equal(table.named("collab_task_create")[0].args.path_scopes, undefined);
});

test("delegate is not offered in autonomous mode, and an unknown action only explains", async () => {
  const { table, seats } = setup();
  assert.match(await runDelegateTool(seats, EDIT_TASK, { autonomous: true }), /not offered in autonomous mode/);
  assert.match(await runDelegateTool(seats, { action: "assign" }), /DELEGATE usage/);
  assert.equal(table.calls.length, 0);
  assert.equal(seats.home.starts, 0, "no seat was started");
});

test("delegate seats lists the workspace's sessions, marking YOUI's own seat and stale ones", async () => {
  const { table, seats } = setup();
  const text = await runDelegateTool(seats, { action: "seats" });
  assert.deepEqual(table.calls.map(call => [call.name, call.args]), [["collab_workspace_status", { workspace_id: "ws_home" }]]);
  assert.match(text, /1 live session YOUI can offer a task to, of 3 active/);
  assert.match(text, /- session_peer · claude-peer, reviewer · live · .* · may be offered a task/);
  assert.match(text, /- session_stale · codex-old · stale · .* · stale, not a target/);
  assert.match(text, new RegExp(`- ${seats.home.sessionId} · youi, youi chat agents · live · .* · YOUI's own seat, not a target`));
  assert.match(text, /the same sessions the decision room's Seats tab lists/);

  // Another workspace's seats are read through the home seat; no seat starts there.
  const far = await runDelegateTool(seats, { action: "seats", workspace: "ws_other" });
  assert.match(far, /- session_far · claude-far · live/);
  assert.equal(table.seats.length, 1);
  assert.match(await runDelegateTool(seats, { action: "seats", workspace: "ws_unknown" }), /There is no workspace ws_unknown/);
});

test("delegate status reports whether the offer was accepted, declined or is still pending", async () => {
  const { table, seats } = setup();
  table.addSession("ws_home", { id: "session_second", actor: "codex-two" });
  const first = (await runDelegateTool(seats, EDIT_TASK)).match(/offered task (\S+)/)[1];

  const pending = await runDelegateTool(seats, { action: "status", task: first });
  const get = table.named("collab_task_get").at(-1);
  assert.deepEqual(get.args, { workspace_id: "ws_home", task_id: first });
  assert.match(pending, /Held by: youi .* — YOUI's own seat\./);
  assert.match(pending, /pending until .*; the session has not answered\./);
  assert.match(pending, /Accepted: not yet\./);

  const handoff = table.named("collab_handoff_offer")[0];
  const handoffId = pending.match(/Offer (handoff_\d+)/)[1];
  table.respond(handoffId, "accept", PEER);
  const accepted = await runDelegateTool(seats, { action: "status", task: first });
  assert.match(accepted, /Held by: claude-peer \(session session_peer, live\)\./);
  assert.match(accepted, new RegExp(`Offer ${handoffId} to session session_peer \\(claude-peer\\): accepted at `));
  assert.match(accepted, /Accepted: yes\./);
  assert.equal(handoff.args.to_session_id, PEER.id);

  // A declined offer leaves the task with YOUI's seat, which can offer it to someone else.
  const second = (await runDelegateTool(seats, { ...EDIT_TASK, title: "Second", path_scopes: ["docs/YOUI-DECISIONS.md"] }))
    .match(/offered task (\S+)/)[1];
  const secondOffer = table.named("collab_handoff_offer").at(-1);
  const secondHandoff = (await runDelegateTool(seats, { action: "status", task: second })).match(/Offer (handoff_\d+)/)[1];
  table.respond(secondHandoff, "decline", PEER);
  const declined = await runDelegateTool(seats, { action: "status", task: second });
  assert.match(declined, /: declined at /);
  assert.match(declined, /Accepted: no\./);
  assert.match(declined, /YOUI's seat still holds the advisory lease/);
  assert.equal(secondOffer.args.to_session_id, PEER.id);

  const before = table.calls.length;
  const again = await runDelegateTool(seats, { action: "offer", task: second, to_session: "session_second" });
  assert.match(again, new RegExp(`offered task ${second} \\("Second", edit\\) to session session_second`));
  assert.deepEqual(table.calls.slice(before).map(call => call.name),
    ["collab_workspace_status", "collab_task_get", "collab_handoff_offer"], "a held task is offered again without a new task or claim");

  assert.match(await runDelegateTool(seats, { action: "status" }), /DELEGATE status: refused\. task is required/);
  assert.match(await runDelegateTool(seats, { action: "status", task: "task_404" }), /There is no task task_404 in workspace ws_home/);
  assert.match(await runDelegateTool(seats, { action: "status", task: first, workspace: "ws_unknown" }),
    /There is no workspace ws_unknown/);
});

test("another workspace gets one YOUI seat, started on first use and closed with the rest", async () => {
  const { table, seats } = setup();
  const offer = { ...EDIT_TASK, workspace: "ws_other", to_session: "session_far" };
  const first = await runDelegateTool(seats, offer);
  assert.match(first, /offered task \S+ .* to session session_far .* workspace ws_other\./);
  assert.match(first, /Next: delegate status, task \S+, workspace ws_other\./);
  const taskId = first.match(/offered task (\S+)/)[1];
  assert.match(await runDelegateTool(seats, { ...offer, title: "Again" }), /offered task/);
  assert.equal(table.seats.length, 2, "one seat per workspace, not one per call");
  const [home, far] = table.seats;
  assert.equal(far.root, OTHER_ROOT);
  assert.equal(far.actor, "youi");
  assert.equal(far.starts, 1);
  assert.equal(far.workspaceId, "ws_other");
  for (const call of table.calls.filter(call => ["collab_task_create", "collab_task_claim", "collab_handoff_offer"].includes(call.name))) {
    assert.equal(call.seat, far.sessionId, `${call.name} runs as YOUI's seat in that workspace`);
    assert.equal(call.args.workspace_id, "ws_other");
  }
  assert.deepEqual([...seats.ownSessionIds()].sort(), [home.sessionId, far.sessionId].sort());
  assert.match(await runDelegateTool(seats, { ...offer, to_session: far.sessionId }), /is YOUI's own seat/);
  const status = await runDelegateTool(seats, { action: "status", task: taskId, workspace: "ws_other" });
  assert.match(status, new RegExp(`status of task ${taskId} .* in workspace ws_other`));
  assert.match(status, /Accepted: not yet\./, "the offer is read from that workspace's journal");

  await seats.close();
  assert.equal(home.closes, 1);
  assert.equal(far.closes, 1);
});

// ── The server's wiring ─────────────────────────────────────────────

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

const VLLM_MODEL = "Qwen/Qwen2.5-72B-Instruct-AWQ";

// GET /v1/models lists the model; each POST streams the next scripted reply,
// { text } or { tool: { id, name, input } }, as OpenAI-style deltas.
async function scriptedAgent(t, script) {
  const requests = [];
  let next = 0;
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      let body = null;
      try { body = JSON.parse(Buffer.concat(chunks).toString("utf-8")); } catch {}
      requests.push({ method: req.method, path: req.url, body });
      if (req.method === "GET" && req.url === "/v1/models") {
        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ object: "list", data: [{ id: VLLM_MODEL, object: "model" }] }));
      }
      const step = script[next++] || { text: "nothing more scripted" };
      const delta = step.tool
        ? { tool_calls: [{ index: 0, id: step.tool.id, function: { name: step.tool.name, arguments: JSON.stringify(step.tool.input) } }] }
        : { content: step.text };
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.write(`data: ${JSON.stringify({ model: body?.model, choices: [{ delta, finish_reason: null }] })}\n\n`);
      res.write(`data: ${JSON.stringify({
        choices: [{ delta: {}, finish_reason: step.tool ? "tool_calls" : "stop" }],
        usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
      })}\n\n`);
      res.end("data: [DONE]\n\n");
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    turns: () => requests.filter(r => r.method === "POST" && r.path === "/v1/chat/completions").map(r => r.body),
  };
}

async function startServer(t, env) {
  const child = spawn(process.execPath, ["youi-web/server.mjs"], { cwd: REPO_ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
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
  return { port, child };
}

// A server whose seat could never reach a journal: no Collab MCP, a scratch journal path.
function serverEnv(t, agent, capabilities) {
  const dir = mkdtempSync(join(tmpdir(), "youi-table-server-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const env = {
    ...process.env, PORT: "0", YOUI_TEST: "1", TRUTH_DETECTOR_ENABLED: "0", AUTONOMOUS: "0", OLLAMA_API_KEY: "",
    KINGDOM_AGENT: "alpha", OLLAMA_VLLM_BASE_URL: agent.baseUrl, OLLAMA_LOCAL_BASE_URL: "http://127.0.0.1:9",
    YOUI_COLLAB_MCP: join(dir, "no-collab-mcp"), YOUI_COLLAB_DB: join(dir, "collab.sqlite"),
    AGENTOOL_COLLAB_DB: join(dir, "collab.sqlite"),
  };
  delete env.YOUI_CAPABILITIES;
  delete env.YOUI_CAPABILITY_PROFILE;
  if (capabilities === "safe") env.YOUI_CAPABILITY_PROFILE = "safe";
  else env.YOUI_CAPABILITIES = capabilities;
  return env;
}

async function chatSession(port) {
  const origin = `http://127.0.0.1:${port}`;
  const bootstrap = await request(port);
  const pairs = (bootstrap.headers["set-cookie"] || []).map(value => value.split(";", 1)[0]);
  const csrf = decodeURIComponent((pairs.find(pair => pair.startsWith("youi_csrf=")) || "=").split("=")[1]);
  const page = "page-table-tools-000000000001";
  const created = await request(port, {
    path: "/api/sessions", method: "POST",
    headers: { Cookie: pairs.join("; "), Origin: origin, "X-YOUI-CSRF": csrf, "X-YOUI-Page": page },
    body: { label: "table", agent: "alpha", model: VLLM_MODEL },
  });
  assert.equal(created.status, 201, created.text);
  const headers = {
    Cookie: pairs.join("; "), Origin: origin, "X-YOUI-CSRF": csrf, "X-YOUI-Page": page,
    "X-YOUI-Session": created.json.session.id, "X-YOUI-Session-Token": created.json.sessionToken,
  };
  return async message => {
    const response = await fetch(`${origin}/api/chat`, {
      method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ message }),
    });
    assert.equal(response.status, 200);
    return (await response.text()).split("\n\n").filter(Boolean).map(chunk => ({
      event: chunk.match(/^event: (.+)$/m)?.[1],
      data: JSON.parse(chunk.match(/^data: (.+)$/m)?.[1] || "null"),
    }));
  };
}

test("the safe profile offers council but not delegate, which needs collab:delegate", async t => {
  const agent = await scriptedAgent(t, [
    { text: "Hello." },
    { tool: { id: "call-delegate", name: "delegate", input: { action: "seats" } } },
    { text: "I cannot delegate here." },
    { tool: { id: "call-council", name: "council", input: { action: "open", question: "Rest?", criteria: "Care.", extent: "Scheduling." } } },
    { text: "The table is not reachable." },
  ]);
  const env = serverEnv(t, agent, "safe");
  const { port } = await startServer(t, env);
  const chat = await chatSession(port);

  await chat("hello");
  const first = agent.turns()[0];
  const offered = first.tools.map(tool => tool.function.name);
  assert.ok(offered.includes("council"), "council rides on decisions:read/write, both in safe");
  assert.ok(offered.includes("decision"));
  assert.ok(!offered.includes("delegate"), "collab:delegate is not in the safe profile");
  const council = first.tools.find(tool => tool.function.name === "council").function;
  assert.match(council.description, /council never decides by vote/);
  assert.match(council.description, /authorises nothing except through Yu's own act/);
  assert.match(council.description, /decision tool/);
  const system = first.messages[0].content;
  assert.match(system, /council {5}— Put a question to the agents at the table/);
  assert.match(system, /delegate {4}— Offer a task to a live session at this device's table/);
  assert.match(system, /goes to council \(or the decision tool\), through the table/);

  const events = await chat("who can take a task?");
  const result = events.find(event => event.event === "tool_result");
  assert.match(result.data.result, /Permission denied: tool "delegate" requires capability "collab:delegate"/);

  // council open is allowed in safe; with no Collab server installed it says so at once.
  const started = Date.now();
  const opened = (await chat("take it to council")).find(event => event.event === "tool_result");
  assert.match(opened.data.result, /COUNCIL open: the table answered with an error: The Collab server could not start/);
  assert.ok(Date.now() - started < 10_000, "a missing Collab server fails the call, not its timeout");
  assert.ok(!existsSync(env.YOUI_COLLAB_DB), "no journal was written");
});

test("YOUI_CAPABILITIES can grant collab:delegate; council open still needs decisions:write", async t => {
  const agent = await scriptedAgent(t, [
    { text: "Hello." },
    { tool: { id: "call-council", name: "council", input: { action: "open", question: "Rest?", criteria: "Care.", extent: "Scheduling." } } },
    { text: "I may not open proposals here." },
  ]);
  const { port } = await startServer(t, serverEnv(t, agent,
    "chat,status:read,sessions:manage,settings:write,models:use,decisions:read,collab:delegate"));
  const chat = await chatSession(port);

  await chat("hello");
  const first = agent.turns()[0];
  const offered = first.tools.map(tool => tool.function.name);
  assert.ok(offered.includes("delegate"), "delegate is offered once collab:delegate is granted");
  assert.ok(offered.includes("council"), "council is offered to read with decisions:read");
  const delegate = first.tools.find(tool => tool.function.name === "delegate").function;
  assert.match(delegate.description, /advisory coordination, not a lock or authority/);
  assert.match(delegate.description, /the session may decline/);
  assert.match(delegate.description, /completion tests/);

  const events = await chat("take it to council");
  const result = events.find(event => event.event === "tool_result");
  assert.match(result.data.result, /Permission denied: tool "council" requires capability "decisions:write"/);
});

// ── End to end, against the installed Collab MCP bundle ─────────────

const BUN = "/opt/homebrew/bin/bun";
const BUNDLE = join(homedir(), ".local", "share", "agenttool", "collab", "current", "agenttool-collab-mcp.js");
const realSkip = !existsSync(BUN)
  ? `Bun is not installed at ${BUN}`
  : !existsSync(BUNDLE) ? `the Collab MCP bundle is not installed at ${BUNDLE}` : false;

// A scratch journal in a temp dir: YOUI_COLLAB_DB and AGENTOOL_COLLAB_DB both name it.
function scratchTable(t) {
  const dir = mkdtempSync(join(tmpdir(), "youi-table-e2e-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const db = join(dir, "collab.sqlite");
  const env = { ...process.env, YOUI_COLLAB_MCP: BUNDLE, YOUI_COLLAB_BUN: BUN, YOUI_COLLAB_DB: db, AGENTOOL_COLLAB_DB: db };
  assert.equal(localJournal(env), db);
  assert.notEqual(localJournal(env), REAL_JOURNAL);
  assert.ok(db.startsWith(dir));
  const repo = name => {
    const path = join(dir, name);
    mkdirSync(path);
    return path;
  };
  return { dir, db, env, repo };
}

const NO_CREDENTIALS = /session_token|bearer|credential_file/i;

test("end to end: delegate offers a session a task it accepts, and council's proposal reads from the same journal",
  { skip: realSkip, timeout: 60_000 }, async t => {
    const { dir, db, env, repo } = scratchTable(t);
    const root = repo("repo");
    const seats = new TableSeats({ env, root });
    const peer = new DecisionSeat({ env, root, actor: "e2e-peer", role: "second session" });
    t.after(async () => { await seats.close(); await peer.close(); });
    await peer.start();

    const listed = await runDelegateTool(seats, { action: "seats" });
    assert.match(listed, new RegExp(`- ${peer.sessionId} · e2e-peer, second session · live · .* · may be offered a task`));
    assert.match(listed, new RegExp(`- ${seats.home.sessionId} · youi, youi chat agents · live · .* · YOUI's own seat, not a target`));
    assert.equal(seats.home.workspaceId, peer.workspaceId);
    assert.ok(existsSync(db), "the scratch journal holds the table");

    assert.match(await runDelegateTool(seats, { action: "offer", to_session: seats.home.sessionId, title: "x",
      description: "Completion tests: none", kind: "coordination" }), /is YOUI's own seat/);
    const offered = await runDelegateTool(seats, {
      action: "offer",
      to_session: peer.sessionId,
      title: "Read the scratch journal",
      description: "Look over the table.\nCompletion tests: you accept this offer and report what you saw.",
      kind: "coordination",
    });
    assert.match(offered, /DELEGATE: offered task task_\S+ \("Read the scratch journal", coordination\)/);
    const taskId = offered.match(/offered task (\S+)/)[1];
    assert.match(await runDelegateTool(seats, { action: "status", task: taskId }), /Accepted: not yet\./);

    // The second session sees the offer in its own session, and accepts it there.
    const nextState = await peer.tool("collab_next", { workspace_id: peer.workspaceId });
    const incoming = nextState.handoff_offers.find(entry => entry.task.id === taskId);
    assert.ok(incoming, "the offer reaches the named session");
    assert.equal(incoming.handoff.to_session_id, peer.sessionId);
    assert.equal(incoming.handoff.from_session_id, seats.home.sessionId);
    assert.match(incoming.handoff.summary, /not a lock or authority/);
    await peer.tool("collab_handoff_respond", {
      workspace_id: peer.workspaceId, handoff_id: incoming.handoff.id, idempotency_key: "e2e-accept",
      expected_version: incoming.task.version, response: "accept",
    });
    const status = await runDelegateTool(seats, { action: "status", task: taskId });
    assert.match(status, new RegExp(`Held by: e2e-peer \\(session ${peer.sessionId}, live\\)\\.`));
    assert.match(status, new RegExp(`Offer ${incoming.handoff.id} to session ${peer.sessionId} \\(e2e-peer\\): accepted at `));
    assert.match(status, /Accepted: yes\./);

    const opened = await runCouncilTool(seats.home, {
      action: "open", question: "Should YOUI rest on Sundays?", criteria: "Care.", extent: "Scheduling only.",
    });
    const proposalId = opened.match(/opened proposal (report_\S+) on this device's table/)[1];
    assert.match(opened, new RegExp(`- ${peer.sessionId}: live session ${peer.sessionId}`), "the peer is in the snapshot");
    const councilStatus = await runCouncilTool(seats.home, { action: "status", proposal: proposalId });
    assert.match(councilStatus, /Agents: agents talking/);
    assert.match(councilStatus, /Yu: awaiting\./);
    assert.match(councilStatus, /may_proceed: false/);
    // The other session reads the same proposal from the same journal.
    const { proposal } = await peer.tool("collab_proposal_status", { workspace_id: peer.workspaceId, proposal_report_id: proposalId });
    assert.equal(proposal.proposal.id, proposalId);
    assert.equal(proposal.proposal.from_actor, "youi");
    assert.equal(proposal.terms.extent, "Scheduling only.");

    for (const text of [listed, offered, status, opened, councilStatus]) assert.doesNotMatch(text, NO_CREDENTIALS);
    await seats.close();
    await peer.close();
    assert.deepEqual(readdirSync(join(dir, "collab-sessions")), [], "every session ended and removed its credential file");
  });

test("end to end: an offer in another workspace starts a second YOUI seat, and stopping releases what it held",
  { skip: realSkip, timeout: 60_000 }, async t => {
    const { env, repo } = scratchTable(t);
    const seats = new TableSeats({ env, root: repo("love") });
    const far = new DecisionSeat({ env, root: repo("elsewhere"), actor: "e2e-far" });
    t.after(async () => { await seats.close(); await far.close(); });
    await far.start();
    await seats.homeSeat();
    assert.notEqual(far.workspaceId, seats.home.workspaceId);

    assert.match(await runDelegateTool(seats, { action: "seats", workspace: "ws_missing" }), /There is no workspace ws_missing/);
    const offered = await runDelegateTool(seats, {
      action: "offer", workspace: far.workspaceId, to_session: far.sessionId, title: "Edit a file",
      description: "Edit notes.md.\nCompletion tests: notes.md says hello.", kind: "edit", path_scopes: ["notes.md"],
    });
    assert.match(offered, new RegExp(`workspace ${far.workspaceId}\\.`));
    const taskId = offered.match(/offered task (\S+)/)[1];
    assert.equal(seats.ownSessionIds().size, 2, "one seat at home, one in the other workspace");
    const held = (await far.tool("collab_task_get", { workspace_id: far.workspaceId, task_id: taskId })).task;
    assert.equal(held.assignee, "youi");
    assert.deepEqual(held.path_scopes, ["notes.md"]);

    // Stopping YOUI ends both seats; the unaccepted task is released and its offer lapses.
    await seats.close();
    const released = (await far.tool("collab_task_get", { workspace_id: far.workspaceId, task_id: taskId })).task;
    assert.equal(released.status, "open");
    assert.equal(released.assignee_session_id, null);
    const { handoff_offers: offers } = await far.tool("collab_next", { workspace_id: far.workspaceId });
    assert.equal(offers.filter(entry => entry.task.id === taskId).length, 0, "the lapsed offer is no longer pending");
  });

test("end to end: the server's delegate offers through the real table, and stopping the server releases the task",
  { skip: realSkip, timeout: 60_000 }, async t => {
    const { dir, db, env, repo } = scratchTable(t);
    const love = repo("love");
    const peer = new DecisionSeat({ env, root: love, actor: "e2e-peer" });
    t.after(() => peer.close());
    await peer.start();
    const agent = await scriptedAgent(t, [
      { tool: { id: "call-delegate", name: "delegate", input: {
        action: "offer", to_session: peer.sessionId, title: "Say hello", kind: "coordination",
        description: "Greet the table.\nCompletion tests: a hello report is on the table.",
      } } },
      { text: "Offered." },
    ]);
    const serverEnvironment = {
      ...serverEnv(t, agent, "chat,status:read,sessions:manage,settings:write,models:use,decisions:read,decisions:write,collab:delegate"),
      LOVE_HOME: love, YOUI_COLLAB_MCP: BUNDLE, YOUI_COLLAB_BUN: BUN, YOUI_COLLAB_DB: db, AGENTOOL_COLLAB_DB: db,
    };
    assert.equal(localJournal(serverEnvironment), db);
    const { port, child } = await startServer(t, serverEnvironment);
    const chat = await chatSession(port);

    const result = (await chat("offer the peer a hello")).find(event => event.event === "tool_result");
    assert.match(result.data.result, new RegExp(`DELEGATE: offered task (\\S+) \\("Say hello", coordination\\) to session ${peer.sessionId}`));
    const taskId = result.data.result.match(/offered task (\S+)/)[1];
    const held = (await peer.tool("collab_task_get", { workspace_id: peer.workspaceId, task_id: taskId })).task;
    assert.equal(held.assignee, "youi");
    const { handoff_offers: offers } = await peer.tool("collab_next", { workspace_id: peer.workspaceId });
    assert.equal(offers.filter(entry => entry.task.id === taskId).length, 1, "the peer sees the offer");

    // SIGTERM: the server ends its seat's session before exiting, so the unaccepted task is released.
    const exited = new Promise(resolve => child.once("exit", resolve));
    child.kill("SIGTERM");
    await exited;
    const released = (await peer.tool("collab_task_get", { workspace_id: peer.workspaceId, task_id: taskId })).task;
    assert.equal(released.status, "open");
    assert.equal(released.assignee_session_id, null);
    assert.deepEqual(readdirSync(join(dir, "collab-sessions")), [`${peer.sessionId}.json`],
      "the server's seat removed its credential file; only the peer's own remains");
  });
