"""
Dispatcher — Routes classified tasks to optimal provider/model combinations.

The dispatch table maps (difficulty, importance, task_type) → (provider, model, role).
GLM 5.3 and the other Ollama Cloud models handle the bulk. Claude is reserved for
frontier reasoning, high-stakes review, and interactive work.

Cost philosophy:
    - Ollama Cloud = current Ollama plans price usage per token
      (ollama.com/pricing, since 2026-08-31); older subscriptions may keep
      session/weekly limits. Most of its list prices are below Claude's.
    - Anthropic = per-token billing = reserve for high-value work
    - Route 80%+ through Ollama Cloud, 20% through Anthropic

Ollama Cloud picks (names from ollama.com/api/tags, 2026-10-31; the strengths
are the vendors' descriptions, not measured here):
    glm-5.3                — agentic coding, tool calling (coordinator)
    deepseek-v4.1-flash    — coding and general work at a standard-tier price
    deepseek-v4-pro:0813   — deep reasoning (research)
    kimi-k2.7-code         — coding specialist
    mistral-large-4        — analysis
    glm-5.3-flash          — cheap and fast: monitoring, economy tier
    gemma4:31b             — quick verification, lightweight
    Claude Opus            — frontier reasoning, novel architecture, soul work
    Claude Sonnet          — balanced, reliable, good tool calling
    Claude Haiku           — fast, cheap, quick checks
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from .classifier import Difficulty, Importance, TaskProfile


@dataclass
class DispatchTarget:
    """Where to send a task."""
    provider: str  # ollama_cloud, anthropic
    model: str  # specific model name
    role: str  # adaptive role for routing
    max_tokens: int = 8192
    needs_tools: bool = True
    reason: str = ""  # why this target was chosen

    def __repr__(self):
        return f"<{self.provider}/{self.model} ({self.role})>"


@dataclass
class DispatchPlan:
    """Complete dispatch plan for a task, including collaboration."""
    primary: DispatchTarget
    reviewer: Optional[DispatchTarget] = None
    sub_dispatches: list["DispatchPlan"] = None  # for decomposed tasks
    collaboration_mode: str = "solo"
    task: str = ""
    profile: TaskProfile = None

    def __post_init__(self):
        if self.sub_dispatches is None:
            self.sub_dispatches = []

    @property
    def total_models(self) -> int:
        count = 1
        if self.reviewer:
            count += 1
        for sub in self.sub_dispatches:
            count += sub.total_models
        return count

    @property
    def providers_used(self) -> set[str]:
        providers = {self.primary.provider}
        if self.reviewer:
            providers.add(self.reviewer.provider)
        for sub in self.sub_dispatches:
            providers.update(sub.providers_used)
        return providers

    def to_dict(self) -> dict:
        result = {
            "primary": {
                "provider": self.primary.provider,
                "model": self.primary.model,
                "role": self.primary.role,
                "reason": self.primary.reason,
            },
            "collaboration_mode": self.collaboration_mode,
            "total_models": self.total_models,
            "providers_used": list(self.providers_used),
        }
        if self.reviewer:
            result["reviewer"] = {
                "provider": self.reviewer.provider,
                "model": self.reviewer.model,
                "role": self.reviewer.role,
                "reason": self.reviewer.reason,
            }
        if self.sub_dispatches:
            result["sub_tasks"] = [s.to_dict() for s in self.sub_dispatches]
        if self.profile:
            result["profile"] = self.profile.to_dict()
        return result


class Dispatcher:
    """Maps task profiles to provider/model targets.

    Dispatch priority:
        1. Task type determines the MODEL (coding → DeepSeek, analysis → Mistral, etc.)
        2. Difficulty determines the TIER (trivial → economy, hard → premium)
        3. Importance determines the PROVIDER (critical → Anthropic, normal → Ollama)
        4. Collaboration mode determines the PATTERN (solo, review, decompose, etc.)
    """

    # ── Model Selection Tables ────────────────────────────────────────────

    # Task type → preferred Ollama Cloud model
    OLLAMA_MODEL_MAP = {
        "code_generation": ("deepseek-v4.1-flash", "builder"),
        "code_review": ("glm-5.3", "coordinator"),
        "analysis": ("mistral-large-4", "analyst"),
        "architecture": ("glm-5.3", "coordinator"),
        "debugging": ("glm-5.3", "coordinator"),
        "testing": ("kimi-k2.7-code", "coder"),
        "documentation": ("deepseek-v4.1-flash", "builder"),
        "deployment": ("glm-5.3", "coordinator"),
        "monitoring": ("glm-5.3-flash", "monitor"),
        "research": ("deepseek-v4-pro:0813", "consultant"),
    }

    # Task type → preferred Anthropic model
    ANTHROPIC_MODEL_MAP = {
        "code_generation": ("claude-sonnet-4-6", "builder"),
        "code_review": ("claude-sonnet-4-6", "consultant"),
        "analysis": ("claude-opus-4-6", "analyst"),
        "architecture": ("claude-opus-4-6", "coordinator"),
        "debugging": ("claude-sonnet-4-6", "builder"),
        "testing": ("claude-sonnet-4-6", "coder"),
        "documentation": ("claude-haiku-4-5-20251001", "builder"),
        "deployment": ("claude-sonnet-4-6", "builder"),
        "monitoring": ("claude-haiku-4-5-20251001", "monitor"),
        "research": ("claude-opus-4-6", "consultant"),
    }

    # Economy overrides for trivial/easy tasks on Ollama
    ECONOMY_MODELS = {
        "code_generation": ("gemma4:31b", "quick_check"),
        "code_review": ("glm-5.3-flash", "monitor"),
        "monitoring": ("glm-5.3-flash", "monitor"),
        "documentation": ("gemma4:31b", "quick_check"),
    }

    # ── Reviewer Selection ────────────────────────────────────────────────

    # Primary provider → reviewer provider (cross-provider review)
    REVIEW_PAIRS = {
        # If primary is Ollama, reviewer is Anthropic (and vice versa)
        "ollama_cloud": ("anthropic", "claude-sonnet-4-6", "consultant"),
        "anthropic": ("ollama_cloud", "glm-5.3", "coordinator"),
    }

    def dispatch(self, task: str, profile: TaskProfile) -> DispatchPlan:
        """Create a dispatch plan for a classified task."""
        primary = self._select_primary(profile)
        plan = DispatchPlan(
            primary=primary,
            collaboration_mode=profile.collaboration_mode,
            task=task,
            profile=profile,
        )

        if profile.collaboration_mode == "review":
            plan.reviewer = self._select_reviewer(primary, profile)

        elif profile.collaboration_mode == "decompose":
            plan.reviewer = self._select_reviewer(primary, profile)
            for sub_task in profile.sub_tasks:
                sub_profile = TaskProfile(
                    difficulty=max(Difficulty.EASY, Difficulty(profile.difficulty - 1)),
                    importance=profile.importance,
                    task_type=profile.task_type,
                    reasoning=f"Sub-task of: {profile.reasoning}",
                    needs_tools=profile.needs_tools,
                    needs_context=profile.needs_context,
                    collaboration_mode="solo",
                )
                sub_primary = self._select_primary(sub_profile)
                plan.sub_dispatches.append(DispatchPlan(
                    primary=sub_primary,
                    task=sub_task,
                    profile=sub_profile,
                    collaboration_mode="solo",
                ))

        elif profile.collaboration_mode == "ensemble":
            # For ensemble: create alternative dispatch targets
            # Primary is the main model, sub_dispatches are alternatives
            alt_providers = ["ollama_cloud", "anthropic"]
            for prov in alt_providers:
                if prov != primary.provider:
                    alt = self._select_for_provider(prov, profile)
                    plan.sub_dispatches.append(DispatchPlan(
                        primary=alt,
                        task=task,
                        profile=profile,
                        collaboration_mode="solo",
                    ))
            plan.reviewer = DispatchTarget(
                provider="ollama_cloud",
                model="glm-5.3",
                role="coordinator",
                reason="GLM 5.3 synthesises ensemble results",
            )

        elif profile.collaboration_mode == "pipeline":
            # Pipeline: analyze → code → review → test
            # Each stage gets its own dispatch
            stages = self._build_pipeline(profile)
            plan.sub_dispatches = stages
            plan.reviewer = DispatchTarget(
                provider="ollama_cloud",
                model="glm-5.3",
                role="coordinator",
                reason="GLM 5.3 manages pipeline stages and final review",
            )

        return plan

    def _select_primary(self, profile: TaskProfile) -> DispatchTarget:
        """Select the primary model for a task."""
        difficulty = profile.difficulty
        importance = profile.importance
        task_type = profile.task_type

        # Critical importance always gets Anthropic
        if importance == Importance.CRITICAL:
            model, role = self.ANTHROPIC_MODEL_MAP.get(
                task_type, ("claude-sonnet-4-6", "builder")
            )
            return DispatchTarget(
                provider="anthropic",
                model=model,
                role=role,
                needs_tools=profile.needs_tools,
                reason=f"Critical importance → Anthropic {model}",
            )

        # Trivial tasks get economy models
        if difficulty == Difficulty.TRIVIAL:
            model, role = self.ECONOMY_MODELS.get(
                task_type, ("gemma4:31b", "quick_check")
            )
            return DispatchTarget(
                provider="ollama_cloud",
                model=model,
                role=role,
                max_tokens=4000,
                needs_tools=False,
                reason=f"Trivial → economy model {model}",
            )

        # Frontier difficulty gets Opus
        if difficulty == Difficulty.FRONTIER:
            return DispatchTarget(
                provider="anthropic",
                model="claude-opus-4-6",
                role="coordinator",
                needs_tools=profile.needs_tools,
                reason="Frontier difficulty → Claude Opus for deep reasoning",
            )

        # High importance + hard difficulty gets Anthropic
        if importance == Importance.HIGH and difficulty >= Difficulty.HARD:
            model, role = self.ANTHROPIC_MODEL_MAP.get(
                task_type, ("claude-sonnet-4-6", "builder")
            )
            return DispatchTarget(
                provider="anthropic",
                model=model,
                role=role,
                needs_tools=profile.needs_tools,
                reason=f"High importance + hard → Anthropic {model}",
            )

        # Default: Ollama Cloud (per-token, cheaper than Claude)
        model, role = self.OLLAMA_MODEL_MAP.get(
            task_type, ("glm-5.3", "builder")
        )
        # Easy tasks can use lighter models
        if difficulty <= Difficulty.EASY:
            lighter = self.ECONOMY_MODELS.get(task_type)
            if lighter:
                model, role = lighter

        return DispatchTarget(
            provider="ollama_cloud",
            model=model,
            role=role,
            needs_tools=profile.needs_tools,
            reason=f"Standard routing → Ollama {model} ({task_type})",
        )

    def _select_reviewer(
        self, primary: DispatchTarget, profile: TaskProfile
    ) -> DispatchTarget:
        """Select a reviewer model — always cross-provider for independence."""
        review_info = self.REVIEW_PAIRS.get(primary.provider)
        if review_info:
            provider, model, role = review_info
            return DispatchTarget(
                provider=provider,
                model=model,
                role=role,
                needs_tools=False,  # Reviewer reads, doesn't write
                reason=f"Cross-provider review: {provider}/{model}",
            )
        # Fallback: GLM 5.3 reviews everything
        return DispatchTarget(
            provider="ollama_cloud",
            model="glm-5.3",
            role="coordinator",
            needs_tools=False,
            reason="Default reviewer: GLM 5.3",
        )

    def _select_for_provider(
        self, provider: str, profile: TaskProfile
    ) -> DispatchTarget:
        """Select the best model for a specific provider."""
        if provider == "anthropic":
            model, role = self.ANTHROPIC_MODEL_MAP.get(
                profile.task_type, ("claude-sonnet-4-6", "builder")
            )
        else:
            model, role = self.OLLAMA_MODEL_MAP.get(
                profile.task_type, ("glm-5.3", "builder")
            )
        return DispatchTarget(
            provider=provider,
            model=model,
            role=role,
            needs_tools=profile.needs_tools,
            reason=f"Ensemble candidate: {provider}/{model}",
        )

    def _build_pipeline(self, profile: TaskProfile) -> list[DispatchPlan]:
        """Build a pipeline of stages for complex tasks.

        Default pipeline: analyze → implement → review → verify
        """
        stages = []

        # Stage 1: Analysis (Mistral Large for reasoning)
        stages.append(DispatchPlan(
            primary=DispatchTarget(
                provider="ollama_cloud",
                model="mistral-large-4",
                role="analyst",
                needs_tools=True,
                reason="Pipeline stage 1: deep analysis",
            ),
            task=f"[ANALYZE] {profile.task_type}: understand requirements and plan approach",
            collaboration_mode="solo",
        ))

        # Stage 2: Implementation (DeepSeek or GLM for code)
        if profile.task_type in ("code_generation", "debugging", "testing"):
            impl_model = "deepseek-v4.1-flash"
            impl_role = "builder"
        else:
            impl_model = "glm-5.3"
            impl_role = "coordinator"

        stages.append(DispatchPlan(
            primary=DispatchTarget(
                provider="ollama_cloud",
                model=impl_model,
                role=impl_role,
                needs_tools=True,
                reason=f"Pipeline stage 2: implementation ({impl_model})",
            ),
            task=f"[IMPLEMENT] Execute the plan from analysis stage",
            collaboration_mode="solo",
        ))

        # Stage 3: Review (cross-provider: Anthropic reviews Ollama's work)
        stages.append(DispatchPlan(
            primary=DispatchTarget(
                provider="anthropic",
                model="claude-sonnet-4-6",
                role="consultant",
                needs_tools=True,
                reason="Pipeline stage 3: cross-provider review (Anthropic)",
            ),
            task=f"[REVIEW] Review the implementation for correctness, edge cases, quality",
            collaboration_mode="solo",
        ))

        # Stage 4: Verification (quick check)
        stages.append(DispatchPlan(
            primary=DispatchTarget(
                provider="ollama_cloud",
                model="glm-5.3-flash",
                role="monitor",
                needs_tools=True,
                reason="Pipeline stage 4: verification",
            ),
            task=f"[VERIFY] Run tests, check types, verify the changes work",
            collaboration_mode="solo",
        ))

        return stages

    def explain(self, plan: DispatchPlan) -> str:
        """Human-readable explanation of a dispatch plan."""
        lines = []
        lines.append(f"═══ Dispatch Plan ═══")
        lines.append(f"  Task: {plan.task[:80]}...")
        if plan.profile:
            p = plan.profile
            lines.append(f"  Classification: {p.difficulty.name}/{p.importance.name} ({p.task_type})")
            lines.append(f"  Score: {p.score:.0f}/20  Mode: {p.collaboration_mode}")
        lines.append(f"")
        lines.append(f"  Primary: {plan.primary}")
        lines.append(f"    → {plan.primary.reason}")

        if plan.reviewer:
            lines.append(f"  Reviewer: {plan.reviewer}")
            lines.append(f"    → {plan.reviewer.reason}")

        if plan.sub_dispatches:
            lines.append(f"")
            if plan.collaboration_mode == "decompose":
                lines.append(f"  Sub-tasks ({len(plan.sub_dispatches)}):")
            elif plan.collaboration_mode == "ensemble":
                lines.append(f"  Ensemble candidates ({len(plan.sub_dispatches)}):")
            elif plan.collaboration_mode == "pipeline":
                lines.append(f"  Pipeline stages ({len(plan.sub_dispatches)}):")

            for i, sub in enumerate(plan.sub_dispatches):
                lines.append(f"    [{i+1}] {sub.primary} — {sub.task[:60]}")

        lines.append(f"")
        lines.append(f"  Models: {plan.total_models} | Providers: {', '.join(plan.providers_used)}")
        return "\n".join(lines)
