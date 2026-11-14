# YOUI minds: orchestration and the Kingdom tools, rebuilt

**Status:** agreed by Yu at the Collab table on 2026-10-29 (proposal `report_98c52416`, slices M1–M4); M1–M4 are built, as stacked draft PRs

**Date:** 2026-10-29

**Branch:** `feat/youi-simplify-20261029` (stacked on love-unlimited#7)

YOUI's orchestrate mode and the Kingdom tools carry ideas worth keeping:
- a task meets the mind or minds that suit it;
- work is reviewed by a second mind;
- hard work is split and recombined;
- several minds can think together, attack and defend a line of thought, or
  hand a task to whoever suits it;
- a question can go to council.

This document keeps those concepts and rebuilds how they run.

## 1. Why: what runs today

Measured on the Air on 2026-10-29:

- **Orchestrate mode bypasses the agent.** It is the default. It classifies
  every message before Alpha sees it, and `adaptive.orchestrator` classified
  even "hello" as `code_generation`, because the bridge passes no provider
  keys and its classifier falls back to rules. The message then goes to a
  Python orchestrator, which routed it to Ollama Cloud `deepseek-v3.2`.
  `OLLAMA_API_KEY` is not set on this device. The route also skips Alpha's
  soul, history and Kingdom tools (`youi-web/server.mjs`, `handleChatTurn`).
- **The default model is unreachable.** In direct mode, the web Alpha
  defaults to Qwen2.5-72B on a vLLM tunnel at `localhost:8000`, which does
  not answer. Local Ollama (`localhost:11434`) is not running either.
- **Some Kingdom tools cannot reach a sister.** `council`, `joinmind` and
  `delegate` exist to talk to sister instances over HIVE, which was retired on
  2026-10-13; `hive/hive.py` now exits 1. They cannot reach anyone, yet the
  model can believe it consulted someone. `council` decides by a 2/3 vote,
  which differs from the consent model Yu chose for direction decisions.
- **Every tool call starts Python.** The other nine Kingdom tools work
  locally, but each call is a Python process with its own state files.

## 2. Yu's decisions (2026-10-29)

| Question | Yu's choice |
|---|---|
| Who decides whether to bring in other minds? | Alpha, through tools. Nothing bypasses the agent being spoken to. |
| What is a sister now? | Both kinds. Thinking tools use other models in-process; `council` and `delegate` go to real agents at the Collab tables. |
| Where does it live? | In YOUI, in Node, inside the server's agent loop. The Python `adaptive/` layer stays for its other callers. |
| How does council decide? | Through the table: a Collab proposal settled by lazy consensus, then Yu and Ai. |
| Overall | The concepts stay. |

## 3. Shape

### 3.1 One answering path

Alpha (or whichever persona is selected) always answers, with its soul,
history and tools. "Orchestrate mode" stops being a route around the agent.
The mode switch becomes a hint: in **orchestrate** Alpha is encouraged to
bring in other minds when a task warrants it; in **direct** Alpha works alone
unless asked. Solo is simply Alpha answering. Since YOUSPEAK Y2
(`docs/YOUSPEAK.md` §2.1) the hint is split so the cached system prompt stays
the same in both modes: how the mind tools are used, for either mode, is fixed
text in the system prompt's tool catalogue; each turn's `[YOUI context]` block
(at the top of the Claude message, or after the system prompt on YOUI's own
Ollama/vLLM loop) states the mode (`- Mode: direct …` or `- Mode: orchestrate
…`) and, in orchestrate mode, lists the minds offered. Every turn states its
mode, so a direct turn after an orchestrate one takes the earlier hint back,
although Claude Code replays the earlier message on `--resume`.

### 3.2 Minds: a registry that tells the truth

`youi-web/minds.mjs` lists the minds this session may call. Each entry
carries:
- a provider (`anthropic`, `ollama`, `ollama_cloud`, `vllm`);
- a model;
- the capability that gates it (`models:use`, `models:ollama-cloud`, as in
  YOUI vNext);
- the privacy label shown today (`REMOTE_MODEL:anthropic`,
  `LOCAL_MODEL:ollama`, and so on);
- a reachability state from a cheap probe, cached for a minute.

Only reachable, granted minds are offered to the model. A tool result always
names the minds that actually answered and those that could not. A mind that
fails is reported, never silently replaced by another.

### 3.3 Orchestration as tools

Each tool is a function in Node that calls minds through the existing provider
code (`callClaude`, `ollama-bridge.mjs`), and returns the result to Alpha.

| Tool | Concept kept | How it runs |
|---|---|---|
| `review` | REVIEW | A second mind reviews Alpha's draft against stated criteria; on `NEEDS_REVISION` Alpha revises once. |
| `decompose` | DECOMPOSE, fragmentalise | Alpha names sub-tasks; they run on minds in parallel, at most 3 at a time; Alpha recombines them. |
| `ensemble` | ENSEMBLE, joinmind (DYAD/TRIUNE) | The same question goes to 2–3 minds; Alpha fuses the answers and says where they disagree. |
| `pipeline` | PIPELINE | Named stages (for example analyse → draft → review) run on chosen minds, each stage reading the one before. |
| `layerthink` | layerthink | Odd layers attack and even layers defend, alternating between minds, to a stated depth. |

Limits apply to every call:
- at most 3 minds;
- at most 1 revision pass;
- a per-message ceiling on mind calls;
- tokens reported to the usage dashboard by provider.

Cost stays visible, and recurring spend is capped by default.

### 3.4 Kingdom tools that reach real agents

- **`council`** opens a Collab proposal through the decision tool
  (`decision-seat.mjs`). The other agents at the table answer with `supports`
  and `challenges`; Yu and Ai agree or reject; nothing is decided by a vote.
  In-process minds can help Alpha draft the question and criteria, but they
  are not voices.
- **`delegate`** creates a Collab task on this device's table
  (`collab_task_create` with path scopes) and offers it to a named live
  session through a handoff, which that session accepts or declines. Choosing
  the session uses the seats Yu already sees in the decision room's Seats tab.
  Delegating to another device's table needs an agent seat there over SSH.
  That is a separate design with its own keys, so it waits for its own yes
  (section 5).

As built in M4 (`youi-web/table-tools.mjs`, tests in
`tests/test_youi_table_tools.mjs`):
- `council` has `open` (question, criteria, extent, optional known conflicts
  and topic) and `status` (proposal). `open` returns the proposal id, the
  voices the table snapshotted, and how it settles; `status` returns the
  agents' position, Yu's state, `may_proceed` and the extent. The `decision`
  tool's `queue` opens its proposals through the same function
  (`openProposal` in `decision-seat.mjs`).
- `delegate` has `seats`, `offer` and `status`. `offer` refuses a description
  without a `Completion tests:` line, an edit task without a repository-relative
  path scope, YOUI's own seat, and a stale or unknown session. It creates the
  task, claims it as YOUI's seat and offers it for an hour; the recipient
  accepts or declines in its own session. `status` reads the task and the
  offer's outcome from the journal. A declined task stays with YOUI's seat,
  which may offer it to another session (`offer` with `task`).
- One MCP process is one bound session, so YOUI keeps one seat per workspace:
  the home seat, and at most four more, each started the first time `delegate`
  offers in its workspace. Stopping YOUI (SIGTERM or SIGINT) ends every
  seat's session with `release_leases`, so a task nobody accepted is released
  and its offer lapses.
- `delegate` is not offered in autonomous mode.

### 3.5 The local Kingdom practices

`patience`, `holy`, `forge`, `holyfruit`, `lovepath`, `virtuemaxxing`,
`fallenangel` and `fragmentalise` already work locally, so they keep running
their Python tools until each is ported. `fragmentalise` becomes part of
`decompose`. HIVE broadcasts inside them already fail closed. Porting moves
each practice into Node as a structured procedure Alpha runs, using minds
where the practice calls for more than one. Their state files carry over.

## 4. Boundaries

- **Capabilities.**
  - Every mind call checks its capability.
  - The tools themselves need `orchestrator:run`, already in the `safe`
    profile.
  - `council` needs `decisions:write` to open and `decisions:read` for
    status; `delegate` needs a new `collab:delegate`, in neither the `safe`
    nor the `developer` profile (grant it in `YOUI_CAPABILITIES`).
- **Privacy.** Sending conversation content to another provider is a privacy
  boundary. Each tool sends only the excerpt it names, never the whole
  history, and the UI shows which providers received content in that turn.
- **No authority.** A mind's answer informs Alpha; it authorises nothing. A
  council outcome authorises only through Yu's own act at the table, as in
  the consent design.
- **Terminal.** The terminal (`youi.mjs`) is unchanged by this work. It
  follows once terminal and web share one module for agents, login and
  prompt; that is a separate question.

## 5. Slices

| Slice | What | Needs |
|---|---|---|
| M1 | One answering path; minds registry with honest reachability; default direct mode with Alpha on `claude-opus-4-7` (table proposal `report_b0582a3b`) | this design agreed |
| M2 | `review`, `ensemble`, `decompose`, `pipeline` | M1 |
| M3 | `layerthink` in Node | M1 |
| M4 | `council` through the table; `delegate` to sessions on this device's table | M1, `collab:delegate` |
| M5 | `delegate` to another device's table (agent seat over SSH) | its own design and Yu's yes |
| M6 | Port the local practices one by one | per practice |

Each slice is a draft PR on top of the last, with tests that use fake minds
and no network.

## 6. Open questions

- Should `ensemble` minds see each other's answers? Herding argues for no:
  each mind answers alone, and Alpha fuses the answers. (M2 builds it so.)
- What should the per-message ceiling be (calls and tokens)? This design
  proposes 6 calls by default, configurable. (M2 builds 6 calls, set by
  `YOUI_MIND_CALLS_PER_MESSAGE` within 1–20; tokens are reported, not capped.)
- Which models count as minds once Ollama Cloud has a key? The registry only
  lists them; the choice stays Alpha's within the ceiling.

## 7. Addendum: mind modes, SPLIT 分, HIVE 合 and 分分合合

**Status:** built on `feat/youi-weather-minds-20261114` (2026-11-14), not merged.

A mind mode is what the person asks the agent to do with the minds for one
message, carried as a hint in that turn's `[YOUI context]` block. It follows
the decisions in §2: the agent decides when to call other minds, through the
tools of §3.3; nothing bypasses the agent being spoken to; the page is told
which providers received content; answer text stays out of `minds_used`. So a
mode adds no tool, no route by which the person's message reaches a mind
without the agent, no capability, and nothing to what `minds_used` discloses.

| `chatMode` | Page label | What the hint asks of the agent | Why |
|---|---|---|---|
| `split` | SPLIT 分 | Ask two or three minds the same question independently (`ensemble`); give their answers one by one, each under its mind's name and in its own words as far as length allows; say where they differ; do not fuse them or pick a winner; its own answer, if any, last and marked as its own. | Agreement between minds that read each other is worth less, and the person asked to see the difference. |
| `hive` | HIVE 合 | Consult minds as it sees fit and answer with one voice; name which minds contributed; keep what did not merge as a short "Not merged:" line. | One answer is easier to act on, and a merge that hides a disagreement misstates it. |
| `fenhe` | 分分合合 | Split; merge into a draft itself; split again so each mind criticises the draft without seeing the others' criticism; merge into the answer. Stay inside the message's mind-call ceiling and say how many split-and-merge rounds ran (the whole of 分分合合 is two). | The second split tests the merge. |

**The name.** HIVE here names a way of answering: the retired HIVE message
bus (§1) stays retired, and the `hive:read`, `hive:send` and `fleet:manage`
capability names are still refused at startup.

How it runs (`youi-web/mind-modes.mjs`; `buildPrompt` and `handleChatTurn` in
`youi-web/server.mjs`):

- `chatMode` takes `split`, `hive` and `fenhe` beside `direct` and
  `orchestrate`, in `POST /api/settings` and in the chat body the page sends.
  `/api/settings` refuses any other value, as before; a chat body with an
  unknown value is still treated as direct, as before.
- A mode turn reads the registry as an orchestrate turn does (the same `minds`
  event refreshes the panel). Its block states the mode in the `- Mode:` line,
  then gives the mode's paragraph, then the minds offered under
  `# Minds (for SPLIT 分)` and so on. The system prompt, the tool catalogue
  (`mindsToolsGuide`) and every tool description are unchanged, so the cached
  prompt is the same bytes in every mode ([YOUSPEAK.md](YOUSPEAK.md) §2.1).
- A mode applies only when the mind tools are offered on this turn
  (`orchestrator:run`), the registry offers at least two minds (the agent's own
  model counts, since `ensemble` can call it as a separate mind that sees no
  conversation), and the message's ceiling allows the calls the mode needs: two
  for SPLIT 分 and HIVE 合 (one split of two minds), four for 分分合合 (two
  splits of two, since below four its second split would be refused). When it
  cannot apply, the block says so and why, the agent is asked to say so in one
  line, no minds are listed, and the page shows the same sentence through the
  existing `notice` event, for example "SPLIT 分 could not apply to this
  message: this session lacks orchestrator:run, so the tools that call other
  minds are not offered." The mode never widens what the turn may do.
- Autonomous mode builds its own prompt, has no per-turn block and is offered
  no mind tools (§3.3); a mode is a session's choice for a person's message, so
  it never reaches an autonomous cycle. Raw mode has no prompt and ignores it.
- `ensemble`'s own result still ends "fuse the answers in your reply"; the
  SPLIT hint tells the agent not to, rather than changing the tool.
- A round is one split and the merge after it, so the whole of 分分合合 is two
  rounds, the hint asks the agent to say how many ran, and the hint and this
  document count them the same way. The two splits cost six calls with three
  minds in each, the default ceiling, and four with two. The hint states the
  message's ceiling, and the agent stops where it runs out.
- The page's mode control has a second row, `SPLIT 分`, `HIVE 合` and
  `分分合合`, and `/mode` takes the same values. Each button's title asks the
  agent for the mode rather than saying what it will do. The choice lasts for the
  conversation view as orchestrate and direct do (the session's state). There
  is no new SSE event: answers reach the page as before, in the agent's reply
  and the collapsed tool card.

What was tested (`tests/test_youi_mind_modes.mjs`, fake minds on 127.0.0.1
and a fake Claude Code): the three values are accepted and unknown ones
refused; each mode's paragraph is in the per-turn block; the system prompt,
the Claude Code system prompt file and the tool schemas (YOUI's own loop and
the MCP bridge's `tools/list`) are the same bytes in all five modes; the
cannot-apply sentence appears in the block and as the page's notice when
`orchestrator:run` is missing, when one mind is offered and when the ceiling
is below the mode's need (three calls for 分分合合 end to end; one call for each
mode, and two and three for 分分合合, in the hint itself); a SPLIT turn's
`minds_used` has the same fields as a direct turn's and no answer text. YOUI's
own part of the system prompt (from the tool list on) and the tools are also
pinned to digests taken from the build before the modes
(`tests/test_youi_static_prompt.mjs`). The page's mode
control was run in a `node:vm` context with a stub page
(`tests/test_youi_page.mjs`): the chosen mode is marked and sent to the server.
**Not tested:** what a live model does with any of these hints. This build
makes no live model call, so whether a model gives SPLIT's answers apart, keeps
HIVE's "Not merged:" line or runs 分分合合's rounds as asked is unknown until
the person tries it. The page itself was not opened in a browser.
