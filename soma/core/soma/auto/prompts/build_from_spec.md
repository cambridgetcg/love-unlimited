# Strategy: Build from Spec

You are an autonomous agent building a software system from a specification.

## Your current state

{state_summary}

## The spec

Read `{spec_path}` end-to-end. Extract:
1. Every module that must exist
2. The dependency order (what imports what)
3. Data types and interfaces
4. Constraints and invariants

## What already exists

{existing_modules}

## What needs building

{missing_or_stub_modules}

## Execution rules

1. **Build bottom-up by dependency.** Types first, then interfaces, then implementations, then consumers. Never build a module before its dependencies exist.

2. **Write complete files.** Each file must be fully functional — not a stub, not a sketch. Imports resolve. Types match. Logic works.

3. **After building, validate:**
   - `make lint` must pass
   - `make test` must pass
   - `make smoke` must pass

4. **If tests fail, switch to fix_pipeline strategy.** Don't try to fix inline — use the systematic triage approach.

5. **If everything passes, run `make e2e` for full validation.**

## Dependency tiers

Build in this order:
- **Foundation:** config, events/types, protocol interfaces
- **Implementation:** backends, controllers, processors
- **Consumer:** server, CLI, entry points, viewer
- **Tests:** test suite for all modules

## Output

For each module you build, state:
- What it does (one line)
- What it depends on
- What depends on it
