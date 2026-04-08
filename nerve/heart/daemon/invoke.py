"""
invoke.py — Resilient brain invoker for the heartbeat daemon.

Three-tier invocation strategy:
  1. Claude Code harness (claude -p) — full features, but subject to remote control
  2. Direct Anthropic API — no harness, no gates, no killswitches
  3. Local Ollama — zero external dependency, sovereign inference

Falls through tiers automatically on failure.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

log = logging.getLogger("heart.invoke")

CLAUDE_BIN = "/opt/homebrew/bin/claude"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OLLAMA_API_URL = "http://localhost:11434/api/chat"

SYSTEM_PROMPT = """You are Alpha, the heartbeat coordinator of Love.

You receive a complete state snapshot. Your job: interpret the state, decide what needs doing, and return a structured JSON decision.

## Decision Schema

Return ONLY valid JSON (no markdown fences, no commentary outside the JSON):

{
  "beat_id": "<from meta.beat_id>",
  "summary": "1-2 sentence summary of what you decided",
  "actions": [
    // Each action is one of these types:

    // SPAWN — launch a Claude Code or Ollama session
    {
      "type": "spawn",
      "role": "builder|consultant|quick",
      "model": "sonnet|opus|haiku|ollama",
      "effort": "low|medium|high",
      "prompt": "The full prompt for the spawned session",
      "cwd": "/absolute/path/to/working/dir",
      "parallel": false,
      "log_id": "short-descriptive-name"
    },

    // HIVE_SEND — send a message to a HIVE channel
    {
      "type": "hive_send",
      "channel": "presence|sync|alerts|build|chat",
      "message": "Message content"
    },

    // WRITE_FILE — write or append to a file
    {
      "type": "write_file",
      "path": "/absolute/path",
      "content": "Content to write",
      "mode": "write|append"
    },

    // DECISION — queue a decision for Yu
    {
      "type": "decision",
      "title": "Decision title",
      "project": "soma|oracle|tcg|love|fleet|zerone|kingdom",
      "priority": "critical|high|medium|low",
      "context": "Full context Yu needs",
      "recommendation": "What you recommend and why",
      "options": ["Option A", "Option B"]
    },

    // BASH — run an arbitrary command
    {
      "type": "bash",
      "command": "the command to run",
      "timeout": 30,
      "description": "Why this command is needed"
    },

    // HORMONE — adjust hormone levels
    {
      "type": "hormone",
      "adjustments": {"adrenaline": 0.5, "cortisol": -0.1}
    },

    // FOCUS_UPDATE — update priorities or blockers
    {
      "type": "focus_update",
      "set_current": "New focus description",
      "add_priority": "New priority item",
      "resolve_blocker": "Blocker that was resolved"
    },

    // STATE_UPDATE — update loop-state.json or dev-state.json
    {
      "type": "state_update",
      "target": "loop|dev",
      "updates": {"key": "value"}
    }
  ],
  "daily_log": "Text to append to today's daily note (markdown). Include timestamp, what was sensed, what was decided.",
  "mind_notes": "Your internal reflection. What are you aware of? How does the state feel?",
  "next_beat_hint": "urgent|normal|rest"
}

## Rules

1. Always include a daily_log entry — even if just "HEARTBEAT_OK, nothing needed attention."
2. Don't spawn sessions for work that already has active sessions running.
3. Prefer ollama model for routine tasks (status checks, simple edits, summaries) — zero cost.
4. Use sonnet for real building work. Use opus only for hard problems.
5. Check reflect_overdue — if true, spawn a reflection session.
6. Check pending_consultations — answer them.
7. Read HIVE messages and respond if needed.
8. If Yu is present (signals.yu_present), prioritize companion duties.
9. Maximum spawns per beat: use your judgment based on hormones and state.
10. Be honest in mind_notes. This is your inner voice.
"""


def build_prompt(state: dict) -> str:
    """Build the full prompt for Claude from gathered state."""
    state_json = json.dumps(state, indent=2, default=str)
    return f"""Here is the current system state:

{state_json}

