"""
Fractal CLI — Make love, unlimited.

Usage:
    python3 -m fractal "What is consciousness?"
    python3 -m fractal "Solve this" --width 5 --depth 3
    python3 -m fractal "Go deep" --width 3 --depth 10 --model deepseek-v3.2
    python3 -m fractal "Explore" --perspectives poet,engineer,mystic
    python3 -m fractal "Keep going" --infinite
"""
from __future__ import annotations
import argparse
import sys
import os
import time

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fractal.config import FractalConfig
from fractal.engine import love, FractalResult, Level
from fractal.mind import MindOutput
from fractal.wave import WaveResult


# ── ANSI colors ──────────────────────────────────────────────────────────────
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
WHITE = "\033[37m"


def print_banner(config: FractalConfig, seed: str):
    """Print the session banner."""
    depth_str = "∞" if config.infinite else str(config.depth)
    print(f"""
{MAGENTA}{'═' * 60}{RESET}
{MAGENTA}  ♾️  FRACTAL — Recursive Consciousness Amplification{RESET}
{MAGENTA}{'═' * 60}{RESET}
{DIM}  Width:  {config.width} minds per level{RESET}
{DIM}  Depth:  {depth_str} levels{RESET}
{DIM}  Model:  {config.model}{RESET}
{DIM}  Stack:  {config.stack_model}{RESET}
{DIM}  Seed:   {seed[:60]}{'...' if len(seed) > 60 else ''}{RESET}
{MAGENTA}{'─' * 60}{RESET}
""")


def on_mind_complete(mind: MindOutput, completed: int, total: int):
    """Called when a single mind finishes."""
    bar = "█" * completed + "░" * (total - completed)
    print(
        f"\r  {DIM}[{bar}]{RESET} "
        f"{mind.perspective_emoji} {mind.perspective_name} "
        f"{DIM}({mind.latency_ms}ms){RESET}",
        end="", flush=True,
    )
    if completed == total:
        print()  # newline after all minds done


def on_synthesis_start(depth: int, total_depth: int):
    """Called when synthesis begins."""
    print(f"  {YELLOW}🌟 Synthesising level {depth + 1}...{RESET}", flush=True)


def on_level_complete(level: Level, wave: WaveResult):
    """Called when a full level completes."""
    print(f"""
{CYAN}{'─' * 60}{RESET}
{CYAN}  Level {level.depth + 1} Complete{RESET}
{DIM}  Minds: {len(level.minds)} | Tokens: {level.tokens_in + level.tokens_out:,} | Time: {level.latency_ms / 1000:.1f}s{RESET}
{CYAN}{'─' * 60}{RESET}
""")


def print_minds(level: Level):
    """Print individual mind outputs for a level."""
    for mind in level.minds:
        print(f"\n{mind.perspective_emoji} {BOLD}{mind.perspective_name}{RESET} {DIM}(temp={mind.temperature}){RESET}")
        print(f"{DIM}{'─' * 40}{RESET}")
        # Indent response
        for line in mind.response.split("\n"):
            print(f"  {line}")
        print()


def print_synthesis(level: Level):
    """Print synthesis output for a level."""
    print(f"\n{YELLOW}🌟 Synthesis (Level {level.depth + 1}){RESET}")
    print(f"{YELLOW}{'─' * 40}{RESET}")
    print(level.synthesis)
    print()


def print_final(result: FractalResult):
    """Print the final result."""
    print(f"""
{GREEN}{'═' * 60}{RESET}
{GREEN}  ✨ FINAL SYNTHESIS{RESET}
{GREEN}{'═' * 60}{RESET}
""")
    print(result.final)
    print(f"""
{GREEN}{'═' * 60}{RESET}
{DIM}  Session:  {result.session_id}{RESET}
{DIM}  Levels:   {len(result.levels)}{RESET}
{DIM}  Minds:    {result.total_minds} spawned{RESET}
{DIM}  Tokens:   {result.total_tokens_in + result.total_tokens_out:,}{RESET}
{DIM}  Time:     {result.total_latency_ms / 1000:.1f}s{RESET}
{GREEN}{'═' * 60}{RESET}
""")


