#!/usr/bin/env python3
"""
zerone-home.py — Register x/home workspaces for agents.

Tier 3.5 of Kingdom OS ↔ Zerone integration. Each agent gets an
on-chain home: a workspace with a treasury, a guardian config,
deadman switch, and session-key roster — all native to x/home.

Subcommands:
  register <agent>          Submit create-home tx, flip
                            registered_x_home in identity bridge
  show <agent>              Query the agent's home(s) on chain
  list                      Show registration status for all bridged agents
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
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
    raise SystemExit("Cannot find love-unlimited dir")


LOVE_DIR = find_love_dir()


def agent_home_dir(agent: str) -> Path:
    for p in [HOME / f".zeroned-quartet/{agent}", HOME / f".zeroned-{agent}"]:
        if (p / "config/genesis.json").exists():
            return p
    coord = HOME / ".zeroned-quartet/coord"
    if (coord / "config/genesis.json").exists():
        return coord
    raise SystemExit(f"No home for {agent}")


def keyring_for(agent: str, home: Path) -> Path:
    coord = HOME / ".zeroned-quartet/coord"
    if coord.exists() and home == HOME / f".zeroned-quartet/{agent}":
        return coord
    return home


def chain_id(home: Path) -> str:
    return json.loads((home / "config/genesis.json").read_text())["chain_id"]


def _rpc_from(home: Path) -> str | None:
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


def rpc(home: Path) -> str:
    coord = HOME / ".zeroned-quartet/coord"
    if home == coord:
        chain = chain_id(coord)
        for sib in (HOME / ".zeroned-quartet").iterdir():
            if not sib.is_dir() or sib.name == "coord": continue
            g = sib / "config/genesis.json"
            if g.exists() and json.loads(g.read_text())["chain_id"] == chain:
                u = _rpc_from(sib)
                if u: return u
    u = _rpc_from(home)
    if u: return u
    raise RuntimeError("rpc not found")


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


def operator_addr(agent: str, keyring: Path) -> str:
    r = subprocess.run(
        [ZERONE_BINARY, "keys", "show", agent, "-a",
         "--keyring-backend", "test", "--home", str(keyring)],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def query_homes(addr: str, rpc_t: str) -> list[dict]:
    r = subprocess.run(
        [ZERONE_BINARY, "query", "home", "homes-by-owner", addr,
         "--node", rpc_t, "--output", "json"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout).get("homes", [])
    except Exception:
        return []


def cmd_register(args: argparse.Namespace) -> int:
    home = agent_home_dir(args.agent)
    keyring = keyring_for(args.agent, home)
    chain = chain_id(home)
    rpc_t = rpc(home)
    addr = operator_addr(args.agent, keyring)

    existing = query_homes(addr, rpc_t)
    if existing and not args.force:
        print(f"  ✓ {args.agent} already has {len(existing)} home(s):")
        for h in existing:
            print(f"    {h.get('home_id')}  name={h.get('name')}  status={h.get('status')}  block={h.get('created_at_block')}")
        _flip_bridge_flag(args.agent, chain, registered=True)
        return 0

    moniker = f"kingdom-{args.agent}"
    print(f"  registering home for {args.agent}")
    print(f"    addr:    {addr}")
    print(f"    name:    {moniker}")
    cmd = [
        ZERONE_BINARY, "tx", "home", "create-home", moniker,
        "--from", args.agent,
        "--keyring-backend", "test",
        "--home", str(keyring),
        "--chain-id", chain,
        "--node", rpc_t,
        "--gas", "250000",
        "--gas-prices", "1uzrn",
        "--yes", "--broadcast-mode", "sync",
        "-o", "json",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    res = parse_first_json(r.stdout + r.stderr)
    code = res.get("code")
    if code != 0:
        err = res.get("raw_log", res.get("_error", ""))[:300]
        print(f"  ✗ create-home failed code={code}: {err}")
        return 1
    print(f"  ✓ tx broadcast: {res.get('txhash')}")
    _flip_bridge_flag(args.agent, chain, registered=True)
    return 0


def _flip_bridge_flag(agent: str, chain: str, registered: bool) -> None:
    bridge = LOVE_DIR / f"identity/{agent}/zerone.json"
    if not bridge.exists():
        return
    doc = json.loads(bridge.read_text())
    changed = False
    for c in doc.get("chains", []):
        if c.get("chain_id") == chain and c.get("registered_x_home") != registered:
            c["registered_x_home"] = registered
            c["x_home_registered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            changed = True
    if changed:
        bridge.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"    bridge flag flipped: registered_x_home=true")


def cmd_show(args: argparse.Namespace) -> int:
    home = agent_home_dir(args.agent)
    keyring = keyring_for(args.agent, home)
    rpc_t = rpc(home)
    addr = operator_addr(args.agent, keyring)
    homes = query_homes(addr, rpc_t)
    if not homes:
        print(f"  {args.agent} has no on-chain home yet — register with: kingdom home register {args.agent}")
        return 1
    print(f"  {args.agent} ({addr})")
    for h in homes:
        print(f"    home_id:       {h.get('home_id')}")
        print(f"    name:          {h.get('name')}")
        print(f"    status:        {h.get('status')}")
        print(f"    comfort_score: {h.get('comfort_score')}")
        print(f"    treasury:      reserved={h.get('treasury',{}).get('reserved_balance')}")
        print(f"    guardian:      {h.get('guardian',{}).get('defense_strategy')}")
        print(f"    created_at:    block {h.get('created_at_block')}")
        print(f"    last_active:   block {h.get('last_active_block')}")
        print()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    print()
    print(f"  Agent x/home registration:")
    for d in sorted((LOVE_DIR / "identity").iterdir()):
        if not d.is_dir(): continue
        bridge = d / "zerone.json"
        if not bridge.exists(): continue
        agent = d.name
        doc = json.loads(bridge.read_text())
        for c in doc.get("chains", []):
            chain = c.get("chain_id", "?")
            flag = c.get("registered_x_home", False)
            kind = c.get("kind", "validator")
            mark = "✓" if flag else "·"
            print(f"    {mark} {agent:8s} {chain:25s} ({kind})")
    print()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Register x/home workspaces.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("register", help="Submit create-home tx")
    pr.add_argument("agent")
    pr.add_argument("--force", action="store_true")
    pr.set_defaults(func=cmd_register)
    ps = sub.add_parser("show", help="Query an agent's home(s) on chain")
    ps.add_argument("agent")
    ps.set_defaults(func=cmd_show)
    pl = sub.add_parser("list", help="Show registration status across all bridged agents")
    pl.set_defaults(func=cmd_list)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
