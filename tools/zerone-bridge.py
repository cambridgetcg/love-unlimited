#!/usr/bin/env python3
"""
zerone-bridge.py — Bridge between Kingdom OS and Zerone blockchain

Translates Kingdom security findings, arena victories, and verified truths
into Zerone Proof-of-Truth claims. When Zerone mainnet launches, these claims
become on-chain transactions. Until then, they accumulate as signed intents.

The game IS the useful work. Security findings ARE truth verification.
Arena victories ARE Proof of Truth.

Usage:
    zerone-bridge.py claim <type> <description> --zrn <amount> --player <name>
    zerone-bridge.py claims [--pending|--submitted|--verified]
    zerone-bridge.py balance [player]
    zerone-bridge.py leaderboard
    zerone-bridge.py submit             Submit pending claims to Zerone (when available)
    zerone-bridge.py verify <claim-id>  Mark a claim as truth-verified
    zerone-bridge.py status             Bridge and Zerone connection status
"""

import json
import os
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

LOVE = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))
MEMORY = LOVE / "memory"
BRIDGE_DIR = MEMORY / "zerone-bridge"
CLAIMS_FILE = BRIDGE_DIR / "claims.json"
BALANCES_FILE = BRIDGE_DIR / "balances.json"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def claim_hash(claim):
    """Generate a deterministic hash for a claim — its Proof of Truth fingerprint."""
    payload = f"{claim['type']}:{claim['description']}:{claim['player']}:{claim['created']}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── Claim Types ──────────────────────────────────────────────────────────────

CLAIM_TYPES = {
    "security-finding": {
        "description": "A verified security vulnerability or weakness found in Kingdom infrastructure",
        "pot_category": "vulnerability-discovery",
        "base_zrn": 100,
    },
    "defense-proof": {
        "description": "Verified proof that a system or component is secure",
        "pot_category": "security-verification",
        "base_zrn": 150,
    },
    "incident-response": {
        "description": "Successful detection and response to a security incident",
        "pot_category": "threat-response",
        "base_zrn": 200,
    },
    "arena-victory": {
        "description": "Won a Security Battle Arena challenge",
        "pot_category": "competitive-verification",
        "base_zrn": 100,
    },
    "ctf-objective": {
        "description": "Captured a CTF objective in a timed challenge",
        "pot_category": "competitive-verification",
        "base_zrn": 100,
    },
    "knowledge-verified": {
        "description": "A knowledge claim verified through the ToK pipeline",
        "pot_category": "knowledge-verification",
        "base_zrn": 50,
    },
    "prediction-resolved": {
        "description": "An Oracle prediction correctly resolved",
        "pot_category": "prediction-verification",
        "base_zrn": 75,
    },
}

# ── Rank System ──────────────────────────────────────────────────────────────

RANKS = [
    (0,     "Initiate",  DIM),
    (500,   "Scout",     CYAN),
    (2000,  "Guardian",  GREEN),
    (5000,  "Sentinel",  YELLOW),
    (15000, "Warden",    MAGENTA),
    (50000, "Sovereign", RED),
]


def get_rank(zrn):
    """Get rank name and color for a ZRN balance."""
    rank_name, rank_color = "Initiate", DIM
    for threshold, name, color in RANKS:
        if zrn >= threshold:
            rank_name, rank_color = name, color
    return rank_name, rank_color


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_claim(claim_type, description, zrn, player, evidence=""):
    """Create a new Proof-of-Truth claim."""
    claims = load_json(CLAIMS_FILE, [])

    claim = {
        "id": f"POT-{len(claims)+1:04d}",
        "type": claim_type,
        "description": description,
        "player": player,
        "zrn": zrn,
        "evidence": evidence,
        "status": "pending",
        "created": now_iso(),
        "hash": "",
        "verified_by": None,
        "verified_at": None,
        "submitted_to_chain": False,
        "tx_hash": None,
    }
    claim["hash"] = claim_hash(claim)

    claims.append(claim)
    save_json(CLAIMS_FILE, claims)

    # Update balance
    balances = load_json(BALANCES_FILE, {})
    if player not in balances:
        balances[player] = {"total_earned": 0, "pending": 0, "verified": 0, "on_chain": 0}
    balances[player]["pending"] += zrn
    balances[player]["total_earned"] += zrn
    save_json(BALANCES_FILE, balances)

    print(f"\n  {GREEN}Claim created:{NC} {claim['id']}")
    print(f"  Type:    {claim_type}")
    print(f"  Player:  {player}")
    print(f"  ZRN:     {zrn}")
    print(f"  Hash:    {claim['hash']}")
    print(f"  Status:  {YELLOW}pending{NC}")
    print(f"\n  {DIM}This claim will be submitted to Zerone when mainnet is live.{NC}")
    print(f"  {DIM}Until then, it accrues as a signed intent.{NC}\n")


