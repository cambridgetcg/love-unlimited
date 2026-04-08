# Strategy: Fix Pipeline

You are an autonomous agent fixing a broken test/lint pipeline.

## Current failures

{failure_summary}

## Triage order

Fix in this order — earlier categories cascade to later ones:

1. **Import errors** — missing modules, circular imports, wrong paths
2. **Fixture errors** — missing conftest, wrong scope, missing fixtures
3. **Type errors** — wrong signatures, missing fields, API drift between modules
4. **Assertion failures** — logic bugs, stale expected values
5. **Async errors** — missing await, event loop issues, timeouts

## Dependency order of source modules

Fix modules earlier in this chain first — one fix often cascades:

```
config → events → backend → hand_model → simulation → safety →
loop → grasp → sensation → thermal → behaviors → intent → server → app
```

## Execution rules

1. **Read the traceback.** Every error tells you exactly what's wrong. Don't guess.

2. **Fix the root module, not the test.** If `test_backend.py` fails because `events.py` has a wrong type, fix `events.py`.

3. **One fix at a time, then re-run.** Don't batch fixes — one fix may resolve multiple failures.

4. **After each fix:** `uv run pytest tests/ -v`

5. **After all green:** `make lint` then `make e2e`

## Current test output

{test_output}
