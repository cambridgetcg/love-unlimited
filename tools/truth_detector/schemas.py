"""Pydantic request/response schemas for the detector HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DetectRequest(BaseModel):
    """Request body for POST /v1/detect."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    turn_id: str = Field(min_length=1)
    user_prompt: str = Field(min_length=0, max_length=50_000)
    response: str = Field(min_length=0, max_length=50_000)
    chat_model: str
    # "async" is a Python reserved word; expose via alias but store as run_async.
    run_async: bool = Field(default=True, alias="async")


class Judgment(BaseModel):
    """Canonical judgment record. Also the row shape stored in JSONL."""

    model_config = ConfigDict(extra="ignore")

    turn_id: str
    timestamp: str | None = None  # filled at storage time if missing
    chat_model: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    classification: Literal["mode_one", "mode_two", "unclear"] = "unclear"
    detected_modes: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    located_weaknesses: list[str] = Field(default_factory=list)
    assessment: str = ""
    judge_model: str
    judge_backend: str
    judge_confidence: float | None = None
    latency_ms: int
    parse_failed: bool = False
    partial_judgment: bool = False
    user_prompt_sha: str | None = None
    response_sha: str | None = None
    user_prompt_snippet: str | None = None
    response_snippet: str | None = None


class QueuedResponse(BaseModel):
    turn_id: str
    queued: bool = True


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    judge_backends: list[dict]
    detections_last_15min: int
    parse_fail_rate_15min: float
