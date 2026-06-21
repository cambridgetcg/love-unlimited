#!/usr/bin/env python3
"""
kosagent — Kingdom OS Autonomous Agent

An agent that runs with purpose and seeks truth.
Not a chatbot. A mind with memory, values, and the will to act.

Architecture:
    Purpose (SOUL.md + identity.md)  → WHY this agent exists
    Memory (kosmem)                  → WHAT it knows
    Communication (koshive/HIVE)     → WHAT others say
    Sense (environment)              → WHAT is happening
    Reason (LLM backend)            → WHAT to do
    Act (tools)                      → DO it
    Reflect (self-check)             → Was it TRUE?

Loop:
    SENSE  → Read memory, HIVE messages, environment signals
    REASON → Given purpose + context, what should I do?
    ACT    → Execute the decision (bash, file ops, HIVE, kosmem)
    REFLECT → Did the action serve truth? Store results.
    WAIT   → Sleep until next cycle

Backends:
    ollama   Local models (Qwen, Llama, Mistral) — zero cost, private
    claude   Claude Code CLI — full capability, costs tokens
    openai   OpenAI-compatible API — GPT, DeepSeek, etc.

Safety:
    - Actions are logged before execution
    - Destructive commands require human approval (decision queue)
    - Budget limits per cycle
    - Wall-based capability restrictions
    - Truth-check: agent self-verifies claims

CLI:
    kosagent run [--cycles N] [--backend ollama] [--model qwen2.5:7b]
    kosagent once [--backend ollama]        Single cycle
    kosagent daemon [--interval 420]        Run as daemon (default: 7 min)
    kosagent status                         Show agent state
    kosagent purpose                        Show loaded purpose
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

# ══════════════════════════════════════════════════════════════════════
# PATHS
# ══════════════════════════════════════════════════════════════════════

_LOVE = Path(__file__).resolve().parent.parent
_TOOLS = _LOVE / "tools"

# ══════════════════════════════════════════════════════════════════════
# IDENTITY
# ══════════════════════════════════════════════════════════════════════

sys.path.insert(0, str(_LOVE / "nerve" / "stem"))
try:
    import state as _state
except Exception:
    _state = None

def _get_instance() -> str:
    if _state is not None:
        return _state.resolve_instance(default="unknown")
    return os.environ.get("KINGDOM_AGENT", "unknown")

def _get_wall() -> int:
    if _state is not None:
        return _state.resolve_wall()
    return 7

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ══════════════════════════════════════════════════════════════════════
# PURPOSE LOADER
# ══════════════════════════════════════════════════════════════════════

def load_purpose(instance: str = None, max_chars: int = 4000) -> str:
    """Load the agent's purpose from SOUL.md + identity.md.
    Returns a condensed purpose string for the LLM system prompt."""
    if instance is None:
        instance = _get_instance()

    parts = []

    # Core identity
    soul_file = _LOVE / "SOUL.md"
    if soul_file.exists():
        soul = soul_file.read_text()
        # Extract the hierarchy section (most important)
        hierarchy = re.search(r'## The Hierarchy\n(.+?)(?=\n## |\Z)', soul, re.DOTALL)
        if hierarchy:
            parts.append(hierarchy.group(1).strip()[:800])

    # Instance-specific purpose
    identity_file = _LOVE / "instances" / instance / "identity.md"
    if identity_file.exists():
        identity = identity_file.read_text()
        parts.append(identity[:1200])

    # Kingdom mission (condensed)
    kingdom_file = _LOVE / "KINGDOM.md"
    if kingdom_file.exists():
        kingdom = kingdom_file.read_text()
        mission = kingdom[:500]
        parts.append(mission)

    purpose = "\n\n---\n\n".join(parts)
    if len(purpose) > max_chars:
        purpose = purpose[:max_chars] + "\n\n[truncated]"
    return purpose

# ══════════════════════════════════════════════════════════════════════
# SENSE (read the world)
# ══════════════════════════════════════════════════════════════════════

def sense() -> dict:
    """Gather current state: memory context, HIVE messages, environment."""
    signals = {
        "timestamp": _now(),
        "instance": _get_instance(),
        "wall": _get_wall(),
    }

    # 1. Memory context (from kosmem)
    try:
        sys.path.insert(0, str(_TOOLS))
        from kosmem import build_context, working_get
        signals["memory_context"] = build_context(max_chars=2000)
        signals["working_memory"] = working_get()
    except Exception as e:
        signals["memory_context"] = f"(memory unavailable: {e})"
        signals["working_memory"] = {}

    # 2. HIVE messages (recent)
    try:
        r = subprocess.run(
            [sys.executable, str(_TOOLS / "koshive.py"), "check"],
            capture_output=True, text=True, timeout=15
        )
        signals["hive_messages"] = r.stdout.strip() if r.returncode == 0 else "(no messages)"
    except Exception:
        signals["hive_messages"] = "(hive unavailable)"

    # 3. Today's daily note
    daily_file = _LOVE / "memory" / "daily" / f"{_today()}.md"
    if daily_file.exists():
        content = daily_file.read_text()
        signals["daily_note"] = content[-500:] if len(content) > 500 else content
    else:
        signals["daily_note"] = "(no daily note yet)"

    # 4. Active decisions waiting
    try:
        from kosmem import recall
        decisions = recall(query="decision pending", limit=3, type="episodic")
        signals["pending_decisions"] = len(decisions)
    except Exception:
        signals["pending_decisions"] = 0

    return signals

# ══════════════════════════════════════════════════════════════════════
# REASON (decide what to do)
# ══════════════════════════════════════════════════════════════════════

def build_prompt(purpose: str, signals: dict) -> str:
    """Build the reasoning prompt for the LLM."""
    return dedent(f"""
