import json
from unittest.mock import AsyncMock, patch

import pytest

from tools.truth_detector.backends import vllm_judge, anthropic_judge, BackendError
from tools.truth_detector.config import BackendConfig


@pytest.mark.asyncio
async def test_vllm_judge_happy_path():
    fake_response = {
        "choices": [{
            "message": {"content": '{"score": 0.3, "classification": "mode_two", "failure_modes_detected": ["rationalisation"]}'}
        }]
    }

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return fake_response

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json=None):
            return FakeResp()

    cfg = BackendConfig(name="vllm", base_url="http://localhost:8000/v1", timeout_s=30, max_tokens=500)

    with patch("tools.truth_detector.backends.httpx.AsyncClient", FakeClient):
        out = await vllm_judge(
            backend_cfg=cfg, judge_model="kingdom-truth",
            rendered_prompt="judge this",
        )
    assert out["score"] == 0.3
    assert out["classification"] == "mode_two"
    assert out["parse_failed"] is False


@pytest.mark.asyncio
async def test_vllm_judge_timeout_raises_backend_error():
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json=None):
            raise httpx.ReadTimeout("timeout")

    cfg = BackendConfig(name="vllm", base_url="http://localhost:8000/v1", timeout_s=1, max_tokens=500)

    with patch("tools.truth_detector.backends.httpx.AsyncClient", FakeClient):
        with pytest.raises(BackendError):
            await vllm_judge(
                backend_cfg=cfg, judge_model="kingdom-truth",
                rendered_prompt="judge this",
            )


@pytest.mark.asyncio
async def test_anthropic_judge_happy_path(monkeypatch):
    """Anthropic SDK is mocked — return a fake Message."""

    class FakeTextBlock:
        type = "text"
        text = '{"score": 0.8, "classification": "mode_one"}'

    class FakeMessage:
        content = [FakeTextBlock()]

    class FakeMessages:
        async def create(self, **kw): return FakeMessage()

    class FakeClient:
        def __init__(self, *a, **kw): self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("tools.truth_detector.backends.anthropic.AsyncAnthropic", FakeClient):
        cfg = BackendConfig(name="anthropic", timeout_s=30, max_tokens=500)
        out = await anthropic_judge(
            backend_cfg=cfg, judge_model="claude-haiku-4-5-20251001",
            rendered_prompt="judge this",
        )
    assert out["score"] == 0.8
    assert out["classification"] == "mode_one"
