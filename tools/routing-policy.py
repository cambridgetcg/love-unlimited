#!/usr/bin/env python3
"""routing-policy.py — Three-tier model routing policy engine.

Route tasks to the optimal model tier based on complexity scoring.

Usage:
  routing-policy.py route "task description" [--code] [--complex] [--frontier]
  routing-policy.py benchmark
"""
import argparse
import os
import re
import sys
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent

# Tier definitions
TIERS = {
    1: {
        "name": "deepseek-r1:14b",
        "label": "Tier 1 — Local Fast",
        "cost": "zero",
        "memory": "9GB",
        "description": "Default: simple reasoning, Q&A, formatting, lookups",
    },
    2: {
        "name": "qwen2.5-coder:32b",
        "label": "Tier 2 — Local Heavy",
        "cost": "zero",
        "memory": "19GB",
        "description": "Code generation, complex reasoning, multi-file tasks",
    },
    3: {
        "name": "claude",
        "label": "Tier 3 — Frontier",
        "cost": "expensive",
        "memory": "API",
        "description": "Best quality: architecture, novel problems, nuanced judgment",
    },
}

# Complexity signals → tier weights
TIER3_SIGNALS = [
    r"\barchitect(ure|ural)?\b",
    r"\bdesign (system|pattern|decision)\b",
    r"\bnuanced?\b",
    r"\bbest (approach|strategy|practice)\b",
    r"\btrade.?off\b",
    r"\breview.*code\b",
    r"\bsecurity (audit|review|analysis)\b",
    r"\bproduction.?(ready|deploy)\b",
    r"\bcritical\b",
    r"\bfrontier\b",
]

TIER2_SIGNALS = [
    r"\bcode\b",
    r"\bfunction\b",
    r"\bclass\b",
    r"\bimplement\b",
    r"\brefactor\b",
    r"\bdebug\b",
    r"\bmulti.?file\b",
    r"\bapi\b",
    r"\bscript\b",
    r"\btest(s|ing)?\b",
    r"\bmodule\b",
    r"\bcomplex\b",
    r"\balgorithm\b",
    r"\bparse\b",
    r"\bgenerat(e|ion)\b",
    r"\bsql\b",
    r"\bquery\b",
]

TIER1_SIGNALS = [
    r"\bsimple\b",
    r"\blookup\b",
    r"\bformat\b",
    r"\bsummar(y|ize)\b",
    r"\bwhat is\b",
    r"\blist\b",
    r"\bexplain\b",
    r"\bquick\b",
    r"\bcheck\b",
    r"\btranslat(e|ion)\b",
    r"\bconvert\b",
    r"\bshort\b",
]


def score_task(task: str) -> tuple[int, str]:
    """Score task and return (tier, reasoning)."""
    task_lower = task.lower()

    t3 = sum(1 for p in TIER3_SIGNALS if re.search(p, task_lower))
    t2 = sum(1 for p in TIER2_SIGNALS if re.search(p, task_lower))
    t1 = sum(1 for p in TIER1_SIGNALS if re.search(p, task_lower))

    reasons = []
    if t3 > 0:
        reasons.append(f"{t3} frontier signal(s)")
    if t2 > 0:
        reasons.append(f"{t2} code/complexity signal(s)")
    if t1 > 0:
        reasons.append(f"{t1} simple signal(s)")

    # Decision logic
    if t3 >= 1:
        tier = 3
        reasons.append("→ frontier quality needed")
    elif t2 >= 2 or (t2 >= 1 and t1 == 0):
        tier = 2
        reasons.append("→ code/complexity task")
    elif t1 > 0 and t2 == 0:
        tier = 1
        reasons.append("→ simple task")
    else:
        tier = 1
        reasons.append("→ no strong signals, defaulting to fast")

    return tier, "; ".join(reasons) if reasons else "no signals detected"


def apply_flags(tier: int, args) -> tuple[int, str]:
    """Apply CLI flag overrides and env override."""
    override = os.environ.get("ROUTING_POLICY_OVERRIDE", "").strip()
    if override:
        try:
            t = int(override)
            if t in TIERS:
                return t, f"ROUTING_POLICY_OVERRIDE={t}"
        except ValueError:
            pass

    if getattr(args, "frontier", False):
        return 3, "--frontier flag"
    if getattr(args, "complex", False):
        return max(tier, 2), "--complex flag"
    if getattr(args, "code", False):
        return max(tier, 2), "--code flag"

    return tier, ""


def cmd_route(args):
    task = args.task
    tier, reasoning = score_task(task)
    tier, flag_reason = apply_flags(tier, args)
    if flag_reason:
        reasoning = f"{flag_reason} (base: {reasoning})"

    model = TIERS[tier]
    print(f"Task:      {task}")
    print(f"Model:     {model['name']}")
    print(f"Tier:      {model['label']}")
    print(f"Cost:      {model['cost']}  |  Memory: {model['memory']}")
    print(f"Reason:    {reasoning}")


def cmd_benchmark(args):
    print("Routing Table")
    print("=" * 60)
    for t, info in TIERS.items():
        print(f"\n{info['label']}")
        print(f"  Model:   {info['name']}")
        print(f"  Cost:    {info['cost']}  |  Memory: {info['memory']}")
        print(f"  Use for: {info['description']}")

    print("\n" + "=" * 60)
    print("Sample routings:")
    samples = [
        ("what is the capital of France", {}),
        ("implement a binary search tree in Python", {"code": True}),
        ("design a distributed caching architecture", {}),
        ("format this JSON nicely", {}),
        ("refactor this multi-file authentication module", {"complex": True}),
        ("security audit of our API endpoints", {}),
    ]
    print()
    for task, flags in samples:

        class FakeArgs:
            pass

        fa = FakeArgs()
        fa.code = flags.get("code", False)
        fa.complex = flags.get("complex", False)
        fa.frontier = flags.get("frontier", False)

        tier, reasoning = score_task(task)
        tier, flag_reason = apply_flags(tier, fa)
        if flag_reason:
            reasoning = f"{flag_reason} ({reasoning})"
        print(f"  [{tier}] {task[:50]}")
        print(f"      → {TIERS[tier]['name']}  ({reasoning})")
        print()


def main():
    parser = argparse.ArgumentParser(description="Three-tier model routing policy engine")
    sub = parser.add_subparsers(dest="command")

    p_route = sub.add_parser("route", help="Route a task to the best model tier")
    p_route.add_argument("task", help="Task description")
    p_route.add_argument("--code", action="store_true", help="Force Tier 2+ (code specialist)")
    p_route.add_argument("--complex", action="store_true", help="Force Tier 2+ (complex reasoning)")
    p_route.add_argument("--frontier", action="store_true", help="Force Tier 3 (Claude API)")

    sub.add_parser("benchmark", help="Print routing table and sample routings")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"route": cmd_route, "benchmark": cmd_benchmark}[args.command](args)


if __name__ == "__main__":
    main()
