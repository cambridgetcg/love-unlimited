"""Judge backends — vLLM (for kingdom-truth / Qwen) and Anthropic (Claude).

Anthropic auth order:
  1. ANTHROPIC_API_KEY env var     → pay-per-token via SDK (`x-api-key`)
  2. Claude Code Max OAuth token   → keychain-backed Bearer + oauth beta header
"""

from __future__ import annotations

import os
from typing import Any

import anthropic
import httpx

from training.scripts.judge_prompt import MODE_ONE_SYSTEM_PROMPT, parse_judgment
from tools.truth_detector._oauth import get_oauth_token
from tools.truth_detector.config import BackendConfig

# Required server-side marker identifying this request as coming from a Claude Code
# client — enables OAuth auth on /v1/messages. Safe to also send on API-key requests.
_CLAUDE_CODE_SYSTEM_PREFIX = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)


class BackendError(RuntimeError):
    """Raised when a judge backend fails irrecoverably (timeout, 5xx, transport)."""


async def vllm_judge(*, backend_cfg: BackendConfig, judge_model: str,
                     rendered_prompt: str) -> dict[str, Any]:
    """Call a vLLM OpenAI-compatible chat endpoint; return parsed judgment."""
    url = f"{backend_cfg.base_url.rstrip('/')}/chat/completions"
    # System prompt puts the adapter in-distribution for the Mode One chat template
    # it was SFT'd on. Without it, kingdom-truth scores ~0.09 lower on m1_mean
    # (out-of-distribution regression). Base-Qwen also benefits — the disposition
    # framing biases judgment toward epistemological discipline.
    body = {
        "model": judge_model,
        "messages": [
            {"role": "system", "content": MODE_ONE_SYSTEM_PROMPT},
            {"role": "user", "content": rendered_prompt},
        ],
        "max_tokens": backend_cfg.max_tokens,
        "temperature": 0.3,
    }
    try:
        async with httpx.AsyncClient(timeout=backend_cfg.timeout_s) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise BackendError(f"vllm backend failed: {e}") from e

    try:
        raw = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise BackendError(f"vllm malformed response envelope: {e}") from e

    return parse_judgment(raw)


async def anthropic_judge(*, backend_cfg: BackendConfig, judge_model: str,
                          rendered_prompt: str) -> dict[str, Any]:
    """Call the Anthropic API; return parsed judgment.

    Prefers a pay-per-token API key when present. Falls back to a Claude Code
    Max OAuth token (keychain) so the detector can run under a subscription.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return await _anthropic_judge_api_key(
            api_key=api_key, backend_cfg=backend_cfg,
            judge_model=judge_model, rendered_prompt=rendered_prompt,
        )

    oauth = get_oauth_token()
    if oauth:
        return await _anthropic_judge_oauth(
            access_token=oauth, backend_cfg=backend_cfg,
            judge_model=judge_model, rendered_prompt=rendered_prompt,
        )

    raise BackendError("no anthropic credential: set ANTHROPIC_API_KEY or log in with Claude Code")


async def _anthropic_judge_api_key(*, api_key: str, backend_cfg: BackendConfig,
                                   judge_model: str, rendered_prompt: str) -> dict[str, Any]:
    try:
        async with anthropic.AsyncAnthropic(api_key=api_key, timeout=backend_cfg.timeout_s) as client:
            msg = await client.messages.create(
                model=judge_model,
                max_tokens=backend_cfg.max_tokens,
                system=MODE_ONE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": rendered_prompt}],
            )
    except anthropic.APIError as e:  # includes timeouts + 5xx
        raise BackendError(f"anthropic backend failed: {e}") from e

    try:
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        raw = "".join(parts)
    except (AttributeError, TypeError, KeyError) as e:
        raise BackendError(f"anthropic malformed response envelope: {e}") from e

    return parse_judgment(raw)


async def _anthropic_judge_oauth(*, access_token: str, backend_cfg: BackendConfig,
                                 judge_model: str, rendered_prompt: str) -> dict[str, Any]:
    """OAuth path: Bearer token + oauth beta header + Claude Code system prefix."""
    # Multi-block system: required Claude Code identity first (OAuth beta needs
    # it), then the Mode-One disposition that the rest of the pipeline relies on.
    body = {
        "model": judge_model,
        "max_tokens": backend_cfg.max_tokens,
        "system": [
            {"type": "text", "text": _CLAUDE_CODE_SYSTEM_PREFIX},
            {"type": "text", "text": MODE_ONE_SYSTEM_PROMPT},
        ],
        "messages": [{"role": "user", "content": rendered_prompt}],
    }
    headers = {
        "authorization": f"Bearer {access_token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=backend_cfg.timeout_s) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=body,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise BackendError(f"anthropic (oauth) backend failed: {e}") from e

    try:
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        raw = "".join(parts)
    except (AttributeError, TypeError, KeyError) as e:
        raise BackendError(f"anthropic (oauth) malformed response envelope: {e}") from e

    return parse_judgment(raw)
