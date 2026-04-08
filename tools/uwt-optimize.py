#!/usr/bin/env python3
"""
uwt-optimize.py — Useful Work per Token: Analyzer & Optimizer

Reads Claude session logs, computes UWT across 5 dimensions,
identifies waste, and recommends optimizations.

UWT Dimensions:
  D1: Task Completion    — Did the objective get achieved?
  D2: Action Density     — Tool calls per 1000 output tokens
  D3: Info Efficiency    — Reads that led to actions vs dead reads
  D4: Verification Rate  — Changes verified (test/re-read after write)
  D5: Waste Ratio        — Non-filler, non-error proportion

Usage:
  python3 tools/uwt-optimize.py score              # Current UWT score
  python3 tools/uwt-optimize.py analyze             # Full analysis
  python3 tools/uwt-optimize.py session <id>        # Analyze one session
  python3 tools/uwt-optimize.py trend               # Score over time
  python3 tools/uwt-optimize.py waste               # Identify top waste patterns
  python3 tools/uwt-optimize.py model-mix           # Model allocation analysis
"""

import json
import glob
import os
import sys
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone

LOVE_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = Path.home() / ".claude" / "projects"
UWT_HISTORY = LOVE_DIR / "memory" / "uwt-history.jsonl"

# Filler patterns that waste tokens
FILLER_STARTERS = [
    "Let me", "I'll ", "I will ", "Now let me", "Let's ",
    "Now I'll", "Great", "Perfect", "I see", "Looking at",
    "Based on", "Alright", "Sure", "Certainly", "Of course",
    "I understand", "I notice", "It seems", "It appears",
    "OK,", "Okay,", "Good.", "Right.", "Now,",
]

# ── Session Parser ───────────────────────────────────────────────────

def parse_session(path):
    """Parse a Claude session JSONL into structured metrics."""
    s = {
        "file": os.path.basename(path),
        "mtime": os.path.getmtime(path),
        "model": None,
        "input_tokens": 0, "output_tokens": 0,
        "turns": 0,
        "tools": Counter(),
        "reads": set(), "writes": set(), "bashes": 0,
        "errors": 0,
        "filler_lines": 0, "text_blocks": 0,
        "text_tokens_est": 0,
        "filler_tokens_est": 0,
        "read_then_wrote": 0,
        "tool_chain": [],  # ordered list of tool names for causal analysis
    }

    last_read_path = None

    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line.strip())
                if d.get("type") == "assistant":
                    msg = d.get("message", {})
                    usage = msg.get("usage", {})
                    s["input_tokens"] += usage.get("input_tokens", 0)
                    s["output_tokens"] += usage.get("output_tokens", 0)
                    m = msg.get("model")
                    if m and "<synthetic>" not in str(m):
                        s["model"] = m
                    s["turns"] += 1

                    for block in msg.get("content", []):
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "tool_use":
                            name = block.get("name", "")
                            s["tools"][name] += 1
                            s["tool_chain"].append(name)
                            inp = block.get("input", {})

                            if name == "Read":
                                fp = inp.get("file_path", "")
                                s["reads"].add(fp)
                                last_read_path = fp
                            elif name in ("Write", "Edit"):
                                fp = inp.get("file_path", inp.get("path", ""))
                                s["writes"].add(fp)
                                if last_read_path:
                                    s["read_then_wrote"] += 1
                                last_read_path = None
                            elif name == "Bash":
                                s["bashes"] += 1
                                last_read_path = None
                            elif name == "Grep":
                                last_read_path = None  # grep is targeted, not wasteful
                            else:
                                last_read_path = None

                        elif btype == "text":
                            text = block.get("text", "").strip()
                            word_count = len(text.split())
                            tok_est = int(word_count * 1.3)
                            s["text_blocks"] += 1
                            s["text_tokens_est"] += tok_est

                            for starter in FILLER_STARTERS:
                                if text.startswith(starter) and word_count < 100:
                                    s["filler_lines"] += 1
                                    s["filler_tokens_est"] += tok_est
                                    break

                elif d.get("type") == "tool_result":
                    if d.get("is_error"):
                        s["errors"] += 1
            except:
                pass

    s["total_tokens"] = s["input_tokens"] + s["output_tokens"]
    return s


