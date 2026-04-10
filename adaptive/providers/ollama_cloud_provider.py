"""
Ollama Cloud provider — Frontier models at flat rate.

Uses Ollama's OpenAI-compatible API at ollama.com (NOT api.ollama.com).
36 models including GLM 5.1 (754B), DeepSeek v3.2, Qwen 3.5, Kimi K2.

Key differences from local ollama provider:
  - Remote API (ollama.com/v1/chat/completions)
  - Bearer token auth (API key required)
  - Has 'reasoning' field in responses (chain-of-thought)
  - 'reasoning_effort' parameter controls CoT depth:
      "none"   — disables reasoning entirely (fastest, best for deterministic tasks)
      "low"    — light CoT (good for planning)
      "medium" — default CoT depth
      "high"   — full CoT (use only when reasoning materially helps)
  - When effort is set (including "none"), max_tokens can be small again
  - $100/mo flat, unlimited usage, 10 concurrent models
  - No data logging, no training on prompts

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
  ✅ ollama.com/v1/chat/completions — works from Python
  ❌ api.ollama.com/v1/chat/completions — 301 redirect, breaks POST
  ⚠️ api.ollama.com/api/chat — native format, blocked by Cloudflare from Python

API param gotchas:
  - reasoning_effort must be one of: "none", "low", "medium", "high"
  - reasoning (as bool) is rejected — server expects an object
  - reasoning_effort="minimal" is rejected (must be "none")
"""

from __future__ import annotations
import json
import os
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

# When reasoning is enabled (effort != "none"), the 'reasoning' field
# consumes max_tokens. Enforce a floor so content has room to emerge.
# When reasoning_effort="none", this floor is bypassed and callers can
# use tiny max_tokens safely.
MIN_MAX_TOKENS_WITH_REASONING = 4000
MIN_MAX_TOKENS_NO_REASONING = 64

# Ollama Max plan supports 10 concurrent model slots. Default batch
# concurrency is 8 to leave headroom for other callers.
DEFAULT_BATCH_CONCURRENCY = 8


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

    def _build_tools(
        self, tools: list[ToolDefinition] | None
    ) -> list[dict] | None:
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

    def _build_body(self, request: CompletionRequest) -> dict:
        """Construct the JSON body for a /v1/chat/completions call."""
        api_messages = self._build_messages(request.messages, request.system)

        # Map reasoning_effort — "none" disables CoT and relaxes the
        # max_tokens floor, everything else enforces the reasoning floor.
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

        tools = self._build_tools(request.tools)
        if tools:
            body["tools"] = tools

        if request.stop_sequences:
            body["stop"] = request.stop_sequences

        return body

    def _post(self, body: dict, timeout: int = 300) -> dict:
        """Low-level POST. Raises RuntimeError on HTTP/network failure."""
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("No Ollama Cloud API key available")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        req = urllib.request.Request(
            f"{self.api_url}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode()[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"Ollama Cloud API error {e.code}: {error_body}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Ollama Cloud unreachable at {self.api_url}: {e.reason}"
            ) from e

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        body = self._build_body(request)
        data = self._post(body)
        return self._parse_response(data)

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

    def _parse_response(self, data: dict) -> CompletionResponse:
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

        tool_calls = []
        for tc in message.get("tool_calls", []):
            func = tc["function"]
            try:
                args = json.loads(func["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {"raw": func.get("arguments", "")}

            tool_calls.append(ToolCall(
                id=tc.get("id", f"call_{len(tool_calls)}"),
                name=func["name"],
                arguments=args,
            ))

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
