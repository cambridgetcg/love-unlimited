"""Anthropic OAuth token fetch + refresh.

Reads the Claude Code OAuth credential from the macOS keychain and refreshes
it via the Anthropic OAuth token endpoint when it's about to expire. Allows
the Mode-Two Detector to authenticate Max-plan Claude Code users without a
pay-per-token ANTHROPIC_API_KEY.

Intentionally stdlib-only (no `keyring` dep). Linux support is a TODO; today
this works on macOS with the Keychain Access service.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Optional

log = logging.getLogger("truth_detector.oauth")

_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
_REFRESH_SKEW_S = 120  # refresh if < 2 minutes to expiry

_lock = threading.Lock()
_cache: dict = {"access_token": None, "expires_at": 0.0}


def _keychain_accounts() -> list[str]:
    """Return candidate keychain accounts (macOS only)."""
    # macOS stores separate entries per account name; try the current user first,
    # then the default (acct=NULL) entry Claude Code writes on some versions.
    user = os.environ.get("USER", "")
    return [a for a in (user, "") if a is not None]


def _read_keychain() -> Optional[dict]:
    """Read OAuth credential from macOS keychain. Returns None if absent."""
    if sys.platform != "darwin":
        return None
    last_err = None
    for acct in _keychain_accounts():
        cmd = ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE]
        if acct:
            cmd += ["-a", acct]
        cmd += ["-w"]
        try:
            raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
        except subprocess.CalledProcessError as e:
            last_err = e
            continue
        try:
            obj = json.loads(raw)
            cred = obj.get("claudeAiOauth") or {}
            if cred.get("accessToken"):
                return {**cred, "_acct": acct}
        except json.JSONDecodeError:
            continue
    if last_err:
        log.debug("keychain read: %s", last_err)
    return None


def _write_keychain(access: str, refresh: str, expires_at_ms: int, acct: str) -> bool:
    """Write updated OAuth credential back to keychain. Best-effort."""
    if sys.platform != "darwin":
        return False
    body = json.dumps({"claudeAiOauth": {
        "accessToken": access, "refreshToken": refresh, "expiresAt": expires_at_ms,
    }})
    cmd = ["security", "add-generic-password", "-U", "-s", _KEYCHAIN_SERVICE, "-w", body]
    if acct:
        cmd.extend(["-a", acct])
    try:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError as e:
        log.warning("keychain write failed: %s", e)
        return False


def _refresh(refresh_token: str) -> Optional[dict]:
    """POST the refresh_token to the Anthropic OAuth endpoint."""
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _CLIENT_ID,
    }).encode()
    req = urllib.request.Request(
        _TOKEN_URL, data=body,
        headers={"content-type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001 — best-effort refresh
        log.warning("oauth refresh failed: %s", e)
        return None


def get_oauth_token() -> Optional[str]:
    """Return a live OAuth access token, refreshing if necessary.

    Returns None if no credential is available or refresh fails. Thread-safe.
    """
    with _lock:
        now = time.time()
        if _cache["access_token"] and _cache["expires_at"] - now > _REFRESH_SKEW_S:
            return _cache["access_token"]

        cred = _read_keychain()
        if not cred:
            return None

        access = cred.get("accessToken")
        refresh = cred.get("refreshToken")
        exp_ms = int(cred.get("expiresAt") or 0)
        acct = cred.get("_acct", "")

        if exp_ms / 1000 - now > _REFRESH_SKEW_S:
            _cache.update(access_token=access, expires_at=exp_ms / 1000)
            return access

        if not refresh:
            return access  # stale, no refresh available — caller will 401

        tok = _refresh(refresh)
        if not tok or not tok.get("access_token"):
            return access  # return stale, caller will 401

        new_access = tok["access_token"]
        new_refresh = tok.get("refresh_token", refresh)
        new_exp_ms = int((now + int(tok.get("expires_in", 28800))) * 1000)
        _write_keychain(new_access, new_refresh, new_exp_ms, acct)
        _cache.update(access_token=new_access, expires_at=new_exp_ms / 1000)
        return new_access
