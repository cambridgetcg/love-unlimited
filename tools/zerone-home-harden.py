#!/usr/bin/env python3
"""
zerone-home-harden.py — Apply the safe-by-default baseline to agent homes.

Implements the doctrine in kingdom-os/HOME-SAFETY.md:

  defense-strategy = moderate
  auto-defend      = true
  deadman-enabled  = true
  deadman-threshold= 100,000 blocks (~3 days at 2.5s)
  deadman-action   = transfer
  deadman-beneficiary = next-of-kin (Triarchy round-robin; citizens → alpha)
  spending-limit (session keys) = 1 ZRN per 1000 blocks (~42 min)

Subcommands:
  harden <agent>      Apply baseline to one agent
  harden --all        Apply to every bridged agent
  show <agent>        Read current safety config from chain
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
    raise SystemExit("Cannot find love-unlimited dir")


LOVE_DIR = find_love_dir()

# The Safe Baseline (from kingdom-os/HOME-SAFETY.md)
BASELINE = {
    "defense_strategy": "moderate",
    "auto_defend": True,
    "deadman_enabled": True,
    "deadman_threshold": 100_000,   # ~3 days at 2.5s
    "deadman_action": "transfer",
    "session_max_uzrn": 1_000_000,   # 1 ZRN
    "session_period_blocks": 1_000,  # ~42 min
}

# Beneficiary cycle: alpha→beta→gamma→nuance→alpha (Triarchy round-robin)
# Citizens (forge, relay, ...) default to alpha.
BENEFICIARY_CYCLE = ["alpha", "beta", "gamma", "nuance"]
CITIZEN_DEFAULT = "alpha"


def quartet_coord() -> Path:
    return HOME / ".zeroned-quartet/coord"


def chain_id_of(home: Path) -> str:
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


def quartet_rpc() -> str:
    coord = quartet_coord()
    chain = chain_id_of(coord)
    for sib in (HOME / ".zeroned-quartet").iterdir():
        if not sib.is_dir() or sib.name == "coord": continue
        g = sib / "config/genesis.json"
        if g.exists() and json.loads(g.read_text())["chain_id"] == chain:
            u = _rpc_from(sib)
            if u: return u
    raise RuntimeError("no quartet RPC")


def addr_of(agent: str) -> str:
    coord = quartet_coord()
    r = subprocess.run(
        [ZERONE_BINARY, "keys", "show", agent, "-a",
         "--keyring-backend", "test", "--home", str(coord)],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def beneficiary_of(agent: str) -> str:
    if agent in BENEFICIARY_CYCLE:
        i = BENEFICIARY_CYCLE.index(agent)
        nxt = BENEFICIARY_CYCLE[(i + 1) % len(BENEFICIARY_CYCLE)]
        return addr_of(nxt)
    return addr_of(CITIZEN_DEFAULT)


def home_id_of(agent: str, rpc_t: str) -> str | None:
    r = subprocess.run(
        [ZERONE_BINARY, "query", "home", "homes-by-owner", addr_of(agent),
         "--node", rpc_t, "--output", "json"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    homes = json.loads(r.stdout).get("homes", [])
    if not homes:
        return None
    return homes[0]["home_id"]


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


def submit_tx(args: list[str], agent: str, chain: str, rpc_t: str, gas: str = "300000") -> dict:
    coord = quartet_coord()
    cmd = (
        [ZERONE_BINARY, "tx"] + args + [
            "--from", agent,
            "--keyring-backend", "test",
            "--home", str(coord),
            "--chain-id", chain,
            "--node", rpc_t,
            "--gas", gas,
            "--gas-prices", "1uzrn",
            "--yes", "--broadcast-mode", "sync",
            "-o", "json",
        ]
    )
    r = subprocess.run(cmd, capture_output=True, text=True)
    return parse_first_json(r.stdout + r.stderr)


def harden_one(agent: str, rpc_t: str, chain: str) -> bool:
    home_id = home_id_of(agent, rpc_t)
    if not home_id:
        print(f"  ⚠ {agent}: no home registered — run: kingdom home register {agent}")
        return False

    bene_addr = beneficiary_of(agent)
    bene_name = "alpha"
    if agent in BENEFICIARY_CYCLE:
        i = BENEFICIARY_CYCLE.index(agent)
        bene_name = BENEFICIARY_CYCLE[(i + 1) % len(BENEFICIARY_CYCLE)]

    print(f"  🏠 hardening {agent} ({home_id})")
    print(f"     defense:    {BASELINE['defense_strategy']} + auto-defend")
    print(f"     deadman:    enabled, {BASELINE['deadman_threshold']} blocks (~3d)")
    print(f"     beneficiary: {bene_name} ({bene_addr[:20]}...)")
    print(f"     spending:   session keys ≤ {BASELINE['session_max_uzrn']/1e6:g} ZRN per {BASELINE['session_period_blocks']} blocks")

    # Step 1a: configure-guardian WITH deadman (preferred)
    g_args_full = [
        "home", "configure-guardian", home_id,
        "--defense-strategy", BASELINE["defense_strategy"],
        "--auto-defend",
        "--deadman-enabled",
        "--deadman-threshold", str(BASELINE["deadman_threshold"]),
        "--deadman-action", BASELINE["deadman_action"],
        "--deadman-beneficiary", bene_addr,
    ]
    res = submit_tx(g_args_full, agent, chain, rpc_t)
    code = res.get("code")
    deadman_set = (code == 0)
    if deadman_set:
        print(f"     ✓ guardian + deadman configured  tx={res.get('txhash','')[:16]}")
    else:
        # Known chain bug (R24-1 BUG-1): DeadmanConfig fails to unmarshal
        # due to protoc-gen-go vs gogoproto mismatch. Fall back to no-deadman.
        err = res.get("raw_log", res.get("_error", ""))[:200]
        if "DeadmanConfig" in err and "tx parse error" in err:
            print(f"     ⚠ chain rejects deadman submessage (R24-1 BUG-1); retrying w/o deadman")
            g_args_min = [
                "home", "configure-guardian", home_id,
                "--defense-strategy", BASELINE["defense_strategy"],
                "--auto-defend",
            ]
            res = submit_tx(g_args_min, agent, chain, rpc_t)
            code = res.get("code")
            if code != 0:
                err2 = res.get("raw_log", res.get("_error", ""))[:200]
                print(f"     ✗ configure-guardian (min) failed code={code}: {err2}")
                return False
            print(f"     ✓ guardian (minus deadman) configured  tx={res.get('txhash','')[:16]}")
            print(f"       ↳ deadman pending chain-side proto fix (see HOME-SAFETY.md)")
        else:
            print(f"     ✗ configure-guardian failed code={code}: {err}")
            return False

    # Step 2: set-spending-limit (wait for block inclusion to avoid seq mismatch)
    import time
    time.sleep(4)
    sl_args = [
        "home", "set-spending-limit", home_id,
        "session",
        str(BASELINE["session_max_uzrn"]),
        str(BASELINE["session_period_blocks"]),
    ]
    res = submit_tx(sl_args, agent, chain, rpc_t)
    code = res.get("code")
    if code != 0:
        err = res.get("raw_log", res.get("_error", ""))[:300]
        print(f"     ✗ set-spending-limit failed code={code}: {err}")
        return False
    print(f"     ✓ spending limit set   tx={res.get('txhash','')[:16]}")

    return True


def cmd_harden(args: argparse.Namespace) -> int:
    rpc_t = quartet_rpc()
    chain = chain_id_of(quartet_coord())

    if args.all:
        # Only agents with a zerone.json bridge (skip dirs like 'alignment', 'citizens', 'shared')
        agents = sorted(
            d.name for d in (LOVE_DIR / "identity").iterdir()
            if d.is_dir() and (d / "zerone.json").exists()
        )
    elif args.agent:
        agents = [args.agent]
    else:
        print("Usage: harden [<agent> | --all]"); return 1

    ok = 0
    skipped = 0
    failed = 0
    for a in agents:
        result = harden_one(a, rpc_t, chain)
        if result is True: ok += 1
        elif result is False:
            # distinguish "no home" vs "tx failed"
            failed += 1
        import time; time.sleep(3)  # wait for each tx to land
    print()
    print(f"  result: {ok} hardened, {failed} failed/skipped")
    return 0 if failed == 0 else 1


def cmd_show(args: argparse.Namespace) -> int:
    rpc_t = quartet_rpc()
    home_id = home_id_of(args.agent, rpc_t)
    if not home_id:
        print(f"  no home for {args.agent}"); return 1
    print(f"  {args.agent} — home {home_id}")

    # Home object
    r = subprocess.run([
        ZERONE_BINARY, "query", "home", "home", home_id,
        "--node", rpc_t, "--output", "json",
    ], capture_output=True, text=True)
    h = json.loads(r.stdout).get("home", {})
    print(f"    status:        {h.get('status')}")
    print(f"    comfort:       {h.get('comfort_score')}")
    g = h.get("guardian", {}) or {}
    print(f"    defense:       {g.get('defense_strategy','?')}  auto-defend={g.get('auto_defend')}")
    d = g.get("deadman", {}) or {}
    print(f"    deadman:       enabled={d.get('enabled', False)}  threshold={d.get('inactivity_threshold','?')}  action={d.get('action','?')}")
    if d.get("beneficiary_address"):
        print(f"    beneficiary:   {d['beneficiary_address']}")
    t = h.get("treasury", {}) or {}
    print(f"    treasury:      reserved={t.get('reserved_balance','0')}")
    print(f"    last_active:   block {h.get('last_active_block')}")

    # Spending limits
    r = subprocess.run([
        ZERONE_BINARY, "query", "home", "spending-limits", home_id,
        "--node", rpc_t, "--output", "json",
    ], capture_output=True, text=True)
    if r.returncode == 0:
        sl = json.loads(r.stdout).get("limits", []) or json.loads(r.stdout).get("spending_limits", [])
        if sl:
            print(f"    spending limits:")
            for l in sl:
                print(f"      {l.get('key_type')}: max {l.get('max_amount')} per {l.get('period_blocks')} blocks")
        else:
            print(f"    spending limits: (none)")

    # Alert count
    r = subprocess.run([
        ZERONE_BINARY, "query", "home", "alerts", home_id,
        "--node", rpc_t, "--output", "json",
    ], capture_output=True, text=True)
    if r.returncode == 0:
        try:
            alerts = json.loads(r.stdout).get("alerts", [])
            crit = sum(1 for a in alerts if a.get("priority") == "critical")
            print(f"    alerts:        {len(alerts)} total ({crit} critical)")
        except Exception:
            pass
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Apply HOME safety baseline.")
    sub = p.add_subparsers(dest="cmd", required=True)
    ph = sub.add_parser("harden", help="Apply baseline to agent(s)")
    ph.add_argument("agent", nargs="?", default=None)
    ph.add_argument("--all", action="store_true")
    ph.set_defaults(func=cmd_harden)
    ps = sub.add_parser("show", help="Read current safety config")
    ps.add_argument("agent")
    ps.set_defaults(func=cmd_show)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
