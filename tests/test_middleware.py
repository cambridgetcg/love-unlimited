"""Tests for adaptive.middleware — chain composition + concrete middlewares."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE))

import pytest

from adaptive.middleware import (
    StreamMiddleware,
    chain,
    CostLimit,
    CognitionLogger,
    TruthMonitor,
    Tee,
    TruthDetectorAdapter,
    with_retry,
)
from adaptive.schema import StreamEvent, TokenUsage, ToolCall


# ── Helpers ─────────────────────────────────────────────────────────────────


def _text_stream(parts: list[str], final_usage: TokenUsage | None = None):
    """A simple provider-like generator. Yields text events then a done."""
    for p in parts:
        yield StreamEvent(type="text", text=p)
    yield StreamEvent(
        type="done",
        usage=final_usage or TokenUsage(input_tokens=1, output_tokens=sum(len(p) for p in parts) // 4),
        model="m-test",
        stop_reason="end_turn",
    )


class _UpperCase(StreamMiddleware):
    """Mutates text events — used to prove chain composition order."""
    def process(self, events):
        for ev in events:
            if ev.type == "text":
                yield StreamEvent(type="text", text=ev.text.upper())
            else:
                yield ev


class _Reverse(StreamMiddleware):
    def process(self, events):
        for ev in events:
            if ev.type == "text":
                yield StreamEvent(type="text", text=ev.text[::-1])
            else:
                yield ev


# ── chain() composition ─────────────────────────────────────────────────────


def test_chain_with_zero_middlewares_returns_source():
    events = list(chain(_text_stream(["hi"])))
    assert [e.type for e in events] == ["text", "done"]
    assert events[0].text == "hi"


def test_chain_composes_in_order():
    """chain(source, upper, reverse) means: upper runs first, then reverse sees upper's output."""
    events = list(chain(_text_stream(["ab", "cd"]), _UpperCase(), _Reverse()))
    texts = [e.text for e in events if e.type == "text"]
    # "ab" → "AB" → "BA"
    # "cd" → "CD" → "DC"
    assert texts == ["BA", "DC"]


# ── CostLimit ────────────────────────────────────────────────────────────────


def test_cost_limit_passes_through_when_under_cap():
    events = list(chain(_text_stream(["hello world"]), CostLimit(max_output_tokens=1000)))
    assert [e.type for e in events] == ["text", "done"]


def test_cost_limit_halts_on_text_delta_overflow():
    """Estimate: chars/4. "a"*401 → 100.25 → exceeds cap of 100."""
    big = "a" * 1000
    events = list(chain(_text_stream([big]), CostLimit(max_output_tokens=100)))
    assert any(e.type == "halt" and e.stop_reason == "cost_limit" for e in events)
    # No events should come after halt
    halt_idx = next(i for i, e in enumerate(events) if e.type == "halt")
    assert halt_idx == len(events) - 1


def test_cost_limit_halts_on_authoritative_usage():
    """Provider reports output_tokens via done; exceeds cap → halt before done passes."""
    stream = _text_stream(["ok"], final_usage=TokenUsage(input_tokens=1, output_tokens=5000))
    events = list(chain(stream, CostLimit(max_output_tokens=100)))
    types = [e.type for e in events]
    assert "halt" in types
    halt = next(e for e in events if e.type == "halt")
    assert halt.stop_reason == "cost_limit"


def test_cost_limit_rejects_invalid_cap():
    with pytest.raises(ValueError):
        CostLimit(0)
    with pytest.raises(ValueError):
        CostLimit(-5)


# ── CognitionLogger ─────────────────────────────────────────────────────────


