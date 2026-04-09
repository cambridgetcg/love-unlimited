"""
Ollama Cloud provider — Frontier models at flat rate.

Uses Ollama's OpenAI-compatible API at ollama.com (NOT api.ollama.com).
36 models including GLM 5.1 (754B), DeepSeek v3.2, Qwen 3.5, Kimi K2.

Key differences from local ollama provider:
  - Remote API (ollama.com/v1/chat/completions)
  - Bearer token auth (API key required)
  - Has 'reasoning' field in responses (chain-of-thought)
  - Reasoning consumes max_tokens budget — enforce minimum 4000
  - $100/mo flat, unlimited usage, 10 concurrent models
  - No data logging, no training on prompts

Endpoint gotchas (from E2E 2026-04-09):
  ✅ ollama.com/v1/chat/completions — works from Python
  ❌ api.ollama.com/v1/chat/completions — 301 redirect, breaks POST
  ⚠️ api.ollama.com/api/chat — native format, blocked by Cloudflare from Python
"""

from __future__ import annotations
import json
import os
import urllib.request
import urllib.error

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

# Minimum max_tokens for GLM 5.1 and other reasoning models.
# The 'reasoning' field consumes the max_tokens budget.
# At max_tokens < 2000, reasoning eats everything and content is empty.
MIN_MAX_TOKENS = 4000


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

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("No Ollama Cloud API key available")

        api_messages = self._build_messages(request.messages, request.system)

        # Enforce minimum max_tokens — reasoning consumes the budget
        max_tokens = max(request.max_tokens, MIN_MAX_TOKENS)

        body: dict = {
            "model": request.model,
            "max_tokens": max_tokens,
            "messages": api_messages,
            "stream": False,
        }

        if request.temperature is not None:
            body["temperature"] = request.temperature

        tools = self._build_tools(request.tools)
        if tools:
            body["tools"] = tools

        if request.stop_sequences:
            body["stop"] = request.stop_sequences

        payload = json.dumps(body).encode()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        req = urllib.request.Request(
            f"{self.api_url}/v1/chat/completions",
            data=payload,
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
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

        return self._parse_response(data)

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
