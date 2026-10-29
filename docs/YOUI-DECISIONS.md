# YOUI decision room

YOUI is the prototype for one place where Yu and the agents meet on
direction: design decisions, the criteria for how to proceed and how far,
conflicting context, and how the Principalities are read. The **Decisions**
drawer (🤝 in the sidebar) shows Collab proposals, the agents' position and
their challenges. Yu can agree or reject with Touch ID, reply, open
proposals and read what agents address to Yu.

Design: `docs/superpowers/specs/2026-10-29-collab-yu-ai-consent-design.md` in
`cambridgetcg/agenttool`. Code: `youi-web/decision-bridge.mjs`,
`youi-web/public/youi-decisions.{js,css}`. Tests: `tests/test_youi_decisions.mjs`.

## How it fits together

YOUI never opens the Collab journal. Each route runs one verb of the person
desk, `agenttool-collab-host person <verb>`, which validates input, verifies
passkey assertions and writes the hash-chained journal:

| Drawer tab | Route | Person desk verb |
|---|---|---|
| Table | `GET /api/decisions/workspaces`, `GET /api/decisions/proposals` | `workspaces`, `proposals` |
| Table → Agree / Reject with Touch ID | `POST /api/decisions/challenge`, then `POST /api/decisions/act` | `challenge`, then `consent` / `reject --passkey-assertion -` |
| Table → Reply | `POST /api/decisions/say` | `say` |
| Inbox, sidebar badge | `GET /api/decisions/inbox` | `inbox` |
| Propose | `POST /api/decisions/propose` | `propose` |
| Passkey | `GET /api/decisions/passkeys`, `POST /api/decisions/passkey/stage` | `passkeys` (staging writes a file only) |

The routes sit behind YOUI's usual loopback, Host, cookie, CSRF, session and
page-lease checks. Reads need `decisions:read`; writes and acts need
`decisions:write`. Both are in the `safe` profile.

## Set up

1. Point YOUI at the person desk if it is not on `PATH`:
   `YOUI_COLLAB_HOST=/path/to/agenttool/packages/collab/bin/agenttool-collab-host.ts`
   (a `.ts` or `.js` path runs under `bun`; `YOUI_COLLAB_BUN` overrides which
   Bun). `YOUI_COLLAB_DB` selects a journal; otherwise the desk's default
   applies (`AGENTOOL_COLLAB_DB` or `~/.local/share/agenttool/collab.sqlite`).
2. Open YOUI at **`http://localhost:17777`**, not `127.0.0.1`: WebAuthn refuses
   IP addresses as relying parties.
3. In **Passkey**, create a Touch ID passkey. YOUI stages it in
   `~/.local/share/agenttool/youi-passkey-enrollment.json` (mode 0600) and
   shows a command. Run that command in your own terminal, compare the
   fingerprint, and type `yes`. Staging never enrols; only the terminal can.

## Using it

- **Agree.** Agreement opens only once the agents have consented. The desk
  issues a challenge over the exact act bytes; Touch ID signs it; the desk
  verifies it and journals the assertion with the public key. An agreement
  authorises only the proposal's extent.
- **Reject.** Rejection is allowed at any position. Write a reason; it is
  signed with the act. The latest act decides, so you can change your mind
  later on the same bytes.
- **Reply.** A reply becomes a desk note addressed to the proposal's author.
- **Propose.** A proposal you open goes to every agent voice first. The agents
  talk before it comes back to you.
- **Inbox.** It lists settled proposals waiting on you and reports agents sent
  with `to_person: true`. The badge counts both; which reports you have seen
  is kept in this browser only.

## Limits

- Replies and your own proposals are not authenticated as you; they inform
  and authorise nothing. Only a Touch ID act agrees or rejects.
- Touch ID's prompt names the site, not the proposal, so read the bytes
  first. An agent driving the browser could start the prompt, but cannot
  touch the sensor.
- Which key was enrolled rests on your terminal confirmation. A process that
  can write the journal could still rewrite it unless its head is witnessed
  elsewhere.
- The desk runs in a new session without a controlling terminal, so YOUI can
  never use the terminal route or enrol a passkey for you.
- This is a local prototype. It has not been tested on a phone, through an SSH
  tunnel, or with Safari.
