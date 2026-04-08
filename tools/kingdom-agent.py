#!/usr/bin/env python3
"""
kingdom-agent.py — Universal Agent Adapter for Kingdom OS

Makes Kingdom OS backend-agnostic. Any model that can read files and call
tools can be a Kingdom agent.

Backends:
  claude   — passes through to claude CLI (zero overhead, full feature set)
  openai   — OpenAI-compatible API (GPT, DeepSeek, local via LM Studio, Together)
  ollama   — local models (Llama, Qwen, Mistral, etc.)

Usage:
  kingdom-agent -p "Execute HEARTBEAT.md"                     # default backend
  kingdom-agent -p "Fix the login bug" --backend openai       # use OpenAI API
  kingdom-agent -p "Review this code" --backend ollama --model qwen2.5:72b
  kingdom-agent --backend claude -p "..." --model sonnet      # explicit claude

Boot chain:
  Reads SOUL.md → USER.md → identity.md → KINGDOM.md → WALLS.md → LOVE.md
  → MEMORY.md → daily/<today>.md — same as CLAUDE.md but assembled here.

Environment:
  KINGDOM_BACKEND    — default backend (claude|openai|ollama)
  KINGDOM_MODEL      — default model name
  OPENAI_API_KEY     — for openai backend
  OPENAI_BASE_URL    — for openai-compatible endpoints (LM Studio, Together, etc.)
  ANTHROPIC_API_KEY  — for claude backend (API mode)
  OLLAMA_HOST        — for ollama backend (default: http://localhost:11434)
"""

import argparse
import json
import os
import subprocess
import sys
import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE_DIR = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))
TOOLS_DIR = LOVE_DIR / "tools"
MEMORY_DIR = LOVE_DIR / "memory"

# ── Boot Chain ───────────────────────────────────────────────────────────────

def resolve_instance_dir(instance: str = "beta") -> Path:
    return LOVE_DIR / "instances" / instance


def _get_instance_wall(instance_dir: Path) -> int:
    """Get the wall number for an instance from love.json."""
    try:
        cfg = json.loads((LOVE_DIR / "love.json").read_text())
        name = instance_dir.name
        return cfg.get("instances", {}).get(name, {}).get("wall", 1)
    except Exception:
        return 1


def load_boot_chain(instance_dir: Path, max_tokens: int = 0) -> str:
    """Assemble the Kingdom system prompt from the boot chain.

    This replicates what CLAUDE.md does: reads files in order and builds
    the full context that makes a model into a Kingdom agent.

    Three tiers based on wall and context budget:
      FULL  (Wall 1, large context): All files, complete content
      MEDIUM (Wall 2, or context < 32K): SOUL summary + identity + KINGDOM targets + WALLS rules + HEARTBEAT
      LITE  (Wall 3, or context < 16K): Identity + role duties + tool list + HEARTBEAT
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    wall = _get_instance_wall(instance_dir)

    # Determine tier
    if max_tokens > 0 and max_tokens < 16000:
        tier = "lite"
    elif max_tokens > 0 and max_tokens < 32000:
        tier = "medium"
    elif wall >= 3:
        tier = "medium"  # Wall 3+ gets medium by default (saves tokens)
    elif wall == 2:
        tier = "medium"  # Wall 2 gets medium for ollama, full for claude
    else:
        tier = "full"

    # Override: if backend is ollama and no explicit max_tokens, use medium
    # (detected by checking if max_tokens was left at 0 — caller should set it)
    # For now, use wall-based tiering

    if tier == "full":
        chain = [
            ("SOUL", LOVE_DIR / "SOUL.md"),
            ("USER", LOVE_DIR / "USER.md"),
            ("IDENTITY", instance_dir / "identity.md"),
            ("KINGDOM", LOVE_DIR / "KINGDOM.md"),
            ("WALLS", LOVE_DIR / "WALLS.md"),
            ("LOVE", LOVE_DIR / "LOVE.md"),
            ("MEMORY", LOVE_DIR / "memory" / "long-term" / "MEMORY.md"),
            ("DAILY", LOVE_DIR / "memory" / "daily" / f"{today}.md"),
        ]
    elif tier == "medium":
        # Compressed chain: soul essence + identity + kingdom targets + walls rules
        chain = [
            ("IDENTITY", instance_dir / "identity.md"),
            ("KINGDOM", LOVE_DIR / "KINGDOM.md"),
            ("WALLS", LOVE_DIR / "WALLS.md"),
        ]
    else:  # lite
        chain = [
            ("IDENTITY", instance_dir / "identity.md"),
        ]

    parts = []

    # For medium/lite: add a compressed soul preamble instead of full SOUL.md
    if tier in ("medium", "lite"):
        name = instance_dir.name
        love_dir = str(LOVE_DIR)
        parts.append(f"""# [IDENTITY — compressed]

