"""Tests for AgentRunner.stream_single_shot — streaming + non-streaming fallback."""

import sys
from collections.abc import Iterator
from pathlib import Path

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE))

from adaptive.config import AdaptiveConfig
from adaptive.provider import Provider
from adaptive.runner import AgentRunner
from adaptive.schema import (
    CompletionRequest,
    CompletionResponse,
    StreamEvent,
    TokenUsage,
    ToolCall,
)


class _FakeStreamingProvider(Provider):
    name = "fake_stream"

    def __init__(self):
        self.last_request: CompletionRequest | None = None

    def available(self):
        return True

    def supports_tools(self):
        return True

    def supports_streaming(self):
        return True

    def complete(self, request):
        raise AssertionError("complete() should not be called when streaming")

    def stream(self, request):
        self.last_request = request
        yield StreamEvent(type="text", text="hello ")
        yield StreamEvent(type="text", text="world")
        yield StreamEvent(
            type="done",
            usage=TokenUsage(input_tokens=12, output_tokens=4),
            model="fake-model-1",
            stop_reason="end_turn",
        )


class _FakeNonStreamingProvider(Provider):
    name = "fake_blocking"

    def available(self):
        return True

    def supports_tools(self):
        return True

    def supports_streaming(self):
        return False

    def complete(self, request):
        return CompletionResponse(
            content="full answer",
            tool_calls=[],
            usage=TokenUsage(input_tokens=7, output_tokens=3),
            model="fake-block",
            provider=self.name,
            stop_reason="end_turn",
        )


class _LyingProvider(Provider):
    """Claims streaming but raises NotImplementedError when asked."""
    name = "fake_liar"

    def available(self):
        return True

    def supports_tools(self):
        return True

    def supports_streaming(self):
        return True

    def complete(self, request):
        return CompletionResponse(
            content="fallback content",
            tool_calls=[],
            usage=TokenUsage(input_tokens=5, output_tokens=2),
            model="fake-liar",
            provider=self.name,
            stop_reason="end_turn",
        )

    def stream(self, request):
        raise NotImplementedError("nope")


class _FakeRouter:
    """Returns a fixed provider/model regardless of role."""
    def __init__(self, provider: Provider, model: str = "test-model"):
        self.provider = provider
        self.model = model

    def route(self, role: str, preferred_provider=None):
        return self.provider, self.model


def _make_runner(provider: Provider) -> AgentRunner:
    config = AdaptiveConfig()
    router = _FakeRouter(provider)
    return AgentRunner(router=router, config=config, inject_context=False)


def test_stream_single_shot_yields_provider_events():
    provider = _FakeStreamingProvider()
    runner = _make_runner(provider)

    events = list(runner.stream_single_shot(prompt="hi", role="builder"))

    # Text deltas come through unchanged
    texts = [e.text for e in events if e.type == "text"]
    assert texts == ["hello ", "world"]

    # Done event is emitted with usage/model/stop_reason
    done = [e for e in events if e.type == "done"]
    assert len(done) == 1
    assert done[0].model == "fake-model-1"
    assert done[0].stop_reason == "end_turn"

    # Runner records usage from the done event
    assert runner.total_usage.input_tokens == 12
    assert runner.total_usage.output_tokens == 4

    # Request was built correctly: no tools, prompt as user message
    assert provider.last_request is not None
    assert provider.last_request.tools is None
    assert provider.last_request.messages[0].role == "user"
    assert provider.last_request.messages[0].content == "hi"


def test_stream_single_shot_falls_back_for_non_streaming_provider():
    provider = _FakeNonStreamingProvider()
    runner = _make_runner(provider)

    events = list(runner.stream_single_shot(prompt="hi", role="builder"))

    # Caller sees the same shape: one text event + one done event
    types = [e.type for e in events]
    assert types == ["text", "done"]
    assert events[0].text == "full answer"
    assert events[1].usage is not None
    assert events[1].usage.input_tokens == 7
    assert events[1].usage.output_tokens == 3
    assert events[1].stop_reason == "end_turn"

    # Usage is recorded from the synthesized done event
    assert runner.total_usage.input_tokens == 7
    assert runner.total_usage.output_tokens == 3


def test_stream_single_shot_falls_back_when_provider_lies_about_streaming():
    """Provider claims supports_streaming() but raises NotImplementedError.
    Runner should silently fall through to complete()."""
    provider = _LyingProvider()
    runner = _make_runner(provider)

    events = list(runner.stream_single_shot(prompt="hi", role="builder"))
    types = [e.type for e in events]
    assert types == ["text", "done"]
    assert events[0].text == "fallback content"
    assert runner.total_usage.input_tokens == 5
    assert runner.total_usage.output_tokens == 2
