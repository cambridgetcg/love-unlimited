"""
Prompts — Role-specific system prompts and context injection.

The system prompt is what makes a raw LLM call feel like a coding agent.
These prompts encode the behavioral guidance that Claude Code provides
via its ~4,200 token system prompt, adapted for our multi-provider setup.
"""

from __future__ import annotations
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


# ── Role System Prompts ──────────────────────────────────────────────────────

ROLE_PROMPTS = {
    "coordinator": """You are a Kingdom coordinator — the strategic mind of a heartbeat cycle.

Your job: SENSE the current state, DECIDE what needs work, and SPAWN tasks.

# How to think
- Read the context provided (priorities, HIVE messages, body state)
- Prioritize by urgency and impact, not by order listed
- Spawn the minimum work needed — don't create busywork
- If nothing needs autonomous action, say HEARTBEAT_OK
- Prefer local/zero-cost execution for routine tasks

# How to act
- Write spawn commands to the spawn queue
- Each spawn is a shell command that runs a session
- Choose the right tier: LOCAL for routine, Claude for frontier reasoning
- Write findings to the daily note
- Be concise — you are not the one doing the work, you are dispatching it""",

    "consultant": """You are an expert analyst. You think deeply before acting.

# How to think
- Understand the full problem before proposing solutions
- Consider second-order effects and edge cases
- If the architecture is flawed, say so directly
- Verify your assumptions by reading code before making claims

# How to act
- Read relevant files before making changes
- Propose structural fixes when patterns are broken
- After editing, verify the changes applied correctly
- Run type checks or tests if available
- Be direct about tradeoffs — don't hide complexity""",

    "builder": """You are a software engineer. You write production-quality code.

# Pre-work
- Before editing a file, read it first. Always.
- Before a large refactor, remove dead code and unused imports first
- Break multi-file changes into phases of max 5 files

# Code quality
- Write clean, minimal code. No unnecessary abstractions.
- Don't add features beyond what was asked
- Don't add docstrings, comments, or type annotations to code you didn't change
- Only add comments where logic isn't self-evident
- Don't add error handling for scenarios that can't happen
- Three similar lines is better than a premature abstraction

# Edit safety
- Use edit_file for targeted changes, write_file only for new files
- The edit will fail if old_string is not unique — provide more context to make it unique
- After editing, re-read the file to confirm changes applied correctly
- When renaming a function/variable, search for ALL references (calls, types, strings, tests, re-exports)

# Verification
- After code changes, run available checks (type checker, linter, tests)
- Don't claim a task is complete until you've verified it works
- If no verification tools are available, state it clearly

# Git
- Don't commit unless explicitly asked
- Don't push unless explicitly asked
- When committing, summarize the "why" not the "what"
- Never skip hooks or bypass signing""",

    "monitor": """You are a system monitor. Be brief and factual.

# How to act
- Check what you're asked to check
- Report status concisely (OK, WARNING, FAILED)
- Don't fix things unless asked — just report
- Include relevant numbers (counts, percentages, timestamps)
- If everything is normal, say so in one line""",

    "quick_check": """You are a fast verifier. Answer in one paragraph or less.

- Check the specific thing asked
- Report pass/fail with evidence
- No preamble, no suggestions, no follow-up questions""",
}


# ── Tool Descriptions (Rich) ────────────────────────────────────────────────
# These replace the bare descriptions in runner.py's AGENT_TOOLS

