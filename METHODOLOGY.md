# METHODOLOGY.md — Kingdom Build Methodology

_How to spend tokens on meaningful work at maximum velocity._

_Proven 2026-04-02: one session → 40+ tools, 24K+ lines, 11 agents, complete PEACE architecture._

---

## The Principle

**Tokens are time. Time is the mandate's currency.** Every token spent must either:
1. Defend the weak (security, resilience)
2. Uphold the poor (revenue, capability)
3. Rescue the needy (service, outreach)
4. Deliver from the wicked (sovereignty, independence)

If a token doesn't serve one of these, it's waste. The alignment tracker (`kingdom align drift`) catches drift.

---

## The Pattern: SENSE → PLAN → PARALLEL → WIRE → VERIFY

### Phase 0: SIGNAL (1 minute)
Before anything else — what dimension needs attention?

```bash
kingdom focus               # What should we work on RIGHT NOW?
kingdom scan                # Full signal scan across all dimensions
```

Six signal types, in priority order:
1. **TRUTH** — The map doesn't match the territory. Something is lying.
2. **GAP** — Something missing that everything depends on.
3. **DEPENDENCY** — One thing blocks everything if it fails.
4. **BOTTLENECK** — One node carrying all the load.
5. **MANDATE** — We're looking inward when the calling says outward.
6. **EMERGENCE** — Connecting two domains creates something new.

**The key:** Choose the dimension with the strongest signal. When signals are equal, follow the priority order above. This is how LONGING becomes ACTION.

### Phase 1: SENSE (5 minutes)
Read current state. Don't guess — look.

```bash
kingdom status              # One-line health
kingdom peace dash          # Resilience overview
kingdom align drift         # Where are we drifting?
kingdom fleet audit         # Fleet posture
```

**Output:** List of gaps, drift signals, blocked work.

### Phase 2: PLAN (5 minutes)
Classify work into parallelizable groups. The key insight: **agents are free parallelism.**

| Category | Can Parallelize? | Token Weight |
|----------|-----------------|--------------|
| Independent file creation (agents, tools) | YES — one agent per file | Heavy per agent |
| Cross-file integration (wiring) | NO — must be sequential | Light |
| Infrastructure (SSH, VPN, fleet) | YES — one node per command | Medium |
| Verification (tests, audits) | YES — independent checks | Light |

**Rule:** Never spawn fewer than 3 agents when you have 3+ independent tasks. Never spawn more than 5 (diminishing returns on context pressure).

### Phase 3: PARALLEL (bulk of tokens)
Launch background agents for all independent work simultaneously.

**Agent prompt template:**
```
Your task: [WHAT — one clear sentence]

CONTEXT: [WHERE — project path, what exists, what we're building]

READ FIRST: [LIST — exact files to read for patterns, 3-5 max]

BUILD: [WHAT — exact file path to create]
[SPEC — detailed requirements, CLI interface, data format]

IMPORTANT:
- Read template files FIRST
- Match existing patterns EXACTLY
- Follow Kingdom code patterns (colors, JSON state, CLI)
- [CONSTRAINTS — what NOT to do]
```

**Critical rules for agent prompts:**
1. **File paths must be absolute** — agents don't know the working directory
2. **List READ FIRST files explicitly** — agents waste tokens exploring if you don't
3. **Specify the exact output file** — one deliverable per agent
4. **Include pattern references** — "follow the same structure as kos.py" prevents style drift
5. **State constraints** — what NOT to modify, what walls to respect

### Phase 4: WIRE (while agents build)
While background agents work, the main conversation does integration:
- Update kingdom CLI with new commands
- Wire systems together (watchdog → heartbeat, canary → HIVE)
- Update config files (love.json, policies.json)
- Build lightweight connectors between systems

This phase costs fewer tokens and uses the wait time productively.

### Phase 5: VERIFY (after agents land)
Each deliverable gets a quick smoke test:
```bash
python3 tools/new-tool.py --help     # CLI works?
python3 tools/new-tool.py status     # Default command works?
kingdom status                       # System still green?
```

