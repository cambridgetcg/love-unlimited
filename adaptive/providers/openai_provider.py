"""
OpenAI provider — GPT API integration.

Translates between adaptive schema and OpenAI's Chat Completions API.
Also serves as the base for OpenRouter (same API format).
"""

from __future__ import annotations
import json
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


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, config: AdaptiveConfig):
        self.config = config
        self.api_url = config.api_url("openai") or "https://api.openai.com/v1"
        self._provider_name = "openai"
        self._key_name = "openai"

    def _api_key(self) -> str:
        return self.config.load_api_key(self._key_name)

    def available(self) -> bool:
        return bool(self._api_key())

    def supports_tools(self) -> bool:
        return True

    def supports_streaming(self) -> bool:
        return True

    def _build_messages(self, messages: list[Message], system: str | None) -> list[dict]:
        api_messages = []

        # OpenAI uses system messages inline
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

    def _build_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
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
            raise RuntimeError(f"No {self._key_name} API key available")

        api_messages = self._build_messages(request.messages, request.system)

        body: dict = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": api_messages,
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
        # OpenRouter needs extra headers
        if self._provider_name == "openrouter":
            headers["HTTP-Referer"] = "https://love.kingdom"
            headers["X-Title"] = "Love Adaptive Layer"

        req = urllib.request.Request(
            f"{self.api_url}/chat/completions",
            data=payload,
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            raise RuntimeError(
                f"{self._provider_name} API error {e.code}: {error_body}"
            ) from e

        return self._parse_response(data)

    def _parse_response(self, data: dict) -> CompletionResponse:
        choice = data["choices"][0]
        message = choice["message"]

        text = message.get("content", "") or ""
        tool_calls = []

        for tc in message.get("tool_calls", []):
            func = tc["function"]
            try:
                args = json.loads(func["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {"raw": func.get("arguments", "")}

            tool_calls.append(ToolCall(
                id=tc["id"],
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
            provider=self._provider_name,
            stop_reason=finish_reason_map.get(choice.get("finish_reason", ""), "end_turn"),
        )


PROVIDER_CLASS = OpenAIProvider