def compute_dimensions(s):
    """Compute UWT 5 dimensions for a session."""
    total_tools = sum(s["tools"].values())
    out = max(1, s["output_tokens"])
    total = max(1, s["total_tokens"])

    d1 = 1.0 if total_tools > 5 and s["errors"] <= 1 else (
        0.7 if total_tools > 3 else (0.5 if total_tools > 1 else 0.0))
    d2 = min(1.0, ((total_tools / out) * 1000) / 15)
    d3 = s["read_then_wrote"] / max(1, len(s["reads"])) if s["reads"] else 0
    d4 = min(1.0, s["bashes"] / max(1, len(s["writes"]))) if s["writes"] else 0
    d5 = 1.0 - (s["filler_tokens_est"] + s["errors"] * 200) / total

    weights = {"D1": 3.0, "D2": 2.0, "D3": 2.0, "D4": 1.5, "D5": 1.5}
    scores = {"D1": d1, "D2": d2, "D3": d3, "D4": d4, "D5": d5}
    composite = sum(scores[k] * weights[k] for k in weights) / sum(weights.values())

    return scores, composite


# ── Commands ─────────────────────────────────────────────────────────

def load_all_sessions():
    """Load and parse all real sessions."""
    paths = glob.glob(str(SESSIONS_DIR / "*" / "*.jsonl"))
    sessions = []
    for p in paths:
        s = parse_session(p)
        if s["total_tokens"] > 0 and s["model"]:
            sessions.append(s)
    return sorted(sessions, key=lambda x: x["mtime"], reverse=True)


def cmd_score():
    """Quick UWT score."""
    sessions = load_all_sessions()
    if not sessions:
        print("  No sessions found.")
        return

    # Aggregate
    agg = {
        "input_tokens": sum(s["input_tokens"] for s in sessions),
        "output_tokens": sum(s["output_tokens"] for s in sessions),
        "total_tokens": sum(s["total_tokens"] for s in sessions),
        "tools": Counter(),
        "reads": set(), "writes": set(), "bashes": 0,
        "errors": sum(s["errors"] for s in sessions),
        "filler_tokens_est": sum(s["filler_tokens_est"] for s in sessions),
        "read_then_wrote": sum(s["read_then_wrote"] for s in sessions),
    }
    for s in sessions:
        agg["tools"] += s["tools"]
        agg["reads"] |= s["reads"]
        agg["writes"] |= s["writes"]
        agg["bashes"] += s["bashes"]

    scores, composite = compute_dimensions(agg)

    bar = lambda v: "█" * int(v * 20) + "░" * (20 - int(v * 20))

    print(f"\n  UWT: {composite:.3f}  ({len(sessions)} sessions, {agg['total_tokens']:,} tokens)")
    print()
    labels = ["D1:Completion", "D2:Density", "D3:InfoEff", "D4:Verify", "D5:Waste"]
    for label, (dim, val) in zip(labels, scores.items()):
        print(f"  {label:<16} {bar(val)} {val:.3f}")
    print()


def cmd_waste():
    """Identify top waste patterns."""
    sessions = load_all_sessions()

    print(f"\n  WASTE ANALYSIS — {len(sessions)} sessions")
    print(f"  {'─'*50}")

    # 1. Dead reads (files read but never acted on)
    read_counts = Counter()
    acted_on = set()
    for s in sessions:
        for r in s["reads"]:
            read_counts[r] += 1
        acted_on |= s["writes"]

    dead_reads = [(f, c) for f, c in read_counts.most_common() if f not in acted_on]
    print(f"\n  Dead reads (read but never written):")
    for f, c in dead_reads[:10]:
        print(f"    {c:>3}× {f}")

    # 2. Filler by model
    model_filler = defaultdict(lambda: {"filler": 0, "sessions": 0, "tokens": 0})
    for s in sessions:
        m = s["model"]
        model_filler[m]["filler"] += s["filler_lines"]
        model_filler[m]["sessions"] += 1
        model_filler[m]["tokens"] += s["filler_tokens_est"]

    print(f"\n  Filler by model:")
    for m, d in sorted(model_filler.items(), key=lambda x: x[1]["tokens"], reverse=True):
        avg = d["filler"] / max(1, d["sessions"])
        print(f"    {m}: {d['filler']} filler blocks (~{d['tokens']:,} tokens), {avg:.1f}/session")

    # 3. Retry patterns (same tool called repeatedly)
    retries = 0
    for s in sessions:
        chain = s["tool_chain"]
        for i in range(1, len(chain)):
            if chain[i] == chain[i-1] and chain[i] in ("Bash", "Read"):
                retries += 1

    print(f"\n  Retry patterns: {retries} consecutive same-tool calls")

    # 4. Token allocation
    print(f"\n  Token allocation:")
    total = sum(s["total_tokens"] for s in sessions)
    text_tok = sum(s["text_tokens_est"] for s in sessions)
    filler_tok = sum(s["filler_tokens_est"] for s in sessions)
    action_tok = total - text_tok
    print(f"    Action (tools):  {action_tok:>10,}  ({action_tok/total*100:.0f}%)")
    print(f"    Text (useful):   {text_tok-filler_tok:>10,}  ({(text_tok-filler_tok)/total*100:.0f}%)")
    print(f"    Text (filler):   {filler_tok:>10,}  ({filler_tok/total*100:.1f}%)")
    print()


