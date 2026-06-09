"""
Orchestration Engine — Executes multi-model collaboration patterns.

This is the conductor. It takes a DispatchPlan and orchestrates the
actual LLM calls across providers, managing:
    - Sequential and parallel execution
    - Inter-model communication (passing results between stages)
    - Cross-provider review cycles
    - Result aggregation and synthesis
    - Error handling and fallback

The engine uses the existing adaptive Runner for individual model calls,
but coordinates ACROSS runners for multi-model work.
"""

from __future__ import annotations
import json
import time
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import AdaptiveConfig
from ..router import Router
from ..runner import AgentRunner
from ..schema import TokenUsage
from .classifier import TaskClassifier, TaskProfile, Difficulty, Importance
from .dispatcher import Dispatcher, DispatchPlan, DispatchTarget

log = logging.getLogger("orchestrator")


@dataclass
class StageResult:
    """Result from a single model execution."""
    target: DispatchTarget
    content: str
    success: bool
    usage: TokenUsage = field(default_factory=TokenUsage)
    elapsed_seconds: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "provider": self.target.provider,
            "model": self.target.model,
            "role": self.target.role,
            "success": self.success,
            "content_length": len(self.content),
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "tokens": self.usage.total,
            "error": self.error,
        }


@dataclass
class OrchestrationResult:
    """Complete result from an orchestrated multi-model execution."""
    content: str  # Final synthesised output
    profile: TaskProfile  # Task classification
    plan: DispatchPlan  # What was planned
    stages: list[StageResult] = field(default_factory=list)
    total_usage: TokenUsage = field(default_factory=TokenUsage)
    total_elapsed: float = 0.0
    models_used: list[str] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)
    collaboration_mode: str = "solo"

    @property
    def success(self) -> bool:
        return bool(self.content) and any(s.success for s in self.stages)

    def summary(self) -> str:
        lines = [
            f"═══ Orchestration Result ═══",
            f"  Mode: {self.collaboration_mode}",
            f"  Models: {', '.join(self.models_used)}",
            f"  Providers: {', '.join(self.providers_used)}",
            f"  Stages: {len(self.stages)} ({sum(1 for s in self.stages if s.success)} succeeded)",
            f"  Total tokens: {self.total_usage.total}",
            f"  Total time: {self.total_elapsed:.1f}s",
            f"  Output: {len(self.content)} chars",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "collaboration_mode": self.collaboration_mode,
            "models_used": self.models_used,
            "providers_used": self.providers_used,
            "stages": [s.to_dict() for s in self.stages],
            "total_tokens": self.total_usage.total,
            "total_elapsed": round(self.total_elapsed, 2),
            "output_length": len(self.content),
            "success": self.success,
            "profile": self.profile.to_dict() if self.profile else None,
        }