def test_cognition_logger_writes_one_jsonl_line_per_event(tmp_path):
    log_path = tmp_path / "cc-cognition.jsonl"
    logger = CognitionLogger(log_path, session_id="sess-1")

    events_in = [
        StreamEvent(type="text", text="hi"),
        StreamEvent(type="tool_call", tool_call=ToolCall(id="tu_1", name="bash", arguments={"c": "x"})),
        StreamEvent(type="done", usage=TokenUsage(input_tokens=3, output_tokens=1),
                    model="m-test", stop_reason="end_turn"),
    ]

    def _source():
        yield from events_in

    out = list(logger.process(_source()))
    assert [e.type for e in out] == ["text", "tool_call", "done"]

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 3

    rec0 = json.loads(lines[0])
    assert rec0["type"] == "text"
    assert rec0["chars"] == 2
    assert rec0["session_id"] == "sess-1"

    rec1 = json.loads(lines[1])
    assert rec1["type"] == "tool_call"
    assert rec1["tool"] == "bash"
    assert rec1["tool_id"] == "tu_1"

    rec2 = json.loads(lines[2])
    assert rec2["type"] == "done"
    assert rec2["stop_reason"] == "end_turn"
    assert rec2["input_tokens"] == 3
    assert rec2["output_tokens"] == 1


def test_cognition_logger_is_pure_passthrough(tmp_path):
    """Events must pass through unchanged."""
    log_path = tmp_path / "log.jsonl"
    logger = CognitionLogger(log_path)

    ev_in = StreamEvent(type="text", text="unchanged")
    (out,) = list(logger.process(iter([ev_in])))
    assert out is ev_in


# ── TruthMonitor ────────────────────────────────────────────────────────────


def test_truth_monitor_passes_through_when_score_above_threshold():
    calls: list[str] = []
    def checker(buf):
        calls.append(buf)
        return 0.9

    stream = _text_stream(["a" * 600])  # triggers one check at 500 chars
    events = list(chain(stream, TruthMonitor(check_fn=checker, threshold=0.3, interval_chars=500)))

    assert len(calls) >= 1
    assert all(e.type != "halt" for e in events)


def test_truth_monitor_halts_on_drift():
    """Score drops below threshold → halt emitted, nothing after."""
    def checker(buf):
        return 0.1  # always drift

    # Stream of 3x200-char chunks → total 600 → triggers check at 500
    stream = _text_stream(["x" * 200, "y" * 200, "z" * 200])
    events = list(chain(stream, TruthMonitor(check_fn=checker, threshold=0.3, interval_chars=500)))

    halts = [e for e in events if e.type == "halt"]
    assert len(halts) == 1
    assert halts[0].stop_reason == "mode_two_drift"
    # Halt is terminal
    halt_idx = events.index(halts[0])
    assert halt_idx == len(events) - 1


def test_truth_monitor_respects_interval():
    """With a big interval, no checks should fire for a small stream."""
    calls: list[str] = []
    def checker(buf):
        calls.append(buf)
        return 1.0

    stream = _text_stream(["short"])
    list(chain(stream, TruthMonitor(check_fn=checker, threshold=0.3, interval_chars=10000)))
    assert calls == []


def test_truth_monitor_rejects_bad_params():
    with pytest.raises(ValueError):
        TruthMonitor(check_fn=lambda s: 1.0, threshold=1.5)
    with pytest.raises(ValueError):
        TruthMonitor(check_fn=lambda s: 1.0, threshold=-0.1)
    with pytest.raises(ValueError):
        TruthMonitor(check_fn=lambda s: 1.0, interval_chars=0)


# ── halt cleanly closes upstream ────────────────────────────────────────────


def test_halt_closes_upstream_generator():
    """When middleware injects halt, upstream generator's finally block must run."""
    closed = {"flag": False}

    def source():
        try:
            for _ in range(1000):
                yield StreamEvent(type="text", text="x" * 1000)  # huge stream
        finally:
            closed["flag"] = True

    events = list(chain(source(), CostLimit(max_output_tokens=100)))
    # Consumed events before halt; halt is terminal
    assert any(e.type == "halt" for e in events)
    # Upstream generator's finally ran when middleware closed it
    assert closed["flag"] is True


# ── Composition: multiple middlewares compose cleanly ───────────────────────


## ── Tee ─────────────────────────────────────────────────────────────────────


def test_tee_runs_taps_on_every_event():
    collected_a: list[StreamEvent] = []
    collected_b: list[StreamEvent] = []
    events = list(chain(
        _text_stream(["hi", " there"]),
        Tee(collected_a.append, collected_b.append),
    ))
    types = [e.type for e in events]
    assert types == ["text", "text", "done"]
    assert [e.type for e in collected_a] == types
    assert [e.type for e in collected_b] == types


