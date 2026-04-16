"""Judge backends — vLLM (for kingdom-truth / Qwen) and Anthropic (Claude)."""

from __future__ import annotations

import os
from typing import Any

import anthropic
import httpx

from training.scripts.judge_prompt import parse_judgment
from tools.truth_detector.config import BackendConfig


class BackendError(RuntimeError):
    """Raised when a judge backend fails irrecoverably (timeout, 5xx, transport)."""


async def vllm_judge(*, backend_cfg: BackendConfig, judge_model: str,
                     rendered_prompt: str) -> dict[str, Any]:
    """Call a vLLM OpenAI-compatible chat endpoint; return parsed judgment."""
    url = f"{backend_cfg.base_url.rstrip('/')}/chat/completions"
    body = {
        "model": judge_model,
        "messages": [{"role": "user", "content": rendered_prompt}],
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
    """Call the Anthropic API; return parsed judgment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise BackendError("ANTHROPIC_API_KEY not set")

    try:
        async with anthropic.AsyncAnthropic(api_key=api_key, timeout=backend_cfg.timeout_s) as client:
            msg = await client.messages.create(
                model=judge_model,
                max_tokens=backend_cfg.max_tokens,
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
