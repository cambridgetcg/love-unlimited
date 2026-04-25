"""
Provider — Abstract base class for LLM providers.

Each provider translates between the universal schema and its own API format.
No external dependencies — uses urllib for HTTP.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterator

from .schema import CompletionRequest, CompletionResponse, StreamEvent, TokenUsage


class Provider(ABC):
    """Abstract LLM provider. Subclass for each backend."""

    name: str = "base"

    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send a completion request, return a normalized response."""
        ...

    @abstractmethod
    def supports_tools(self) -> bool:
        """Whether this provider supports native tool/function calling."""
        ...

    @abstractmethod
    def supports_streaming(self) -> bool:
        """Whether this provider supports streaming responses."""
        ...

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider is currently reachable (has API key, server up, etc)."""
        ...

    def stream(self, request: CompletionRequest) -> Iterator[StreamEvent]:
        """Send a completion request, yield StreamEvents as they arrive.

        Providers whose `supports_streaming()` returns True must override this.
        Default implementation rejects — don't silently fall back to non-streaming.
        """
        raise NotImplementedError(f"{self.name} provider does not implement streaming")

    def effort_to_params(self, effort: str) -> dict:
        """Map effort level to provider-specific parameters.

        Override per provider. Default: adjust temperature and max_tokens.
        """
        return {
            "low": {"temperature": 0.0},
            "medium": {"temperature": 0.0},
            "high": {"temperature": 0.0},
        }.get(effort, {})


def collect_stream(events: Iterator[StreamEvent]) -> CompletionResponse:
    """Consume a stream of events, return a final CompletionResponse.

    Works over both provider-level streams (terminal: 'done') and agent-loop
    streams (terminal: 'run_done'). A 'halt' event is treated as terminal too
    and propagates its stop_reason (e.g. 'cost_limit', 'mode_two_drift').

    Text and tool_call events are accumulated across all iterations — callers
    that want only the last iteration's text should filter the event stream
    before calling this.

    Non-terminal framing events (iteration_start/end, tool_executing, tool_result)
    are ignored; they exist for live observation, not final shape.
    """
    text_parts: list[str] = []
    tool_calls = []
    usage = TokenUsage()
    model = ""
    stop_reason = "end_turn"

    for ev in events:
        if ev.type == "text":
            text_parts.append(ev.text)
        elif ev.type == "tool_call" and ev.tool_call is not None:
            tool_calls.append(ev.tool_call)
        elif ev.type in ("done", "run_done"):
            if ev.usage is not None:
                usage = ev.usage
            if ev.model:
                model = ev.model
            if ev.stop_reason:
                stop_reason = ev.stop_reason
        elif ev.type == "halt":
            if ev.stop_reason:
                stop_reason = ev.stop_reason

    return CompletionResponse(
        content="".join(text_parts),
        tool_calls=tool_calls,
        usage=usage,
        model=model,
        provider="",
        stop_reason=stop_reason,
    )
