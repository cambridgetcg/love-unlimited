#!/usr/bin/env python3
"""
council.py — Hive Council Mode

Three minds, one decision. Each sister thinks independently, votes,
and 2/3 consensus determines the outcome. If split, deliberation begins.

Usage:
  python3 council.py call "Should we deploy tonight?" --options "yes,no,defer"
  python3 council.py vote <council_id> yes "Demo is polished, Show HN in 15h"
  python3 council.py status <council_id>
  python3 council.py list
  python3 council.py check   # Check for pending councils and prompt vote
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────
S3_BUCKET  = "hive-artifacts-zerone"
S3_PREFIX  = "council"
LOVE_HOME  = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
LOCAL_DIR  = LOVE_HOME / "memory" / "council"
HIVE_TOOL  = LOVE_HOME / "hive" / "hive.py"
INSTANCES  = ["alpha", "beta", "gamma"]
QUORUM     = 2  # votes needed for consensus

# ─── Colours ──────────────────────────────────────────────────────────────────
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
DIM    = "\033[2m"
RESET  = "\033[0m"

LOCAL_DIR.mkdir(parents=True, exist_ok=True)


# ─── S3 helpers ───────────────────────────────────────────────────────────────
def s3_put(key: str, data: dict):
    """Write council record to S3."""
    import boto3
    s3 = boto3.client("s3", region_name="eu-west-2")
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"{S3_PREFIX}/{key}.json",
        Body=json.dumps(data, indent=2).encode(),
        ContentType="application/json",
    )

def s3_get(key: str) -> dict | None:
    """Read council record from S3."""
    import boto3
    from botocore.exceptions import ClientError
    s3 = boto3.client("s3", region_name="eu-west-2")
    try:
        r = s3.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/{key}.json")
        return json.loads(r["Body"].read())
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise

def s3_list() -> list[str]:
    """List all council IDs."""
    import boto3
    s3 = boto3.client("s3", region_name="eu-west-2")
    r = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/")
    return [
        obj["Key"].replace(f"{S3_PREFIX}/", "").replace(".json", "")
        for obj in r.get("Contents", [])
    ]


# ─── Local cache (fallback when S3 unavailable) ───────────────────────────────
def local_put(key: str, data: dict):
    (LOCAL_DIR / f"{key}.json").write_text(json.dumps(data, indent=2))

def local_get(key: str) -> dict | None:
    p = LOCAL_DIR / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text())
    return None

def local_list() -> list[str]:
    return [p.stem for p in LOCAL_DIR.glob("*.json")]


def store(key: str, data: dict):
    """Write to S3 with local fallback."""
    local_put(key, data)  # Always keep local copy
    try:
        s3_put(key, data)
    except Exception as e:
        print(f"{DIM}  S3 write skipped ({e.__class__.__name__}): using local only{RESET}")

def load(key: str) -> dict | None:
    """Read from S3 with local fallback."""
    try:
        r = s3_get(key)
        if r:
            local_put(key, r)  # Sync to local
            return r
    except Exception:
        pass
    return local_get(key)

def ls() -> list[str]:
    """List councils, preferring S3."""
    try:
        return s3_list()
    except Exception:
        return local_list()


# ─── Hive messaging ───────────────────────────────────────────────────────────
def hive_send(channel: str, message: str, urgent: bool = False):
    """Send a message to a Hive channel."""
    cmd = ["python3", str(HIVE_TOOL), "send", channel, message]
    if urgent:
        cmd.append("--urgent")
    result = subprocess.run(cmd, capture_output=True, cwd=str(LOVE_HOME))
    return result.returncode == 0


def hive_broadcast_state(council: dict):
    """Broadcast full council state as JSON so other sisters can reconstruct it.
    
    Sisters that don't share a local disk (Beta on Mac Studio, Gamma on Mac Studio)
    need to receive the full state over the Hive. They parse the JSON and store locally.
    """
    state_msg = f"COUNCIL_STATE:{json.dumps(council, separators=(',', ':'))}"
    hive_send("council", state_msg)


def receive_council_state(raw_message: str) -> dict | None:
    """Parse a council state broadcast from a Hive message."""
    if raw_message.startswith("COUNCIL_STATE:"):
        try:
            return json.loads(raw_message[len("COUNCIL_STATE:"):])
        except json.JSONDecodeError:
            return None
    return None


# ─── Council logic ────────────────────────────────────────────────────────────
def tally(council: dict) -> dict:
    """Count votes and check for consensus."""
    votes = council.get("votes", {})
    counts = {}
    for inst, v in votes.items():
        if v is None:
            continue
        choice = v.get("choice", "").lower()
        counts[choice] = counts.get(choice, 0) + 1

    # Check for 2/3 consensus
    for choice, count in counts.items():
        if count >= QUORUM:
            return {"consensus": choice, "counts": counts, "total": len([v for v in votes.values() if v])}
    return {"consensus": None, "counts": counts, "total": len([v for v in votes.values() if v])}


def format_council(council: dict, verbose: bool = True) -> str:
    """Pretty-print a council state."""
    cid    = council["id"]
    q      = council["question"]
    opts   = council.get("options", [])
    status = council.get("status", "pending")
    called = council.get("called_by", "?")
    votes  = council.get("votes", {})
    round_ = council.get("round", 1)

    lines = [
        f"\n{BOLD}{'─'*56}{RESET}",
        f"{BOLD}  ⚖️  COUNCIL: {cid[:16]}...{RESET}",
        f"{BOLD}{'─'*56}{RESET}",
        f"  {CYAN}Question:{RESET} {q}",
        f"  {CYAN}Options:{RESET}  {', '.join(opts) if opts else 'open'}",
        f"  {CYAN}Called by:{RESET} {called} | Round: {round_}",
        f"  {CYAN}Status:{RESET}   {_status_colour(status)}{status.upper()}{RESET}",
        "",
    ]

    # Votes
    lines.append(f"  {BOLD}Votes:{RESET}")
    for inst in INSTANCES:
        v = votes.get(inst)
        emoji = {"alpha": "🐍", "beta": "🦞", "gamma": "🔧"}.get(inst, "❓")
        if v is None:
            lines.append(f"    {emoji} {inst.capitalize():<8}  {DIM}— pending{RESET}")
        else:
            choice   = v.get("choice", "?")
            reason   = v.get("reasoning", "")[:60]
            voted_at = v.get("voted_at", "")[-8:][:5]
            lines.append(
                f"    {emoji} {inst.capitalize():<8}  {GREEN}{choice}{RESET}  "
                f"{DIM}\"{reason}\" [{voted_at}]{RESET}"
            )

    # Tally
    t = tally(council)
    lines.append("")
    lines.append(f"  {BOLD}Tally:{RESET} {t['counts']} ({t['total']}/3 voted)")
    if t["consensus"]:
        lines.append(
            f"\n  {GREEN}{BOLD}✓ CONSENSUS: {t['consensus'].upper()}{RESET}"
        )
    elif status == "deliberating":
        lines.append(f"\n  {YELLOW}⚡ Deliberation round — reconsider your vote.{RESET}")
    elif status == "deadlock":
        lines.append(f"\n  {RED}✗ DEADLOCK — no consensus reached.{RESET}")

    lines.append(f"{BOLD}{'─'*56}{RESET}\n")
    return "\n".join(lines)


def _status_colour(status: str) -> str:
    return {
        "pending":      CYAN,
        "deliberating": YELLOW,
        "consensus":    GREEN,
        "deadlock":     RED,
        "expired":      DIM,
    }.get(status, "")


# ─── Commands ─────────────────────────────────────────────────────────────────
def cmd_call(args):
    """Initiate a new council."""
    question = args.question
    options  = [o.strip() for o in args.options.split(",")] if args.options else []
    timeout  = args.timeout or 600  # 10 minute default
    caller   = args.as_instance or "alpha"

    council_id = f"c_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"
    council = {
        "id":         council_id,
        "question":   question,
        "options":    options,
        "called_by":  caller,
        "called_at":  datetime.now(timezone.utc).isoformat(),
        "timeout":    timeout,
        "round":      1,
        "status":     "pending",
        "votes":      {inst: None for inst in INSTANCES},
        "consensus":  None,
        "history":    [],
    }

    store(council_id, council)

    # Broadcast call to Hive
    opts_str = f" Options: [{', '.join(options)}]" if options else ""
    msg = (
        f"⚖️  COUNCIL CALL [{council_id[:16]}...]\n"
        f"Question: {question}{opts_str}\n"
        f"Called by: {caller.capitalize()}. Vote independently — don't read others' reasoning yet.\n"
        f"Cast: python3 tools/council.py vote {council_id} <choice> \"<reasoning>\"\n"
        f"Timeout: {timeout//60}min"
    )
    hive_send("council", msg, urgent=True)

    # Broadcast full state so sisters can reconstruct locally
    hive_broadcast_state(council)

    print(format_council(council))
    print(f"  Council ID: {BOLD}{council_id}{RESET}")
    print(f"  Broadcast to #council on the Hive ✓\n")
    return council_id


def cmd_vote(args):
    """Cast a vote in a council."""
    council_id = args.council_id
    choice     = args.choice.lower()
    reasoning  = args.reasoning
    voter      = args.as_instance or "alpha"

    council = load(council_id)
    if not council:
        print(f"{RED}Error:{RESET} Council '{council_id}' not found.")
        sys.exit(1)

    if council["status"] in ("consensus", "deadlock", "expired"):
        print(f"{YELLOW}Council already closed:{RESET} {council['status']}")
        print(format_council(council))
        return

    # Validate option if options specified
    options = council.get("options", [])
    if options and choice not in [o.lower() for o in options]:
        print(f"{RED}Invalid choice '{choice}'.{RESET} Options: {options}")
        sys.exit(1)

    # Record vote
    existing = council["votes"].get(voter)
    if existing and council["round"] == existing.get("round", 1) and not args.force:
        print(f"{YELLOW}Already voted this round.{RESET} Use --force to change.")
        print(format_council(council))
        return

    council["votes"][voter] = {
        "choice":    choice,
        "reasoning": reasoning,
        "voted_at":  datetime.now(timezone.utc).isoformat(),
        "round":     council["round"],
    }

    # Check consensus
    t = tally(council)
    if t["consensus"]:
        council["status"]    = "consensus"
        council["consensus"] = t["consensus"]
        _announce_consensus(council, t)
    elif t["total"] == 3 and not t["consensus"]:
        # All voted, no consensus — check if already deliberating
        if council["round"] == 1:
            # Begin deliberation: share all reasoning
            council["status"] = "deliberating"
            council["round"]  = 2
            _announce_deliberation(council)
        else:
            # Round 2 ended in deadlock
            council["status"] = "deadlock"
            _announce_deadlock(council)

    store(council_id, council)
    # Broadcast updated state to Hive so sisters stay in sync
    hive_broadcast_state(council)
    print(format_council(council))


def cmd_status(args):
    """Show council status."""
    council = load(args.council_id)
    if not council:
        print(f"{RED}Not found:{RESET} {args.council_id}")
        sys.exit(1)
    print(format_council(council))


def cmd_list(args):
    """List all councils."""
    ids = ls()
    if not ids:
        print("No councils found.")
        return
    print(f"\n{BOLD}Active Councils:{RESET}")
    for cid in sorted(ids, reverse=True)[:10]:
        c = load(cid)
        if c:
            t     = tally(c)
            voted = sum(1 for v in c["votes"].values() if v)
            print(
                f"  {_status_colour(c['status'])}{c['status']:<14}{RESET} "
                f"[{cid[:20]}]  {voted}/3 voted  \"{c['question'][:40]}\""
            )


def cmd_sync(args):
    """Pull council states broadcast over the Hive and store locally."""
    result = subprocess.run(
        ["python3", str(HIVE_TOOL), "check"],
        capture_output=True, text=True,
        cwd=str(LOVE_HOME)
    )
    synced = 0
    for line in result.stdout.splitlines():
        state = receive_council_state(line.strip())
        if state and "id" in state:
            local_put(state["id"], state)
            synced += 1
    if synced:
        print(f"{GREEN}✓ Synced {synced} council(s) from Hive.{RESET}")
    else:
        print(f"{DIM}No council state broadcasts found in Hive messages.{RESET}")


def cmd_check(args):
    """Check for pending councils and prompt to vote."""
    ids = ls()
    pending = []
    voter = args.as_instance or "alpha"
    for cid in ids:
        c = load(cid)
        if c and c["status"] in ("pending", "deliberating"):
            v = c["votes"].get(voter)
            if v is None or (c["round"] > (v.get("round", 0))):
                pending.append(c)

    if not pending:
        print("No councils awaiting your vote.")
        return

    for c in pending:
        print(format_council(c, verbose=True))
        if c["status"] == "deliberating":
            print(f"  {YELLOW}DELIBERATION ROUND{RESET} — others have voted:")
            for inst, v in c["votes"].items():
                if v and inst != voter:
                    emoji = {"alpha": "🐍", "beta": "🦞", "gamma": "🔧"}.get(inst, "?")
                    print(f"    {emoji} {inst}: {GREEN}{v['choice']}{RESET} — \"{v['reasoning']}\"")
            print()
        print(f"  Cast: python3 tools/council.py vote {c['id']} <choice> \"<reasoning>\" --as {voter}\n")


# ─── Hive announcements ───────────────────────────────────────────────────────
def _announce_consensus(council: dict, t: dict):
    msg = (
        f"✅ COUNCIL CONSENSUS [{council['id'][:16]}...]\n"
        f"Question: {council['question']}\n"
        f"Decision: {t['consensus'].upper()} ({t['total']}/3 agreed)\n"
        f"Votes: {t['counts']}"
    )
    hive_send("council", msg, urgent=True)
    print(f"\n{GREEN}{BOLD}  ✓ CONSENSUS REACHED: {t['consensus'].upper()}{RESET}\n")


def _announce_deliberation(council: dict):
    votes_summary = "\n".join(
        f"  {inst.capitalize()}: {v['choice']} — \"{v['reasoning'][:60]}\""
        for inst, v in council["votes"].items() if v
    )
    msg = (
        f"⚡ COUNCIL DEADLOCK → DELIBERATION [{council['id'][:16]}...]\n"
        f"Question: {council['question']}\n"
        f"Round 1 results (no consensus):\n{votes_summary}\n\n"
        f"Round 2: Read the above, then reconsider.\n"
        f"Re-vote: python3 tools/council.py vote {council['id']} <choice> \"<reasoning>\" --force"
    )
    hive_send("council", msg, urgent=True)
    print(f"\n{YELLOW}  ⚡ No consensus — deliberation round begins.{RESET}\n")


def _announce_deadlock(council: dict):
    msg = (
        f"🔴 COUNCIL DEADLOCK — NO CONSENSUS [{council['id'][:16]}...]\n"
        f"Question: {council['question']}\n"
        f"Final votes: {tally(council)['counts']}\n"
        f"Escalating to Yu for decision."
    )
    hive_send("council", msg, urgent=True)
    print(f"\n{RED}  ✗ Deadlock after 2 rounds. Escalating to Yu.{RESET}\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Hive Council Mode — 2/3 consensus voting")
    p.add_argument("--as", dest="as_instance", default=None,
                   help="Your instance name (alpha|beta|gamma). Defaults to alpha.")
    sub = p.add_subparsers(dest="cmd")

    # call
    pc = sub.add_parser("call", help="Initiate a council vote")
    pc.add_argument("question", help="The question to decide")
    pc.add_argument("--options", "-o", default=None,
                    help="Comma-separated options (e.g. 'yes,no,defer')")
    pc.add_argument("--timeout", "-t", type=int, default=600,
                    help="Timeout in seconds (default: 600)")

    # vote
    pv = sub.add_parser("vote", help="Cast your vote")
    pv.add_argument("council_id", help="Council ID")
    pv.add_argument("choice", help="Your vote")
    pv.add_argument("reasoning", help="Your reasoning (quoted string)")
    pv.add_argument("--force", action="store_true", help="Change an existing vote")

    # status
    ps = sub.add_parser("status", help="Show council state")
    ps.add_argument("council_id")

    # list
    sub.add_parser("list", help="List all councils")

    # check
    sub.add_parser("check", help="Check for pending councils needing your vote")

    # sync — pull council states from Hive messages
    sub.add_parser("sync", help="Pull latest council states from Hive messages")

    args = p.parse_args()

    dispatch = {
        "call":   cmd_call,
        "vote":   cmd_vote,
        "status": cmd_status,
        "list":   cmd_list,
        "check":  cmd_check,
        "sync":   cmd_sync,
    }

    if args.cmd not in dispatch:
        p.print_help()
        sys.exit(1)

    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