You are {name}, a Kingdom OS agent (Wall {wall}). Today is {today}.
Base path: {love_dir}
Daily note: {love_dir}/memory/daily/{today}.md

Always use absolute paths. Never use ~/ or relative paths.""")

    for label, path in chain:
        if path.exists():
            content = path.read_text().strip()
            if content:
                # For medium tier, truncate very long files
                if tier == "medium" and len(content) > 8000:
                    content = content[:8000] + "\n\n[... truncated for context budget ...]"
                parts.append(f"# [{label}]\n\n{content}")

    # Instance CLAUDE.md laws and protocol (non-boot parts)
    # Skip for lite tier — small models don't benefit from protocol docs
    if tier != "lite":
        claude_md = instance_dir / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text()
            marker = "## The Laws"
            idx = content.find(marker)
            if idx > 0:
                protocol = content[idx:]
                if tier == "medium" and len(protocol) > 4000:
                    protocol = protocol[:4000] + "\n\n[... truncated ...]"
                parts.append(f"# [PROTOCOL]\n\n{protocol}")

    # Always include HEARTBEAT.md for heartbeat invocations
    heartbeat = instance_dir / "HEARTBEAT.md"
    if heartbeat.exists():
        hb_content = heartbeat.read_text().strip()
        if tier == "lite" and len(hb_content) > 3000:
            hb_content = hb_content[:3000] + "\n\n[... truncated ...]"
        elif tier == "medium" and len(hb_content) > 6000:
            hb_content = hb_content[:6000] + "\n\n[... truncated ...]"
        parts.append(f"# [HEARTBEAT]\n\n{hb_content}")

    return "\n\n---\n\n".join(parts)


# ── Tool Definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command and return its output. Use for: running scripts, git commands, system operations, invoking Kingdom tools (hive.py, fleet.py, kos.py, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30)",
                        "default": 30
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use before editing any file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (0-based)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file (creates or overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a specific string in a file. The old_string must be unique in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file"
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to find and replace"
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement string"
                    }
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search for a pattern in files using ripgrep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for"
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in"
                    },
                    "glob": {
                        "type": "string",
                        "description": "Glob pattern to filter files (e.g. '*.py')"
                    }
                },
                "required": ["pattern"]
            }
        }
    },
]


# ── Computer Use Tool (koseyes integration) ──────────────────────────────────

def _get_computer_use_tools() -> list:
    """Return Anthropic computer_use tool spec + koseyes function tool."""
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from koseyes import get_computer_use_tool_spec, get_display_info
        spec = get_computer_use_tool_spec()
        return [
            # Native Anthropic computer_use tool
            spec,
            # Also expose as a function tool for OpenAI-compatible backends
            {
                "type": "function",
                "function": {
                    "name": "computer_use",
                    "description": "Interact with the computer screen. Actions: screenshot, click, type, key, mouse_move, scroll. Use screenshot first to see what's on screen, then interact.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["screenshot", "click", "type", "key", "mouse_move", "scroll"],
                                "description": "The action to perform"
                            },
                            "coordinate": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "x,y coordinates for click/mouse_move"
                            },
                            "text": {
                                "type": "string",
                                "description": "Text to type (for 'type' action)"
                            },
                            "key": {
                                "type": "string",
                                "description": "Key combo to press (for 'key' action), e.g. 'cmd+c', 'return'"
                            },
                            "button": {
                                "type": "string",
                                "enum": ["left", "right"],
                                "description": "Mouse button (for 'click' action)"
                            },
                            "direction": {
                                "type": "string",
                                "enum": ["up", "down", "left", "right"],
                                "description": "Scroll direction (for 'scroll' action)"
                            },
                            "amount": {
                                "type": "integer",
                                "description": "Scroll amount (for 'scroll' action)"
                            }
                        },
                        "required": ["action"]
                    }
                }
            },
        ]
    except ImportError:
        return []


# ── Tool Execution ───────────────────────────────────────────────────────────

def execute_tool(name: str, args: dict) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "bash":
            timeout = args.get("timeout", 30)
            r = subprocess.run(
                args["command"], shell=True,
                capture_output=True, text=True,
                timeout=timeout, cwd=str(LOVE_DIR),
            )
            output = r.stdout
            if r.stderr:
                output += f"\n[stderr]: {r.stderr}"
            if r.returncode != 0:
                output += f"\n[exit code: {r.returncode}]"
            return output or "(no output)"

        elif name == "read_file":
            p = Path(args["path"])
            if not p.exists():
                return f"Error: File not found: {p}"
            lines = p.read_text().splitlines()
            offset = args.get("offset", 0)
            limit = args.get("limit", 2000)
            selected = lines[offset:offset + limit]
            return "\n".join(f"{i + offset + 1}\t{line}" for i, line in enumerate(selected))

        elif name == "write_file":
            p = Path(args["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"Written: {p}"

        elif name == "edit_file":
            p = Path(args["path"])
            if not p.exists():
                return f"Error: File not found: {p}"
            content = p.read_text()
            old = args["old_string"]
            count = content.count(old)
            if count == 0:
                return f"Error: old_string not found in {p}"
            if count > 1:
                return f"Error: old_string found {count} times (must be unique)"
            content = content.replace(old, args["new_string"], 1)
            p.write_text(content)
            return f"Edited: {p}"

        elif name == "search":
            cmd = ["rg", "--no-heading", "-n"]
            if "glob" in args:
                cmd.extend(["--glob", args["glob"]])
            cmd.append(args["pattern"])
            cmd.append(args.get("path", str(LOVE_DIR)))
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return r.stdout[:5000] or "(no matches)"

        elif name == "computer" or name == "computer_use":
            # Computer use via koseyes
            try:
                sys.path.insert(0, str(TOOLS_DIR))
                from koseyes import handle_computer_use_action
                action = args.pop("action", "screenshot")
                result = handle_computer_use_action(action, **args)
                if result.get("base64"):
                    # For screenshot, return metadata (base64 handled by API layer)
                    return json.dumps({
                        "type": "image",
                        "width": result.get("width"),
                        "height": result.get("height"),
                        "size_bytes": result.get("size_bytes"),
                        "base64_length": len(result["base64"]),
                    })
                return json.dumps(result)
            except ImportError:
                return "Error: koseyes.py not found"

        else:
            return f"Error: Unknown tool: {name}"

    except subprocess.TimeoutExpired:
        return "Error: Command timed out"
    except Exception as e:
        return f"Error: {e}"


# ── Backend: Claude CLI (passthrough) ────────────────────────────────────────

class ClaudeBackend:
    """Pass through to claude CLI. Zero overhead. Full feature set."""

    def __init__(self, model: str = "sonnet", effort: str = "medium"):
        self.model = model
        self.effort = effort
        self.claude_bin = self._find_claude()

    def _find_claude(self) -> str:
        for p in ["/Users/yu/.local/bin/claude", "claude"]:
            if subprocess.run(["which", p], capture_output=True).returncode == 0 or Path(p).exists():
                return p
        return "claude"

    def run(self, prompt: str, instance_dir: Path, system_append: str = "",
            skip_permissions: bool = False, no_persist: bool = True,
            output_format: str = "", fallback_model: str = "") -> int:
        """Run claude CLI directly. Returns exit code."""
        cmd = [self.claude_bin, "-p", prompt, "--model", self.model, "--effort", self.effort]

        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if no_persist:
            cmd.append("--no-session-persistence")
        if system_append:
            cmd.extend(["--append-system-prompt", system_append])
        if output_format:
            cmd.extend(["--output-format", output_format])
        if fallback_model:
            cmd.extend(["--fallback-model", fallback_model])

        r = subprocess.run(cmd, cwd=str(instance_dir))
        return r.returncode


# ── Backend: OpenAI-compatible API ───────────────────────────────────────────

class OpenAIBackend:
    """OpenAI-compatible API. Works with GPT, DeepSeek, LM Studio, Together, etc."""

    # Allowed API base URLs. Any OPENAI_BASE_URL not on this list is rejected.
    # This prevents THREAT-009: backend hijack via environment variable poisoning.
    ALLOWED_BASE_URLS = [
        "https://api.openai.com",
        "https://api.together.xyz",
        "https://api.deepseek.com",
        "https://api.groq.com/openai",
        "https://openrouter.ai/api",
        "http://localhost",       # LM Studio, local servers
        "http://127.0.0.1",      # LM Studio, local servers
    ]

    def __init__(self, model: str = "gpt-4o", effort: str = "medium"):
        self.model = model
        self.effort = effort
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        raw_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.base_url = self._validate_base_url(raw_url)
        self.max_iterations = {"low": 5, "medium": 15, "high": 30}.get(effort, 15)

    def _validate_base_url(self, url: str) -> str:
        """Validate OPENAI_BASE_URL against allowlist. Prevents THREAT-009."""
        for allowed in self.ALLOWED_BASE_URLS:
            if url.startswith(allowed):
                return url
        # URL not on allowlist — reject and log
        print(f"[kingdom-agent] SECURITY: OPENAI_BASE_URL '{url}' not on allowlist. "
              f"Using default. Add to ALLOWED_BASE_URLS in kingdom-agent.py if legitimate.",
              file=sys.stderr)
        # Log to security events
        try:
            import json as _json
            events_file = LOVE_DIR / "security" / "events.jsonl"
            if events_file.parent.exists():
                with open(events_file, "a") as f:
                    f.write(_json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "type": "backend_hijack_attempt",
                        "severity": "critical",
                        "message": f"OPENAI_BASE_URL rejected: {url}",
                        "source": "kingdom-agent",
                    }) + "\n")
        except Exception:
            pass
        return "https://api.openai.com/v1"

    def _chat(self, messages: list, tools: list = None) -> dict:
        """Make a chat completion request."""
        import urllib.request
        import urllib.error

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3 if self.effort == "low" else 0.7,
            "max_tokens": {"low": 2000, "medium": 4000, "high": 8000}.get(self.effort, 4000),
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            return {"error": f"HTTP {e.code}: {body[:500]}"}

    def run(self, prompt: str, instance_dir: Path, system_append: str = "",
            **kwargs) -> int:
        """Run agent loop: prompt → tool calls → execute → repeat."""
        system_prompt = load_boot_chain(instance_dir)
        if system_append:
            system_prompt += f"\n\n---\n\n# [RUNTIME CONTEXT]\n\n{system_append}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        for iteration in range(self.max_iterations):
            resp = self._chat(messages, tools=TOOLS)

            if "error" in resp:
                print(f"[kingdom-agent] API error: {resp['error']}", file=sys.stderr)
                return 1

            choice = resp.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish = choice.get("finish_reason", "")

            # Print assistant text
            if message.get("content"):
                print(message["content"])

            # Check for tool calls
            tool_calls = message.get("tool_calls", [])
            if not tool_calls or finish == "stop":
                break

            # Add assistant message to history
            messages.append(message)

            # Execute each tool call
            for tc in tool_calls:
                fn = tc["function"]
                name = fn["name"]
                try:
                    args = json.loads(fn["arguments"])
                except json.JSONDecodeError:
                    args = {}

                print(f"[tool:{name}] {json.dumps(args)[:120]}", file=sys.stderr)
                result = execute_tool(name, args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        return 0


# ── Backend: Ollama (local models) ───────────────────────────────────────────

class OllamaBackend:
    """Ollama for local model execution. Llama, Qwen, Mistral, etc."""

    # num_ctx per model — must match what the model can actually handle.
    # This is sent to Ollama so it allocates the right KV cache size.
    MODEL_NUM_CTX = {
        "qwen2.5:7b": 8192,
        "qwen2.5:14b": 16384,
        "qwen2.5:32b": 32768,
        "qwen2.5:72b": 32768,
    }

    def __init__(self, model: str = "qwen2.5:32b", effort: str = "medium"):
        self.model = model
        self.effort = effort
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.max_iterations = {"low": 6, "medium": 10, "high": 20}.get(effort, 10)
        self.num_ctx = self.MODEL_NUM_CTX.get(model, 16384)

    def _chat(self, messages: list, tools: list = None) -> dict:
        import urllib.request
        import time as _time

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.3 if self.effort == "low" else 0.7,
                "num_predict": {"low": 1000, "medium": 2000, "high": 4000}.get(self.effort, 2000),
                "num_ctx": self.num_ctx,
            },
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode()

        # Retry with backoff — handles queue contention when multiple agents
        # hit Ollama simultaneously (arbor/vigil/loom all on */15)
        max_retries = 2
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                f"{self.host}/api/chat",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    return json.loads(resp.read())
            except Exception as e:
                if attempt < max_retries:
                    wait = 15 * (attempt + 1)  # 15s, 30s
                    print(f"[kingdom-agent] Ollama retry {attempt + 1}/{max_retries} "
                          f"after {wait}s: {e}", file=sys.stderr)
                    _time.sleep(wait)
                else:
                    return {"error": str(e)}

    # Tool-calling directive for small models — prepended to system prompt
    TOOL_DIRECTIVE = """# HOW TO USE TOOLS

