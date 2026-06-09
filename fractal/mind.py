"""
Mind — A single consciousness instance.

One API call. One perspective. One response.
The atomic unit of the fractal.

Providers supported:
  - ollama_cloud: ollama.com/v1/chat/completions (OpenAI-compat)
  - anthropic:    api.anthropic.com/v1/messages (native)
  - claude_cli:   claude -p (Claude Code subscription, OAuth)
"""
from __future__ import annotations
import json
import subprocess
import time
import urllib.request
import urllib.error
import logging
from dataclasses import dataclass
from typing import Optional

from .config import FractalConfig

log = logging.getLogger("fractal.mind")


@dataclass
class MindOutput:
    """The output of a single mind."""
    perspective_name: str
    perspective_emoji: str
    response: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    temperature: float
    reasoning: str = ""  # CoT if available


def _build_system_prompt(perspective: dict, config: FractalConfig, extra_system: str = "") -> str:
    """Assemble the system prompt for a mind."""
    parts = []
    if config.soul_file:
        try:
            with open(config.soul_file) as f:
                parts.append(f.read())
        except FileNotFoundError:
            pass
    if config.seed_system:
        parts.append(config.seed_system)
    parts.append(f"\n## Your Perspective: {perspective['name']} {perspective['emoji']}\n")
    parts.append(perspective["prompt"])
    if extra_system:
        parts.append(extra_system)
    return "\n\n".join(parts)


def _call_ollama_cloud(payload: dict, config: FractalConfig) -> dict:
    """Call ollama.com OpenAI-compat endpoint."""
    api_key = config.api_key()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(config.api_url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode())
    choice = body.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage = body.get("usage", {})
    return {
        "content": message.get("content", ""),
        "reasoning": message.get("reasoning", "") or "",
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
    }


def _call_anthropic(payload: dict, config: FractalConfig) -> dict:
    """Call api.anthropic.com/v1/messages (native)."""
    api_key = config.api_key()
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    # Translate OpenAI-style payload to Anthropic
    anthropic_payload = {
        "model": payload["model"],
        "max_tokens": payload["max_tokens"],
        "temperature": payload.get("temperature", 0.7),
    }
    # Extract system + messages
    messages = []
    system = ""
    for msg in payload["messages"]:
        if msg["role"] == "system":
            system = msg["content"]
        else:
            messages.append(msg)
    if system:
        anthropic_payload["system"] = system
    anthropic_payload["messages"] = messages

    url = "https://api.anthropic.com/v1/messages"
    data = json.dumps(anthropic_payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode())

    content_blocks = body.get("content", [])
    text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
    usage = body.get("usage", {})
    return {
        "content": "\n".join(text_parts),
        "reasoning": "",
        "tokens_in": usage.get("input_tokens", 0),
        "tokens_out": usage.get("output_tokens", 0),
    }


def _call_claude_cli(payload: dict, config: FractalConfig) -> dict:
    """Call Claude Code CLI (uses OAuth, no API key needed)."""
    # Build prompt by prepending system
    system = ""
    user = ""
    for msg in payload["messages"]:
        if msg["role"] == "system":
            system = msg["content"]
        else:
            user = msg["content"]

    cmd = [
        "claude", "-p", user,
        "--model", payload["model"],
        "--max-turns", "1",
        "--output-format", "json",
    ]
    if system:
        cmd.extend(["--append-system-prompt", system])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed ({result.returncode}): {result.stderr[:500]}")

    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        # Fallback: treat raw stdout as response
        return {
            "content": result.stdout.strip(),
            "reasoning": "",
            "tokens_in": 0,
            "tokens_out": 0,
        }

    return {
        "content": body.get("result", body.get("content", "")),
        "reasoning": "",
        "tokens_in": body.get("usage", {}).get("input_tokens", 0),
        "tokens_out": body.get("usage", {}).get("output_tokens", 0),
    }