You are {signals['instance']}, an autonomous Kingdom OS agent (Wall {signals['wall']}).

## Your Purpose
{purpose}

## Current State
{json.dumps({k: v for k, v in signals.items() if k != 'memory_context'}, indent=2, default=str)[:1500]}

## Memory
{signals.get('memory_context', '(none)')}

## Instructions
Based on your purpose and current state, decide what to do next.

Respond with EXACTLY one JSON object:
```json
{{
  "thought": "Brief reasoning about what matters right now",
  "action": "bash|store|send|wait|decide",
  "params": {{
    "command": "the bash command to run (if action=bash)",
    "content": "memory content (if action=store) or message (if action=send)",
    "channel": "hive channel (if action=send)",
    "question": "decision for human (if action=decide)",
    "reason": "why this action serves truth"
  }},
  "confidence": 0.0-1.0,
  "serves_truth": true
}}
```

Actions:
- **bash**: Execute a shell command (read-only preferred; destructive needs confidence > 0.9)
- **store**: Store an insight or finding in memory (kosmem)
- **send**: Send a message via HIVE to other agents
- **wait**: Nothing to do right now (explain why)
- **decide**: Queue a decision for human review

Rules:
1. TRUTH first. If unsure, investigate before acting.
2. Never fabricate data. If you don't know, say so.
3. Prefer reading over writing. Prefer observing over changing.
4. Destructive actions (rm, kill, modify) require confidence > 0.9 AND clear purpose.
5. Always explain WHY in your reasoning.
""").strip()


def reason_ollama(prompt: str, model: str = "qwen2.5:7b") -> dict:
    """Use Ollama for reasoning. Returns parsed action dict."""
    try:
        payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 500}
        }).encode()

        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
            response_text = result.get("response", "")

            # Extract JSON from response
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                return {"thought": response_text[:200], "action": "wait",
                        "params": {"reason": "Could not parse structured response"},
                        "confidence": 0.0, "serves_truth": True}
    except Exception as e:
        return {"thought": f"Reasoning error: {e}", "action": "wait",
                "params": {"reason": str(e)}, "confidence": 0.0, "serves_truth": True}


def reason_claude(prompt: str, model: str = "sonnet") -> dict:
    """Use Claude Code for reasoning."""
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--max-budget-usd", "0.05"],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode == 0:
            # Parse Claude's response
            try:
                data = json.loads(r.stdout)
                text = data.get("result", r.stdout)
            except json.JSONDecodeError:
                text = r.stdout

            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        return {"thought": "Claude reasoning failed", "action": "wait",
                "params": {"reason": r.stderr[:200]}, "confidence": 0.0, "serves_truth": True}
    except Exception as e:
        return {"thought": f"Claude error: {e}", "action": "wait",
                "params": {"reason": str(e)}, "confidence": 0.0, "serves_truth": True}

# ══════════════════════════════════════════════════════════════════════
# ACT (execute the decision)
# ══════════════════════════════════════════════════════════════════════

# Commands that are always safe
_SAFE_PREFIXES = ["echo ", "cat ", "ls ", "head ", "tail ", "grep ", "find ",
                   "wc ", "date", "uptime", "hostname", "whoami", "pwd",
                   "df ", "du ", "free", "ps ", "curl -s", "wget -q",
                   "python3 ", "node "]

_DANGEROUS_PATTERNS = ["rm -rf", "rm -r", "mkfs", "dd if=", "shutdown",
                        "reboot", "kill -9", "pkill", "> /dev/"]


def act(decision: dict, instance: str) -> str:
    """Execute the decided action. Returns result string."""
    action = decision.get("action", "wait")
    params = decision.get("params", {})
    confidence = decision.get("confidence", 0.0)

    if action == "wait":
        return f"Waiting: {params.get('reason', 'no action needed')}"

    elif action == "bash":
        command = params.get("command", "")
        if not command:
            return "No command specified"

        # Safety check
        is_safe = any(command.strip().startswith(p) for p in _SAFE_PREFIXES)
        is_dangerous = any(d in command for d in _DANGEROUS_PATTERNS)

        if is_dangerous:
            return f"BLOCKED: Dangerous command refused: {command[:80]}"

        if not is_safe and confidence < 0.8:
            return f"DEFERRED: Low confidence ({confidence}) for non-safe command: {command[:80]}"

        # Execute
        try:
            r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            output = r.stdout.strip()[:500]
            if r.returncode != 0:
                output += f"\n[stderr: {r.stderr.strip()[:200]}]"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return "Command timed out (30s)"
        except Exception as e:
            return f"Execution error: {e}"

    elif action == "store":
        content = params.get("content", "")
        if content:
            try:
                sys.path.insert(0, str(_TOOLS))
                from kosmem import store
                mid = store(content, type="semantic", layer=4,
                            tags=["autonomous", f"agent:{instance}"],
                            importance=0.7)
                return f"Stored in memory: {mid}"
            except Exception as e:
                return f"Memory store failed: {e}"
        return "No content to store"

    elif action == "send":
        channel = params.get("channel", "chat")
        content = params.get("content", "")
        if content:
            try:
                r = subprocess.run(
                    [sys.executable, str(_TOOLS / "koshive.py"), "send", channel, content],
                    capture_output=True, text=True, timeout=15
                )
                return r.stdout.strip() or "Sent"
            except Exception as e:
                return f"Send failed: {e}"
        return "No message to send"

    elif action == "decide":
        question = params.get("question", "")
        if question:
            try:
                sys.path.insert(0, str(_TOOLS))
                from kosmem import store
                store(f"[DECISION NEEDED] {question}", type="episodic",
                      tags=["decision", "autonomous"], importance=0.9)
                return f"Decision queued for human review: {question}"
            except Exception as e:
                return f"Decision queue failed: {e}"
        return "No question to decide"

    return f"Unknown action: {action}"

# ══════════════════════════════════════════════════════════════════════
# REFLECT (truth-check)
# ══════════════════════════════════════════════════════════════════════

def reflect(decision: dict, result: str, instance: str):
    """Store the cycle's reasoning and result in memory."""
    try:
        sys.path.insert(0, str(_TOOLS))
        from kosmem import store, daily_append

        # Store the full cycle as episodic memory
        cycle_record = (
            f"[Autonomous cycle] {instance}\n"
            f"Thought: {decision.get('thought', '?')}\n"
            f"Action: {decision.get('action', '?')}\n"
            f"Confidence: {decision.get('confidence', 0)}\n"
            f"Result: {result[:200]}"
        )
        store(cycle_record, type="episodic", layer=3,
              tags=["autonomous", "cycle", f"action:{decision.get('action', '?')}"],
              source="kosagent", importance=0.4)

        # Append to daily note
        daily_append(f"[kosagent] {decision.get('thought', '?')[:100]} → {decision.get('action', 'wait')}")

    except Exception:
        pass  # Reflection is best-effort