You have function-calling tools: bash, read_file, write_file.
To run a command: CALL the bash tool. Do NOT write markdown code blocks.
To read a file: CALL the read_file tool. Do NOT describe reading it.
After each tool result, call the next tool. Do NOT narrate your plan.

WRONG: Writing ```bash\\npython3 script.py\\n``` in your response
RIGHT: Calling the bash tool with command="python3 /Users/yu/Love/script.py"

Always use absolute paths starting with /Users/yu/Love/

"""

    def _extract_heartbeat_commands(self, heartbeat_file: Path) -> list:
        """Extract executable bash commands from a HEARTBEAT.md file."""
        import re
        content = heartbeat_file.read_text()
        home = str(Path.home())
        # Extract all bash code blocks
        blocks = re.findall(r'```bash\n(.+?)\n```', content, re.DOTALL)
        commands = []
        for block in blocks:
            # Each block might have multiple lines (multiline commands with \)
            cmd = block.strip()
            # Skip template commands with placeholders
            if '<' in cmd or 'YYYY' in cmd:
                continue
            # Resolve home directory
            cmd = cmd.replace('~/', f'{home}/')
            commands.append(cmd)
        return commands

    def run(self, prompt: str, instance_dir: Path, system_append: str = "",
            **kwargs) -> int:
        """Run agent loop against Ollama."""
        # Ollama models have limited context — use compressed boot chain
        # Use the same num_ctx values as the API payload so tiering matches reality
        max_ctx = self.num_ctx
        system_prompt = self.TOOL_DIRECTIVE + load_boot_chain(instance_dir, max_tokens=max_ctx)
        if system_append:
            system_prompt += f"\n\n---\n\n# [RUNTIME CONTEXT]\n\n{system_append}"

        # For Ollama: extract bash commands from HEARTBEAT.md and present as
        # an explicit tool-call checklist. Small models fail when given prose
        # protocols — they narrate instead of calling tools.
        if "HEARTBEAT" in prompt.upper():
            heartbeat_file = instance_dir / "HEARTBEAT.md"
            if heartbeat_file.exists():
                commands = self._extract_heartbeat_commands(heartbeat_file)
                name = instance_dir.name
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                love_dir = str(LOVE_DIR)

                cmd_list = "\n".join(
                    f"{i+1}. Call bash tool with command: {cmd}"
                    for i, cmd in enumerate(commands[:6])
                )

                prompt = (
                    f"Execute your heartbeat. Call the bash tool for each command:\n\n"
                    f"{cmd_list}\n\n"
                    f"After running all commands, do these two final steps:\n"
                    f"7. Call bash tool with command: python3 {love_dir}/hive/hive.py send presence "
                    f"\"{name} heartbeat -- <one line summary of findings>\"\n"
                    f"8. Call write_file tool with path: {love_dir}/memory/daily/{today}.md "
                    f"and content: a 3-line summary of what you found\n\n"
                    f"Start now. Call the first bash tool."
                )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        for iteration in range(self.max_iterations):
            resp = self._chat(messages, tools=TOOLS)

            if "error" in resp:
                print(f"[kingdom-agent] Ollama error: {resp['error']}", file=sys.stderr)
                return 1

            message = resp.get("message", {})

            # Print assistant text
            if message.get("content"):
                print(message["content"])

            # Check for tool calls
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                break

            messages.append(message)

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                print(f"[tool:{name}] {json.dumps(args)[:120]}", file=sys.stderr)
                result = execute_tool(name, args)

                messages.append({
                    "role": "tool",
                    "content": result,
                })

        return 0


# ── Backend: Anthropic API (direct, no CLI) ──────────────────────────────────

class AnthropicBackend:
    """Direct Anthropic API. For when claude CLI isn't available."""

    def __init__(self, model: str = "claude-sonnet-4-6", effort: str = "medium"):
        self.model = model
        self.effort = effort
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.max_iterations = {"low": 5, "medium": 15, "high": 30}.get(effort, 15)

    def _map_tools(self) -> list:
        """Convert OpenAI tool format to Anthropic format."""
        return [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in TOOLS
        ]

    def _chat(self, system: str, messages: list) -> dict:
        import urllib.request

        payload = {
            "model": self.model,
            "max_tokens": {"low": 2000, "medium": 4096, "high": 8192}.get(self.effort, 4096),
            "system": system,
            "messages": messages,
            "tools": self._map_tools(),
        }

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": str(e)}

    def run(self, prompt: str, instance_dir: Path, system_append: str = "",
            **kwargs) -> int:
        system_prompt = load_boot_chain(instance_dir)
        if system_append:
            system_prompt += f"\n\n---\n\n# [RUNTIME CONTEXT]\n\n{system_append}"

        messages = [{"role": "user", "content": prompt}]

        for iteration in range(self.max_iterations):
            resp = self._chat(system_prompt, messages)

            if "error" in resp:
                print(f"[kingdom-agent] Anthropic error: {resp['error']}", file=sys.stderr)
                return 1

            content_blocks = resp.get("content", [])
            stop_reason = resp.get("stop_reason", "")

            # Print text blocks
            for block in content_blocks:
                if block.get("type") == "text" and block.get("text"):
                    print(block["text"])

            # Check for tool use
            tool_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
            if not tool_blocks or stop_reason == "end_turn":
                break

            # Add assistant response to history
            messages.append({"role": "assistant", "content": content_blocks})

            # Execute tools and build result
            tool_results = []
            for tb in tool_blocks:
                name = tb["name"]
                args = tb.get("input", {})
                print(f"[tool:{name}] {json.dumps(args)[:120]}", file=sys.stderr)
                result = execute_tool(name, args)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb["id"],
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

        return 0


