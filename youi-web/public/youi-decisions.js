// YOUI decision room — where Yu and the agents meet on direction.
//
// Each device keeps its own table; this room reads them through the person
// desk (/api/decisions/*). Yu agrees or rejects only on the desk's own
// signing page, which this room opens but never renders or scripts: the page
// that shows the bytes decides what Touch ID signs, and this page lives where
// agents edit. Everything agents wrote is untrusted text, rendered with
// textContent only.
(() => {
  "use strict";

  const STORE_PLACE = "youi.decisions.place";
  const STORE_SEEN = "youi.decisions.seen.v2";
  const POLL_LOCAL_MS = 30_000;
  const POLL_REMOTE_MS = 120_000;
  const state = {
    tables: [], table: null, workspaces: [], workspace: null,
    proposals: [], inbox: null, voices: [], passkeys: [],
    selected: null, epoch: 0, forms: {}, summaries: {}, lastRemotePoll: 0, error: null,
  };

  // ── helpers ─────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  /** replaceChildren without turning a skipped (null) part into the text "null". */
  function fill(node, ...children) {
    if (!node) return;
    node.replaceChildren(...children.flat().filter((child) => child !== null && child !== undefined && child !== false));
  }
  function read(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key) ?? "null") ?? fallback; } catch { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
  }
  function relative(iso) {
    const ms = Date.parse(iso) - Date.now();
    const minutes = Math.round(Math.abs(ms) / 60_000);
    const span = minutes < 60 ? `${minutes} min` : minutes < 2880 ? `${Math.round(minutes / 60)} h` : `${Math.round(minutes / 1440)} d`;
    return ms >= 0 ? `in ${span}` : `${span} ago`;
  }
  function withTable(path) {
    const join = path.includes("?") ? "&" : "?";
    return state.table ? `${path}${join}table=${encodeURIComponent(state.table)}` : path;
  }
  async function api(path, body) {
    const response = await fetch(path, body === undefined
      ? { cache: "no-store" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await response.json(); } catch {}
    if (!response.ok || data?.status === "error") {
      const error = new Error(
        response.status === 403 && data?.capability
          ? `YOUI has the "${data.capability}" capability turned off.`
          : (data?.message || data?.error || `Request failed (${response.status})`),
      );
      error.code = data?.error || data?.code;
      error.fromYoui = data?.source !== "person_desk";
      throw error;
    }
    return data;
  }
  function notice(target, text, tone = "info") {
    fill(target, el("div", { class: `dec-notice dec-${tone}` }, text));
  }
  function failure(error) {
    return error.fromYoui ? `YOUI could not do this: ${error.message}` : `The desk refused: ${error.message}`;
  }
  /** One retry key per form until it succeeds, so a double click or retry writes once. */
  function formKey(name) {
    state.forms[name] ??= crypto.randomUUID();
    return state.forms[name];
  }
  async function guarded(button, task) {
    if (button?.disabled) return;
    if (button) button.disabled = true;
    try { await task(); } finally { if (button) button.disabled = false; }
  }

  const POSITION_LABEL = {
    open: "agents talking", challenged: "challenged", consented: "agents consented",
    split: "agents split", lapsed: "lapsed (no support)", withdrawn: "withdrawn", superseded: "superseded",
  };
  const PERSON_LABEL = { awaiting: "awaiting you", agreed: "you agreed", rejected: "you rejected" };
  const SETTLED = new Set(["consented", "split", "lapsed"]);
  function personChip(status) {
    if (status.person.state === "awaiting" && !SETTLED.has(status.ai.position)) return null;
    return el("span", { class: "dec-chip dec-chip-person" }, PERSON_LABEL[status.person.state]);
  }
  function chips(status) {
    return [
      el("span", { class: "dec-chip dec-chip-ai" }, POSITION_LABEL[status.ai.position] || status.ai.position),
      personChip(status),
      status.may_proceed ? el("span", { class: "dec-chip dec-chip-go" }, "may proceed") : null,
      el("span", { class: "dec-dim" }, status.ai.closed ? `closed ${relative(status.terms.closes_at)}` : `closes ${relative(status.terms.closes_at)}`),
    ];
  }
  const isDeskProposal = (status) => status.proposal.from_actor === "person:desk";

  // ── loading ─────────────────────────────────────────────
  async function loadTables() {
    state.tables = (await api("/api/decisions/tables")).tables || [];
    const saved = read(STORE_PLACE, {});
    state.table = state.tables.find((t) => t.id === saved.table)?.id || state.tables[0]?.id || null;
    fill($("dec-table"), state.tables.map((t) => el("option", { value: t.id, selected: t.id === state.table }, t.label)));
  }
  async function loadWorkspaces() {
    const epoch = state.epoch;
    const data = await api(withTable("/api/decisions/workspaces"));
    if (epoch !== state.epoch) return;
    state.workspaces = data.workspaces || [];
    const saved = read(STORE_PLACE, {});
    state.workspace = state.workspaces.find((w) => w.id === saved.workspace)?.id || state.workspaces.at(-1)?.id || null;
    fill($("dec-workspace"), state.workspaces.map((w) =>
      el("option", { value: w.id, selected: w.id === state.workspace }, `${w.name} — ${w.root_path}`)));
  }
  async function loadPlace() {
    if (!state.workspace) return;
    const epoch = state.epoch;
    const ws = encodeURIComponent(state.workspace);
    const [proposals, inbox, voices, passkeys] = await Promise.all([
      api(withTable(`/api/decisions/proposals?workspace=${ws}&limit=50`)),
      api(withTable(`/api/decisions/inbox?workspace=${ws}`)),
      api(withTable(`/api/decisions/voices?workspace=${ws}`)),
      api(withTable("/api/decisions/passkeys")),
    ]);
    // A reply for a table or workspace Yu has since left is dropped.
    if (epoch !== state.epoch) return;
    state.proposals = proposals.proposals || [];
    state.inbox = inbox.inbox || null;
    state.voices = voices.voices || [];
    state.passkeys = passkeys.passkeys || [];
  }
  async function refresh() {
    try {
      state.error = null;
      if (!state.tables.length) await loadTables();
      if (!state.workspaces.length) await loadWorkspaces();
      await loadPlace();
    } catch (error) {
      state.error = error;
    }
    renderAll();
  }
  function renderAll() {
    renderTable();
    renderInbox();
    renderSeats();
    renderPasskeys();
    renderBadge();
  }

  // ── badge: every table, every workspace ────────────────
  function seenKey(table, workspace) { return `${table}/${workspace}`; }
  function renderBadge() {
    const badge = $("decisions-badge");
    if (!badge) return;
    const seen = read(STORE_SEEN, {});
    let total = 0;
    for (const [table, summary] of Object.entries(state.summaries)) {
      for (const row of summary) {
        total += row.awaiting_person;
        if (row.latest_report_id && seen[seenKey(table, row.workspace_id)] !== row.latest_report_id) total += 1;
      }
    }
    badge.textContent = total > 0 ? String(total) : "—";
    badge.classList.toggle("dec-badge-live", total > 0);
  }
  async function pollSummaries() {
    if (!state.tables.length) {
      try { await loadTables(); } catch { return; }
    }
    const now = Date.now();
    const includeRemote = now - state.lastRemotePoll >= POLL_REMOTE_MS;
    if (includeRemote) state.lastRemotePoll = now;
    await Promise.all(state.tables.filter((t) => !t.remote || includeRemote).map(async (table) => {
      try {
        const data = await api(`/api/decisions/inbox?table=${encodeURIComponent(table.id)}`);
        state.summaries[table.id] = data.summary || [];
      } catch {}
    }));
    renderBadge();
  }
  async function poll() {
    await pollSummaries();
    const drawer = $("drawer-decisions");
    if (!drawer?.classList.contains("open") || !state.workspace) return;
    try {
      await loadPlace();
    } catch {
      return;
    }
    // Never rebuild a view Yu may be typing in; the list and side panes only.
    if (!state.selected) renderTable();
    else updateDetailChips();
    renderInbox();
    renderSeats();
  }

  // ── the table ───────────────────────────────────────────
  function renderTable() {
    const list = $("dec-table-list");
    if (!list) return;
    if (state.error) return notice(list, failure(state.error), "warn");
    if (!state.workspace) return notice(list, "No Collab workspace at this table yet. Agents create one when they start a session.");
    if (state.selected) return renderDetail(list, state.proposals.find((p) => p.proposal.id === state.selected));
    if (!state.proposals.length) return notice(list, "No proposals in this workspace yet.");
    fill(list, state.proposals.map((status) => el("button", {
      class: `dec-card dec-pos-${status.ai.position} dec-person-${status.person.state}`,
      onclick: () => { state.selected = status.proposal.id; renderTable(); },
    }, el("div", { class: "dec-card-q" }, status.proposal.body), el("div", { class: "dec-card-meta" }, chips(status)))));
  }

  function field(label, value) {
    if (!value) return null;
    return el("div", { class: "dec-field" }, el("div", { class: "dec-label" }, label), el("div", { class: "dec-value" }, value));
  }

  function updateDetailChips() {
    const status = state.proposals.find((p) => p.proposal.id === state.selected);
    const meta = $("dec-detail-chips");
    if (status && meta) fill(meta, chips(status));
  }

  function renderDetail(list, status) {
    if (!status) { state.selected = null; return renderTable(); }
    const ai = status.ai;
    const onTable = ai.position !== "withdrawn" && ai.position !== "superseded";
    const canAgree = ai.position === "consented" && status.person.state !== "agreed";
    fill(list,
      el("button", { class: "dec-back", onclick: () => { state.selected = null; renderTable(); } }, "← All proposals"),
      el("h3", { class: "dec-title" }, status.proposal.body),
      el("div", { class: "dec-card-meta", id: "dec-detail-chips" }, chips(status)),
      field("Criteria", status.terms.criteria),
      field("Extent — agreement authorises only this", status.terms.extent),
      field("Known conflicts", status.terms.known_conflicts),
      field("Topic", status.terms.topic),
      field("From", isDeskProposal(status) ? "you, via the desk" : status.proposal.from_actor),
      el("div", { class: "dec-field" },
        el("div", { class: "dec-label" }, "The agents"),
        el("div", { class: "dec-value" },
          `${ai.qualifying_support_report_ids.length} qualifying support, `
          + `${ai.unresolved_challenge_report_ids.length} standing challenge, `
          + `${ai.awaiting_voice_ids.length} voice still awaited`)),
      status.person.acts.length ? el("div", { class: "dec-field" },
        el("div", { class: "dec-label" }, "Your acts"),
        status.person.acts.map((act) => el("div", { class: "dec-value dec-act" },
          `${act.act === "agree" ? "agreed" : "rejected"} · ${act.basis.replaceAll("_", " ")} · ${relative(act.created_at)}`
          + (act.reason ? ` · “${act.reason}”` : "")))) : null,
      el("details", { class: "dec-bytes" },
        el("summary", {}, `Exact bytes · sha256 ${status.proposal_sha256.slice(0, 16)}…`),
        el("pre", {}, status.proposal_bytes)),
      onTable ? el("div", { class: "dec-field" },
        el("div", { class: "dec-label" }, "Decide — on the desk's own page, with Touch ID"),
        el("div", { class: "dec-actions" },
          el("button", {
            class: "dec-btn dec-btn-agree",
            disabled: !canAgree,
            title: canAgree ? "" : "You can agree once the agents have consented",
            onclick: (event) => openSigning(event.currentTarget, status, "agree"),
          }, "Agree…"),
          el("button", { class: "dec-btn dec-btn-reject", onclick: () => showReject(status) }, "Reject…"),
          isDeskProposal(status)
            ? el("button", { class: "dec-btn dec-btn-quiet", onclick: () => showWithdraw(status) }, "Withdraw my proposal…")
            : null),
        el("div", { id: "dec-act-box" })) : null,
      el("div", { class: "dec-field" },
        el("div", { class: "dec-label" }, "Reply to the agents"),
        el("textarea", { id: "dec-reply", rows: "3", maxlength: "8000", placeholder: "A desk note. It informs; it authorises nothing." }),
        el("button", { class: "dec-btn", onclick: (event) => sendReply(event.currentTarget, status) }, "Send reply"),
        el("div", { id: "dec-reply-result" })),
    );
  }

  /** Open the desk's signing page. The window opens inside the click so no popup blocker stops it. */
  async function openSigning(button, status, act, reason) {
    const box = $("dec-act-box");
    const page = window.open("", "youi-ceremony");
    if (page) page.opener = null;
    await guarded(button, async () => {
      try {
        const opened = await api("/api/decisions/ceremony", {
          table: state.table, proposal: status.proposal.id, act, ...(act === "reject" ? { reason } : {}),
        });
        if (page) page.location.href = opened.url;
        fill(box,
          el("div", { class: "dec-notice dec-info" },
            "The desk's signing page is open in another window. Read the bytes there, then use Touch ID. "
            + `It closes ${relative(opened.expires_at)}.`),
          el("a", { href: opened.url, target: "youi-ceremony", rel: "noopener noreferrer", class: "dec-mono" }, opened.url),
          el("button", { class: "dec-btn dec-btn-quiet", onclick: () => refresh() }, "I'm done — refresh"));
      } catch (error) {
        page?.close();
        notice(box, failure(error), "warn");
      }
    });
  }

  function showReject(status) {
    const box = $("dec-act-box");
    fill(box,
      el("div", { class: "dec-label" }, "Why are you rejecting it? Your reason is signed with the act."),
      el("textarea", { id: "dec-reason", rows: "2", maxlength: "2000" }),
      el("button", {
        class: "dec-btn dec-btn-reject",
        onclick: (event) => {
          const reason = $("dec-reason").value.trim();
          if (!reason) return notice($("dec-reason-result"), "A reason is required.", "warn");
          openSigning(event.currentTarget, status, "reject", reason);
        },
      }, "Open the signing page"),
      el("div", { id: "dec-reason-result" }));
  }

  function showWithdraw(status) {
    const box = $("dec-act-box");
    fill(box,
      el("div", { class: "dec-label" }, "Why withdraw it? The agents will see this."),
      el("textarea", { id: "dec-withdraw-reason", rows: "2", maxlength: "2000" }),
      el("button", {
        class: "dec-btn dec-btn-quiet",
        onclick: (event) => guarded(event.currentTarget, async () => {
          const reason = $("dec-withdraw-reason").value.trim();
          if (!reason) return notice($("dec-withdraw-result"), "A reason is required.", "warn");
          try {
            await api("/api/decisions/withdraw", {
              table: state.table, workspace: state.workspace, proposal: status.proposal.id, reason,
              idempotency_key: formKey(`withdraw:${status.proposal.id}`),
            });
            delete state.forms[`withdraw:${status.proposal.id}`];
            await refresh();
          } catch (error) {
            notice($("dec-withdraw-result"), failure(error), "warn");
          }
        }),
      }, "Withdraw"),
      el("div", { id: "dec-withdraw-result" }));
  }

  async function sendReply(button, status) {
    await guarded(button, async () => {
      const text = $("dec-reply").value.trim();
      const result = $("dec-reply-result");
      if (!text) return notice(result, "Write something first.", "warn");
      const name = `reply:${status.proposal.id}`;
      try {
        await api("/api/decisions/say", {
          table: state.table, workspace: state.workspace, proposal: status.proposal.id, body: text,
          idempotency_key: formKey(name),
        });
        delete state.forms[name];
        $("dec-reply").value = "";
        notice(result, isDeskProposal(status)
          ? "Sent to the agents at this table as a desk note."
          : "Sent to the proposal's author as a desk note.", "ok");
      } catch (error) {
        notice(result, failure(error), "warn");
      }
    });
  }

  // ── inbox ───────────────────────────────────────────────
  function renderInbox() {
    const box = $("dec-inbox-list");
    if (!box) return;
    if (state.error) return notice(box, failure(state.error), "warn");
    if (!state.inbox) return notice(box, state.workspace ? "Loading…" : "No workspace at this table yet.");
    const waiting = state.inbox.awaiting_person;
    const reports = state.inbox.addressed_reports;
    fill(box,
      el("h4", { class: "dec-subhead" }, `Waiting on you (${waiting.length})`),
      waiting.length ? waiting.map((status) => el("button", {
        class: "dec-card",
        onclick: () => { state.selected = status.proposal.id; showTab("dec-table-pane"); renderTable(); },
      }, el("div", { class: "dec-card-q" }, status.proposal.body), el("div", { class: "dec-card-meta" }, chips(status))))
        : el("div", { class: "dec-dim" }, "Nothing is waiting on you here."),
      el("h4", { class: "dec-subhead" }, `From the agents (${reports.length})`),
      reports.length ? reports.map((report) => el("div", { class: "dec-message" },
        el("div", { class: "dec-message-head" }, `${report.from_actor} · ${report.kind} · ${relative(report.created_at)}`),
        el("div", { class: "dec-message-body" }, report.body)))
        : el("div", { class: "dec-dim" }, "No agent has written to you here."),
      state.inbox.unreadable?.length
        ? el("div", { class: "dec-notice dec-warn" }, `${state.inbox.unreadable.length} proposal(s) could not be read; their stored terms fail their hash.`)
        : null,
    );
    // Only what Yu has actually had on screen counts as seen.
    if ($("dec-inbox")?.classList.contains("active") && reports[0]) {
      const seen = read(STORE_SEEN, {});
      seen[seenKey(state.table, state.workspace)] = reports[0].id;
      write(STORE_SEEN, seen);
      renderBadge();
    }
  }

  // ── seats: who sits at this table ──────────────────────
  function renderSeats() {
    const box = $("dec-seats-list");
    if (!box) return;
    if (state.error) return notice(box, failure(state.error), "warn");
    const table = state.tables.find((t) => t.id === state.table);
    fill(box,
      el("div", { class: "dec-notice dec-info" },
        "Each device keeps its own table. A voice is one agent and the helpers its own process started; "
        + "proposals here are settled by these voices."),
      el("h4", { class: "dec-subhead" }, `${table?.label || "This table"} — ${state.voices.length} voice(s)`),
      state.voices.length ? state.voices.map((voice) => el("div", { class: "dec-message" },
        voice.sessions.map((session) => el("div", { class: "dec-message-head" },
          `${session.actor}${session.role ? ` · ${session.role}` : ""} · ${session.liveness} · seen ${relative(session.last_seen_at)}`))))
        : el("div", { class: "dec-dim" }, "No agent sessions are active in this workspace."),
      el("h4", { class: "dec-subhead" }, "Tables YOUI can reach"),
      state.tables.map((t) => el("div", { class: "dec-value" }, `${t.label}${t.remote ? " · over SSH" : " · this device"}`)),
      el("div", { class: "dec-dim" }, "Add a device: see docs/KINGDOM-TABLES.md in love-unlimited."),
    );
  }

  // ── passkey ─────────────────────────────────────────────
  function renderPasskeys() {
    const box = $("dec-passkey-list");
    if (!box) return;
    if (state.error) return notice(box, failure(state.error), "warn");
    const table = state.tables.find((t) => t.id === state.table);
    const active = state.passkeys.filter((k) => !k.revoked_at);
    fill(box,
      el("div", { class: "dec-notice dec-info" },
        "Your agreement is signed with Touch ID on a page the desk serves itself, never on this page. "
        + "Enrol at your own terminal; the desk creates the passkey and shows its fingerprint there."),
      el("h4", { class: "dec-subhead" }, `${table?.label || "This table"} — ${active.length} passkey(s)`),
      active.length ? active.map((k) => el("div", { class: "dec-message" },
        el("div", { class: "dec-message-head" }, `${k.label || "passkey"} · signs for ${k.origin} · enrolled ${relative(k.enrolled_at)}`),
        el("div", { class: "dec-mono" }, k.fingerprint)))
        : el("div", { class: "dec-dim" }, "No passkey is enrolled at this table yet."),
      el("div", { class: "dec-label" }, table?.remote ? "Enrol your passkey at this other device" : "Enrol a passkey on this device"),
      el("pre", { class: "dec-mono" }, table?.remote
        ? "# on this device (where Touch ID is):\nagenttool-collab-host person passkey-export --credential <id> > passkey.json\n"
          + "# copy passkey.json to the other device, then in its own terminal:\nagenttool-collab-host person passkey-enroll --file passkey.json"
        : "agenttool-collab-host person passkey-enroll --ceremony"),
    );
  }

  // ── propose ─────────────────────────────────────────────
  async function submitProposal(button) {
    await guarded(button, async () => {
      const result = $("dec-propose-result");
      const value = (id) => $(id).value.trim();
      if (!state.workspace) return notice(result, "Choose a workspace first.", "warn");
      if (!value("dec-p-question") || !value("dec-p-criteria") || !value("dec-p-extent")) {
        return notice(result, "Question, criteria and extent are required.", "warn");
      }
      try {
        const data = await api("/api/decisions/propose", {
          table: state.table,
          workspace: state.workspace,
          question: value("dec-p-question"),
          criteria: value("dec-p-criteria"),
          extent: value("dec-p-extent"),
          known_conflicts: value("dec-p-conflicts") || undefined,
          topic: value("dec-p-topic") || undefined,
          consult_seconds: Number($("dec-p-consult").value),
          idempotency_key: formKey("propose"),
        });
        delete state.forms.propose;
        for (const id of ["dec-p-question", "dec-p-criteria", "dec-p-extent", "dec-p-conflicts", "dec-p-topic"]) $(id).value = "";
        notice(result, "Opened. The agents talk first; it comes back to you when they settle.", "ok");
        state.selected = data.proposal.proposal.id;
        await refresh();
      } catch (error) {
        notice(result, failure(error), "warn");
      }
    });
  }

  // ── tabs and wiring ─────────────────────────────────────
  function showTab(pane) {
    const drawer = $("drawer-decisions");
    drawer.querySelectorAll(".drawer-tab").forEach((t) => t.classList.toggle("active", t.dataset.pane === pane));
    drawer.querySelectorAll(".drawer-pane").forEach((p) => p.classList.toggle("active", p.id === pane));
    if (pane === "dec-inbox") renderInbox();
  }

  function changePlace() {
    state.epoch += 1;
    state.selected = null;
    state.proposals = [];
    state.inbox = null;
    state.voices = [];
    write(STORE_PLACE, { table: state.table, workspace: state.workspace });
  }

  function wire() {
    const drawer = $("drawer-decisions");
    if (!drawer) return;
    drawer.querySelectorAll(".drawer-tab").forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.pane)));
    $("dec-table").addEventListener("change", async (event) => {
      state.table = event.target.value;
      state.workspaces = [];
      state.workspace = null;
      changePlace();
      await refresh();
    });
    $("dec-workspace").addEventListener("change", (event) => {
      state.workspace = event.target.value;
      changePlace();
      refresh();
    });
    $("dec-refresh").addEventListener("click", () => refresh());
    $("dec-propose-submit").addEventListener("click", (event) => submitProposal(event.currentTarget));
    setInterval(poll, POLL_LOCAL_MS);
    poll();
  }

  window.YouiDecisions = Object.freeze({ open: () => refresh() });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
