"""Tests for AgentRunner.stream — agent-loop streaming with per-iteration events."""

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


class _ScriptedStreamProvider(Provider):
    """Yields a scripted sequence of StreamEvents per call, one script per iteration."""
    name = "fake_scripted"

    def __init__(self, scripts: list[list[StreamEvent]]):
        self.scripts = scripts
        self.call_count = 0
        self.requests: list[CompletionRequest] = []

    def available(self): return True
    def supports_tools(self): return True
    def supports_streaming(self): return True

    def complete(self, request):
        raise AssertionError("complete() should not be called when streaming")

    def stream(self, request):
        self.requests.append(request)
        script = self.scripts[self.call_count]
        self.call_count += 1
        yield from script


class _FakeRouter:
    def __init__(self, provider: Provider, model: str = "test-model"):
        self.provider = provider
        self.model = model

    def route(self, role: str, preferred_provider=None):
        return self.provider, self.model


def _runner(provider: Provider, *, max_iterations: int = 5) -> AgentRunner:
    return AgentRunner(
        router=_FakeRouter(provider),
        config=AdaptiveConfig(),
        max_iterations=max_iterations,
        inject_context=False,
    )


def _iter0_text_only_done():
    return [
        StreamEvent(type="text", text="hi"),
        StreamEvent(type="done", usage=TokenUsage(input_tokens=3, output_tokens=1),
                    model="m1", stop_reason="end_turn"),
    ]


def _iter0_tool_call(tool_id="tu_1", name="bash", args=None):
    args = args or {"command": "echo ok"}
    return [
        StreamEvent(type="text", text="running "),
        StreamEvent(type="tool_call", tool_call=ToolCall(id=tool_id, name=name, arguments=args)),
        StreamEvent(type="done", usage=TokenUsage(input_tokens=5, output_tokens=2),
                    model="m1", stop_reason="tool_use"),
    ]


def _iter1_final_text():
    return [
        StreamEvent(type="text", text="done"),
        StreamEvent(type="done", usage=TokenUsage(input_tokens=8, output_tokens=1),
                    model="m1", stop_reason="end_turn"),
    ]


def test_agent_stream_single_iteration_no_tools():
    """One turn, no tool calls — expect iteration_start, text, iteration_end, run_done."""
    provider = _ScriptedStreamProvider([_iter0_text_only_done()])
    runner = _runner(provider)

    events = list(runner.stream(prompt="hi", role="builder"))
    types = [e.type for e in events]
    assert types == ["iteration_start", "text", "iteration_end", "run_done"]

    assert events[0].iteration == 0
    assert events[2].iteration == 0
    rd = events[-1]
    assert rd.stop_reason == "end_turn"
    assert rd.model == "m1"
    assert rd.usage is not None and rd.usage.input_tokens == 3 and rd.usage.output_tokens == 1
    assert runner.total_usage.input_tokens == 3
    assert runner.total_usage.output_tokens == 1


def test_agent_stream_tool_loop_two_iterations():
    """Iter 0 returns a tool_call; runner executes it; iter 1 returns text and ends."""
    provider = _ScriptedStreamProvider([
        _iter0_tool_call(tool_id="tu_1", name="bash", args={"command": "echo hello"}),
        _iter1_final_text(),
    ])
    runner = _runner(provider)

    events = list(runner.stream(prompt="run echo", role="builder"))
    types = [e.type for e in events]

    # Expected envelope (one full round-trip + final turn):
    # iteration_start(0), text, tool_call, tool_executing, tool_result, iteration_end(0),
    # iteration_start(1), text, iteration_end(1), run_done
    assert types == [
        "iteration_start", "text", "tool_call",
        "tool_executing", "tool_result", "iteration_end",
        "iteration_start", "text", "iteration_end", "run_done",
    ]

    # Iteration indices
    assert events[0].iteration == 0
    assert events[5].iteration == 0
    assert events[6].iteration == 1
    assert events[8].iteration == 1

    # Tool events carry ids/content
    tool_exec = events[3]
    assert tool_exec.tool_call is not None
    assert tool_exec.tool_call.id == "tu_1"
    assert tool_exec.tool_call.name == "bash"

    tool_result = events[4]
    assert tool_result.tool_result_id == "tu_1"
    # bash echo executes for real — result should contain "hello"
    assert "hello" in tool_result.tool_result_content

    # Usage cumulated across both iterations
    rd = events[-1]
    assert rd.usage is not None
    assert rd.usage.input_tokens == 5 + 8
    assert rd.usage.output_tokens == 2 + 1
    assert rd.stop_reason == "end_turn"  # from the terminal iteration

    # Second provider call saw the assistant turn + tool_result appended
    second_req = provider.requests[1]
    msg_roles = [m.role for m in second_req.messages]
    assert msg_roles == ["user", "assistant", "tool_result"]
    assert second_req.messages[1].tool_calls is not None
    assert second_req.messages[1].tool_calls[0].id == "tu_1"
    assert second_req.messages[2].tool_call_id == "tu_1"


def test_agent_stream_falls_back_for_non_streaming_provider():
    """Provider.supports_streaming()==False — runner synthesizes events from complete()."""

    class _BlockingProvider(Provider):
        name = "blocking"
        def available(self): return True
        def supports_tools(self): return True
        def supports_streaming(self): return False
        def complete(self, request):
            return CompletionResponse(
                content="synthetic answer",
                tool_calls=[],
                usage=TokenUsage(input_tokens=2, output_tokens=1),
                model="m-blocking",
                provider=self.name,
                stop_reason="end_turn",
            )

    runner = _runner(_BlockingProvider())
    events = list(runner.stream(prompt="hi", role="builder"))
    types = [e.type for e in events]
    assert types == ["iteration_start", "text", "iteration_end", "run_done"]
    assert events[1].text == "synthetic answer"
    assert events[-1].stop_reason == "end_turn"
    assert runner.total_usage.input_tokens == 2


def test_agent_stream_hits_max_iterations():
    """If every iteration returns a tool_call, run ends with max_iterations stop_reason."""
    # 3 iterations, each returns a tool call — runner caps at max_iterations=2
    always_tool = lambda i: [
        StreamEvent(type="tool_call", tool_call=ToolCall(id=f"tu_{i}", name="bash", arguments={"command": "true"})),
        StreamEvent(type="done", usage=TokenUsage(input_tokens=1, output_tokens=1),
                    model="m1", stop_reason="tool_use"),
    ]
    provider = _ScriptedStreamProvider([always_tool(0), always_tool(1)])
    runner = _runner(provider, max_iterations=2)

    events = list(runner.stream(prompt="loop", role="builder"))
    types = [e.type for e in events]

    # Two full tool-use iterations, then run_done with max_iterations
    assert types[-1] == "run_done"
    assert events[-1].stop_reason == "max_iterations"
    assert types.count("iteration_start") == 2
    assert types.count("iteration_end") == 2
    assert types.count("tool_executing") == 2
