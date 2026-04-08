"""Strategy definitions — the what and why of each automated workflow.

Each strategy is a declarative pipeline of steps. Steps can be:
  - observe: read project state
  - gate: check a condition, branch or abort
  - act: do something (run command, generate prompt, write file)
  - validate: run checks and report

Strategies adapt: they observe before acting, and branch based on what they find.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepKind(Enum):
    OBSERVE = "observe"
    GATE = "gate"
    ACT = "act"
    VALIDATE = "validate"


@dataclass(frozen=True)
class Step:
    kind: StepKind
    name: str
    description: str
    action: str  # function name or command
    params: dict[str, Any] = field(default_factory=dict)
    on_fail: str = "abort"  # "abort" | "continue" | "switch:<strategy>"


@dataclass(frozen=True)
class Strategy:
    name: str
    purpose: str
    when: str  # natural language: when to use this strategy
    steps: tuple[Step, ...]

    def step_names(self) -> list[str]:
        return [s.name for s in self.steps]


# ── Strategy: Full Build from Spec ───────────────────────────────────────────

BUILD_FROM_SPEC = Strategy(
    name="build_from_spec",
    purpose="Build a complete system from a specification document",
    when="A spec defines N modules to build and tests to pass",
    steps=(
        Step(StepKind.OBSERVE, "ingest_spec",
             "Read the spec end-to-end, extract dependency order",
             "read_spec",
             params={"spec_path": "STACK.md"}),
        Step(StepKind.OBSERVE, "audit_existing",
             "Scan all source files: real code vs stubs vs missing",
             "observe_state"),
        Step(StepKind.GATE, "check_what_needs_building",
             "Compare spec requirements against existing code",
             "diff_spec_vs_state",
             on_fail="continue"),
        Step(StepKind.ACT, "build_types_and_interfaces",
             "Build data types and protocol interfaces first (no dependencies)",
             "build_modules",
             params={"tier": "foundation"}),
        Step(StepKind.ACT, "build_implementations",
             "Build implementations that depend on the interfaces",
             "build_modules",
             params={"tier": "implementation"}),
        Step(StepKind.ACT, "build_consumers",
             "Build modules that consume implementations (app, server, CLI)",
             "build_modules",
             params={"tier": "consumer"}),
        Step(StepKind.ACT, "build_tests",
             "Build test suite covering all modules",
             "build_modules",
             params={"tier": "tests"}),
        Step(StepKind.VALIDATE, "install",
             "Install the package and verify imports",
             "run_install"),
        Step(StepKind.VALIDATE, "lint",
             "Run linter, fix any issues",
             "run_lint",
             on_fail="switch:fix_lint"),
        Step(StepKind.VALIDATE, "unit_tests",
             "Run full test suite",
             "run_tests",
             on_fail="switch:fix_pipeline"),
        Step(StepKind.VALIDATE, "e2e",
             "Start app, hit every endpoint, verify shutdown",
             "run_e2e",
             on_fail="switch:fix_pipeline"),
        Step(StepKind.ACT, "commit",
             "Commit with clear message",
             "git_commit"),
    ),
)

# ── Strategy: Fix Pipeline ───────────────────────────────────────────────────

FIX_PIPELINE = Strategy(
    name="fix_pipeline",
    purpose="Diagnose and fix test/lint failures in dependency order",
    when="Tests or lint are failing",
    steps=(
        Step(StepKind.OBSERVE, "run_and_capture",
             "Run full test suite, capture all output",
             "run_tests"),
        Step(StepKind.GATE, "check_if_green",
             "If all tests pass, strategy is done",
             "check_tests_green",
             on_fail="continue"),
        Step(StepKind.OBSERVE, "categorise_failures",
             "Group failures: import → fixture → type → assertion → async",
             "categorise_failures"),
        Step(StepKind.OBSERVE, "find_root_modules",
             "Identify which source modules are likely broken",
             "trace_failure_to_source"),
        Step(StepKind.ACT, "fix_imports",
             "Fix import/module errors first (they cascade)",
             "fix_by_category",
             params={"category": "import"}),
        Step(StepKind.ACT, "fix_types",
             "Fix type/attribute errors (API drift)",
             "fix_by_category",
             params={"category": "type"}),
        Step(StepKind.ACT, "fix_assertions",
             "Fix assertion failures (logic bugs)",
             "fix_by_category",
             params={"category": "assertion"}),
        Step(StepKind.VALIDATE, "rerun_tests",
             "Verify all tests pass after fixes",
             "run_tests",
             on_fail="abort"),
        Step(StepKind.VALIDATE, "lint",
             "Verify lint is clean",
             "run_lint"),
    ),
)

# ── Strategy: Extend Infrastructure ──────────────────────────────────────────

EXTEND_INFRA = Strategy(
    name="extend_infra",
    purpose="Add a new module to an existing system",
    when="Adding a new layer, interface, or capability",
    steps=(
        Step(StepKind.OBSERVE, "map_interfaces",
             "Read all interfaces the new module will touch",
             "observe_state"),
        Step(StepKind.OBSERVE, "map_dependency_position",
             "Determine where the new module sits in the dependency graph",
             "analyse_dependencies"),
        Step(StepKind.ACT, "write_interface",
             "Define the Protocol/ABC before implementation",
             "build_modules",
             params={"tier": "foundation"}),
        Step(StepKind.ACT, "write_failing_test",
             "Write a test that imports and exercises the new interface",
             "build_modules",
             params={"tier": "tests"}),
        Step(StepKind.ACT, "implement",
             "Build minimal implementation that passes the test",
             "build_modules",
             params={"tier": "implementation"}),
        Step(StepKind.ACT, "wire_in",
             "Update consuming modules, config, entry points",
             "build_modules",
             params={"tier": "consumer"}),
        Step(StepKind.VALIDATE, "full_suite",
             "Run ALL tests, not just new ones",
             "run_tests",
             on_fail="switch:fix_pipeline"),
        Step(StepKind.VALIDATE, "e2e",
             "Verify the whole app still works",
             "run_e2e"),
    ),
)

# ── Strategy: Validate ───────────────────────────────────────────────────────

VALIDATE = Strategy(
    name="validate",
    purpose="Full validation sweep — prove the stack works",
    when="After any significant change, before commit, before PR",
    steps=(
        Step(StepKind.OBSERVE, "audit",
             "Report module state, dependency graph, coverage",
             "observe_state"),
        Step(StepKind.VALIDATE, "lint",
             "Ruff lint must be clean",
             "run_lint",
             on_fail="switch:fix_lint"),
        Step(StepKind.VALIDATE, "unit_tests",
             "All pytest tests must pass",
             "run_tests",
             on_fail="switch:fix_pipeline"),
        Step(StepKind.VALIDATE, "e2e",
             "App starts, API responds, WebSocket works, shutdown clean",
             "run_e2e"),
        Step(StepKind.OBSERVE, "coverage_check",
             "Flag untested modules",
             "check_coverage"),
    ),
)

# ── Strategy: Deepen ─────────────────────────────────────────────────────────

DEEPEN = Strategy(
    name="deepen",
    purpose="Spend tokens to increase robustness — more tests, edge cases, hardening",
    when="Core is working but needs depth: more test coverage, boundary tests, error paths",
    steps=(
        Step(StepKind.OBSERVE, "audit",
             "Find coverage gaps and untested edge cases",
             "observe_state"),
        Step(StepKind.OBSERVE, "find_gaps",
             "Identify untested modules, missing error paths, boundary conditions",
             "analyse_coverage_gaps"),
        Step(StepKind.ACT, "write_missing_tests",
             "Write tests for untested modules",
             "build_modules",
             params={"tier": "tests"}),
        Step(StepKind.ACT, "write_edge_case_tests",
             "Add edge case and boundary tests for existing modules",
             "build_modules",
             params={"tier": "tests"}),
        Step(StepKind.ACT, "write_error_path_tests",
             "Test error handling: bad input, disconnection, timeout",
             "build_modules",
             params={"tier": "tests"}),
        Step(StepKind.VALIDATE, "full_suite",
             "All tests pass including new ones",
             "run_tests",
             on_fail="switch:fix_pipeline"),
        Step(StepKind.VALIDATE, "e2e",
             "App still works end-to-end",
             "run_e2e"),
    ),
)

# ── Registry ─────────────────────────────────────────────────────────────────

STRATEGIES: dict[str, Strategy] = {
    s.name: s for s in [
        BUILD_FROM_SPEC,
        FIX_PIPELINE,
        EXTEND_INFRA,
        VALIDATE,
        DEEPEN,
    ]
}


def select_strategy(state_health: str, task_hint: str = "") -> Strategy:
    """Adaptive strategy selection based on project state and task.

    This is the decision function: given where the project is and what the user
    wants, which strategy should the agent run?
    """
    hint = task_hint.lower()

    # Explicit task hints override state-based selection
    if any(w in hint for w in ("build", "create", "implement", "phase")):
        return BUILD_FROM_SPEC
    if any(w in hint for w in ("fix", "repair", "broken", "failing")):
        return FIX_PIPELINE
    if any(w in hint for w in ("add", "extend", "new module", "new feature")):
        return EXTEND_INFRA
    if any(w in hint for w in ("deepen", "harden", "coverage", "edge case", "robust")):
        return DEEPEN
    if any(w in hint for w in ("check", "validate", "verify", "test")):
        return VALIDATE

    # State-based fallback
    match state_health:
        case "empty":
            return BUILD_FROM_SPEC
        case "red":
            return FIX_PIPELINE
        case "yellow":
            return VALIDATE
        case "green":
            return DEEPEN
        case _:
            return VALIDATE