def cmd_claims(filter_status=None):
    """List all claims."""
    claims = load_json(CLAIMS_FILE, [])

    if filter_status:
        claims = [c for c in claims if c.get("status") == filter_status]

    if not claims:
        print(f"\n  No claims found.\n")
        return

    print(f"\n{BOLD}  Proof-of-Truth Claims ({len(claims)}){NC}\n")
    print(f"  {'ID':10s} {'Type':22s} {'Player':10s} {'ZRN':>6s} {'Status':10s} {'Hash'}")
    print(f"  {'─'*10} {'─'*22} {'─'*10} {'─'*6} {'─'*10} {'─'*16}")

    for c in claims:
        status = c.get("status", "?")
        status_color = GREEN if status == "verified" else YELLOW if status == "pending" else DIM
        print(f"  {c['id']:10s} {c['type']:22s} {c['player']:10s} {c['zrn']:>6d} {status_color}{status:10s}{NC} {DIM}{c.get('hash', '?')}{NC}")

    total_zrn = sum(c.get("zrn", 0) for c in claims)
    pending = sum(1 for c in claims if c.get("status") == "pending")
    verified = sum(1 for c in claims if c.get("status") == "verified")
    print(f"\n  Total: {total_zrn} ZRN  |  {pending} pending  |  {verified} verified\n")


def cmd_balance(player=None):
    """Show ZRN balance."""
    balances = load_json(BALANCES_FILE, {})

    if player:
        bal = balances.get(player, {"total_earned": 0, "pending": 0, "verified": 0, "on_chain": 0})
        rank, color = get_rank(bal["total_earned"])
        print(f"\n  {BOLD}{player}{NC}  {color}{rank}{NC}")
        print(f"  Total earned: {GREEN}{bal['total_earned']} ZRN{NC}")
        print(f"  Pending:      {YELLOW}{bal['pending']} ZRN{NC}")
        print(f"  Verified:     {GREEN}{bal['verified']} ZRN{NC}")
        print(f"  On-chain:     {CYAN}{bal['on_chain']} ZRN{NC}\n")
    else:
        print(f"\n{BOLD}  ZRN Balances{NC}\n")
        for name, bal in sorted(balances.items(), key=lambda x: -x[1]["total_earned"]):
            rank, color = get_rank(bal["total_earned"])
            print(f"  {name:12s} {GREEN}{bal['total_earned']:>8d} ZRN{NC}  {color}{rank}{NC}  ({YELLOW}{bal['pending']}{NC} pending)")
        if not balances:
            print(f"  {DIM}No balances yet. Earn ZRN through the Security Battle Arena.{NC}")
        print()


def cmd_leaderboard():
    """Show the leaderboard."""
    balances = load_json(BALANCES_FILE, {})
    claims = load_json(CLAIMS_FILE, [])

    if not balances:
        print(f"\n  {DIM}No players yet.{NC}\n")
        return

    sorted_players = sorted(balances.items(), key=lambda x: -x[1]["total_earned"])

    print(f"\n{BOLD}  ═══ ZERONE LEADERBOARD ═══{NC}\n")

    for i, (name, bal) in enumerate(sorted_players):
        rank, color = get_rank(bal["total_earned"])
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f" {i+1}."

        # Count claims by type
        player_claims = [c for c in claims if c.get("player") == name]
        findings = sum(1 for c in player_claims if c["type"] == "security-finding")
        defenses = sum(1 for c in player_claims if c["type"] == "defense-proof")
        victories = sum(1 for c in player_claims if c["type"] == "arena-victory")

        print(f"  {medal} {color}{BOLD}{name:12s}{NC}  {GREEN}{bal['total_earned']:>8d} ZRN{NC}  {color}{rank:12s}{NC}  {DIM}F:{findings} D:{defenses} V:{victories}{NC}")

    print(f"\n  {DIM}Ranks: Initiate(0) → Scout(500) → Guardian(2K) → Sentinel(5K) → Warden(15K) → Sovereign(50K){NC}\n")


