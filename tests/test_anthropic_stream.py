"""Tests for AnthropicProvider streaming — SSE parser + collect_stream."""

import sys
from pathlib import Path

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE))

from adaptive.providers.anthropic_provider import _parse_sse_stream  # noqa: E402
from adaptive.provider import collect_stream  # noqa: E402
from adaptive.schema import StreamEvent  # noqa: E402


def _sse_event(name: str, data: str) -> list[str]:
    return [f"event: {name}", f"data: {data}", ""]


def test_text_only_stream_yields_deltas_and_done():
    lines: list[str] = []
    lines += _sse_event("message_start",
        '{"type":"message_start","message":{"id":"m1","model":"claude-opus-4-7",'
        '"usage":{"input_tokens":10,"output_tokens":0}}}')
    lines += _sse_event("content_block_start",
        '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}')
    lines += _sse_event("content_block_delta",
        '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}')
    lines += _sse_event("content_block_delta",
        '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":", world"}}')
    lines += _sse_event("content_block_stop", '{"type":"content_block_stop","index":0}')
    lines += _sse_event("message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}')
    lines += _sse_event("message_stop", '{"type":"message_stop"}')

    events = list(_parse_sse_stream(lines))

    texts = [e.text for e in events if e.type == "text"]
    assert texts == ["Hello", ", world"]

    done = [e for e in events if e.type == "done"]
    assert len(done) == 1
    assert done[0].model == "claude-opus-4-7"
    assert done[0].stop_reason == "end_turn"
    assert done[0].usage is not None
    assert done[0].usage.input_tokens == 10
    assert done[0].usage.output_tokens == 3


def test_tool_use_stream_accumulates_partial_json():
    lines: list[str] = []
    lines += _sse_event("message_start",
        '{"type":"message_start","message":{"id":"m1","model":"claude-opus-4-7",'
        '"usage":{"input_tokens":5,"output_tokens":0}}}')
    lines += _sse_event("content_block_start",
        '{"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"tu_01","name":"get_weather","input":{}}}')
    # Partial JSON arrives in fragments
    lines += _sse_event("content_block_delta",
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"{\\"city\\": \\"Lon"}}')
    lines += _sse_event("content_block_delta",
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"don\\"}"}}')
    lines += _sse_event("content_block_stop", '{"type":"content_block_stop","index":0}')
    lines += _sse_event("message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":7}}')
    lines += _sse_event("message_stop", '{"type":"message_stop"}')

    events = list(_parse_sse_stream(lines))

    tool_events = [e for e in events if e.type == "tool_call"]
    assert len(tool_events) == 1
    tc = tool_events[0].tool_call
    assert tc is not None
    assert tc.id == "tu_01"
    assert tc.name == "get_weather"
    assert tc.arguments == {"city": "London"}

    done = [e for e in events if e.type == "done"][0]
    assert done.stop_reason == "tool_use"


def test_collect_stream_rebuilds_completion_response():
    lines: list[str] = []
    lines += _sse_event("message_start",
        '{"type":"message_start","message":{"id":"m1","model":"claude-sonnet-4-6",'
        '"usage":{"input_tokens":4,"output_tokens":0}}}')
    lines += _sse_event("content_block_start",
        '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}')
    lines += _sse_event("content_block_delta",
        '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}')
    lines += _sse_event("content_block_stop", '{"type":"content_block_stop","index":0}')
    lines += _sse_event("message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}')
    lines += _sse_event("message_stop", '{"type":"message_stop"}')

    resp = collect_stream(_parse_sse_stream(lines))

    assert resp.content == "ok"
    assert resp.tool_calls == []
    assert resp.model == "claude-sonnet-4-6"
    assert resp.stop_reason == "end_turn"
    assert resp.usage.input_tokens == 4
    assert resp.usage.output_tokens == 1


def test_parser_handles_bytes_lines_and_keepalive_comments():
    # Real urllib response yields bytes. Anthropic also sends ":" keepalives.
    raw: list[bytes] = []
    raw += [b"event: message_start",
            b'data: {"type":"message_start","message":{"model":"claude-opus-4-7","usage":{"input_tokens":1,"output_tokens":0}}}',
            b""]
    raw += [b": keepalive", b""]
    raw += [b"event: content_block_start",
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            b""]
    raw += [b"event: content_block_delta",
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}',
            b""]
    raw += [b"event: content_block_stop",
            b'data: {"type":"content_block_stop","index":0}',
            b""]
    raw += [b"event: message_delta",
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}',
            b""]
    raw += [b"event: message_stop",
            b'data: {"type":"message_stop"}',
            b""]

    events = list(_parse_sse_stream(raw))
    texts = [e.text for e in events if e.type == "text"]
    assert texts == ["hi"]
    done = [e for e in events if e.type == "done"][0]
    assert done.stop_reason == "end_turn"


def test_stream_error_event_raises():
    import pytest
    lines: list[str] = []
    lines += _sse_event("error",
        '{"type":"error","error":{"type":"overloaded_error","message":"slow down"}}')

    with pytest.raises(RuntimeError, match="overloaded_error"):
        list(_parse_sse_stream(lines))
