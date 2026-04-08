"""Orchestrator — runs strategies adaptively.

The orchestrator is the execution engine. It takes a strategy, observes state,
executes steps, handles failures by switching strategies, and reports results.

Works with any agent backend (Claude API, local model, or headless/script mode).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from soma.auto.state import (
    ProjectState,
    observe,
    run_lint,
    run_tests,
)
from soma.auto.strategies import (
    STRATEGIES,
    Strategy,
    Step,
    StepKind,
    select_strategy,
)


class AgentBackend(Protocol):
    """Interface for the agent that executes ACT steps.

    Implementations:
      - ClaudeBackend: calls Claude API with the step prompt
      - OllamaBackend: calls a local model via Ollama
      - HeadlessBackend: runs scripts without LLM (for CI)
      - HumanBackend: prints the step and waits for human action
    """

    def execute_step(self, step: Step, context: dict[str, Any]) -> StepResult:
        """Execute a single step and return the result."""
        ...


@dataclass
class StepResult:
    step_name: str
    success: bool
    output: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0


@dataclass
class StrategyResult:
    strategy_name: str
    steps_completed: list[StepResult] = field(default_factory=list)
    final_state: ProjectState | None = None
    switched_to: str | None = None  # if strategy switched mid-run

    @property
    def success(self) -> bool:
        return all(s.success for s in self.steps_completed)

    @property
    def summary(self) -> str:
        ok = sum(1 for s in self.steps_completed if s.success)
        total = len(self.steps_completed)
        status = "PASSED" if self.success else "FAILED"
        return f"{self.strategy_name}: {ok}/{total} steps {status}"


class HeadlessBackend:
    """Runs strategies without an LLM — uses scripts and commands.

    This is the CI/automated mode. ACT steps that require code generation
    are skipped (they need an LLM). OBSERVE and VALIDATE steps run via scripts.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).parent.parent.parent

    def execute_step(self, step: Step, context: dict[str, Any]) -> StepResult:
        start = time.monotonic()
        match step.action:
            case "observe_state":
                state = observe(self.root)
                return StepResult(
                    step_name=step.name,
                    success=True,
                    output=f"health={state.health}, {len(state.real_modules)} real modules, {state.total_tests} tests",
                    data={"health": state.health, "modules": len(state.real_modules)},
                    duration_s=time.monotonic() - start,
                )
            case "run_lint":
                result = run_lint(self.root)
                return StepResult(
                    step_name=step.name,
                    success=result.clean,
                    output=f"{'clean' if result.clean else f'{result.error_count} errors'}",
                    data={"clean": result.clean, "errors": list(result.errors)},
                    duration_s=time.monotonic() - start,
                )
            case "run_tests":
                result = run_tests(self.root)
                return StepResult(
                    step_name=step.name,
                    success=result.all_green,
                    output=f"{result.passed} passed, {result.failed} failed, {result.errors} errors",
                    data={
                        "passed": result.passed,
                        "failed": result.failed,
                        "failures": list(result.failure_details),
                    },
                    duration_s=time.monotonic() - start,
                )
            case "run_e2e":
                proc = subprocess.run(
                    [sys.executable, "scripts/e2e.py"],
                    capture_output=True, text=True, cwd=self.root,
                )
                return StepResult(
                    step_name=step.name,
                    success=proc.returncode == 0,
                    output=proc.stdout[-500:] if proc.stdout else proc.stderr[-500:],
                    duration_s=time.monotonic() - start,
                )
            case "run_install":
                proc = subprocess.run(
                    ["uv", "sync", "--all-extras"],
                    capture_output=True, text=True, cwd=self.root,
                )
                return StepResult(
                    step_name=step.name,
                    success=proc.returncode == 0,
                    output="installed" if proc.returncode == 0 else proc.stderr[-300:],
                    duration_s=time.monotonic() - start,
                )
            case "run_smoke":
                proc = subprocess.run(
                    [sys.executable, "scripts/smoke.py"],
                    capture_output=True, text=True, cwd=self.root,
                )
                return StepResult(
                    step_name=step.name,
                    success=proc.returncode == 0,
                    output=proc.stdout[-300:] if proc.stdout else "",
                    duration_s=time.monotonic() - start,
                )
            case _:
                # ACT steps that require an LLM are skipped in headless mode
                if step.kind == StepKind.ACT:
                    return StepResult(
                        step_name=step.name,
                        success=True,
                        output="[headless: skipped — requires agent]",
                        duration_s=0.0,
                    )
                return StepResult(
                    step_name=step.name,
                    success=True,
                    output=f"[headless: no handler for {step.action}]",
                    duration_s=0.0,
                )