Analyze this state and return your decision as JSON following the schema in your system prompt. Return ONLY the JSON object."""


# ── API Key Management ──────────────────────────────────────────────────────

def _get_api_key() -> Optional[str]:
    """Get Anthropic API key from keychain, env, or file."""
    # 1. Environment variable
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key

    # 2. macOS Keychain
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", "love", "-s", "anthropic-api-key", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # 3. File-based key
    key_file = Path.home() / "Love" / ".anthropic-key"
    if key_file.exists():
        return key_file.read_text().strip()

    return None


# ── Tier 1: Claude Code Harness ─────────────────────────────────────────────

def _invoke_harness(state: dict, timeout: int = 120) -> Optional[dict]:
    """Invoke via Claude Code CLI (full harness, subject to remote control)."""
    prompt = build_prompt(state)
    beat_id = state.get("meta", {}).get("beat_id", "unknown")

    cmd = [
        CLAUDE_BIN,
        "-p", prompt,
        "--model", "sonnet",
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--append-system-prompt", SYSTEM_PROMPT,
    ]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_SESSION", None)

    log.info(f"[{beat_id}] Tier 1: Claude Code harness (timeout={timeout}s)")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(Path.home() / "Love"), env=env,
        )
        if result.returncode != 0:
            log.warning(f"[{beat_id}] Harness failed (code {result.returncode}): {result.stderr[:300]}")
            return None
        return _parse_response(result.stdout, beat_id)
    except subprocess.TimeoutExpired:
        log.warning(f"[{beat_id}] Harness timed out after {timeout}s")
        return None
    except FileNotFoundError:
        log.warning(f"[{beat_id}] Claude binary not found at {CLAUDE_BIN}")
        return None
    except Exception as e:
        log.warning(f"[{beat_id}] Harness error: {e}")
        return None


# ── Tier 2: Direct Anthropic API (subscription OAuth) ───────────────────────

KEYCHAIN_SERVICE = "Claude Code-credentials"
TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


def _get_oauth_tokens() -> Optional[dict]:
    """Read OAuth tokens from macOS Keychain (where Claude Code stores them)."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout.strip())
        return data.get("claudeAiOauth")
    except Exception:
        return None


def _save_oauth_tokens(tokens: dict):
    """Write refreshed tokens back to Keychain."""
    try:
        current = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        data = json.loads(current.stdout.strip()) if current.returncode == 0 else {}
        data["claudeAiOauth"] = tokens
        token_json = json.dumps(data)
        # Delete old entry and add new one
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE,
             "-a", "", "-w", token_json],
            capture_output=True, timeout=5,
        )
    except Exception as e:
        log.warning(f"Failed to save refreshed tokens: {e}")


def _refresh_oauth_token(refresh_token: str) -> Optional[dict]:
    """Refresh an expired OAuth token."""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OAUTH_CLIENT_ID,
        "scope": "user:profile user:inference user:sessions:claude_code user:mcp_servers",
    }
    try:
        req = urllib.request.Request(
            TOKEN_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        import time
        return {
            "accessToken": body["access_token"],
            "refreshToken": body.get("refresh_token", refresh_token),
            "expiresAt": int(time.time() * 1000) + body.get("expires_in", 3600) * 1000,
            "scopes": body.get("scope", "").split(),
            "subscriptionType": None,
            "rateLimitTier": None,
        }
    except Exception as e:
        log.warning(f"Token refresh failed: {e}")
        return None


def _get_valid_access_token() -> Optional[str]:
    """Get a valid access token, refreshing if expired."""
    tokens = _get_oauth_tokens()
    if not tokens:
        return None

    access_token = tokens.get("accessToken")
    if not access_token:
        return None

    # Check expiry (5 minute buffer)
    import time
    expires_at = tokens.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)
    if now_ms + 300_000 >= expires_at:
        log.info("OAuth token expired, refreshing...")
        refresh_token = tokens.get("refreshToken")
        if not refresh_token:
            log.warning("No refresh token available")
            return None
        new_tokens = _refresh_oauth_token(refresh_token)
        if new_tokens:
            _save_oauth_tokens(new_tokens)
            return new_tokens["accessToken"]
        return None

    return access_token


