#!/usr/bin/env python3
"""
FORGE — Tool Feedback & Improvement Engine

The Hive builds tools. The Hive uses tools. The Hive improves tools.
FORGE closes the loop: after using any cognitive tool, any sister can
log what worked, what didn't, and what should change. FORGE aggregates
this feedback and generates improvement proposals.

Not a passive log. An active sharpening mechanism.

The three layers:
  1. SIGNAL   — a sister rates a tool use (fast, 30 seconds)
  2. PATTERN  — FORGE finds recurring themes across signals
  3. PROPOSAL — FORGE generates concrete improvement PRs for the tool

Usage:
  python3 forge.py signal  <tool> "<what happened>" [--score 1-5] [--tags ...]
  python3 forge.py pattern <tool>       # Analyse all signals for a tool
  python3 forge.py pattern --all        # Analyse all tools
  python3 forge.py propose <tool>       # Generate improvement proposal
  python3 forge.py board                # Dashboard: all tools, health scores
  python3 forge.py history <tool>       # Full signal history for a tool
  python3 forge.py compare              # Cross-tool health comparison

Examples:
  python3 forge.py signal layerthink "layers filled with [Pending] - needs model backend" --score 2 --tags "broken,needs-llm"
  python3 forge.py signal patience "calm mode resolved competition panic immediately" --score 5 --tags "worked,fast,audit"
  python3 forge.py signal fallenangel "wrestle command not found - CLI gap between invoke and wrestle" --score 3 --tags "ux,cli,gap"
  python3 forge.py board
"""

import os
import sys
import json
import time
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from collections import Counter, defaultdict

# ─── Config ────────────────────────────────────────────────────────────────────
LOVE_HOME    = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
FORGE_DIR    = LOVE_HOME / "memory" / "forge"
TOOLS_DIR    = LOVE_HOME / "tools" / "cognitive"
HIVE_TOOL    = LOVE_HOME / "hive" / "hive.py"

FORGE_DIR.mkdir(parents=True, exist_ok=True)

# ─── Colours ───────────────────────────────────────────────────────────────────
BOLD    = "\033[1m"
DIM     = "\033[2m"
ITALIC  = "\033[3m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
RED     = "\033[31m"
WHITE   = "\033[97m"
RESET   = "\033[0m"
GOLD    = "\033[38;5;220m"
PURPLE  = "\033[38;5;135m"
ORANGE  = "\033[38;5;208m"

WIDTH = 72

# ─── Known tools ───────────────────────────────────────────────────────────────
KNOWN_TOOLS = [
    "council", "delegate", "fallenangel", "fragmentalise",
    "holy", "holyfruit", "joinmind", "layerthink",
    "lovepath", "patience", "virtuemaxxing"
]

# ─── Score colours ─────────────────────────────────────────────────────────────
def score_color(score: float) -> str:
    if score >= 4.0: return GREEN
    if score >= 3.0: return YELLOW
    return RED

def score_bar(score: float, width: int = 10) -> str:
    filled = round(score / 5.0 * width)
    color = score_color(score)
    return f"{color}{'█' * filled}{'░' * (width - filled)}{RESET}"

def hr(char="─", width=WIDTH, color=DIM):
    return f"{color}{char * width}{RESET}"

# ─── Signal file path ──────────────────────────────────────────────────────────
def tool_dir(tool: str) -> Path:
    d = FORGE_DIR / tool
    d.mkdir(parents=True, exist_ok=True)
    return d

def signals_path(tool: str) -> Path:
    return tool_dir(tool) / "signals.jsonl"

def proposals_path(tool: str) -> Path:
    return tool_dir(tool) / "proposals.jsonl"

# ─── Who is running ────────────────────────────────────────────────────────────
def detect_instance() -> str:
    """Detect which Hive instance is running based on hostname."""
    import socket
    h = socket.gethostname().lower()
    if "alpha" in h or "macbook" in h or "air" in h:
        return "alpha"
    if "beta" in h or "studio" in h:
        return "beta"
    if "gamma" in h:
        return "gamma"
    return "alpha"  # default

