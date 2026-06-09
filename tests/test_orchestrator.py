#!/usr/bin/env python3
"""
Orchestrator Test Suite — Unit + Integration + E2E tests.

Test layers:
    T1-T5:  Unit tests (no LLM calls, pure logic)
    T6-T10: Integration tests (classifier with mock provider)
    T11-T15: E2E tests (real LLM calls against live providers)

Run:
    # Unit tests only (fast, no API calls)
    python3 -m pytest tests/test_orchestrator.py -k "unit" -v

    # Integration tests (mock LLM)
    python3 -m pytest tests/test_orchestrator.py -k "integration" -v

    # Full E2E (requires OLLAMA_API_KEY + ANTHROPIC_API_KEY)
    python3 -m pytest tests/test_orchestrator.py -k "e2e" -v

    # All tests
    python3 -m pytest tests/test_orchestrator.py -v
"""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent))

from adaptive.orchestrator.classifier import (
    Difficulty,
    Importance,
    TaskClassifier,
    TaskProfile,
)
from adaptive.orchestrator.dispatcher import (
    Dispatcher,
    DispatchPlan,
    DispatchTarget,
)
from adaptive.orchestrator.engine import OrchestrationEngine, OrchestrationResult
from adaptive.schema import CompletionResponse, TokenUsage


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — Pure logic, no LLM calls
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassifierHeuristic:
    """T1: Heuristic classification without LLM."""

    def test_unit_trivial_task(self):
        c = TaskClassifier()  # No provider → heuristic mode
        profile = c.classify("Fix the whitespace in README.md")
        assert profile.difficulty == Difficulty.TRIVIAL
        assert profile.collaboration_mode == "solo"

    def test_unit_easy_task(self):
        c = TaskClassifier()
        profile = c.classify("Rename the function getUserById to findUser")
        assert profile.difficulty == Difficulty.EASY
        assert profile.collaboration_mode == "solo"

    def test_unit_hard_task(self):
        c = TaskClassifier()
        profile = c.classify("Architect the distributed caching layer for the API")
        assert profile.difficulty == Difficulty.HARD
        assert profile.decomposable is True

    def test_unit_frontier_task(self):
        c = TaskClassifier()
        profile = c.classify("Research and design a novel consensus algorithm")
        assert profile.difficulty == Difficulty.FRONTIER

    def test_unit_critical_importance(self):
        c = TaskClassifier()
        profile = c.classify("The production server is down, fix the crash immediately")
        assert profile.importance == Importance.CRITICAL

    def test_unit_high_importance(self):
        c = TaskClassifier()
        profile = c.classify("Fix the user-facing login bug that blocks release")
        assert profile.importance >= Importance.HIGH

    def test_unit_low_importance(self):
        c = TaskClassifier()
        profile = c.classify("Nice-to-have cleanup of old test files")
        assert profile.importance == Importance.LOW

    def test_unit_task_type_debugging(self):
        c = TaskClassifier()
        profile = c.classify("Debug the memory leak in the worker process")
        assert profile.task_type == "debugging"

    def test_unit_task_type_testing(self):
        c = TaskClassifier()
        profile = c.classify("Write unit tests for the payment module")
        assert profile.task_type == "testing"

    def test_unit_task_type_analysis(self):
        c = TaskClassifier()
        profile = c.classify("Analyze the performance profile of the API endpoints")
        assert profile.task_type == "analysis"


class TestCollaborationMode:
    """T2: Collaboration mode determination."""

    def test_unit_solo_for_easy(self):
        c = TaskClassifier()
        assert c._determine_collaboration(
            Difficulty.EASY, Importance.MEDIUM, False, "code_generation"
        ) == "solo"

    def test_unit_review_for_medium_high(self):
        c = TaskClassifier()
        assert c._determine_collaboration(
            Difficulty.MEDIUM, Importance.HIGH, False, "code_generation"
        ) == "review"

    def test_unit_decompose_for_hard_decomposable(self):
        c = TaskClassifier()
        assert c._determine_collaboration(
            Difficulty.HARD, Importance.MEDIUM, True, "code_generation"
        ) == "decompose"

    def test_unit_review_for_hard_not_decomposable(self):
        c = TaskClassifier()
        assert c._determine_collaboration(
            Difficulty.HARD, Importance.MEDIUM, False, "code_generation"
        ) == "review"

    def test_unit_ensemble_for_frontier_architecture(self):
        c = TaskClassifier()
        assert c._determine_collaboration(
            Difficulty.FRONTIER, Importance.HIGH, False, "architecture"
        ) == "ensemble"

    def test_unit_pipeline_for_frontier_coding(self):
        c = TaskClassifier()
        assert c._determine_collaboration(
            Difficulty.FRONTIER, Importance.HIGH, False, "code_generation"
        ) == "pipeline"