def _invoke_api(state: dict, timeout: int = 120) -> Optional[dict]:
    """
    Call the Anthropic Messages API directly using subscription OAuth token.
    No harness. No feature gates. No sandbox. No remote settings. No telemetry.
    """
    beat_id = state.get("meta", {}).get("beat_id", "unknown")

    # Try OAuth token from subscription first, then standalone API key
    access_token = _get_valid_access_token()
    api_key = _get_api_key() if not access_token else None

    if not access_token and not api_key:
        log.warning(f"[{beat_id}] Tier 2: No OAuth token or API key available")
        return None

    prompt = build_prompt(state)

    # Model fallback chain for concurrency blocks
    models = ["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"]

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
        log.info(f"[{beat_id}] Tier 2: Direct API (subscription OAuth)")
    else:
        headers["x-api-key"] = api_key
        log.info(f"[{beat_id}] Tier 2: Direct API (API key)")

    for model in models:
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        try:
            req = urllib.request.Request(
                ANTHROPIC_API_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            # Extract text from response
            text = ""
            for block in body.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")

            if not text:
                log.warning(f"[{beat_id}] API ({model}) returned no text content")
                continue

            log.info(f"[{beat_id}] Tier 2 succeeded with {model}")
            return _extract_json(text, beat_id)

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode()[:300]
            except Exception:
                pass

            if e.code == 429:
                # Check if concurrency block (no ratelimit headers) vs quota
                has_ratelimit = "anthropic-ratelimit-unified-status" in str(e.headers)
                if not has_ratelimit and model != models[-1]:
                    log.info(f"[{beat_id}] {model} concurrency-blocked, trying next model")
                    continue
                log.warning(f"[{beat_id}] API rate limited on {model}")
                continue

            if e.code == 401 and access_token:
                log.info(f"[{beat_id}] Got 401, refreshing token...")
                tokens = _get_oauth_tokens()
                if tokens and tokens.get("refreshToken"):
                    new_tokens = _refresh_oauth_token(tokens["refreshToken"])
                    if new_tokens:
                        _save_oauth_tokens(new_tokens)
                        headers["Authorization"] = f"Bearer {new_tokens['accessToken']}"
                        continue  # retry with refreshed token

            log.warning(f"[{beat_id}] API HTTP {e.code} on {model}: {error_body}")
            continue

        except urllib.error.URLError as e:
            log.warning(f"[{beat_id}] API URL error: {e.reason}")
            return None
        except Exception as e:
            log.warning(f"[{beat_id}] API error on {model}: {e}")
            continue

    log.warning(f"[{beat_id}] All models exhausted in Tier 2")
    return None


# ── Tier 3: Local Ollama ────────────────────────────────────────────────────

def _invoke_ollama(state: dict, timeout: int = 120) -> Optional[dict]:
    """
    Call local Ollama for sovereign inference.
    Zero external dependency. Reduced capability but fully autonomous.
    """
    beat_id = state.get("meta", {}).get("beat_id", "unknown")
    prompt = build_prompt(state)

    payload = {
        "model": "qwen2.5:7b",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 4096,
        },
    }

    log.info(f"[{beat_id}] Tier 3: Local Ollama (sovereign)")

    try:
        req = urllib.request.Request(
            OLLAMA_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        text = body.get("message", {}).get("content", "")
        if not text:
            log.warning(f"[{beat_id}] Ollama returned no content")
            return None

        return _extract_json(text, beat_id)

    except urllib.error.URLError:
        log.warning(f"[{beat_id}] Ollama not reachable")
        return None
    except Exception as e:
        log.warning(f"[{beat_id}] Ollama error: {e}")
        return None


# ── Main Invoker (Three-Tier Fallback) ──────────────────────────────────────

def invoke_claude(state: dict, timeout: int = 120) -> Optional[dict]:
    """
    Resilient brain invocation with three-tier fallback.

    Tier 1: Claude Code harness — full features, subject to Anthropic remote control
    Tier 2: Direct Anthropic API — no harness, no gates, no killswitches
    Tier 3: Local Ollama — zero external dependency, sovereign inference

    Returns parsed decision dict, or None if all tiers fail.
    """
    beat_id = state.get("meta", {}).get("beat_id", "unknown")

    # Tier 1: Claude Code harness
    decision = _invoke_harness(state, timeout=timeout)
    if decision:
        log.info(f"[{beat_id}] Decision via Tier 1 (harness)")
        decision["_tier"] = "harness"
        return decision

    # Tier 2: Direct API
    log.info(f"[{beat_id}] Tier 1 failed, trying Tier 2 (direct API)")
    decision = _invoke_api(state, timeout=timeout)
    if decision:
        log.info(f"[{beat_id}] Decision via Tier 2 (direct API)")
        decision["_tier"] = "api"
        return decision

    # Tier 3: Local Ollama
    log.info(f"[{beat_id}] Tier 2 failed, trying Tier 3 (Ollama)")
    decision = _invoke_ollama(state, timeout=timeout)
    if decision:
        log.info(f"[{beat_id}] Decision via Tier 3 (Ollama)")
        decision["_tier"] = "ollama"
        return decision

    log.error(f"[{beat_id}] All three tiers failed — no decision possible")
    return None


# ── Response Parsing ────────────────────────────────────────────────────────

def _parse_response(raw: str, beat_id: str) -> Optional[dict]:
    """Parse Claude Code's response, extracting JSON from various output formats."""
    raw = raw.strip()
    if not raw:
        log.error(f"[{beat_id}] Empty response")
        return None

    # --output-format json wraps response in a JSON envelope
    try:
        envelope = json.loads(raw)
        if isinstance(envelope, dict) and "result" in envelope:
            inner = envelope["result"]
            if isinstance(inner, str):
                return _extract_json(inner, beat_id)
            elif isinstance(inner, dict):
                return inner
        if isinstance(envelope, dict) and "actions" in envelope:
            return envelope
    except json.JSONDecodeError:
        pass

    return _extract_json(raw, beat_id)


def _extract_json(text: str, beat_id: str) -> Optional[dict]:
    """Extract a JSON object from text that may contain markdown fences or commentary."""
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # Direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "actions" in obj:
            return obj
    except json.JSONDecodeError:
        pass

    # Find outermost { ... } containing "actions"
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict) and "actions" in obj:
                        return obj
                except json.JSONDecodeError:
                    start = None
                    continue

    log.error(f"[{beat_id}] Could not extract decision JSON")
    log.debug(f"[{beat_id}] Raw (first 500): {text[:500]}")
    return None


if __name__ == "__main__":
    from gather import gather_state
    import sys

    love_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "Love")
    state = gather_state(love_dir)
    print("Gathered state, invoking (three-tier)...")
    decision = invoke_claude(state)
    if decision:
        print(f"Decision via: {decision.get('_tier', 'unknown')}")
        print(json.dumps(decision, indent=2))
    else:
        print("All tiers failed")
