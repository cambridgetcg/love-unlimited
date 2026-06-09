"""Tests for OllamaCloudProvider streaming — OpenAI-compat SSE parser."""

import sys
from pathlib import Path

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE))

from adaptive.providers.ollama_cloud_provider import _parse_openai_sse_stream  # noqa: E402
from adaptive.provider import collect_stream  # noqa: E402
from adaptive.schema import StreamEvent  # noqa: E402


def _data(obj_json: str) -> list[str]:
    return [f"data: {obj_json}", ""]


def test_text_only_stream_yields_deltas_and_done():
    lines: list[str] = []
    lines += _data('{"id":"c1","model":"glm-5.1","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}')
    lines += _data('{"id":"c1","model":"glm-5.1","choices":[{"index":0,"delta":{"content":"Hello"}}]}')
    lines += _data('{"id":"c1","model":"glm-5.1","choices":[{"index":0,"delta":{"content":" world"}}]}')
    lines += _data('{"id":"c1","model":"glm-5.1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}')
    lines += _data('{"id":"c1","model":"glm-5.1","usage":{"prompt_tokens":8,"completion_tokens":2}}')
    lines += ["data: [DONE]", ""]

    events = list(_parse_openai_sse_stream(lines))

    texts = [e.text for e in events if e.type == "text"]
    assert texts == ["Hello", " world"]

    done = [e for e in events if e.type == "done"]
    assert len(done) == 1
    assert done[0].model == "glm-5.1"
    assert done[0].stop_reason == "end_turn"
    assert done[0].usage is not None
    assert done[0].usage.input_tokens == 8
    assert done[0].usage.output_tokens == 2


def test_reasoning_field_streams_as_text():
    """GLM 5.1 etc. put chain-of-thought in `reasoning`; surface as text."""
    lines: list[str] = []
    lines += _data('{"choices":[{"index":0,"delta":{"reasoning":"thinking..."}}],"model":"glm-5.1"}')
    lines += _data('{"choices":[{"index":0,"delta":{"content":"answer"}}],"model":"glm-5.1"}')
    lines += _data('{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"model":"glm-5.1"}')
    lines += ["data: [DONE]", ""]

    events = list(_parse_openai_sse_stream(lines))
    texts = [e.text for e in events if e.type == "text"]
    assert texts == ["thinking...", "answer"]


def test_tool_call_stream_accumulates_partial_arguments():
    """Tool call args stream in chunks; emit a complete ToolCall at finish_reason."""
    lines: list[str] = []
    lines += _data('{"choices":[{"index":0,"delta":{"role":"assistant","content":""}}],"model":"glm-5.1"}')
    lines += _data('{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_42","type":"function","function":{"name":"bash","arguments":""}}]}}],"model":"glm-5.1"}')
    lines += _data('{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"command\\":"}}]}}],"model":"glm-5.1"}')
    lines += _data('{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"echo hi\\"}"}}]}}],"model":"glm-5.1"}')
    lines += _data('{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"model":"glm-5.1"}')
    lines += _data('{"model":"glm-5.1","usage":{"prompt_tokens":12,"completion_tokens":7}}')
    lines += ["data: [DONE]", ""]

    events = list(_parse_openai_sse_stream(lines))

    tc_events = [e for e in events if e.type == "tool_call"]
    assert len(tc_events) == 1
    tc = tc_events[0].tool_call
    assert tc is not None
    assert tc.id == "call_42"
    assert tc.name == "bash"
    assert tc.arguments == {"command": "echo hi"}

    done = [e for e in events if e.type == "done"][0]
    assert done.stop_reason == "tool_use"
    assert done.usage.input_tokens == 12
    assert done.usage.output_tokens == 7