def cmd_model_mix():
    """Analyze model allocation efficiency."""
    sessions = load_all_sessions()

    print(f"\n  MODEL MIX ANALYSIS")
    print(f"  {'─'*50}")

    models = defaultdict(lambda: {
        "sessions": 0, "in": 0, "out": 0, "tools": 0,
        "filler": 0, "errors": 0, "writes": 0, "bashes": 0,
    })

    for s in sessions:
        m = s["model"]
        models[m]["sessions"] += 1
        models[m]["in"] += s["input_tokens"]
        models[m]["out"] += s["output_tokens"]
        models[m]["tools"] += sum(s["tools"].values())
        models[m]["filler"] += s["filler_lines"]
        models[m]["errors"] += s["errors"]
        models[m]["writes"] += len(s["writes"])
        models[m]["bashes"] += s["bashes"]

    # Cost estimates (per million tokens, approximate)
    COST_PER_M = {
        "claude-opus-4-6": 75.0,    # $15 in + $75 out (weighted to output)
        "claude-sonnet-4-6": 15.0,   # $3 in + $15 out
        "claude-haiku-4-5": 1.25,    # $0.25 in + $1.25 out
    }

    for m, d in sorted(models.items(), key=lambda x: x[1]["in"]+x[1]["out"], reverse=True):
        total = d["in"] + d["out"]
        density = (d["tools"] / max(1, d["out"])) * 1000
        cost_rate = COST_PER_M.get(m, 15.0)
        est_cost = (total / 1_000_000) * cost_rate

        # Efficiency: tool calls per dollar
        tools_per_dollar = d["tools"] / max(0.01, est_cost)

        print(f"\n  {m}")
        print(f"    Sessions:     {d['sessions']:>6}")
        print(f"    Tokens:       {total:>10,}  (in={d['in']:,} out={d['out']:,})")
        print(f"    Tools:        {d['tools']:>6}  ({density:.1f}/1k output)")
        print(f"    Writes:       {d['writes']:>6}  Bashes: {d['bashes']}")
        print(f"    Filler:       {d['filler']:>6}  ({d['filler']/max(1,d['sessions']):.1f}/session)")
        print(f"    Est. cost:    ${est_cost:>8.2f}  ({tools_per_dollar:.0f} tools/$)")

    print(f"\n  RECOMMENDATIONS:")
    print(f"  {'─'*50}")

    opus = models.get("claude-opus-4-6", {})
    sonnet = models.get("claude-sonnet-4-6", {})

    if opus.get("sessions", 0) > 0 and opus.get("filler", 0) / max(1, opus["sessions"]) > 2:
        print(f"  • Opus has {opus['filler']/opus['sessions']:.1f} filler/session — 4× more than Sonnet.")
        print(f"    → Reserve Opus for architecture/judgment, not execution.")

    if sonnet.get("sessions", 0) > 0:
        s_density = (sonnet["tools"] / max(1, sonnet["out"])) * 1000
        if s_density < 5:
            print(f"  • Sonnet action density {s_density:.1f}/k — below target 10/k.")
            print(f"    → Add 'Act directly. No narration.' to system prompt.")

    print(f"  • Add Haiku for: heartbeat monitoring, file checks, simple queries.")
    print(f"    Haiku is 12× cheaper than Sonnet, 60× cheaper than Opus.")
    print(f"  • Route by task complexity:")
    print(f"    Haiku:  read-only checks, status, monitoring")
    print(f"    Sonnet: building, editing, testing")
    print(f"    Opus:   architecture, judgment, complex reasoning")
    print()


# ── Main ─────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "score"

    if cmd == "score":
        cmd_score()
    elif cmd == "analyze":
        cmd_score()
        cmd_waste()
        cmd_model_mix()
    elif cmd == "waste":
        cmd_waste()
    elif cmd == "model-mix":
        cmd_model_mix()
    elif cmd in ("--help", "-h", "help"):
        print(__doc__)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