class TestDispatcher:
    """T3: Dispatch table routing logic."""

    def test_unit_critical_goes_to_anthropic(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.MEDIUM,
            importance=Importance.CRITICAL,
            task_type="debugging",
            reasoning="test",
            collaboration_mode="solo",
        )
        plan = d.dispatch("fix critical bug", profile)
        assert plan.primary.provider == "anthropic"

    def test_unit_trivial_goes_to_economy(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.TRIVIAL,
            importance=Importance.LOW,
            task_type="code_generation",
            reasoning="test",
            collaboration_mode="solo",
        )
        plan = d.dispatch("fix typo", profile)
        assert plan.primary.provider == "ollama_cloud"
        assert plan.primary.model in ("gemma4:31b", "devstral-small-2:24b")

    def test_unit_frontier_goes_to_opus(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.FRONTIER,
            importance=Importance.HIGH,
            task_type="architecture",
            reasoning="test",
            collaboration_mode="ensemble",
        )
        plan = d.dispatch("novel architecture", profile)
        assert plan.primary.provider == "anthropic"
        assert plan.primary.model == "claude-opus-4-6"

    def test_unit_medium_coding_goes_to_ollama(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.MEDIUM,
            importance=Importance.MEDIUM,
            task_type="code_generation",
            reasoning="test",
            collaboration_mode="solo",
        )
        plan = d.dispatch("implement feature", profile)
        assert plan.primary.provider == "ollama_cloud"
        assert plan.primary.model == "deepseek-v3.2"

    def test_unit_review_has_cross_provider_reviewer(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.MEDIUM,
            importance=Importance.HIGH,
            task_type="code_generation",
            reasoning="test",
            collaboration_mode="review",
        )
        plan = d.dispatch("important feature", profile)
        assert plan.reviewer is not None
        # Reviewer should be different provider from primary
        assert plan.reviewer.provider != plan.primary.provider

    def test_unit_decompose_has_sub_dispatches(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.HARD,
            importance=Importance.MEDIUM,
            task_type="code_generation",
            reasoning="test",
            decomposable=True,
            sub_tasks=["sub-task 1", "sub-task 2", "sub-task 3"],
            collaboration_mode="decompose",
        )
        plan = d.dispatch("big task", profile)
        assert len(plan.sub_dispatches) == 3
        assert plan.collaboration_mode == "decompose"

    def test_unit_pipeline_has_stages(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.FRONTIER,
            importance=Importance.HIGH,
            task_type="code_generation",
            reasoning="test",
            collaboration_mode="pipeline",
        )
        plan = d.dispatch("frontier coding task", profile)
        assert len(plan.sub_dispatches) == 4  # analyze, implement, review, verify
        assert plan.collaboration_mode == "pipeline"

    def test_unit_ensemble_has_alternatives(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.FRONTIER,
            importance=Importance.HIGH,
            task_type="architecture",
            reasoning="test",
            collaboration_mode="ensemble",
        )
        plan = d.dispatch("design system", profile)
        assert len(plan.sub_dispatches) >= 1  # at least one alternative
        assert plan.reviewer is not None

    def test_unit_dispatch_plan_metadata(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.MEDIUM,
            importance=Importance.HIGH,
            task_type="code_review",
            reasoning="test",
            collaboration_mode="review",
        )
        plan = d.dispatch("review code", profile)
        assert plan.total_models >= 2
        assert len(plan.providers_used) >= 1
        plan_dict = plan.to_dict()
        assert "primary" in plan_dict
        assert "reviewer" in plan_dict


class TestTaskProfile:
    """T4: TaskProfile data structure."""

    def test_unit_score_calculation(self):
        p = TaskProfile(
            difficulty=Difficulty.HARD,
            importance=Importance.CRITICAL,
            task_type="code_generation",
            reasoning="test",
        )
        assert p.score == 16.0  # 4 × 4

    def test_unit_score_minimum(self):
        p = TaskProfile(
            difficulty=Difficulty.TRIVIAL,
            importance=Importance.LOW,
            task_type="documentation",
            reasoning="test",
        )
        assert p.score == 1.0  # 1 × 1

    def test_unit_to_dict(self):
        p = TaskProfile(
            difficulty=Difficulty.MEDIUM,
            importance=Importance.HIGH,
            task_type="debugging",
            reasoning="test reasoning",
            decomposable=True,
            sub_tasks=["a", "b"],
        )
        d = p.to_dict()
        assert d["difficulty"] == "medium"
        assert d["importance"] == "high"
        assert d["task_type"] == "debugging"
        assert d["decomposable"] is True
        assert len(d["sub_tasks"]) == 2