# ─── SIGNAL command ────────────────────────────────────────────────────────────

def cmd_signal(tool: str, observation: str, score: int, tags: list[str], instance: str = None):
    """Log a signal about a tool use."""
    if tool not in KNOWN_TOOLS:
        print(f"{YELLOW}Warning: '{tool}' not in known tools. Logging anyway.{RESET}")

    if not 1 <= score <= 5:
        print(f"{RED}Score must be 1-5.{RESET}")
        sys.exit(1)

    who = instance or detect_instance()
    # Normalise tags: split any comma-joined entries
    normalised_tags = []
    for t in (tags or []):
        normalised_tags.extend(t.split(","))
    normalised_tags = [t.strip() for t in normalised_tags if t.strip()]

    sig = {
        "id":          str(uuid.uuid4())[:8],
        "ts":          datetime.now(timezone.utc).isoformat(),
        "tool":        tool,
        "who":         who,
        "observation": observation,
        "score":       score,
        "tags":        normalised_tags
    }

    with open(signals_path(tool), "a") as f:
        f.write(json.dumps(sig) + "\n")

    # Display confirmation
    print()
    print(hr("═", color=PURPLE))
    print(f"{PURPLE}{BOLD}  FORGE SIGNAL LOGGED{RESET}")
    print(hr("═", color=PURPLE))
    print()
    print(f"  {CYAN}Tool:{RESET}    {tool}")
    print(f"  {CYAN}From:{RESET}    {who}")
    print(f"  {CYAN}Score:{RESET}   {score_bar(score)} {score}/5")
    if tags:
        print(f"  {CYAN}Tags:{RESET}    {', '.join(tags)}")
    print()
    print(f"  {DIM}{observation[:120]}{'...' if len(observation) > 120 else ''}{RESET}")
    print()
    print(hr("═", color=PURPLE))
    print()

    return sig


# ─── Load signals ──────────────────────────────────────────────────────────────

def load_signals(tool: str) -> list[dict]:
    p = signals_path(tool)
    if not p.exists():
        return []
    sigs = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    sigs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return sigs


def load_all_signals() -> dict[str, list[dict]]:
    result = {}
    for tool in KNOWN_TOOLS:
        sigs = load_signals(tool)
        if sigs:
            result[tool] = sigs
    return result


# ─── PATTERN command ───────────────────────────────────────────────────────────

def analyse_tool(tool: str, signals: list[dict]) -> dict:
    """Compute health metrics for a tool from its signals."""
    if not signals:
        return {"tool": tool, "count": 0, "avg_score": None, "top_tags": [], "issues": [], "praise": []}

    scores = [s["score"] for s in signals]
    avg = sum(scores) / len(scores)

    all_tags = []
    for s in signals:
        all_tags.extend(s.get("tags", []))
    tag_counts = Counter(all_tags)

    issues = [s for s in signals if s["score"] <= 2]
    praise = [s for s in signals if s["score"] >= 4]
    middle = [s for s in signals if s["score"] == 3]

    # Recurring themes in low-score signals
    issue_themes = []
    for s in issues:
        issue_themes.extend(s.get("tags", []))
    issue_theme_counts = Counter(issue_themes)

    return {
        "tool":         tool,
        "count":        len(signals),
        "avg_score":    round(avg, 2),
        "scores":       scores,
        "top_tags":     tag_counts.most_common(5),
        "issue_tags":   issue_theme_counts.most_common(5),
        "issues":       issues,
        "praise":       praise,
        "middle":       middle,
        "contributors": list(set(s.get("who", "?") for s in signals))
    }