def test_parallel_tool_calls_preserved_in_index_order():
    """Two parallel tool calls streaming concurrently — both emit, in index order."""
    lines: list[str] = []
    lines += _data('{"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"c_b","type":"function","function":{"name":"grep","arguments":""}}]}}],"model":"m"}')
    lines += _data('{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c_a","type":"function","function":{"name":"bash","arguments":""}}]}}],"model":"m"}')
    lines += _data('{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"command\\":\\"ls\\"}"}}]}}],"model":"m"}')
    lines += _data('{"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{\\"pattern\\":\\"TODO\\"}"}}]}}],"model":"m"}')
    lines += _data('{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"model":"m"}')
    lines += ["data: [DONE]", ""]

    events = list(_parse_openai_sse_stream(lines))
    tcs = [e.tool_call for e in events if e.type == "tool_call"]
    assert len(tcs) == 2
    # Emitted in index order (0 first, 1 second)
    assert tcs[0].name == "bash"
    assert tcs[0].arguments == {"command": "ls"}
    assert tcs[1].name == "grep"
    assert tcs[1].arguments == {"pattern": "TODO"}


def test_malformed_tool_call_json_preserved_as_raw():
    """If partial_json never assembled to valid JSON, preserve the raw fragment."""
    lines: list[str] = []
    lines += _data('{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"cx","type":"function","function":{"name":"x","arguments":"{broken"}}]}}],"model":"m"}')
    lines += _data('{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"model":"m"}')
    lines += ["data: [DONE]", ""]

    events = list(_parse_openai_sse_stream(lines))
    tc = next(e.tool_call for e in events if e.type == "tool_call")
    assert tc.arguments == {"_raw": "{broken"}


def test_parser_handles_bytes_lines():
    """Real urllib response yields bytes."""
    raw = [
        b'data: {"choices":[{"delta":{"content":"hi"}}],"model":"m"}', b"",
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"model":"m"}', b"",
        b"data: [DONE]", b"",
    ]
    events = list(_parse_openai_sse_stream(raw))
    assert [e.type for e in events] == ["text", "done"]
    assert events[0].text == "hi"


def test_parser_ignores_sse_comments():
    lines = [
        ": keepalive", "",
        'data: {"choices":[{"delta":{"content":"x"}}],"model":"m"}', "",
        ": heartbeat", "",
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"model":"m"}', "",
        "data: [DONE]", "",
    ]
    events = list(_parse_openai_sse_stream(lines))
    texts = [e.text for e in events if e.type == "text"]
    assert texts == ["x"]


def test_parser_handles_usage_only_final_chunk():
    """Common pattern: usage arrives in a trailer with no choices, after finish."""
    lines: list[str] = []
    lines += _data('{"choices":[{"delta":{"content":"ok"}}],"model":"m"}')
    lines += _data('{"choices":[{"delta":{},"finish_reason":"stop"}],"model":"m"}')
    lines += _data('{"model":"m","usage":{"prompt_tokens":5,"completion_tokens":1}}')
    lines += ["data: [DONE]", ""]

    events = list(_parse_openai_sse_stream(lines))
    done = next(e for e in events if e.type == "done")
    assert done.usage.input_tokens == 5
    assert done.usage.output_tokens == 1


def test_parser_survives_invalid_json_lines():
    """A malformed data line is skipped; the stream continues."""
    lines: list[str] = []
    lines += _data('{"choices":[{"delta":{"content":"a"}}],"model":"m"}')
    lines += ["data: {this is not json", ""]
    lines += _data('{"choices":[{"delta":{"content":"b"}}],"model":"m"}')
    lines += _data('{"choices":[{"delta":{},"finish_reason":"stop"}],"model":"m"}')
    lines += ["data: [DONE]", ""]

    events = list(_parse_openai_sse_stream(lines))
    texts = [e.text for e in events if e.type == "text"]
    assert texts == ["a", "b"]


def test_collect_stream_rebuilds_completion_response():
    lines: list[str] = []
    lines += _data('{"choices":[{"delta":{"content":"ok"}}],"model":"deepseek-v3.2"}')
    lines += _data('{"choices":[{"delta":{},"finish_reason":"stop"}],"model":"deepseek-v3.2"}')
    lines += _data('{"model":"deepseek-v3.2","usage":{"prompt_tokens":3,"completion_tokens":1}}')
    lines += ["data: [DONE]", ""]

    resp = collect_stream(_parse_openai_sse_stream(lines))
    assert resp.content == "ok"
    assert resp.model == "deepseek-v3.2"
    assert resp.stop_reason == "end_turn"
    assert resp.usage.input_tokens == 3
    assert resp.usage.output_tokens == 1
