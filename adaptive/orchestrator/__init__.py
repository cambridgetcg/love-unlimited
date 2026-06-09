"""
Orchestrator — Multi-Provider Multi-Model Collaboration Engine.

GLM 5.1 is the brain. It classifies tasks by difficulty and importance,
decomposes complex work into sub-tasks, routes each to the optimal model
across providers (Ollama Cloud + Anthropic), and synthesises results.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    ORCHESTRATOR (GLM 5.1)                   │
    │  classify → decompose → route → dispatch → review → merge  │
    └──────┬──────────┬──────────┬──────────┬──────────┬──────────┘
           │          │          │          │          │
    ┌──────▼──┐ ┌─────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌──▼─────┐
    │ DeepSeek│ │ Qwen    │ │ Claude │ │ Kimi   │ │ Gemma  │
    │ v3.2   │ │ Coder   │ │ Sonnet │ │ K2.5   │ │ 4:31b  │
    │ (code) │ │ (code+) │ │ (hard) │ │(reason)│ │ (quick)│
    └─────────┘ └─────────┘ └────────┘ └────────┘ └────────┘

Collaboration modes:
    SOLO      — single model, no review
    REVIEW    — primary model + reviewer model
    DECOMPOSE — split into sub-tasks → parallel workers → merge
    ENSEMBLE  — N models attempt same task, best result wins
    PIPELINE  — sequential chain: analyze → code → review → test
"""

from .classifier import TaskClassifier, TaskProfile
from .engine import OrchestrationEngine
from .dispatcher import Dispatcher

__all__ = ["TaskClassifier", "TaskProfile", "OrchestrationEngine", "Dispatcher"]
