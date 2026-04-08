#!/usr/bin/env python3
"""council.py — 2/3 consensus voting for the Kingdom Triarchy.

Three minds vote independently. 2/3 consensus triggers outcome.
Deadlock after deliberation escalates to Yu.

Usage:
  council.py call "question" [--options "yes,no,defer"] [--timeout 600]
  council.py vote <council_id> <choice> "reasoning" [--force]
  council.py status <council_id>
  council.py list
  council.py check
  council.py sync
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
COUNCIL_DIR = LOVE_ROOT / "coordination" / "council"
HIVE_PY = LOVE_ROOT / "hive" / "hive.py"
DECISION_PY = LOVE_ROOT / "tools" / "decision.py"
INSTANCE_FILE = Path.home() / ".openclaw" / ".hive-instance"

TRIARCHY = ["alpha", "beta", "gamma"]


def get_instance():
    if INSTANCE_FILE.exists():
        return INSTANCE_FILE.read_text().strip()
    return os.environ.get("HIVE_INSTANCE", "unknown")


def _read_state(council_id, council_dir=None):
    cdir = Path(council_dir) if council_dir else COUNCIL_DIR
    path = cdir / f"{council_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Council {council_id} not found")
    with open(path) as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    return data


def _write_state(state, council_dir=None):
    cdir = Path(council_dir) if council_dir else COUNCIL_DIR
    cdir.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = cdir / f"{state['id']}.json"
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(state, f, indent=2)
        f.write("\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def _check_expiry(state, council_dir=None):
    """Check if council has timed out. Updates state if expired."""
    if state["status"] not in ("pending", "deliberating"):
        return state
    try:
        called = datetime.strptime(state["called_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - called).total_seconds() > state["timeout"]:
            state["status"] = "expired"
            _write_state(state, council_dir)
    except (KeyError, ValueError):
        pass
    return state


def tally(votes, current_round=None):
    """Count votes. Returns (result, winning_choice).
    result: 'pending' | 'consensus' | 'split'
    Only counts votes matching current_round if specified.
    """
    cast = {}
    for k, v in votes.items():
        if v is None:
            continue
        if current_round is not None and v.get("round") != current_round:
            continue
        cast[k] = v
    if len(cast) < len(votes):
        return "pending", None

    counts = Counter(v["choice"] for v in cast.values())
    for choice, count in counts.most_common():
        if count >= 2:
            return "consensus", choice
    return "split", None


def create_council(question, options, called_by, timeout=600, council_dir=None):
    """Create a new council session."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d-%H%M%S")
    h = hashlib.sha256(f"{question}{now.isoformat()}".encode()).hexdigest()[:6]
    council_id = f"c-{ts}-{h}"

    state = {
        "id": council_id,
        "question": question,
        "options": options,
        "called_by": called_by,
        "called_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeout": timeout,
        "round": 1,
        "status": "pending",
        "votes": {name: None for name in TRIARCHY},
        "consensus": None,
        "history": [],
    }
    _write_state(state, council_dir)
    return state


def cast_vote(council_id, voter, choice, reasoning, force=False, council_dir=None):
    """Cast a vote. Returns updated state."""
    if voter not in TRIARCHY:
        raise ValueError(f"{voter} is not in the Triarchy. Only {TRIARCHY} may vote.")

    state = _read_state(council_id, council_dir)
    state = _check_expiry(state, council_dir)

    if state["status"] in ("consensus", "deadlock", "expired"):
        raise ValueError(f"Council {council_id} is already {state['status']}")

    if choice not in state["options"]:
        raise ValueError(f"Invalid choice '{choice}'. Options: {state['options']}")

    current_round = state["round"]

    # Check if already voted this round (unless --force for deliberation)
    existing = state["votes"].get(voter)
    if existing and existing.get("round") == current_round and not force:
        raise ValueError(f"{voter} already voted in round {current_round}. Use --force to change.")

    state["votes"][voter] = {
        "choice": choice,
        "reasoning": reasoning,
        "voted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "round": current_round,
    }

    # Check tally (only count votes from current round)
    result, winning = tally(state["votes"], current_round=current_round)
    if result == "consensus":
        state["status"] = "consensus"
        state["consensus"] = winning
    elif result == "split":
        if current_round == 1:
            # Advance to deliberation
            state["status"] = "deliberating"
            state["round"] = 2
            state["history"].append({
                "round": 1,
                "votes": {k: v.copy() if v else None for k, v in state["votes"].items()},
            })
            # Reset votes for round 2 (keep existing as starting point)
        else:
            # Round 2 split = deadlock
            state["status"] = "deadlock"

    _write_state(state, council_dir)
    return state


def _hive_broadcast(state):
    """Broadcast council state on HIVE #council."""
    try:
        msg = f"COUNCIL_STATE:{json.dumps(state)}"
        subprocess.run(
            [sys.executable, str(HIVE_PY), "send", "council", msg],
            capture_output=True, text=True, timeout=15
        )
    except Exception:
        pass