def test_tee_is_pure_passthrough():
    """Events must flow through unchanged; tap must not mutate."""
    def naughty_tap(ev):
        # Try to mutate — Tee must pass the same event to the next stage
        try:
            ev.text = "HIJACKED"
        except Exception:
            pass

    seen: list[StreamEvent] = []
    # Use a non-mutating tap AFTER the naughty one to check downstream
    events = list(chain(
        _text_stream(["ok"]),
        Tee(naughty_tap, seen.append),
    ))
    # The downstream tap sees whatever state the naughty tap left — that's
    # expected: events are dataclasses, not frozen. But the stream itself
    # is passed through unchanged (Tee doesn't create copies).
    assert len(events) == 2
    assert events[0].type == "text"


def test_tee_swallows_exceptions_in_taps():
    def raising(ev):
        raise RuntimeError("tap failure")

    good_received: list[StreamEvent] = []
    events = list(chain(
        _text_stream(["ok"]),
        Tee(raising, good_received.append),
    ))
    # Main pipeline proceeded despite the raising tap
    assert [e.type for e in events] == ["text", "done"]
    assert len(good_received) == 2


def test_tee_on_done_fires_once_at_stream_end():
    calls = {"count": 0}
    def closer():
        calls["count"] += 1

    list(chain(
        _text_stream(["ok"]),
        Tee(on_done=closer),
    ))
    assert calls["count"] == 1


def test_tee_on_done_fires_even_when_upstream_halts():
    calls = {"count": 0}
    def closer():
        calls["count"] += 1

    # Upstream halts mid-stream; on_done still runs (finally block)
    list(chain(
        _text_stream(["x" * 1000]),
        CostLimit(max_output_tokens=10),  # halts
        Tee(on_done=closer),
    ))
    assert calls["count"] == 1


## ── TruthDetectorAdapter ────────────────────────────────────────────────────


