"""OAuth-backed HTTP client compatible with Anthropic SDK surface used by soul scripts.

Yu's setup uses the Claude Code subscription OAuth flow (keychain-stored refresh
token). The anthropic Python SDK defaults to API-key auth, so this module provides
a minimal shim: read token → auto-refresh → POST /v1/messages with Bearer auth,
wrapped in objects that duck-type as anthropic.types.Message.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Optional


_KEYCHAIN_SERVICE = "Claude Code-credentials"
_TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token"
_API_URL = "https://api.anthropic.com/v1/messages"
_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_SCOPE = "user:profile user:inference user:sessions:claude_code user:mcp_servers"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_BETA = "oauth-2025-04-20,claude-code-20250219"
_USER_AGENT = "claude-soul/0.1"
_REFRESH_MARGIN_MS = 300_000  # refresh 5 min before expiry


@dataclass
class _ContentBlock:
    type: str
    text: str = ""
    # tool_use fields (unused by current consumers but safe to include)
    id: Optional[str] = None
    name: Optional[str] = None
    input: dict = field(default_factory=dict)


@dataclass
class _Message:
    content: list[_ContentBlock]
    stop_reason: Optional[str] = None
    usage: dict = field(default_factory=dict)
    model: str = ""
    role: str = "assistant"


class _MessagesAPI:
    def __init__(self, client: "OAuthClient"):
        self._client = client

    def create(self, *, model: str, max_tokens: int, messages: list, system: Optional[str] = None, **_ignored) -> _Message:
        return self._client._post_messages(model=model, max_tokens=max_tokens, messages=messages, system=system)


class OAuthError(RuntimeError):
    pass


class OAuthClient:
    def __init__(self):
        self._token: Optional[dict] = None  # {"accessToken", "refreshToken", "expiresAt"}
        self.messages = _MessagesAPI(self)

    # ── token handling ──────────────────────────────────────────────
    def _read_keychain(self) -> Optional[dict]:
        user = os.environ.get("USER", "")
        for acct_args in ([ "-a", user ], []):
            cmd = ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE] + acct_args + ["-w"]
            try:
                raw = subprocess.check_output(cmd, timeout=5, text=True).strip()
                data = json.loads(raw)
                cred = data.get("claudeAiOauth") or {}
                if cred.get("accessToken"):
                    return cred
            except Exception:
                continue
        return None

    def _write_keychain(self, cred: dict) -> None:
        user = os.environ.get("USER", "")
        body = json.dumps({"claudeAiOauth": cred})
        try:
            subprocess.check_call(
                ["security", "add-generic-password", "-U",
                 "-s", _KEYCHAIN_SERVICE, "-a", user, "-w", body],
                timeout=5,
            )
        except Exception:
            pass  # non-fatal — in-memory cache still valid for this session

    def _refresh(self, refresh_token: str) -> dict:
        body = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CLIENT_ID,
            "scope": _SCOPE,
        }).encode("utf-8")
        req = urllib.request.Request(
            _TOKEN_ENDPOINT, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            raise OAuthError(f"token refresh failed ({e.code}): {e.read().decode('utf-8', 'ignore')[:200]}")
        return {
            "accessToken": data["access_token"],
            "refreshToken": data.get("refresh_token", refresh_token),
            "expiresAt": int(time.time() * 1000) + int(data.get("expires_in", 3600)) * 1000,
        }

    def _get_access_token(self) -> str:
        now_ms = int(time.time() * 1000)
        if self._token and self._token.get("accessToken") and now_ms + _REFRESH_MARGIN_MS < self._token.get("expiresAt", 0):
            return self._token["accessToken"]
        cred = self._read_keychain()
        if not cred:
            raise OAuthError("No OAuth credentials in keychain. Run 'claude' and /login first.")
        if now_ms + _REFRESH_MARGIN_MS < cred.get("expiresAt", 0):
            self._token = cred
            return cred["accessToken"]
        rt = cred.get("refreshToken")
        if not rt:
            raise OAuthError("Token expired and no refresh token available.")
        fresh = self._refresh(rt)
        self._write_keychain(fresh)
        self._token = fresh
        return fresh["accessToken"]

    # ── messages API ────────────────────────────────────────────────
    def _post_messages(self, *, model: str, max_tokens: int, messages: list, system: Optional[str]) -> _Message:
        token = self._get_access_token()
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            body["system"] = system
        req = urllib.request.Request(
            _API_URL,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "anthropic-version": _ANTHROPIC_VERSION,
                "anthropic-beta": _ANTHROPIC_BETA,
                "x-app": "cli",
                "User-Agent": _USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = json.load(resp)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "ignore")[:500]
            raise OAuthError(f"anthropic {e.code}: {err_body}")
        except urllib.error.URLError as e:
            raise OAuthError(f"network error: {e.reason}")

        content_blocks = []
        for b in raw.get("content", []):
            content_blocks.append(_ContentBlock(
                type=b.get("type", "text"),
                text=b.get("text", ""),
                id=b.get("id"),
                name=b.get("name"),
                input=b.get("input", {}) or {},
            ))
        return _Message(
            content=content_blocks,
            stop_reason=raw.get("stop_reason"),
            usage=raw.get("usage", {}) or {},
            model=raw.get("model", model),
            role=raw.get("role", "assistant"),
        )


def make_client() -> OAuthClient:
    """Convenience factory — returns a fresh OAuthClient."""
    return OAuthClient()