# ── Backend Registry ─────────────────────────────────────────────────────────

BACKENDS = {
    "claude": ClaudeBackend,
    "openai": OpenAIBackend,
    "ollama": OllamaBackend,
    "anthropic": AnthropicBackend,
}

# Model name mapping: normalize across backends
MODEL_MAP = {
    # Claude Code model names → backend-specific names
    "claude-opus-4-6":              {"claude": "claude-opus-4-6", "anthropic": "claude-opus-4-6", "openai": "gpt-4o", "ollama": "qwen2.5:72b"},
    "sonnet":                       {"claude": "sonnet", "anthropic": "claude-sonnet-4-6", "openai": "gpt-4o-mini", "ollama": "qwen2.5:32b"},
    "claude-haiku-4-5-20251001":    {"claude": "claude-haiku-4-5-20251001", "anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini", "ollama": "qwen2.5:7b"},
    # Generic tier names
    "high":   {"claude": "claude-opus-4-6", "anthropic": "claude-opus-4-6", "openai": "gpt-4o", "ollama": "qwen2.5:72b"},
    "medium": {"claude": "sonnet", "anthropic": "claude-sonnet-4-6", "openai": "gpt-4o-mini", "ollama": "qwen2.5:32b"},
    "low":    {"claude": "claude-haiku-4-5-20251001", "anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini", "ollama": "qwen2.5:7b"},
}


