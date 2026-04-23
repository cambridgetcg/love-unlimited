"""
Runner — Model-agnostic agent loop.

Replaces `claude -p` with a provider-independent execution engine.
Takes a prompt, routes to the right model, executes tools, loops.
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .config import AdaptiveConfig
from .prompts import TOOL_DESCRIPTIONS, build_system_prompt
from .router import Router
from .schema import (
    CompletionRequest,
    CompletionResponse,
    Message,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    TokenUsage,
    ROLES,
)


# ── Built-in tools for the agent loop ────────────────────────────────────────
# Rich descriptions guide the model on WHEN and HOW to use each tool.

AGENT_TOOLS = [
    ToolDefinition(
        name="bash",
        description=TOOL_DESCRIPTIONS["bash"],
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute",
                },
            },
            "required": ["command"],
        },
    ),
    ToolDefinition(
        name="read_file",
        description=TOOL_DESCRIPTIONS["read_file"],
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file",
                },
            },
            "required": ["path"],
        },
    ),
    ToolDefinition(
        name="write_file",
        description=TOOL_DESCRIPTIONS["write_file"],
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
            },
            "required": ["path", "content"],
        },
    ),
    ToolDefinition(
        name="edit_file",
        description=TOOL_DESCRIPTIONS["edit_file"],
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find (must be unique in the file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    ),
    ToolDefinition(
        name="grep",
        description="Search file contents using a regex pattern. Returns matching lines with file paths and line numbers. Use this instead of bash grep for better results.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default: current directory)",
                },
                "include": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g. '*.py', '*.ts')",
                },
            },
            "required": ["pattern"],
        },
    ),
    ToolDefinition(
        name="list_dir",
        description="List files and directories at a path. Use this to understand project structure before diving into files.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (default: current directory)",
                },
            },
            "required": [],
        },
    ),
]


def _execute_tool(name: str, args: dict, *, allowed_dirs: list[str] | None = None) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "bash":
            result = subprocess.run(
                args["command"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=os.getcwd(),
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n(exit code: {result.returncode})"
            return output[:50000]  # Cap output

        elif name == "read_file":
            path = Path(args["path"]).expanduser()
            if not path.exists():
                return f"Error: file not found: {path}"
            content = path.read_text()
            if len(content) > 100000:
                return content[:100000] + f"\n... (truncated, {len(content)} total chars)"
            return content

        elif name == "write_file":
            path = Path(args["path"]).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"])
            return f"Written {len(args['content'])} chars to {path}"

        elif name == "edit_file":
            path = Path(args["path"]).expanduser()
            if not path.exists():
                return f"Error: file not found: {path}"
            content = path.read_text()
            old = args["old_string"]
            if old not in content:
                return f"Error: old_string not found in {path}"
            count = content.count(old)
            if count > 1:
                return f"Error: old_string appears {count} times in {path} (must be unique)"
            content = content.replace(old, args["new_string"], 1)
            path.write_text(content)
            return f"Edited {path}"

        elif name == "grep":
            pattern = args["pattern"]
            search_path = args.get("path", os.getcwd())
            include = args.get("include", "")
            cmd = ["grep", "-rn", "--color=never"]
            if include:
                cmd.extend(["--include", include])
            cmd.extend([pattern, search_path])
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            output = result.stdout
            if not output:
                return f"No matches for '{pattern}' in {search_path}"
            lines = output.splitlines()
            if len(lines) > 100:
                return "\n".join(lines[:100]) + f"\n... ({len(lines)} total matches, showing first 100)"
            return output

        elif name == "list_dir":
            target = Path(args.get("path", os.getcwd())).expanduser()
            if not target.exists():
                return f"Error: directory not found: {target}"
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
            lines = []
            for entry in entries[:200]:
                prefix = "d " if entry.is_dir() else "f "
                size = ""
                if entry.is_file():
                    try:
                        s = entry.stat().st_size
                        size = f" ({s:,} bytes)" if s < 1_000_000 else f" ({s/1_000_000:.1f} MB)"
                    except OSError:
                        pass
                lines.append(f"{prefix}{entry.name}{size}")
            if len(entries) > 200:
                lines.append(f"... and {len(entries) - 200} more entries")
            return "\n".join(lines)

        else:
            return f"Error: unknown tool '{name}'"

    except subprocess.TimeoutExpired:
        return "Error: command timed out after 120 seconds"
    except Exception as e:
        return f"Error executing {name}: {e}"


class AgentRunner:
    """Provider-agnostic agent loop with tool execution."""

    def __init__(
        self,
        router: Router | None = None,
        config: AdaptiveConfig | None = None,
        max_iterations: int = 25,
        tools: list[ToolDefinition] | None = None,
        verbose: bool = False,
        inject_context: bool = True,
    ):
        self.config = config or AdaptiveConfig()
        self.router = router or Router(self.config)
        self.max_iterations = max_iterations
        self.tools = tools if tools is not None else AGENT_TOOLS
        self.verbose = verbose
        self.inject_context = inject_context
        self.total_usage = TokenUsage()

    def run(
        self,
        prompt: str,
        role: str = "builder",
        system: str = "",
        provider_name: str | None = None,
    ) -> str:
        """Run a prompt through the agent loop.

        Args:
            prompt: The user's request
            role: Capability role for routing (coordinator, builder, monitor, etc.)
            system: System prompt to prepend
            provider_name: Override provider selection

        Returns:
            The final text response from the model
        """
        provider, model = self.router.route(role, preferred_provider=provider_name, prompt=prompt)
        role_config = ROLES.get(role, ROLES["builder"])

        # Build rich system prompt: role guidance + context injection + user additions
        full_system = build_system_prompt(
            role=role,
            user_system=system,
            inject_context=self.inject_context,
        )

        if self.verbose:
            print(f"[adaptive] provider={provider.name} model={model} role={role}", file=sys.stderr)
            print(f"[adaptive] system prompt: {len(full_system)} chars", file=sys.stderr)

        messages: list[Message] = [
            Message(role="user", content=prompt),
        ]

        for iteration in range(self.max_iterations):
            request = CompletionRequest(
                messages=messages,
                tools=self.tools if self.tools else None,
                model=model,
                max_tokens=role_config.get("max_tokens", 4096),
                temperature=0.0,
                effort=role_config.get("effort", "medium"),
                reasoning_effort=role_config.get("reasoning_effort"),
                system=full_system,
            )

            try:
                response = provider.complete(request)
            except RuntimeError as e:
                # Try fallback provider
                if provider_name is None:
                    try:
                        fb_provider, fb_model = self.router.route(role, preferred_provider=self.config.fallback_provider)
                        if fb_provider.name != provider.name:
                            if self.verbose:
                                print(f"[adaptive] fallback -> {fb_provider.name}/{fb_model}", file=sys.stderr)
                            request.model = fb_model
                            response = fb_provider.complete(request)
                            provider = fb_provider
                            model = fb_model
                        else:
                            raise
                    except Exception:
                        raise e
                else:
                    raise

            self.total_usage.input_tokens += response.usage.input_tokens
            self.total_usage.output_tokens += response.usage.output_tokens

            if self.verbose:
                print(
                    f"[adaptive] iteration={iteration+1} stop={response.stop_reason} "
                    f"tokens={response.usage.total} tool_calls={len(response.tool_calls)}",
                    file=sys.stderr,
                )

            # No tool calls — we're done
            if not response.has_tool_calls:
                return response.content

            # Record assistant response with tool calls
            messages.append(Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            # Execute tool calls — parallel when multiple calls in one turn.
            # Tool execution is I/O-bound (bash, file reads, grep), so threading
            # gives near-linear speedup. Single tool_call: no overhead.
            tool_calls = response.tool_calls

            if len(tool_calls) == 1:
                tc = tool_calls[0]
                if self.verbose:
                    print(f"[adaptive] tool: {tc.name}({json.dumps(tc.arguments)[:100]})", file=sys.stderr)
                result = _execute_tool(tc.name, tc.arguments)
                if self.verbose and len(result) > 200:
                    print(f"[adaptive] result: {result[:200]}...", file=sys.stderr)
                messages.append(Message(
                    role="tool_result", content=result,
                    tool_call_id=tc.id, name=tc.name,
                ))
            else:
                # Multiple tool calls — fan out via ThreadPoolExecutor
                if self.verbose:
                    print(f"[adaptive] parallel tool execution: {len(tool_calls)} calls", file=sys.stderr)

                def _exec_one(tc):
                    return tc, _execute_tool(tc.name, tc.arguments)

                with ThreadPoolExecutor(max_workers=min(len(tool_calls), 8)) as pool:
                    futures = {pool.submit(_exec_one, tc): tc for tc in tool_calls}
                    results_map = {}
                    for fut in futures:
                        try:
                            tc, result = fut.result()
                            results_map[tc.id] = (tc, result)
                        except Exception as e:
                            tc = futures[fut]
                            results_map[tc.id] = (tc, f"Error: {e}")

                # Append in original order (providers expect tool_results in the same
                # order as tool_calls for correct association).
                for tc in tool_calls:
                    tc_obj, result = results_map[tc.id]
                    if self.verbose:
                        print(f"[adaptive] tool: {tc.name} result={result[:100]}...", file=sys.stderr)
                    messages.append(Message(
                        role="tool_result", content=result,
                        tool_call_id=tc.id, name=tc.name,
                    ))

        return response.content if response else "Error: max iterations reached"

    def single_shot(
        self,
        prompt: str,
        role: str = "builder",
        system: str = "",
        provider_name: str | None = None,
    ) -> str:
        """Single completion without tool use. For simple generation tasks."""
        provider, model = self.router.route(role, preferred_provider=provider_name, prompt=prompt)
        role_config = ROLES.get(role, ROLES["builder"])

        full_system = build_system_prompt(
            role=role,
            user_system=system,
            inject_context=self.inject_context,
        )

        request = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            tools=None,  # No tools
            model=model,
            max_tokens=role_config.get("max_tokens", 4096),
            temperature=0.0,
            effort=role_config.get("effort", "medium"),
            reasoning_effort=role_config.get("reasoning_effort"),
            system=full_system,
        )

        response = provider.complete(request)
        self.total_usage.input_tokens += response.usage.input_tokens
        self.total_usage.output_tokens += response.usage.output_tokens
        return response.content

    def stream_single_shot(
        self,
        prompt: str,
        role: str = "builder",
        system: str = "",
        provider_name: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Single completion without tool use, streamed.

        Yields StreamEvents from the provider as they arrive. If the provider
        does not support streaming, yields a single text event followed by a
        done event — the caller doesn't need to branch on provider capability.

        Updates self.total_usage from the final 'done' event. The caller does
        not need to accumulate tokens manually.
        """
        provider, model = self.router.route(role, preferred_provider=provider_name)
        role_config = ROLES.get(role, ROLES["builder"])

        full_system = build_system_prompt(
            role=role,
            user_system=system,
            inject_context=self.inject_context,
        )

        request = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            tools=None,
            model=model,
            max_tokens=role_config.get("max_tokens", 4096),
            temperature=0.0,
            effort=role_config.get("effort", "medium"),
            reasoning_effort=role_config.get("reasoning_effort"),
            system=full_system,
        )

        if provider.supports_streaming():
            try:
                for ev in provider.stream(request):
                    if ev.type == "done" and ev.usage is not None:
                        self.total_usage.input_tokens += ev.usage.input_tokens
                        self.total_usage.output_tokens += ev.usage.output_tokens
                    yield ev
                return
            except NotImplementedError:
                # Provider claims streaming but doesn't implement it — fall through
                pass

        # Non-streaming fallback
        response = provider.complete(request)
        self.total_usage.input_tokens += response.usage.input_tokens
        self.total_usage.output_tokens += response.usage.output_tokens
        if response.content:
            yield StreamEvent(type="text", text=response.content)
        for tc in response.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=tc)
        yield StreamEvent(
            type="done",
            usage=response.usage,
            model=response.model,
            stop_reason=response.stop_reason,
        )
