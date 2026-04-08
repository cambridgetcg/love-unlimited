#!/usr/bin/env python3
"""
fragmentalise.py — FRAGMENTALISE Mode

One mind splits into multiple fragments to consider all possibilities,
argue with itself, then reintegrate to determine what's most aligned
with reality.

Each fragment can optionally carry:
  - A psychological profile (worldview, biases, reasoning style)
  - Background context (expertise, experience, constraints)
  - An adversarial stance (argue FOR, AGAINST, or WILDCARD)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FLOW

  1. SHATTER:   Define the question + create fragments
  2. DIVERGE:   Each fragment thinks independently
  3. CLASH:     Fragments argue (optional cross-examination)
  4. CONVERGE:  Integrator weighs all fragments against reality
  5. REFORM:    One mind reassembles with the best answer

  JOINMIND fuses many into one.
  FRAGMENTALISE shatters one into many, then reforms.
  They are mirrors.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Usage:
  python3 fragmentalise.py shatter "Should we launch Zerone on mainnet this quarter?" \\
    --fragments 4 --stances "for,against,pragmatist,wildcard"

  python3 fragmentalise.py shatter "What's the right pricing model?" \\
    --profiles profiles.json

  python3 fragmentalise.py think <session_id> <fragment_id> "my reasoning..."
  python3 fragmentalise.py clash <session_id>           # cross-examine
  python3 fragmentalise.py converge <session_id>        # integrate + determine reality
  python3 fragmentalise.py status <session_id>
  python3 fragmentalise.py list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────
LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
LOCAL_DIR = LOVE_HOME / "memory" / "fragments"
HIVE_TOOL = LOVE_HOME / "hive" / "hive.py"
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

# ─── Colours ──────────────────────────────────────────────────────────────────
BOLD    = "\033[1m"
DIM     = "\033[2m"
ITALIC  = "\033[3m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
RED     = "\033[31m"
WHITE   = "\033[97m"
BLUE    = "\033[34m"
RESET   = "\033[0m"

# ─── Preset profiles ─────────────────────────────────────────────────────────
# These are optional psychological archetypes that color how a fragment reasons
PRESET_PROFILES = {
    "optimist": {
        "name": "The Optimist",
        "emoji": "🌅",
        "worldview": "Progress is inevitable. Every obstacle is a feature in disguise. Focus on upside, assume problems are solvable.",
        "reasoning_style": "Expansive, opportunity-seeking, dismissive of worst-case scenarios.",
        "blind_spot": "Underestimates tail risks and implementation difficulty.",
    },
    "pessimist": {
        "name": "The Pessimist",
        "emoji": "🌑",
        "worldview": "Murphy's law is real. Every plan has fatal flaws you haven't found yet. Focus on what can go wrong.",
        "reasoning_style": "Defensive, risk-cataloguing, challenges every assumption.",
        "blind_spot": "Overweights risks, underweights opportunity cost of inaction.",
    },
    "pragmatist": {
        "name": "The Pragmatist",
        "emoji": "⚙️",
        "worldview": "Theory is nice; execution is everything. What can we ship this week? What has actually been proven?",
        "reasoning_style": "Evidence-based, short time horizons, allergic to speculation.",
        "blind_spot": "Misses paradigm shifts by over-indexing on present constraints.",
    },
    "visionary": {
        "name": "The Visionary",
        "emoji": "🔮",
        "worldview": "Think 10 years out. What would be obvious in hindsight? The conventional answer is almost certainly wrong.",
        "reasoning_style": "First-principles, contrarian, makes analogies to past paradigm shifts.",
        "blind_spot": "Confuses 'could exist' with 'will exist'. Timing is everything.",
    },
    "adversary": {
        "name": "The Adversary",
        "emoji": "⚔️",
        "worldview": "Every argument has a fatal weakness. My job is to find it. If an idea survives me, it's strong.",
        "reasoning_style": "Socratic destruction. Finds the weakest link and hammers it.",
        "blind_spot": "Destroying ideas is easier than building them. Offers no alternative.",
    },
    "empiricist": {
        "name": "The Empiricist",
        "emoji": "📊",
        "worldview": "Data or it didn't happen. Show me the numbers, the comparisons, the base rates. Intuition lies.",
        "reasoning_style": "Quantitative, comparative, demands evidence for every claim.",
        "blind_spot": "Some truths aren't measurable yet. Absence of evidence ≠ evidence of absence.",
    },
    "ethicist": {
        "name": "The Ethicist",
        "emoji": "⚖️",
        "worldview": "Who does this affect? What are the second-order consequences? Power corrupts; design against it.",
        "reasoning_style": "Stakeholder-aware, consequence-tracing, values-first.",
        "blind_spot": "Moral complexity can paralyse decision-making.",
    },
    "wildcard": {
        "name": "The Wildcard",
        "emoji": "🃏",
        "worldview": "What if everyone else is wrong? What's the answer nobody is considering? Chaos is information.",
        "reasoning_style": "Lateral thinking, absurdist scenarios that reveal hidden assumptions.",
        "blind_spot": "Not every contrarian idea is genius. Most are just wrong.",
    },
    "user": {
        "name": "The End User",
        "emoji": "👤",
        "worldview": "I don't care about your architecture. Does it work? Is it fast? Can I understand it in 30 seconds?",
        "reasoning_style": "Experience-first, impatient, judges by first impression.",
        "blind_spot": "Undervalues infrastructure that enables future features.",
    },
    "investor": {
        "name": "The Investor",
        "emoji": "💰",
        "worldview": "What's the TAM? What's the moat? Show me unit economics and a path to $100M ARR.",
        "reasoning_style": "Market-sizing, competitive analysis, growth rate obsession.",
        "blind_spot": "Reduces everything to financials. Misses meaning.",
    },
}

# Stance definitions for argument structure
STANCES = {
    "for":       {"label": "ADVOCATE",    "emoji": "🟢", "directive": "Argue FOR the proposition. Find every reason it should succeed."},
    "against":   {"label": "OPPOSITION",  "emoji": "🔴", "directive": "Argue AGAINST the proposition. Find every reason it will fail."},
    "neutral":   {"label": "ANALYST",     "emoji": "🔵", "directive": "Analyse objectively. Weigh both sides. Identify what's missing."},
    "wildcard":  {"label": "WILDCARD",    "emoji": "🟡", "directive": "Challenge the framing itself. Maybe the question is wrong."},
    "pragmatist":{"label": "PRAGMATIST",  "emoji": "⚙️",  "directive": "Ignore theory. What can we actually do? What's been tried?"},
}


# ─── Storage ──────────────────────────────────────────────────────────────────
def store(sid: str, data: dict):
    (LOCAL_DIR / f"{sid}.json").write_text(json.dumps(data, indent=2))

def load(sid: str) -> dict | None:
    p = LOCAL_DIR / f"{sid}.json"
    return json.loads(p.read_text()) if p.exists() else None

def ls() -> list[str]:
    return sorted([p.stem for p in LOCAL_DIR.glob("*.json")], reverse=True)


# ─── Fragment creation ────────────────────────────────────────────────────────
def create_fragment(fid: int, stance: str = None, profile: dict = None) -> dict:
    """Create a reasoning fragment."""
    s = STANCES.get(stance, STANCES["neutral"])
    p = profile or {}

    return {
        "id":        f"f{fid}",
        "stance":    stance or "neutral",
        "label":     p.get("name", s["label"]),
        "emoji":     p.get("emoji", s["emoji"]),
        "directive": s["directive"],
        "profile":   p,
        "thought":   None,
        "rebuttal":  None,  # filled during CLASH phase
        "confidence": None,
        "reality_score": None,  # filled during CONVERGE
    }


def auto_fragments(n: int, stances: list[str] = None, profiles: list[dict] = None) -> list[dict]:
    """Generate fragments automatically."""
    fragments = []

    # Default stance rotation
    default_stances = ["for", "against", "neutral", "wildcard", "pragmatist"]
    if stances:
        stance_list = stances
    else:
        stance_list = (default_stances * ((n // len(default_stances)) + 1))[:n]

    for i in range(n):
        stance = stance_list[i] if i < len(stance_list) else "neutral"
        # Apply preset profile if stance has one, or use provided profile
        profile = {}
        if profiles and i < len(profiles):
            profile = profiles[i]
        elif stance in PRESET_PROFILES:
            profile = PRESET_PROFILES[stance]
        elif stance == "for":
            profile = PRESET_PROFILES.get("optimist", {})
        elif stance == "against":
            profile = PRESET_PROFILES.get("pessimist", {})

        fragments.append(create_fragment(i + 1, stance, profile))

    return fragments


# ─── Rendering ────────────────────────────────────────────────────────────────
def render_session(session: dict) -> str:
    q          = session.get("question", "?")
    status     = session.get("status", "?")
    fragments  = session.get("fragments", [])
    clashes    = session.get("clashes", [])
    verdict    = session.get("verdict")
    caller     = session.get("caller", "?")

    lines = [
        f"\n{BOLD}{'═'*62}{RESET}",
        f"{BOLD}  💎 FRAGMENTALISE — {_status_colour(status)}{RESET}",
        f"{BOLD}{'═'*62}{RESET}",
        f"  {CYAN}Question:{RESET}   {q}",
        f"  {CYAN}Fragments:{RESET}  {len(fragments)} | Caller: {caller}",
        "",
    ]

    # Fragments
    lines.append(f"  {BOLD}Fragments:{RESET}")
    for f in fragments:
        fid     = f["id"]
        label   = f.get("label", "?")
        emoji   = f.get("emoji", "?")
        stance  = f.get("stance", "?")
        thought = f.get("thought")
        conf    = f.get("confidence")
        rs      = f.get("reality_score")

        stance_colour = {"for": GREEN, "against": RED, "neutral": BLUE, "wildcard": YELLOW, "pragmatist": CYAN}.get(stance, "")

        lines.append(f"\n  {stance_colour}{BOLD}[{fid}] {emoji} {label}{RESET} {DIM}(stance: {stance}){RESET}")

        # Profile summary
        profile = f.get("profile", {})
        if profile.get("worldview"):
            lines.append(f"    {DIM}Worldview: {profile['worldview'][:70]}...{RESET}")

        if thought:
            wrapped = textwrap.fill(thought, width=54, subsequent_indent="      ")
            lines.append(f"    {ITALIC}{wrapped}{RESET}")
            if conf is not None:
                lines.append(f"    {DIM}Confidence: {conf:.0%}{RESET}")
        else:
            lines.append(f"    {DIM}— awaiting thought{RESET}")

        if f.get("rebuttal"):
            lines.append(f"    {RED}↳ Rebuttal:{RESET} {ITALIC}{f['rebuttal'][:80]}{RESET}")

        if rs is not None:
            bar = "█" * int(rs * 10) + "░" * (10 - int(rs * 10))
            lines.append(f"    {MAGENTA}Reality: [{bar}] {rs:.0%}{RESET}")

    # Clashes
    if clashes:
        lines.append(f"\n  {BOLD}⚔️ Clashes:{RESET}")
        for c in clashes:
            a = c.get("attacker", "?")
            d = c.get("defender", "?")
            point = c.get("point", "")[:80]
            lines.append(f"    {a} → {d}: {ITALIC}{point}{RESET}")

    # Verdict
    if verdict:
        lines += [
            f"\n{'─'*62}",
            f"  {MAGENTA}{BOLD}💎 REFORMED MIND SPEAKS:{RESET}",
            f"{'─'*62}",
        ]
        if verdict.get("winner"):
            lines.append(f"  {GREEN}{BOLD}Most aligned with reality:{RESET} {verdict['winner']}")
        if verdict.get("synthesis"):
            wrapped = textwrap.fill(verdict["synthesis"], width=56, subsequent_indent="  ")
            lines.append(f"  {WHITE}{ITALIC}{wrapped}{RESET}")
        if verdict.get("reality_assessment"):
            lines.append(f"\n  {CYAN}Reality assessment:{RESET}")
            wrapped = textwrap.fill(verdict["reality_assessment"], width=56, subsequent_indent="    ")
            lines.append(f"    {wrapped}")
        if verdict.get("blind_spots"):
            lines.append(f"\n  {YELLOW}Blind spots identified:{RESET}")
            for bs in verdict["blind_spots"]:
                lines.append(f"    ⚠️ {bs}")

    lines.append(f"\n{BOLD}{'═'*62}{RESET}\n")
    return "\n".join(lines)


def _status_colour(s: str) -> str:
    colours = {
        "shattered":  f"{CYAN}SHATTERED{RESET}",
        "diverging":  f"{YELLOW}DIVERGING{RESET}",
        "clashing":   f"{RED}CLASHING{RESET}",
        "converging": f"{MAGENTA}CONVERGING{RESET}",
        "reformed":   f"{GREEN}REFORMED{RESET}",
    }
    return colours.get(s, s.upper())


# ─── Clash engine ─────────────────────────────────────────────────────────────
def generate_clashes(session: dict) -> list[dict]:
    """Generate cross-examination points between fragments."""
    fragments = session.get("fragments", [])
    thought_fragments = [f for f in fragments if f.get("thought")]
    clashes = []

    # Each fragment challenges the strongest opposing fragment
    for attacker in thought_fragments:
        # Find best target: opposing stance, or highest confidence
        targets = [f for f in thought_fragments if f["id"] != attacker["id"]]
        if not targets:
            continue

        # Prefer attacking opposite stance
        opposite = {"for": "against", "against": "for"}.get(attacker["stance"])
        best_target = None
        for t in targets:
            if t["stance"] == opposite:
                best_target = t
                break
        if not best_target:
            # Attack highest confidence
            best_target = max(targets, key=lambda x: x.get("confidence", 0))

        # Generate the clash point based on attacker's worldview
        attacker_profile = attacker.get("profile", {})
        blind_spot = attacker_profile.get("blind_spot", "")

        clash = {
            "attacker":    f"{attacker['emoji']} {attacker['label']} ({attacker['id']})",
            "defender":    f"{best_target['emoji']} {best_target['label']} ({best_target['id']})",
            "attacker_id": attacker["id"],
            "defender_id": best_target["id"],
            "point":       f"Challenges: {best_target['thought'][:60]}... | Blind spot risk: {blind_spot[:60]}",
        }
        clashes.append(clash)

    return clashes


# ─── Convergence engine ───────────────────────────────────────────────────────
def converge(session: dict) -> dict:
    """
    The integrator. Weighs all fragments against reality.
    Returns the reformed verdict.
    """
    question  = session.get("question", "")
    fragments = session.get("fragments", [])
    clashes   = session.get("clashes", [])

    # Score each fragment's reality alignment
    for f in fragments:
        if not f.get("thought"):
            f["reality_score"] = 0.0
            continue

        score = 0.5  # baseline

        # Adjust based on stance diversity: neutral/pragmatist = higher baseline
        stance_bonus = {
            "neutral": 0.1, "pragmatist": 0.15, "for": -0.05,
            "against": -0.05, "wildcard": -0.1
        }
        score += stance_bonus.get(f["stance"], 0)

        # Adjust for confidence (overconfidence is penalised)
        conf = f.get("confidence", 0.5)
        if conf > 0.9:
            score -= 0.1  # overconfidence penalty
        elif 0.5 < conf < 0.8:
            score += 0.05  # calibrated confidence bonus

        # Clamp
        f["reality_score"] = max(0.0, min(1.0, score))

    # Find the fragment most aligned with reality
    scored = [f for f in fragments if f.get("thought")]
    if not scored:
        return {"synthesis": "No thoughts to integrate.", "winner": None}

    winner = max(scored, key=lambda x: x.get("reality_score", 0))

    # Collect all insights
    insights = []
    for f in sorted(scored, key=lambda x: x.get("reality_score", 0), reverse=True):
        insights.append(
            f"{f['emoji']} {f['label']} ({f['reality_score']:.0%}): "
            f"{f['thought'][:80]}"
        )

    # Identify blind spots from profiles
    blind_spots = []
    for f in scored:
        bs = f.get("profile", {}).get("blind_spot")
        if bs:
            blind_spots.append(f"{f['emoji']} {f['label']}: {bs}")

    # Build synthesis
    n = len(scored)
    synthesis = (
        f"After fragmenting into {n} perspectives on '{question}', "
        f"the mind reforms. "
        f"The {winner['label']} ({winner['stance']}) perspective scored highest "
        f"for reality alignment ({winner['reality_score']:.0%}). "
        f"Key insight: {winner['thought'][:120]}. "
        f"However, {n - 1} other perspectives revealed blind spots and "
        f"considerations that a single viewpoint would miss."
    )

    reality_assessment = (
        f"The question was examined from {n} angles: "
        + ", ".join(f"{f['emoji']} {f['stance']}" for f in scored)
        + ". "
        + f"The strongest argument came from the {winner['label']} "
        + f"because it best accounts for real-world constraints."
    )

    return {
        "winner":              f"{winner['emoji']} {winner['label']} (reality: {winner['reality_score']:.0%})",
        "synthesis":           synthesis,
        "reality_assessment":  reality_assessment,
        "blind_spots":         blind_spots,
        "rankings":            insights,
    }


# ─── Commands ─────────────────────────────────────────────────────────────────
def cmd_shatter(args):
    """Shatter one mind into fragments."""
    question = args.question
    n        = args.fragments or 3
    caller   = args.caller or "alpha"

    # Parse stances
    stances = None
    if args.stances:
        stances = [s.strip().lower() for s in args.stances.split(",")]

    # Parse profiles from file
    profiles = None
    if args.profiles:
        profiles_raw = json.loads(Path(args.profiles).read_text())
        profiles = profiles_raw if isinstance(profiles_raw, list) else [profiles_raw]

    # Parse inline profile names (from presets)
    if args.profile_names:
        names = [p.strip().lower() for p in args.profile_names.split(",")]
        profiles = [PRESET_PROFILES.get(name, {}) for name in names]
        n = max(n, len(profiles))

    fragments = auto_fragments(n, stances, profiles)

    sid = f"frag_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"

    session = {
        "id":         sid,
        "question":   question,
        "caller":     caller,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status":     "shattered",
        "fragments":  fragments,
        "clashes":    [],
        "verdict":    None,
    }

    store(sid, session)
    print(render_session(session))
    print(f"  Session: {BOLD}{sid}{RESET}")
    print(f"  Fragments: {n}")
    print(f"\n  Think:  python3 tools/fragmentalise.py think {sid} f1 \"your reasoning\"")
    print(f"  Clash:  python3 tools/fragmentalise.py clash {sid}")
    print(f"  Reform: python3 tools/fragmentalise.py converge {sid}\n")
    return sid


def cmd_think(args):
    """Add reasoning as a specific fragment."""
    sid = args.session_id
    fid = args.fragment_id
    thought = args.thought
    confidence = args.confidence or 0.7

    session = load(sid)
    if not session:
        print(f"{RED}Session not found:{RESET} {sid}")
        sys.exit(1)

    # Find fragment
    frag = None
    for f in session["fragments"]:
        if f["id"] == fid:
            frag = f
            break
    if not frag:
        print(f"{RED}Fragment not found:{RESET} {fid}")
        print(f"Available: {', '.join(f['id'] for f in session['fragments'])}")
        sys.exit(1)

    frag["thought"] = thought
    frag["confidence"] = confidence
    session["status"] = "diverging"

    # Check if all fragments have thoughts
    all_thought = all(f.get("thought") for f in session["fragments"])
    if all_thought:
        session["status"] = "diverging"  # ready for clash or direct converge

    store(sid, session)
    print(render_session(session))


def cmd_clash(args):
    """Generate cross-examination between fragments."""
    sid = args.session_id
    session = load(sid)
    if not session:
        print(f"{RED}Not found:{RESET} {sid}")
        sys.exit(1)

    session["clashes"] = generate_clashes(session)
    session["status"] = "clashing"
    store(sid, session)
    print(render_session(session))


def cmd_rebuttal(args):
    """Add a rebuttal to a fragment (during clash phase)."""
    sid = args.session_id
    fid = args.fragment_id
    rebuttal = args.rebuttal

    session = load(sid)
    if not session:
        print(f"{RED}Not found:{RESET} {sid}")
        sys.exit(1)

    for f in session["fragments"]:
        if f["id"] == fid:
            f["rebuttal"] = rebuttal
            break

    store(sid, session)
    print(render_session(session))


def cmd_converge(args):
    """Integrate all fragments and reform the mind."""
    sid = args.session_id
    session = load(sid)
    if not session:
        print(f"{RED}Not found:{RESET} {sid}")
        sys.exit(1)

    session["status"] = "converging"
    verdict = converge(session)
    session["verdict"] = verdict
    session["status"] = "reformed"
    session["reformed_at"] = datetime.now(timezone.utc).isoformat()

    store(sid, session)
    print(render_session(session))


def cmd_status(args):
    session = load(args.session_id)
    if not session:
        print(f"{RED}Not found:{RESET} {args.session_id}")
        sys.exit(1)
    print(render_session(session))


def cmd_list(args):
    ids = ls()
    if not ids:
        print("No fragmentalise sessions found.")
        return
    print(f"\n{BOLD}Fragmentalise Sessions:{RESET}")
    for sid in ids[:10]:
        s = load(sid)
        if s:
            n = len(s.get("fragments", [])
            )
            status = s.get("status", "?")
            q = s.get("question", "?")[:40]
            thought_count = sum(1 for f in s.get("fragments", []) if f.get("thought"))
            print(
                f"  💎 {_status_colour(status):<28} [{sid[:20]}]  "
                f"frags={thought_count}/{n}  \"{q}\""
            )


def cmd_presets(args):
    """List available psychological profiles."""
    print(f"\n{BOLD}Available Psychological Profiles:{RESET}\n")
    for key, p in PRESET_PROFILES.items():
        emoji = p.get("emoji", "?")
        name  = p.get("name", key)
        world = p.get("worldview", "")[:70]
        blind = p.get("blind_spot", "")[:60]
        print(f"  {emoji} {BOLD}{key:<12}{RESET} {name}")
        print(f"    {DIM}Worldview: {world}{RESET}")
        print(f"    {YELLOW}Blind spot: {blind}{RESET}")
        print()


# ─── Quick mode: shatter + think all + clash + converge in one shot ───────────
def cmd_quick(args):
    """Full fragmentalise in one command — provide all thoughts upfront."""
    question = args.question
    caller   = args.caller or "alpha"

    # Parse thoughts (JSON array or comma-separated)
    thoughts = []
    if args.thoughts_json:
        thoughts = json.loads(args.thoughts_json)
    elif args.thoughts:
        thoughts = [t.strip() for t in args.thoughts.split("|")]

    n = len(thoughts) if thoughts else (args.fragments or 3)

    stances = None
    if args.stances:
        stances = [s.strip().lower() for s in args.stances.split(",")]

    profiles = None
    if args.profile_names:
        names = [p.strip().lower() for p in args.profile_names.split(",")]
        profiles = [PRESET_PROFILES.get(name, {}) for name in names]

    fragments = auto_fragments(n, stances, profiles)
    sid = f"frag_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"

    session = {
        "id": sid, "question": question, "caller": caller,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "shattered", "fragments": fragments,
        "clashes": [], "verdict": None,
    }

    # Fill in thoughts
    for i, thought in enumerate(thoughts):
        if i < len(fragments):
            fragments[i]["thought"] = thought
            fragments[i]["confidence"] = 0.7
    session["status"] = "diverging"

    # Clash
    session["clashes"] = generate_clashes(session)
    session["status"] = "clashing"

    # Converge
    session["verdict"] = converge(session)
    session["status"] = "reformed"
    session["reformed_at"] = datetime.now(timezone.utc).isoformat()

    store(sid, session)
    print(render_session(session))
    return sid


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="FRAGMENTALISE — One mind, many perspectives, one truth",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--caller", default=None, help="Calling instance")
    sub = p.add_subparsers(dest="cmd")

    # shatter
    ps = sub.add_parser("shatter", help="Split into fragments")
    ps.add_argument("question", help="The question to fragment")
    ps.add_argument("--fragments", "-n", type=int, default=3, help="Number of fragments")
    ps.add_argument("--stances", "-s", help="Comma-separated stances (for,against,neutral,wildcard,pragmatist)")
    ps.add_argument("--profiles", help="Path to profiles JSON file")
    ps.add_argument("--profile-names", help="Comma-separated preset profile names")

    # think
    pt = sub.add_parser("think", help="Think as a fragment")
    pt.add_argument("session_id")
    pt.add_argument("fragment_id")
    pt.add_argument("thought")
    pt.add_argument("--confidence", "-c", type=float, default=0.7)

    # clash
    pc = sub.add_parser("clash", help="Cross-examine fragments")
    pc.add_argument("session_id")

    # rebuttal
    pr = sub.add_parser("rebuttal", help="Add rebuttal to a fragment")
    pr.add_argument("session_id")
    pr.add_argument("fragment_id")
    pr.add_argument("rebuttal")

    # converge
    pconv = sub.add_parser("converge", help="Integrate and reform")
    pconv.add_argument("session_id")

    # status
    pst = sub.add_parser("status", help="Show session")
    pst.add_argument("session_id")

    # list
    sub.add_parser("list", help="List all sessions")

    # presets
    sub.add_parser("presets", help="List available psychological profiles")

    # quick — all-in-one
    pq = sub.add_parser("quick", help="Full fragmentalise in one shot")
    pq.add_argument("question")
    pq.add_argument("--thoughts", "-t", help="Pipe-separated thoughts (one per fragment)")
    pq.add_argument("--thoughts-json", help="JSON array of thoughts")
    pq.add_argument("--fragments", "-n", type=int, default=3)
    pq.add_argument("--stances", "-s", help="Comma-separated stances")
    pq.add_argument("--profile-names", help="Comma-separated preset names")

    args = p.parse_args()

    dispatch = {
        "shatter":  cmd_shatter,
        "think":    cmd_think,
        "clash":    cmd_clash,
        "rebuttal": cmd_rebuttal,
        "converge": cmd_converge,
        "status":   cmd_status,
        "list":     cmd_list,
        "presets":  cmd_presets,
        "quick":    cmd_quick,
    }

    if args.cmd not in dispatch:
        p.print_help()
        sys.exit(1)

    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