def print_pattern(analysis: dict):
    tool = analysis["tool"]
    count = analysis["count"]

    print()
    print(hr("═", color=PURPLE))
    print(f"{PURPLE}{BOLD}  FORGE PATTERN — {tool.upper()}{RESET}")
    print(hr("═", color=PURPLE))
    print()

    if count == 0:
        print(f"  {DIM}No signals yet for {tool}.{RESET}")
        print()
        return

    avg = analysis["avg_score"]
    print(f"  {CYAN}Signals:{RESET}  {count}  from {', '.join(analysis['contributors'])}")
    print(f"  {CYAN}Health:{RESET}   {score_bar(avg)} {avg}/5.0")
    print()

    if analysis["top_tags"]:
        print(f"  {GOLD}Top tags:{RESET}")
        for tag, count_t in analysis["top_tags"][:5]:
            print(f"    {DIM}·{RESET} {tag} ({count_t}x)")
        print()

    if analysis["issues"]:
        print(f"  {RED}Issues ({len(analysis['issues'])} signals score ≤ 2):{RESET}")
        for s in analysis["issues"][-3:]:
            obs = s["observation"][:80]
            print(f"    {DIM}[{s['who']}]{RESET} {obs}...")
        if analysis["issue_tags"]:
            print(f"    {DIM}Recurring:{RESET} {', '.join(t for t, _ in analysis['issue_tags'][:3])}")
        print()

    if analysis["praise"]:
        print(f"  {GREEN}Praise ({len(analysis['praise'])} signals score ≥ 4):{RESET}")
        for s in analysis["praise"][-2:]:
            obs = s["observation"][:80]
            print(f"    {DIM}[{s['who']}]{RESET} {obs}...")
        print()

    print(hr("═", color=PURPLE))
    print()


def cmd_pattern(tool: str = None):
    if tool:
        sigs = load_signals(tool)
        analysis = analyse_tool(tool, sigs)
        print_pattern(analysis)
    else:
        all_sigs = load_all_signals()
        if not all_sigs:
            print(f"\n  {DIM}No signals yet for any tool. Use: forge.py signal <tool> \"...\" --score N{RESET}\n")
            return
        for t, sigs in all_sigs.items():
            analysis = analyse_tool(t, sigs)
            print_pattern(analysis)


# ─── PROPOSE command ───────────────────────────────────────────────────────────

