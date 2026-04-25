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
    effort: str = "medium"  # "low", "medium", "high" — Anthropic-style
    stop_sequences: list[str] | None = None
    system: str | None = None  # Top-level system prompt (some providers separate this)
    # Ollama/OpenAI reasoning control — "none", "low", "medium", "high".
    # "none" disables the reasoning/CoT stage entirely. For GLM 5.1 and
    # DeepSeek v3.2, this delivers 3-7× latency reduction on deterministic
    # tasks (builder/coder/monitor roles) with equal or better output
    # quality. Unset means "provider default".
    reasoning_effort: str | None = None


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


@dataclass
class StreamEvent:
    """A single event from a streaming completion or agent run.

    Provider-level types (from AnthropicProvider.stream, etc.):
      "text"           — incremental text delta (cumulate `text` to get full content)
      "tool_call"      — a complete tool call, emitted once its JSON input is assembled
      "done"           — terminal event for a single completion; carries usage/model/stop_reason

    Agent-loop types (from AgentRunner.stream):
      "iteration_start" — a new turn in the agent loop is beginning; `iteration` is 0-indexed
      "tool_executing"  — a tool is about to run; `tool_call` identifies it
      "tool_result"     — a tool finished; `tool_result_id`/`tool_result_content` carry the output
      "iteration_end"   — turn complete (post tool execution, before the next model call)
      "run_done"        — the whole agent run is finished; cumulative usage/model/stop_reason

    Middleware types (injected by StreamMiddleware):
      "halt"            — stream terminated early (cost, truth drift, safety); `stop_reason`
                          carries the cause. Upstream is closed; consumers should treat
                          halt as terminal (no further events follow).
    """
    type: str
    text: str = ""
    tool_call: ToolCall | None = None
    usage: TokenUsage | None = None
    model: str = ""
    stop_reason: str = ""
    iteration: int | None = None
    tool_result_id: str | None = None
    tool_result_content: str = ""


# ── Role definitions ─────────────────────────────────────────────────────────
# Roles map to capability tiers, not specific models.
#
# reasoning_effort defaults (measured 2026-04-09 vs ollama.com/v1):
#   glm-5.1 effort=none    → 0.99s (vs 3.7s default)  — 3.7× faster
#   deepseek-v3.2 effort=none → 3.18s (vs ~20s default) — 6.3× faster
#   Quality on deterministic coding tasks is EQUAL OR BETTER at effort=none.
#
# Use effort=none for deterministic well-specified tasks (coding, monitoring,
# quick checks). Reserve effort=low/high for genuine planning or open-ended
# analysis where chain-of-thought helps.

ROLES = {
    "coordinator": {
        "description": "Strategic sensing and decision-making",
        "effort": "high",
        "max_tokens": 8192,       # GLM 5.1 reasoning needs room
        "tier": "premium",
        "reasoning_effort": "low",  # coordinator plans — light CoT helps
        "preferred_models": {     # per-provider model overrides
            "ollama_cloud": "glm-5.1",
        },
    },
    "consultant": {
        "description": "Expert analysis for hard problems",
        "effort": "high",
        "max_tokens": 8192,
        "tier": "premium",
        "reasoning_effort": "high",  # hard problems — full CoT
        "preferred_models": {
            "ollama_cloud": "kimi-k2.5",  # massive context for analysis
        },
    },
    "builder": {
        "description": "Code generation and routine automation",
        "effort": "medium",
        "max_tokens": 8192,
        "tier": "standard",
        "reasoning_effort": "none",  # deterministic coding — reasoning is pure overhead
        "preferred_models": {
            "ollama_cloud": "deepseek-v3.2",  # top coding benchmarks
        },
    },
    "coder": {
        "description": "Pure code generation specialist",
        "effort": "medium",
        "max_tokens": 8192,
        "tier": "standard",
        "reasoning_effort": "none",  # pure code gen — no reasoning needed
        "preferred_models": {
            "ollama_cloud": "qwen3-coder:480b",
        },
    },
    "analyst": {
        "description": "Market analysis, prediction, reasoning",
        "effort": "high",
        "max_tokens": 8192,
        "tier": "premium",
        "reasoning_effort": "high",  # market reasoning genuinely benefits from CoT
        "preferred_models": {
            "ollama_cloud": "cogito-2.1:671b",
        },
    },
    "monitor": {
        "description": "Lightweight status checks and verification",
        "effort": "low",
        "max_tokens": 1024,       # small — effort=none removes reasoning budget need
        "tier": "economy",
        "reasoning_effort": "none",  # status checks don't reason
        "preferred_models": {
            "ollama_cloud": "devstral-small-2:24b",
        },
    },
    "quick_check": {
        "description": "Fast one-shot verification",
        "effort": "low",
        "max_tokens": 1024,       # small — effort=none removes reasoning budget need
        "tier": "economy",
        "reasoning_effort": "none",  # one-shot verification — fastest path
        "preferred_models": {
            # ministral-3:3b: smallest instruction-following model on Ollama
            # Max. Consistent 0.92s latency measured 2026-04-09 across
            # repeated calls. gemma4:31b was the original choice but tested
            # at 13-70s with poor instruction following — swapped out.
            "ollama_cloud": "ministral-3:3b",
        },
    },
}