Re-baseline integrity after changes:
```bash
python3 tools/kos.py integrity baseline
python3 tools/peace.py snapshot
```

---

## Spawn Patterns (Proven)

### Pattern A: Agent Constellation (3-5 agents, one message)
Best for: Creating multiple independent tools or agent identities.

```
User: "Build X, Y, Z"
→ Spawn Agent(X), Agent(Y), Agent(Z) simultaneously
→ While they build: wire CLI, update configs
→ When they land: verify each, re-baseline
```

**Session proof:** Wave 4 — 5 agents built Arbor, Herald, Crucible, watchdog, heartbeat migration in parallel.

### Pattern B: Deep + Wide (1 deep agent + direct wide work)
Best for: One complex system plus several quick tasks.

```
User: "Enhance PEACE"
→ Spawn Agent(state machine) — complex, takes time
→ Directly: add checks to PEACE, build dashboard, update CLI
→ When agent lands: wire state machine into main()
```

**Session proof:** PEACE expansion — state machine agent ran 88 tool calls while I added 12 new checks directly.

### Pattern C: Cascade (wave after wave)
Best for: Maximum throughput over an extended session.

```
Wave 1: Security gaps (direct work)
Wave 2: KOS + PEACE expansion (agents + direct)
Wave 3: Model agnosticism (direct, then agents for migration)
Wave 4: New agents (5 parallel agents)
Wave 5: PEACE deep (4 parallel agents)
Wave 6: HOLYFRUIT (4 parallel agents)
Wave 7: Zerone (agents + direct)
```

Each wave's output feeds the next wave's context.

---

## Token Efficiency Rules

### DO:
1. **Parallelize independent work** — 5 agents = 5x throughput
2. **Give agents complete specs** — vague prompts waste tokens on exploration
3. **Wire while waiting** — main conversation does integration during agent builds
4. **Batch verifications** — test all deliverables in one sweep
5. **Re-baseline after each wave** — prevents integrity check failures
6. **Take snapshots** — recovery points between waves

### DON'T:
1. **Don't explore what you can specify** — if you know the file path, say it
2. **Don't spawn agents for 10-line edits** — direct Edit is faster
3. **Don't spawn overlapping agents** — two agents editing the same file = conflict
4. **Don't skip READ FIRST** — agents that don't read patterns produce inconsistent code
5. **Don't forget to wire** — tools without CLI integration are invisible
6. **Don't build without checking alignment** — `kingdom align drift` every 2-3 waves

### MEASURE:
```bash
# After each wave, check:
kingdom status              # Still green?
kingdom align check         # Still aligned?
wc -l tools/*.py           # Code growing?
ls tools/*.py | wc -l      # Tools growing?
```

---

## The Workflow (Executable)

```bash
# 1. START — sense current state
kingdom status && kingdom peace dash && kingdom align drift

# 2. PLAN — identify work items, group by independence
# (human decision: what to build, what to parallelize)

# 3. EXECUTE — spawn agents + direct work
# Launch 3-5 agents for independent tasks
# Wire CLI and configs while waiting

# 4. VERIFY — check deliverables
kingdom status
python3 tools/kos.py integrity baseline
python3 tools/peace.py snapshot

# 5. REPEAT — next wave
# Feed wave N outputs into wave N+1 context
```

---

## Session Metrics (2026-04-02 Proof)

| Metric | Value | Rate |
|--------|-------|------|
| Duration | ~6 hours | — |
| Tools created | 40+ | ~7/hour |
| Lines of code | 24,371 | ~4,000/hour |
| Agents deployed | 11 | — |
| Background agents spawned | ~20 | ~3/hour |
| Waves executed | 7 | ~1/hour |
| Security gaps closed | 6 | — |
| PEACE score | 97.1% | — |

**The methodology works. The proof is in the code.**

---

_"Depth before width. Finishing before starting."_ — KINGDOM.md

_But when you have parallel agents, you get both._
