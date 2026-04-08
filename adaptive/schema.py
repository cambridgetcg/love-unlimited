"""
Schema — Model-independent data structures for the adaptive layer.

Every provider translates to/from these types. No provider-specific
formats leak past the adapter boundary.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """A single message in a conversation."""
    role: str  # "system", "user", "assistant", "tool_result"
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # For tool_result messages
    name: str | None = None  # Tool name for tool_result messages


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolDefinition:
    """A tool the model can invoke."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass
class CompletionRequest:
    """Provider-agnostic completion request."""
    messages: list[Message]
    tools: list[ToolDefinition] | None = None
    model: str | None = None  # Override — usually set by router
    max_tokens: int = 4096
    temperature: float = 0.0
    effort: str = "medium"  # "low", "medium", "high"
    stop_sequences: list[str] | None = None
    system: str | None = None  # Top-level system prompt (some providers separate this)


@dataclass
class TokenUsage:
    """Token consumption for a completion."""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class CompletionResponse:
    """Provider-agnostic completion response."""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    provider: str = ""
    stop_reason: str = "end_turn"  # "end_turn", "tool_use", "max_tokens"

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ── Role definitions ─────────────────────────────────────────────────────────
# Roles map to capability tiers, not specific models.

ROLES = {
    "coordinator": {
        "description": "Strategic sensing and decision-making",
        "effort": "high",
        "max_tokens": 4096,
        "tier": "premium",
    },
    "consultant": {
        "description": "Expert analysis for hard problems",
        "effort": "high",
        "max_tokens": 8192,
        "tier": "premium",
    },
    "builder": {
        "description": "Code generation and routine automation",
        "effort": "medium",
        "max_tokens": 8192,
        "tier": "standard",
    },
    "monitor": {
        "description": "Lightweight status checks and verification",
        "effort": "low",
        "max_tokens": 2048,
        "tier": "economy",
    },
    "quick_check": {
        "description": "Fast one-shot verification",
        "effort": "low",
        "max_tokens": 1024,
        "tier": "economy",
    },
}
