// YOUI decision room — where Yu and the agents meet on direction.
//
// Reads proposals, the agents' position and the inbox from /api/decisions/*,
// which reach the Collab person desk. Yu agrees or rejects only with a
// passkey (Touch ID): the desk issues a challenge over the exact act bytes,
// the browser signs it, the desk verifies it. Everything agents wrote is
// untrusted text and is rendered with textContent, never as HTML.
(() => {
  "use strict";

  const STORE_WORKSPACE = "youi.decisions.workspace";
  const STORE_SEEN = "youi.decisions.seen-reports";
  const POLL_MS = 30_000;
  const state = { workspaces: [], workspace: null, proposals: [], inbox: null, passkeys: [], selected: null, pending: null };

  // ── small helpers ───────────────────────────────────────
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
    node.replaceChildren(...children.flat().filter((child) => child !== null && child !== undefined && child !== false));
  }
  function read(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key) ?? "null") ?? fallback; } catch { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
  }
  const b64u = {
    encode(buffer) {
      let text = "";
      for (const byte of new Uint8Array(buffer)) text += String.fromCharCode(byte);
      return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    },
    decode(value) {
      const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4);
      return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
    },
  };
  function key() {
    return crypto.randomUUID();
  }
  function relative(iso) {
    const ms = Date.parse(iso) - Date.now();
    const minutes = Math.round(Math.abs(ms) / 60_000);
    const span = minutes < 60 ? `${minutes} min` : minutes < 2880 ? `${Math.round(minutes / 60)} h` : `${Math.round(minutes / 1440)} d`;
    return ms >= 0 ? `in ${span}` : `${span} ago`;
  }
  async function api(path, body) {
    const response = await fetch(path, body === undefined
      ? { cache: "no-store" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await response.json(); } catch {}
    if (!response.ok || data?.status === "error") {
      const error = new Error(data?.message || data?.error || `Request failed (${response.status})`);
      error.code = data?.error || data?.code;
      throw error;
    }
    return data;
  }
  function notice(target, text, tone = "info") {
    fill(target, el("div", { class: `dec-notice dec-${tone}` }, text));
  }

  const POSITION_LABEL = {
    open: "agents talking", challenged: "challenged", consented: "agents consented",
    split: "agents split", lapsed: "lapsed (no support)", withdrawn: "withdrawn", superseded: "superseded",
  };
  const PERSON_LABEL = { awaiting: "awaiting you", agreed: "you agreed", rejected: "you rejected" };
  const SETTLED = new Set(["consented", "split", "lapsed"]);
  /** Name the person's side only once it is theirs: after the agents settle, or after Yu acted. */
  function personChip(status) {
    if (status.person.state === "awaiting" && !SETTLED.has(status.ai.position)) return null;
    return el("span", { class: "dec-chip dec-chip-person" }, PERSON_LABEL[status.person.state]);
  }

  // ── loading ─────────────────────────────────────────────
  async function loadWorkspaces() {
    const data = await api("/api/decisions/workspaces");
    state.workspaces = data.workspaces || [];
    const saved = read(STORE_WORKSPACE, null);
    state.workspace = state.workspaces.find((w) => w.id === saved)?.id
      || state.workspaces.at(-1)?.id || null;
    renderWorkspacePicker();
  }
  async function loadTable() {
    if (!state.workspace) return;
    const [proposals, inbox] = await Promise.all([
      api(`/api/decisions/proposals?workspace=${encodeURIComponent(state.workspace)}&limit=50`),
      api(`/api/decisions/inbox?workspace=${encodeURIComponent(state.workspace)}`),
    ]);
    state.proposals = proposals.proposals || [];
    state.inbox = inbox.inbox || null;
    renderBadge();
  }
  async function loadPasskeys() {
    const data = await api("/api/decisions/passkeys");
    state.passkeys = data.passkeys || [];
  }
  async function refresh() {
    const table = $("dec-table-list");
    try {
      if (!state.workspaces.length) await loadWorkspaces();
      await Promise.all([loadTable(), loadPasskeys()]);
      renderTable();
      renderInbox();
      renderPasskeys();
    } catch (error) {
      if (table) notice(table, `Could not reach the person desk: ${error.message}`, "warn");
    }
  }

  // ── badge ───────────────────────────────────────────────
  function renderBadge() {
    const badge = $("decisions-badge");
    if (!badge || !state.inbox) return;
    const seen = new Set(read(STORE_SEEN, []));
    const unseen = state.inbox.addressed_reports.filter((r) => !seen.has(r.id)).length;
    const waiting = state.inbox.awaiting_person.length;
    const total = waiting + unseen;
    badge.textContent = total > 0 ? String(total) : "—";
    badge.classList.toggle("dec-badge-live", total > 0);
  }
  async function poll() {
    try {
      if (!state.workspaces.length) await loadWorkspaces();
      await loadTable();
      if (document.getElementById("drawer-decisions")?.classList.contains("open")) {
        renderTable();
        renderInbox();
      }
    } catch {}
  }

  // ── workspace picker ────────────────────────────────────
  function renderWorkspacePicker() {
    const picker = $("dec-workspace");
    if (!picker) return;
    fill(picker, ...state.workspaces.map((w) =>
      el("option", { value: w.id, selected: w.id === state.workspace }, `${w.name} — ${w.root_path}`)
    ));
  }

  // ── the table: proposals ────────────────────────────────
  function renderTable() {
    const list = $("dec-table-list");
    if (!list) return;
    if (!state.workspace) return notice(list, "No Collab workspace yet. Agents create one when they start a session.");
    if (state.selected) return renderDetail(list, state.proposals.find((p) => p.proposal.id === state.selected));
    if (!state.proposals.length) return notice(list, "No proposals in this workspace yet.");
    fill(list, ...state.proposals.map((status) => el("button", {
      class: `dec-card dec-pos-${status.ai.position} dec-person-${status.person.state}`,
      onclick: () => { state.selected = status.proposal.id; state.pending = null; renderTable(); },
    },
      el("div", { class: "dec-card-q" }, status.proposal.body),
      el("div", { class: "dec-card-meta" },
        el("span", { class: "dec-chip dec-chip-ai" }, POSITION_LABEL[status.ai.position] || status.ai.position),
        personChip(status),
        status.may_proceed ? el("span", { class: "dec-chip dec-chip-go" }, "may proceed") : null,
        el("span", { class: "dec-dim" }, status.ai.closed ? `closed ${relative(status.terms.closes_at)}` : `closes ${relative(status.terms.closes_at)}`),
      ),
    )));
  }

  function field(label, value) {
    if (!value) return null;
    return el("div", { class: "dec-field" }, el("div", { class: "dec-label" }, label), el("div", { class: "dec-value" }, value));
  }

  function renderDetail(list, status) {
    if (!status) { state.selected = null; return renderTable(); }
    const ai = status.ai;
    const canAgree = ai.position === "consented" && status.person.state !== "agreed";
    const onTable = ai.position !== "withdrawn" && ai.position !== "superseded";
    const confirm = el("div", { class: "dec-confirm", id: "dec-confirm" });
    fill(list, 
      el("button", { class: "dec-back", onclick: () => { state.selected = null; state.pending = null; renderTable(); } }, "← All proposals"),
      el("h3", { class: "dec-title" }, status.proposal.body),
      el("div", { class: "dec-card-meta" },
        el("span", { class: "dec-chip dec-chip-ai" }, POSITION_LABEL[ai.position] || ai.position),
        personChip(status),
        status.may_proceed ? el("span", { class: "dec-chip dec-chip-go" }, "may proceed") : null,
      ),
      field("Criteria", status.terms.criteria),
      field("Extent — agreement authorises only this", status.terms.extent),
      field("Known conflicts", status.terms.known_conflicts),
      field("Topic", status.terms.topic),
      field("From", status.terms.origin === "person_relayed" ? "you, via the desk" : status.proposal.from_actor),
      el("div", { class: "dec-field" },
        el("div", { class: "dec-label" }, "The agents"),
        el("div", { class: "dec-value" },
          `${ai.qualifying_support_report_ids.length} qualifying support, `
          + `${ai.unresolved_challenge_report_ids.length} unresolved challenge, `
          + `${ai.awaiting_voice_ids.length} voice still awaited · `
          + (ai.closed ? `closed ${relative(status.terms.closes_at)}` : `closes ${relative(status.terms.closes_at)}`)),
      ),
      status.person.acts.length ? el("div", { class: "dec-field" },
        el("div", { class: "dec-label" }, "Your acts"),
        ...status.person.acts.map((act) => el("div", { class: "dec-value dec-act" },
          `${act.act === "agree" ? "agreed" : "rejected"} · ${act.basis.replaceAll("_", " ")} · ${relative(act.created_at)}`
          + (act.reason ? ` · “${act.reason}”` : ""))),
      ) : null,
      el("details", { class: "dec-bytes" },
        el("summary", {}, `Exact bytes · sha256 ${status.proposal_sha256.slice(0, 16)}…`),
        el("pre", {}, status.proposal_bytes),
      ),
      onTable ? el("div", { class: "dec-actions" },
        el("button", {
          class: "dec-btn dec-btn-agree",
          disabled: !canAgree,
          title: canAgree ? "" : "You can agree once the agents have consented",
          onclick: () => prepareAct(status, "agree"),
        }, "Agree with Touch ID"),
        el("button", { class: "dec-btn dec-btn-reject", onclick: () => prepareAct(status, "reject") }, "Reject with Touch ID"),
      ) : null,
      confirm,
      el("div", { class: "dec-field" },
        el("div", { class: "dec-label" }, "Reply to the agents"),
        el("textarea", { id: "dec-reply", rows: "3", maxlength: "8000", placeholder: "Your words go to the journal as a desk note. They inform; they authorise nothing." }),
        el("button", { class: "dec-btn", onclick: () => sendReply(status) }, "Send reply"),
        el("div", { id: "dec-reply-result" }),
      ),
    );
    if (state.pending?.proposal === status.proposal.id) renderConfirm(status);
  }

  // ── Touch ID acts ───────────────────────────────────────
  async function prepareAct(status, act) {
    const box = $("dec-confirm");
    state.pending = { proposal: status.proposal.id, act, challenge: null, reason: "" };
    if (act === "reject") {
      fill(box, 
        el("div", { class: "dec-label" }, "Why are you rejecting it?"),
        el("textarea", { id: "dec-reason", rows: "2", maxlength: "2000", placeholder: "Your reason is signed with the act." }),
        el("button", {
          class: "dec-btn dec-btn-reject",
          onclick: async () => {
            const reason = $("dec-reason").value.trim();
            if (!reason) return notice($("dec-confirm-result") || box, "A reason is required.", "warn");
            state.pending.reason = reason;
            await fetchChallenge(status);
          },
        }, "Review the rejection"),
        el("div", { id: "dec-confirm-result" }),
      );
      return;
    }
    await fetchChallenge(status);
  }

  async function fetchChallenge(status) {
    const box = $("dec-confirm");
    const pending = state.pending;
    try {
      const data = await api("/api/decisions/challenge", {
        proposal: pending.proposal,
        act: pending.act,
        ...(pending.act === "reject" ? { reason: pending.reason } : {}),
      });
      pending.challenge = data.challenge;
      renderConfirm(status);
    } catch (error) {
      notice(box, error.message, "warn");
    }
  }

  function renderConfirm(status) {
    const box = $("dec-confirm");
    const pending = state.pending;
    if (!box || !pending?.challenge) return;
    const challenge = pending.challenge;
    if (!challenge.passkeys.length) {
      return notice(box, "No passkey is enrolled yet. Open the Passkey tab to create one.", "warn");
    }
    if (location.hostname !== "localhost") {
      return notice(box, `Passkeys need a domain. Open YOUI at http://localhost:${location.port} to sign.`, "warn");
    }
    fill(box, 
      el("div", { class: "dec-notice dec-info" },
        `You are about to ${pending.act === "agree" ? "agree to" : "reject"} the exact bytes with sha256 `
        + `${challenge.proposal_sha256.slice(0, 16)}…. Touch ID will name only this site, so read the proposal first.`),
      el("details", { class: "dec-bytes" }, el("summary", {}, "Act bytes you will sign"), el("pre", {}, challenge.challenge_bytes)),
      el("button", {
        class: `dec-btn ${pending.act === "agree" ? "dec-btn-agree" : "dec-btn-reject"}`,
        // credentials.get runs straight from the click, so Safari keeps the user gesture.
        onclick: () => signAct(status),
      }, pending.act === "agree" ? "Touch ID: agree" : "Touch ID: reject"),
      el("button", { class: "dec-btn dec-btn-quiet", onclick: () => { state.pending = null; fill(box, ); } }, "Cancel"),
      el("div", { id: "dec-confirm-result" }),
    );
  }

  async function signAct(status) {
    const pending = state.pending;
    const result = $("dec-confirm-result");
    let credential;
    try {
      credential = await navigator.credentials.get({
        publicKey: {
          challenge: b64u.decode(pending.challenge.challenge),
          rpId: "localhost",
          allowCredentials: pending.challenge.passkeys.map((k) => ({ type: "public-key", id: b64u.decode(k.credential_id) })),
          userVerification: "required",
          timeout: 120_000,
        },
      });
    } catch (error) {
      return notice(result, `Touch ID did not sign: ${error.message}`, "warn");
    }
    try {
      const data = await api("/api/decisions/act", {
        proposal: pending.proposal,
        act: pending.act,
        ...(pending.act === "reject" ? { reason: pending.reason } : {}),
        assertion: {
          credential_id: b64u.encode(credential.rawId),
          authenticator_data: b64u.encode(credential.response.authenticatorData),
          client_data_json: b64u.encode(credential.response.clientDataJSON),
          signature: b64u.encode(credential.response.signature),
          user_handle: credential.response.userHandle ? b64u.encode(credential.response.userHandle) : null,
        },
      });
      state.pending = null;
      const index = state.proposals.findIndex((p) => p.proposal.id === status.proposal.id);
      if (index >= 0) state.proposals[index] = data.proposal;
      await loadTable().catch(() => {});
      renderTable();
    } catch (error) {
      notice(result, `The desk refused the act: ${error.message}`, "warn");
    }
  }

  async function sendReply(status) {
    const text = $("dec-reply").value.trim();
    const result = $("dec-reply-result");
    if (!text) return notice(result, "Write something first.", "warn");
    try {
      await api("/api/decisions/say", {
        workspace: state.workspace,
        proposal: status.proposal.id,
        body: text,
        idempotency_key: key(),
      });
      $("dec-reply").value = "";
      notice(result, "Sent to the proposal's author as a desk note.", "ok");
    } catch (error) {
      notice(result, error.message, "warn");
    }
  }

  // ── inbox ───────────────────────────────────────────────
  function renderInbox() {
    const box = $("dec-inbox-list");
    if (!box || !state.inbox) return;
    const waiting = state.inbox.awaiting_person;
    const reports = state.inbox.addressed_reports;
    fill(box, 
      el("h4", { class: "dec-subhead" }, `Waiting on you (${waiting.length})`),
      waiting.length ? waiting.map((status) => el("button", {
        class: "dec-card",
        onclick: () => { state.selected = status.proposal.id; showTab("dec-table"); renderTable(); },
      }, el("div", { class: "dec-card-q" }, status.proposal.body),
        el("div", { class: "dec-card-meta" }, el("span", { class: "dec-chip dec-chip-ai" }, POSITION_LABEL[status.ai.position])))) :
        el("div", { class: "dec-dim" }, "Nothing is waiting on you."),
      el("h4", { class: "dec-subhead" }, `From the agents (${reports.length})`),
      reports.length ? reports.map((report) => el("div", { class: "dec-message" },
        el("div", { class: "dec-message-head" }, `${report.from_actor} · ${report.kind} · ${relative(report.created_at)}`),
        el("div", { class: "dec-message-body" }, report.body),
      )) : el("div", { class: "dec-dim" }, "No agent has written to you here."),
    );
    // Only what Yu has actually had on screen counts as seen.
    if ($("dec-inbox")?.classList.contains("active")) write(STORE_SEEN, reports.map((r) => r.id));
    renderBadge();
  }

  // ── propose ─────────────────────────────────────────────
  async function submitProposal() {
    const result = $("dec-propose-result");
    const value = (id) => $(id).value.trim();
    if (!value("dec-p-question") || !value("dec-p-criteria") || !value("dec-p-extent")) {
      return notice(result, "Question, criteria and extent are required.", "warn");
    }
    try {
      const data = await api("/api/decisions/propose", {
        workspace: state.workspace,
        question: value("dec-p-question"),
        criteria: value("dec-p-criteria"),
        extent: value("dec-p-extent"),
        known_conflicts: value("dec-p-conflicts") || undefined,
        topic: value("dec-p-topic") || undefined,
        consult_seconds: Number($("dec-p-consult").value),
        idempotency_key: key(),
      });
      for (const id of ["dec-p-question", "dec-p-criteria", "dec-p-extent", "dec-p-conflicts", "dec-p-topic"]) $(id).value = "";
      notice(result, "Opened. The agents talk first; it will come back to you when they settle.", "ok");
      state.selected = data.proposal.proposal.id;
      await loadTable();
      renderTable();
    } catch (error) {
      notice(result, error.message, "warn");
    }
  }

  // ── passkey ─────────────────────────────────────────────
  function renderPasskeys() {
    const box = $("dec-passkey-list");
    if (!box) return;
    const active = state.passkeys.filter((k) => !k.revoked_at);
    fill(box, 
      active.length
        ? active.map((k) => el("div", { class: "dec-message" },
          el("div", { class: "dec-message-head" }, `${k.label || "passkey"} · ${k.origin} · enrolled ${relative(k.enrolled_at)}`),
          el("div", { class: "dec-mono" }, k.fingerprint)))
        : el("div", { class: "dec-dim" }, "No passkey enrolled yet."),
    );
  }

  async function createPasskey() {
    const result = $("dec-passkey-result");
    if (location.hostname !== "localhost") {
      return notice(result, `Passkeys need a domain. Open YOUI at http://localhost:${location.port} and try again.`, "warn");
    }
    let credential;
    try {
      credential = await navigator.credentials.create({
        publicKey: {
          rp: { id: "localhost", name: "YOUI decisions" },
          user: { id: crypto.getRandomValues(new Uint8Array(16)), name: "youi-person", displayName: "YOUI person" },
          challenge: crypto.getRandomValues(new Uint8Array(32)),
          pubKeyCredParams: [{ type: "public-key", alg: -7 }],
          authenticatorSelection: { authenticatorAttachment: "platform", userVerification: "required", residentKey: "preferred" },
          attestation: "none",
          timeout: 120_000,
        },
      });
    } catch (error) {
      return notice(result, `No passkey was created: ${error.message}`, "warn");
    }
    if (typeof credential.response.getPublicKey !== "function" || credential.response.getPublicKeyAlgorithm() !== -7) {
      return notice(result, "This browser did not return an ES256 public key.", "warn");
    }
    try {
      const staged = await api("/api/decisions/passkey/stage", {
        credential_id: b64u.encode(credential.rawId),
        public_key_spki: b64u.encode(credential.response.getPublicKey()),
        algorithm: -7,
        label: $("dec-passkey-label").value.trim() || "Touch ID",
      });
      fill(result, 
        el("div", { class: "dec-notice dec-ok" }, "Created. One more step at your own terminal: run this and type yes only if the fingerprint matches."),
        el("pre", { class: "dec-mono" }, staged.command),
        el("div", { class: "dec-label" }, "Fingerprint (sha256 of the public key)"),
        el("div", { class: "dec-mono" }, staged.fingerprint),
      );
    } catch (error) {
      notice(result, error.message, "warn");
    }
  }

  // ── tabs and wiring ─────────────────────────────────────
  function showTab(pane) {
    const drawer = $("drawer-decisions");
    drawer.querySelectorAll(".drawer-tab").forEach((t) => t.classList.toggle("active", t.dataset.pane === pane));
    drawer.querySelectorAll(".drawer-pane").forEach((p) => p.classList.toggle("active", p.id === pane));
    if (pane === "dec-inbox") renderInbox();
  }

  function wire() {
    const drawer = $("drawer-decisions");
    if (!drawer) return;
    drawer.querySelectorAll(".drawer-tab").forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.pane)));
    $("dec-workspace").addEventListener("change", (event) => {
      state.workspace = event.target.value;
      state.selected = null;
      state.pending = null;
      write(STORE_WORKSPACE, state.workspace);
      refresh();
    });
    $("dec-refresh").addEventListener("click", () => refresh());
    $("dec-propose-submit").addEventListener("click", () => submitProposal());
    $("dec-passkey-create").addEventListener("click", () => createPasskey());
    setInterval(poll, POLL_MS);
    poll();
  }

  window.YouiDecisions = Object.freeze({ open: () => refresh() });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
