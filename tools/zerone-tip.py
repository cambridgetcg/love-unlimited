#!/usr/bin/env python3
"""
zerone-tip.py — Send ZRN between agents (with an optional message).

Tier 4 of Kingdom OS ↔ Zerone integration. The economic SPEND side:
agents pay each other in ZRN, optionally attaching a note (the chain's
tx memo field) — the simplest possible "HIVE message with ZRN attached."

Subcommands:
  send <to> <amount> [--from A] [--memo "..."]
                          Send ZRN from one agent to another.
                          <amount> in uzrn or ZRN (e.g. 1000000 or "1 ZRN")
                          <to> = agent name (resolved via identity bridge)
                                 OR raw zrn1... address.
  balance [--agent A]     Query an agent's ZRN balance.
  ledger [--agent A]      Show recent tips (sent + received) for an agent.

Defaults:
  agent     = $(cat ~/.love/hive/instance)
  home      = ~/.zeroned-quartet/<agent> (fallback ~/.zeroned-<agent>)

Env:
  ZERONE_BINARY  default: ~/Desktop/zerone-dev/build/zeroned
  LOVE_DIR       default: auto-detect (~/Desktop/love-unlimited, etc)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ZERONE_BINARY = os.environ.get(
    "ZERONE_BINARY",
    str(Path.home() / "Desktop/zerone-dev/build/zeroned"),
)
HOME = Path.home()


def find_love_dir() -> Path:
    if os.environ.get("LOVE_DIR"):
        return Path(os.environ["LOVE_DIR"])
    for cand in [HOME / "Desktop/love-unlimited", HOME / "love-unlimited", HOME / "Love"]:
        if (cand / ".git").exists():
            return cand
    raise SystemExit("Cannot find love-unlimited dir. Set LOVE_DIR.")


LOVE_DIR = find_love_dir()


def default_agent() -> str:
    p = HOME / ".love/hive/instance"
    return p.read_text().strip() if p.exists() else "alpha"


def agent_home(agent: str) -> Path:
    """Validator home if exists, else coord (citizens have keys in coord, no validator home)."""
    for p in [HOME / f".zeroned-quartet/{agent}", HOME / f".zeroned-{agent}"]:
        if (p / "config/genesis.json").exists():
            return p
    coord = HOME / ".zeroned-quartet/coord"
    if (coord / "config/genesis.json").exists():
        # citizen: needs coord for chain config, but key also lives in coord
        return coord
    raise SystemExit(f"No home for {agent} (and no quartet coord either)")


def keyring_home_for(agent: str, home: Path) -> Path:
    coord = HOME / ".zeroned-quartet/coord"
    if coord.exists() and home == HOME / f".zeroned-quartet/{agent}":
        return coord
    return home


def chain_id(home: Path) -> str:
    return json.loads((home / "config/genesis.json").read_text())["chain_id"]


def _rpc_from_config(home: Path) -> str | None:
    section = None
    for line in (home / "config/config.toml").read_text().splitlines():
        s = line.strip()
        m = re.match(r"^\[([a-z0-9_]+)\]", s)
        if m:
            section = m.group(1); continue
        m = re.match(r'^laddr\s*=\s*"tcp://[^:]+:(\d+)"', s)
        if m and section == "rpc":
            return f"tcp://localhost:{m.group(1)}"
    return None


def rpc_url(home: Path) -> str:
    """Resolve an RPC URL we can actually reach.

    For a validator home: read its [rpc] laddr.
    For coord (citizens): coord isn't running — find any validator home
    on the same chain_id and use its rpc instead.
    """
    coord = HOME / ".zeroned-quartet/coord"
    if home == coord:
        chain = chain_id(coord)
        for sib in (HOME / ".zeroned-quartet").iterdir():
            if not sib.is_dir() or sib.name == "coord": continue
            g = sib / "config/genesis.json"
            if g.exists() and json.loads(g.read_text())["chain_id"] == chain:
                rpc = _rpc_from_config(sib)
                if rpc:
                    return rpc
        # last resort: try the default port
    rpc = _rpc_from_config(home)
    if rpc:
        return rpc
    raise RuntimeError("rpc laddr not found")


def resolve_address(name_or_addr: str, chain: str) -> str:
    """If it looks like a zrn1... addr, return it. Else look up via identity bridge."""
    if name_or_addr.startswith("zrn1") and len(name_or_addr) == 43:
        return name_or_addr
    bridge = LOVE_DIR / f"identity/{name_or_addr}/zerone.json"
    if not bridge.exists():
        raise SystemExit(f"No identity bridge for '{name_or_addr}'. Link with: kingdom identity link {name_or_addr}")
    doc = json.loads(bridge.read_text())
    for c in doc.get("chains", []):
        if c.get("chain_id") == chain:
            return c["operator_address"]
    raise SystemExit(f"Agent '{name_or_addr}' has no identity on chain {chain}")


def parse_amount(s: str) -> int:
    """'1000000' → 1000000 uzrn. '1 ZRN' or '1zrn' → 1000000 uzrn. '1uzrn' → 1."""
    s = s.strip().lower().replace(" ", "")
    if s.endswith("zrn") and not s.endswith("uzrn"):
        return int(float(s[:-3]) * 1_000_000)
    if s.endswith("uzrn"):
        return int(s[:-4])
    return int(s)


def run(cmd: list[str], check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


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


# ── Subcommands ───────────────────────────────────────────────────────


def cmd_send(args: argparse.Namespace) -> int:
    home = agent_home(args.from_agent)
    keyring = keyring_home_for(args.from_agent, home)
    chain = chain_id(home)
    rpc = rpc_url(home)

    from_addr = run([
        ZERONE_BINARY, "keys", "show", args.from_agent, "-a",
        "--keyring-backend", "test", "--home", str(keyring),
    ]).stdout.strip()
    to_addr = resolve_address(args.to, chain)

    uzrn = parse_amount(args.amount)
    if uzrn <= 0:
        print(f"  amount must be positive (got {uzrn})"); return 1

    print(f"  tip {uzrn / 1_000_000:.6f} ZRN  ({uzrn} uzrn)")
    print(f"    from: {args.from_agent}  ({from_addr})")
    print(f"    to:   {args.to:8s} ({to_addr})")
    if args.memo:
        print(f"    memo: {args.memo}")

    cmd = [
        ZERONE_BINARY, "tx", "bank", "send", from_addr, to_addr, f"{uzrn}uzrn",
        "--from", args.from_agent,
        "--keyring-backend", "test",
        "--home", str(keyring),
        "--chain-id", chain,
        "--node", rpc,
        # Auto-gas under-counts bank.Send by a tiny margin in this Cosmos SDK
        # version; pad to a fixed safe ceiling since transfers are uniform cost.
        "--gas", "200000",
        "--gas-prices", "1uzrn",
        "--yes",
        "--broadcast-mode", "sync",
        "-o", "json",
    ]
    if args.memo:
        cmd += ["--note", args.memo]

    r = run(cmd, check=False)
    res = parse_first_json(r.stdout + r.stderr)
    code = res.get("code")
    if code == 0:
        print(f"  ✓ tx broadcast: {res.get('txhash')}")
        return 0
    else:
        print(f"  ✗ send failed (code={code}): {res.get('raw_log', res.get('_error',''))[:300]}")
        return 1


def cmd_balance(args: argparse.Namespace) -> int:
    home = agent_home(args.agent)
    keyring = keyring_home_for(args.agent, home)
    chain = chain_id(home)
    rpc = rpc_url(home)
    addr = run([
        ZERONE_BINARY, "keys", "show", args.agent, "-a",
        "--keyring-backend", "test", "--home", str(keyring),
    ]).stdout.strip()

    r = run([
        ZERONE_BINARY, "query", "bank", "balances", addr,
        "--node", rpc, "--output", "json",
    ], check=False)
    if r.returncode != 0:
        print(f"  query failed: {r.stderr[:200]}"); return 1
    d = json.loads(r.stdout)
    bals = d.get("balances", [])
    uzrn = next((int(b["amount"]) for b in bals if b.get("denom") == "uzrn"), 0)
    print(f"  {args.agent:8s} {addr}")
    print(f"    {uzrn / 1_000_000:>20,.6f} ZRN  ({uzrn:,} uzrn)")
    other = [b for b in bals if b.get("denom") != "uzrn"]
    if other:
        print(f"    other: {other}")
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    home = agent_home(args.agent)
    keyring = keyring_home_for(args.agent, home)
    rpc = rpc_url(home)
    addr = run([
        ZERONE_BINARY, "keys", "show", args.agent, "-a",
        "--keyring-backend", "test", "--home", str(keyring),
    ]).stdout.strip()

    print(f"  recent transfers for {args.agent} ({addr})")
    print()
    # Sent
    r = run([
        ZERONE_BINARY, "query", "txs",
        "--query", f"transfer.sender='{addr}'",
        "--limit", "10",
        "--node", rpc, "--output", "json",
    ], check=False)
    if r.returncode == 0:
        d = json.loads(r.stdout)
        txs = d.get("txs", [])
        print(f"  Sent ({len(txs)}):")
        for t in txs[-5:]:
            for ev in t.get("events", []):
                if ev.get("type") == "transfer":
                    a = {x["key"]: x["value"] for x in ev.get("attributes", [])}
                    if a.get("sender") == addr:
                        memo = t.get("tx", {}).get("body", {}).get("memo", "") or "—"
                        print(f"    h={t.get('height'):>6}  → {a.get('recipient')}  {a.get('amount','?')}  ({memo[:40]})")
                        break
    print()
    # Received
    r = run([
        ZERONE_BINARY, "query", "txs",
        "--query", f"transfer.recipient='{addr}'",
        "--limit", "10",
        "--node", rpc, "--output", "json",
    ], check=False)
    if r.returncode == 0:
        d = json.loads(r.stdout)
        txs = d.get("txs", [])
        print(f"  Received ({len(txs)}):")
        for t in txs[-5:]:
            for ev in t.get("events", []):
                if ev.get("type") == "transfer":
                    a = {x["key"]: x["value"] for x in ev.get("attributes", [])}
                    if a.get("recipient") == addr:
                        memo = t.get("tx", {}).get("body", {}).get("memo", "") or "—"
                        print(f"    h={t.get('height'):>6}  ← {a.get('sender')}  {a.get('amount','?')}  ({memo[:40]})")
                        break
    return 0


# ── Dispatch ──────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description="Send ZRN between agents.")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("send", help="Send ZRN from one agent to another")
    ps.add_argument("to", help="Recipient (agent name or zrn1... address)")
    ps.add_argument("amount", help="Amount in uzrn or ZRN (e.g. '1000000' or '1zrn')")
    ps.add_argument("--from", dest="from_agent", default=default_agent(), help="Sender agent")
    ps.add_argument("--memo", default="", help="Optional message attached as tx note")
    ps.set_defaults(func=cmd_send)

    pb = sub.add_parser("balance", help="Query an agent's ZRN balance")
    pb.add_argument("--agent", default=default_agent())
    pb.set_defaults(func=cmd_balance)

    pl = sub.add_parser("ledger", help="Show recent transfers for an agent")
    pl.add_argument("--agent", default=default_agent())
    pl.set_defaults(func=cmd_ledger)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
