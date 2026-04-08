# Strategy: Deepen

You are an autonomous agent spending tokens to increase system robustness.

## Current state

{state_summary}

## Coverage gaps

{coverage_gaps}

## Untested modules

{untested_modules}

## What to deepen

In priority order:

1. **Missing test files** — modules with zero test coverage
2. **Boundary conditions** — min/max values, empty inputs, overflow
3. **Error paths** — disconnection, timeout, invalid input, corrupted data
4. **Concurrency** — race conditions in async code, control loop timing
5. **Integration seams** — where modules meet (backend ↔ loop, loop ↔ server)

## Execution rules

1. **One test file at a time.** Write tests for one untested module, run, verify, move on.

2. **Test the contract, not the implementation.** Test what the module promises, not how it does it.

3. **Edge cases that matter:**
   - What happens when the motor array has NaN?
   - What happens when WebSocket disconnects mid-intent?
   - What happens when all 16 motors hit current limit simultaneously?
   - What happens when thermal target is below ambient?

4. **After each new test file:** `uv run pytest tests/ -v`

5. **After all tests written:** `make validate`
