"""
Task Classifier — Determines difficulty and importance using GLM 5.1.

The classifier runs a lightweight GLM 5.1 call to analyze each incoming
task and produce a TaskProfile. The profile drives all routing decisions.

Difficulty levels:
    trivial  — formatting, renaming, one-line fix
    easy     — single-file change, well-defined scope
    medium   — multi-file, requires understanding context
    hard     — architectural, cross-cutting, ambiguous
    frontier — novel design, requires deep reasoning or creativity

Importance levels:
    low      — nice-to-have, cleanup, cosmetic
    medium   — useful improvement, bug fix
    high     — blocks progress, user-facing, data integrity
    critical — system down, security, data loss risk

The classifier also detects task TYPE to influence model selection:
    code_generation, code_review, analysis, architecture, debugging,
    testing, documentation, deployment, monitoring, research
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from ..schema import CompletionRequest, Message


class Difficulty(IntEnum):
    TRIVIAL = 1
    EASY = 2
    MEDIUM = 3
    HARD = 4
    FRONTIER = 5


class Importance(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class TaskProfile:
    """Classification result for a task."""
    difficulty: Difficulty
    importance: Importance
    task_type: str  # code_generation, analysis, architecture, etc.
    reasoning: str  # why this classification
    estimated_tokens: int = 4000  # expected output size
    needs_tools: bool = True  # does the task need file/bash access?
    needs_context: bool = True  # does it need codebase context?
    decomposable: bool = False  # can it be split into sub-tasks?
    sub_tasks: list[str] = field(default_factory=list)  # suggested sub-tasks if decomposable
    collaboration_mode: str = "solo"  # solo, review, decompose, ensemble, pipeline

    @property
    def score(self) -> float:
        """Combined difficulty × importance score (1-20)."""
        return float(self.difficulty * self.importance)

    def to_dict(self) -> dict:
        return {
            "difficulty": self.difficulty.name.lower(),
            "importance": self.importance.name.lower(),
            "task_type": self.task_type,
            "reasoning": self.reasoning,
            "estimated_tokens": self.estimated_tokens,
            "needs_tools": self.needs_tools,
            "needs_context": self.needs_context,
            "decomposable": self.decomposable,
            "sub_tasks": self.sub_tasks,
            "collaboration_mode": self.collaboration_mode,
            "score": self.score,
        }


CLASSIFY_SYSTEM = """You are a task classifier for a software development system.
Analyze the given task and return a JSON classification.

You MUST respond with ONLY valid JSON, no markdown fences, no explanation outside the JSON.

Schema:
{
    "difficulty": "trivial|easy|medium|hard|frontier",
    "importance": "low|medium|high|critical",
    "task_type": "code_generation|code_review|analysis|architecture|debugging|testing|documentation|deployment|monitoring|research",
    "reasoning": "brief explanation of classification",
    "estimated_tokens": <int, expected output length>,
    "needs_tools": <bool, needs file/shell access>,
    "needs_context": <bool, needs to read existing code>,
    "decomposable": <bool, can be split into independent sub-tasks>,
    "sub_tasks": ["sub-task 1", "sub-task 2"] // only if decomposable=true
}

Classification rules:
- trivial: one-liner, formatting, rename. 1 file, <5 min.
- easy: clear scope, single file, well-defined. <15 min.
- medium: multi-file, needs understanding. 15-60 min.
- hard: architectural, cross-cutting, ambiguous requirements. 1-4 hours.
- frontier: novel design, no clear pattern, requires deep reasoning.

