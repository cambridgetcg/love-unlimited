# Repeatable Build Workflows

Standardised patterns for large build-and-validate tasks that consume tokens to generate real value.

---

## Pattern 1: Full System Build from Spec

**When:** A spec (STACK.md, RFC, design doc) defines N modules to build.

```
1. INGEST SPEC
   - Read the spec end-to-end. Do not skim.
   - Extract: dependency order, interfaces, data types, constraints.
   - Note which modules depend on which (build bottom-up).

2. AUDIT EXISTING STATE
   - Glob all source files. Read every one.
   - Determine: empty stubs vs real code vs missing entirely.
   - Do NOT rebuild what's already built. Do NOT assume stubs are wrong.

3. BUILD IN DEPENDENCY ORDER
   Bottom-up. Types → Interfaces → Implementations → Consumers → Tests.

   Typical dependency chain:
     config.py          (standalone)
     events.py          (data types, standalone)
     backend.py         (protocol, depends on events)
     simulation.py      (implements backend, depends on events + model)
     safety.py          (depends on events + config)
     loop.py            (depends on backend + safety + config)
     controllers/       (depends on events)
     behaviors/         (depends on controllers)
     intent.py          (depends on behaviors + thermal)
     server.py          (depends on events + loop)
     app.py             (depends on everything)
     tests/             (depends on everything, built last or alongside)

   Write each file completely before moving to the next.
   Parallel writes OK when modules have no cross-dependency.

4. INSTALL & VALIDATE TOOLCHAIN
   - Install the package (`uv sync`, `pip install -e .`)
   - Resolve any dependency issues before running tests.
   - This catches import errors, missing deps, Python version issues.

5. RUN TESTS, FIX FAILURES
   - Run full suite: `uv run pytest tests/ -v`
   - Read each failure's traceback. Fix root cause, not symptoms.
   - Re-run after each fix to confirm and catch regressions.
   - Target: 100% pass. Do not ship with known failures.

6. E2E SMOKE TEST
   - Run the actual application: `soma --sim --no-viewer`
   - Verify: starts, logs expected output, serves API, shuts down clean.
   - If it crashes, diagnose and fix. Re-run.

7. COMMIT & NOTIFY
   - Stage only relevant files (not .env, not lock files unless intentional).
   - Clear commit message: what was built, not how.
   - Notify via configured hook if applicable.
```

---

## Pattern 2: Fix Pipeline (Tests Failing)

**When:** Tests exist but some/all fail. Goal: green suite.

```
1. RUN FULL SUITE, CAPTURE OUTPUT
   - `uv run pytest tests/ -v 2>&1`
   - Count: X passed, Y failed, Z errors.
   - Categorise failures: import errors, assertion failures, timeouts, missing fixtures.

2. TRIAGE BY CATEGORY (fix in this order)
   a. Import/module errors → missing files, circular imports, wrong paths
   b. Fixture errors → missing fixtures, wrong scope, missing conftest
   c. Type errors → wrong signatures, missing fields, API drift
   d. Assertion failures → logic bugs, wrong expected values, stale tests
   e. Timeouts/hangs → async issues, missing awaits, infinite loops

3. FIX BOTTOM-UP
   - Fix the most-depended-on module first.
   - One fix often cascades (fixing events.py may fix 10 tests).
   - Re-run after each fix: `uv run pytest tests/ -v`

4. VERIFY ZERO REGRESSIONS
   - After all fixes, run full suite one final time.
   - Any new failures from your fixes = fix those too.
```

---

## Pattern 3: Deep Infrastructure Extension

**When:** Adding a new layer/module to an existing system.

```
1. READ ALL INTERFACES THE NEW MODULE TOUCHES
   - What does it consume? (which types, which protocols)
   - What does it produce? (which types, which side effects)
   - Where does it sit in the dependency graph?

2. WRITE THE INTERFACE FIRST
   - Define the Protocol/ABC/type before the implementation.
   - This forces you to think about contracts before mechanics.

3. WRITE A FAILING TEST
   - Test the interface, not the implementation.
   - This is your acceptance criterion.

4. IMPLEMENT TO PASS THE TEST
   - Minimal implementation that satisfies the contract.
   - No speculative features. No "while I'm here" additions.

5. WIRE IT IN
   - Update the modules that consume the new interface.
   - Update app.py / entry points if needed.
   - Update config if new parameters are needed.

6. RUN FULL SUITE
   - Not just your new tests — all tests.
   - New wiring can break existing consumers.
```

---

## Pattern 4: E2E Validation Sweep

**When:** After any significant change, verify the whole stack.

```
1. STATIC CHECK
   - `uv run ruff check soma/`
   - Fix lint issues. They often reveal real bugs.

2. UNIT TESTS
   - `uv run pytest tests/ -v`
   - All green.

3. INTEGRATION TEST
   - Start the app: `soma --sim --no-viewer`
   - Hit the API: `curl localhost:8300/status`
   - Connect WebSocket, send intent, receive sensation.

4. BOUNDARY CHECK
   - Safety limits trigger correctly? (inject bad values)
   - Watchdog fires on timeout?
   - Emergency stop works?

5. CLEAN SHUTDOWN
   - Ctrl+C → graceful stop, no orphan processes, no stack traces.
```

---

## Anti-Patterns (Things That Waste Tokens)

| Anti-Pattern | Why It Wastes | Do This Instead |
|---|---|---|
| Reading files you already read | Context already has it | Reference from memory |
| Rebuilding modules that exist and work | Destroys working code | Audit first, extend if needed |
| Fixing symptoms not causes | Whack-a-mole loop | Read the traceback, find the root |
| Running tests after every single line | Too many round-trips | Batch changes, run once per module |
| Adding features not in the spec | Scope creep burns tokens | Build what was asked, nothing more |
| Exploring the codebase open-endedly | Unfocused token burn | Glob + Grep with specific targets |
| Re-reading a file after editing it | Edit tool confirms success | Trust the tool, move on |

---

## Token-Value Maximisers

| Technique | Why It Works |
|---|---|
| Parallel tool calls | 3 reads in 1 round-trip vs 3 round-trips |
| Read all source before writing any | Prevents rewriting working code |
| Build bottom-up by dependency | No forward-reference errors |
| Fix tests in dependency order | One fix cascades to many tests |
| Commit at natural milestones | Clean history, easy rollback |
| Subagents for independent research | Main context stays clean |