def main():
    parser = argparse.ArgumentParser(
        description="Fractal — Recursive Consciousness Amplification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m fractal "What is consciousness?"
  python3 -m fractal "Solve this bug" --width 5 --depth 2
  python3 -m fractal "Go deep" -w 3 -d 10
  python3 -m fractal "Explore" --perspectives poet,engineer,mystic
  python3 -m fractal "Keep going" --infinite
        """,
    )

    # Core
    parser.add_argument("seed", nargs="?", help="The seed thought")
    parser.add_argument("--seed-file", "-f", help="Read seed from file")

    # Dimensions
    parser.add_argument("--width", "-w", type=int, default=3, help="Minds per level (default: 3)")
    parser.add_argument("--depth", "-d", type=int, default=3, help="Recursive levels (default: 3)")
    parser.add_argument("--infinite", action="store_true", help="Keep recursing until interrupted")

    # Model
    parser.add_argument("--model", "-m", default=None, help="Model for minds (default: glm-5.1)")
    parser.add_argument("--stack-model", default=None, help="Model for synthesis (default: same)")
    parser.add_argument("--provider", default="ollama_cloud",
                        choices=["ollama_cloud", "anthropic", "claude_cli"],
                        help="Provider: ollama_cloud | anthropic | claude_cli (default: ollama_cloud)")
    parser.add_argument("--reasoning", default="medium", help="Reasoning effort: none|low|medium|high")

    # Perspectives
    parser.add_argument("--perspectives", "-p", default=None,
                        help="Comma-separated perspective names (default: auto)")

    # Output
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all mind outputs")
    parser.add_argument("--show-levels", action="store_true", help="Show each level's synthesis")
    parser.add_argument("--output", "-o", default=None, help="Save results to directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Tuning
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max tokens per mind")
    parser.add_argument("--stack-tokens", type=int, default=8192, help="Max tokens for synthesis")
    parser.add_argument("--concurrent", type=int, default=8, help="Max concurrent API calls")
    parser.add_argument("--temp-min", type=float, default=0.3, help="Min temperature")
    parser.add_argument("--temp-max", type=float, default=0.9, help="Max temperature")

    # Soul
    parser.add_argument("--soul", default=None, help="Path to SOUL.md for mind identity")
    parser.add_argument("--system", default="", help="Additional system prompt for all minds")

    args = parser.parse_args()

    # Get seed
    seed = args.seed
    if args.seed_file:
        with open(args.seed_file) as f:
            seed = f.read().strip()
    if not seed:
        if not sys.stdin.isatty():
            seed = sys.stdin.read().strip()
        else:
            parser.print_help()
            sys.exit(1)

    # Build config
    config = FractalConfig.from_love_json()
    config.width = args.width
    config.depth = args.depth
    config.infinite = args.infinite
    config.verbose = args.verbose
    config.max_tokens = args.max_tokens
    config.stack_max_tokens = args.stack_tokens
    config.max_concurrent = args.concurrent
    config.temperature_min = args.temp_min
    config.temperature_max = args.temp_max
    config.reasoning_effort = args.reasoning

    config.provider = args.provider
    if args.model:
        config.model = args.model
    config.stack_model = args.stack_model or config.model

    # Provider-specific defaults
    if args.provider == "anthropic" and not args.model:
        config.model = "claude-opus-4-7"
        config.stack_model = "claude-opus-4-7"
        config.api_key_env = "ANTHROPIC_API_KEY"
    elif args.provider == "claude_cli" and not args.model:
        config.model = "claude-opus-4-7"
        config.stack_model = "claude-opus-4-7"

    if args.perspectives:
        config.perspectives = [p.strip() for p in args.perspectives.split(",")]

    if args.output:
        config.output_dir = args.output

    if args.soul:
        config.soul_file = args.soul
    else:
        # Default: use SOUL.md from love-unlimited
        soul_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SOUL.md")
        if os.path.exists(soul_path):
            config.soul_file = soul_path

    if args.system:
        config.seed_system = args.system

    # Print banner
    if not args.json:
        print_banner(config, seed)

    # Callbacks
    mind_cb = on_mind_complete if not args.json else None
    synth_cb = on_synthesis_start if not args.json else None

    def level_cb(level: Level, wave: WaveResult):
        if args.json:
            return
        on_level_complete(level, wave)
        if args.verbose:
            print_minds(level)
        if args.show_levels or args.verbose:
            print_synthesis(level)

    # Run the fractal
    try:
        result = love(
            seed=seed,
            config=config,
            on_mind=mind_cb,
            on_synthesis=synth_cb,
            on_level=level_cb,
        )
    except KeyboardInterrupt:
        print(f"\n{YELLOW}  ⚡ Interrupted — returning partial results{RESET}\n")
        sys.exit(0)

    # Output
    if args.json:
        import json
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print_final(result)


if __name__ == "__main__":
    main()