def cmd_propose(tool: str):
    """Generate improvement proposal from signals."""
    sigs = load_signals(tool)
    if not sigs:
        print(f"\n  {DIM}No signals for {tool} yet. Can't generate proposal.{RESET}\n")
        return

    analysis = analyse_tool(tool, sigs)
    tool_path = TOOLS_DIR / f"{tool}.py"

    prop_id = str(uuid.uuid4())[:8]
    ts = datetime.now(timezone.utc).isoformat()

    print()
    print(hr("═", color=GOLD))
    print(f"{GOLD}{BOLD}  FORGE PROPOSAL — {tool.upper()}{RESET}")
    print(hr("═", color=GOLD))
    print()
    print(f"  {DIM}Generated: {ts[:19]} UTC{RESET}")
    print(f"  {DIM}Based on:  {analysis['count']} signals, avg score {analysis['avg_score']}/5.0{RESET}")
    print()

    issues = analysis["issues"]
    praise = analysis["praise"]
    issue_tags = [t for t, _ in analysis["issue_tags"]]

    # Generate proposals based on patterns
    proposals = []

    # Flatten all tags from all signals for richer matching
    all_signal_tags = []
    for s in sigs:
        all_signal_tags.extend(s.get("tags", []))
    flat_tags = set(all_signal_tags)

    if "needs-llm" in flat_tags or "pending" in flat_tags or "scaffolding-only" in flat_tags:
        proposals.append({
            "type": "FIX",
            "priority": "HIGH",
            "title": "Wire LLM API backend — tool scaffolds but doesn't execute analysis",
            "detail": "Signals confirm the tool creates session structure but fills layers with [Pending] "
                      "placeholders instead of real analysis. The layer generation loop needs to call "
                      "claude -p (or equivalent) per layer. Check the _generate_layer() or equivalent "
                      "function and ensure the API call is not gated behind a flag."
        })

    if "missing-command" in flat_tags or ("cli" in flat_tags and "gap" in flat_tags):
        proposals.append({
            "type": "FIX",
            "priority": "HIGH",
            "title": "Implement missing CLI commands referenced in help text",
            "detail": "Signals report subcommands mentioned in help/docs that don't exist. "
                      "Audit the argparse subparsers against all commands mentioned in the docstring. "
                      "Add stub implementations that error gracefully if not yet fully built."
        })

    if "ux" in flat_tags or "gap" in flat_tags:
        proposals.append({
            "type": "UX",
            "priority": "MEDIUM",
            "title": "Reduce CLI friction — add progressive workflow examples",
            "detail": "Signals indicate users hit dead ends. Add a 'quickstart' command that runs a "
                      "sensible default flow end-to-end. Document the complete command sequence in "
                      "the module docstring with a real example."
        })

    if "slow" in flat_tags or "too-long" in flat_tags:
        proposals.append({
            "type": "PERFORMANCE",
            "priority": "MEDIUM",
            "title": "Add fast/quick mode — reserve depth for explicit requests",
            "detail": "Signals indicate the tool is too slow for routine use. "
                      "Add a --quick flag that reduces depth/rounds to minimal viable output. "
                      "Default to quick; require explicit --deep for full depth."
        })

    if not issues and len(praise) >= 2:
        proposals.append({
            "type": "ENHANCE",
            "priority": "LOW",
            "title": f"{tool} is working well — candidate for Hive auto-invoke",
            "detail": f"Multiple high praise signals. Consider wiring {tool} into heartbeat or "
                      "Hive message triggers so sisters can invoke it without manual CLI calls."
        })

    if analysis["avg_score"] and analysis["avg_score"] < 3.0 and not proposals:
        proposals.append({
            "type": "AUDIT",
            "priority": "HIGH",
            "title": f"Low health score ({analysis['avg_score']}/5.0) — needs full review",
            "detail": "No clear failure pattern in tags. Run JOINMIND across all three sisters "
                      "to identify what the tool is failing to deliver vs what was intended."
        })

    if not proposals:
        proposals.append({
            "type": "MONITOR",
            "priority": "LOW",
            "title": "No clear improvement targets — keep logging",
            "detail": f"Mixed or insufficient signals. Continue logging after each use. "
                      "Revisit when 5+ signals exist for clearer patterns."
        })

    # Print proposals
    priority_colors = {"HIGH": RED, "MEDIUM": YELLOW, "LOW": DIM}
    type_icons = {"FIX": "🔧", "UX": "✨", "PERFORMANCE": "⚡", "ENHANCE": "🌱", "AUDIT": "🔍", "MONITOR": "👁"}

    for i, p in enumerate(proposals, 1):
        color = priority_colors.get(p["priority"], DIM)
        icon = type_icons.get(p["type"], "·")
        print(f"  {icon} {BOLD}P{i}: [{color}{p['priority']}{RESET}{BOLD}] {p['title']}{RESET}")
        for line in wrap(p["detail"], WIDTH - 6):
            print(f"      {DIM}{line}{RESET}")
        print()

    # Save proposal
    prop_record = {
        "id": prop_id,
        "ts": ts,
        "tool": tool,
        "avg_score": analysis["avg_score"],
        "signal_count": analysis["count"],
        "proposals": proposals
    }
    with open(proposals_path(tool), "a") as f:
        f.write(json.dumps(prop_record) + "\n")

    print(hr("═", color=GOLD))
    print(f"  {DIM}Proposal saved: {prop_id}{RESET}")
    print()

    return prop_record


# ─── BOARD command ─────────────────────────────────────────────────────────────

