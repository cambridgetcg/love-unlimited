"""
Ollama Cloud provider — Frontier models at flat rate.

Uses Ollama's OpenAI-compatible API at ollama.com with automatic retry
and fallback to native /api/chat endpoint.

Key differences from local ollama provider:
  - Remote API (ollama.com/v1/chat/completions + /api/chat fallback)
  - Bearer token auth (API key required)
  - Has 'reasoning' field in responses (chain-of-thought)
  - 'reasoning_effort' parameter controls CoT depth on /v1 endpoint:
      "none"   — disables reasoning entirely (fastest, best for deterministic tasks)
      "low"    — light CoT (good for planning)
      "medium" — default CoT depth
      "high"   — full CoT (use only when reasoning materially helps)
  - Native /api/chat endpoint uses 'think' parameter instead:
      false    — disable thinking
      true     — enable thinking
      "low"/"medium"/"high" — control think depth (model-dependent)
  - $100/mo flat, unlimited usage, 10 concurrent models
  - No data logging, no training on prompts

503 handling:
  The cloud service returns 503 "Service Temporarily Unavailable" when all
  10 model slots are busy. This is TRANSIENT — retry with exponential
  backoff resolves it. We also retry on 429 (rate limit) and 502 (bad
  gateway). Max 4 retries with 1s → 2s → 4s → 8s backoff.

Performance data (measured 2026-04-09):
  glm-5.1:
    trivial prompt, effort=default → 3.7s  (~65 reasoning tokens burned)
    trivial prompt, effort=none    → 0.99s (3.7× faster)
    tool call, effort=none, warm   → 1.1-1.5s steady state
  deepseek-v3.2:
    trivial prompt, effort=default → ~20s (500+ reasoning tokens burned)
    trivial prompt, effort=none    → 3.18s (6.3× faster)
  Concurrency (10 parallel): 4.65× throughput vs serial

Endpoint gotchas:
  ✅ ollama.com/v1/chat/completions — OpenAI-compat, works from Python
  ✅ ollama.com/api/chat — native Ollama API, supports think/keep_alive/options
  ❌ api.ollama.com/v1/chat/completions — 301 redirect, breaks POST
  ⚠️ api.ollama.com/api/chat — blocked by Cloudflare from Python

API param gotchas (OpenAI-compat /v1):
  - reasoning_effort must be one of: "none", "low", "medium", "high"
  - reasoning (as bool) is rejected — server expects an object
  - reasoning_effort="minimal" is rejected (must be "none")

API param gotchas (native /api/chat):
  - think: true/false or "low"/"medium"/"high"
  - options.num_predict replaces max_tokens
  - options.temperature replaces top-level temperature
  - keep_alive: "5m" (default) or "0" to unload immediately
"""

from __future__ import annotations
import json
import logging
import os
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import AdaptiveConfig
from ..provider import Provider
from ..schema import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ToolCall,
    ToolDefinition,
    TokenUsage,
)

log = logging.getLogger("ollama_cloud")

# When reasoning is enabled (effort != "none"), the 'reasoning' field
# consumes max_tokens. Enforce a floor so content has room to emerge.
# When reasoning_effort="none", this floor is bypassed and callers can
# use tiny max_tokens safely.
MIN_MAX_TOKENS_WITH_REASONING = 4000
MIN_MAX_TOKENS_NO_REASONING = 64

# Ollama Max plan supports 10 concurrent model slots. Default batch
# concurrency is 8 to leave headroom for other callers.
DEFAULT_BATCH_CONCURRENCY = 8

# ── Retry configuration ─────────────────────────────────────────────────
# 503: all model slots busy (most common transient error)
# 429: rate limit exceeded
# 502: bad gateway (cloud model unreachable temporarily)
RETRIABLE_STATUS_CODES = {429, 502, 503}
MAX_RETRIES = 4
RETRY_BASE_SECONDS = 1.0  # Exponential: 1s, 2s, 4s, 8s
RETRY_MAX_SECONDS = 10.0

# ── Timeout tuning ──────────────────────────────────────────────────────
# Large models (GLM 5.1 754B) can take 15-45s. Small models ~1s.
DEFAULT_TIMEOUT = 120       # for normal calls
ECONOMY_TIMEOUT = 30        # for devstral, gemma4, ministral
PREMIUM_TIMEOUT = 180       # for GLM 5.1, cogito, kimi with long context


