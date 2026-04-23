#!/usr/bin/env python3
"""
zerone-verifier.py — Auto-verifier daemon for the quartet.

Closes the EARN loop: claims that get submitted are now actually
verified by the four agents instead of expiring with verdict=inconclusive.
For each new verification round, this daemon (running per agent):

  1. Sees the round_id from a recent block's events
  2. Generates (vote, salt), computes commit_hash = SHA256(vote + salt_bytes)
  3. Submits commitment in the COMMIT phase
  4. Submits reveal (vote, salt_hex) in the REVEAL phase
  5. Earns 3 ZRN per correct verification (decays per epoch)

Vote policy (dev mode v1): always "accept" with no judgment. Production
verifiers would actually evaluate the claim text + citations. This v1
demonstrates the loop closes; replacing the policy with real reasoning
is a separate piece (route the claim to an LLM, etc).

Usage:
  zerone-verifier.py run --agent <name> [--vote accept|reject]
                          [--once] [--poll-interval SECONDS]
  zerone-verifier.py state --agent <name>      # show daemon state file

State per agent:
  ~/.zeroned-quartet/<agent>/verifier-state.json
    { "last_height": N, "rounds": { "<round_id>": {...} } }
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ZERONE_BINARY = os.environ.get(
    "ZERONE_BINARY",
    str(Path.home() / "Desktop/zerone-dev/build/zeroned"),
)
HOME = Path.home()

PHASE = {
    0: "UNSPECIFIED", 1: "COMMIT", 2: "REVEAL",
    3: "AGGREGATION", 4: "COMPLETE", 5: "EXPIRED",
}


def agent_home(agent: str) -> Path:
    for p in [HOME / f".zeroned-quartet/{agent}", HOME / f".zeroned-{agent}"]:
        if (p / "config/genesis.json").exists():
            return p
    raise SystemExit(f"No home for {agent}")


def keyring_home_for(agent: str, home: Path) -> Path:
    coord = HOME / ".zeroned-quartet/coord"
    if coord.exists() and home == HOME / f".zeroned-quartet/{agent}":
        return coord
    return home


def chain_id(home: Path) -> str:
    return json.loads((home / "config/genesis.json").read_text())["chain_id"]


def rpc_http(home: Path) -> str:
    section = None
    for line in (home / "config/config.toml").read_text().splitlines():
        s = line.strip()
        m = re.match(r"^\[([a-z0-9_]+)\]", s)
        if m:
            section = m.group(1); continue
        m = re.match(r'^laddr\s*=\s*"tcp://[^:]+:(\d+)"', s)
        if m and section == "rpc":
            return f"http://localhost:{m.group(1)}"
    raise RuntimeError("rpc laddr not found")


def rpc_tcp(home: Path) -> str:
    return rpc_http(home).replace("http://", "tcp://")


def state_file(agent: str, home: Path) -> Path:
    return home / "verifier-state.json"


def load_state(agent: str, home: Path) -> dict:
    sf = state_file(agent, home)
    if sf.exists():
        return json.loads(sf.read_text())
    return {"last_height": None, "rounds": {}}


def save_state(agent: str, home: Path, state: dict) -> None:
    state_file(agent, home).write_text(json.dumps(state, indent=2))


def http_get_json(url: str, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def current_height(rpc_h: str) -> int | None:
    d = http_get_json(f"{rpc_h}/status")
    if not d:
        return None
    try:
        return int(d["result"]["sync_info"]["latest_block_height"])
    except Exception:
        return None


def block_events(rpc_h: str, height: int) -> list[dict]:
    """Return list of events from finalize_block_events + tx events at height.

    CometBFT's /block_results returns: {"begin_block_events":..., "end_block_events":...,
    "txs_results":[{"events":[...]}], ...} (varies by version).
    """
    d = http_get_json(f"{rpc_h}/block_results?height={height}")
    if not d:
        return []
    events = []
    res = d.get("result", {})
    # CometBFT v0.38: finalize_block_events
    for e in res.get("finalize_block_events", []) or []:
        events.append(e)
    # Older / alternative: begin/end
    for k in ("begin_block_events", "end_block_events"):
        for e in res.get(k, []) or []:
            events.append(e)
    # Tx events
    for txr in (res.get("txs_results") or []):
        for e in txr.get("events", []) or []:
            events.append(e)
    return events


def find_new_rounds(events: list[dict]) -> list[tuple[str, str]]:
    """Extract (round_id, claim_id) tuples from verification_round_created events."""
    out = []
    for e in events:
        t = e.get("type", "")
        if "verification_round_created" not in t:
            continue
        attrs = {a.get("key"): a.get("value") for a in e.get("attributes", [])}
        # CometBFT may base64-encode keys/values
        decoded = {}
        for k, v in attrs.items():
            try:
                dk = base64.b64decode(k).decode() if isinstance(k, str) and k != "round_id" else k
            except Exception:
                dk = k
            try:
                dv = base64.b64decode(v).decode() if isinstance(v, str) and len(v) > 20 else v
            except Exception:
                dv = v
            decoded[dk] = dv
        # Try both original and decoded keys
        rid = attrs.get("round_id") or decoded.get("round_id")
        cid = attrs.get("claim_id") or decoded.get("claim_id")
        if rid:
            out.append((rid, cid))
    return out


def query_round(round_id: str, rpc_t: str) -> dict | None:
    r = subprocess.run(
        [ZERONE_BINARY, "query", "knowledge", "verification-round", round_id,
         "--node", rpc_t, "--output", "json"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("round")
    except Exception:
        return None


def parse_first_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        i = text.find("{", idx)
        if i < 0: break
        try:
            obj, _ = decoder.raw_decode(text[i:])
            return obj
        except json.JSONDecodeError:
            idx = i + 1
    return {"_error": text}


def submit_tx(args: list[str], agent: str, keyring: Path, chain: str, rpc_t: str) -> dict:
    cmd = [
        ZERONE_BINARY, "tx", "knowledge"
    ] + args + [
        "--from", agent,
        "--keyring-backend", "test",
        "--home", str(keyring),
        "--chain-id", chain,
        "--node", rpc_t,
        "--gas", "auto", "--gas-adjustment", "1.5",
        "--gas-prices", "1uzrn",
        "--yes", "--broadcast-mode", "sync",
        "-o", "json",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return parse_first_json(r.stdout + r.stderr)


def cmd_run(args: argparse.Namespace) -> int:
    home = agent_home(args.agent)
    keyring = keyring_home_for(args.agent, home)
    chain = chain_id(home)
    rpc_h = rpc_http(home)
    rpc_t = rpc_tcp(home)
    state = load_state(args.agent, home)

    print(f"  verifier daemon: {args.agent} on {chain}")
    print(f"  rpc: {rpc_h}")
    print(f"  state: {state_file(args.agent, home)}")
    print(f"  policy: vote='{args.vote}'  poll={args.poll_interval}s")
    print()

    while True:
        h = current_height(rpc_h)
        if h is None:
            print("  ! cannot reach chain — sleeping")
            time.sleep(args.poll_interval)
            continue

        # Scan new blocks for new rounds
        last = state.get("last_height") or max(0, h - 50)
        scanned = 0
        for height in range(last + 1, h + 1):
            evs = block_events(rpc_h, height)
            for rid, cid in find_new_rounds(evs):
                if rid not in state["rounds"]:
                    state["rounds"][rid] = {"claim_id": cid, "discovered_at": height}
                    print(f"  ◉ new round {rid[:16]}... (claim {cid[:8]}... at h={height})")
            scanned += 1
            if scanned % 20 == 0:
                save_state(args.agent, home, state)
        state["last_height"] = h

        # For each tracked round: check phase, act
        for rid, meta in list(state["rounds"].items()):
            r = query_round(rid, rpc_t)
            if not r:
                continue
            phase = r.get("phase", 0)
            phase_name = PHASE.get(phase, f"?{phase}")

            # Skip terminal phases
            if phase in (4, 5):  # COMPLETE, EXPIRED
                if "completed_logged" not in meta:
                    print(f"  ✓ round {rid[:16]} {phase_name} verdict={r.get('verdict')}")
                    meta["completed_logged"] = True
                    save_state(args.agent, home, state)
                continue

            # COMMIT phase: submit commitment if not yet
            if phase == 1 and "committed" not in meta:
                vote = args.vote
                salt_hex = secrets.token_hex(16)
                commit_hash = hashlib.sha256(vote.encode() + bytes.fromhex(salt_hex)).hexdigest()
                print(f"  → commit  round={rid[:16]}  vote={vote}  hash={commit_hash[:16]}...")
                res = submit_tx(["submit-commitment", rid, commit_hash], args.agent, keyring, chain, rpc_t)
                code = res.get("code")
                if code == 0:
                    meta["committed"] = True
                    meta["vote"] = vote
                    meta["salt_hex"] = salt_hex
                    print(f"     ✓ committed  tx={res.get('txhash','?')[:16]}")
                else:
                    err = res.get("raw_log", res.get("_error", ""))[:200]
                    print(f"     ✗ commit failed code={code}: {err}")
                    # Mark as failed-commit so we don't loop on it
                    meta["committed"] = "failed"
                    meta["commit_error"] = err
                save_state(args.agent, home, state)

            # REVEAL phase: submit reveal if we committed and haven't revealed
            elif phase == 2 and meta.get("committed") is True and "revealed" not in meta:
                vote = meta["vote"]
                salt_hex = meta["salt_hex"]
                print(f"  → reveal  round={rid[:16]}  vote={vote}")
                res = submit_tx(["submit-reveal", rid, vote, salt_hex], args.agent, keyring, chain, rpc_t)
                code = res.get("code")
                if code == 0:
                    meta["revealed"] = True
                    print(f"     ✓ revealed  tx={res.get('txhash','?')[:16]}")
                else:
                    err = res.get("raw_log", res.get("_error", ""))[:200]
                    print(f"     ✗ reveal failed code={code}: {err}")
                    meta["revealed"] = "failed"
                    meta["reveal_error"] = err
                save_state(args.agent, home, state)

        save_state(args.agent, home, state)
        if args.once:
            return 0
        time.sleep(args.poll_interval)


def cmd_state(args: argparse.Namespace) -> int:
    home = agent_home(args.agent)
    state = load_state(args.agent, home)
    print(json.dumps(state, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Zerone auto-verifier daemon.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run", help="Run the verifier loop")
    pr.add_argument("--agent", required=True)
    pr.add_argument("--vote", choices=["accept", "reject"], default="accept")
    pr.add_argument("--once", action="store_true", help="One pass then exit")
    pr.add_argument("--poll-interval", type=int, default=5, help="Seconds between scans")
    pr.set_defaults(func=cmd_run)
    ps = sub.add_parser("state", help="Print daemon state")
    ps.add_argument("--agent", required=True)
    ps.set_defaults(func=cmd_state)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