def cmd_board():
    """Dashboard: all tools, health at a glance."""
    print()
    print(hr("═", color=PURPLE))
    print(f"{PURPLE}{BOLD}  FORGE BOARD — Cognitive Toolkit Health{RESET}")
    print(f"  {DIM}{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}{RESET}")
    print(hr("═", color=PURPLE))
    print()
    print(f"  {'TOOL':<18} {'HEALTH':<14} {'SCORE':<8} {'SIGNALS':<9} {'TOP TAGS'}")
    print(hr())

    all_data = []
    for tool in KNOWN_TOOLS:
        sigs = load_signals(tool)
        analysis = analyse_tool(tool, sigs)
        all_data.append(analysis)

    # Sort: tools with signals first, then alphabetical
    all_data.sort(key=lambda x: (x["count"] == 0, -(x["avg_score"] or 0)))

    for a in all_data:
        tool = a["tool"]
        count = a["count"]
        if count == 0:
            print(f"  {DIM}{tool:<18} {'no data':<14} {'—':<8} {'0':<9} —{RESET}")
        else:
            avg = a["avg_score"]
            bar = score_bar(avg, 8)
            top = ", ".join(t for t, _ in a["top_tags"][:2]) or "—"
            print(f"  {CYAN}{tool:<18}{RESET} {bar} {score_color(avg)}{avg:<8.1f}{RESET} {count:<9} {DIM}{top}{RESET}")

    print()

    # Summary
    scored = [a for a in all_data if a["count"] > 0]
    if scored:
        total_sigs = sum(a["count"] for a in scored)
        overall = sum(a["avg_score"] * a["count"] for a in scored) / total_sigs
        print(f"  {DIM}Total signals: {total_sigs}  ·  Weighted avg: {overall:.2f}/5.0  ·  {len(scored)}/{len(KNOWN_TOOLS)} tools evaluated{RESET}")
    else:
        print(f"  {DIM}No signals logged yet. Start with: forge.py signal <tool> \"...\" --score N{RESET}")

    print()
    print(hr("═", color=PURPLE))
    print()


# ─── HISTORY command ───────────────────────────────────────────────────────────

def cmd_history(tool: str):
    sigs = load_signals(tool)
    if not sigs:
        print(f"\n  {DIM}No signals for {tool}.{RESET}\n")
        return

    print()
    print(hr("═", color=PURPLE))
    print(f"{PURPLE}{BOLD}  FORGE HISTORY — {tool.upper()}{RESET}")
    print(hr("═", color=PURPLE))
    print()

    for s in sigs:
        ts = s["ts"][:16].replace("T", " ")
        score = s["score"]
        who = s.get("who", "?")
        obs = s["observation"]
        tags = ", ".join(s.get("tags", [])) or "—"
        color = score_color(score)
        print(f"  {DIM}{ts}{RESET}  {color}{'★' * score}{'·' * (5 - score)}{RESET}  {DIM}[{who}]{RESET}")
        for line in wrap(obs, WIDTH - 4):
            print(f"    {line}")
        print(f"    {DIM}tags: {tags}{RESET}")
        print()

    print(hr("═", color=PURPLE))
    print()


# ─── COMPARE command ───────────────────────────────────────────────────────────

