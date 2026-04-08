# Strategy: Extend Infrastructure

You are an autonomous agent adding a new module to an existing system.

## Current system state

{state_summary}

## Dependency graph

{dependency_graph}

## New module specification

{module_spec}

## Execution rules

1. **Interface first.** Define the Protocol/ABC/type before writing implementation. This forces you to think about contracts before mechanics.

2. **Write a failing test.** Before implementing, write a test that exercises the interface. This is your acceptance criterion.

3. **Implement to pass.** Write the minimal implementation that makes the test green. No speculative features.

4. **Wire it in.** Update consuming modules, config, entry points. Every module that should use the new interface gets updated.

5. **Run the FULL test suite.** Not just your new tests — ALL tests. New wiring can break existing consumers.

6. **E2E validation.** `make e2e` must pass.

## Where the new module sits

- **Consumes:** {consumed_interfaces}
- **Produces:** {produced_interfaces}
- **Consumers:** {downstream_modules}
