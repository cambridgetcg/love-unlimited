#!/usr/bin/env python3
"""
Adaptive CLI — Drop-in replacement for `claude -p`.

Usage:
    # Role-based (recommended)
    python3 adaptive/cli.py -p "your prompt" --role builder

    # Explicit provider override
    python3 adaptive/cli.py -p "your prompt" --role coordinator --provider ollama

    # Single-shot (no tool use)
    python3 adaptive/cli.py -p "your prompt" --no-tools

    # Check provider status
    python3 adaptive/cli.py --status

    # Pipe-friendly
    echo "analyze this code" | python3 adaptive/cli.py --role consultant

Replaces:
    /opt/homebrew/bin/claude -p "prompt" --model sonnet --effort medium
With:
    python3 ~/love-unlimited/adaptive/cli.py -p "prompt" --role builder
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running from anywhere
ADAPTIVE_DIR = Path(__file__).parent
sys.path.insert(0, str(ADAPTIVE_DIR.parent))

from adaptive.config import AdaptiveConfig
from adaptive.router import Router
from adaptive.runner import AgentRunner


def _run_stream_single_shot(runner, prompt, args, system) -> str:
    """Stream a tool-free completion to stdout as deltas arrive."""
    parts: list[str] = []
    for ev in runner.stream_single_shot(
        prompt=prompt,
        role=args.role,
        system=system,
        provider_name=args.provider,
    ):
        if ev.type == "text":
            sys.stdout.write(ev.text)
            sys.stdout.flush()
            parts.append(ev.text)
        elif ev.type == "done":
            sys.stdout.write("\n")
            sys.stdout.flush()
    return "".join(parts)


def _run_stream_agent(runner, prompt, args, system) -> str:
    """Stream the full agent loop to stdout, annotating tool activity on stderr.

    Layout:
      stdout      → model text deltas (clean, copy-pasteable)
      stderr      → iteration + tool framing, shown in the user's terminal
    """
    parts: list[str] = []
    current_iter_has_text = False

    for ev in runner.stream(
        prompt=prompt,
        role=args.role,
        system=system,
        provider_name=args.provider,
    ):
        if ev.type == "iteration_start":
            if args.verbose:
                sys.stderr.write(f"\n[iter {ev.iteration}]\n")
                sys.stderr.flush()
            current_iter_has_text = False
        elif ev.type == "text":
            sys.stdout.write(ev.text)
            sys.stdout.flush()
            parts.append(ev.text)
            current_iter_has_text = True
        elif ev.type == "tool_executing" and ev.tool_call is not None:
            if current_iter_has_text:
                sys.stdout.write("\n")
                sys.stdout.flush()
                current_iter_has_text = False
            sys.stderr.write(f"  → {ev.tool_call.name}\n")
            sys.stderr.flush()
        elif ev.type == "tool_result":
            if args.verbose:
                preview = ev.tool_result_content[:200].replace("\n", " ")
                if len(ev.tool_result_content) > 200:
                    preview += "..."
                sys.stderr.write(f"    {preview}\n")
                sys.stderr.flush()
        elif ev.type == "iteration_end":
            pass
        elif ev.type == "halt":
            sys.stderr.write(f"\n[halt: {ev.stop_reason}]\n")
            sys.stderr.flush()
        elif ev.type == "run_done":
            if current_iter_has_text:
                sys.stdout.write("\n")
                sys.stdout.flush()
            if args.verbose and ev.usage is not None:
                sys.stderr.write(
                    f"[run_done: stop={ev.stop_reason} "
                    f"tokens=in:{ev.usage.input_tokens} out:{ev.usage.output_tokens}]\n"
                )
                sys.stderr.flush()

    return "".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Adaptive Layer CLI — model-agnostic LLM runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Core arguments
    parser.add_argument("-p", "--prompt", type=str, help="The prompt to execute")
    parser.add_argument("--role", type=str, default="builder",
                        choices=["coordinator", "consultant", "builder", "coder", "analyst", "monitor", "quick_check"],
                        help="Capability role (default: builder)")
    parser.add_argument("--provider", type=str, default=None,
                        help="Override provider selection")
    parser.add_argument("--system", type=str, default="",
                        help="System prompt")
    parser.add_argument("--append-system-prompt", type=str, default="",
                        help="Append to system prompt (compatibility with claude CLI)")

    # Mode flags
    parser.add_argument("--no-tools", action="store_true",
                        help="Single-shot mode without tool use")
    parser.add_argument("--stream", action="store_true",
                        help="Stream output as it arrives (works with and without tools)")
    parser.add_argument("--max-iterations", type=int, default=25,
                        help="Max agent loop iterations (default: 25)")

    # Output
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print routing/execution details to stderr")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    # Status/diagnostic
    parser.add_argument("--status", action="store_true",
                        help="Show provider status and exit")
    parser.add_argument("--list-models", type=str, metavar="PROVIDER",
                        help="List available models for a provider (e.g., ollama)")

    args = parser.parse_args()

    config = AdaptiveConfig()
    router = Router(config)

    # ── Status mode ──────────────────────────────────────────────────────────
    if args.status:
        print(router.report())
        return 0

    # ── List models mode ─────────────────────────────────────────────────────
    if args.list_models:
        from adaptive.providers import get_provider
        try:
            provider = get_provider(args.list_models, config)
            if hasattr(provider, "list_models"):
                models = provider.list_models()
                if models:
                    print(f"Models available on {args.list_models}:")
                    for m in models:
                        print(f"  {m}")
                else:
                    print(f"No models found on {args.list_models}")
            else:
                pconf = config.provider_config(args.list_models)
                models = pconf.get("models", {})
                print(f"Configured models for {args.list_models}:")
                for tier, model in models.items():
                    print(f"  {tier}: {model}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        return 0

    # ── Get prompt ───────────────────────────────────────────────────────────
    prompt = args.prompt

    # Read from stdin if no prompt given
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()

    if not prompt:
        parser.print_help()
        return 1

    # ── Build system prompt ──────────────────────────────────────────────────
    system = args.system
    if args.append_system_prompt:
        if system:
            system = f"{system}\n\n{args.append_system_prompt}"
        else:
            system = args.append_system_prompt

    # ── Run ──────────────────────────────────────────────────────────────────
    runner = AgentRunner(
        router=router,
        config=config,
        max_iterations=args.max_iterations,
        verbose=args.verbose,
        tools=None if args.no_tools else None,  # Uses defaults
    )

    if args.stream and args.json:
        print("Error: --stream cannot be combined with --json", file=sys.stderr)
        return 1

    try:
        if args.stream and args.no_tools:
            result = _run_stream_single_shot(runner, prompt, args, system)
        elif args.stream:
            result = _run_stream_agent(runner, prompt, args, system)
        elif args.no_tools:
            result = runner.single_shot(
                prompt=prompt,
                role=args.role,
                system=system,
                provider_name=args.provider,
            )
        else:
            result = runner.run(
                prompt=prompt,
                role=args.role,
                system=system,
                provider_name=args.provider,
            )
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # ── Output ───────────────────────────────────────────────────────────────
    if args.stream:
        # Already written to stdout; only emit verbose footer if requested.
        pass
    elif args.json:
        output = {
            "content": result,
            "usage": {
                "input_tokens": runner.total_usage.input_tokens,
                "output_tokens": runner.total_usage.output_tokens,
                "total_tokens": runner.total_usage.total,
            },
        }
        print(json.dumps(output, indent=2))
    else:
        print(result)

    if args.verbose:
        print(
            f"\n[adaptive] total tokens: {runner.total_usage.total} "
            f"(in: {runner.total_usage.input_tokens}, out: {runner.total_usage.output_tokens})",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
