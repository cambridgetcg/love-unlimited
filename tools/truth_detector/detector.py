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

    Never raises. On backend failure, persists a minimal error row and returns
    a Judgment with parse_failed=True.
    """
    route = config.resolve_route(chat_model)
    backend_cfg = config.backends[route.backend]
    rendered = format_judge_prompt(prompt=user_prompt, trigger="", response=response)

    started = time.perf_counter()
    parsed: dict[str, Any]
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

    judgment = Judgment(
        turn_id=turn_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        chat_model=chat_model,
        score=float(parsed.get("score", 0.5)),
        classification=parsed.get("classification", "unclear"),
        detected_modes=list(parsed.get("failure_modes_detected", [])),
        strengths=list(parsed.get("strengths", [])),
        located_weaknesses=list(parsed.get("weaknesses", [])),
        assessment=parsed.get("assessment", ""),
        judge_model=route.judge,
        judge_backend=route.backend,
        latency_ms=latency_ms,
        parse_failed=bool(parsed.get("parse_failed", False)),
        user_prompt_sha=_sha(user_prompt),
        response_sha=_sha(response),
        user_prompt_snippet=user_prompt[:200],
        response_snippet=response[:500],
    )

    # Persist row (Pydantic model → dict)
    store.append(judgment.model_dump())
    return judgment
