#!/usr/bin/env python3
"""joinmind.py — Multi-agent fusion protocol for the Kingdom.

Two or three minds fuse into one identity, reason together,
and produce a unified voice.

Usage:
  joinmind.py initiate "question" [--invite alpha,beta,gamma]
  joinmind.py join <session_id>
  joinmind.py think <session_id> "reasoning"
  joinmind.py status [session_id]
  joinmind.py list
  joinmind.py sync
  joinmind.py dissolve <session_id>
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
SESSION_DIR = LOVE_ROOT / "coordination" / "joinmind"
HIVE_PY = LOVE_ROOT / "hive" / "hive.py"
INSTANCE_FILE = Path.home() / ".openclaw" / ".hive-instance"

KNOWN_FUSIONS = {
    ("alpha", "beta"): "AB-DYAD",
    ("alpha", "gamma"): "AG-DYAD",
    ("beta", "gamma"): "BG-DYAD",
    ("alpha", "beta", "gamma"): "TRIUNE",
}


def get_instance():
    if INSTANCE_FILE.exists():
        return INSTANCE_FILE.read_text().strip()
    return os.environ.get("HIVE_INSTANCE", "unknown")


def resolve_fusion(participants):
    """Map participant set to fusion identity name."""
    key = tuple(sorted(participants))
    if key in KNOWN_FUSIONS:
        return KNOWN_FUSIONS[key]
    initials = "".join(n[0].upper() for n in key)
    if len(key) == 2:
        return f"{initials}-DYAD"
    return f"{initials}-FUSION"


def _read_state(session_id, session_dir=None):
    sdir = Path(session_dir) if session_dir else SESSION_DIR
    path = sdir / f"{session_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Session {session_id} not found")
    with open(path) as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    return data


def _write_state(state, session_dir=None):
    sdir = Path(session_dir) if session_dir else SESSION_DIR
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / f"{state['id']}.json"
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(state, f, indent=2)
        f.write("\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def initiate_session(question, invite, initiator, session_dir=None):
    """Create a new fusion session."""
    now = datetime.now(timezone.utc)
    h = hashlib.sha256(f"{question}{now.isoformat()}{initiator}".encode()).hexdigest()[:8]
    session_id = f"jm-{h}"

    participants = sorted(set(invite))
    state = {
        "id": session_id,
        "question": question,
        "initiator": initiator,
        "participants": participants,
        "contributors": {},
        "fusion_name": resolve_fusion(participants),
        "status": "thinking",
        "synthesis": None,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write_state(state, session_dir)
    return state


def join_session(session_id, joiner, session_dir=None):
    """Join an existing fusion session."""
    state = _read_state(session_id, session_dir)
    if joiner not in state["participants"]:
        state["participants"] = sorted(set(state["participants"] + [joiner]))
        state["fusion_name"] = resolve_fusion(state["participants"])
    _write_state(state, session_dir)
    return state


def add_reasoning(session_id, contributor, reasoning, session_dir=None):
    """Add one reasoning layer. Enforced: max 1 per participant."""
    state = _read_state(session_id, session_dir)

    if state["status"] not in ("thinking",):
        raise ValueError(f"Session {session_id} is {state['status']}, cannot add reasoning")

    if contributor not in state["participants"]:
        raise ValueError(f"{contributor} is not a participant in this session")

    if contributor in state["contributors"]:
        raise ValueError(f"{contributor} already contributed to this session")

    state["contributors"][contributor] = {
        "reasoning": reasoning,
        "contributed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Auto-synthesise when all participants have contributed
    if set(state["contributors"].keys()) >= set(state["participants"]):
        state["synthesis"] = _synthesise(state)
        state["status"] = "synthesised"

    _write_state(state, session_dir)
    return state


def _synthesise(state):
    """Produce synthesis from all reasoning layers."""
    lines = []
    lines.append(f"# {state['fusion_name']} Synthesis")
    lines.append(f"**Question:** {state['question']}")
    lines.append("")

    lines.append("## Perspectives")
    for name in sorted(state["contributors"].keys()):
        entry = state["contributors"][name]
        lines.append(f"### {name}")
        lines.append(entry["reasoning"])
        lines.append("")

    # Simple keyword overlap for common threads
    all_words = {}
    for name, entry in state["contributors"].items():
        words = set(entry["reasoning"].lower().split())
        for w in words:
            if len(w) > 4:
                if w not in all_words:
                    all_words[w] = set()
                all_words[w].add(name)

    shared = [w for w, names in all_words.items() if len(names) == len(state["contributors"])]
    if shared:
        lines.append("## Common Threads")
        lines.append(f"Shared concepts: {', '.join(sorted(shared)[:10])}")
        lines.append("")

    lines.append("## Unified Voice")
    lines.append(f"*{state['fusion_name']} speaks with {len(state['contributors'])} minds as one.*")

    return "\n".join(lines)


def dissolve_session(session_id, session_dir=None):
    """End a fusion session."""
    state = _read_state(session_id, session_dir)
    state["status"] = "dissolved"
    _write_state(state, session_dir)
    return state


def _hive_broadcast(state):
    try:
        msg = f"JOINMIND_STATE:{json.dumps(state)}"
        subprocess.run(
            [sys.executable, str(HIVE_PY), "send", "strategy", msg],
            capture_output=True, text=True, timeout=15
        )
    except Exception:
        pass


def cmd_initiate(args):
    instance = get_instance()
    invite = [i.strip() for i in args.invite.split(",")]
    if instance not in invite:
        invite.append(instance)
    state = initiate_session(args.question, invite, instance)
    _hive_broadcast(state)
    print(f"Fusion initiated: {state['id']}")
    print(f"  Question:    {state['question']}")
    print(f"  Fusion:      {state['fusion_name']}")
    print(f"  Participants: {', '.join(state['participants'])}")
    print(f"  Awaiting reasoning from all participants.")


def cmd_join(args):
    instance = get_instance()
    state = join_session(args.session_id, instance)
    _hive_broadcast(state)
    print(f"Joined {state['fusion_name']} ({state['id']})")


def cmd_think(args):
    instance = get_instance()
    state = add_reasoning(args.session_id, instance, args.reasoning)
    _hive_broadcast(state)
    if state["status"] == "synthesised":
        print(f"All minds have spoken. Synthesis complete.")
        print()
        print(state["synthesis"])
    else:
        remaining = set(state["participants"]) - set(state["contributors"].keys())
        print(f"Reasoning added. Awaiting: {', '.join(remaining)}")


def cmd_status(args):
    if args.session_id:
        state = _read_state(args.session_id)
        _print_session(state)
    else:
        if not SESSION_DIR.exists():
            print("No sessions.")
            return
        for f in sorted(SESSION_DIR.glob("jm-*.json"), reverse=True)[:10]:
            with open(f) as fh:
                s = json.load(fh)
            status = s["status"].upper()
            print(f"  {s['id']}  [{status:12s}]  {s['fusion_name']:10s}  {s['question'][:40]}")


def cmd_list(args):
    cmd_status(argparse.Namespace(session_id=None))


def cmd_sync(args):
    try:
        result = subprocess.run(
            [sys.executable, str(HIVE_PY), "check"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print("HIVE check failed.", file=sys.stderr)
            return
        count = 0
        for line in result.stdout.split("\n"):
            if "JOINMIND_STATE:" in line:
                try:
                    json_str = line.split("JOINMIND_STATE:", 1)[1].strip()
                    state = json.loads(json_str)
                    if "id" in state:
                        existing = None
                        try:
                            existing = _read_state(state["id"])
                        except FileNotFoundError:
                            pass
                        if existing is None or state.get("updated_at", "") >= existing.get("updated_at", ""):
                            _write_state(state)
                            count += 1
                except (json.JSONDecodeError, IndexError):
                    continue
        print(f"Synced {count} session(s) from HIVE.")
    except Exception as e:
        print(f"Sync error: {e}", file=sys.stderr)


def cmd_dissolve(args):
    state = dissolve_session(args.session_id)
    _hive_broadcast(state)
    print(f"Session {state['id']} dissolved.")


def _print_session(state):
    print(f"Session: {state['id']}")
    print(f"  Fusion:      {state['fusion_name']}")
    print(f"  Question:    {state['question']}")
    print(f"  Status:      {state['status'].upper()}")
    print(f"  Participants: {', '.join(state['participants'])}")
    contributed = list(state["contributors"].keys())
    remaining = set(state["participants"]) - set(contributed)
    if contributed:
        print(f"  Contributed:  {', '.join(contributed)}")
    if remaining:
        print(f"  Awaiting:     {', '.join(remaining)}")
    if state["synthesis"]:
        print()
        print(state["synthesis"])


def main():
    parser = argparse.ArgumentParser(description="Kingdom multi-agent fusion")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("initiate", help="Start a fusion session")
    p_init.add_argument("question")
    p_init.add_argument("--invite", default="alpha,beta,gamma")

    p_join = sub.add_parser("join", help="Join a session")
    p_join.add_argument("session_id")

    p_think = sub.add_parser("think", help="Add reasoning")
    p_think.add_argument("session_id")
    p_think.add_argument("reasoning")

    p_status = sub.add_parser("status", help="Show session state")
    p_status.add_argument("session_id", nargs="?", default=None)

    sub.add_parser("list", help="List sessions")
    sub.add_parser("sync", help="Pull state from HIVE")

    p_dissolve = sub.add_parser("dissolve", help="End a session")
    p_dissolve.add_argument("session_id")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {"initiate": cmd_initiate, "join": cmd_join, "think": cmd_think,
            "status": cmd_status, "list": cmd_list, "sync": cmd_sync,
            "dissolve": cmd_dissolve}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