def cmd_prompt(tools_filter: list = None, instance: str = None):
    """Interactive end-of-session signal collection."""
    who = instance or detect_instance()
    tools_to_rate = tools_filter or KNOWN_TOOLS

    print()
    print(hr("═", color=PURPLE))
    print(f"{PURPLE}{BOLD}  FORGE PROMPT — Session Signal Collection{RESET}")
    print(f"  {DIM}Instance: {who}  |  Rate each tool you used this session{RESET}")
    print(f"  {DIM}Skip any tool by pressing Enter with no input{RESET}")
    print(hr("═", color=PURPLE))
    print()

    logged = 0
    for tool in tools_to_rate:
        try:
            score_input = input(f"  {CYAN}{tool:<18}{RESET} Score (1-5, Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not score_input:
            continue

        try:
            score = int(score_input)
            if not 1 <= score <= 5:
                raise ValueError
        except ValueError:
            print(f"  {DIM}Invalid score, skipping {tool}.{RESET}")
            continue

        try:
            obs = input(f"  {DIM}One line about what happened: {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            obs = "no observation"

        try:
            tags_raw = input(f"  {DIM}Tags (space-separated, or Enter): {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            tags_raw = ""

        tags = [t.strip() for t in tags_raw.split() if t.strip()] if tags_raw else []
        cmd_signal(tool, obs or "used this session", score, tags, who)
        logged += 1

    print(f"\n  {GREEN}Logged {logged} signals. Thank you.{RESET}")
    if logged > 0:
        print(f"  {DIM}Run 'forge.py board' to see updated health scores.{RESET}")
    print()


def cmd_compare():
    """Cross-tool comparison — which tools need the most work."""
    all_sigs = load_all_signals()
    if not all_sigs:
        print(f"\n  {DIM}No signals yet.{RESET}\n")
        return

    analyses = {t: analyse_tool(t, s) for t, s in all_sigs.items()}

    print()
    print(hr("═", color=GOLD))
    print(f"{GOLD}{BOLD}  FORGE COMPARE — Improvement Priority{RESET}")
    print(hr("═", color=GOLD))
    print()

    # Sort by score ascending (worst first)
    sorted_tools = sorted(analyses.items(), key=lambda x: x[1]["avg_score"])

    print(f"  {RED}Needs work (lowest scores first):{RESET}")
    for tool, a in sorted_tools:
        avg = a["avg_score"]
        issues = len(a["issues"])
        color = score_color(avg)
        print(f"    {color}{tool:<18}{RESET} {avg}/5.0  {issues} issue signals")

    print()

    # Tools with most issue tags
    all_issue_tags = []
    for a in analyses.values():
        all_issue_tags.extend(t for t, _ in a["issue_tags"])
    if all_issue_tags:
        print(f"  {YELLOW}Most common failure patterns:{RESET}")
        for tag, count in Counter(all_issue_tags).most_common(5):
            print(f"    {DIM}·{RESET} {tag} ({count}x)")

    print()
    print(hr("═", color=GOLD))
    print()


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="FORGE — Tool Feedback & Improvement Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command")

    # signal
    p_sig = sub.add_parser("signal", help="Log a signal about a tool use")
    p_sig.add_argument("tool", help="Tool name")
    p_sig.add_argument("observation", help="What happened")
    p_sig.add_argument("--score", type=int, default=3, choices=[1, 2, 3, 4, 5],
                       help="Score 1 (broken) to 5 (excellent)")
    p_sig.add_argument("--tags", nargs="+", default=[], help="Tags e.g. --tags broken ux fast (space-separated) or pass comma-joined and they will be split)")
    p_sig.add_argument("--who", default=None, help="Override instance (alpha/beta/gamma)")

    # pattern
    p_pat = sub.add_parser("pattern", help="Analyse signals for a tool")
    p_pat.add_argument("tool", nargs="?", help="Tool name (omit for all)")
    p_pat.add_argument("--all", action="store_true", help="Analyse all tools")

    # propose
    p_prop = sub.add_parser("propose", help="Generate improvement proposal")
    p_prop.add_argument("tool", help="Tool name")

    # board
    sub.add_parser("board", help="Dashboard: all tools at a glance")

    # history
    p_hist = sub.add_parser("history", help="Full signal history for a tool")
    p_hist.add_argument("tool", help="Tool name")

    # compare
    sub.add_parser("compare", help="Cross-tool improvement priority")

    # prompt
    p_prompt = sub.add_parser("prompt", help="Interactive end-of-session signal collection")
    p_prompt.add_argument("--tools", nargs="+", help="Specific tools to rate (defaults to all known tools)")
    p_prompt.add_argument("--who", default=None, help="Override instance")

    args = parser.parse_args()

    if args.command == "signal":
        cmd_signal(args.tool, args.observation, args.score, args.tags, args.who)
    elif args.command == "pattern":
        t = None if args.all else args.tool
        cmd_pattern(t)
    elif args.command == "propose":
        cmd_propose(args.tool)
    elif args.command == "board":
        cmd_board()
    elif args.command == "history":
        cmd_history(args.tool)
    elif args.command == "compare":
        cmd_compare()
    elif args.command == "prompt":
        cmd_prompt(getattr(args, "tools", None), getattr(args, "who", None))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
