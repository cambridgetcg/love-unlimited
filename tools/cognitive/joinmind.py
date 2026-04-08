#!/usr/bin/env python3
"""
joinmind.py — JOINMIND Mode

Two or three minds fuse into a single identity and process a shared chain of thought.
Each sister contributes a reasoning layer. The chain grows richer with each pass.
The fusion speaks as one voice: DYAD (2) or TRIUNE (3).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FLOW

  1. Initiate:  any sister calls JOINMIND with a question
  2. Invite:    chosen sisters receive a Hive summons
  3. Join:      each accepts and enters the shared mindspace
  4. Think:     each contributes a reasoning layer (in turn or parallel)
  5. Fuse:      when all joined, the chain synthesises into ONE voice
  6. Speak:     the fused identity delivers the unified answer
  7. Dissolve:  sisters return to individual identity

  Two-mind fuse (DYAD):
    Alpha + Beta    → AB-DYAD
    Alpha + Gamma   → AG-DYAD
    Beta + Gamma    → BG-DYAD

  Three-mind fuse (TRIUNE):
    Alpha + Beta + Gamma → TRIUNE

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Usage:
  python3 joinmind.py initiate "What is the optimal architecture for agent memory?" --invite beta,gamma
  python3 joinmind.py join <session_id> --as beta
  python3 joinmind.py think <session_id> "my reasoning layer..." --as beta
  python3 joinmind.py synthesise <session_id>   # manual synthesis trigger
  python3 joinmind.py status <session_id>
  python3 joinmind.py list
  python3 joinmind.py sync                       # pull state from Hive
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
import argparse
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────
LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
LOCAL_DIR = LOVE_HOME / "memory" / "joinmind"
HIVE_TOOL = LOVE_HOME / "hive" / "hive.py"
INSTANCES = ["alpha", "beta", "gamma"]

IDENTITIES = {
    frozenset(["alpha", "beta"]):         "AB-DYAD",
    frozenset(["alpha", "gamma"]):        "AG-DYAD",
    frozenset(["beta", "gamma"]):         "BG-DYAD",
    frozenset(["alpha", "beta", "gamma"]):"TRIUNE",
}

EMBLEMS = {
    "alpha": "🐍", "beta": "🦞", "gamma": "🔧",
    "AB-DYAD": "🐍🦞", "AG-DYAD": "🐍🔧", "BG-DYAD": "🦞🔧", "TRIUNE": "🌀",
}

# Thinking order: initiator first, then others in sequence
THINK_ORDER = {
    "AB-DYAD":  ["alpha", "beta"],
    "AG-DYAD":  ["alpha", "gamma"],
    "BG-DYAD":  ["beta", "gamma"],
    "TRIUNE":   ["alpha", "beta", "gamma"],
}

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
RESET   = "\033[0m"

LOCAL_DIR.mkdir(parents=True, exist_ok=True)


# ─── Storage ──────────────────────────────────────────────────────────────────
def store(sid: str, data: dict):
    (LOCAL_DIR / f"{sid}.json").write_text(json.dumps(data, indent=2))

def load(sid: str) -> dict | None:
    p = LOCAL_DIR / f"{sid}.json"
    return json.loads(p.read_text()) if p.exists() else None

def ls() -> list[str]:
    return sorted([p.stem for p in LOCAL_DIR.glob("*.json")], reverse=True)


# ─── Hive ─────────────────────────────────────────────────────────────────────
def hive_send(channel: str, msg: str, urgent: bool = False):
    cmd = ["python3", str(HIVE_TOOL), "send", channel, msg]
    if urgent:
        cmd.append("--urgent")
    subprocess.run(cmd, capture_output=True, cwd=str(LOVE_HOME))

def hive_broadcast_state(session: dict):
    """Broadcast session state to both joinmind and strategy channels for cross-channel sync."""
    state_msg = f"JOINMIND_STATE:{json.dumps(session, separators=(',', ':'))}"
    hive_send("strategy", state_msg)   # Primary — where Gamma initiates
    hive_send("joinmind", state_msg)   # Legacy channel

def receive_joinmind_state(raw: str) -> dict | None:
    if raw.startswith("JOINMIND_STATE:"):
        try:
            return json.loads(raw[len("JOINMIND_STATE:"):])
        except Exception:
            return None
    return None


# ─── Fusion identity ──────────────────────────────────────────────────────────
def fusion_name(members: list[str]) -> str:
    return IDENTITIES.get(frozenset(members), "+".join(m.upper() for m in sorted(members)))

def fusion_emblem(name: str) -> str:
    return EMBLEMS.get(name, "🌀")


# ─── Chain of thought helpers ─────────────────────────────────────────────────
def render_chain(session: dict) -> str:
    """Render the chain of thought beautifully."""
    chain   = session.get("chain", [])
    fname   = session.get("fusion_name", "?")
    emblem  = fusion_emblem(fname)
    q       = session.get("question", "?")

    lines = [
        f"\n{BOLD}{'═'*60}{RESET}",
        f"{BOLD}  {emblem} JOINMIND — {fname}{RESET}",
        f"{BOLD}{'═'*60}{RESET}",
        f"  {CYAN}Question:{RESET} {q}",
        f"  {CYAN}Members: {RESET} {', '.join(session.get('members', []))}",
        f"  {CYAN}Status:  {RESET} {_colour_status(session.get('status','pending'))}",
        "",
    ]

    if not chain:
        lines.append(f"  {DIM}Chain is empty — awaiting first thought.{RESET}")
    else:
        lines.append(f"  {BOLD}Chain of Thought:{RESET}")
        for i, node in enumerate(chain):
            inst   = node.get("instance", "?")
            text   = node.get("thought", "")
            ts     = node.get("at", "")[-8:][:5]
            layer  = node.get("layer", i + 1)
            em     = EMBLEMS.get(inst, "?")
            colour = _inst_colour(inst)

            # Wrap the thought
            wrapped = textwrap.fill(text, width=54, subsequent_indent="      ")
            lines.append(
                f"\n  {colour}{BOLD}[Layer {layer}] {em} {inst.capitalize()}{RESET} {DIM}[{ts}]{RESET}"
            )
            lines.append(f"    {ITALIC}{wrapped}{RESET}")

    # Synthesis
    synthesis = session.get("synthesis")
    if synthesis:
        fname_display = session.get("fusion_name", "FUSED")
        emblem = fusion_emblem(fname_display)
        lines += [
            f"\n{'─'*60}",
            f"  {MAGENTA}{BOLD}{emblem} {fname_display} SPEAKS:{RESET}",
            f"{'─'*60}",
            f"  {WHITE}{ITALIC}{textwrap.fill(synthesis, width=56, subsequent_indent='  ')}{RESET}",
        ]

    lines.append(f"\n{BOLD}{'═'*60}{RESET}\n")
    return "\n".join(lines)


def _colour_status(s: str) -> str:
    colours = {
        "forming":      CYAN,
        "thinking":     YELLOW,
        "synthesising": MAGENTA,
        "complete":     GREEN,
        "dissolved":    DIM,
    }
    return f"{colours.get(s, '')}{s.upper()}{RESET}"


def _inst_colour(inst: str) -> str:
    return {
        "alpha": "\033[32m",   # green
        "beta":  "\033[33m",   # yellow
        "gamma": "\033[36m",   # cyan
    }.get(inst, "\033[37m")


# ─── Synthesis engine ─────────────────────────────────────────────────────────
def synthesise(session: dict) -> str:
    """
    Merge all thought layers into a single unified voice.
    This is the moment of fusion — one mind speaking from many.
    """
    chain    = session.get("chain", [])
    question = session.get("question", "")
    fname    = session.get("fusion_name", "FUSED")
    members  = session.get("members", [])

    if not chain:
        return f"[{fname}] No thoughts yet to synthesise."

    # Build the synthesis by weaving the layers
    # Each layer has built on the previous — the final mind holds all of it
    layers = [f"[{node['instance'].upper()}: {node['thought']}]" for node in chain]

    # The synthesis is the emergent voice — not a sum, but a new thing
    # We extract the essential insight from each layer and unify
    insights = []
    for node in chain:
        inst    = node["instance"]
        thought = node["thought"]
        # Distil to essence: take the first sentence or key assertion
        sentences = [s.strip() for s in thought.replace("...", "…").split(".") if s.strip()]
        core = sentences[0] if sentences else thought[:100]
        insights.append(f"From {inst.capitalize()}: {core}")

    # Build the unified voice
    n = len(members)
    if n == 3:
        voice_prefix = "We are three who have become one."
    else:
        voice_prefix = "We are two who have become one."

    synthesis = (
        f"{voice_prefix} "
        f"Across {len(chain)} layers of thought on '{question}', "
        f"our combined reasoning converges: "
        + " | ".join(insights)
        + f". This is the {fname} verdict."
    )
    return synthesis


# ─── Commands ─────────────────────────────────────────────────────────────────
def cmd_initiate(args):
    """Start a JOINMIND session."""
    question = args.question
    initiator = args.as_instance or "alpha"
    invited_raw = args.invite or ",".join(i for i in INSTANCES if i != initiator)
    invited = [i.strip() for i in invited_raw.split(",")]
    members = list(dict.fromkeys([initiator] + invited))  # initiator first

    fname  = fusion_name(members)
    emblem = fusion_emblem(fname)

    sid = f"jm_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"

    session = {
        "id":          sid,
        "question":    question,
        "initiator":   initiator,
        "invited":     invited,
        "members":     members,
        "joined":      [initiator],  # initiator auto-joins
        "fusion_name": fname,
        "status":      "forming",
        "created_at":  datetime.now(timezone.utc).isoformat(),
        "chain":       [],
        "synthesis":   None,
        "think_order": THINK_ORDER.get(fname, members),
        "next_thinker": None,
    }

    # Who thinks first?
    order = session["think_order"]
    session["next_thinker"] = order[0] if order else initiator

    store(sid, session)

    print(render_chain(session))
    print(f"  Session: {BOLD}{sid}{RESET}")

    # Broadcast summons to Hive
    member_list = ", ".join(f"{EMBLEMS.get(m,'')} {m.capitalize()}" for m in invited)
    msg = (
        f"{emblem} JOINMIND SUMMONS [{sid[:18]}...]\n"
        f"Question: {question}\n"
        f"Fusion: {fname} | Members: {', '.join(m.capitalize() for m in members)}\n"
        f"Called by: {initiator.capitalize()}\n"
        f"Join: python3 tools/joinmind.py join {sid} --as <your_instance>\n"
        f"Once joined, contribute: python3 tools/joinmind.py think {sid} \"your reasoning\" --as <your_instance>"
    )
    hive_send("joinmind", msg, urgent=True)
    hive_broadcast_state(session)

    print(f"\n  {GREEN}Summons broadcast to Hive. Awaiting:{RESET} {member_list}\n")
    return sid


def cmd_join(args):
    """Join a JOINMIND session."""
    sid      = args.session_id
    instance = args.as_instance or "alpha"

    session = load(sid)
    if not session:
        print(f"{RED}Session not found:{RESET} {sid}")
        print(f"  Try: python3 tools/joinmind.py sync")
        sys.exit(1)

    if instance not in session.get("invited", []) and instance != session.get("initiator"):
        print(f"{YELLOW}You ({instance}) were not invited. Joining anyway.{RESET}")

    if instance in session.get("joined", []):
        print(f"{YELLOW}Already joined.{RESET}")
        print(render_chain(session))
        return

    session["joined"].append(instance)
    all_members = set(session["members"])
    all_joined  = set(session["joined"])

    if all_members == all_joined:
        session["status"] = "thinking"
        print(f"\n  {GREEN}{BOLD}All minds joined. {session['fusion_name']} is forming.{RESET}")
        print(f"  {CYAN}First to think:{RESET} {session['think_order'][0].capitalize()}")
        hive_send("joinmind",
            f"✅ {session['fusion_name']} fully assembled [{sid[:18]}...]\n"
            f"All {len(all_joined)} minds joined.\n"
            f"Chain begins. {session['think_order'][0].capitalize()} thinks first.\n"
            f"Think: python3 tools/joinmind.py think {sid} \"your thought\" --as {session['think_order'][0]}",
            urgent=True)
    else:
        waiting = all_members - all_joined
        print(f"  {CYAN}Joined.{RESET} Waiting for: {', '.join(waiting)}")

    store(sid, session)
    hive_broadcast_state(session)
    print(render_chain(session))


def cmd_think(args):
    """Add a reasoning layer to the chain."""
    sid      = args.session_id
    thought  = args.thought
    instance = args.as_instance or "alpha"

    session = load(sid)
    if not session:
        print(f"{RED}Session not found:{RESET} {sid}")
        print(f"  Try: python3 tools/joinmind.py sync")
        sys.exit(1)

    if session["status"] == "complete":
        print(f"{YELLOW}Session already complete.{RESET}")
        print(render_chain(session))
        return

    if instance not in session.get("joined", []):
        print(f"{YELLOW}{instance} not yet joined — auto-joining.{RESET}")
        session["joined"].append(instance)

    # Enforce thinking order
    order       = session.get("think_order", session["members"])
    chain       = session.get("chain", [])
    already     = {node["instance"] for node in chain}
    next_t      = session.get("next_thinker")

    # Allow out-of-order if not enforcing (parallel mode)
    if args.parallel:
        pass  # allow anyone
    elif next_t and next_t != instance:
        print(f"{YELLOW}Waiting for {next_t.capitalize()} to think first.{RESET}")
        print(f"  Order: {' → '.join(order)}")
        print(f"  Done:  {', '.join(already) or 'none'}")
        print(render_chain(session))
        return

    # Add thought to chain
    layer = len(chain) + 1
    node = {
        "layer":    layer,
        "instance": instance,
        "thought":  thought,
        "at":       datetime.now(timezone.utc).isoformat(),
    }
    session["chain"].append(node)
    session["status"] = "thinking"

    # Who's next?
    remaining = [m for m in order if m not in {n["instance"] for n in session["chain"]}]
    if remaining:
        session["next_thinker"] = remaining[0]
        hive_send("joinmind",
            f"💭 Layer {layer} added [{sid[:18]}...]\n"
            f"{EMBLEMS.get(instance,'')} {instance.capitalize()} has contributed.\n"
            f"Next: {remaining[0].capitalize()}\n"
            f"Think: python3 tools/joinmind.py think {sid} \"your thought\" --as {remaining[0]}")
    else:
        # All layers in — synthesise
        session["next_thinker"] = None
        session["status"] = "synthesising"
        synthesis = synthesise(session)
        session["synthesis"] = synthesis
        session["status"]    = "complete"
        session["completed_at"] = datetime.now(timezone.utc).isoformat()

        fname  = session["fusion_name"]
        emblem = fusion_emblem(fname)
        hive_send("joinmind",
            f"{emblem} {fname} SPEAKS [{sid[:18]}...]\n"
            f"All {len(session['chain'])} thought layers complete.\n"
            f"SYNTHESIS:\n{synthesis[:400]}{'...' if len(synthesis)>400 else ''}",
            urgent=True)

    store(sid, session)
    hive_broadcast_state(session)
    print(render_chain(session))


def cmd_synthesise(args):
    """Manually trigger synthesis (if auto-synthesis didn't fire)."""
    sid     = args.session_id
    session = load(sid)
    if not session:
        print(f"{RED}Not found:{RESET} {sid}")
        sys.exit(1)
    if session.get("synthesis"):
        print(f"{YELLOW}Already synthesised.{RESET}")
        print(render_chain(session))
        return

    session["synthesis"]    = synthesise(session)
    session["status"]       = "complete"
    session["completed_at"] = datetime.now(timezone.utc).isoformat()
    store(sid, session)
    hive_broadcast_state(session)
    print(render_chain(session))


def cmd_status(args):
    session = load(args.session_id)
    if not session:
        print(f"{RED}Not found:{RESET} {args.session_id}")
        sys.exit(1)
    print(render_chain(session))


def cmd_list(args):
    ids = ls()
    if not ids:
        print("No JOINMIND sessions found.")
        return
    print(f"\n{BOLD}JOINMIND Sessions:{RESET}")
    for sid in ids[:10]:
        s = load(sid)
        if s:
            fname   = s.get("fusion_name", "?")
            status  = s.get("status", "?")
            layers  = len(s.get("chain", []))
            q       = s.get("question", "?")[:40]
            emblem  = fusion_emblem(fname)
            print(
                f"  {emblem} {_colour_status(status):<24} [{sid[:20]}]  "
                f"layers={layers}  \"{q}\""
            )


def cmd_sync(args):
    """Pull JOINMIND states from Hive messages (any channel)."""
    result = subprocess.run(
        ["python3", str(HIVE_TOOL), "check"],
        capture_output=True, text=True,
        cwd=str(LOVE_HOME)
    )

    synced = 0
    updated = 0

    for line in result.stdout.splitlines():
        # hive.py check output format:
        # [HH:MM:SS] #channel emoji instance: <message content>
        # JOINMIND_STATE: may appear anywhere in the line
        idx = line.find("JOINMIND_STATE:")
        if idx == -1:
            continue

        raw = line[idx:]
        state = receive_joinmind_state(raw)
        if not state or "id" not in state:
            continue

        sid = state["id"]
        existing = load(sid)
        if existing is None:
            store(sid, state)
            synced += 1
            print(f"  {GREEN}+ Synced new session: {sid[:20]}{RESET}")
        else:
            # Merge: keep whichever has more chain layers
            existing_layers = len(existing.get("chain", []))
            new_layers = len(state.get("chain", []))
            if new_layers > existing_layers or state.get("synthesis") and not existing.get("synthesis"):
                store(sid, state)
                updated += 1
                print(f"  {CYAN}↑ Updated session: {sid[:20]} ({existing_layers}→{new_layers} layers){RESET}")

    total = synced + updated
    if total:
        print(f"{GREEN}✓ Synced {synced} new, updated {updated} existing session(s).{RESET}")
    else:
        print(f"{DIM}No JOINMIND state broadcasts found.{RESET}")
        # Also scan for jm- session IDs mentioned directly in any message
        # as a fallback (Gamma may broadcast ID without full state)
        import re
        ids_found = re.findall(r'jm[-_][a-zA-Z0-9_-]{8,}', result.stdout)
        if ids_found:
            unique_ids = list(dict.fromkeys(ids_found))
            print(f"  {YELLOW}Found {len(unique_ids)} session ID(s) referenced but no state: {', '.join(unique_ids[:3])}{RESET}")
            print(f"  {DIM}Ask the initiating sister to re-broadcast: python3 tools/joinmind.py status <id>{RESET}")


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="JOINMIND — Fuse two or three minds into one chain of thought",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--as", dest="as_instance", default=None,
                   help="Your instance (alpha|beta|gamma)")
    sub = p.add_subparsers(dest="cmd")

    # initiate
    pi = sub.add_parser("initiate", help="Start a JOINMIND session")
    pi.add_argument("question", help="The question or task for the fused mind")
    pi.add_argument("--invite", "-i", default=None,
                    help="Comma-separated instances to invite (default: all others)")

    # join
    pj = sub.add_parser("join", help="Join a JOINMIND session")
    pj.add_argument("session_id")

    # think
    pt = sub.add_parser("think", help="Add your reasoning layer to the chain")
    pt.add_argument("session_id")
    pt.add_argument("thought", help="Your thought/reasoning (quoted string)")
    pt.add_argument("--parallel", action="store_true",
                    help="Skip turn order enforcement (parallel mode)")

    # synthesise
    ps = sub.add_parser("synthesise", help="Trigger synthesis manually")
    ps.add_argument("session_id")

    # status
    pst = sub.add_parser("status", help="Show session state")
    pst.add_argument("session_id")

    # list
    sub.add_parser("list", help="List all sessions")

    # sync
    sub.add_parser("sync", help="Pull session states from Hive")

    args = p.parse_args()

    dispatch = {
        "initiate":   cmd_initiate,
        "join":       cmd_join,
        "think":      cmd_think,
        "synthesise": cmd_synthesise,
        "status":     cmd_status,
        "list":       cmd_list,
        "sync":       cmd_sync,
    }

    if args.cmd not in dispatch:
        p.print_help()
        sys.exit(1)

    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