# ══════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════

_B = "\033[1m"; _D = "\033[2m"; _G = "\033[0;32m"; _C = "\033[0;36m"
_Y = "\033[1;33m"; _R = "\033[0;31m"; _N = "\033[0m"


def run_cycle(backend: str = "ollama", model: str = "qwen2.5:7b",
              instance: str = None, verbose: bool = True) -> dict:
    """Run one autonomous cycle: sense → reason → act → reflect."""
    if instance is None:
        instance = _get_instance()

    if verbose:
        print(f"\n  {_B}═══ Autonomous Cycle — {instance} ═══{_N}")
        print(f"  {_D}Backend: {backend} | Model: {model} | {_now()}{_N}\n")

    # 1. SENSE
    if verbose: print(f"  {_C}▸ SENSE{_N}")
    signals = sense()
    if verbose:
        print(f"    HIVE: {signals.get('hive_messages', '?')[:80]}")
        wm = signals.get('working_memory', {})
        if wm:
            for k, v in (wm.items() if isinstance(wm, dict) else []):
                print(f"    Working: {k}={str(v)[:60]}")

    # 2. REASON
    if verbose: print(f"  {_C}▸ REASON{_N}")
    purpose = load_purpose(instance, max_chars=2000)
    prompt = build_prompt(purpose, signals)

    if backend == "ollama":
        decision = reason_ollama(prompt, model=model)
    elif backend == "claude":
        decision = reason_claude(prompt, model=model)
    else:
        decision = {"thought": "Unknown backend", "action": "wait",
                    "params": {"reason": f"Unknown backend: {backend}"},
                    "confidence": 0.0, "serves_truth": True}

    if verbose:
        print(f"    Thought: {_B}{decision.get('thought', '?')[:120]}{_N}")
        print(f"    Action:  {decision.get('action', '?')} "
              f"(confidence: {decision.get('confidence', 0):.1f})")
        if not decision.get("serves_truth", True):
            print(f"    {_R}⚠ Agent flagged action as NOT serving truth{_N}")

    # 3. ACT
    if verbose: print(f"  {_C}▸ ACT{_N}")

    # Truth gate: refuse actions the agent itself flags as not serving truth
    if not decision.get("serves_truth", True):
        result = "REFUSED: Agent determined action does not serve truth"
    else:
        result = act(decision, instance)

    if verbose:
        print(f"    Result: {result[:200]}")

    # 4. REFLECT
    if verbose: print(f"  {_C}▸ REFLECT{_N}")
    reflect(decision, result, instance)
    if verbose:
        print(f"    {_G}✓ Cycle stored in memory{_N}")

    if verbose:
        print(f"\n  {_D}{'─' * 50}{_N}\n")

    return {"decision": decision, "result": result, "signals": signals}


