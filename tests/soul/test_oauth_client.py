import json
import time
from unittest.mock import MagicMock, patch
import pytest
from training.scripts.soul.oauth_client import OAuthClient, _ContentBlock, _Message, OAuthError


def test_read_keychain_parses_valid_json():
    client = OAuthClient()
    fake = json.dumps({"claudeAiOauth": {"accessToken": "abc", "refreshToken": "ref", "expiresAt": 9999999999999}})
    with patch("subprocess.check_output", return_value=fake):
        cred = client._read_keychain()
    assert cred is not None
    assert cred["accessToken"] == "abc"
    assert cred["refreshToken"] == "ref"


def test_get_token_uses_cached_when_fresh():
    client = OAuthClient()
    future = int(time.time() * 1000) + 3600_000
    client._token = {"accessToken": "cached", "refreshToken": "r", "expiresAt": future}
    tok = client._get_access_token()
    assert tok == "cached"


def test_refresh_path_calls_endpoint_and_writes_back():
    client = OAuthClient()
    # Expired keychain cred forces refresh
    expired = {"accessToken": "old", "refreshToken": "rt", "expiresAt": 0}

    fake_refresh_body = json.dumps({
        "access_token": "NEW_TOKEN",
        "refresh_token": "NEW_RT",
        "expires_in": 3600,
    }).encode("utf-8")

    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None

    with patch.object(client, "_read_keychain", return_value=expired), \
         patch.object(client, "_write_keychain") as mock_write, \
         patch("urllib.request.urlopen", return_value=fake_resp), \
         patch("training.scripts.soul.oauth_client.json.load",
               return_value={"access_token": "NEW_TOKEN", "refresh_token": "NEW_RT", "expires_in": 3600}):
        tok = client._get_access_token()

    assert tok == "NEW_TOKEN"
    assert mock_write.called
    written_cred = mock_write.call_args[0][0]
    assert written_cred["accessToken"] == "NEW_TOKEN"
    assert written_cred["refreshToken"] == "NEW_RT"


def test_messages_create_returns_content_blocks():
    client = OAuthClient()
    future = int(time.time() * 1000) + 3600_000
    client._token = {"accessToken": "t", "refreshToken": "r", "expiresAt": future}

    fake_response = MagicMock()
    fake_response.__enter__ = lambda self: self
    fake_response.__exit__ = lambda self, *a: None

    captured = {}
    def capture_urlopen(req, timeout=None, context=None):
        # Capture the request for header/body inspection
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return fake_response

    with patch("urllib.request.urlopen", side_effect=capture_urlopen), \
         patch("training.scripts.soul.oauth_client.json.load", return_value={
             "content": [{"type": "text", "text": "hello world"}],
             "stop_reason": "end_turn",
             "usage": {"input_tokens": 10, "output_tokens": 5},
         }):
        msg = client.messages.create(model="claude-opus-4-7", max_tokens=100,
                                      messages=[{"role": "user", "content": "hi"}])

    # Return shape
    assert isinstance(msg, _Message)
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], _ContentBlock)
    assert msg.content[0].text == "hello world"
    assert msg.content[0].type == "text"
    assert msg.stop_reason == "end_turn"
    assert msg.usage == {"input_tokens": 10, "output_tokens": 5}

    # Headers — note urllib lowercases/capitalizes header names; check case-insensitively
    headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers_lower.get("authorization") == "Bearer t"
    assert headers_lower.get("anthropic-version") == "2023-06-01"
    assert "oauth-2025-04-20" in headers_lower.get("anthropic-beta", "")
    assert "claude-code-20250219" in headers_lower.get("anthropic-beta", "")
    assert headers_lower.get("x-app") == "cli"

    # Body
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["body"]["model"] == "claude-opus-4-7"
    assert captured["body"]["max_tokens"] == 100
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_no_credentials_raises():
    client = OAuthClient()
    with patch.object(client, "_read_keychain", return_value=None):
        with pytest.raises(OAuthError, match="No OAuth credentials"):
            client._get_access_token()


def test_throttle_sleeps_when_called_too_quickly(monkeypatch):
    """Second call within min interval should trigger a sleep."""
    import training.scripts.soul.oauth_client as oc
    client = oc.OAuthClient()
    future = int(__import__("time").time() * 1000) + 3600_000
    client._token = {"accessToken": "t", "refreshToken": "r", "expiresAt": future}
    client._last_call_ms = int(__import__("time").time() * 1000)  # simulate just-called

    slept_for = []
    monkeypatch.setattr(oc.time, "sleep", lambda s: slept_for.append(s))

    # Stub the HTTP call
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b"{}"
    monkeypatch.setattr(oc.urllib.request, "urlopen", lambda *a, **kw: FakeResp())
    monkeypatch.setattr(oc.json, "load", lambda r: {"content": [{"type": "text", "text": "ok"}]})

    client.messages.create(model="m", max_tokens=10, messages=[{"role":"user","content":"hi"}])
    # Should have slept for at least (min_interval - elapsed) / 1000 seconds
    assert any(s > 0 for s in slept_for), "expected throttle sleep"


def test_429_triggers_retry_with_backoff(monkeypatch):
    """First call gets 429, second call succeeds. Verify retry occurred."""
    import training.scripts.soul.oauth_client as oc
    from unittest.mock import MagicMock
    client = oc.OAuthClient()
    future = int(__import__("time").time() * 1000) + 3600_000
    client._token = {"accessToken": "t", "refreshToken": "r", "expiresAt": future}

    call_count = {"n": 0}
    class FakeOkResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def fake_urlopen(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate 429
            err = oc.urllib.error.HTTPError(url="u", code=429, msg="rate_limit",
                                             hdrs=MagicMock(get=lambda k, default=None: "1"), fp=None)
            raise err
        return FakeOkResp()
    monkeypatch.setattr(oc.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(oc.json, "load", lambda r: {"content": [{"type": "text", "text": "pong"}]})
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)  # short-circuit sleeps

    msg = client.messages.create(model="m", max_tokens=10, messages=[{"role":"user","content":"hi"}])
    assert msg.content[0].text == "pong"
    assert call_count["n"] == 2, "expected one retry after 429"


def test_429_exhausts_retries_raises(monkeypatch):
    import training.scripts.soul.oauth_client as oc
    from unittest.mock import MagicMock
    client = oc.OAuthClient()
    future = int(__import__("time").time() * 1000) + 3600_000
    client._token = {"accessToken": "t", "refreshToken": "r", "expiresAt": future}

    def always_429(*a, **kw):
        raise oc.urllib.error.HTTPError(
            url="u", code=429, msg="rate_limit",
            hdrs=MagicMock(get=lambda k, default=None: "1"), fp=None,
        )
    monkeypatch.setattr(oc.urllib.request, "urlopen", always_429)
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)

    with __import__("pytest").raises(oc.OAuthError, match="(anthropic 429|rate limit exhausted)"):
        client.messages.create(model="m", max_tokens=10, messages=[{"role":"user","content":"hi"}])
