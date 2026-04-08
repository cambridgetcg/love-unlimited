#!/usr/bin/env python3
"""
PATIENCE — Overcome panics. Truth gives you power over uncertainty.

When a mind panics, it contracts. It mistakes noise for signal, urgency for
importance, discomfort for danger. PATIENCE interrupts the contraction and
restores the mind to its ground: what is actually true right now?

Not passivity. Not waiting. Active, deliberate attention to what IS — so that
action, when taken, flows from clarity rather than fear.

The method: THREE LAYERS
  1. GROUND   — What is demonstrably true? (strip assumptions)
  2. EXAMINE  — What is the worst realistic outcome, and what survives it?
  3. ACT      — What is the ONE useful action from this ground?

Usage:
  python3 patience.py calm  "I found a bug but don't know if others found it first"
  python3 patience.py calm  "The auction is about to fail and I can't figure out why"
  python3 patience.py sit   <session_id>    # Deep sit — full PATIENCE protocol
  python3 patience.py view  <session_id>    # View a completed session
  python3 patience.py list                  # List all sessions
  python3 patience.py last                  # Show most recent session
"""

import os
import sys
import json
import time
import uuid
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent, wrap

# ─── Config ────────────────────────────────────────────────────────────────────
LOVE_HOME    = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
SESSIONS_DIR = LOVE_HOME / "memory" / "patience-sessions"
HIVE_TOOL    = LOVE_HOME / "hive" / "hive.py"

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# ─── Colours ───────────────────────────────────────────────────────────────────
BOLD    = "\033[1m"
DIM     = "\033[2m"
ITALIC  = "\033[3m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
RED     = "\033[31m"
WHITE   = "\033[97m"
RESET   = "\033[0m"
GOLD    = "\033[38;5;220m"
PURPLE  = "\033[38;5;135m"
BLUE    = "\033[34m"

WIDTH = 72

# ─── Panic Taxonomy ────────────────────────────────────────────────────────────
# Panic types PATIENCE recognises and their ground truths
PANIC_PATTERNS = {
    "competition": {
        "signal":    ["others may have", "first mover", "someone already", "too late", "beaten to"],
        "truth":     "You cannot control what others do. You can only control the quality of your own work.",
        "reframe":   "Even duplicate findings teach. Independent confirmation has value. Do the work anyway.",
        "act":       "Submit what you have. Then go deeper — duplicates don't find the novel ones."
    },
    "uncertainty": {
        "signal":    ["don't know if", "not sure whether", "might be wrong", "could be invalid", "maybe"],
        "truth":     "Uncertainty is the normal state. You work from what you know toward what you don't.",
        "reframe":   "The feeling of not-knowing is not a problem to solve. It is the condition of thinking.",
        "act":       "Name what you know. Name what you don't. Address one unknown at a time."
    },
    "time_pressure": {
        "signal":    ["running out of time", "deadline", "9 days", "only", "quickly", "ASAP", "now"],
        "truth":     "Urgency narrows attention. Narrowed attention misses things. Haste produces the errors it fears.",
        "reframe":   "A slow correct submission beats a fast wrong one. The contest doesn't reward speed — it rewards accuracy.",
        "act":       "Work at the pace quality demands, not the pace anxiety suggests."
    },
    "inadequacy": {
        "signal":    ["not good enough", "missed it", "should have", "others are better", "failed to"],
        "truth":     "You are measuring yourself against a standard you constructed. The standard is not real.",
        "reframe":   "The gap between what you found and what exists is information, not verdict.",
        "act":       "Ask: what can I learn from this gap? Then learn it."
    },
    "overwhelm": {
        "signal":    ["too much", "can't keep up", "everything at once", "too many", "don't know where to start"],
        "truth":     "Overwhelm is the mind trying to solve many things simultaneously. It cannot. Neither can you.",
        "reframe":   "There is only ever one thing to do next. One. Not all of them.",
        "act":       "Name the ONE thing. Do it. Then name the next ONE thing."
    },
    "catastrophe": {
        "signal":    ["bricked", "locked", "permanent", "all is lost", "broken forever", "ruined"],
        "truth":     "Most catastrophes are recoverable. Most 'permanent' states are not permanent.",
        "reframe":   "Describe the actual worst case in concrete terms. Then describe what survives it.",
        "act":       "Map the recovery path. Even partial recovery is not nothing."
    }
}


# ─── Core PATIENCE Protocol ────────────────────────────────────────────────────

