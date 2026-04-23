"""FastAPI service for the Mode-Two Detector.

Endpoints:
  POST /v1/detect             — fire-and-forget (async=true) or sync judgment
  GET  /v1/detections/query   — bounded-tail filter over stored JSONL
  GET  /v1/health             — status, backend reachability, recent stats
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse

from tools.truth_detector.config import load_config
from tools.truth_detector.detector import detect
from tools.truth_detector.schemas import (
    DetectRequest, HealthResponse, Judgment, QueuedResponse,
)
from tools.truth_detector.storage import DetectionStore

log = logging.getLogger("truth_detector.service")

DEFAULT_CONFIG_PATH = os.environ.get(
    "TRUTH_DETECTOR_CONFIG",
    str(Path(__file__).parent / "config.yaml"),
)


def build_app(config_path: str | None = None) -> FastAPI:
    cfg = load_config(config_path or DEFAULT_CONFIG_PATH)
    store = DetectionStore(
        path=cfg.storage.detections_path,
        rolling_window_min=cfg.storage.rolling_window_min,
        tail_scan_bytes=cfg.storage.tail_scan_bytes,
    )
    app = FastAPI(title="Mode-Two Detector", version="1.0.0")

    @app.get("/")
    async def root():
        return {
            "service": "Mode-Two Detector",
            "version": "1.0.0",
            "endpoints": {
                "health": "/v1/health",
                "detect": "/v1/detect (POST)",
                "query": "/v1/detections/query (GET)"
            }
        }

    @app.get("/health")
    async def health_redirect():
        return RedirectResponse(url="/v1/health")

    @app.post("/v1/detect")
    async def post_detect(req: DetectRequest, bg: BackgroundTasks):
        if req.run_async:
            async def _run():
                try:
                    await detect(
                        turn_id=req.turn_id,
                        user_prompt=req.user_prompt,
                        response=req.response,
                        chat_model=req.chat_model,
                        config=cfg, store=store,
                    )
                except Exception:  # defensive — background must never crash
                    log.exception("async detect failed turn_id=%s", req.turn_id)
            bg.add_task(_run)
            return JSONResponse(
                status_code=202,
                content=QueuedResponse(turn_id=req.turn_id).model_dump(),
            )
        try:
            judgment = await detect(
                turn_id=req.turn_id,
                user_prompt=req.user_prompt,
                response=req.response,
                chat_model=req.chat_model,
                config=cfg, store=store,
            )
        except Exception:
            log.exception("sync detect failed turn_id=%s", req.turn_id)
            return JSONResponse(
                status_code=502,
                content={"error": "detect_failed", "turn_id": req.turn_id},
            )
        return judgment.model_dump()

    @app.get("/v1/detections/query")
    async def query_detections(
        since: str = Query("1h"),
        score_below: float | None = Query(None),
        chat_model: str | None = Query(None),
        failure_mode: str | None = Query(None),
        limit: int = Query(100, ge=1, le=1000),
    ):
        rows, truncated = await store.query_raw_async(
            since=since, score_below=score_below, chat_model=chat_model,
            failure_mode=failure_mode, limit=limit,
        )
        status = 206 if truncated else 200
        return JSONResponse(
            status_code=status,
            content={"rows": rows, "truncated": truncated},
        )

    @app.get("/v1/health", response_model=HealthResponse)
    async def health():
        backends_status = []
        # vllm
        vllm_cfg = cfg.backends.get("vllm")
        if vllm_cfg:
            backends_status.append(await _probe_vllm(vllm_cfg))
        # anthropic: cheap probe — just confirm API key is present; don't hit API every health call.
        anth_cfg = cfg.backends.get("anthropic")
        if anth_cfg:
            backends_status.append({
                "name": "anthropic",
                "reachable": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "latency_ms": None,
            })
        stats = store.window_stats()
        parse_fail = stats["parse_fail_rate"]
        degraded = (parse_fail > cfg.alerts.parse_fail_rate_threshold or
                    not all(b["reachable"] for b in backends_status))
        return HealthResponse(
            status="degraded" if degraded else "ok",
            judge_backends=backends_status,
            detections_last_15min=stats["count"],
            parse_fail_rate_15min=parse_fail,
        )

    return app


async def _probe_vllm(backend_cfg) -> dict:
    url = f"{backend_cfg.base_url.rstrip('/')}/models"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            reachable = r.status_code == 200
    except Exception:
        reachable = False
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {"name": "vllm", "reachable": reachable, "latency_ms": latency_ms}


try:
    app = build_app()
except FileNotFoundError as e:
    log.error("Config missing, building degraded app: %s", e)
    _err = str(e)
    app = FastAPI(title="Mode-Two Detector (degraded)", version="1.0.0")

    @app.get("/v1/health")
    async def _health_degraded():
        return {"status": "degraded", "reason": f"config_missing: {_err}",
                "judge_backends": [], "detections_last_15min": 0, "parse_fail_rate_15min": 0.0}
