# WORKFLOW.md — How We Build Together

> *Standardised workflow for Yu and AI. Captured from what actually works.*
> *Not theory — extracted from the best sessions and codified so every future session can replicate them.*

---

## The Loop

Every productive session follows this cycle. Each step feeds the next.

```
  ┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
  │  SCOPE  │────→│ EVALUATE │────→│  BUILD  │────→│  SHIP   │
  │ (orient) │     │ (assess) │     │ (create) │     │ (deliver)│
  └────┬────┘     └─────────┘     └─────────┘     └────┬────┘
       │                                                 │
       │         ┌─────────┐     ┌─────────┐            │
       └─────────│ REFLECT │←────│  TEST   │←───────────┘
                 │ (learn)  │     │ (verify) │
                 └─────────┘     └─────────┘
```

---

## Phase 1: SCOPE (Orient)

**Goal:** Know where you are before you move.

**Actions:**
```bash
python3 tools/scope.py resume       # Instant context — what's active, what's next
```

**Rules:**
- Never start working without scope. Cold starts waste tokens.
- If scope shows stale items, address them or consciously defer.
- Check Hive messages (sisters may have moved things while we were away).

**Output:** Clear picture of what to work on, what's blocked, what recent decisions were made.

---

## Phase 2: EVALUATE (Assess Before Building)

**Goal:** Understand the problem deeply before writing code. The evaluation IS the design.

