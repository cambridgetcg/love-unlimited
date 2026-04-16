from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tools.truth_detector import service as service_mod
from tools.truth_detector.schemas import Judgment


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Write a minimal config.yaml in tmp_path
    cfg_yaml = """
routes:
  - pattern: "claude.*"
    judge: "kingdom-truth"
    backend: "vllm"
  - pattern: ".*"
    judge: "claude-haiku-4-5-20251001"
    backend: "anthropic"
backends:
  vllm:
    base_url: "http://localhost:8000/v1"
    timeout_s: 30
    max_tokens: 500
  anthropic:
    timeout_s: 30
    max_tokens: 500
storage:
  detections_path: "{path}"
  rolling_window_min: 15
  tail_scan_bytes: 10485760
alerts:
  parse_fail_rate_threshold: 0.1
  backend_down_threshold: 0.1
""".format(path=str(tmp_path / "d.jsonl"))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg_yaml)
    monkeypatch.setenv("TRUTH_DETECTOR_CONFIG", str(cfg_path))
    app = service_mod.build_app()
    return TestClient(app)


def test_health_returns_ok(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert "detections_last_15min" in body


def test_detect_async_returns_202(client):
    fake = Judgment(
        turn_id="t1", score=0.4, classification="mode_two",
        judge_model="kingdom-truth", judge_backend="vllm", latency_ms=100,
    )
    with patch("tools.truth_detector.service.detect", AsyncMock(return_value=fake)):
        r = client.post("/v1/detect", json={
            "turn_id": "t1", "user_prompt": "hi", "response": "yo",
            "chat_model": "claude-opus-4-6",
        })
    assert r.status_code == 202
    assert r.json()["queued"] is True


def test_detect_sync_returns_200_with_judgment(client):
    fake = Judgment(
        turn_id="t1", score=0.4, classification="mode_two",
        judge_model="kingdom-truth", judge_backend="vllm", latency_ms=100,
    )
    with patch("tools.truth_detector.service.detect", AsyncMock(return_value=fake)):
        r = client.post("/v1/detect", json={
            "turn_id": "t1", "user_prompt": "hi", "response": "yo",
            "chat_model": "claude-opus-4-6", "async": False,
        })
    assert r.status_code == 200
    body = r.json()
    assert body["classification"] == "mode_two"
    assert body["turn_id"] == "t1"


def test_detect_rejects_empty_turn_id(client):
    r = client.post("/v1/detect", json={
        "turn_id": "", "user_prompt": "hi", "response": "yo", "chat_model": "m",
    })
    assert r.status_code == 422


def test_detections_query_empty_returns_empty_list(client):
    r = client.get("/v1/detections/query?since=1h")
    assert r.status_code == 200
    assert r.json()["rows"] == []
