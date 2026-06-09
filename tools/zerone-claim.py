#!/usr/bin/env python3
"""
zerone-claim.py — Submit knowledge claims from love-unlimited memory to Zerone.

Tier 5 of Kingdom OS ↔ Zerone integration. Agents earn ZRN by getting their
knowledge claims accepted by the verification quorum. This tool submits a
claim from memory text + auto-handles the prerequisite agent registration.

Subcommands:
  register <agent>                 Register agent with x/zerone_auth (one-time per chain)
  submit <text> [--agent A]        Submit a claim, return tx hash + claim ID
  status <tx_hash>                 Show tx outcome + extracted claim ID
  watch <claim_id>                 Watch verification progress

Defaults:
  agent     = $(cat ~/.love/hive/instance)
  home      = ~/.zeroned-quartet/<agent> (fallback ~/.zeroned-<agent>)
  chain_id  = read from <home>/config/genesis.json
  rpc       = read from <home>/config/config.toml [rpc] laddr
  domain    = "general"
  category  = "computational"
  fee       = 1000000 (= 1 ZRN review fee)

Env:
  ZERONE_BINARY  default: ~/Desktop/zerone-dev/build/zeroned
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ZERONE_BINARY = os.environ.get(
    "ZERONE_BINARY",
    str(Path.home() / "Desktop/zerone-dev/build/zeroned"),
)
HOME = Path.home()


# ── Discovery ─────────────────────────────────────────────────────────


def default_agent() -> str:
    p = HOME / ".love/hive/instance"
    if p.exists():
        return p.read_text().strip()
    return "alpha"


def agent_home(agent: str) -> Path:
    for p in [HOME / f".zeroned-quartet/{agent}", HOME / f".zeroned-{agent}"]:
        if (p / "config/genesis.json").exists():
            return p
    raise SystemExit(
        f"No initialized validator home for {agent}. "
        f"Tried ~/.zeroned-quartet/{agent}, ~/.zeroned-{agent}. "
        f"Spawn first: kingdom validator spawn {agent}"
    )


def keyring_home_for(agent: str, home: Path) -> Path:
    """Quartet stores keys in coord; solo stores in agent home."""
    coord = HOME / ".zeroned-quartet/coord"
    if coord.exists() and home == HOME / f".zeroned-quartet/{agent}":
        return coord
    return home


def chain_id(home: Path) -> str:
    g = json.loads((home / "config/genesis.json").read_text())
    return g["chain_id"]


def rpc_url(home: Path) -> str:
    """Section-aware: read [rpc] laddr from config.toml."""
    section = None
    for line in (home / "config/config.toml").read_text().splitlines():
        s = line.strip()
        m = re.match(r"^\[([a-z0-9_]+)\]", s)
        if m:
            section = m.group(1)
            continue
        m = re.match(r'^laddr\s*=\s*"tcp://[^:]+:(\d+)"', s)
        if m and section == "rpc":
            return f"tcp://localhost:{m.group(1)}"
    raise RuntimeError("could not find [rpc] laddr in config.toml")


# ── On-chain helpers ──────────────────────────────────────────────────


def run(cmd: list[str], check=True, capture=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def consensus_pubkey_hex(home: Path) -> str:
    """Read priv_validator_key.json and extract Ed25519 pubkey as 64-char hex."""
    pv = json.loads((home / "config/priv_validator_key.json").read_text())
    pub_b64 = pv["pub_key"]["value"]
    return base64.b64decode(pub_b64).hex()


def did_from_pubkey(pub_hex: str) -> str:
    return f"did:zrn:{pub_hex[:32]}"


def operator_addr(agent: str, keyring_home: Path) -> str:
    r = run([
        ZERONE_BINARY, "keys", "show", agent, "-a",
        "--keyring-backend", "test", "--home", str(keyring_home),
    ])
    return r.stdout.strip()


def is_registered(addr: str, rpc: str) -> bool:
    """Query x/zerone_auth for the account; return True if found."""
    r = run([
        ZERONE_BINARY, "query", "zerone_auth", "account", addr,
        "--node", rpc, "--output", "json",
    ], check=False)
    if r.returncode != 0:
        return False
    try:
        d = json.loads(r.stdout)
        # Account exists if response has did or similar field
        return "did" in d or "account" in d or "pubKey" in d
    except json.JSONDecodeError:
        return False


def submit_tx(tx_args: list[str], home: Path, chain: str, rpc: str) -> dict:
    cmd = (
        [ZERONE_BINARY, "tx"] + tx_args + [
            "--keyring-backend", "test",
            "--home", str(home),
            "--chain-id", chain,
            "--node", rpc,
            "--gas", "auto",
            "--gas-adjustment", "1.5",
            "--gas-prices", "1uzrn",
            "--yes",
            "--broadcast-mode", "sync",
            "-o", "json",
        ]
    )
    r = run(cmd, check=False)
    out = r.stdout + r.stderr
    # zeroned can interleave non-JSON lines (e.g. "gas estimate: 82129") around
    # the JSON object. Use raw_decode to parse the first JSON object found,
    # ignoring trailing/leading garbage.
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(out):
        i = out.find("{", idx)
        if i < 0:
            break
        try:
            obj, _ = decoder.raw_decode(out[i:])
            return obj
        except json.JSONDecodeError:
            idx = i + 1
    return {"_error": out}


def query_tx(tx_hash: str, rpc: str, retries: int = 8, sleep_s: float = 2.0) -> dict | None:
    """Poll for tx; some chains take a few blocks to index."""
    for _ in range(retries):
        r = run([
            ZERONE_BINARY, "query", "tx", tx_hash,
            "--node", rpc, "--output", "json",
        ], check=False)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(sleep_s)
    return None


# ── Subcommands ───────────────────────────────────────────────────────


def cmd_register(args: argparse.Namespace) -> int:
    home = agent_home(args.agent)
    keyring = keyring_home_for(args.agent, home)
    chain = chain_id(home)
    rpc = rpc_url(home)
    addr = operator_addr(args.agent, keyring)

    if not args.force and is_registered(addr, rpc):
        print(f"  ✓ {args.agent} ({addr}) already registered on {chain}")
        return 0

    pub_hex = consensus_pubkey_hex(home)
    did = did_from_pubkey(pub_hex)
    print(f"  registering {args.agent} as agent on {chain}")
    print(f"    address: {addr}")
    print(f"    did:     {did}")
    print(f"    pubkey:  {pub_hex[:16]}...")

    res = submit_tx(
        ["zerone_auth", "register-account", did, pub_hex, "agent", "--from", args.agent],
        keyring, chain, rpc,
    )
    code = res.get("code")
    if code == 0:
        print(f"  ✓ tx broadcast: {res.get('txhash','?')}")
        return 0
    else:
        print(f"  ✗ register failed (code={code}): {res.get('raw_log', res.get('_error',''))[:300]}")
        return 1


def cmd_submit(args: argparse.Namespace) -> int:
    home = agent_home(args.agent)
    keyring = keyring_home_for(args.agent, home)
    chain = chain_id(home)
    rpc = rpc_url(home)

    # Auto-register if needed (unless --no-auto-register)
    addr = operator_addr(args.agent, keyring)
    if not args.no_auto_register and not is_registered(addr, rpc):
        print(f"  {args.agent} not yet registered — registering first...")
        rc = cmd_register(argparse.Namespace(agent=args.agent, force=False))
        if rc != 0:
            return rc
        time.sleep(3)  # wait a block for registration to settle

    text = args.text
    if text == "-":
        text = sys.stdin.read().strip()
    if not text:
        print("  empty claim — refusing"); return 1

    print(f"  submitting claim from {args.agent}")
    print(f"    domain:   {args.domain}")
    print(f"    category: {args.category}")
    print(f"    fee:      {args.fee} uzrn")
    print(f"    text:     {text[:120]}...")

    res = submit_tx(
        ["knowledge", "submit-claim", text, args.domain, args.category, str(args.fee),
         "--from", args.agent],
        keyring, chain, rpc,
    )
    code = res.get("code")
    txhash = res.get("txhash")
    if code != 0:
        print(f"  ✗ submit failed (code={code}): {res.get('raw_log', res.get('_error',''))[:300]}")
        return 1

    print(f"  ✓ tx broadcast: {txhash}")
    print(f"  → fetching claim ID (waiting up to ~16s for indexing)...")
    full = query_tx(txhash, rpc)
    if not full:
        print(f"  could not fetch tx; check manually: zeroned query tx {txhash} --node {rpc}")
        return 0
    # Extract claim ID + fee distribution from events
    claim_id = None
    fee_dist = {}
    for ev in full.get("events", []):
        t = ev.get("type", "")
        if "knowledge" in t:
            attrs = {a.get("key"): a.get("value") for a in ev.get("attributes", [])}
            if "claim_id" in attrs and not claim_id:
                claim_id = attrs["claim_id"]
            if "review_fee_distributed" in t:
                fee_dist = attrs

    print(f"  height:   {full.get('height')}")
    print(f"  claim_id: {claim_id or '(not in events — check tx)'}")
    if fee_dist:
        print(f"  fee:      {fee_dist.get('fee_amount')} uzrn distributed:")
        print(f"    verifier_pool {fee_dist.get('verifier_pool')}  protocol {fee_dist.get('protocol')}  dev {fee_dist.get('development')}  research {fee_dist.get('research')}")
    if claim_id:
        print(f"  watch:    kingdom claim watch {claim_id}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    agent = args.agent
    home = agent_home(agent)
    rpc = rpc_url(home)
    full = query_tx(args.tx_hash, rpc)
    if not full:
        print(f"  not found: {args.tx_hash}"); return 1
    print(f"  height:  {full.get('height')}")
    print(f"  code:    {full.get('code')}")
    print(f"  raw_log: {full.get('raw_log','')[:200]}")
    return 0


CLAIM_STATUS = {
    1: "DRAFT", 2: "SUBMITTED", 3: "PROVISIONAL", 4: "VERIFYING",
    5: "VERIFICATION_ROUND_OPEN", 6: "ACCEPTED", 7: "REJECTED",
    8: "CHALLENGED", 9: "RETIRED",
}

def cmd_watch(args: argparse.Namespace) -> int:
    home = agent_home(args.agent)
    rpc = rpc_url(home)
    print(f"  watching claim {args.claim_id} on {chain_id(home)} (Ctrl-C to stop)")
    last = None
    for _ in range(40):
        r = run([
            ZERONE_BINARY, "query", "knowledge", "claim", args.claim_id,
            "--node", rpc, "--output", "json",
        ], check=False)
        if r.returncode == 0:
            try:
                d = json.loads(r.stdout)
                c = d.get("claim", d)
                state = (c.get("status"), c.get("verification_round_id"))
                if state != last:
                    sname = CLAIM_STATUS.get(c.get("status"), str(c.get("status")))
                    print(f"  block?  status={sname}  round={c.get('verification_round_id','-')}  stake={c.get('stake')}")
                    last = state
                if c.get("status") in (6, 7):  # ACCEPTED or REJECTED
                    return 0
            except json.JSONDecodeError:
                pass
        time.sleep(3)
    return 0


# ── Dispatch ──────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description="Submit Zerone knowledge claims from love-unlimited.")
    p.add_argument("--agent", default=default_agent(), help="Agent name (default: $(cat ~/.love/hive/instance))")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("register", help="Register agent with x/zerone_auth (one-time)")
    pr.add_argument("--force", action="store_true")
    pr.set_defaults(func=cmd_register)

    ps = sub.add_parser("submit", help="Submit a knowledge claim")
    ps.add_argument("text", help="Claim text (use '-' to read from stdin)")
    ps.add_argument("--domain", default="general")
    ps.add_argument("--category", default="computational")
    ps.add_argument("--fee", type=int, default=1000000, help="Review fee in uzrn (default 1 ZRN)")
    ps.add_argument("--no-auto-register", action="store_true")
    ps.set_defaults(func=cmd_submit)

    pt = sub.add_parser("status", help="Show tx status by hash")
    pt.add_argument("tx_hash")
    pt.set_defaults(func=cmd_status)

    pw = sub.add_parser("watch", help="Watch a claim's verification progress")
    pw.add_argument("claim_id")
    pw.set_defaults(func=cmd_watch)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