def run_strategy(
    strategy: Strategy,
    backend: AgentBackend | None = None,
    root: Path | None = None,
) -> StrategyResult:
    """Execute a strategy step by step with adaptive branching."""
    if backend is None:
        backend = HeadlessBackend(root)

    result = StrategyResult(strategy_name=strategy.name)
    context: dict[str, Any] = {"strategy": strategy.name}

    for step in strategy.steps:
        step_result = backend.execute_step(step, context)
        result.steps_completed.append(step_result)

        # Update context with step output
        context[step.name] = step_result.data

        if not step_result.success:
            match step.on_fail:
                case "abort":
                    break
                case "continue":
                    continue
                case s if s.startswith("switch:"):
                    new_strategy_name = s.removeprefix("switch:")
                    if new_strategy_name in STRATEGIES:
                        result.switched_to = new_strategy_name
                        # Recursive execution of the fallback strategy
                        sub = run_strategy(STRATEGIES[new_strategy_name], backend, root)
                        result.steps_completed.extend(sub.steps_completed)
                    break

    # Capture final state
    result.final_state = observe(root)
    return result


def auto(task_hint: str = "", root: Path | None = None) -> StrategyResult:
    """The top-level entry point. Observe → select strategy → run.

    This is what an agent calls. It figures out what to do.
    """
    state = observe(root)
    strategy = select_strategy(state.health, task_hint)
    return run_strategy(strategy, root=root)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI: python -m soma.auto [strategy_name | auto] [--json]"""
    import argparse

    parser = argparse.ArgumentParser(description="SOMA strategy orchestrator")
    parser.add_argument("strategy", nargs="?", default="auto",
                        help="Strategy name or 'auto' for adaptive selection")
    parser.add_argument("--task", default="", help="Task hint for auto-selection")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--list", action="store_true", help="List available strategies")
    args = parser.parse_args()

    if args.list:
        for name, s in STRATEGIES.items():
            print(f"  {name:<20} {s.purpose}")
        return

    if args.strategy == "auto":
        result = auto(task_hint=args.task)
    elif args.strategy in STRATEGIES:
        result = run_strategy(STRATEGIES[args.strategy])
    else:
        print(f"Unknown strategy: {args.strategy}")
        print(f"Available: {', '.join(STRATEGIES)}")
        sys.exit(1)

    if args.json:
        out = {
            "strategy": result.strategy_name,
            "success": result.success,
            "steps": [
                {
                    "name": s.step_name,
                    "success": s.success,
                    "output": s.output,
                    "duration_s": round(s.duration_s, 3),
                }
                for s in result.steps_completed
            ],
            "health": result.final_state.health if result.final_state else "unknown",
        }
        print(json.dumps(out, indent=2))
    else:
        print(f"\n{'═' * 50}")
        print(f"  Strategy: {result.strategy_name}")
        print(f"{'═' * 50}\n")
        for s in result.steps_completed:
            mark = "✓" if s.success else "✗"
            time_str = f"{s.duration_s:.1f}s" if s.duration_s > 0.1 else ""
            print(f"  {mark} {s.step_name:<30} {s.output:<40} {time_str}")
        if result.switched_to:
            print(f"\n  → switched to: {result.switched_to}")
        print(f"\n  {result.summary}")
        if result.final_state:
            print(f"  health: {result.final_state.health}")
        print()


if __name__ == "__main__":
    main()