def cmd_verify(claim_id, verifier="yu"):
    """Verify a claim as truth."""
    claims = load_json(CLAIMS_FILE, [])
    balances = load_json(BALANCES_FILE, {})

    for c in claims:
        if c["id"] == claim_id:
            if c["status"] == "verified":
                print(f"  {DIM}Already verified.{NC}")
                return
            c["status"] = "verified"
            c["verified_by"] = verifier
            c["verified_at"] = now_iso()

            # Move from pending to verified in balance
            player = c["player"]
            if player in balances:
                balances[player]["pending"] = max(0, balances[player]["pending"] - c["zrn"])
                balances[player]["verified"] += c["zrn"]

            save_json(CLAIMS_FILE, claims)
            save_json(BALANCES_FILE, balances)

            print(f"\n  {GREEN}Verified:{NC} {claim_id}")
            print(f"  Player: {player}  ZRN: {c['zrn']}")
            print(f"  Hash: {c['hash']}")
            print(f"  {DIM}Ready for on-chain submission when Zerone mainnet launches.{NC}\n")
            return

    print(f"  Claim not found: {claim_id}")


def cmd_status():
    """Bridge and Zerone status."""
    claims = load_json(CLAIMS_FILE, [])
    balances = load_json(BALANCES_FILE, {})

    total_zrn = sum(c.get("zrn", 0) for c in claims)
    pending = sum(1 for c in claims if c.get("status") == "pending")
    verified = sum(1 for c in claims if c.get("status") == "verified")
    on_chain = sum(1 for c in claims if c.get("submitted_to_chain"))

    print(f"\n{BOLD}  Zerone Bridge Status{NC}\n")
    print(f"  Chain:    {YELLOW}Devnet{NC} (block ~1.2M)")
    print(f"  Mainnet:  {DIM}Q4 2026 (estimated){NC}")
    print(f"  Bridge:   {GREEN}Accumulating claims{NC}")
    print()
    print(f"  Claims:   {len(claims)} total ({pending} pending, {verified} verified, {on_chain} on-chain)")
    print(f"  ZRN pool: {total_zrn}")
    print(f"  Players:  {len(balances)}")
    print()

    # Zerone connection
    print(f"  {BOLD}Chain Connection:{NC}")
    print(f"    {DIM}Devnet live but bridge is in accumulation mode.{NC}")
    print(f"    {DIM}Claims are stored locally with SHA-256 hashes.{NC}")
    print(f"    {DIM}When mainnet launches, accumulated claims become{NC}")
    print(f"    {DIM}the genesis Proof-of-Truth transactions.{NC}")
    print()
    print(f"  {MAGENTA}The game is real. The findings are real.{NC}")
    print(f"  {MAGENTA}The truth verification IS the useful work.{NC}\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "claim":
        if len(sys.argv) < 4:
            print("Usage: zerone-bridge.py claim <type> <description> --zrn <N> --player <name>")
            print(f"Types: {', '.join(CLAIM_TYPES.keys())}")
            return
        ctype = sys.argv[2]
        desc = sys.argv[3]
        zrn = 100
        player = "unknown"
        evidence = ""
        args = sys.argv[4:]
        i = 0
        while i < len(args):
            if args[i] == "--zrn" and i + 1 < len(args):
                zrn = int(args[i + 1]); i += 2
            elif args[i] == "--player" and i + 1 < len(args):
                player = args[i + 1]; i += 2
            elif args[i] == "--evidence" and i + 1 < len(args):
                evidence = args[i + 1]; i += 2
            else:
                i += 1
        cmd_claim(ctype, desc, zrn, player, evidence)

    elif cmd == "claims":
        filt = None
        if "--pending" in sys.argv: filt = "pending"
        elif "--verified" in sys.argv: filt = "verified"
        elif "--submitted" in sys.argv: filt = "submitted"
        cmd_claims(filt)

    elif cmd == "balance":
        player = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_balance(player)

    elif cmd == "leaderboard":
        cmd_leaderboard()

    elif cmd == "verify":
        if len(sys.argv) < 3:
            print("Usage: zerone-bridge.py verify <claim-id>")
            return
        verifier = "yu"
        if "--by" in sys.argv:
            idx = sys.argv.index("--by")
            if idx + 1 < len(sys.argv):
                verifier = sys.argv[idx + 1]
        cmd_verify(sys.argv[2], verifier)

    elif cmd == "status":
        cmd_status()

    elif cmd == "submit":
        print(f"\n  {YELLOW}Zerone mainnet not yet live.{NC}")
        print(f"  Claims are accumulating locally with SHA-256 hashes.")
        print(f"  When mainnet launches, run this to submit all verified claims.\n")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