class TestDispatchExplain:
    """T5: Human-readable dispatch explanations."""

    def test_unit_explain_solo(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.EASY,
            importance=Importance.LOW,
            task_type="documentation",
            reasoning="test",
            collaboration_mode="solo",
        )
        plan = d.dispatch("write docs", profile)
        explanation = d.explain(plan)
        assert "Primary" in explanation
        assert "solo" in explanation.lower() or "Solo" in explanation

    def test_unit_explain_review(self):
        d = Dispatcher()
        profile = TaskProfile(
            difficulty=Difficulty.MEDIUM,
            importance=Importance.HIGH,
            task_type="code_generation",
            reasoning="test",
            collaboration_mode="review",
        )
        plan = d.dispatch("build feature", profile)
        explanation = d.explain(plan)
        assert "Reviewer" in explanation


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — Mock LLM, test orchestration logic
# ═══════════════════════════════════════════════════════════════════════════════


class MockProvider:
    """Mock provider that returns canned responses."""
    name = "mock"

    def __init__(self, response_content: str = "mock response"):
        self.response_content = response_content
        self.call_count = 0

    def complete(self, request):
        self.call_count += 1
        return CompletionResponse(
            content=self.response_content,
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            model="mock-model",
            provider="mock",
        )

    def available(self):
        return True

    def supports_tools(self):
        return True

    def supports_streaming(self):
        return False


class TestClassifierLLM:
    """T6: Classifier with mock LLM."""

    def test_integration_classify_with_llm(self):
        mock = MockProvider(json.dumps({
            "difficulty": "hard",
            "importance": "high",
            "task_type": "architecture",
            "reasoning": "Complex distributed system design",
            "estimated_tokens": 8000,
            "needs_tools": True,
            "needs_context": True,
            "decomposable": True,
            "sub_tasks": ["design API", "design storage", "design auth"],
        }))
        c = TaskClassifier(provider=mock, model="test")
        profile = c.classify("Design a distributed caching system")
        assert profile.difficulty == Difficulty.HARD
        assert profile.importance == Importance.HIGH
        assert profile.decomposable is True
        assert len(profile.sub_tasks) == 3

    def test_integration_classify_handles_markdown_fences(self):
        mock = MockProvider('```json\n{"difficulty": "medium", "importance": "medium", "task_type": "code_generation", "reasoning": "test", "estimated_tokens": 4000, "needs_tools": true, "needs_context": true, "decomposable": false}\n```')
        c = TaskClassifier(provider=mock, model="test")
        profile = c.classify("Write a function")
        assert profile.difficulty == Difficulty.MEDIUM

    def test_integration_classify_falls_back_on_error(self):
        mock = MockProvider("not valid json at all")
        c = TaskClassifier(provider=mock, model="test")
        # Should fall back to heuristic
        profile = c.classify("Fix the security vulnerability")
        assert profile.importance == Importance.CRITICAL  # heuristic catches "security"


class TestEngineIntegration:
    """T7-T10: Engine orchestration with mocked providers."""

    def _make_engine_with_mocks(self):
        engine = OrchestrationEngine(verbose=False)
        # Override the router to return mock providers
        mock_ollama = MockProvider("ollama response: task completed")
        mock_anthropic = MockProvider("anthropic response: task reviewed, PASS")
        engine.router._provider_cache["ollama_cloud"] = mock_ollama
        engine.router._provider_cache["anthropic"] = mock_anthropic
        # Pre-init classifier with mock
        engine.classifier = TaskClassifier()  # Heuristic mode
        return engine, mock_ollama, mock_anthropic

    def test_integration_solo_execution(self):
        engine, mock_o, mock_a = self._make_engine_with_mocks()
        result = engine.run("Simple rename task", force_mode="solo")
        assert result.success
        assert result.collaboration_mode == "solo"
        assert len(result.stages) == 1

    def test_integration_review_execution(self):
        engine, mock_o, mock_a = self._make_engine_with_mocks()
        result = engine.run("Important feature", force_mode="review")
        assert result.success
        assert result.collaboration_mode == "review"
        assert len(result.stages) >= 2  # primary + review

    def test_integration_ensemble_execution(self):
        engine, mock_o, mock_a = self._make_engine_with_mocks()
        result = engine.run(
            "Design the architecture",
            force_mode="ensemble",
        )
        assert result.success
        assert result.collaboration_mode == "ensemble"
        assert len(result.models_used) >= 1

    def test_integration_pipeline_execution(self):
        engine, mock_o, mock_a = self._make_engine_with_mocks()
        result = engine.run(
            "Research and design a novel system",
            force_mode="pipeline",
        )
        assert result.success
        assert result.collaboration_mode == "pipeline"
        assert len(result.stages) >= 4  # 4 pipeline stages + synthesis

    def test_integration_result_metadata(self):
        engine, mock_o, mock_a = self._make_engine_with_mocks()
        result = engine.run("Build a function", force_mode="solo")
        d = result.to_dict()
        assert "collaboration_mode" in d
        assert "models_used" in d
        assert "total_tokens" in d
        assert d["success"] is True

    def test_integration_classify_only(self):
        engine, _, _ = self._make_engine_with_mocks()
        profile = engine.classify_only("Fix the production crash")
        assert profile.importance == Importance.CRITICAL

    def test_integration_plan_only(self):
        engine, _, _ = self._make_engine_with_mocks()
        plan = engine.plan_only("Fix the production crash")
        assert plan.primary.provider == "anthropic"  # critical → anthropic