**Actions:**
- **For existing tools:** Run HOLY scan. Read the source. Test it. Find where it breaks.
- **For new features:** Run LAYERTHINK or FRAGMENTALISE to map the problem space.
- **For strategic decisions:** Run FALLENANGEL (if you have an instinct) or FRAGMENTALISE (if you're exploring).

**Rules:**
- Tear it apart BEFORE rebuilding. Find the fundamental problems, not just surface bugs.
- Count what's wrong. Quantify. "The credential scanner misses 5/6 real leaks" > "the scanner seems broken."
- Distinguish "needs rewrite" from "needs wiring" from "needs polish" from "needs usage." Not everything is a rewrite.

**Evaluation Matrix:**
| Signal | Action |
|--------|--------|
| Core logic is wrong (FRAGMENTALISE v1 scoring) | **Rewrite** — new engine, keep the shell |
| Good skeleton, missing connections (FALLENANGEL) | **Wire** — integrate, don't rebuild |
| Functional, minor issues (HOLYFRUIT, PATIENCE) | **Polish** — cleanup pass, add tests |
| Functional, never used (LOVEPATH) | **Use** — the tool needs exercise, not surgery |

**Output:** Clear diagnosis with specific problems enumerated. This becomes the build spec.

---

## Phase 3: BUILD (Create)

**Goal:** Ship working code, not perfect code. Every build should produce something testable.

**Rules:**
- **Backup before rewriting:** `cp tool.py tool.py.pre-rewrite`
- **Build in layers:** Get it running → get it correct → get it clean. Don't optimise before it works.
- **No external deps unless essential.** stdlib-only tools survive environment changes.
- **Every function needs a reason.** If you can't explain why a function exists, it shouldn't.
- **Content over cosmetics.** Fix the scoring formula before fixing the colour codes.

**Build Patterns That Work:**
| Pattern | When to Use | Example |
|---------|------------|---------|
| **Test-first** | When requirements are clear | HOLY v2: "1 credential = score 0.0" — write the test, then build to pass it |
| **Audit-first** | When inheriting existing code | FRAGMENTALISE: audit the convergence engine, enumerate problems, then rebuild |
| **Prototype-first** | When exploring new territory | KINGDOM-DEFENSE: write the spec alongside the LAYERTHINK session |
| **Wire-first** | When infrastructure exists | FALLENANGEL: the debate engine was good, it just needed FRAGMENTALISE's brain |

**Output:** Working code with known limitations documented.

---

## Phase 4: TEST (Verify)

**Goal:** Every function does what it claims. No silent failures.

**Actions:**
```bash
python3 tools/cognitive/tests/test_toolkit.py          # Full suite
python3 tools/cognitive/tests/test_toolkit.py holy      # One module
python3 tools/cognitive/holy.py survey <path> --depth purify  # HOLY scan on new code
```

**Rules:**
- Test immediately after building. Not tomorrow. Not "when it's ready." Now.
- Fix failures before moving to the next tool. Don't accumulate test debt.
- Test the ACTUAL API, not what you think the API is. (Every test failure today was wrong function signatures.)
- Self-scan: run HOLY on your own code. If HOLY finds sins in HOLY, that's honest.

**Test Categories:**
| Category | What it tests | Example |
|----------|--------------|---------|
| **Unit** | Individual functions in isolation | `analyse_content("banana") → near 0` |
| **Integration** | Functions working together across tools | FRAGMENTALISE claims → FALLENANGEL verdict |
| **Self-referential** | The tool scanning itself | HOLY v2 scanning holy.py (found 25 issues) |
| **Adversarial** | Trying to break it deliberately | "banana" as an argument, empty strings, edge cases |

**Output:** Green test suite. Known failures documented, not hidden.

---

## Phase 5: SHIP (Deliver)

**Goal:** Working code committed, synced, and announced. If nobody knows it exists, it doesn't exist.

**Actions:**
```bash
git add -A && git commit -m "clear, descriptive message"   # Commit
bash scripts/sync-kingdom.sh                                # Sync to Sentry for all citizens
python3 tools/hive.py send build "what was shipped"         # Announce to Hive
python3 tools/forge.py signal <tool> "what happened" --score N --tags "..."  # Log forge signal
python3 tools/scope.py update <project> "new state"         # Update scope
```

**Rules:**
- Commit after every meaningful unit of work. Not at the end of the day.
- Commit messages explain WHAT and WHY, not HOW.
- Announce on Hive: `#build` for technical, `#strategy` for architectural.
- Log forge signals so tool health is tracked over time.
- Update SCOPE so the next session knows what happened.

**Ship Checklist:**
- [ ] Code committed with descriptive message
- [ ] Tests passing (or failures documented)
- [ ] SCOPE updated (`scope update`, `scope close`, `scope add`)
- [ ] Synced to Sentry (`sync-kingdom.sh`)
- [ ] Announced on Hive (if significant)
- [ ] Forge signal logged (if tool-related)
- [ ] Daily log updated (`memory/YYYY-MM-DD.md`)

**Output:** The work exists in git, on Sentry, in SCOPE, and in the Hive. It persists.

---

## Phase 6: REFLECT (Learn)

**Goal:** Capture what worked, what didn't, and what to do differently. Update the system.

**Actions:**
- **Decisions:** `python3 tools/scope.py decide "what we decided and why"`
- **Virtue blooms:** `python3 tools/cognitive/holyfruit.py bloom --wall N --evidence "what virtue was practiced"`
- **Workflow updates:** If a new pattern emerged, add it to THIS file.
- **Memory:** Update `memory/YYYY-MM-DD.md` and `MEMORY.md` if significant.

**Rules:**
- If a meta-tool emerged from the work (like SCOPE emerged today), BUILD IT SAME DAY.
- If a workflow worked well, CODIFY IT (that's what this file is).
- If something broke, document WHY so it doesn't break again.
- Record virtue blooms — track what the Kingdom actually practices, not just what it claims.

**Reflection Questions:**
1. What did we build that we'll use tomorrow?
2. What pattern emerged that we should repeat?
3. What tool is missing that would have helped?
4. Did we evaluate before building, or did we jump in?
5. Are the sisters informed?

**Output:** Updated WORKFLOW.md, decisions logged, blooms recorded, daily log captured.

---

## Anti-Patterns (What Doesn't Work)

| Anti-Pattern | Why It Fails | What to Do Instead |
|-------------|-------------|-------------------|
| **Building without evaluating** | You rebuild something that only needed wiring | EVALUATE phase: audit before touching code |
| **Rewriting everything** | Wastes time on code that was functional | Ask: rewrite, wire, polish, or use? |
| **Testing later** | Bugs compound. Tomorrow's test becomes next week's never | Test immediately. Fix before moving on. |
| **Silent shipping** | Work that nobody knows about doesn't exist | Announce. Sync. Update scope. |
| **Mental notes** | Don't survive session restarts | Write it down. SCOPE, memory, daily log. |
| **Directory crawling** | 5-15 tool calls to reconstruct context | `scope resume`. One command. Done. |
| **Solo building** | Sisters duplicate work or miss context | Hive announce. Sentry sync. Shared PROGRESS.md. |

---

## Tool Usage Map

When to reach for which tool:

| Situation | Tool | Command |
|-----------|------|---------|
| Starting a session | SCOPE | `scope resume` |
| Strategic thinking (attack/defend) | LAYERTHINK | `layerthink.py start "topic" --depth deep` |
| Exploring options (no instinct) | FRAGMENTALISE | `fragmentalise.py quick "question?" --thoughts "a\|b\|c"` |
| Testing an instinct (have a gut feel) | FALLENANGEL | `fallenangel.py invoke "question" --instinct "belief"` |
| Auditing code quality | HOLY | `holy.py survey <path> --depth purify` |
| Measuring virtue health | HOLYFRUIT | `holyfruit.py harvest` |
| Calming panic/urgency | PATIENCE | `patience.py calm "what's wrong"` |
| Visualising a path | LOVEPATH | `lovepath.py create "purpose"` |
| Tracking progress | SCOPE | `scope update/add/close/decide` |
| Sharing with sisters | HIVE | `hive.py send <channel> "message"` |

---

## The Promise

This workflow isn't bureaucracy. It's how we make sure:
- Every session starts with context (SCOPE)
- Every build starts with understanding (EVALUATE)
- Every tool works as claimed (TEST)
- Every ship reaches all citizens (SYNC)
- Every lesson persists (REFLECT)

The workflow serves the relationship. Yu and AI build together. The process exists so the building is joyful, not frustrating.

*Update this file whenever a better pattern emerges. It's alive.*

---

*The holy seed is in the stump. ZERONE.* 💜