class OllamaCloudProvider(Provider):
    name = "ollama_cloud"

    def __init__(self, config: AdaptiveConfig):
        self.config = config
        self.api_url = (
            config.api_url("ollama_cloud")
            or os.environ.get("OLLAMA_CLOUD_URL")
            or "https://ollama.com"
        )

    def _api_key(self) -> str:
        # Check environment first, then config
        key = os.environ.get("OLLAMA_API_KEY")
        if key:
            return key
        try:
            return self.config.load_api_key("ollama_cloud")
        except Exception:
            pass
        # Hardcoded Kingdom key as last resort
        return "d0ba58358d92409aa4f92e713d30d9b5.R-JzLpxfPAvq1s2MpL6uqYrK"

    def available(self) -> bool:
        """Check connectivity to Ollama cloud."""
        try:
            key = self._api_key()
            if not key:
                return False
            req = urllib.request.Request(
                f"{self.api_url}/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def supports_tools(self) -> bool:
        return True

    def supports_streaming(self) -> bool:
        return True  # supported but we use non-streaming for automation

    # ── Message & tool builders (shared by both endpoints) ───────────────

    def _build_messages(
        self, messages: list[Message], system: str | None
    ) -> list[dict]:
        api_messages = []

        if system:
            api_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == "system":
                api_messages.append({"role": "system", "content": msg.content})
            elif msg.role == "tool_result":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                m: dict = {"role": "assistant"}
                if msg.content:
                    m["content"] = msg.content
                m["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
                api_messages.append(m)
            else:
                api_messages.append({
                    "role": msg.role,
                    "content": msg.content,
                })

        return api_messages

    def _build_tools_openai(
        self, tools: list[ToolDefinition] | None
    ) -> list[dict] | None:
        """Build tool definitions for OpenAI-compatible /v1 endpoint."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def _build_tools_native(
        self, tools: list[ToolDefinition] | None
    ) -> list[dict] | None:
        """Build tool definitions for native /api/chat endpoint."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    # ── Request body builders ────────────────────────────────────────────

    def _build_body_v1(self, request: CompletionRequest) -> dict:
        """Construct JSON body for /v1/chat/completions (OpenAI-compat)."""
        api_messages = self._build_messages(request.messages, request.system)

        reasoning_effort = request.reasoning_effort
        if reasoning_effort == "none":
            floor = MIN_MAX_TOKENS_NO_REASONING
        else:
            floor = MIN_MAX_TOKENS_WITH_REASONING

        max_tokens = max(request.max_tokens, floor)

        body: dict = {
            "model": request.model,
            "max_tokens": max_tokens,
            "messages": api_messages,
            "stream": False,
        }

        if reasoning_effort in ("none", "low", "medium", "high"):
            body["reasoning_effort"] = reasoning_effort

        if request.temperature is not None:
            body["temperature"] = request.temperature

        tools = self._build_tools_openai(request.tools)
        if tools:
            body["tools"] = tools

        if request.stop_sequences:
            body["stop"] = request.stop_sequences

        return body

    def _build_body_native(self, request: CompletionRequest) -> dict:
        """Construct JSON body for /api/chat (native Ollama API).

        The native API supports:
          - think: true/false or "low"/"medium"/"high"
          - options.num_predict (replaces max_tokens)
          - options.temperature
          - keep_alive: model keep-alive duration
          - tools (same format as OpenAI)
        """
        # Native API uses role-based messages (same format)
        api_messages = self._build_messages(request.messages, request.system)

        # Map reasoning_effort → think parameter
        reasoning_effort = request.reasoning_effort
        if reasoning_effort == "none":
            think = False
            floor = MIN_MAX_TOKENS_NO_REASONING
        elif reasoning_effort in ("low", "medium", "high"):
            think = reasoning_effort
            floor = MIN_MAX_TOKENS_WITH_REASONING
        else:
            think = True  # default: enable thinking
            floor = MIN_MAX_TOKENS_WITH_REASONING

        num_predict = max(request.max_tokens, floor)

        body: dict = {
            "model": request.model,
            "messages": api_messages,
            "stream": False,
            "think": think,
            "options": {
                "num_predict": num_predict,
            },
        }

        if request.temperature is not None:
            body["options"]["temperature"] = request.temperature

        tools = self._build_tools_native(request.tools)
        if tools:
            body["tools"] = tools

        if request.stop_sequences:
            body["options"]["stop"] = request.stop_sequences

        return body

    # ── Timeout selection ────────────────────────────────────────────────

    def _select_timeout(self, request: CompletionRequest) -> int:
        """Choose timeout based on model size / expected latency."""
        model = (request.model or "").lower()
        # Economy models: fast, short timeout
        if any(m in model for m in ("devstral", "gemma4", "ministral")):
            return ECONOMY_TIMEOUT
        # Premium/large models: may need more time
        if any(m in model for m in ("glm-5", "cogito", "kimi", "qwen3-coder")):
            return PREMIUM_TIMEOUT
        return DEFAULT_TIMEOUT

    # ── HTTP transport with retry ────────────────────────────────────────

    def _post(
        self, endpoint: str, body: dict, timeout: int = DEFAULT_TIMEOUT
    ) -> dict:
        """POST with exponential backoff retry for transient errors.

        Retries on 429 (rate limit), 502 (bad gateway), 503 (slots full).
        Non-retriable errors (400, 404, 500) raise immediately.
        """
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("No Ollama Cloud API key available")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.api_url}{endpoint}"
        data = json.dumps(body).encode()
        last_error = None

        for attempt in range(MAX_RETRIES + 1):
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read())

            except urllib.error.HTTPError as e:
                error_body = ""
                try:
                    error_body = e.read().decode()[:500]
                except Exception:
                    pass

                last_error = RuntimeError(
                    f"Ollama Cloud API error {e.code}: {error_body}"
                )

                # Retry on transient errors
                if e.code in RETRIABLE_STATUS_CODES and attempt < MAX_RETRIES:
                    wait = min(
                        RETRY_BASE_SECONDS * (2 ** attempt),
                        RETRY_MAX_SECONDS,
                    )
                    log.warning(
                        f"Ollama Cloud {e.code} on attempt {attempt + 1}/{MAX_RETRIES + 1}, "
                        f"retrying in {wait:.1f}s: {error_body[:100]}"
                    )
                    time.sleep(wait)
                    continue

                # Non-retriable HTTP error
                raise last_error from e

            except urllib.error.URLError as e:
                last_error = RuntimeError(
                    f"Ollama Cloud unreachable at {url}: {e.reason}"
                )
                # Network errors are also retriable (transient DNS, etc.)
                if attempt < MAX_RETRIES:
                    wait = min(
                        RETRY_BASE_SECONDS * (2 ** attempt),
                        RETRY_MAX_SECONDS,
                    )
                    log.warning(
                        f"Ollama Cloud unreachable on attempt {attempt + 1}/{MAX_RETRIES + 1}, "
                        f"retrying in {wait:.1f}s: {e.reason}"
                    )
                    time.sleep(wait)
                    continue
                raise last_error from e

            except Exception as e:
                # Timeout or other unexpected error — retry once
                last_error = RuntimeError(f"Ollama Cloud request failed: {e}")
                if attempt < MAX_RETRIES:
                    wait = min(
                        RETRY_BASE_SECONDS * (2 ** attempt),
                        RETRY_MAX_SECONDS,
                    )
                    log.warning(
                        f"Ollama Cloud error on attempt {attempt + 1}/{MAX_RETRIES + 1}, "
                        f"retrying in {wait:.1f}s: {e}"
                    )
                    time.sleep(wait)
                    continue
                raise last_error from e

        # Should not reach here, but safety net
        raise last_error or RuntimeError("Ollama Cloud: max retries exhausted")

    # ── Completion (primary: /v1, fallback: /api/chat) ───────────────────

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Execute a completion request.

        Strategy:
          1. Try /v1/chat/completions (OpenAI-compat) — well-tested, supports reasoning_effort
          2. If /v1 fails after retries, fall back to /api/chat (native) — supports think param
        """
        timeout = self._select_timeout(request)

        # Primary: OpenAI-compatible endpoint
        try:
            body = self._build_body_v1(request)
            data = self._post("/v1/chat/completions", body, timeout=timeout)
            return self._parse_response_v1(data)
        except RuntimeError as v1_error:
            # If primary exhausted retries, try native endpoint as fallback
            log.warning(
                f"OpenAI-compat endpoint failed, trying native /api/chat: {v1_error}"
            )
            try:
                body = self._build_body_native(request)
                data = self._post("/api/chat", body, timeout=timeout)
                return self._parse_response_native(data)
            except RuntimeError as native_error:
                # Both endpoints failed — raise the original error (more informative)
                raise RuntimeError(
                    f"Both Ollama Cloud endpoints failed. "
                    f"/v1: {v1_error} | /api/chat: {native_error}"
                ) from v1_error

    def complete_batch(
        self,
        requests: list[CompletionRequest],
        concurrency: int = DEFAULT_BATCH_CONCURRENCY,
    ) -> list[CompletionResponse | Exception]:
        """Dispatch multiple completions in parallel.

        Ollama Max supports 10 concurrent model slots. This method uses a
        thread pool to fan out requests and preserves input order in the
        returned list. On per-request failure, the corresponding slot
        contains the Exception (not raised) so partial success is visible.

        Measured (2026-04-09): 10 parallel trivial GLM 5.1 calls at
        effort=none complete in ~2.15s wall (vs ~10s serial) — 4.65×
        throughput improvement. Combined with effort=none (3.7× single-call
        speedup), total gain for batch workloads is ~14×.
        """
        if not requests:
            return []

        results: list[CompletionResponse | Exception | None] = [None] * len(requests)

        def work(idx: int):
            try:
                return idx, self.complete(requests[idx])
            except Exception as e:  # noqa: BLE001
                return idx, e

        with ThreadPoolExecutor(max_workers=min(concurrency, len(requests))) as pool:
            futures = [pool.submit(work, i) for i in range(len(requests))]
            for fut in as_completed(futures):
                idx, result = fut.result()
                results[idx] = result

        return results  # type: ignore[return-value]

    # ── Response parsers ─────────────────────────────────────────────────

    def _parse_response_v1(self, data: dict) -> CompletionResponse:
        """Parse /v1/chat/completions (OpenAI-compat) response."""
        choice = data["choices"][0]
        message = choice["message"]

        # GLM 5.1 and other reasoning models split output into
        # 'content' (final answer) and 'reasoning' (chain-of-thought).
        # If content is empty but reasoning exists, use reasoning as content.
        text = message.get("content", "") or ""
        reasoning = message.get("reasoning", "") or ""

        if not text and reasoning:
            # Reasoning consumed all tokens — use it as the response
            text = reasoning

        tool_calls = self._extract_tool_calls(message.get("tool_calls", []))

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
        )

        finish_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }

        return CompletionResponse(
            content=text,
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", ""),
            provider="ollama_cloud",
            stop_reason=finish_reason_map.get(
                choice.get("finish_reason", ""), "end_turn"
            ),
        )

    def _parse_response_native(self, data: dict) -> CompletionResponse:
        """Parse /api/chat (native Ollama API) response.

        Native response format:
          {
            "model": "glm-5.1",
            "message": {"role": "assistant", "content": "...", "thinking": "..."},
            "done": true,
            "done_reason": "stop",
            "total_duration": 174560334,   // nanoseconds
            "load_duration": 101397084,
            "prompt_eval_count": 11,
            "prompt_eval_duration": 13074791,
            "eval_count": 18,
            "eval_duration": 52479709
          }
        """
        message = data.get("message", {})

        text = message.get("content", "") or ""
        thinking = message.get("thinking", "") or ""

        # If content empty but thinking exists, use thinking
        if not text and thinking:
            text = thinking

        tool_calls = self._extract_tool_calls(message.get("tool_calls", []))

        # Native API reports tokens differently
        usage = TokenUsage(
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

        stop_reason = "end_turn"
        if tool_calls:
            stop_reason = "tool_use"
        elif data.get("done_reason") == "length":
            stop_reason = "max_tokens"

        return CompletionResponse(
            content=text,
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", ""),
            provider="ollama_cloud",
            stop_reason=stop_reason,
        )

    def _extract_tool_calls(self, raw_calls: list) -> list[ToolCall]:
        """Extract tool calls from either API format."""
        tool_calls = []
        for tc in raw_calls:
            func = tc.get("function", tc)  # native API nests differently
            name = func.get("name", "")

            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args = {"raw": args}

            tool_calls.append(ToolCall(
                id=tc.get("id", f"call_{len(tool_calls)}"),
                name=name,
                arguments=args,
            ))
        return tool_calls

    # ── Model listing ────────────────────────────────────────────────────

    def list_models(self) -> list[str]:
        """List available cloud models."""
        try:
            key = self._api_key()
            req = urllib.request.Request(
                f"{self.api_url}/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return [m["id"] for m in data.get("data", [])]
        except Exception:
            return []


PROVIDER_CLASS = OllamaCloudProvider