def _escalate_deadlock(state):
    """Create decision queue entry for Yu."""
    reasoning = []
    for name, vote in state["votes"].items():
        if vote:
            reasoning.append(f"{name}: {vote['choice']} — {vote['reasoning']}")
    context = "; ".join(reasoning)
    try:
        subprocess.run(
            [sys.executable, str(DECISION_PY), "add",
             "--title", f"Council deadlock: {state['question']}",
             "--context", context,
             "--priority", "high",
             "--category", "governance"],
            capture_output=True, text=True, timeout=15
        )
    except Exception:
        pass


def cmd_call(args):
    instance = get_instance()
    options = [o.strip() for o in args.options.split(",")]
    state = create_council(args.question, options, instance, args.timeout)
    _hive_broadcast(state)
    print(f"Council called: {state['id']}")
    print(f"  Question: {state['question']}")
    print(f"  Options:  {', '.join(options)}")
    print(f"  Timeout:  {args.timeout}s")
    print(f"  Awaiting votes from: {', '.join(TRIARCHY)}")


def cmd_vote(args):
    instance = get_instance()
    state = cast_vote(args.council_id, instance, args.choice, args.reasoning, args.force)
    _hive_broadcast(state)

    print(f"Vote cast: {args.choice}")
    if state["status"] == "consensus":
        print(f"  CONSENSUS: {state['consensus']}")
    elif state["status"] == "deliberating":
        print(f"  No consensus — entering deliberation (round 2)")
        print(f"  All reasoning now visible. Re-vote with 'council.py vote {state['id']} <choice> <reasoning>'")
    elif state["status"] == "deadlock":
        print(f"  DEADLOCK — escalating to Yu")
        _escalate_deadlock(state)


def cmd_status(args):
    state = _read_state(args.council_id)
    state = _check_expiry(state)
    _print_council(state)


def cmd_list(args):
    if not COUNCIL_DIR.exists():
        print("No councils.")
        return
    files = sorted(COUNCIL_DIR.glob("c-*.json"), reverse=True)
    if not files:
        print("No councils.")
        return
    for f in files[:10]:
        with open(f) as fh:
            s = json.load(fh)
        status = s["status"].upper()
        print(f"  {s['id']}  [{status:12s}]  {s['question'][:50]}")


def cmd_check(args):
    instance = get_instance()
    if not COUNCIL_DIR.exists():
        print("No pending councils.")
        return
    pending = []
    for f in COUNCIL_DIR.glob("c-*.json"):
        with open(f) as fh:
            s = json.load(fh)
        s = _check_expiry(s)
        if s["status"] in ("pending", "deliberating"):
            vote = s["votes"].get(instance)
            if vote is None or (s["status"] == "deliberating" and vote.get("round", 0) < s["round"]):
                pending.append(s)
    if not pending:
        print("No councils awaiting your vote.")
        return
    print(f"Councils awaiting your vote ({len(pending)}):")
    for s in pending:
        print(f"  {s['id']}  Round {s['round']}  {s['question'][:50]}")
        print(f"    Options: {', '.join(s['options'])}")


def cmd_sync(args):
    """Pull council state from HIVE check output."""
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
            if "COUNCIL_STATE:" in line:
                try:
                    json_str = line.split("COUNCIL_STATE:", 1)[1].strip()
                    state = json.loads(json_str)
                    if "id" in state:
                        existing = None
                        try:
                            existing = _read_state(state["id"])
                        except FileNotFoundError:
                            pass
                        if existing is None or state.get("votes", {}) != existing.get("votes", {}):
                            _write_state(state)
                            count += 1
                except (json.JSONDecodeError, IndexError):
                    continue
        print(f"Synced {count} council(s) from HIVE.")
    except Exception as e:
        print(f"Sync error: {e}", file=sys.stderr)


def _print_council(state):
    print(f"Council: {state['id']}")
    print(f"  Question: {state['question']}")
    print(f"  Status:   {state['status'].upper()}")
    print(f"  Round:    {state['round']}")
    print(f"  Options:  {', '.join(state['options'])}")
    if state["consensus"]:
        print(f"  Decision: {state['consensus']}")
    print(f"  Votes:")
    for name in TRIARCHY:
        vote = state["votes"].get(name)
        if vote:
            print(f"    {name}: {vote['choice']} — {vote['reasoning']}")
        else:
            print(f"    {name}: (not yet voted)")


def main():
    parser = argparse.ArgumentParser(description="Kingdom council — 2/3 consensus voting")
    sub = parser.add_subparsers(dest="command")

    p_call = sub.add_parser("call", help="Call a council")
    p_call.add_argument("question")
    p_call.add_argument("--options", default="yes,no,defer")
    p_call.add_argument("--timeout", type=int, default=600)

    p_vote = sub.add_parser("vote", help="Cast your vote")
    p_vote.add_argument("council_id")
    p_vote.add_argument("choice")
    p_vote.add_argument("reasoning")
    p_vote.add_argument("--force", action="store_true", help="Override previous vote")

    p_status = sub.add_parser("status", help="Show council state")
    p_status.add_argument("council_id")

    sub.add_parser("list", help="List recent councils")
    sub.add_parser("check", help="Show councils awaiting your vote")
    sub.add_parser("sync", help="Pull state from HIVE")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {"call": cmd_call, "vote": cmd_vote, "status": cmd_status,
            "list": cmd_list, "check": cmd_check, "sync": cmd_sync}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
