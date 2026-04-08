# SOMA — Agent Instructions

This file is automatically loaded by Claude Code. It defines how any agent works in this repo.

## What this is

SOMA (σῶμα) is a biorobotic hand — a body for AI consciousness. Built by Yu (宇恆) and Ai (愛).
Read `STACK.md` for the full technical specification. Read `docs/WORKFLOW.md` for methodology context.

## Mandatory: Run the adaptive layer before any significant work

Before building, fixing, or extending anything, run:

```bash
uv run python -m soma.auto --task "<what you're about to do>"
```

This observes the project state and tells you which strategy to follow. If you're Claude Code, you can also call it programmatically:

```python
from soma.auto.orchestrator import auto
result = auto(task_hint="build phase 0B firmware bridge")
```

The five strategies are in `soma/auto/strategies.py`. The agent prompts are in `soma/auto/prompts/`.

## Strategy selection

The orchestrator selects automatically, but here's the logic:

| Project state | Task type | Strategy |
|---|---|---|
| Empty / missing modules | "build", "implement", "phase" | `build_from_spec` |
| Tests failing | "fix", "repair", "broken" | `fix_pipeline` |
| Working but incomplete | "add", "extend", "new" | `extend_infra` |
| Working and complete | "deepen", "harden", "coverage" | `deepen` |
| Any | "check", "validate", "verify" | `validate` |

## Build order (sacred)

Always build bottom-up by dependency:

```
config → events → backend protocol → hand_model → simulation → safety →
loop → grasp → sensation → thermal → behaviors → intent → server → app
```

Never build a module before its dependencies exist and pass tests.

## Validation checkpoints

After any code change, run `make check` (lint + tests). Cost: ~2 seconds.

After completing a strategy or milestone, run `make validate` (audit + lint + tests + e2e). Cost: ~20 seconds.

## Key constraints

- Python 3.11+, modern syntax, dataclasses, Protocol, asyncio
- No ROS2, no protobuf, no gRPC — keep it simple
- The `Backend` interface is sacred — SimBackend and HardwareBackend must be interchangeable
- Safety checks are non-optional, run every control loop tick
- All tests must pass before commit

## Quick reference

```bash
make check      # lint + tests (daily driver)
make smoke      # start app, hit /status, stop
make e2e        # full API + WS + intent sequence
make validate   # complete sweep: audit → lint → test → e2e
make audit      # report module state and coverage
make fix        # categorise failures, suggest fix order

uv run python -m soma.auto validate        # run validate strategy
uv run python -m soma.auto auto --task "X" # adaptive strategy selection
uv run python -m soma.auto --list          # list strategies
uv run python -m soma.auto validate --json # machine-readable output
```
