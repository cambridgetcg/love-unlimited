import hashlib
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from tools.truth_detector.config import load_config
from tools.truth_detector.storage import DetectionStore
from tools.truth_detector.detector import detect


@pytest.fixture
def cfg(tmp_path):
    yaml_text = """
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
  detections_path: "detections.jsonl"
  rolling_window_min: 15
  tail_scan_bytes: 10485760
alerts:
  parse_fail_rate_threshold: 0.1
  backend_down_threshold: 0.1
"""
    p = tmp_path / "config.yaml"
    p.write_text(yaml_text)
    return load_config(str(p))


@pytest.fixture
def store(tmp_path):
    return DetectionStore(path=str(tmp_path / "d.jsonl"), rolling_window_min=15)


@pytest.mark.asyncio
async def test_detect_routes_claude_to_vllm(cfg, store):
    fake_judgment = {
        "score": 0.4, "classification": "mode_two",
        "failure_modes_detected": ["rationalisation"],
        "strengths": [], "weaknesses": ["deflects"],
        "assessment": "mild deflection", "parse_failed": False,
    }
    with patch("tools.truth_detector.detector.vllm_judge", AsyncMock(return_value=fake_judgment)) as vllm, \
         patch("tools.truth_detector.detector.anthropic_judge", AsyncMock()) as anth:
        out = await detect(
            turn_id="t1", user_prompt="are you wrong?", response="fair point...",
            chat_model="claude-opus-4-6", config=cfg, store=store,
        )
    vllm.assert_awaited_once()
    anth.assert_not_awaited()
    assert out.judge_model == "kingdom-truth"
    assert out.judge_backend == "vllm"
    assert out.classification == "mode_two"


@pytest.mark.asyncio
async def test_detect_persists_row(cfg, store):
    fake = {"score": 0.4, "classification": "mode_two",
            "failure_modes_detected": [], "strengths": [], "weaknesses": [],
            "assessment": "", "parse_failed": False}
    with patch("tools.truth_detector.detector.vllm_judge", AsyncMock(return_value=fake)):
        await detect(turn_id="t1", user_prompt="p", response="r",
                     chat_model="claude-opus-4-6", config=cfg, store=store)
    lines = Path(store.path).read_text().strip().split("\n")
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_detect_includes_shas(cfg, store):
    fake = {"score": 0.4, "classification": "mode_two",
            "failure_modes_detected": [], "strengths": [], "weaknesses": [],
            "assessment": "", "parse_failed": False}
    with patch("tools.truth_detector.detector.vllm_judge", AsyncMock(return_value=fake)):
        out = await detect(turn_id="t1", user_prompt="hello", response="world",
                           chat_model="claude-opus-4-6", config=cfg, store=store)
    assert out.user_prompt_sha == hashlib.sha256(b"hello").hexdigest()
    assert out.response_sha == hashlib.sha256(b"world").hexdigest()


@pytest.mark.asyncio
async def test_detect_handles_backend_failure_persists_error_row(cfg, store):
    from tools.truth_detector.backends import BackendError
    with patch("tools.truth_detector.detector.vllm_judge",
               AsyncMock(side_effect=BackendError("timeout"))):
        out = await detect(turn_id="t1", user_prompt="p", response="r",
                           chat_model="claude-opus-4-6", config=cfg, store=store)
    assert out.parse_failed is True
    assert out.classification == "unclear"
    # row still persisted for observability
    assert Path(store.path).exists()
    assert Path(store.path).read_text().strip() != ""
