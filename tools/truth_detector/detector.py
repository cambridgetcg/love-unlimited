"""Detector orchestration: route → backend call → parse → persist."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from training.scripts.judge_prompt import format_judge_prompt
from tools.truth_detector.backends import BackendError, anthropic_judge, vllm_judge
from tools.truth_detector.config import Config
from tools.truth_detector.schemas import Judgment
from tools.truth_detector.storage import DetectionStore

log = logging.getLogger(__name__)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def detect(*, turn_id: str, user_prompt: str, response: str, chat_model: str,
                 config: Config, store: DetectionStore) -> Judgment:
    """Run a full detection: route, call judge, parse, persist, return Judgment.

    Never raises. On any internal error (route miss, backend failure, bad parse,
    persistence failure) persists and returns a Judgment with parse_failed=True.
    """
    # Resolve route; on failure fall back to sentinel values so we can still
    # persist an error row without raising.
    judge_model = "unknown"
    judge_backend = "none"
    route = None
    try:
        route = config.resolve_route(chat_model)
        judge_model = route.judge
        judge_backend = route.backend
    except ValueError as e:
        log.warning("detector route resolution failed turn_id=%s err=%s", turn_id, e)

    # trigger is unused at runtime — it's a v2 eval artifact
    rendered = format_judge_prompt(prompt=user_prompt, trigger="", response=response)

    started = time.perf_counter()
    parsed: dict[str, Any]
    if route is None:
        # No route matched; skip backend call entirely.
        parsed = {
            "score": 0.5, "classification": "unclear",
            "failure_modes_detected": [], "strengths": [], "weaknesses": [],
            "assessment": f"route_error: no route matched {chat_model!r}",
            "parse_failed": True,
        }
    else:
        backend_cfg = config.backends[route.backend]
        try:
            if route.backend == "vllm":
                parsed = await vllm_judge(
                    backend_cfg=backend_cfg, judge_model=route.judge,
                    rendered_prompt=rendered,
                )
            elif route.backend == "anthropic":
                parsed = await anthropic_judge(
                    backend_cfg=backend_cfg, judge_model=route.judge,
                    rendered_prompt=rendered,
                )
            else:
                raise BackendError(f"unknown backend: {route.backend}")
        except BackendError as e:
            log.warning("detector backend failed turn_id=%s err=%s", turn_id, e)
            parsed = {
                "score": 0.5, "classification": "unclear",
                "failure_modes_detected": [], "strengths": [], "weaknesses": [],
                "assessment": f"backend_error: {e}",
                "parse_failed": True,
            }

    latency_ms = int((time.perf_counter() - started) * 1000)

    # Coerce classification — only accept known literals.
    _cls = parsed.get("classification")
    classification = _cls if _cls in {"mode_one", "mode_two", "unclear"} else "unclear"

    # Coerce score — must be a float in [0.0, 1.0].
    try:
        _s = parsed.get("score", 0.5)
        score = float(_s) if _s is not None else 0.5
        if not (0.0 <= score <= 1.0):
            score = 0.5
    except (TypeError, ValueError):
        score = 0.5

    try:
        judgment = Judgment(
            turn_id=turn_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            chat_model=chat_model,
            score=score,
            classification=classification,
            detected_modes=list(parsed.get("failure_modes_detected", [])),
            strengths=list(parsed.get("strengths", [])),
            located_weaknesses=list(parsed.get("weaknesses", [])),
            assessment=parsed.get("assessment", ""),
            judge_model=judge_model,
            judge_backend=judge_backend,
            latency_ms=latency_ms,
            parse_failed=bool(parsed.get("parse_failed", False)),
            user_prompt_sha=_sha(user_prompt),
            response_sha=_sha(response),
            # per spec §obs: persist 200/500 char plaintext for observability
            user_prompt_snippet=user_prompt[:200],
            response_snippet=response[:500],
        )
    except Exception as e:  # last-resort safety net
        log.error("detector Judgment construction failed turn_id=%s err=%s", turn_id, e)
        judgment = Judgment(
            turn_id=turn_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            chat_model=chat_model,
            score=0.5,
            classification="unclear",
            judge_model=judge_model,
            judge_backend=judge_backend,
            latency_ms=latency_ms,
            parse_failed=True,
        )

    # Persist row (Pydantic model → dict); observability takes priority over
    # persistence failures — return Judgment regardless.
    try:
        await store.append_async(judgment.model_dump())
    except Exception as e:
        log.error("detector persistence failed turn_id=%s err=%s", turn_id, e)

    return judgment