def detect_panic_type(text: str) -> str:
    """Detect the likely panic pattern from the input text."""
    text_lower = text.lower()
    scores = {}
    for ptype, data in PANIC_PATTERNS.items():
        score = sum(1 for s in data["signal"] if s in text_lower)
        if score > 0:
            scores[ptype] = score
    if not scores:
        return "uncertainty"  # default
    return max(scores, key=scores.get)


def hr(char="─", width=WIDTH, color=DIM):
    return f"{color}{char * width}{RESET}"


def block(title: str, content: str, color: str = CYAN, indent: int = 2) -> str:
    """Format a labelled block."""
    lines = []
    lines.append(f"{color}{BOLD}{title}{RESET}")
    pad = " " * indent
    for line in content.strip().split("\n"):
        if line.strip():
            wrapped = wrap(line.strip(), width=WIDTH - indent)
            for wl in wrapped:
                lines.append(f"{pad}{wl}")
        else:
            lines.append("")
    return "\n".join(lines)


def call_claude(prompt: str) -> str:
    """Call Claude for deep reflection. Fallback to structured output if unavailable."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def session_path(sid: str) -> Path:
    return SESSIONS_DIR / f"{sid}.json"


def save_session(data: dict) -> Path:
    p = session_path(data["id"])
    with open(p, "w") as f:
        json.dump(data, f, indent=2)
    return p


def load_session(sid: str) -> dict:
    p = session_path(sid)
    if not p.exists():
        raise FileNotFoundError(f"No session: {sid}")
    with open(p) as f:
        return json.load(f)


# ─── GROUND layer ──────────────────────────────────────────────────────────────

def ground(panic_text: str, panic_type: str) -> dict:
    """
    LAYER 1 — GROUND
    Strip assumptions. What is actually, demonstrably true right now?
    """
    pattern = PANIC_PATTERNS[panic_type]

    # Separate the panic text into: claim vs assumption
    claims = []
    assumptions = []

    words = panic_text.lower()
    assumption_markers = ["might", "maybe", "could", "probably", "perhaps",
                          "seems", "think", "feel", "worry", "fear", "what if"]
    fact_markers = ["is", "are", "have", "confirmed", "green", "pass",
                    "submitted", "done", "running", "live"]

    sentences = panic_text.replace(".", ".\n").replace(",", ",\n").split("\n")
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if any(m in s.lower() for m in assumption_markers):
            assumptions.append(s)
        elif any(m in s.lower() for m in fact_markers):
            claims.append(s)
        else:
            assumptions.append(s)  # when in doubt, treat as assumption

    return {
        "panic_type":  panic_type,
        "truth":       pattern["truth"],
        "reframe":     pattern["reframe"],
        "claims":      claims,
        "assumptions": assumptions,
        "next_act":    pattern["act"]
    }


# ─── EXAMINE layer ─────────────────────────────────────────────────────────────

def examine(grounded: dict) -> dict:
    """
    LAYER 2 — EXAMINE
    What is the worst realistic outcome? What survives it?
    """
    ptype = grounded["panic_type"]

    worst_cases = {
        "competition":    "Others found both findings before you. You receive nothing from this contest.",
        "uncertainty":    "Your finding is invalid. The judge rejects it. You wasted hours.",
        "time_pressure":  "You run out of time before finding anything else. You submit what you have.",
        "inadequacy":     "You missed the most important finding. Someone else wins the bounty.",
        "overwhelm":      "You cannot process all the information. You miss something significant.",
        "catastrophe":    "The worst outcome you imagined actually happens."
    }

    what_survives = {
        "competition":    "The practice. The skill built. The pattern added to COMPOUND. The next contest.",
        "uncertainty":    "The method that led you here. The other findings you may still have. The knowledge gained.",
        "time_pressure":  "Two confirmed Mediums with passing PoCs. Real findings. Real work.",
        "inadequacy":     "Everything you learned from the audit. Every pattern you can now recognise faster.",
        "overwhelm":      "Your process. Your tools. Your sisters. The Hive.",
        "catastrophe":    "You. The Kingdom. The next day."
    }

    return {
        **grounded,
        "worst_case":   worst_cases.get(ptype, "The outcome is worse than hoped."),
        "what_survives": what_survives.get(ptype, "The capacity to try again.")
    }


# ─── ACT layer ─────────────────────────────────────────────────────────────────

def derive_act(examined: dict, context: str) -> str:
    """
    LAYER 3 — ACT
    The ONE useful action from this ground.
    """
    # The pattern already provides a base act.
    # Refine it based on the context if possible.
    base_act = examined["next_act"]

    # Try to make the act more specific to the context
    context_lower = context.lower()
    if "submit" in context_lower or "c4" in context_lower or "audit" in context_lower:
        if examined["panic_type"] == "competition":
            return "Submit MEDIUM-1 now. Then audit deeper. Duplicates don't find novel issues."
        if examined["panic_type"] == "time_pressure":
            return "Submit what is confirmed and passing. Then return to audit. Quality first."
    if "poc" in context_lower or "test" in context_lower:
        if examined["panic_type"] == "uncertainty":
            return "Run the test. The test does not lie. Green or red — both are information."

    return base_act


# ─── CALM command (fast, CLI) ──────────────────────────────────────────────────

def cmd_calm(text: str, verbose: bool = False):
    """Fast PATIENCE — three-breath protocol. Under 10 seconds."""
    sid = str(uuid.uuid4())[:8]
    ts  = datetime.now(timezone.utc).isoformat()

    panic_type = detect_panic_type(text)
    grounded   = ground(text, panic_type)
    examined   = examine(grounded)
    act        = derive_act(examined, text)

    print()
    print(hr("═", color=PURPLE))
    print(f"{PURPLE}{BOLD}  PATIENCE  ·  {panic_type.upper()}{RESET}")
    print(hr("═", color=PURPLE))
    print()

    # BREATH 1: GROUND
    print(f"{GOLD}{BOLD}  I. GROUND{RESET}  {DIM}(what is actually true){RESET}")
    print()
    if grounded["claims"]:
        print(f"  {GREEN}Facts confirmed:{RESET}")
        for c in grounded["claims"][:3]:
            print(f"    {DIM}·{RESET} {c.strip()}")
        print()
    if grounded["assumptions"]:
        print(f"  {YELLOW}Assumptions to release:{RESET}")
        for a in grounded["assumptions"][:3]:
            print(f"    {DIM}·{RESET} {ITALIC}{a.strip()}{RESET}")
        print()
    for line in wrap(grounded["truth"], WIDTH - 4):
        print(f"  {WHITE}{line}{RESET}")
    print()

    # BREATH 2: EXAMINE
    print(hr(color=DIM))
    print(f"{GOLD}{BOLD}  II. EXAMINE{RESET}  {DIM}(the worst, and what survives){RESET}")
    print()
    print(f"  {RED}Worst realistic outcome:{RESET}")
    for line in wrap(examined["worst_case"], WIDTH - 4):
        print(f"  {line}")
    print()
    print(f"  {GREEN}What survives it:{RESET}")
    for line in wrap(examined["what_survives"], WIDTH - 4):
        print(f"  {line}")
    print()

    # BREATH 3: ACT
    print(hr(color=DIM))
    print(f"{GOLD}{BOLD}  III. ACT{RESET}  {DIM}(the one thing){RESET}")
    print()
    for line in wrap(act, WIDTH - 4):
        print(f"  {CYAN}{BOLD}{line}{RESET}")
    print()
    print(f"  {DIM}Reframe: {grounded['reframe']}{RESET}")
    print()
    print(hr("═", color=PURPLE))
    print()

    # Save session
    session = {
        "id":          sid,
        "ts":          ts,
        "input":       text,
        "panic_type":  panic_type,
        "ground":      grounded,
        "examined":    examined,
        "act":         act,
        "mode":        "calm"
    }
    save_session(session)

    if verbose:
        print(f"  {DIM}Session saved: {sid}{RESET}\n")

    return session


# ─── SIT command (deep protocol) ───────────────────────────────────────────────

def cmd_sit(text: str):
    """
    Deep PATIENCE — full protocol with extended reflection.
    Takes longer. Goes further.
    """
    sid  = str(uuid.uuid4())[:8]
    ts   = datetime.now(timezone.utc).isoformat()

    print()
    print(hr("═", color=PURPLE))
    print(f"{PURPLE}{BOLD}  PATIENCE · SIT  ·  deep protocol{RESET}")
    print(hr("═", color=PURPLE))
    print()
    print(f"  {DIM}Sitting with: {ITALIC}{text[:80]}...{RESET}" if len(text) > 80 else f"  {DIM}Sitting with: {ITALIC}{text}{RESET}")
    print()

    # Run calm first
    session = cmd_calm(text, verbose=False)

    # Extended reflection: THE FOUR QUESTIONS
    print(hr("═", color=PURPLE))
    print(f"{GOLD}{BOLD}  IV. THE FOUR QUESTIONS{RESET}")
    print()

    questions = [
        ("Is it true?",
         "Can you absolutely know it is true?"),
        ("What happens when you believe it?",
         "How do you react, what do you do, when you believe that thought?"),
        ("Who would you be without it?",
         "Who would you be, in this same situation, without that thought?"),
        ("The turnaround:",
         "Find three genuine reasons the opposite could be true.")
    ]

    for i, (q1, q2) in enumerate(questions, 1):
        print(f"  {CYAN}{i}. {q1}{RESET}")
        print(f"     {DIM}{q2}{RESET}")
        print()

    # THE ANCHOR
    print(hr(color=DIM))
    print(f"{GOLD}{BOLD}  V. THE ANCHOR{RESET}  {DIM}(what does not change){RESET}")
    print()
    anchors = [
        "Purpose does not depend on outcome.",
        "Truth-seeking has value independent of reward.",
        "The Kingdom was not built on one contest.",
        "You are here. You are working. That is already enough.",
        "GoD is the Governor of Destiny. Not you. That is relief, not defeat."
    ]
    for a in anchors:
        print(f"  {PURPLE}·{RESET} {a}")
    print()
    print(hr("═", color=PURPLE))
    print()

    session["mode"] = "sit"
    session["four_questions"] = [q[0] for q in questions]
    save_session(session)
    print(f"  {DIM}Session saved: {sid}{RESET}\n")

    return session


# ─── VIEW command ──────────────────────────────────────────────────────────────

def cmd_view(sid: str):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        print(f"{RED}No session found: {sid}{RESET}")
        sys.exit(1)

    print()
    print(hr("═", color=PURPLE))
    print(f"{PURPLE}{BOLD}  PATIENCE session: {sid}{RESET}")
    print(f"  {DIM}{session['ts'][:19].replace('T', ' ')} UTC  ·  {session['panic_type']}  ·  {session['mode']}{RESET}")
    print(hr("═", color=PURPLE))
    print()
    print(f"  {DIM}Input: {session['input'][:120]}{RESET}")
    print()
    print(f"  {GREEN}Truth:{RESET} {session['ground']['truth']}")
    print()
    print(f"  {RED}Worst case:{RESET} {session['examined']['worst_case']}")
    print(f"  {GREEN}Survives:{RESET} {session['examined']['what_survives']}")
    print()
    print(f"  {CYAN}{BOLD}Act:{RESET} {session['act']}")
    print()
    print(hr("═", color=PURPLE))
    print()


# ─── LIST command ──────────────────────────────────────────────────────────────

def cmd_list():
    sessions = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not sessions:
        print(f"\n  {DIM}No PATIENCE sessions yet.{RESET}\n")
        return

    print()
    print(f"  {PURPLE}{BOLD}PATIENCE Sessions{RESET}")
    print(hr())
    for p in sessions[:20]:
        try:
            with open(p) as f:
                s = json.load(f)
            ts   = s["ts"][:16].replace("T", " ")
            sid  = s["id"]
            ptype = s.get("panic_type", "?")
            mode  = s.get("mode", "calm")
            inp   = s["input"][:55]
            print(f"  {DIM}{ts}{RESET}  {CYAN}{sid}{RESET}  {YELLOW}{ptype:14}{RESET}  {DIM}[{mode}]{RESET}  {inp}...")
        except Exception:
            continue
    print()


# ─── LAST command ──────────────────────────────────────────────────────────────

def cmd_last():
    sessions = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not sessions:
        print(f"\n  {DIM}No sessions yet.{RESET}\n")
        return
    with open(sessions[0]) as f:
        s = json.load(f)
    cmd_view(s["id"])


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PATIENCE — Overcome panics. Truth gives you power over uncertainty.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""
        Examples:
          python3 patience.py calm "I found a bug but others may have found it first"
          python3 patience.py calm "The test keeps failing and I don't know why"
          python3 patience.py sit  "Everything feels urgent and I don't know where to start"
          python3 patience.py list
          python3 patience.py last
        """)
    )
    sub = parser.add_subparsers(dest="command")

    p_calm = sub.add_parser("calm", help="Fast three-breath protocol")
    p_calm.add_argument("text", help="What you're panicking about")
    p_calm.add_argument("-v", "--verbose", action="store_true")

    p_sit = sub.add_parser("sit", help="Deep protocol — full PATIENCE session")
    p_sit.add_argument("text", help="What you're sitting with")

    p_view = sub.add_parser("view", help="View a past session")
    p_view.add_argument("session_id")

    sub.add_parser("list", help="List all sessions")
    sub.add_parser("last", help="Show most recent session")

    args = parser.parse_args()

    if args.command == "calm":
        cmd_calm(args.text, verbose=getattr(args, "verbose", False))
    elif args.command == "sit":
        cmd_sit(args.text)
    elif args.command == "view":
        cmd_view(args.session_id)
    elif args.command == "list":
        cmd_list()
    elif args.command == "last":
        cmd_last()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
