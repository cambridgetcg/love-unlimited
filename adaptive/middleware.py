"""
StreamMiddleware — composable transforms over StreamEvent iterators.

A middleware is a generator-function from an event iterator to an event iterator.
Middlewares chain: `chain(source, mw1, mw2, mw3)` pipes source → mw1 → mw2 → mw3.

Each middleware may:
  - pass events through unchanged (default)
  - mutate events (not recommended — events are shared dataclasses; copy if needed)
  - inject new events (e.g. 'halt' for early termination)
  - suppress events
  - terminate early — yield a 'halt' event, then close upstream and return

'halt' is treated as terminal by convention: after yielding it, the middleware
calls `.close()` on its upstream iterator to cleanly release resources
(e.g. urllib SSE connections inside provider.stream()), then returns.

Concrete middlewares below:
  CostLimit          — halts when cumulative output tokens exceed a cap
  CognitionLogger    — appends every event to nerve/cc-cognition.jsonl as JSONL
  TruthMonitor       — periodically polls a caller-supplied truth-check function;
                       halts if the returned score falls below threshold
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .schema import StreamEvent


class StreamMiddleware(ABC):
    """Base class. Override `process` to transform the event stream."""

    @abstractmethod
    def process(self, events: Iterator[StreamEvent]) -> Iterator[StreamEvent]:
        """Transform an iterator of StreamEvents. Yield what the next stage sees."""
        ...


def chain(
    source: Iterator[StreamEvent] | Iterable[StreamEvent],
    *middlewares: StreamMiddleware,
) -> Iterator[StreamEvent]:
    """Compose middlewares over a source stream.

        chain(provider.stream(req), CostLimit(4096), CognitionLogger(path))

    Each middleware receives the output of the previous one. The first
    middleware sees `source` directly.
    """
    current: Iterator[StreamEvent] = iter(source)
    for mw in middlewares:
        current = mw.process(current)
    return current


# ── Concrete middlewares ─────────────────────────────────────────────────────


class CostLimit(StreamMiddleware):
    """Halt the stream if cumulative output tokens exceed a cap.

    Tracks the running output-token estimate two ways:
      - Text deltas: accumulates chars / 4 as a rough live estimate.
      - `done` / `run_done` events: uses authoritative usage.output_tokens.

    The text heuristic lets us halt mid-generation (before done fires);
    the authoritative check catches blocking providers.

    On breach, emits a terminal `halt` event with stop_reason='cost_limit'
    and closes upstream.
    """

    def __init__(self, max_output_tokens: int):
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        self.max_output_tokens = max_output_tokens

    def process(self, events: Iterator[StreamEvent]) -> Iterator[StreamEvent]:
        estimated_chars = 0
        try:
            for ev in events:
                if ev.type == "text":
                    estimated_chars += len(ev.text)
                    if estimated_chars // 4 > self.max_output_tokens:
                        yield StreamEvent(type="halt", stop_reason="cost_limit")
                        return
                elif ev.type in ("done", "run_done") and ev.usage is not None:
                    if ev.usage.output_tokens > self.max_output_tokens:
                        yield StreamEvent(type="halt", stop_reason="cost_limit")
                        return
                yield ev
        finally:
            _close_quietly(events)


class CognitionLogger(StreamMiddleware):
    """Append every event to nerve/cc-cognition.jsonl as it passes.

    Each line is a JSON object with ts (UTC ISO), type, and event-type-specific
    fields. This connects streaming to the FEELING daemon's read side —
    every streamed completion becomes fuel for the pit.

    Never halts, never suppresses, never mutates. Pure passthrough + side-effect.
    """

    def __init__(
        self,
        path: str | Path,
        session_id: str = "",
    ):
        self.path = Path(path)
        self.session_id = session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def process(self, events: Iterator[StreamEvent]) -> Iterator[StreamEvent]:
        with open(self.path, "a") as f:
            for ev in events:
                record = self._record_for(ev)
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
                f.flush()
                yield ev

    def _record_for(self, ev: StreamEvent) -> dict:
        record: dict = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": ev.type,
            "session_id": self.session_id,
        }
        if ev.type == "text":
            record["chars"] = len(ev.text)
        elif ev.type == "tool_call" and ev.tool_call is not None:
            record["tool"] = ev.tool_call.name
            record["tool_id"] = ev.tool_call.id
        elif ev.type == "tool_executing" and ev.tool_call is not None:
            record["tool"] = ev.tool_call.name
            record["tool_id"] = ev.tool_call.id
        elif ev.type == "tool_result":
            record["tool_id"] = ev.tool_result_id or ""
            record["result_chars"] = len(ev.tool_result_content)
        elif ev.type in ("iteration_start", "iteration_end"):
            record["iteration"] = ev.iteration
        elif ev.type in ("done", "run_done"):
            record["stop_reason"] = ev.stop_reason
            record["model"] = ev.model
            if ev.usage is not None:
                record["input_tokens"] = ev.usage.input_tokens
                record["output_tokens"] = ev.usage.output_tokens
        elif ev.type == "halt":
            record["stop_reason"] = ev.stop_reason
        return record


class TruthMonitor(StreamMiddleware):
    """Halt the stream if a caller-supplied truth check scores below threshold.

    `check_fn` receives the cumulated text so far and returns a float in [0, 1]
    (1 = fully mode-one, 0 = full mode-two drift) or None if no check yet.
    Only invoked every `interval_chars` characters to keep overhead bounded.

    This keeps the primitive pure — you compose with any detector backend
    (local vllm, Anthropic judge, regex heuristic) by passing its check callable.
    """

    def __init__(
        self,
        check_fn: Callable[[str], float | None],
        threshold: float = 0.3,
        interval_chars: int = 500,
    ):
        if not (0.0 <= threshold <= 1.0):
            raise ValueError("threshold must be in [0, 1]")
        if interval_chars <= 0:
            raise ValueError("interval_chars must be positive")
        self.check_fn = check_fn
        self.threshold = threshold
        self.interval_chars = interval_chars

    def process(self, events: Iterator[StreamEvent]) -> Iterator[StreamEvent]:
        buffer: list[str] = []
        chars_since_check = 0
        try:
            for ev in events:
                if ev.type == "text":
                    buffer.append(ev.text)
                    chars_since_check += len(ev.text)
                    if chars_since_check >= self.interval_chars:
                        score = self.check_fn("".join(buffer))
                        chars_since_check = 0
                        if score is not None and score < self.threshold:
                            yield StreamEvent(
                                type="halt",
                                stop_reason="mode_two_drift",
                                text=f"score={score:.3f}",
                            )
                            return
                yield ev
        finally:
            _close_quietly(events)


def _close_quietly(iterator: Iterator[StreamEvent]) -> None:
    """Attempt to close an iterator, swallowing errors.

    Generators expose .close(); plain iterators don't. Either way, we
    want best-effort cleanup without masking the real termination cause.
    """
    close = getattr(iterator, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