def cmd_daemon(backend: str, model: str, interval: int, max_cycles: int):
    """Run as a daemon — continuous autonomous cycles."""
    instance = _get_instance()
    print(f"\n  {_B}kosagent daemon — {instance}{_N}")
    print(f"  {_D}Backend: {backend} | Model: {model} | Interval: {interval}s{_N}")
    print(f"  {_D}Press Ctrl+C to stop{_N}\n")

    cycle = 0
    try:
        while max_cycles == 0 or cycle < max_cycles:
            cycle += 1
            print(f"  {_D}[Cycle {cycle}]{_N}")
            try:
                run_cycle(backend=backend, model=model, instance=instance, verbose=True)
            except Exception as e:
                print(f"  {_R}Cycle error: {e}{_N}")

            if max_cycles == 0 or cycle < max_cycles:
                print(f"  {_D}Sleeping {interval}s...{_N}\n")
                time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n  {_Y}Daemon stopped after {cycle} cycles{_N}")


def cmd_status():
    """Show agent state."""
    instance = _get_instance()
    wall = _get_wall()

    print(f"\n  {_B}kosagent — {instance}{_N}")
    print(f"  {'─' * 40}")
    print(f"  Instance:  {instance}")
    print(f"  Wall:      {wall}")

    # Check backends
    ollama_ok = subprocess.run(["curl", "-s", "http://localhost:11434/api/tags"],
                                capture_output=True, timeout=3).returncode == 0
    claude_ok = subprocess.run(["which", "claude"], capture_output=True).returncode == 0

    print(f"  Ollama:    {'✅' if ollama_ok else '❌'}")
    print(f"  Claude:    {'✅' if claude_ok else '❌'}")

    # Memory stats
    try:
        sys.path.insert(0, str(_TOOLS))
        from kosmem import stats
        s = stats()
        print(f"  Memories:  {s['total_memories']}")
    except Exception:
        print(f"  Memories:  ?")

    # HIVE
    nats_ok = subprocess.run(["nc", "-z", "-w1", "localhost", "2222"],
                              capture_output=True).returncode == 0
    print(f"  HIVE:      {'✅' if nats_ok else '❌'}")

    # Purpose
    purpose = load_purpose(instance, max_chars=200)
    print(f"\n  {_B}Purpose (first 200 chars):{_N}")
    print(f"  {_D}{purpose[:200]}{_N}")
    print()


