"""
Anthropic provider — Claude API integration.

Translates between adaptive schema and Anthropic's Messages API format.
Uses urllib only — no SDK dependency.
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


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, config: AdaptiveConfig):
        self.config = config
        self.api_url = config.api_url("anthropic") or "https://api.anthropic.com/v1"
        self.api_version = config.provider_config("anthropic").get("api_version", "2023-06-01")

    def _api_key(self) -> str:
        return self.config.load_api_key("anthropic")

    def available(self) -> bool:
        return bool(self._api_key())

    def supports_tools(self) -> bool:
        return True

    def supports_streaming(self) -> bool:
        return True

    def _build_messages(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """Split system prompt from messages (Anthropic uses top-level system param)."""
        system = ""
        api_messages = []

        for msg in messages:
            if msg.role == "system":
                system = msg.content
                continue

            if msg.role == "tool_result":
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                content = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_messages.append({"role": "assistant", "content": content})
            else:
                api_messages.append({
                    "role": msg.role,
                    "content": msg.content,
                })

        return system, api_messages

    def _build_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("No Anthropic API key available")

        system_from_msg, api_messages = self._build_messages(request.messages)
        system = request.system or system_from_msg

        body: dict = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": api_messages,
        }

        if system:
            body["system"] = system

        if request.temperature is not None:
            body["temperature"] = request.temperature

        tools = self._build_tools(request.tools)
        if tools:
            body["tools"] = tools

        if request.stop_sequences:
            body["stop_sequences"] = request.stop_sequences

        payload = json.dumps(body).encode()

        req = urllib.request.Request(
            f"{self.api_url}/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": self.api_version,
                "content-type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            raise RuntimeError(
                f"Anthropic API error {e.code}: {error_body}"
            ) from e

        return self._parse_response(data)

    def _parse_response(self, data: dict) -> CompletionResponse:
        content_parts = data.get("content", [])
        text = ""
        tool_calls = []

        for part in content_parts:
            if part["type"] == "text":
                text += part["text"]
            elif part["type"] == "tool_use":
                tool_calls.append(ToolCall(
                    id=part["id"],
                    name=part["name"],
                    arguments=part.get("input", {}),
                ))

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
        )

        stop_reason_map = {
            "end_turn": "end_turn",
            "tool_use": "tool_use",
            "max_tokens": "max_tokens",
            "stop_sequence": "stop_sequence",
        }

        return CompletionResponse(
            content=text,
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", ""),
            provider="anthropic",
            stop_reason=stop_reason_map.get(data.get("stop_reason", ""), "end_turn"),
        )


PROVIDER_CLASS = AnthropicProvider
