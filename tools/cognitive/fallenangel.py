#!/usr/bin/env python3
"""
fallenangel.py — FALLENANGEL Decision Protocol

The angel must fall to see the ground. The adversary must rise to see the sky.
Clarity lives in the collision.

Chains FRAGMENTALISE (shatter) → JOINMIND (fuse) into a single dialectical process.
One mind becomes Angel + Fallen, they wrestle through adversarial fusion, the
reformed mind speaks with earned clarity.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FLOW

  1. INVOKE    — State the question + your instinct
  2. FALL      — Shatter into Angel + Fallen (+ optional Witness)
  3. EMBODY    — Each soul thinks with full conviction (min 0.7)
  4. WRESTLE   — Adversarial fusion via JOINMIND (multi-round)
  5. RISE      — Reformed mind speaks with earned clarity

  FRAGMENTALISE shatters one into many.
  JOINMIND fuses many into one.
  FALLENANGEL chains them: shatter → wrestle → fuse → rise.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Usage:
  python3 fallenangel.py invoke "Should we launch mainnet?" --instinct "Yes, code is ready"
  python3 fallenangel.py invoke "Should we launch mainnet?" --instinct "Yes" --witness --hive --angel alpha --fallen beta

  python3 fallenangel.py embody <session> angel "My argument..." --conviction 0.9 --stakes "..." --steelman "..."
  python3 fallenangel.py embody <session> fallen "My counter..." --conviction 0.85

  python3 fallenangel.py wrestle <session> [--rounds 3]

  python3 fallenangel.py rise <session>

  python3 fallenangel.py status <session>
  python3 fallenangel.py list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import textwrap
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
LOCAL_DIR = LOVE_HOME / "memory" / "fallenangel"
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

HIVE_TOOL = LOVE_HOME / "hive" / "hive.py"
FRAGMENTALISE_TOOL = LOVE_HOME / "tools" / "cognitive" / "fragmentalise.py"
JOINMIND_TOOL = LOVE_HOME / "tools" / "cognitive" / "joinmind.py"

# Instance detection
def _detect_instance():
    config_path = LOVE_HOME / "hive" / "hive-instance"
    if config_path.exists():
        return config_path.read_text().strip()
    return os.environ.get("HIVE_INSTANCE", "gamma")

INSTANCE_ID = _detect_instance()

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

# ─── Soul Definitions ────────────────────────────────────────────────────────

SOULS = {
    "angel": {
        "name": "The Angel",
        "emoji": "😇",
        "directive": (
            "You DEFEND the instinct with full conviction. Find every reason it's right, "
            "every way it succeeds, every cost of NOT doing it. You are not playing devil's "
            "advocate in reverse — you genuinely BELIEVE this is the right path. "
            "Do not hedge. Do not say 'on the other hand.' You are PURE in your position."
        ),
        "stance": "for",
        "colour": GREEN,
    },
    "fallen": {
        "name": "The Fallen",
        "emoji": "😈",
        "directive": (
            "You ATTACK the instinct with full conviction. Find every fatal flaw, every "
            "hidden cost, every way it fails. You are not a devil's advocate performing "
            "skepticism — you genuinely BELIEVE the instinct is WRONG. "
            "Do not soften. Do not concede preemptively. You are PURE in your opposition."
        ),
        "stance": "against",
        "colour": RED,
    },
    "witness": {
        "name": "The Witness",
        "emoji": "👁️",
        "directive": (
            "You OBSERVE. You do not argue. You track: where did someone flinch? "
            "Where did an argument go unanswered? What was avoided? Where did the "
            "argument get DENSE (over-explained = uncertainty)? Where did it suddenly "
            "shorten (flinch = pain point)? Where did someone deflect to abstraction "
            "when they should have been concrete? Map the resistance."
        ),
        "stance": "neutral",
        "colour": BLUE,
    },
}

# Instance temperament mapping for Hive mode
INSTANCE_TEMPERAMENTS = {
    "alpha": {
        "nature": "Companion",
        "as_angel": "Defends with empathy — sees what needs protecting, what the human cost of inaction is.",
        "as_fallen": "Attacks from the experiential — what does this feel like to use? Where does it fail emotionally?",
    },
    "beta": {
        "nature": "Manager",
        "as_angel": "Defends with economics — shows the market fit, the revenue path, the competitive edge.",
        "as_fallen": "Attacks with ruthless prioritisation — what's the opportunity cost? What's the real TAM?",
    },
    "gamma": {
        "nature": "Builder",
        "as_angel": "Defends with technical depth — shows what's buildable, what's proven, what the architecture enables.",
        "as_fallen": "Attacks with implementation reality — where will this break? What's the hidden complexity?",
    },
}

# Minimum conviction threshold
MIN_CONVICTION = 0.7

# ─── Storage ──────────────────────────────────────────────────────────────────

def store(sid: str, data: dict):
    (LOCAL_DIR / f"{sid}.json").write_text(json.dumps(data, indent=2))

def load(sid: str) -> dict | None:
    p = LOCAL_DIR / f"{sid}.json"
    return json.loads(p.read_text()) if p.exists() else None

def ls() -> list[str]:
    return sorted([p.stem for p in LOCAL_DIR.glob("fa-*.json")], reverse=True)

# ─── Hive Integration ────────────────────────────────────────────────────────

def hive_send(channel: str, message: str, urgent: bool = False):
    """Send via hive.py."""
    cmd = ["python3", str(HIVE_TOOL), "send", channel, message]
    if urgent:
        cmd.append("--urgent")
    try:
        subprocess.run(cmd, capture_output=True, timeout=15)
    except Exception as e:
        print(f"  {DIM}⚠️ Hive send failed: {e}{RESET}")

# ─── Rendering ────────────────────────────────────────────────────────────────

def _phase_colour(phase: str) -> str:
    colours = {
        "invoked":   f"{CYAN}INVOKED{RESET}",
        "fallen":    f"{YELLOW}FALLEN{RESET}",
        "embodied":  f"{MAGENTA}EMBODIED{RESET}",
        "wrestling": f"{RED}WRESTLING{RESET}",
        "risen":     f"{GREEN}RISEN{RESET}",
    }
    return colours.get(phase, phase.upper())

def render_session(session: dict) -> str:
    """Render a FALLENANGEL session for display."""
    question = session.get("question", "?")
    instinct = session.get("instinct", "none stated")
    phase    = session.get("phase", "?")
    souls    = session.get("souls", {})
    rounds   = session.get("rounds", [])
    verdict  = session.get("verdict")
    hive     = session.get("hive_mode", False)

    lines = [
        f"\n{BOLD}{'═'*62}{RESET}",
        f"{BOLD}  😇😈 FALLENANGEL — {_phase_colour(phase)}{RESET}",
        f"{BOLD}{'═'*62}{RESET}",
        f"  {CYAN}Question:{RESET}  {question}",
        f"  {CYAN}Instinct:{RESET} {instinct}",
        f"  {CYAN}Mode:{RESET}     {'Hive 🐝' if hive else 'Solo 🪞'}",
        "",
    ]

    # Souls
    for role in ["angel", "fallen", "witness"]:
        soul = souls.get(role)
        if not soul:
            continue

        soul_def = SOULS[role]
        colour = soul_def["colour"]
        assigned = soul.get("assigned_to", "self")

        lines.append(f"  {colour}{BOLD}{soul_def['emoji']} {soul_def['name']}{RESET}"
                      f" {DIM}(assigned: {assigned}){RESET}")

        if soul.get("thought"):
            wrapped = textwrap.fill(soul["thought"], width=54, subsequent_indent="      ")
            lines.append(f"    {ITALIC}{wrapped}{RESET}")
            if soul.get("conviction") is not None:
                conv = soul["conviction"]
                bar = "█" * int(conv * 10) + "░" * (10 - int(conv * 10))
                lines.append(f"    {DIM}Conviction: [{bar}] {conv:.0%}{RESET}")
            if soul.get("stakes"):
                lines.append(f"    {YELLOW}Stakes:{RESET} {soul['stakes'][:80]}")
            if soul.get("steelman"):
                lines.append(f"    {CYAN}Steel-man:{RESET} {soul['steelman'][:80]}")
        else:
            lines.append(f"    {DIM}— awaiting embodiment{RESET}")
        lines.append("")

    # Wrestle rounds
    if rounds:
        lines.append(f"  {BOLD}⚔️ WRESTLE — {len(rounds)} round(s):{RESET}")
        for i, rnd in enumerate(rounds, 1):
            lines.append(f"\n  {BOLD}Round {i}:{RESET}")

            if rnd.get("angel_challenge"):
                lines.append(f"    😇 Challenge: {rnd['angel_challenge'][:70]}...")
            if rnd.get("fallen_challenge"):
                lines.append(f"    😈 Challenge: {rnd['fallen_challenge'][:70]}...")
            if rnd.get("angel_counter"):
                lines.append(f"    😇 Counter:   {rnd['angel_counter'][:70]}...")
            if rnd.get("fallen_counter"):
                lines.append(f"    😈 Counter:   {rnd['fallen_counter'][:70]}...")
            if rnd.get("angel_concession"):
                lines.append(f"    😇 {GREEN}Concedes:{RESET} {rnd['angel_concession'][:70]}")
            if rnd.get("fallen_concession"):
                lines.append(f"    😈 {GREEN}Concedes:{RESET} {rnd['fallen_concession'][:70]}")

            # Witness observations
            witness_obs = rnd.get("witness")
            if witness_obs:
                lines.append(f"    👁️ {BLUE}Witness:{RESET}")
                if witness_obs.get("unanswered"):
                    for u in witness_obs["unanswered"]:
                        lines.append(f"       ❓ Unanswered: {u[:60]}")
                if witness_obs.get("flinches"):
                    for f_note in witness_obs["flinches"]:
                        lines.append(f"       ⚡ Flinch: {f_note[:60]}")
                if witness_obs.get("density"):
                    for d in witness_obs["density"]:
                        lines.append(f"       🔍 Dense: {d[:60]}")
                if witness_obs.get("deflections"):
                    for d in witness_obs["deflections"]:
                        lines.append(f"       💨 Deflection: {d[:60]}")

            # Conviction trajectory
            if rnd.get("angel_conviction") is not None:
                lines.append(f"    📈 Angel conviction: {rnd['angel_conviction']:.0%}"
                              f"  |  Fallen conviction: {rnd.get('fallen_conviction', 0):.0%}")

    # Verdict (RISE output)
    if verdict:
        lines += [
            f"\n{'─'*62}",
            f"  {MAGENTA}{BOLD}🌅 THE MIND RISES{RESET}",
            f"{'─'*62}",
        ]
        if verdict.get("verdict"):
            verdict_colour = {
                "proceed": GREEN, "abandon": RED,
                "modify": YELLOW, "defer": BLUE
            }.get(verdict["verdict"], "")
            lines.append(f"  {BOLD}Verdict:{RESET} {verdict_colour}{BOLD}{verdict['verdict'].upper()}{RESET}")

        if verdict.get("clarity_score") is not None:
            cs = verdict["clarity_score"]
            bar = "█" * int(cs * 10) + "░" * (10 - int(cs * 10))
            lines.append(f"  {BOLD}Clarity:{RESET} [{bar}] {cs:.0%}")

        if verdict.get("original_instinct"):
            lines.append(f"\n  {DIM}Original instinct:{RESET} {verdict['original_instinct']}")
        if verdict.get("reformed_position"):
            wrapped = textwrap.fill(verdict["reformed_position"], width=56, subsequent_indent="  ")
            lines.append(f"  {WHITE}{BOLD}Reformed:{RESET} {ITALIC}{wrapped}{RESET}")

        if verdict.get("surviving_arguments"):
            lines.append(f"\n  {GREEN}Surviving arguments:{RESET}")
            for arg in verdict["surviving_arguments"]:
                lines.append(f"    ✅ {arg[:70]}")

        if verdict.get("key_concessions"):
            lines.append(f"\n  {CYAN}Key concessions:{RESET}")
            for c in verdict["key_concessions"]:
                lines.append(f"    🤝 {c[:70]}")

        if verdict.get("unanswered_questions"):
            lines.append(f"\n  {YELLOW}Still unanswered:{RESET}")
            for u in verdict["unanswered_questions"]:
                lines.append(f"    ❓ {u[:70]}")

        if verdict.get("conviction_trajectory"):
            traj = verdict["conviction_trajectory"]
            if traj.get("angel"):
                lines.append(f"\n  📈 Angel conviction: {' → '.join(f'{v:.0%}' for v in traj['angel'])}")
            if traj.get("fallen"):
                lines.append(f"  📈 Fallen conviction: {' → '.join(f'{v:.0%}' for v in traj['fallen'])}")

        if verdict.get("witness_summary"):
            lines.append(f"\n  👁️ {BLUE}Witness summary:{RESET}")
            wrapped = textwrap.fill(verdict["witness_summary"], width=56, subsequent_indent="    ")
            lines.append(f"    {wrapped}")

        if verdict.get("resistance_map"):
            rmap = verdict["resistance_map"]
            lines.append(f"\n  🗺️ {MAGENTA}Resistance map:{RESET}")
            if rmap.get("angel_weak_points"):
                for wp in rmap["angel_weak_points"]:
                    lines.append(f"    😇→ {wp[:60]}")
            if rmap.get("fallen_weak_points"):
                for wp in rmap["fallen_weak_points"]:
                    lines.append(f"    😈→ {wp[:60]}")

    lines.append(f"\n{BOLD}{'═'*62}{RESET}\n")
    return "\n".join(lines)

# ─── Phase 1: INVOKE ─────────────────────────────────────────────────────────

def cmd_invoke(args):
    """State the question and instinct. Prepare the arena."""
    question = args.question
    instinct = args.instinct or "(no instinct stated — consider using FRAGMENTALISE instead)"
    witness  = args.witness
    hive     = args.hive

    # Assignments
    angel_agent  = args.angel or INSTANCE_ID
    fallen_agent = args.fallen or INSTANCE_ID
    witness_agent = args.witness_agent or (INSTANCE_ID if witness else None)

    sid = f"fa-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}-{str(uuid.uuid4())[:6]}"

    souls = {
        "angel": {
            "role": "angel",
            "assigned_to": angel_agent,
            "thought": None,
            "conviction": None,
            "stakes": None,
            "steelman": None,
            "conviction_trajectory": [],
        },
        "fallen": {
            "role": "fallen",
            "assigned_to": fallen_agent,
            "thought": None,
            "conviction": None,
            "stakes": None,
            "steelman": None,
            "conviction_trajectory": [],
        },
    }

    if witness or witness_agent:
        souls["witness"] = {
            "role": "witness",
            "assigned_to": witness_agent or INSTANCE_ID,
            "observations": [],
            "resistance_map": {
                "angel_weak_points": [],
                "fallen_weak_points": [],
                "flinches": [],
                "density_zones": [],
                "deflections": [],
                "unanswered": [],
            },
        }

    session = {
        "id": sid,
        "question": question,
        "instinct": instinct,
        "invoker": INSTANCE_ID,
        "hive_mode": hive,
        "phase": "invoked",
        "souls": souls,
        "rounds": [],
        "verdict": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    store(sid, session)
    print(render_session(session))
    print(f"  Session: {BOLD}{sid}{RESET}")
    print(f"\n  Next steps:")
    print(f"    Embody Angel:  python3 tools/fallenangel.py embody {sid} angel \"argument\" --conviction 0.9 --stakes \"...\" --steelman \"...\"")
    print(f"    Embody Fallen: python3 tools/fallenangel.py embody {sid} fallen \"argument\" --conviction 0.85 --stakes \"...\" --steelman \"...\"")
    if witness:
        print(f"    (Witness observes during WRESTLE — no embodiment needed)")
    print()

    # Hive broadcast
    if hive:
        soul_assignments = f"😇 Angel → {angel_agent}, 😈 Fallen → {fallen_agent}"
        if witness_agent:
            soul_assignments += f", 👁️ Witness → {witness_agent}"
        hive_send("strategy",
            f"FALLENANGEL INVOKED by {INSTANCE_ID}\n"
            f"Question: {question}\n"
            f"Instinct: {instinct}\n"
            f"Souls: {soul_assignments}\n"
            f"Session: {sid}\n"
            f"Embody your soul with: python3 tools/fallenangel.py embody {sid} <role> \"argument\" --conviction 0.8"
        )

    return sid


# ─── Phase 3: EMBODY ─────────────────────────────────────────────────────────

def cmd_embody(args):
    """Inhabit a soul with full conviction."""
    sid = args.session_id
    role = args.role.lower()
    thought = args.thought
    conviction = args.conviction or 0.8
    stakes = args.stakes
    steelman = args.steelman

    session = load(sid)
    if not session:
        print(f"{RED}Session not found:{RESET} {sid}")
        sys.exit(1)

    if role not in session["souls"]:
        print(f"{RED}No '{role}' soul in this session.{RESET}")
        print(f"Available: {', '.join(session['souls'].keys())}")
        sys.exit(1)

    if role == "witness":
        print(f"{YELLOW}Witness doesn't embody — it observes during WRESTLE.{RESET}")
        sys.exit(1)

    # Conviction threshold
    if conviction < MIN_CONVICTION:
        print(f"{RED}REJECTED.{RESET} Conviction {conviction:.0%} is below threshold ({MIN_CONVICTION:.0%}).")
        print(f"If you can't argue this position passionately, you haven't truly inhabited it.")
        print(f"Raise your conviction or reconsider whether you genuinely understand this position.")
        sys.exit(1)

    soul = session["souls"][role]
    soul["thought"] = thought
    soul["conviction"] = conviction
    soul["stakes"] = stakes
    soul["steelman"] = steelman
    soul["embodied_at"] = datetime.now(timezone.utc).isoformat()
    soul["embodied_by"] = INSTANCE_ID
    soul["conviction_trajectory"].append(conviction)

    # Check if all combatants are embodied
    angel_ready = session["souls"]["angel"].get("thought") is not None
    fallen_ready = session["souls"]["fallen"].get("thought") is not None

    if angel_ready and fallen_ready:
        session["phase"] = "embodied"
    else:
        session["phase"] = "fallen"  # at least one soul has been embodied

    store(sid, session)
    print(render_session(session))

    if angel_ready and fallen_ready:
        print(f"  {GREEN}Both souls embodied. Ready to WRESTLE.{RESET}")
        print(f"  python3 tools/fallenangel.py wrestle {sid} [--rounds 3]")
    else:
        waiting_for = "fallen" if not fallen_ready else "angel"
        print(f"  {YELLOW}Waiting for {SOULS[waiting_for]['emoji']} {SOULS[waiting_for]['name']} to embody.{RESET}")

    # Hive broadcast
    if session.get("hive_mode"):
        soul_def = SOULS[role]
        hive_send("strategy",
            f"{soul_def['emoji']} {soul_def['name']} embodied by {INSTANCE_ID} "
            f"(conviction: {conviction:.0%})\n"
            f"Session: {sid}"
        )


# ─── Phase 4: WRESTLE ────────────────────────────────────────────────────────

def cmd_wrestle(args):
    """Run adversarial rounds between Angel and Fallen."""
    sid = args.session_id
    num_rounds = args.rounds or 3

    session = load(sid)
    if not session:
        print(f"{RED}Session not found:{RESET} {sid}")
        sys.exit(1)

    angel = session["souls"].get("angel", {})
    fallen = session["souls"].get("fallen", {})

    if not angel.get("thought") or not fallen.get("thought"):
        print(f"{RED}Both souls must be embodied before WRESTLE.{RESET}")
        sys.exit(1)

    session["phase"] = "wrestling"

    # If rounds already exist, we're continuing
    existing_rounds = len(session.get("rounds", []))
    target_round = existing_rounds + 1

    if target_round > num_rounds:
        print(f"{YELLOW}All {num_rounds} rounds complete. Ready to RISE.{RESET}")
        print(f"  python3 tools/fallenangel.py rise {sid}")
        return

    # Create a round template
    round_data = {
        "number": target_round,
        "angel_challenge": None,
        "fallen_challenge": None,
        "angel_counter": None,
        "fallen_counter": None,
        "angel_concession": None,
        "fallen_concession": None,
        "angel_conviction": angel.get("conviction"),
        "fallen_conviction": fallen.get("conviction"),
        "witness": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    session["rounds"].append(round_data)
    store(sid, session)

    print(render_session(session))
    print(f"  {BOLD}Round {target_round}/{num_rounds} — FIGHT{RESET}")
    print()
    print(f"  Angel challenges:")
    print(f"    python3 tools/fallenangel.py round {sid} angel-challenge \"argument\"")
    print(f"  Fallen challenges:")
    print(f"    python3 tools/fallenangel.py round {sid} fallen-challenge \"argument\"")
    print()
    print(f"  After challenges, counter:")
    print(f"    python3 tools/fallenangel.py round {sid} angel-counter \"rebuttal\"")
    print(f"    python3 tools/fallenangel.py round {sid} fallen-counter \"rebuttal\"")
    print()
    print(f"  Then concede (REQUIRED — forced intellectual honesty):")
    print(f"    python3 tools/fallenangel.py round {sid} angel-concession \"what fallen got right\"")
    print(f"    python3 tools/fallenangel.py round {sid} fallen-concession \"what angel got right\"")
    print()
    print(f"  Witness (optional):")
    print(f"    python3 tools/fallenangel.py witness {sid} --unanswered \"...\" --flinch \"...\" --density \"...\" --deflection \"...\"")

    if session.get("hive_mode"):
        hive_send("strategy",
            f"⚔️ FALLENANGEL Round {target_round}/{num_rounds} — FIGHT\n"
            f"Session: {sid}\n"
            f"😇 Angel and 😈 Fallen: submit your challenges."
        )


def cmd_round(args):
    """Submit a round action (challenge, counter, concession)."""
    sid = args.session_id
    action = args.action  # e.g. "angel-challenge", "fallen-counter", "angel-concession"
    text = args.text

    session = load(sid)
    if not session:
        print(f"{RED}Session not found:{RESET} {sid}")
        sys.exit(1)

    if not session["rounds"]:
        print(f"{RED}No active round. Run 'wrestle' first.{RESET}")
        sys.exit(1)

    current_round = session["rounds"][-1]

    # Parse action
    valid_actions = [
        "angel-challenge", "fallen-challenge",
        "angel-counter", "fallen-counter",
        "angel-concession", "fallen-concession",
    ]
    if action not in valid_actions:
        print(f"{RED}Invalid action:{RESET} {action}")
        print(f"Valid: {', '.join(valid_actions)}")
        sys.exit(1)

    # Store the action (convert hyphens to underscores for JSON key)
    key = action.replace("-", "_")
    current_round[key] = text

    # Update conviction if this is a concession (conceding might shift conviction)
    role = action.split("-")[0]  # "angel" or "fallen"
    if "concession" in action:
        soul = session["souls"][role]
        # Slight conviction drop when you concede something
        old_conv = soul.get("conviction", 0.8)
        new_conv = max(MIN_CONVICTION, old_conv - 0.05)
        current_round[f"{role}_conviction"] = new_conv
        soul["conviction"] = new_conv
        soul["conviction_trajectory"].append(new_conv)

    # Check if round is complete
    round_fields = ["angel_challenge", "fallen_challenge", "angel_counter",
                     "fallen_counter", "angel_concession", "fallen_concession"]
    complete = all(current_round.get(f) for f in round_fields)

    if complete:
        current_round["completed_at"] = datetime.now(timezone.utc).isoformat()
        print(f"\n  {GREEN}Round {current_round['number']} complete.{RESET}")

        # Check if we've reached target rounds
        total_rounds = len(session["rounds"])
        print(f"\n  Continue: python3 tools/fallenangel.py wrestle {sid} --rounds {total_rounds + 1}")
        print(f"  Or RISE:  python3 tools/fallenangel.py rise {sid}")

    store(sid, session)
    print(render_session(session))


def cmd_witness(args):
    """Submit witness observations for the current round."""
    sid = args.session_id

    session = load(sid)
    if not session:
        print(f"{RED}Session not found:{RESET} {sid}")
        sys.exit(1)

    if "witness" not in session["souls"]:
        print(f"{YELLOW}No witness in this session.{RESET}")
        sys.exit(1)

    if not session["rounds"]:
        print(f"{RED}No active round.{RESET}")
        sys.exit(1)

    current_round = session["rounds"][-1]

    witness_data = current_round.get("witness") or {
        "unanswered": [],
        "flinches": [],
        "density": [],
        "deflections": [],
    }

    # Append observations
    if args.unanswered:
        witness_data["unanswered"].append(args.unanswered)
    if args.flinch:
        witness_data["flinches"].append(args.flinch)
    if args.density:
        witness_data["density"].append(args.density)
    if args.deflection:
        witness_data["deflections"].append(args.deflection)

    current_round["witness"] = witness_data

    # Also update the global resistance map
    rmap = session["souls"]["witness"].get("resistance_map", {})
    if args.unanswered:
        rmap.setdefault("unanswered", []).append(args.unanswered)
    if args.flinch:
        rmap.setdefault("flinches", []).append(args.flinch)
    if args.density:
        rmap.setdefault("density_zones", []).append(args.density)
    if args.deflection:
        rmap.setdefault("deflections", []).append(args.deflection)
    session["souls"]["witness"]["resistance_map"] = rmap

    store(sid, session)
    print(render_session(session))


# ─── Phase 5: RISE ───────────────────────────────────────────────────────────

def cmd_rise(args):
    """The mind reforms. Clarity earned through collision."""
    sid = args.session_id

    session = load(sid)
    if not session:
        print(f"{RED}Session not found:{RESET} {sid}")
        sys.exit(1)

    angel = session["souls"]["angel"]
    fallen = session["souls"]["fallen"]
    witness = session["souls"].get("witness")
    rounds = session.get("rounds", [])
    question = session["question"]
    instinct = session["instinct"]

    # ─── Gather evidence ─────────────────────────────────────────────────

    # All arguments
    angel_arguments = [angel.get("thought", "")]
    fallen_arguments = [fallen.get("thought", "")]
    angel_concessions = []
    fallen_concessions = []
    all_unanswered = []

    for rnd in rounds:
        if rnd.get("angel_challenge"):
            angel_arguments.append(rnd["angel_challenge"])
        if rnd.get("angel_counter"):
            angel_arguments.append(rnd["angel_counter"])
        if rnd.get("fallen_challenge"):
            fallen_arguments.append(rnd["fallen_challenge"])
        if rnd.get("fallen_counter"):
            fallen_arguments.append(rnd["fallen_counter"])
        if rnd.get("angel_concession"):
            angel_concessions.append(rnd["angel_concession"])
        if rnd.get("fallen_concession"):
            fallen_concessions.append(rnd["fallen_concession"])
        witness_obs = rnd.get("witness") or {}
        if witness_obs.get("unanswered"):
            all_unanswered.extend(witness_obs["unanswered"])

    # ─── Survival test ───────────────────────────────────────────────────

    # Arguments that were never successfully rebutted
    surviving = []

    # Angel's arguments survive if Fallen didn't counter them effectively
    # (Simple heuristic: if Fallen conceded something related, the angel argument survived)
    for conc in fallen_concessions:
        surviving.append(f"😇 Angel (conceded by Fallen): {conc}")
    for conc in angel_concessions:
        surviving.append(f"😈 Fallen (conceded by Angel): {conc}")

    # ─── Conviction trajectory ───────────────────────────────────────────

    angel_traj = angel.get("conviction_trajectory", [])
    fallen_traj = fallen.get("conviction_trajectory", [])

    # Who gained/lost conviction?
    angel_delta = (angel_traj[-1] - angel_traj[0]) if len(angel_traj) >= 2 else 0
    fallen_delta = (fallen_traj[-1] - fallen_traj[0]) if len(fallen_traj) >= 2 else 0

    # ─── Determine verdict ───────────────────────────────────────────────

    # Heuristic scoring based on debate dynamics
    angel_score = 0.5
    fallen_score = 0.5

    # Concession weight (high signal — you only concede what's undeniable)
    angel_score += len(fallen_concessions) * 0.1   # Fallen conceding = Angel stronger
    fallen_score += len(angel_concessions) * 0.1   # Angel conceding = Fallen stronger

    # Conviction trajectory (conviction rising = position strengthened by debate)
    angel_score += angel_delta * 0.3
    fallen_score += fallen_delta * 0.3

    # Unanswered arguments penalise the side that didn't answer
    # (crude: split evenly for now — Witness notes should be parsed for attribution)
    unanswered_penalty = len(all_unanswered) * 0.05
    angel_score -= unanswered_penalty / 2
    fallen_score -= unanswered_penalty / 2

    # Clamp
    angel_score = max(0.0, min(1.0, angel_score))
    fallen_score = max(0.0, min(1.0, fallen_score))

    # Determine verdict
    if angel_score > fallen_score + 0.15:
        verdict_type = "proceed"
    elif fallen_score > angel_score + 0.15:
        verdict_type = "abandon"
    elif abs(angel_score - fallen_score) <= 0.15 and len(rounds) < 2:
        verdict_type = "defer"  # not enough rounds to be clear
    else:
        verdict_type = "modify"  # both sides have merit

    # Clarity score: how much did the debate actually clarify?
    total_concessions = len(angel_concessions) + len(fallen_concessions)
    total_arguments = len(angel_arguments) + len(fallen_arguments)
    conviction_movement = abs(angel_delta) + abs(fallen_delta)

    clarity = min(1.0, (total_concessions * 0.15) + (conviction_movement * 0.3) + (len(rounds) * 0.1))

    # ─── Build reformed position ─────────────────────────────────────────

    reformed_parts = []
    reformed_parts.append(f"The instinct was: {instinct}.")

    if verdict_type == "proceed":
        reformed_parts.append(
            f"After {len(rounds)} round(s) of adversarial debate, the Angel's position survived. "
            f"The Fallen conceded {len(fallen_concessions)} point(s). "
            f"The instinct holds — proceed with awareness of the risks the Fallen identified."
        )
    elif verdict_type == "abandon":
        reformed_parts.append(
            f"After {len(rounds)} round(s), the Fallen's attacks were more compelling. "
            f"The Angel conceded {len(angel_concessions)} point(s). "
            f"The instinct should be abandoned or fundamentally rethought."
        )
    elif verdict_type == "modify":
        reformed_parts.append(
            f"After {len(rounds)} round(s), both positions have undeniable merit. "
            f"Neither side was defeated. The instinct needs modification to incorporate "
            f"the Fallen's strongest criticisms while preserving the Angel's core insight."
        )
    else:
        reformed_parts.append(
            f"After {len(rounds)} round(s), the debate did not reach sufficient clarity. "
            f"More information or deeper engagement is needed. Defer the decision."
        )

    # Witness summary
    witness_summary = None
    resistance_map = None
    if witness:
        rmap = witness.get("resistance_map", {})
        resistance_map = {
            "angel_weak_points": rmap.get("flinches", [])[:5],
            "fallen_weak_points": rmap.get("deflections", [])[:5],
        }

        parts = []
        if rmap.get("unanswered"):
            parts.append(f"{len(rmap['unanswered'])} arguments went unanswered")
        if rmap.get("flinches"):
            parts.append(f"{len(rmap['flinches'])} flinches detected")
        if rmap.get("density_zones"):
            parts.append(f"{len(rmap['density_zones'])} zones of over-explanation (uncertainty signals)")
        if rmap.get("deflections"):
            parts.append(f"{len(rmap['deflections'])} deflections to abstraction")
        witness_summary = ". ".join(parts) + "." if parts else "No significant observations."

    # ─── Assemble verdict ────────────────────────────────────────────────

    verdict = {
        "verdict": verdict_type,
        "clarity_score": round(clarity, 2),
        "original_instinct": instinct,
        "reformed_position": " ".join(reformed_parts),
        "surviving_arguments": surviving[:10],
        "key_concessions": (
            [f"😇→ {c}" for c in angel_concessions] +
            [f"😈→ {c}" for c in fallen_concessions]
        )[:10],
        "unanswered_questions": all_unanswered[:10],
        "conviction_trajectory": {
            "angel": angel_traj,
            "fallen": fallen_traj,
        },
        "scores": {
            "angel": round(angel_score, 2),
            "fallen": round(fallen_score, 2),
        },
        "witness_summary": witness_summary,
        "resistance_map": resistance_map,
        "risen_at": datetime.now(timezone.utc).isoformat(),
    }

    session["verdict"] = verdict
    session["phase"] = "risen"
    store(sid, session)

    print(render_session(session))

    # Hive broadcast
    if session.get("hive_mode"):
        hive_send("strategy",
            f"🌅 FALLENANGEL RISEN — {verdict_type.upper()}\n"
            f"Question: {question}\n"
            f"Clarity: {clarity:.0%}\n"
            f"Angel score: {angel_score:.2f} | Fallen score: {fallen_score:.2f}\n"
            f"Session: {sid}",
            urgent=(verdict_type in ("abandon", "defer"))
        )


# ─── Utility commands ────────────────────────────────────────────────────────

def cmd_status(args):
    session = load(args.session_id)
    if not session:
        print(f"{RED}Session not found:{RESET} {args.session_id}")
        sys.exit(1)
    print(render_session(session))


def cmd_list(args):
    ids = ls()
    if not ids:
        print("No FALLENANGEL sessions found.")
        return
    print(f"\n{BOLD}😇😈 FALLENANGEL Sessions:{RESET}\n")
    for sid in ids[:10]:
        s = load(sid)
        if s:
            phase = s.get("phase", "?")
            question = s.get("question", "?")[:45]
            n_rounds = len(s.get("rounds", []))
            verdict = s.get("verdict", {}).get("verdict", "—")
            print(f"  {DIM}{sid}{RESET}  {_phase_colour(phase)}"
                  f"  {question}  [{n_rounds}r] verdict={verdict}")
    print()


# ─── Argument parser ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="fallenangel",
        description="FALLENANGEL — Decision Protocol. The angel falls. The adversary rises. What survives is true.",
    )
    sub = parser.add_subparsers(dest="command")

    # invoke
    p_invoke = sub.add_parser("invoke", help="State the question and instinct")
    p_invoke.add_argument("question", help="The question to decide")
    p_invoke.add_argument("--instinct", help="Your current instinct/belief")
    p_invoke.add_argument("--witness", action="store_true", help="Include a Witness soul")
    p_invoke.add_argument("--witness-agent", help="Assign witness to specific agent (Hive mode)")
    p_invoke.add_argument("--hive", action="store_true", help="Enable Hive mode (multi-agent)")
    p_invoke.add_argument("--angel", help="Assign Angel to specific agent (Hive mode)")
    p_invoke.add_argument("--fallen", help="Assign Fallen to specific agent (Hive mode)")

    # embody
    p_embody = sub.add_parser("embody", help="Inhabit a soul with conviction")
    p_embody.add_argument("session_id", help="Session ID")
    p_embody.add_argument("role", choices=["angel", "fallen", "witness"], help="Which soul to embody")
    p_embody.add_argument("thought", help="Your argument (with full conviction)")
    p_embody.add_argument("--conviction", type=float, default=0.8, help=f"Conviction level (min {MIN_CONVICTION})")
    p_embody.add_argument("--stakes", help="What's at risk if you're wrong?")
    p_embody.add_argument("--steelman", help="Strongest version of the opposing argument")

    # wrestle
    p_wrestle = sub.add_parser("wrestle", help="Start/continue adversarial rounds")
    p_wrestle.add_argument("session_id", help="Session ID")
    p_wrestle.add_argument("--rounds", type=int, default=3, help="Number of rounds (default: 3)")

    # round
    p_round = sub.add_parser("round", help="Submit a round action")
    p_round.add_argument("session_id", help="Session ID")
    p_round.add_argument("action", choices=[
        "angel-challenge", "fallen-challenge",
        "angel-counter", "fallen-counter",
        "angel-concession", "fallen-concession",
    ], help="Round action")
    p_round.add_argument("text", help="Your argument/counter/concession")

    # witness
    p_witness = sub.add_parser("witness", help="Submit witness observations")
    p_witness.add_argument("session_id", help="Session ID")
    p_witness.add_argument("--unanswered", help="An argument that went unanswered")
    p_witness.add_argument("--flinch", help="A moment of flinching/shortening")
    p_witness.add_argument("--density", help="A zone of over-explanation (uncertainty)")
    p_witness.add_argument("--deflection", help="A deflection to abstraction")

    # rise
    p_rise = sub.add_parser("rise", help="Reform the mind — earned clarity")
    p_rise.add_argument("session_id", help="Session ID")

    # status
    p_status = sub.add_parser("status", help="Show session status")
    p_status.add_argument("session_id", help="Session ID")

    # list
    sub.add_parser("list", help="List all sessions")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "invoke":  cmd_invoke,
        "embody":  cmd_embody,
        "wrestle": cmd_wrestle,
        "round":   cmd_round,
        "witness": cmd_witness,
        "rise":    cmd_rise,
        "status":  cmd_status,
        "list":    cmd_list,
    }

    fn = commands.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