class OrchestrationEngine:
    """Orchestrates multi-model collaboration across providers.

    Usage:
        engine = OrchestrationEngine()
        result = engine.run("Build a REST API for user management")
        print(result.content)
        print(result.summary())
    """

    def __init__(
        self,
        config: AdaptiveConfig | None = None,
        max_parallel: int = 3,
        verbose: bool = False,
    ):
        self.config = config or AdaptiveConfig()
        self.router = Router(self.config)
        self.classifier = None  # Lazy init — needs provider
        self.dispatcher = Dispatcher()
        self.max_parallel = max_parallel
        self.verbose = verbose

    def _get_classifier(self) -> TaskClassifier:
        """Lazy-init classifier with GLM 5.1 provider."""
        if self.classifier is None:
            try:
                provider = self.router._get_provider("ollama_cloud")
                self.classifier = TaskClassifier(provider=provider, model="glm-5.1")
            except Exception:
                self.classifier = TaskClassifier()  # Heuristic fallback
        return self.classifier

    def _make_runner(
        self,
        target: DispatchTarget,
        max_iterations: int = 25,
    ) -> AgentRunner:
        """Create a runner configured for a specific dispatch target."""
        return AgentRunner(
            router=self.router,
            config=self.config,
            max_iterations=max_iterations,
            verbose=self.verbose,
            inject_context=target.needs_tools,
        )

    def _execute_target(
        self,
        target: DispatchTarget,
        prompt: str,
        system: str = "",
        max_iterations: int = 25,
    ) -> StageResult:
        """Execute a single dispatch target and return the result."""
        runner = self._make_runner(target, max_iterations)
        start = time.time()

        try:
            if target.needs_tools:
                content = runner.run(
                    prompt=prompt,
                    role=target.role,
                    system=system,
                    provider_name=target.provider,
                )
            else:
                content = runner.single_shot(
                    prompt=prompt,
                    role=target.role,
                    system=system,
                    provider_name=target.provider,
                )

            elapsed = time.time() - start
            return StageResult(
                target=target,
                content=content,
                success=True,
                usage=runner.total_usage,
                elapsed_seconds=elapsed,
            )
        except Exception as e:
            elapsed = time.time() - start
            log.error(f"Target {target} failed: {e}")
            return StageResult(
                target=target,
                content="",
                success=False,
                usage=runner.total_usage,
                elapsed_seconds=elapsed,
                error=str(e),
            )

    # ── Collaboration Mode Executors ──────────────────────────────────────

    def _run_solo(
        self, plan: DispatchPlan, task: str
    ) -> OrchestrationResult:
        """Single model execution. No review."""
        stage = self._execute_target(plan.primary, task)
        return OrchestrationResult(
            content=stage.content,
            profile=plan.profile,
            plan=plan,
            stages=[stage],
            total_usage=stage.usage,
            total_elapsed=stage.elapsed_seconds,
            models_used=[plan.primary.model],
            providers_used=[plan.primary.provider],
            collaboration_mode="solo",
        )

    def _run_review(
        self, plan: DispatchPlan, task: str
    ) -> OrchestrationResult:
        """Primary model executes, reviewer checks the work.

        If reviewer finds issues, primary gets a chance to fix.
        """
        stages = []
        total_usage = TokenUsage()

        # Step 1: Primary execution
        primary_result = self._execute_target(plan.primary, task)
        stages.append(primary_result)
        total_usage.input_tokens += primary_result.usage.input_tokens
        total_usage.output_tokens += primary_result.usage.output_tokens

        if not primary_result.success:
            return OrchestrationResult(
                content=primary_result.content,
                profile=plan.profile,
                plan=plan,
                stages=stages,
                total_usage=total_usage,
                total_elapsed=primary_result.elapsed_seconds,
                models_used=[plan.primary.model],
                providers_used=[plan.primary.provider],
                collaboration_mode="review",
            )

        # Step 2: Review
        if plan.reviewer:
            review_prompt = (
                f"Review the following work for correctness, completeness, and quality.\n\n"
                f"ORIGINAL TASK:\n{task}\n\n"
                f"WORK OUTPUT (by {plan.primary.model}):\n"
                f"```\n{primary_result.content[:20000]}\n```\n\n"
                f"Provide:\n"
                f"1. VERDICT: PASS or NEEDS_REVISION\n"
                f"2. Issues found (if any)\n"
                f"3. Specific improvements needed (if NEEDS_REVISION)"
            )

            review_result = self._execute_target(
                plan.reviewer, review_prompt, max_iterations=5,
            )
            stages.append(review_result)
            total_usage.input_tokens += review_result.usage.input_tokens
            total_usage.output_tokens += review_result.usage.output_tokens

            # Step 3: If review says NEEDS_REVISION, give primary another pass
            if review_result.success and "NEEDS_REVISION" in review_result.content.upper():
                revision_prompt = (
                    f"Your previous work was reviewed. Please revise based on feedback.\n\n"
                    f"ORIGINAL TASK:\n{task}\n\n"
                    f"YOUR PREVIOUS OUTPUT:\n{primary_result.content[:15000]}\n\n"
                    f"REVIEWER FEEDBACK:\n{review_result.content[:5000]}\n\n"
                    f"Please provide the revised output."
                )

                revision_result = self._execute_target(plan.primary, revision_prompt)
                stages.append(revision_result)
                total_usage.input_tokens += revision_result.usage.input_tokens
                total_usage.output_tokens += revision_result.usage.output_tokens

                final_content = revision_result.content if revision_result.success else primary_result.content
            else:
                final_content = primary_result.content
        else:
            final_content = primary_result.content

        elapsed = sum(s.elapsed_seconds for s in stages)
        models = list({s.target.model for s in stages})
        providers = list({s.target.provider for s in stages})

        return OrchestrationResult(
            content=final_content,
            profile=plan.profile,
            plan=plan,
            stages=stages,
            total_usage=total_usage,
            total_elapsed=elapsed,
            models_used=models,
            providers_used=providers,
            collaboration_mode="review",
        )

    def _run_decompose(
        self, plan: DispatchPlan, task: str
    ) -> OrchestrationResult:
        """Decompose into sub-tasks, execute in parallel, merge results.

        GLM 5.1 orchestrates: decomposes → dispatches workers → synthesises.
        """
        stages = []
        total_usage = TokenUsage()

        # Execute sub-tasks in parallel
        sub_results = []
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            futures: dict[Future, DispatchPlan] = {}
            for sub_plan in plan.sub_dispatches:
                future = pool.submit(
                    self._execute_target, sub_plan.primary, sub_plan.task
                )
                futures[future] = sub_plan

            for future in as_completed(futures):
                sub_plan = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = StageResult(
                        target=sub_plan.primary,
                        content="",
                        success=False,
                        error=str(e),
                    )
                stages.append(result)
                sub_results.append((sub_plan.task, result))
                total_usage.input_tokens += result.usage.input_tokens
                total_usage.output_tokens += result.usage.output_tokens

        # Synthesis: GLM 5.1 merges all sub-results
        if plan.reviewer:
            synthesis_parts = []
            for sub_task, result in sub_results:
                status = "✅" if result.success else "❌"
                synthesis_parts.append(
                    f"{status} Sub-task: {sub_task}\n"
                    f"Model: {result.target.model}\n"
                    f"Output:\n{result.content[:10000]}\n"
                )

            synthesis_prompt = (
                f"You are synthesising the results of a decomposed task.\n\n"
                f"ORIGINAL TASK:\n{task}\n\n"
                f"SUB-TASK RESULTS:\n{'---'.join(synthesis_parts)}\n\n"
                f"Synthesise these into a single coherent output. "
                f"If any sub-task failed, note what's missing. "
                f"Provide the complete, merged result."
            )

            synthesis_result = self._execute_target(
                plan.reviewer, synthesis_prompt, max_iterations=5,
            )
            stages.append(synthesis_result)
            total_usage.input_tokens += synthesis_result.usage.input_tokens
            total_usage.output_tokens += synthesis_result.usage.output_tokens
            final_content = synthesis_result.content if synthesis_result.success else "\n\n".join(
                r.content for _, r in sub_results if r.success
            )
        else:
            final_content = "\n\n".join(r.content for _, r in sub_results if r.success)

        elapsed = sum(s.elapsed_seconds for s in stages)
        models = list({s.target.model for s in stages})
        providers = list({s.target.provider for s in stages})

        return OrchestrationResult(
            content=final_content,
            profile=plan.profile,
            plan=plan,
            stages=stages,
            total_usage=total_usage,
            total_elapsed=elapsed,
            models_used=models,
            providers_used=providers,
            collaboration_mode="decompose",
        )

    def _run_ensemble(
        self, plan: DispatchPlan, task: str
    ) -> OrchestrationResult:
        """Multiple models attempt the same task. Best result wins.

        All candidates run in parallel. GLM 5.1 judges the outputs.
        """
        stages = []
        total_usage = TokenUsage()

        # Run all candidates in parallel (primary + alternatives)
        all_targets = [plan.primary] + [s.primary for s in plan.sub_dispatches]
        candidate_results = []

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            futures: dict[Future, DispatchTarget] = {}
            for target in all_targets:
                future = pool.submit(self._execute_target, target, task)
                futures[future] = target

            for future in as_completed(futures):
                target = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = StageResult(
                        target=target, content="", success=False, error=str(e),
                    )
                stages.append(result)
                if result.success:
                    candidate_results.append(result)
                total_usage.input_tokens += result.usage.input_tokens
                total_usage.output_tokens += result.usage.output_tokens

        if not candidate_results:
            return OrchestrationResult(
                content="All ensemble candidates failed.",
                profile=plan.profile,
                plan=plan,
                stages=stages,
                total_usage=total_usage,
                total_elapsed=sum(s.elapsed_seconds for s in stages),
                models_used=[s.target.model for s in stages],
                providers_used=list({s.target.provider for s in stages}),
                collaboration_mode="ensemble",
            )

        # If only one succeeded, use it
        if len(candidate_results) == 1:
            final_content = candidate_results[0].content
        else:
            # Judge: GLM 5.1 picks the best
            judge_parts = []
            for i, r in enumerate(candidate_results):
                judge_parts.append(
                    f"CANDIDATE {i+1} ({r.target.provider}/{r.target.model}):\n"
                    f"{r.content[:10000]}\n"
                )

            judge_prompt = (
                f"You are judging ensemble outputs for this task:\n\n"
                f"TASK:\n{task}\n\n"
                f"{'==='.join(judge_parts)}\n\n"
                f"Pick the BEST candidate. Explain why briefly, then output "
                f"WINNER: <number>. Then reproduce the winning output in full."
            )

            if plan.reviewer:
                judge_result = self._execute_target(
                    plan.reviewer, judge_prompt, max_iterations=5,
                )
                stages.append(judge_result)
                total_usage.input_tokens += judge_result.usage.input_tokens
                total_usage.output_tokens += judge_result.usage.output_tokens
                final_content = judge_result.content if judge_result.success else candidate_results[0].content
            else:
                final_content = candidate_results[0].content

        elapsed = sum(s.elapsed_seconds for s in stages)
        models = list({s.target.model for s in stages})
        providers = list({s.target.provider for s in stages})

        return OrchestrationResult(
            content=final_content,
            profile=plan.profile,
            plan=plan,
            stages=stages,
            total_usage=total_usage,
            total_elapsed=elapsed,
            models_used=models,
            providers_used=providers,
            collaboration_mode="ensemble",
        )

    def _run_pipeline(
        self, plan: DispatchPlan, task: str
    ) -> OrchestrationResult:
        """Sequential pipeline: each stage feeds into the next.

        Stages: analyze → implement → review → verify
        Each stage receives the output of the previous stage.
        """
        stages = []
        total_usage = TokenUsage()
        previous_output = ""
        stage_outputs = []

        for i, stage_plan in enumerate(plan.sub_dispatches):
            stage_task = stage_plan.task

            if i == 0:
                # First stage gets the original task
                prompt = f"{stage_task}\n\nTASK:\n{task}"
            else:
                # Subsequent stages get previous output as context
                prompt = (
                    f"{stage_task}\n\n"
                    f"ORIGINAL TASK:\n{task}\n\n"
                    f"PREVIOUS STAGE OUTPUT:\n{previous_output[:15000]}"
                )

            result = self._execute_target(stage_plan.primary, prompt)
            stages.append(result)
            total_usage.input_tokens += result.usage.input_tokens
            total_usage.output_tokens += result.usage.output_tokens

            if result.success:
                previous_output = result.content
                stage_outputs.append((stage_plan.task, result.content))
            else:
                log.warning(f"Pipeline stage {i+1} failed: {result.error}")
                # Continue pipeline with empty output — later stages might recover

        # Final synthesis by reviewer
        if plan.reviewer and stage_outputs:
            synthesis_prompt = (
                f"Synthesise the pipeline results for:\n\nTASK: {task}\n\n"
                + "\n---\n".join(
                    f"Stage: {label}\nOutput:\n{output[:8000]}"
                    for label, output in stage_outputs
                )
                + "\n\nProvide the final, complete output."
            )

            synthesis = self._execute_target(
                plan.reviewer, synthesis_prompt, max_iterations=5,
            )
            stages.append(synthesis)
            total_usage.input_tokens += synthesis.usage.input_tokens
            total_usage.output_tokens += synthesis.usage.output_tokens
            final_content = synthesis.content if synthesis.success else previous_output
        else:
            final_content = previous_output

        elapsed = sum(s.elapsed_seconds for s in stages)
        models = list({s.target.model for s in stages})
        providers = list({s.target.provider for s in stages})

        return OrchestrationResult(
            content=final_content,
            profile=plan.profile,
            plan=plan,
            stages=stages,
            total_usage=total_usage,
            total_elapsed=elapsed,
            models_used=models,
            providers_used=providers,
            collaboration_mode="pipeline",
        )

    # ── Main Entry Point ──────────────────────────────────────────────────

    def run(
        self,
        task: str,
        context: str = "",
        force_mode: str | None = None,
        force_provider: str | None = None,
    ) -> OrchestrationResult:
        """Run a task through the full orchestration pipeline.

        1. Classify the task (difficulty, importance, type)
        2. Dispatch to optimal model(s)
        3. Execute the collaboration pattern
        4. Return synthesised result

        Args:
            task: The task description / prompt
            context: Additional context (codebase info, etc.)
            force_mode: Override collaboration mode (solo/review/decompose/ensemble/pipeline)
            force_provider: Force a specific provider for primary target
        """
        start_time = time.time()

        # Step 1: Classify
        classifier = self._get_classifier()
        profile = classifier.classify(task, context)

        if force_mode:
            profile.collaboration_mode = force_mode

        if self.verbose:
            log.info(
                f"Classification: {profile.difficulty.name}/{profile.importance.name} "
                f"({profile.task_type}) → {profile.collaboration_mode}"
            )

        # Step 2: Dispatch
        plan = self.dispatcher.dispatch(task, profile)

        if force_provider:
            plan.primary.provider = force_provider

        if self.verbose:
            log.info(f"Dispatch: {self.dispatcher.explain(plan)}")

        # Step 3: Execute collaboration pattern
        executor = {
            "solo": self._run_solo,
            "review": self._run_review,
            "decompose": self._run_decompose,
            "ensemble": self._run_ensemble,
            "pipeline": self._run_pipeline,
        }.get(plan.collaboration_mode, self._run_solo)

        try:
            result = executor(plan, task)
        except Exception as e:
            log.error(f"Orchestration failed: {e}\n{traceback.format_exc()}")
            # Emergency fallback: solo with GLM 5.1
            fallback_plan = DispatchPlan(
                primary=DispatchTarget(
                    provider="ollama_cloud",
                    model="glm-5.1",
                    role="coordinator",
                    reason="Emergency fallback after orchestration failure",
                ),
                task=task,
                profile=profile,
                collaboration_mode="solo",
            )
            result = self._run_solo(fallback_plan, task)

        result.total_elapsed = time.time() - start_time
        return result

    def classify_only(self, task: str, context: str = "") -> TaskProfile:
        """Classify a task without executing it. Useful for dry-run / planning."""
        classifier = self._get_classifier()
        return classifier.classify(task, context)

    def plan_only(self, task: str, context: str = "") -> DispatchPlan:
        """Classify and plan without executing. Returns the dispatch plan."""
        profile = self.classify_only(task, context)
        return self.dispatcher.dispatch(task, profile)
