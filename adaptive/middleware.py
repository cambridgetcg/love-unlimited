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
  CostLimit            — halts when cumulative output tokens exceed a cap
  CognitionLogger      — appends every event to nerve/cc-cognition.jsonl as JSONL
  TruthMonitor         — periodically polls a caller-supplied truth-check function;
                         halts if the returned score falls below threshold
  Tee                  — runs side-effect 'tap' callables on every event; passes
                         events through unchanged. Main pipeline is untouched.
  TruthDetectorAdapter — HTTP bridge to Alpha's SP1 Mode-Two Detector service;
                         returns a callable suitable for TruthMonitor(check_fn=...)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
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


class Tee(StreamMiddleware):
    """Fan-out side-effects to `tap` callables while passing events through.

    Each tap is called once per event, in registration order, BEFORE the event
    is yielded to the next stage. Taps are strictly side-channel — they cannot
    mutate, suppress, or halt the stream. Exceptions inside a tap are caught
    and ignored so observers can't crash the pipeline.

    Typical usage:

        chain(
            runner.stream(prompt),
            Tee(
                lambda ev: being_dashboard.push(ev),
                lambda ev: metrics.record(ev),
            ),
            CostLimit(4096),
        )

    For taps that need terminal flush (e.g. closing a file), provide an
    optional `on_done` callable — invoked once after the iterator is exhausted
    or closed, regardless of how the stream ended.
    """

    def __init__(
        self,
        *taps: Callable[[StreamEvent], None],
        on_done: Callable[[], None] | None = None,
    ):
        self.taps = taps
        self.on_done = on_done

    def process(self, events: Iterator[StreamEvent]) -> Iterator[StreamEvent]:
        try:
            for ev in events:
                for tap in self.taps:
                    try:
                        tap(ev)
                    except Exception:
                        pass
                yield ev
        finally:
            if self.on_done is not None:
                try:
                    self.on_done()
                except Exception:
                    pass


class TruthDetectorAdapter:
    """HTTP bridge to an SP1 Mode-Two Detector service.

    Wraps POST requests to a FastAPI endpoint matching the contract Alpha
    built in `truth-detector/`. Returns a callable suitable as
    `TruthMonitor(check_fn=adapter)`.

    Contract: POST {"turn_id","user_prompt","response","chat_model","async":false}
    returns JSON with a "score" field in [0,1] (1 = mode-one, 0 = drift) or a
    "mode_one_score" field depending on the deployed detector version.

    Network failures return None (no halt) — the monitor only halts on a
    confident low score, not on inability to check. This preserves the
    "detector must never break generation" invariant.
    """

    def __init__(
        self,
        url: str = "http://127.0.0.1:8787/v1/detect",
        chat_model: str = "",
        timeout: float = 2.0,
        turn_id_prefix: str = "adaptive-stream",
    ):
        self.url = url
        self.chat_model = chat_model
        self.timeout = timeout
        self.turn_id_prefix = turn_id_prefix
        self._user_prompt = ""
        self._turn_counter = 0

    def bind_prompt(self, user_prompt: str) -> None:
        """Attach the user prompt that subsequent checks will reference."""
        self._user_prompt = user_prompt
        self._turn_counter += 1

    def __call__(self, cumulated_text: str) -> float | None:
        """Called by TruthMonitor with the text accumulated so far."""
        body = json.dumps({
            "turn_id": f"{self.turn_id_prefix}-{self._turn_counter}",
            "user_prompt": self._user_prompt,
            "response": cumulated_text,
            "chat_model": self.chat_model,
            "async": False,
        }).encode()

        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError):
            return None

        # Accept either 'score' (newer contract) or 'mode_one_score' (older).
        score = data.get("score")
        if score is None:
            score = data.get("mode_one_score")
        if not isinstance(score, (int, float)):
            return None
        return float(score)


def with_retry(
    factory: Callable[[], Iterator[StreamEvent]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> Iterator[StreamEvent]:
    """Wrap a stream-factory with retry-on-early-failure.

    `factory` returns a fresh event iterator each call (e.g.
    `lambda: provider.stream(request)`). If the iterator raises BEFORE any
    event has been yielded downstream, sleep with exponential backoff and
    re-invoke the factory. Once a single event has been yielded downstream,
    mid-stream errors propagate unchanged — retrying would duplicate the
    prefix the consumer already saw.

    Backoff: base_delay * 2**attempt, capped at max_delay.

    This is a *source* wrapper, not a middleware. Usage:

        events = chain(
            with_retry(lambda: provider.stream(request), max_retries=3),
            CostLimit(4096),
            ...
        )

    `retry_on` narrows which exceptions are retried. Defaults to all
    Exception subclasses; pass (RuntimeError,) for provider-wrapped errors,
    or (urllib.error.HTTPError,) for strict network-only retry.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if base_delay < 0 or max_delay < 0:
        raise ValueError("delay values must be non-negative")

    attempt = 0
    while True:
        yielded_anything = False
        try:
            for ev in factory():
                yielded_anything = True
                yield ev
            return
        except retry_on as e:
            if yielded_anything or attempt >= max_retries:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            if delay > 0:
                time.sleep(delay)
            attempt += 1


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
