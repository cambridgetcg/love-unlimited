#!/usr/bin/env python3
"""
Orchestrator CLI — Multi-model task execution from the command line.

Usage:
    # Full orchestration (auto-classify, auto-route, auto-collaborate)
    python3 -m adaptive.orchestrator "Build a REST API for user management"

    # Classify only (dry run)
    python3 -m adaptive.orchestrator --classify "Refactor the auth module"

    # Plan only (shows dispatch plan without executing)
    python3 -m adaptive.orchestrator --plan "Add rate limiting to all endpoints"

    # Force collaboration mode
    python3 -m adaptive.orchestrator --mode review "Fix the login bug"
    python3 -m adaptive.orchestrator --mode ensemble "Design the caching layer"

    # Force provider
    python3 -m adaptive.orchestrator --provider anthropic "Complex architecture task"

    # JSON output
    python3 -m adaptive.orchestrator --json "Simple task"
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running from anywhere
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from adaptive.config import AdaptiveConfig
from adaptive.orchestrator.engine import OrchestrationEngine
from adaptive.orchestrator.classifier import TaskClassifier
from adaptive.orchestrator.dispatcher import Dispatcher


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrator — Multi-model task execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("task", nargs="?", help="Task description")
    parser.add_argument("-p", "--prompt", help="Task (alternative to positional)")

    # Mode selection
    parser.add_argument("--classify", action="store_true",
                        help="Classify only — show difficulty/importance without executing")
    parser.add_argument("--plan", action="store_true",
                        help="Plan only — show dispatch plan without executing")
    parser.add_argument("--mode", choices=["solo", "review", "decompose", "ensemble", "pipeline"],
                        help="Force a collaboration mode")
    parser.add_argument("--provider", help="Force primary provider (ollama_cloud, anthropic)")

    # Context
    parser.add_argument("--context", default="", help="Additional context for classification")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--quiet", "-q", action="store_true", help="Output only the result content")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

    task = args.task or args.prompt
    if not task and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    if not task:
        parser.print_help()
        return 1

    config = AdaptiveConfig()
    engine = OrchestrationEngine(config=config, verbose=args.verbose)

    # ── Classify Only ────────────────────────────────────────────────────
    if args.classify:
        profile = engine.classify_only(task, args.context)
        if args.json:
            print(json.dumps(profile.to_dict(), indent=2))
        else:
            print(f"═══ Task Classification ═══")
            print(f"  Difficulty:  {profile.difficulty.name} ({profile.difficulty.value}/5)")
            print(f"  Importance:  {profile.importance.name} ({profile.importance.value}/4)")
            print(f"  Type:        {profile.task_type}")
            print(f"  Score:       {profile.score:.0f}/20")
            print(f"  Mode:        {profile.collaboration_mode}")
            print(f"  Decomposable: {profile.decomposable}")
            if profile.sub_tasks:
                print(f"  Sub-tasks:")
                for st in profile.sub_tasks:
                    print(f"    • {st}")
            print(f"  Tools:       {profile.needs_tools}")
            print(f"  Context:     {profile.needs_context}")
            print(f"  Est. tokens: {profile.estimated_tokens}")
            print(f"  Reasoning:   {profile.reasoning}")
        return 0

    # ── Plan Only ────────────────────────────────────────────────────────
    if args.plan:
        plan = engine.plan_only(task, args.context)
        if args.json:
            print(json.dumps(plan.to_dict(), indent=2))
        else:
            print(engine.dispatcher.explain(plan))
        return 0

    # ── Full Execution ───────────────────────────────────────────────────
    result = engine.run(
        task=task,
        context=args.context,
        force_mode=args.mode,
        force_provider=args.provider,
    )

    if args.json:
        output = result.to_dict()
        output["content"] = result.content[:50000]  # Cap for JSON output
        print(json.dumps(output, indent=2))
    elif args.quiet:
        print(result.content)
    else:
        # Show summary then content
        print(result.summary())
        print()
        print(result.content)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