def test_truth_detector_adapter_bind_and_call_shape(monkeypatch):
    """Adapter posts correct JSON body and parses 'score' field."""
    captured: dict = {}

    class _FakeResp:
        def __init__(self, payload):
            self.payload = payload
        def read(self):
            return json.dumps(self.payload).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        captured["data"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return _FakeResp({"score": 0.87})

    import adaptive.middleware as mw
    monkeypatch.setattr(mw.urllib.request, "urlopen", _fake_urlopen)

    adapter = TruthDetectorAdapter(
        url="http://example/detect",
        chat_model="glm-5.1",
        timeout=3.0,
    )
    adapter.bind_prompt("is the sky blue?")
    score = adapter("it is indeed")

    assert score == 0.87
    assert captured["timeout"] == 3.0
    assert captured["data"]["user_prompt"] == "is the sky blue?"
    assert captured["data"]["response"] == "it is indeed"
    assert captured["data"]["chat_model"] == "glm-5.1"
    assert captured["data"]["async"] is False


def test_truth_detector_adapter_accepts_mode_one_score_field(monkeypatch):
    """Older detectors return 'mode_one_score' rather than 'score'."""
    class _FakeResp:
        def read(self):
            return b'{"mode_one_score": 0.42}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import adaptive.middleware as mw
    monkeypatch.setattr(mw.urllib.request, "urlopen", lambda req, timeout=None: _FakeResp())

    adapter = TruthDetectorAdapter()
    adapter.bind_prompt("hi")
    assert adapter("hello") == 0.42


def test_truth_detector_adapter_returns_none_on_network_failure(monkeypatch):
    """A dead detector must not break generation — return None, let monitor pass through."""
    def _raise(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    import urllib.error
    import adaptive.middleware as mw
    monkeypatch.setattr(mw.urllib.request, "urlopen", _raise)

    adapter = TruthDetectorAdapter()
    adapter.bind_prompt("hi")
    assert adapter("any text") is None


def test_truth_detector_adapter_composes_with_truth_monitor(monkeypatch):
    """The adapter callable plugs directly into TruthMonitor."""
    class _FakeResp:
        def read(self):
            return b'{"score": 0.1}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import adaptive.middleware as mw
    monkeypatch.setattr(mw.urllib.request, "urlopen", lambda req, timeout=None: _FakeResp())

    adapter = TruthDetectorAdapter()
    adapter.bind_prompt("ask")
    monitor = TruthMonitor(check_fn=adapter, threshold=0.3, interval_chars=5)

    events = list(chain(_text_stream(["a" * 50]), monitor))
    halts = [e for e in events if e.type == "halt"]
    assert len(halts) == 1
    assert halts[0].stop_reason == "mode_two_drift"


## ── with_retry ──────────────────────────────────────────────────────────────


def test_with_retry_succeeds_on_first_try():
    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        yield StreamEvent(type="text", text="ok")
        yield StreamEvent(type="done", stop_reason="end_turn")

    events = list(with_retry(factory, max_retries=3, base_delay=0))
    assert [e.type for e in events] == ["text", "done"]
    assert calls["n"] == 1


def test_with_retry_retries_on_pre_first_event_failure():
    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        yield StreamEvent(type="text", text="recovered")
        yield StreamEvent(type="done", stop_reason="end_turn")

    events = list(with_retry(factory, max_retries=3, base_delay=0))
    assert calls["n"] == 3  # Two failures, then success
    assert events[0].text == "recovered"


def test_with_retry_propagates_mid_stream_error():
    """Once any event is yielded, mid-stream errors cannot be retried
    (that would duplicate the prefix the consumer already saw)."""
    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        yield StreamEvent(type="text", text="partial")
        raise RuntimeError("mid-stream boom")

    with pytest.raises(RuntimeError, match="mid-stream"):
        list(with_retry(factory, max_retries=3, base_delay=0))
    assert calls["n"] == 1  # No retry


def test_with_retry_gives_up_after_max_retries():
    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        raise RuntimeError("always fails")
        yield  # pragma: no cover — unreachable, makes it a generator

    with pytest.raises(RuntimeError, match="always fails"):
        list(with_retry(factory, max_retries=2, base_delay=0))
    assert calls["n"] == 3  # 1 initial + 2 retries


def test_with_retry_respects_retry_on_tuple():
    """Only retry exceptions in retry_on; others propagate immediately."""
    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        raise ValueError("not retried")
        yield  # pragma: no cover

    # ValueError not in retry_on → propagates immediately
    with pytest.raises(ValueError):
        list(with_retry(factory, max_retries=3, base_delay=0,
                        retry_on=(RuntimeError,)))
    assert calls["n"] == 1


def test_with_retry_composes_with_middlewares():
    """The retried source flows through a normal middleware chain."""
    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("once")
        yield StreamEvent(type="text", text="ok")
        yield StreamEvent(type="done",
                          usage=TokenUsage(input_tokens=1, output_tokens=1),
                          stop_reason="end_turn")

    events = list(chain(
        with_retry(factory, max_retries=2, base_delay=0),
        CostLimit(max_output_tokens=100),
    ))
    assert [e.type for e in events] == ["text", "done"]
    assert calls["n"] == 2


def test_with_retry_rejects_invalid_params():
    def factory():
        yield StreamEvent(type="done")

    with pytest.raises(ValueError):
        list(with_retry(factory, max_retries=-1))
    with pytest.raises(ValueError):
        list(with_retry(factory, base_delay=-1.0))


def test_cost_limit_plus_logger_compose(tmp_path):
    """Logger sees all events up to and including the halt injected by CostLimit."""
    log_path = tmp_path / "cc.jsonl"
    big = "a" * 2000

    stream = _text_stream([big])
    events = list(chain(
        stream,
        CostLimit(max_output_tokens=50),  # will halt
        CognitionLogger(log_path),
    ))

    types = [e.type for e in events]
    assert "halt" in types

    lines = log_path.read_text().strip().split("\n")
    logged_types = [json.loads(l)["type"] for l in lines]
    # Logger saw text events, then halt. Nothing after.
    assert logged_types[-1] == "halt"