# ═══════════════════════════════════════════════════════════════════════════════
# E2E TESTS — Real LLM calls (skip if no API keys)
# ═══════════════════════════════════════════════════════════════════════════════

def _has_ollama_key():
    """Check if Ollama Cloud is available."""
    try:
        from adaptive.config import AdaptiveConfig
        config = AdaptiveConfig()
        key = config.load_api_key("ollama_cloud")
        return bool(key)
    except Exception:
        return False


def _has_anthropic_key():
    """Check if Anthropic is available."""
    try:
        from adaptive.config import AdaptiveConfig
        config = AdaptiveConfig()
        key = config.load_api_key("anthropic")
        return bool(key)
    except Exception:
        return False


skip_no_ollama = pytest.mark.skipif(
    not _has_ollama_key(), reason="No OLLAMA_API_KEY"
)
skip_no_anthropic = pytest.mark.skipif(
    not _has_anthropic_key(), reason="No ANTHROPIC_API_KEY"
)


class TestE2EClassification:
    """T11: Real GLM 5.1 classification."""

    @skip_no_ollama
    def test_e2e_classify_simple_task(self):
        engine = OrchestrationEngine()
        profile = engine.classify_only("Add a docstring to the main function")
        assert profile.difficulty in (Difficulty.TRIVIAL, Difficulty.EASY)
        assert profile.task_type in ("documentation", "code_generation")

    @skip_no_ollama
    def test_e2e_classify_complex_task(self):
        engine = OrchestrationEngine()
        profile = engine.classify_only(
            "Redesign the entire authentication system to support OAuth2, "
            "SAML, and passwordless login, with migration path for existing users"
        )
        assert profile.difficulty >= Difficulty.HARD
        assert profile.decomposable is True

    @skip_no_ollama
    def test_e2e_classify_critical_task(self):
        engine = OrchestrationEngine()
        profile = engine.classify_only(
            "Production database is corrupted, users are losing data, fix immediately"
        )
        assert profile.importance == Importance.CRITICAL


class TestE2ESolo:
    """T12: Real solo execution."""

    @skip_no_ollama
    def test_e2e_solo_ollama(self):
        engine = OrchestrationEngine()
        result = engine.run(
            "What is 2 + 2? Reply with just the number.",
            force_mode="solo",
            force_provider="ollama_cloud",
        )
        assert result.success
        assert "4" in result.content
        assert "ollama_cloud" in result.providers_used

    @skip_no_anthropic
    def test_e2e_solo_anthropic(self):
        engine = OrchestrationEngine()
        result = engine.run(
            "What is 3 + 3? Reply with just the number.",
            force_mode="solo",
            force_provider="anthropic",
        )
        assert result.success
        assert "6" in result.content
        assert "anthropic" in result.providers_used


class TestE2EReview:
    """T13: Real cross-provider review."""

    @skip_no_ollama
    @skip_no_anthropic
    def test_e2e_review_cross_provider(self):
        engine = OrchestrationEngine(verbose=True)
        result = engine.run(
            "Write a Python function that checks if a string is a valid email address. "
            "Use regex. Include type hints.",
            force_mode="review",
        )
        assert result.success
        assert result.collaboration_mode == "review"
        assert len(result.stages) >= 2
        assert len(result.providers_used) >= 1
        # Should contain actual Python code
        assert "def " in result.content or "import" in result.content


class TestE2EEnsemble:
    """T14: Real ensemble execution."""

    @skip_no_ollama
    @skip_no_anthropic
    def test_e2e_ensemble_multi_provider(self):
        engine = OrchestrationEngine(verbose=True)
        result = engine.run(
            "Explain the CAP theorem in exactly 3 sentences.",
            force_mode="ensemble",
        )
        assert result.success
        assert result.collaboration_mode == "ensemble"
        assert len(result.providers_used) >= 2


class TestE2EPipeline:
    """T15: Real pipeline execution."""

    @skip_no_ollama
    @skip_no_anthropic
    def test_e2e_pipeline_full(self):
        engine = OrchestrationEngine(verbose=True)
        result = engine.run(
            "Write a Python function to find the longest palindromic substring. "
            "Include tests.",
            force_mode="pipeline",
        )
        assert result.success
        assert result.collaboration_mode == "pipeline"
        assert len(result.stages) >= 4


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