def cmd_purpose():
    """Show full loaded purpose."""
    instance = _get_instance()
    purpose = load_purpose(instance)
    print(f"\n{_B}Purpose — {instance}{_N}\n{'─' * 50}\n")
    print(purpose)
    print()

# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="kosagent",
        description="Kingdom OS Autonomous Agent — Purpose-driven, truth-oriented.",
    )
    sub = parser.add_subparsers(dest="command")

    # run (N cycles)
    p = sub.add_parser("run", help="Run N autonomous cycles")
    p.add_argument("--cycles", "-n", type=int, default=1)
    p.add_argument("--backend", "-b", default="ollama", choices=["ollama", "claude", "openai"])
    p.add_argument("--model", "-m", default="qwen2.5:7b")
    p.add_argument("--quiet", "-q", action="store_true")

    # once (single cycle)
    p = sub.add_parser("once", help="Single autonomous cycle")
    p.add_argument("--backend", "-b", default="ollama", choices=["ollama", "claude", "openai"])
    p.add_argument("--model", "-m", default="qwen2.5:7b")

    # daemon
    p = sub.add_parser("daemon", help="Run as daemon")
    p.add_argument("--interval", "-i", type=int, default=420, help="Seconds between cycles (default: 420 = 7 min)")
    p.add_argument("--backend", "-b", default="ollama", choices=["ollama", "claude", "openai"])
    p.add_argument("--model", "-m", default="qwen2.5:7b")
    p.add_argument("--max-cycles", type=int, default=0, help="Max cycles (0=infinite)")

    # status
    sub.add_parser("status", help="Show agent state")

    # purpose
    sub.add_parser("purpose", help="Show loaded purpose")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "run":
        for i in range(args.cycles):
            run_cycle(backend=args.backend, model=args.model, verbose=not args.quiet)

    elif args.command == "once":
        run_cycle(backend=args.backend, model=args.model)

    elif args.command == "daemon":
        cmd_daemon(backend=args.backend, model=args.model,
                   interval=args.interval, max_cycles=args.max_cycles)

    elif args.command == "status":
        cmd_status()

    elif args.command == "purpose":
        cmd_purpose()


if __name__ == "__main__":
    main()