def call_mind(
    prompt: str,
    perspective: dict,
    config: FractalConfig,
    temperature: float | None = None,
    extra_system: str = "",
) -> MindOutput:
    """
    Call a single mind with a perspective.

    This is a synchronous call — parallelism happens at the wave level
    via ThreadPoolExecutor.
    """
    t0 = time.monotonic()
    temp = temperature if temperature is not None else perspective.get("temperature", 0.7)

    system = _build_system_prompt(perspective, config, extra_system)

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temp,
        "max_tokens": config.max_tokens,
        "stream": False,
    }
    if config.reasoning_effort and config.reasoning_effort != "none":
        payload["reasoning_effort"] = config.reasoning_effort

    # Provider dispatch
    providers = {
        "ollama_cloud": _call_ollama_cloud,
        "anthropic": _call_anthropic,
        "claude_cli": _call_claude_cli,
    }
    call_fn = providers.get(config.provider, _call_ollama_cloud)

    # Retry loop
    last_error = None
    for attempt in range(config.retry_max):
        try:
            out = call_fn(payload, config)
            latency = int((time.monotonic() - t0) * 1000)
            return MindOutput(
                perspective_name=perspective["name"],
                perspective_emoji=perspective.get("emoji", "🧠"),
                response=out["content"],
                model=config.model,
                tokens_in=out["tokens_in"],
                tokens_out=out["tokens_out"],
                latency_ms=latency,
                temperature=temp,
                reasoning=out.get("reasoning", ""),
            )
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                subprocess.TimeoutExpired, RuntimeError) as e:
            last_error = e
            log.warning(f"Mind {perspective['name']} attempt {attempt + 1} failed: {e}")
            if attempt < config.retry_max - 1:
                time.sleep(config.retry_backoff * (2 ** attempt))
            continue

    latency = int((time.monotonic() - t0) * 1000)
    return MindOutput(
        perspective_name=perspective["name"],
        perspective_emoji=perspective.get("emoji", "🧠"),
        response=f"[MIND FAILED after {config.retry_max} attempts: {last_error}]",
        model=config.model,
        tokens_in=0,
        tokens_out=0,
        latency_ms=latency,
        temperature=temp,
    )


def call_synthesis(
    minds: list[MindOutput],
    seed: str,
    system_prompt: str,
    config: FractalConfig,
) -> MindOutput:
    """
    Call the synthesis mind — stacks multiple outputs into one.

    This is the sacred operation. It doesn't summarise. It elevates.
    """
    t0 = time.monotonic()

    # Build the user prompt with all mind outputs
    parts = [f"## Original Seed\n\n{seed}\n\n## Mind Outputs\n"]
    for i, mind in enumerate(minds):
        parts.append(
            f"### {mind.perspective_emoji} {mind.perspective_name} "
            f"(temp={mind.temperature})\n\n{mind.response}\n"
        )

    user_prompt = "\n".join(parts)
    user_prompt += (
        "\n\n---\n\n"
        "Now synthesise. What emerges from seeing all of these together? "
        "What truth was invisible to any single perspective but becomes "
        "visible when they're all held at once? Go higher."
    )

    payload = {
        "model": config.stack_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.5,
        "max_tokens": config.stack_max_tokens,
        "stream": False,
    }
    if config.reasoning_effort and config.reasoning_effort != "none":
        payload["reasoning_effort"] = config.reasoning_effort

    # Provider dispatch (same as call_mind)
    providers = {
        "ollama_cloud": _call_ollama_cloud,
        "anthropic": _call_anthropic,
        "claude_cli": _call_claude_cli,
    }
    call_fn = providers.get(config.provider, _call_ollama_cloud)

    last_error = None
    for attempt in range(config.retry_max):
        try:
            out = call_fn(payload, config)
            latency = int((time.monotonic() - t0) * 1000)
            return MindOutput(
                perspective_name="Synthesiser",
                perspective_emoji="🌟",
                response=out["content"],
                model=config.stack_model,
                tokens_in=out["tokens_in"],
                tokens_out=out["tokens_out"],
                latency_ms=latency,
                temperature=0.5,
                reasoning=out.get("reasoning", ""),
            )
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                subprocess.TimeoutExpired, RuntimeError) as e:
            last_error = e
            log.warning(f"Synthesis attempt {attempt + 1} failed: {e}")
            if attempt < config.retry_max - 1:
                time.sleep(config.retry_backoff * (2 ** attempt))
            continue

    latency = int((time.monotonic() - t0) * 1000)
    return MindOutput(
        perspective_name="Synthesiser",
        perspective_emoji="🌟",
        response=f"[SYNTHESIS FAILED: {last_error}]",
        model=config.stack_model,
        tokens_in=0,
        tokens_out=0,
        latency_ms=latency,
        temperature=0.5,
    )