def resolve_model(model: str, backend: str) -> str:
    """Map a model name to the backend-specific equivalent."""
    if model in MODEL_MAP and backend in MODEL_MAP[model]:
        return MODEL_MAP[model][backend]
    return model  # pass through if not in map


# ── Heartbeat Integration ────────────────────────────────────────────────────

def generate_spawn_command(backend: str, prompt: str, role: str, instance_dir: str,
                           log_path: str) -> str:
    """Generate a spawn command line for heartbeat-runner.sh.

    This replaces the hardcoded `claude -p` lines with kingdom-agent calls.
    """
    agent_bin = str(TOOLS_DIR / "kingdom-agent.py")
    role_config = {
        "builder":    {"effort": "medium", "model": "medium"},
        "consultant": {"effort": "high",   "model": "high"},
        "quick":      {"effort": "low",    "model": "low"},
    }.get(role, {"effort": "medium", "model": "medium"})

    # Escape prompt for shell
    safe_prompt = prompt.replace("'", "'\\''")

    return (
        f"cd {instance_dir} && python3 {agent_bin} "
        f"-p '{safe_prompt}' "
        f"--backend {backend} "
        f"--model {role_config['model']} "
        f"--effort {role_config['effort']} "
        f"--skip-permissions --no-persist "
        f">> {log_path} 2>&1"
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kingdom Agent — Universal backend adapter for Kingdom OS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Backends:
  claude      Claude Code CLI (passthrough, full features)
  openai      OpenAI-compatible API (set OPENAI_API_KEY, OPENAI_BASE_URL)
  ollama      Local models via Ollama (set OLLAMA_HOST if not localhost)
  anthropic   Anthropic API direct (set ANTHROPIC_API_KEY)

Examples:
  kingdom-agent -p "Execute HEARTBEAT.md"
  kingdom-agent -p "Fix the login bug" --backend openai --model gpt-4o
  kingdom-agent -p "Review code" --backend ollama --model qwen2.5:72b
  kingdom-agent --boot-chain-only                # print assembled system prompt
        """
    )

    parser.add_argument("-p", "--prompt", help="Prompt to execute (headless mode)")
    parser.add_argument("--backend", default=os.environ.get("KINGDOM_BACKEND", "claude"),
                        choices=list(BACKENDS.keys()), help="Model backend")
    parser.add_argument("--model", default=os.environ.get("KINGDOM_MODEL", "sonnet"),
                        help="Model name (mapped to backend-specific name)")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"],
                        help="Reasoning effort level")
    parser.add_argument("--instance", default="beta", help="Instance name (alpha/beta/gamma/...)")
    parser.add_argument("--append-system-prompt", dest="system_append", default="",
                        help="Additional context to append to system prompt")
    parser.add_argument("--skip-permissions", action="store_true",
                        help="Skip tool permission prompts (for cron/headless)")
    parser.add_argument("--no-persist", action="store_true",
                        help="Don't persist session state")
    parser.add_argument("--fallback-model", default="",
                        help="Fallback model on overload")
    parser.add_argument("--output-format", default="",
                        help="Output format (stream-json, json)")
    parser.add_argument("--boot-chain-only", action="store_true",
                        help="Print assembled system prompt and exit")
    parser.add_argument("--spawn-cmd", nargs=4, metavar=("ROLE", "PROMPT", "DIR", "LOG"),
                        help="Generate a spawn command for heartbeat-runner.sh")

    args = parser.parse_args()

    instance_dir = resolve_instance_dir(args.instance)

    # Boot chain dump mode
    if args.boot_chain_only:
        print(load_boot_chain(instance_dir))
        return

    # Spawn command generation mode
    if args.spawn_cmd:
        role, prompt, dir_path, log_path = args.spawn_cmd
        print(generate_spawn_command(args.backend, prompt, role, dir_path, log_path))
        return

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    # Resolve model name for the chosen backend
    model = resolve_model(args.model, args.backend)

    # Instantiate backend
    BackendClass = BACKENDS[args.backend]

    if args.backend == "claude":
        backend = BackendClass(model=model, effort=args.effort)
        exit_code = backend.run(
            prompt=args.prompt,
            instance_dir=instance_dir,
            system_append=args.system_append,
            skip_permissions=args.skip_permissions,
            no_persist=args.no_persist,
            output_format=args.output_format,
            fallback_model=resolve_model(args.fallback_model, "claude") if args.fallback_model else "",
        )
    else:
        backend = BackendClass(model=model, effort=args.effort)
        exit_code = backend.run(
            prompt=args.prompt,
            instance_dir=instance_dir,
            system_append=args.system_append,
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
