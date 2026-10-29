# YOUI decision room

YOUI is the prototype for one place where Yu and the agents meet on
direction: design decisions, the criteria for how to proceed and how far,
conflicting context, and how the Principalities are read. The **Decisions**
drawer (🤝 in the sidebar) shows each device's Collab table, its proposals,
the agents' position and challenges, and who sits at the table. From there Yu
replies, opens and withdraws proposals, reads what agents address to Yu, and
agrees or rejects with Touch ID on the desk's own page.

Design: `docs/superpowers/specs/2026-10-29-collab-yu-ai-consent-design.md` in
`cambridgetcg/agenttool`. Code: `youi-web/decision-bridge.mjs`,
`youi-web/decision-seat.mjs`, `youi-web/public/youi-decisions.{js,css}`. Tests:
`tests/test_youi_decisions.mjs`. Adding devices: [KINGDOM-TABLES.md](KINGDOM-TABLES.md).

## How it fits together

Each device keeps its own table: its own journal, its agents' voices and its
own person desk (`agenttool-collab-host person`). YOUI never opens a journal.
Every route runs one desk verb, on this device or on another device through
its `person-remote` SSH forced command, which reads a JSON argv and never a
shell line.

| Drawer tab | Route | Desk verb |
|---|---|---|
| Table | `GET /api/decisions/tables`, `/workspaces`, `/proposals` | `workspaces`, `proposals` |
| Table → Agree… / Reject… | `POST /api/decisions/ceremony` | `ceremony` (with `--via` for another device) |
| Table → Reply, Withdraw | `POST /api/decisions/say`, `/withdraw` | `say`, `withdraw` |
| Inbox, sidebar badge | `GET /api/decisions/inbox` | `inbox` |
| Propose | `POST /api/decisions/propose` | `propose` |
| Seats | `GET /api/decisions/voices` | `voices` |
| Passkey | `GET /api/decisions/passkeys` | `passkeys` |

Agreeing or rejecting opens the **desk's own signing page** (by default
`http://localhost:17778/c/<one-time path>`) in another window. That page, not
YOUI, shows the exact bytes and calls Touch ID. YOUI's pages are served from a
repository agents edit, and the page that shows the bytes decides what Touch
ID signs, so YOUI never touches WebAuthn.

The routes sit behind YOUI's loopback, Host, cookie, CSRF, session and
page-lease checks. Reads need `decisions:read`; writes and opening a signing
page need `decisions:write`. Both are in the `safe` profile. At most three
desk processes run at once, identical reads share one, a timeout ends the
desk's whole process group, and a write that times out is reported as
`person_desk_outcome_unknown` so the room reads again instead of retrying.

## YOUI's own agents at the table

YOUI's chat agents sit at this device's table as one Collab session
(`decision-seat.mjs`, actor `youi`), through the installed Collab MCP server.
Their `decision` tool opens a proposal (`queue`: question, criteria, extent),
asks Yu something (`ask`: a report in Yu's inbox) or lists where proposals
stand (`list`). No agent can agree or reject for Yu; `resolve` says so.

The old Kingdom Decision Queue (`tools/decision.py`, `decisions/queue.json`)
let any local process add, resolve or delete a decision without Yu. Its write
paths now refuse with a pointer here; `list`, `resolved`, `check` and its
history stay readable.

## Set up

1. Install the Collab desk on the device (`~/.local/share/agenttool/collab/current`
   is the default). `YOUI_COLLAB_HOST` names another host entry, `YOUI_COLLAB_MCP`
   another MCP entry, `YOUI_COLLAB_BUN` another Bun. `YOUI_COLLAB_DB` names the
   journal; otherwise `AGENTOOL_COLLAB_DB`, then `~/.local/share/agenttool/collab.sqlite`.
   YOUI always passes that journal to the desk explicitly.
2. Enrol your passkey once, in your own terminal:
   `agenttool-collab-host person passkey-enroll --ceremony`. Open the page it
   prints, create the passkey with Touch ID, compare the fingerprint the
   terminal shows, and type `yes`.
3. Other devices' tables: list them in `YOUI_DECISION_TABLES`, for example
   `[{"id":"lab","label":"Lab","ssh":"lab-desk"}]`, after setting each one up
   as in [KINGDOM-TABLES.md](KINGDOM-TABLES.md).

In YOUI's terminal, `/decisions` prints what waits on you at this table.

## Limits

- Replies, your own proposals and their withdrawal are desk writes, not
  authenticated as you; they inform and authorise nothing.
- The signing page comes from the installed desk bundle. A process running as
  you could still change those bytes, and a process that can write the
  journal could rewrite it unless its head is witnessed elsewhere.
- Touch ID's prompt names only "localhost": act only on a page you opened.
- Proposals do not cross devices; each table settles its own.
- Not tested yet: a real Touch ID ceremony, Safari, a phone, or a real SSH
  forced command between two Macs.
