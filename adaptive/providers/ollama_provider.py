"""
Ollama provider — Local model inference.

Talks to Ollama's OpenAI-compatible API at localhost:11434.
Zero cost, full privacy, no API key needed.
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


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, config: AdaptiveConfig):
        self.config = config
        self.api_url = config.api_url("ollama") or "http://localhost:11434"

    def available(self) -> bool:
        """Check if Ollama is running."""
        try:
            req = urllib.request.Request(f"{self.api_url}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def supports_tools(self) -> bool:
        # Ollama supports tools for some models (llama3.1+, qwen2.5+)
        return True

    def supports_streaming(self) -> bool:
        return True

    def _build_messages(self, messages: list[Message], system: str | None) -> list[dict]:
        api_messages = []

        if system:
            api_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == "system":
                api_messages.append({"role": "system", "content": msg.content})
            elif msg.role == "tool_result":
                api_messages.append({
                    "role": "tool",
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
        api_messages = self._build_messages(request.messages, request.system)

        body: dict = {
            "model": request.model,
            "messages": api_messages,
            "stream": False,
            "options": {
                "temperature": request.temperature or 0.0,
                "num_predict": request.max_tokens,
            },
        }

        tools = self._build_tools(request.tools)
        if tools:
            body["tools"] = tools

        payload = json.dumps(body).encode()

        req = urllib.request.Request(
            f"{self.api_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Ollama error {e.code}: {error_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Ollama not reachable at {self.api_url}. Is it running? ({e.reason})"
            ) from e

        return self._parse_response(data)

    def _parse_response(self, data: dict) -> CompletionResponse:
        message = data.get("message", {})
        text = message.get("content", "") or ""
        tool_calls = []

        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}

            tool_calls.append(ToolCall(
                id=tc.get("id", f"call_{len(tool_calls)}"),
                name=func.get("name", ""),
                arguments=args,
            ))

        # Ollama reports tokens differently
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
            provider="ollama",
            stop_reason=stop_reason,
        )

    def list_models(self) -> list[str]:
        """List locally available models."""
        try:
            req = urllib.request.Request(f"{self.api_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []


PROVIDER_CLASS = OllamaProvider
