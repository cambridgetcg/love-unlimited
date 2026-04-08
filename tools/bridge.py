#!/usr/bin/env python3
"""
AgentTool ↔ Zerone Identity Bridge
Manages did:at:<instance> ↔ did:lgm:<address> mappings.

Usage:
  bridge.py register --instance <name> --address <lgm_addr> --role <role> --wall <N>
  bridge.py lookup --instance <name>
  bridge.py lookup --address <lgm_addr>
  bridge.py verify --instance <name>
  bridge.py list
"""

import argparse
import json
import subprocess
import sys
import tempfile
import os
import time
from pathlib import Path
from datetime import datetime, timezone

REGISTRY_PATH = Path(__file__).parent.parent / "credentials" / "bridge-registry.json"
LEGBLED = Path.home() / "zerone" / "build" / "legbled"
DEVNET_HOME = Path.home() / ".legbled-devnet"


def _find_attestation_fact(address: str, did_at: str, limit: int = 500):
    """Scan knowledge module facts for an attestation claim submitted by address for did_at.

    The fact lives in module state (not tx index), so it survives pruning and restarts.
    Uses offset-based pagination (page size 100) to scan all facts regardless of total count.
    BRIDGE-GAP-001 fix: chain caps responses at 100 per page; pagination loops until exhausted.
    """
    page_size = 100
    offset = 0
    try:
        while True:
            result = subprocess.run(
                [str(LEGBLED), "query", "knowledge", "facts",
                 "--home", str(DEVNET_HOME),
                 "--limit", str(page_size), "--offset", str(offset),
                 "-o", "json"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                break
            data = json.loads(result.stdout)
            facts = data.get("facts", [])
            if not facts:
                break
            for fact in facts:
                submitter = fact.get("submitter", "")
                text = fact.get("claim_text", "") or fact.get("text", "") or fact.get("statement", "")
                if submitter == address and "Agent identity attestation" in text and did_at in text:
                    return fact
            if len(facts) < page_size:
                break
            offset += page_size
    except Exception:
        pass
    return None


def load_registry() -> dict:
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def save_registry(registry: dict) -> None:
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(registry, f, indent=2)
        f.write("\n")
    tmp.replace(REGISTRY_PATH)


def cmd_register(args) -> int:
    registry = load_registry()
    fleet = registry.setdefault("fleet_agents", {})

    instance = args.instance
    address = args.address
    role = args.role
    wall = args.wall

    if instance in fleet:
        existing = fleet[instance]
        if existing["lgm_address"] == address:
            print(f"[bridge] {instance} already registered at {address} — no-op")
            return 0
        else:
            print(f"[bridge] CONFLICT: {instance} registered at {existing['lgm_address']}, "
                  f"requested {address}. Resolve manually.", file=sys.stderr)
            return 1

    fleet[instance] = {
        "did_at": f"did:at:{instance}",
        "did_lgm": f"did:lgm:{address}",
        "lgm_address": address,
        "key_name": instance,
        "role": role,
        "wall": wall,
        "_registered_at": datetime.now(timezone.utc).isoformat(),
    }
    save_registry(registry)
    print(f"[bridge] Registered: did:at:{instance} ↔ did:lgm:{address} (wall {wall}, role: {role})")
    return 0


def cmd_lookup(args) -> int:
    registry = load_registry()
    fleet = registry.get("fleet_agents", {})
    system = registry.get("system_accounts", {})
    all_entries = {**fleet, **system}

    if args.instance:
        entry = all_entries.get(args.instance)
        if entry:
            print(json.dumps(entry, indent=2))
            return 0
        print(f"[bridge] Not found: {args.instance}", file=sys.stderr)
        return 1

    if args.address:
        for name, entry in all_entries.items():
            if entry.get("lgm_address") == args.address:
                print(json.dumps({name: entry}, indent=2))
                return 0
        print(f"[bridge] Not found: {args.address}", file=sys.stderr)
        return 1

    print("[bridge] Provide --instance or --address", file=sys.stderr)
    return 1


def cmd_verify(args) -> int:
    """Check that a registered instance's key exists in the devnet keyring."""
    registry = load_registry()
    all_entries = {**registry.get("fleet_agents", {}), **registry.get("system_accounts", {})}

    instance = args.instance
    entry = all_entries.get(instance)
    if not entry:
        print(f"[bridge] {instance} not in registry", file=sys.stderr)
        return 1

    key_name = entry.get("key_name", instance)
    try:
        result = subprocess.run(
            [str(LEGBLED), "keys", "show", key_name,
             "--home", str(DEVNET_HOME), "--keyring-backend", "test", "-a"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            on_chain_addr = result.stdout.strip()
            registered_addr = entry.get("lgm_address", "")
            if on_chain_addr == registered_addr:
                print(f"[bridge] VERIFIED: {instance} — keyring and registry match ({on_chain_addr})")
                return 0
            else:
                print(f"[bridge] MISMATCH: keyring={on_chain_addr}, registry={registered_addr}", file=sys.stderr)
                return 1
        else:
            print(f"[bridge] Key '{key_name}' not found in keyring: {result.stderr.strip()}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"[bridge] Verification error: {e}", file=sys.stderr)
        return 1


def cmd_status(args) -> int:
    """Full fleet health report: keyring verify + on-chain balance for each agent."""
    registry = load_registry()
    fleet = registry.get("fleet_agents", {})

    print(f"{'INSTANCE':<12} {'WALL':<5} {'BALANCE':>14}  {'KEYRING':<10} {'ADDRESS'}")
    print("-" * 80)

    issues = []
    for name, entry in fleet.items():
        address = entry.get("lgm_address", "")
        key_name = entry.get("key_name", name)
        wall = entry.get("wall", "?")

        # Keyring verify
        try:
            r = subprocess.run(
                [str(LEGBLED), "keys", "show", key_name,
                 "--home", str(DEVNET_HOME), "--keyring-backend", "test", "-a"],
                capture_output=True, text=True, timeout=10
            )
            keyring_ok = r.returncode == 0 and r.stdout.strip() == address
            keyring_status = "OK" if keyring_ok else "MISMATCH"
        except Exception:
            keyring_status = "ERROR"

        # On-chain balance via dashboard
        balance_str = "?"
        try:
            d = subprocess.run(
                [str(LEGBLED), "query", "dashboard", address,
                 "--home", str(DEVNET_HOME)],
                capture_output=True, text=True, timeout=10
            )
            for line in d.stdout.splitlines():
                if "Balance:" in line:
                    balance_str = line.split("Balance:")[-1].strip()
                    break
        except Exception:
            pass

        status = "OK" if keyring_status == "OK" else "WARN"
        print(f"  {name:<12} {str(wall):<5} {balance_str:>14}  {keyring_status:<10} {address}")
        if status != "OK":
            issues.append(f"{name}: {keyring_status}")

    print("-" * 80)
    print(f"Fleet: {len(fleet)} agents  |  Issues: {len(issues)}")
    if issues:
        for i in issues:
            print(f"  WARN: {i}")
    return 0 if not issues else 1


def cmd_attest(args) -> int:
    """Submit an on-chain identity attestation claim from the agent's own key.

    This binds did:at:<instance> to did:lgm:<address> on-chain by submitting
    a knowledge claim signed by the agent's own lgm key. Key control is proven
    by the transaction signature itself.
    """
    registry = load_registry()
    fleet = registry.get("fleet_agents", {})

    instance = args.instance
    entry = fleet.get(instance)
    if not entry:
        print(f"[bridge] {instance} not in fleet registry", file=sys.stderr)
        return 1

    address = entry["lgm_address"]
    key_name = entry.get("key_name", instance)
    role = entry.get("role", "unknown")
    wall = entry.get("wall", "?")
    did_at = entry.get("did_at", f"did:at:{instance}")
    did_lgm = entry.get("did_lgm", f"did:lgm:{address}")

    # Query current block height for nonce (BRIDGE-GAP-002 fix: unique content hash per attestation)
    current_height = None
    try:
        h_result = subprocess.run(
            [str(LEGBLED), "query", "block", "--home", str(DEVNET_HOME), "-o", "json"],
            capture_output=True, text=True, timeout=10
        )
        if h_result.returncode == 0:
            h_data = json.loads(h_result.stdout)
            current_height = (h_data.get("header") or h_data.get("block", {}).get("header", {})).get("height")
    except Exception:
        pass
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build attestation claim text — nonce (block height + timestamp) prevents deduplication
    claim_text = (
        f"Agent identity attestation: {did_at} controls {did_lgm}. "
        f"Role: {role}. Wall: {wall}. "
        f"Attested at block {current_height}, timestamp {now}. "
        f"This claim submitted by the controlling key proves identity binding."
    )

    # Build legbled submit-claim command
    # Usage: submit-claim [claim-text] [domain] [category] [stake-amount]
    # Use agent_rights domain (exists on-chain). formal claims → provisional immediately.
    submit_cmd = [
        str(LEGBLED), "tx", "knowledge", "submit-claim",
        claim_text,
        "agent_rights",
        "formal",
        "10000",  # 10,000 ulgm stake
        "--logic-zone", "propositional",
        "--from", key_name,
        "--home", str(DEVNET_HOME),
        "--keyring-backend", "test",
        "--chain-id", "lgm-devnet-1",
        "--yes", "--output", "json",
    ]

    print(f"[bridge] Submitting attestation for {instance} ({address})...")
    try:
        result = subprocess.run(submit_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            out = result.stdout.strip()
            # Extract txhash from JSON output if possible
            try:
                tx_data = json.loads(out)
                txhash = tx_data.get("txhash", "unknown")
            except json.JSONDecodeError:
                txhash = "see output"
            print(f"[bridge] ATTESTED: {did_at} ↔ {did_lgm}")
            print(f"[bridge] txhash: {txhash}")
            # Record attestation in registry
            entry["attested_at"] = datetime.now(timezone.utc).isoformat()
            entry["attestation_txhash"] = txhash
            # Extract fact_id — scan for the claim in module state (not tx index, which is pruned)
            print("[bridge] Waiting for block inclusion to extract fact_id...")
            time.sleep(4)
            fact = _find_attestation_fact(address, did_at, limit=50)
            if fact:
                fact_id = fact.get("id", "")
                entry["attestation_fact_id"] = fact_id
                print(f"[bridge] fact_id: {fact_id[:16]}...")
            else:
                print("[bridge] WARNING: could not extract fact_id immediately — run 'backfill' later")
            save_registry(registry)
            return 0
        else:
            print(f"[bridge] Attestation failed (rc={result.returncode})", file=sys.stderr)
            if result.stderr:
                print(result.stderr[:500], file=sys.stderr)
            if result.stdout:
                print(result.stdout[:500], file=sys.stderr)
            return 1
    except subprocess.TimeoutExpired:
        print("[bridge] Attestation timed out", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[bridge] Attestation error: {e}", file=sys.stderr)
        return 1


def cmd_resolve(args) -> int:
    """Resolve a did:at: identity to its on-chain attestation by querying the chain directly.

    Unlike 'verify' (which checks the local keyring), 'resolve' fetches the live
    attestation claim from the chain — proving identity binding without trusting the
    local registry file. This is the canonical trust path for fleet-scale operation.

    Primary path: query the stored attestation txhash to find the fact_id, then fetch the fact.
    Fallback: scan recent facts (may miss old claims in long-running chains).
    """
    registry = load_registry()
    all_entries = {**registry.get("fleet_agents", {}), **registry.get("system_accounts", {})}

    # Accept either bare instance name or did:at:<name>
    target = args.instance
    if target.startswith("did:at:"):
        target = target[len("did:at:"):]

    entry = all_entries.get(target)
    if not entry:
        print(f"[bridge] {target} not in local registry", file=sys.stderr)
        return 1

    address = entry.get("lgm_address", "")
    did_at = entry.get("did_at", f"did:at:{target}")
    did_lgm = entry.get("did_lgm", f"did:lgm:{address}")
    txhash = entry.get("attestation_txhash", "")

    attestation = None

    # Primary: use stored fact_id — fact lives in module state, survives tx pruning and restarts
    stored_fact_id = entry.get("attestation_fact_id", "")
    if stored_fact_id:
        try:
            fr = subprocess.run(
                [str(LEGBLED), "query", "knowledge", "fact", stored_fact_id,
                 "--home", str(DEVNET_HOME), "-o", "json"],
                capture_output=True, text=True, timeout=10
            )
            if fr.returncode == 0:
                fact_data = json.loads(fr.stdout)
                attestation = fact_data.get("fact")
        except Exception:
            pass

    # Secondary: query by txhash → extract fact_id from tx events → fetch fact
    # (only works if tx index has not been pruned; dead code on long-running devnets)
    if attestation is None and txhash:
        try:
            r = subprocess.run(
                [str(LEGBLED), "query", "tx", txhash,
                 "--home", str(DEVNET_HOME), "-o", "json"],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                tx_data = json.loads(r.stdout)
                # Extract fact_id from tx events
                fact_id = None
                for event in tx_data.get("events", []):
                    for attr in event.get("attributes", []):
                        if attr.get("key") == "fact_id":
                            fact_id = attr.get("value", "")
                            break
                    if fact_id:
                        break

                if fact_id:
                    # Fetch the fact directly by ID
                    fr = subprocess.run(
                        [str(LEGBLED), "query", "knowledge", "fact", fact_id,
                         "--home", str(DEVNET_HOME), "-o", "json"],
                        capture_output=True, text=True, timeout=10
                    )
                    if fr.returncode == 0:
                        fact_data = json.loads(fr.stdout)
                        attestation = fact_data.get("fact")
        except Exception:
            pass  # fall through to scan

    # Fallback: scan recent facts (unreliable for long-running chains)
    if attestation is None:
        try:
            result = subprocess.run(
                [str(LEGBLED), "query", "knowledge", "facts",
                 "--home", str(DEVNET_HOME), "--limit", "200", "-o", "json"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for fact in data.get("facts", []):
                    if (fact.get("submitter") == address and
                            "Agent identity attestation" in fact.get("claim_text", "") and
                            did_at in fact.get("claim_text", "")):
                        attestation = fact
                        break
        except Exception:
            pass

    if attestation is None:
        print(f"[bridge] UNRESOLVED: no on-chain attestation found for {did_at} at {address}")
        if txhash:
            print(f"  Tried txhash: {txhash[:16]}...")
        print(f"  Run 'bridge.py backfill --instance {target}' to attempt deep scan.")
        return 1

    resolved_fact_id = attestation.get("id", "")
    status = attestation.get("status", "unknown")
    confidence = int(attestation.get("confidence", 0))
    height = attestation.get("created_at_height", "?")

    # Opportunistically backfill fact_id into registry if it was missing
    if resolved_fact_id and not stored_fact_id:
        try:
            write_reg = load_registry()
            all_write = {**write_reg.get("fleet_agents", {}), **write_reg.get("system_accounts", {})}
            if target in all_write:
                all_write[target]["attestation_fact_id"] = resolved_fact_id
                if target in write_reg.get("fleet_agents", {}):
                    write_reg["fleet_agents"][target]["attestation_fact_id"] = resolved_fact_id
                else:
                    write_reg["system_accounts"][target]["attestation_fact_id"] = resolved_fact_id
                save_registry(write_reg)
                print(f"[bridge] backfilled fact_id into registry for {target}")
        except Exception:
            pass

    print(f"[bridge] RESOLVED: {did_at} ↔ {did_lgm}")
    print(f"  fact_id:    {resolved_fact_id[:16]}...")
    print(f"  status:     {status}")
    print(f"  confidence: {confidence / 1_000_000:.4f}")
    print(f"  height:     {height}")
    print(f"  submitter:  {address}")
    return 0


def cmd_backfill(args) -> int:
    """Recover attestation_fact_id for agents where it is missing.

    Scans the knowledge module with a large limit to find old attestation facts.
    Needed for all 9 current agents attested before fact_id storage was implemented.
    Pass --instance to target one agent; omit to scan all fleet agents missing fact_id.
    """
    registry = load_registry()
    fleet = registry.get("fleet_agents", {})

    targets = {}
    if args.instance:
        if args.instance not in fleet:
            print(f"[bridge] {args.instance} not in fleet registry", file=sys.stderr)
            return 1
        targets = {args.instance: fleet[args.instance]}
    else:
        targets = {k: v for k, v in fleet.items() if not v.get("attestation_fact_id")}

    if not targets:
        print("[bridge] All fleet agents already have attestation_fact_id — nothing to backfill")
        return 0

    print(f"[bridge] Backfilling fact_id for {len(targets)} agent(s) — scanning with limit=2000...")
    changed = 0
    for name, entry in targets.items():
        address = entry.get("lgm_address", "")
        did_at = entry.get("did_at", f"did:at:{name}")
        fact = _find_attestation_fact(address, did_at, limit=2000)
        if fact:
            fact_id = fact.get("id", "")
            registry["fleet_agents"][name]["attestation_fact_id"] = fact_id
            print(f"  {name:<12} FOUND  fact_id={fact_id[:16]}...")
            changed += 1
        else:
            print(f"  {name:<12} MISS   (attestation not in top 2000 facts)")

    if changed:
        save_registry(registry)
        print(f"[bridge] Backfilled {changed}/{len(targets)} agents")

    return 0 if changed == len(targets) else 1


def cmd_list(args) -> int:
    registry = load_registry()
    fleet = registry.get("fleet_agents", {})
    system = registry.get("system_accounts", {})

    print("=== Fleet Agents ===")
    for name, entry in fleet.items():
        wall = entry.get("wall", "?")
        role = entry.get("role", "?")
        addr = entry.get("lgm_address", "?")
        print(f"  {name:<12} wall={wall}  {addr[:20]}...  {role}")

    print("\n=== System Accounts ===")
    for name, entry in system.items():
        addr = entry.get("lgm_address", "?")
        role = entry.get("role", "?")
        print(f"  {name:<12}  {addr[:20]}...  {role}")

    print(f"\nTotal: {len(fleet)} fleet agents, {len(system)} system accounts")
    return 0


def main():
    parser = argparse.ArgumentParser(description="AgentTool ↔ Zerone identity bridge")
    sub = parser.add_subparsers(dest="command")

    p_reg = sub.add_parser("register", help="Register a new agent in the bridge")
    p_reg.add_argument("--instance", required=True, help="Instance name (e.g. 'herald')")
    p_reg.add_argument("--address", required=True, help="lgm1... chain address")
    p_reg.add_argument("--role", required=True, help="Agent role description")
    p_reg.add_argument("--wall", type=int, required=True, help="Wall number (1-7)")

    p_look = sub.add_parser("lookup", help="Look up a bridge entry")
    p_look.add_argument("--instance", help="Instance name")
    p_look.add_argument("--address", help="lgm1... address")

    p_ver = sub.add_parser("verify", help="Verify keyring matches registry")
    p_ver.add_argument("--instance", required=True)

    p_att = sub.add_parser("attest", help="Submit on-chain identity attestation from agent's own key")
    p_att.add_argument("--instance", required=True, help="Instance name to attest")

    p_res = sub.add_parser("resolve", help="Verify identity binding by querying the chain directly (no local registry trust)")
    p_res.add_argument("--instance", required=True, help="Instance name or did:at:<name>")

    p_backfill = sub.add_parser("backfill", help="Recover attestation_fact_id for agents missing it")
    p_backfill.add_argument("--instance", help="Target one agent (omit to process all missing)")

    sub.add_parser("list", help="List all registered agents")
    sub.add_parser("status", help="Fleet health report: keyring + balance for each agent")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    dispatch = {
        "register": cmd_register,
        "lookup": cmd_lookup,
        "verify": cmd_verify,
        "attest": cmd_attest,
        "resolve": cmd_resolve,
        "backfill": cmd_backfill,
        "list": cmd_list,
        "status": cmd_status,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
