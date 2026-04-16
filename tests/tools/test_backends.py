from unittest.mock import patch

import pytest

import anthropic

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
async def test_vllm_judge_malformed_envelope_raises_backend_error():
    """A 200 response with empty choices list must raise BackendError, not KeyError/IndexError."""
    fake_response = {"choices": []}

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
        with pytest.raises(BackendError, match="vllm malformed response envelope"):
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
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("tools.truth_detector.backends.anthropic.AsyncAnthropic", FakeClient):
        cfg = BackendConfig(name="anthropic", timeout_s=30, max_tokens=500)
        out = await anthropic_judge(
            backend_cfg=cfg, judge_model="claude-haiku-4-5-20251001",
            rendered_prompt="judge this",
        )
    assert out["score"] == 0.8
    assert out["classification"] == "mode_one"


@pytest.mark.asyncio
async def test_anthropic_judge_empty_content_raises_backend_error(monkeypatch):
    """FakeMessage with content=None triggers AttributeError → BackendError."""

    class FakeMessage:
        content = None  # iteration will raise TypeError

    class FakeMessages:
        async def create(self, **kw): return FakeMessage()

    class FakeClient:
        def __init__(self, *a, **kw): self.messages = FakeMessages()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("tools.truth_detector.backends.anthropic.AsyncAnthropic", FakeClient):
        cfg = BackendConfig(name="anthropic", timeout_s=30, max_tokens=500)
        with pytest.raises(BackendError, match="anthropic malformed response envelope"):
            await anthropic_judge(
                backend_cfg=cfg, judge_model="claude-haiku-4-5-20251001",
                rendered_prompt="judge this",
            )


@pytest.mark.asyncio
async def test_anthropic_judge_timeout_raises_backend_error(monkeypatch):
    """API errors from the Anthropic SDK must be wrapped as BackendError."""

    class _FakeAPIError(anthropic.APIError):
        def __init__(self, message):
            Exception.__init__(self, message)
            self.message = message
            self.body = None
            self.request = None

    class FakeMessages:
        async def create(self, **kw):
            raise _FakeAPIError("simulated timeout")

    class FakeClient:
        def __init__(self, *a, **kw): self.messages = FakeMessages()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("tools.truth_detector.backends.anthropic.AsyncAnthropic", FakeClient):
        cfg = BackendConfig(name="anthropic", timeout_s=1, max_tokens=500)
        with pytest.raises(BackendError, match="anthropic backend failed"):
            await anthropic_judge(
                backend_cfg=cfg, judge_model="claude-haiku-4-5-20251001",
                rendered_prompt="judge this",
            )


@pytest.mark.asyncio
async def test_anthropic_judge_oauth_happy_path(monkeypatch):
    """When no API key is set but an OAuth token is available, use Bearer auth."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"content": [{"type": "text",
                                 "text": '{"score": 0.7, "classification": "mode_one"}'}]}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return FakeResp()

    with patch("tools.truth_detector.backends.get_oauth_token", return_value="oat01-fake-token"), \
         patch("tools.truth_detector.backends.httpx.AsyncClient", FakeClient):
        cfg = BackendConfig(name="anthropic", timeout_s=30, max_tokens=500)
        out = await anthropic_judge(
            backend_cfg=cfg, judge_model="claude-haiku-4-5-20251001",
            rendered_prompt="judge this",
        )

    assert out["score"] == 0.7
    assert out["classification"] == "mode_one"
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["authorization"] == "Bearer oat01-fake-token"
    assert captured["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    # Claude Code system prefix required for OAuth requests
    assert captured["body"]["system"][0]["text"].startswith("You are Claude Code")


@pytest.mark.asyncio
async def test_anthropic_judge_no_credential_raises(monkeypatch):
    """With no API key and no OAuth token, must raise BackendError."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("tools.truth_detector.backends.get_oauth_token", return_value=None):
        cfg = BackendConfig(name="anthropic", timeout_s=30, max_tokens=500)
        with pytest.raises(BackendError, match="no anthropic credential"):
            await anthropic_judge(
                backend_cfg=cfg, judge_model="claude-haiku-4-5-20251001",
                rendered_prompt="judge this",
            )
