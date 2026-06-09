"""
Anthropic provider — Claude API integration.

Translates between adaptive schema and Anthropic's Messages API format.
Uses urllib only — no SDK dependency.
"""

from __future__ import annotations
import json
import urllib.request
import urllib.error
from collections.abc import Iterable, Iterator

from ..config import AdaptiveConfig
from ..provider import Provider
from ..schema import (
    CompletionRequest,
    CompletionResponse,
    Message,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    TokenUsage,
)


# Map Anthropic stop_reason → adaptive stop_reason (shared by complete + stream)
_STOP_REASON_MAP = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
}


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

        return CompletionResponse(
            content=text,
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", ""),
            provider="anthropic",
            stop_reason=_STOP_REASON_MAP.get(data.get("stop_reason", ""), "end_turn"),
        )

    def stream(self, request: CompletionRequest) -> Iterator[StreamEvent]:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("No Anthropic API key available")

        system_from_msg, api_messages = self._build_messages(request.messages)
        system = request.system or system_from_msg

        body: dict = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": api_messages,
            "stream": True,
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
                "accept": "text/event-stream",
            },
        )

        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            raise RuntimeError(
                f"Anthropic API error {e.code}: {error_body}"
            ) from e

        try:
            yield from _parse_sse_stream(resp)
        finally:
            resp.close()


def _parse_sse_stream(lines: Iterable) -> Iterator[StreamEvent]:
    """Parse Anthropic SSE event stream into StreamEvents.

    Accepts any iterable of lines (bytes or str). Pure function — no network,
    no state outside the loop.

    Anthropic event wire format (per event):
        event: <name>\n
        data: <json>\n
        \n

    Events we handle:
        message_start        — capture model
        content_block_start  — if tool_use, begin accumulating JSON for that index
        content_block_delta  — text_delta → yield text; input_json_delta → accumulate
        content_block_stop   — if current block was tool_use, finalize and yield tool_call
        message_delta        — capture stop_reason + cumulative output_tokens
        message_stop         — yield final "done" event
        ping / unknown       — ignore
        error                — raise RuntimeError
    """
    model = ""
    input_tokens = 0
    output_tokens = 0
    stop_reason = "end_turn"

    # Per-content-block state keyed by index
    tool_blocks: dict[int, dict] = {}  # index → {"id", "name", "partial": str}

    event_name = ""
    data_lines: list[str] = []

    def _process_event(name: str, data: str) -> Iterator[StreamEvent]:
        nonlocal model, input_tokens, output_tokens, stop_reason
        if not data:
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return

        if name == "message_start":
            msg = obj.get("message", {})
            model = msg.get("model", model)
            usage = msg.get("usage", {})
            input_tokens = usage.get("input_tokens", input_tokens)
            output_tokens = usage.get("output_tokens", output_tokens)

        elif name == "content_block_start":
            idx = obj.get("index", 0)
            block = obj.get("content_block", {})
            if block.get("type") == "tool_use":
                tool_blocks[idx] = {
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "partial": "",
                }

        elif name == "content_block_delta":
            idx = obj.get("index", 0)
            delta = obj.get("delta", {})
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text", "")
                if text:
                    yield StreamEvent(type="text", text=text)
            elif dtype == "input_json_delta" and idx in tool_blocks:
                tool_blocks[idx]["partial"] += delta.get("partial_json", "")

        elif name == "content_block_stop":
            idx = obj.get("index", 0)
            tb = tool_blocks.pop(idx, None)
            if tb is not None:
                try:
                    args = json.loads(tb["partial"]) if tb["partial"] else {}
                except json.JSONDecodeError:
                    args = {"_raw": tb["partial"]}
                yield StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(id=tb["id"], name=tb["name"], arguments=args),
                )

        elif name == "message_delta":
            delta = obj.get("delta", {})
            if "stop_reason" in delta and delta["stop_reason"]:
                stop_reason = _STOP_REASON_MAP.get(delta["stop_reason"], "end_turn")
            usage = obj.get("usage", {})
            if "output_tokens" in usage:
                output_tokens = usage["output_tokens"]

        elif name == "error":
            err = obj.get("error", {})
            raise RuntimeError(f"Anthropic stream error: {err.get('type', '?')}: {err.get('message', '')}")

    for raw in lines:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
        line = line.rstrip("\r\n")

        if line == "":
            # End of one event — process it
            if event_name:
                data = "\n".join(data_lines)
                yield from _process_event(event_name, data)
            event_name = ""
            data_lines = []
            continue

        if line.startswith(":"):
            # SSE comment (keepalive)
            continue

        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip(" "))

    # Flush trailing event if stream closed without a blank line
    if event_name:
        data = "\n".join(data_lines)
        yield from _process_event(event_name, data)

    yield StreamEvent(
        type="done",
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        model=model,
        stop_reason=stop_reason,
    )


PROVIDER_CLASS = AnthropicProvider