- low: nice-to-have, cosmetic, cleanup
- medium: useful improvement, non-blocking bug
- high: blocks progress, user-facing, data integrity
- critical: system down, security issue, data loss"""


DIFFICULTY_MAP = {
    "trivial": Difficulty.TRIVIAL,
    "easy": Difficulty.EASY,
    "medium": Difficulty.MEDIUM,
    "hard": Difficulty.HARD,
    "frontier": Difficulty.FRONTIER,
}

IMPORTANCE_MAP = {
    "low": Importance.LOW,
    "medium": Importance.MEDIUM,
    "high": Importance.HIGH,
    "critical": Importance.CRITICAL,
}


class TaskClassifier:
    """Classifies tasks using GLM 5.1 or falls back to heuristic classification."""

    def __init__(self, provider=None, model: str = "glm-5.1"):
        self.provider = provider
        self.model = model

    def classify(self, task: str, context: str = "") -> TaskProfile:
        """Classify a task. Uses LLM if provider available, else heuristics."""
        if self.provider:
            try:
                return self._classify_llm(task, context)
            except Exception:
                pass
        return self._classify_heuristic(task)

    def _classify_llm(self, task: str, context: str = "") -> TaskProfile:
        """Use GLM 5.1 to classify the task."""
        prompt = f"Task: {task}"
        if context:
            prompt += f"\n\nContext: {context}"

        request = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model=self.model,
            max_tokens=4000,  # min for reasoning models
            temperature=0.0,
            system=CLASSIFY_SYSTEM,
        )

        response = self.provider.complete(request)
        return self._parse_classification(response.content)

    def _parse_classification(self, text: str) -> TaskProfile:
        """Parse LLM JSON response into TaskProfile."""
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        data = json.loads(text)

        difficulty = DIFFICULTY_MAP.get(
            data.get("difficulty", "medium"), Difficulty.MEDIUM
        )
        importance = IMPORTANCE_MAP.get(
            data.get("importance", "medium"), Importance.MEDIUM
        )
        task_type = data.get("task_type", "code_generation")

        decomposable = data.get("decomposable", False)
        sub_tasks = data.get("sub_tasks", []) if decomposable else []

        # Determine collaboration mode from difficulty + importance
        collaboration_mode = self._determine_collaboration(
            difficulty, importance, decomposable, task_type
        )

        return TaskProfile(
            difficulty=difficulty,
            importance=importance,
            task_type=task_type,
            reasoning=data.get("reasoning", ""),
            estimated_tokens=data.get("estimated_tokens", 4000),
            needs_tools=data.get("needs_tools", True),
            needs_context=data.get("needs_context", True),
            decomposable=decomposable,
            sub_tasks=sub_tasks,
            collaboration_mode=collaboration_mode,
        )

    def _determine_collaboration(
        self,
        difficulty: Difficulty,
        importance: Importance,
        decomposable: bool,
        task_type: str,
    ) -> str:
        """Determine the collaboration mode based on classification.

        Decision matrix:
            trivial/easy + any importance    → solo
            medium + low/medium              → solo
            medium + high/critical           → review
            hard + decomposable              → decompose
            hard + not decomposable          → review (with frontier model)
            frontier + any                   → pipeline or ensemble
            any + critical + architecture    → ensemble (multiple perspectives)
        """
        if difficulty <= Difficulty.EASY:
            return "solo"

        if difficulty == Difficulty.MEDIUM:
            if importance >= Importance.HIGH:
                return "review"
            return "solo"

        if difficulty == Difficulty.HARD:
            if decomposable:
                return "decompose"
            return "review"

        if difficulty == Difficulty.FRONTIER:
            if task_type == "architecture":
                return "ensemble"
            return "pipeline"

        return "solo"

    def _classify_heuristic(self, task: str) -> TaskProfile:
        """Fallback heuristic classification when LLM is unavailable.

        Uses keyword matching and task length as signals.
        """
        task_lower = task.lower()

        # Difficulty signals
        hard_signals = [
            "architect", "redesign", "refactor entire", "migration",
            "cross-cutting", "distributed", "concurrent", "security audit",
        ]
        frontier_signals = [
            "novel", "research", "design from scratch", "invent",
            "breakthrough", "unprecedented",
        ]
        easy_signals = [
            "rename", "format", "typo", "add comment", "update version",
            "bump", "lint", "simple fix",
        ]
        trivial_signals = [
            "one-line", "whitespace", "import", "todo",
        ]

        difficulty = Difficulty.MEDIUM
        if any(s in task_lower for s in frontier_signals):
            difficulty = Difficulty.FRONTIER
        elif any(s in task_lower for s in hard_signals):
            difficulty = Difficulty.HARD
        elif any(s in task_lower for s in easy_signals):
            difficulty = Difficulty.EASY
        elif any(s in task_lower for s in trivial_signals):
            difficulty = Difficulty.TRIVIAL

        # Importance signals
        critical_signals = ["security", "data loss", "crash", "down", "urgent", "broken"]
        high_signals = ["blocks", "user-facing", "production", "deploy", "release"]
        low_signals = ["cleanup", "cosmetic", "nice-to-have", "minor"]

        importance = Importance.MEDIUM
        if any(s in task_lower for s in critical_signals):
            importance = Importance.CRITICAL
        elif any(s in task_lower for s in high_signals):
            importance = Importance.HIGH
        elif any(s in task_lower for s in low_signals):
            importance = Importance.LOW

        # Task type detection
        # Order matters: more specific types checked first
        type_signals = [
            ("testing", ["test", "spec", "coverage", "assert"]),
            ("debugging", ["debug", "fix", "bug", "error", "crash", "broken"]),
            ("architecture", ["architect", "design system", "plan", "structure"]),
            ("code_review", ["review", "audit", "inspect"]),
            ("analysis", ["analyze", "investigate", "diagnose", "profile"]),
            ("documentation", ["doc", "readme", "comment", "explain"]),
            ("deployment", ["deploy", "release", "ship", "publish"]),
            ("monitoring", ["monitor", "alert", "health", "status"]),
            ("research", ["research", "explore", "evaluate", "compare"]),
            ("code_generation", ["implement", "create", "build", "write", "add feature"]),
        ]

        task_type = "code_generation"
        for ttype, signals in type_signals:
            if any(s in task_lower for s in signals):
                task_type = ttype
                break

        # Decomposable if task is long or mentions multiple things
        decomposable = (
            len(task) > 500
            or task_lower.count(" and ") > 1
            or task_lower.count("\n") > 3
            or difficulty >= Difficulty.HARD
        )

        collaboration_mode = self._determine_collaboration(
            difficulty, importance, decomposable, task_type
        )

        return TaskProfile(
            difficulty=difficulty,
            importance=importance,
            task_type=task_type,
            reasoning="Heuristic classification (LLM unavailable)",
            estimated_tokens=4000 if difficulty <= Difficulty.EASY else 8192,
            needs_tools=task_type not in ("documentation", "analysis"),
            needs_context=task_type not in ("documentation", "research"),
            decomposable=decomposable,
            collaboration_mode=collaboration_mode,
        )