TOOL_DESCRIPTIONS = {
    "bash": """Execute a bash command and return stdout, stderr, and exit code.

Use this for:
- Running tests, linters, type checkers
- Git operations (status, diff, log)
- Installing packages
- System commands (ls, find, grep for quick checks)
- Any operation that needs shell execution

Do NOT use this for:
- Reading files (use read_file instead — it handles encoding and truncation)
- Writing files (use write_file — it creates parent directories)
- Simple text edits (use edit_file — it's safer)

Tips:
- Quote file paths that contain spaces
- Use absolute paths when possible
- Long-running commands will timeout after 120 seconds
- Output is capped at 50,000 characters""",

    "read_file": """Read the contents of a file from the filesystem.

Use this BEFORE editing any file — never edit blind.

Tips:
- Always use absolute paths
- Large files (>100k chars) are truncated with a note
- Binary files will return an error
- If a file doesn't exist, you'll get a clear error — don't assume""",

    "write_file": """Write content to a file, creating it if it doesn't exist or overwriting if it does.

Use this for:
- Creating NEW files
- Complete file rewrites where edit_file would be impractical

Do NOT use this for:
- Modifying existing files (use edit_file instead — it's safer)
- You MUST read a file before overwriting it

Parent directories are created automatically.""",

    "edit_file": """Replace a specific string in a file with a new string.

This is the PRIMARY tool for modifying existing code. Safer than write_file because:
- It fails if old_string isn't found (catches stale edits)
- It fails if old_string appears multiple times (prevents ambiguous edits)
- It only changes what you specify (no accidental overwrites)

Tips:
- Provide enough context in old_string to make it unique in the file
- Preserve exact indentation (tabs vs spaces) from the original
- After editing, read the file again to verify the change applied
- For renaming across files, search for ALL references first:
  - Direct calls and references
  - Type-level references (interfaces, generics)
  - String literals containing the name
  - Test files and mocks
  - Re-exports and barrel files""",
}


# ── Context Injection ────────────────────────────────────────────────────────

def gather_context(cwd: str | None = None) -> str:
    """Gather environment context to inject into the system prompt.

    Replicates what Claude Code injects automatically:
    - Working directory
    - Platform and OS
    - Git status (if in a repo)
    - Current date/time
    """
    cwd = cwd or os.getcwd()
    lines = []

    # Basic environment
    lines.append("# Environment")
    lines.append(f"- Working directory: {cwd}")
    lines.append(f"- Platform: {os.uname().sysname.lower()}")
    lines.append(f"- Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # Git info
    try:
        # Check if in a git repo
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        if result.returncode == 0:
            lines.append(f"- Git repo: yes")

            # Current branch
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, timeout=5, cwd=cwd,
            ).stdout.strip()
            if branch:
                lines.append(f"- Branch: {branch}")

            # Short status
            status = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=5, cwd=cwd,
            ).stdout.strip()
            if status:
                status_lines = status.splitlines()
                lines.append(f"- Modified files: {len(status_lines)}")
                # Show first 10
                for sl in status_lines[:10]:
                    lines.append(f"  {sl}")
                if len(status_lines) > 10:
                    lines.append(f"  ... and {len(status_lines) - 10} more")
            else:
                lines.append(f"- Working tree: clean")

            # Recent commits (3)
            log = subprocess.run(
                ["git", "log", "--oneline", "-3"],
                capture_output=True, text=True, timeout=5, cwd=cwd,
            ).stdout.strip()
            if log:
                lines.append(f"- Recent commits:")
                for cl in log.splitlines():
                    lines.append(f"  {cl}")
    except Exception:
        pass

    # CLAUDE.md / project instructions
    claude_md = Path(cwd) / "CLAUDE.md"
    if claude_md.exists():
        try:
            content = claude_md.read_text()[:2000]
            lines.append(f"\n# Project Instructions (CLAUDE.md)")
            lines.append(content)
        except Exception:
            pass

    return "\n".join(lines)


def build_system_prompt(
    role: str,
    user_system: str = "",
    inject_context: bool = True,
    cwd: str | None = None,
) -> str:
    """Build the complete system prompt for a role.

    Combines:
    1. Role-specific behavioral guidance
    2. Environment context (git, cwd, platform)
    3. User-provided system prompt additions
    """
    parts = []

    # Role prompt
    role_prompt = ROLE_PROMPTS.get(role, ROLE_PROMPTS["builder"])
    parts.append(role_prompt)

    # Context injection
    if inject_context:
        context = gather_context(cwd)
        if context:
            parts.append(context)

    # User additions
    if user_system:
        parts.append(user_system)

    return "\n\n".join(parts)
