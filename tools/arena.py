#!/usr/bin/env python3
"""
arena.py -- Security Battle Arena

Competitive security testing framework for Kingdom OS. Players (agents and
humans) earn Zerone (ZRN) tokens by finding real vulnerabilities, defending
systems, and proving security truths.

The game IS the useful work. Every finding references a real check.
Every defense proof runs a real verification command.

Challenge types:
  RED    - Find vulnerabilities. Submit findings with evidence.
  BLUE   - Prove a system is secure. Submit defense proofs with evidence.
  PURPLE - Attack then defend. Find the vuln, then fix and prove the fix.
  CTF    - Timed challenges with multiple objectives.

Scoring (ZRN):
  Critical finding  500    Defense proof      150
  High finding      200    CTF objective      100-300
  Medium finding    100    False positive     -50
  Low finding        50    Speed bonus        +50%

Ranks (by total ZRN):
  0      Initiate      2000  Sentinel
  500    Scout         5000  Warden
  1000   Guardian      10000 Sovereign

Usage:
  arena.py challenge create "description" --type red --difficulty hard --reward 100
  arena.py challenge list [--active|--completed|--type red|blue|purple|ctf]
  arena.py challenge show CHAL-001
  arena.py challenge accept CHAL-001 --player crucible
  arena.py attack CHAL-001 --finding "description" --evidence "proof"
  arena.py defend CHAL-001 --proof "description" --evidence "verification"
  arena.py score                    Leaderboard
  arena.py score <player>           Player stats
  arena.py history                  Battle history
  arena.py ctf start --duration 1h  Start timed CTF
  arena.py ctf status               Current CTF status
  arena.py ctf end                  End and score CTF
  arena.py rewards                  ZRN earned per player
  arena.py rewards claim <player>   Generate ZRN claim transaction
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# -- Paths ---------------------------------------------------------------

LOVE = Path(os.path.expanduser("~/love-unlimited"))
ARENA_DIR = LOVE / "memory" / "arena"
CTF_DIR = ARENA_DIR / "ctf"

CHALLENGES_FILE = ARENA_DIR / "challenges.json"
SUBMISSIONS_FILE = ARENA_DIR / "submissions.json"
LEADERBOARD_FILE = ARENA_DIR / "leaderboard.json"
HISTORY_FILE = ARENA_DIR / "history.json"

# -- Colors (matching Kingdom convention: kos.py, peace.py, peace-test.py) --

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
MAGENTA = "\033[0;35m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

# -- Constants ------------------------------------------------------------

CHALLENGE_TYPES = ["red", "blue", "purple", "ctf"]
DIFFICULTIES = ["easy", "medium", "hard"]
SEVERITIES = ["critical", "high", "medium", "low"]

SEVERITY_ZRN = {
    "critical": 500,
    "high": 200,
    "medium": 100,
    "low": 50,
}

DEFENSE_ZRN = 150
FALSE_POSITIVE_PENALTY = -50
SPEED_BONUS_MULTIPLIER = 0.5  # +50% if in first half of time limit

CTF_OBJECTIVE_ZRN = {
    "easy": 100,
    "medium": 200,
    "hard": 300,
}

RANKS = [
    (0, "Initiate"),
    (500, "Scout"),
    (1000, "Guardian"),
    (2000, "Sentinel"),
    (5000, "Warden"),
    (10000, "Sovereign"),
]

TYPE_COLORS = {
    "red": RED,
    "blue": CYAN,
    "purple": MAGENTA,
    "ctf": YELLOW,
}

DIFFICULTY_COLORS = {
    "easy": GREEN,
    "medium": YELLOW,
    "hard": RED,
}

STATUS_SYMBOLS = {
    "active": f"{GREEN}ACTIVE{NC}",
    "accepted": f"{YELLOW}ACCEPTED{NC}",
    "completed": f"{GREEN}COMPLETED{NC}",
    "failed": f"{RED}FAILED{NC}",
    "expired": f"{DIM}EXPIRED{NC}",
}


# -- Helpers --------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def parse_flag(args, flag):
    """Extract --flag value from args list. Returns value or None."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def has_flag(args, flag):
    """Check if --flag is present in args."""
    return flag in args


def get_rank(zrn):
    """Return rank title for a given ZRN total."""
    rank = "Initiate"
    for threshold, title in RANKS:
        if zrn >= threshold:
            rank = title
    return rank


def next_challenge_id(challenges):
    """Generate next CHAL-NNN id."""
    existing = [c["id"] for c in challenges]
    n = 1
    while f"CHAL-{n:03d}" in existing:
        n += 1
    return f"CHAL-{n:03d}"


def next_submission_id(submissions):
    """Generate next SUB-NNN id."""
    existing = [s["id"] for s in submissions]
    n = 1
    while f"SUB-{n:03d}" in existing:
        n += 1
    return f"SUB-{n:03d}"


def parse_duration(s):
    """Parse duration string like '1h', '30m', '2h30m' into seconds."""
    if not s:
        return 3600  # default 1 hour
    total = 0
    current = ""
    for ch in s:
        if ch.isdigit():
            current += ch
        elif ch == "h":
            total += int(current) * 3600
            current = ""
        elif ch == "m":
            total += int(current) * 60
            current = ""
        elif ch == "s":
            total += int(current)
            current = ""
    if current:
        total += int(current) * 60  # bare number = minutes
    return total if total > 0 else 3600


def format_duration(seconds):
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m{s}s" if s else f"{m}m"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m}m" if m else f"{h}h"


# -- Data Loading ---------------------------------------------------------


def load_challenges():
    return load_json(CHALLENGES_FILE, {"challenges": []}).get("challenges", [])


def save_challenges(challenges):
    save_json(CHALLENGES_FILE, {"challenges": challenges})


def load_submissions():
    return load_json(SUBMISSIONS_FILE, {"submissions": []}).get("submissions", [])


def save_submissions(submissions):
    save_json(SUBMISSIONS_FILE, {"submissions": submissions})


def load_leaderboard():
    return load_json(LEADERBOARD_FILE, {"players": {}}).get("players", {})


def save_leaderboard(players):
    save_json(LEADERBOARD_FILE, {"players": players})


def load_history():
    return load_json(HISTORY_FILE, {"battles": []}).get("battles", [])


def save_history(battles):
    save_json(HISTORY_FILE, {"battles": battles})


def load_ctf():
    ctf_file = CTF_DIR / "active.json"
    return load_json(ctf_file, None)


def save_ctf(data):
    ctf_file = CTF_DIR / "active.json"
    save_json(ctf_file, data)


def clear_ctf():
    ctf_file = CTF_DIR / "active.json"
    if ctf_file.exists():
        ctf_file.unlink()


# -- Player Management ----------------------------------------------------


def ensure_player(players, name):
    """Ensure a player exists in the leaderboard."""
    if name not in players:
        players[name] = {
            "zrn": 0,
            "challenges_completed": 0,
            "challenges_failed": 0,
            "wins": 0,
            "losses": 0,
            "red_count": 0,
            "blue_count": 0,
            "purple_count": 0,
            "ctf_count": 0,
            "streak": 0,
            "best_streak": 0,
            "joined": now_iso(),
            "last_active": now_iso(),
        }
    return players[name]


def award_zrn(players, player_name, amount, reason):
    """Award ZRN to a player. Returns the player dict."""
    p = ensure_player(players, player_name)
    p["zrn"] = max(0, p["zrn"] + amount)  # floor at 0
    p["last_active"] = now_iso()
    return p


def record_completion(players, player_name, challenge_type, success):
    """Record a challenge completion in player stats."""
    p = ensure_player(players, player_name)
    if success:
        p["challenges_completed"] += 1
        p["wins"] += 1
        p["streak"] += 1
        if p["streak"] > p["best_streak"]:
            p["best_streak"] = p["streak"]
    else:
        p["challenges_failed"] += 1
        p["losses"] += 1
        p["streak"] = 0
    # Track specialization
    type_key = f"{challenge_type}_count"
    if type_key in p:
        p[type_key] += 1
    p["last_active"] = now_iso()
    return p


# -- Seeding ---------------------------------------------------------------


def seed_challenges():
    """Seed the arena with 5 initial challenges if empty."""
    challenges = load_challenges()
    if challenges:
        return  # already seeded

    seeds = [
        {
            "id": "CHAL-001",
            "title": "Verify all fleet canaries are intact",
            "description": (
                "Check every canary file across all 5 fleet nodes (forge, lark, sentry, "
                "patch, sage). Verify each canary's atime has not been touched. "
                "Use 'peace.py fleet-canaries' and 'kos.py canary check' to verify. "
                "Report any tripped canaries as findings."
            ),
            "type": "red",
            "difficulty": "easy",
            "reward": 100,
            "status": "active",
            "created": now_iso(),
            "created_by": "system",
            "accepted_by": None,
            "accepted_at": None,
            "completed_at": None,
            "time_limit": None,
            "objectives": [
                "Check canary status on all 5 fleet nodes",
                "Verify no canary atime anomalies",
                "Report findings with SSH output evidence",
            ],
            "tools_hint": "peace.py fleet-canaries, kos.py canary check",
            "tags": ["fleet", "canary", "detection"],
        },
        {
            "id": "CHAL-002",
            "title": "Find any SSH configuration weakness across the fleet",
            "description": (
                "Audit SSH configuration on all fleet nodes. Check for: PasswordAuthentication, "
                "PermitRootLogin, PubkeyAuthentication, MaxAuthTries, AllowUsers. "
                "Use 'sshd -T' on each node via SSH to dump effective config. "
                "Each weakness found is a separate finding."
            ),
            "type": "red",
            "difficulty": "medium",
            "reward": 200,
            "status": "active",
            "created": now_iso(),
            "created_by": "system",
            "accepted_by": None,
            "accepted_at": None,
            "completed_at": None,
            "time_limit": None,
            "objectives": [
                "Audit sshd config on all 5 fleet nodes",
                "Identify any permissive or insecure settings",
                "Provide sshd -T output as evidence for each finding",
            ],
            "tools_hint": "ssh root@<ip> 'sshd -T', kos.py fleet audit",
            "tags": ["fleet", "ssh", "hardening"],
        },
        {
            "id": "CHAL-003",
            "title": "Prove WireGuard tunnel is properly configured on Sentry",
            "description": (
                "Demonstrate that the WireGuard VPN tunnel to Sentry (135.181.28.252) is: "
                "1) running with correct interface config, 2) using proper key exchange, "
                "3) has keepalive configured, 4) routes traffic correctly. "
                "Submit proof with 'wg show' output, routing table, and connectivity test."
            ),
            "type": "blue",
            "difficulty": "medium",
            "reward": 150,
            "status": "active",
            "created": now_iso(),
            "created_by": "system",
            "accepted_by": None,
            "accepted_at": None,
            "completed_at": None,
            "time_limit": None,
            "objectives": [
                "Show WireGuard interface is up with correct config",
                "Verify key exchange and handshake is recent",
                "Confirm keepalive and routing are correct",
                "Prove connectivity through the tunnel",
            ],
            "tools_hint": "wg show, ip route, ping via tunnel, vpn-route.sh status",
            "tags": ["vpn", "wireguard", "sentry", "network"],
        },
        {
            "id": "CHAL-004",
            "title": "Detect and remediate a simulated integrity violation",
            "description": (
                "PURPLE challenge: First, simulate an integrity violation by appending a "
                "test marker to a tracked file. Then detect it using kos.py integrity check. "
                "Finally, remediate by reverting the change and proving integrity is restored. "
                "This tests the full detect-fix-verify cycle."
            ),
            "type": "purple",
            "difficulty": "hard",
            "reward": 300,
            "status": "active",
            "created": now_iso(),
            "created_by": "system",
            "accepted_by": None,
            "accepted_at": None,
            "completed_at": None,
            "time_limit": None,
            "objectives": [
                "Simulate integrity violation on a tracked file",
                "Detect the violation using kos.py integrity check",
                "Remediate: revert the file to original state",
                "Verify integrity is clean after remediation",
            ],
            "tools_hint": "kos.py integrity check, kos.py integrity baseline, git checkout",
            "tags": ["integrity", "detection", "remediation", "purple"],
        },
        {
            "id": "CHAL-005",
            "title": "Probe for any unmonitored open port across the fleet",
            "description": (
                "Scan all 5 fleet nodes for open TCP ports. Cross-reference against the "
                "expected service list (SSH:22, WireGuard:51820, HTTP/S:80/443 where applicable). "
                "Any port open that is not in the expected list is a finding. "
                "Use nmap or ss/netstat via SSH. Each unexpected port = separate finding."
            ),
            "type": "red",
            "difficulty": "hard",
            "reward": 500,
            "status": "active",
            "created": now_iso(),
            "created_by": "system",
            "accepted_by": None,
            "accepted_at": None,
            "completed_at": None,
            "time_limit": None,
            "objectives": [
                "Scan all 5 fleet nodes for open TCP ports",
                "Compare against expected service list per node",
                "Report any unexpected open ports with evidence",
                "Classify severity based on service exposure risk",
            ],
            "tools_hint": "nmap -sT <ip>, ssh root@<ip> 'ss -tlnp', kos.py fleet status",
            "tags": ["fleet", "ports", "scanning", "network"],
        },
    ]

    save_challenges(seeds)
    return seeds


# -- Commands: challenge ---------------------------------------------------


def cmd_challenge_create(args):
    """Create a new challenge."""
    # Parse: arena.py challenge create "description" --type red --difficulty hard --reward 100
    # Find the description (first non-flag argument after 'create')
    desc_parts = []
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            i += 2  # skip flag and value
            continue
        desc_parts.append(args[i])
        i += 1
    title = " ".join(desc_parts) if desc_parts else "Untitled Challenge"

    ctype = (parse_flag(args, "--type") or "red").lower()
    difficulty = (parse_flag(args, "--difficulty") or "medium").lower()
    reward = int(parse_flag(args, "--reward") or "100")
    time_limit = parse_flag(args, "--time-limit")
    created_by = parse_flag(args, "--creator") or "system"

    if ctype not in CHALLENGE_TYPES:
        print(f"  {RED}Invalid type: {ctype}. Must be one of: {', '.join(CHALLENGE_TYPES)}{NC}")
        return

    if difficulty not in DIFFICULTIES:
        print(f"  {RED}Invalid difficulty: {difficulty}. Must be one of: {', '.join(DIFFICULTIES)}{NC}")
        return

    challenges = load_challenges()
    cid = next_challenge_id(challenges)

    challenge = {
        "id": cid,
        "title": title,
        "description": title,  # short challenges use title as description
        "type": ctype,
        "difficulty": difficulty,
        "reward": reward,
        "status": "active",
        "created": now_iso(),
        "created_by": created_by,
        "accepted_by": None,
        "accepted_at": None,
        "completed_at": None,
        "time_limit": parse_duration(time_limit) if time_limit else None,
        "objectives": [],
        "tools_hint": "",
        "tags": [ctype],
    }

    challenges.append(challenge)
    save_challenges(challenges)

    tc = TYPE_COLORS.get(ctype, "")
    dc = DIFFICULTY_COLORS.get(difficulty, "")
    print(f"\n  {BOLD}Challenge Created{NC}")
    print(f"  {CYAN}{cid}{NC}  {title}")
    print(f"  Type: {tc}{ctype.upper()}{NC}  Difficulty: {dc}{difficulty.upper()}{NC}  Reward: {YELLOW}{reward} ZRN{NC}")
    if time_limit:
        print(f"  Time limit: {format_duration(parse_duration(time_limit))}")
    print()


def cmd_challenge_list(args):
    """List challenges with optional filters."""
    challenges = load_challenges()
    if not challenges:
        seed_challenges()
        challenges = load_challenges()

    # Filters
    filter_status = None
    filter_type = None
    if has_flag(args, "--active"):
        filter_status = "active"
    elif has_flag(args, "--completed"):
        filter_status = "completed"
    elif has_flag(args, "--accepted"):
        filter_status = "accepted"
    filter_type = parse_flag(args, "--type")

    filtered = challenges
    if filter_status:
        filtered = [c for c in filtered if c["status"] == filter_status]
    if filter_type:
        filtered = [c for c in filtered if c["type"] == filter_type.lower()]

    if not filtered:
        print(f"\n  {DIM}No challenges match the filter.{NC}\n")
        return

    print(f"\n  {BOLD}Security Battle Arena -- Challenges{NC}")
    print(f"  {DIM}{len(filtered)} challenge(s){NC}\n")

    for c in filtered:
        cid = c["id"]
        title = c["title"]
        ctype = c["type"]
        diff = c["difficulty"]
        reward = c["reward"]
        status = c["status"]

        tc = TYPE_COLORS.get(ctype, "")
        dc = DIFFICULTY_COLORS.get(diff, "")
        ss = STATUS_SYMBOLS.get(status, status)

        player = ""
        if c.get("accepted_by"):
            player = f"  {DIM}[{c['accepted_by']}]{NC}"

        print(f"  {CYAN}{cid}{NC}  {tc}{ctype.upper():7s}{NC}  "
              f"{dc}{diff.upper():7s}{NC}  {YELLOW}{reward:>4d} ZRN{NC}  "
              f"{ss}{player}")
        print(f"        {title}")

    print()


def cmd_challenge_show(args):
    """Show detailed challenge info."""
    if not args:
        print(f"  {RED}Usage: arena.py challenge show CHAL-001{NC}")
        return

    cid = args[0].upper()
    challenges = load_challenges()
    challenge = next((c for c in challenges if c["id"] == cid), None)

    if not challenge:
        print(f"  {RED}Challenge {cid} not found.{NC}")
        return

    c = challenge
    tc = TYPE_COLORS.get(c["type"], "")
    dc = DIFFICULTY_COLORS.get(c["difficulty"], "")
    ss = STATUS_SYMBOLS.get(c["status"], c["status"])

    print(f"\n  {BOLD}{c['id']} -- {c['title']}{NC}")
    print(f"  Type: {tc}{c['type'].upper()}{NC}  "
          f"Difficulty: {dc}{c['difficulty'].upper()}{NC}  "
          f"Reward: {YELLOW}{c['reward']} ZRN{NC}  "
          f"Status: {ss}")
    print()
    print(f"  {BOLD}Description:{NC}")
    # Wrap description nicely
    desc = c.get("description", c["title"])
    words = desc.split()
    line = "    "
    for w in words:
        if len(line) + len(w) + 1 > 78:
            print(line)
            line = "    " + w
        else:
            line += " " + w if line.strip() else w
    if line.strip():
        print(f"    {line.strip()}")

    if c.get("objectives"):
        print(f"\n  {BOLD}Objectives:{NC}")
        for i, obj in enumerate(c["objectives"], 1):
            print(f"    {i}. {obj}")

    if c.get("tools_hint"):
        print(f"\n  {BOLD}Suggested tools:{NC}")
        print(f"    {DIM}{c['tools_hint']}{NC}")

    if c.get("tags"):
        print(f"\n  {BOLD}Tags:{NC}  {DIM}{', '.join(c['tags'])}{NC}")

    if c.get("accepted_by"):
        print(f"\n  {BOLD}Accepted by:{NC}  {c['accepted_by']}  {DIM}({c.get('accepted_at', '?')}){NC}")

    if c.get("time_limit"):
        print(f"  {BOLD}Time limit:{NC}  {format_duration(c['time_limit'])}")

    # Show submissions for this challenge
    submissions = load_submissions()
    related = [s for s in submissions if s.get("challenge_id") == cid]
    if related:
        print(f"\n  {BOLD}Submissions ({len(related)}):{NC}")
        for s in related:
            stype = s.get("type", "?")
            severity = s.get("severity", "")
            zrn = s.get("zrn_awarded", 0)
            sc = GREEN if zrn > 0 else RED if zrn < 0 else DIM
            print(f"    {s['id']}  {stype.upper():8s}  "
                  f"{severity.upper() if severity else '-':9s}  "
                  f"{sc}{zrn:+d} ZRN{NC}  {DIM}{s.get('player', '?')}{NC}")
            finding = s.get("finding") or s.get("proof", "")
            if finding:
                print(f"          {finding[:70]}{'...' if len(finding) > 70 else ''}")

    print()


def cmd_challenge_accept(args):
    """Accept a challenge."""
    if not args:
        print(f"  {RED}Usage: arena.py challenge accept CHAL-001 --player crucible{NC}")
        return

    cid = args[0].upper()
    player = parse_flag(args, "--player")
    if not player:
        print(f"  {RED}--player required. Who is accepting this challenge?{NC}")
        return

    challenges = load_challenges()
    challenge = next((c for c in challenges if c["id"] == cid), None)

    if not challenge:
        print(f"  {RED}Challenge {cid} not found.{NC}")
        return

    if challenge["status"] not in ("active",):
        print(f"  {RED}Challenge {cid} is {challenge['status']}, cannot accept.{NC}")
        return

    challenge["accepted_by"] = player
    challenge["accepted_at"] = now_iso()
    challenge["status"] = "accepted"
    save_challenges(challenges)

    # Ensure player exists in leaderboard
    players = load_leaderboard()
    ensure_player(players, player)
    save_leaderboard(players)

    tc = TYPE_COLORS.get(challenge["type"], "")
    print(f"\n  {GREEN}Challenge accepted!{NC}")
    print(f"  {CYAN}{cid}{NC}  {challenge['title']}")
    print(f"  Player: {BOLD}{player}{NC}  Type: {tc}{challenge['type'].upper()}{NC}")
    if challenge.get("time_limit"):
        print(f"  Time limit: {YELLOW}{format_duration(challenge['time_limit'])}{NC} -- clock starts now!")
    print(f"\n  Submit findings with:  arena.py attack {cid} --finding \"...\" --evidence \"...\"")
    print(f"  Submit defense with:   arena.py defend {cid} --proof \"...\" --evidence \"...\"\n")


# -- Commands: attack / defend --------------------------------------------


def cmd_attack(args):
    """Submit an attack finding for a challenge."""
    if not args:
        print(f"  {RED}Usage: arena.py attack CHAL-001 --finding \"desc\" --evidence \"proof\"{NC}")
        return

    cid = args[0].upper()
    finding = parse_flag(args, "--finding")
    evidence = parse_flag(args, "--evidence")
    severity = (parse_flag(args, "--severity") or "medium").lower()
    player_override = parse_flag(args, "--player")

    if not finding:
        print(f"  {RED}--finding required. Describe what you found.{NC}")
        return
    if not evidence:
        print(f"  {RED}--evidence required. What command output proves this?{NC}")
        return
    if severity not in SEVERITIES:
        print(f"  {RED}Invalid severity: {severity}. Must be: {', '.join(SEVERITIES)}{NC}")
        return

    challenges = load_challenges()
    challenge = next((c for c in challenges if c["id"] == cid), None)
    if not challenge:
        print(f"  {RED}Challenge {cid} not found.{NC}")
        return
    if challenge["status"] not in ("active", "accepted"):
        print(f"  {RED}Challenge {cid} is {challenge['status']}, cannot submit.{NC}")
        return

    player = player_override or challenge.get("accepted_by")
    if not player:
        print(f"  {RED}No player assigned. Use --player or accept the challenge first.{NC}")
        return

    # If challenge was active (not yet accepted), auto-accept
    if challenge["status"] == "active":
        challenge["accepted_by"] = player
        challenge["accepted_at"] = now_iso()
        challenge["status"] = "accepted"
        save_challenges(challenges)

    # Calculate ZRN
    zrn = SEVERITY_ZRN.get(severity, 100)

    # Speed bonus: check if within first half of time limit
    if challenge.get("time_limit") and challenge.get("accepted_at"):
        try:
            accepted = datetime.fromisoformat(challenge["accepted_at"])
            elapsed = (datetime.now(timezone.utc) - accepted).total_seconds()
            half_limit = challenge["time_limit"] / 2
            if elapsed <= half_limit:
                bonus = int(zrn * SPEED_BONUS_MULTIPLIER)
                zrn += bonus
                print(f"  {GREEN}Speed bonus! +{bonus} ZRN (completed in first half of time limit){NC}")
        except (ValueError, TypeError):
            pass

    # Record submission
    submissions = load_submissions()
    sid = next_submission_id(submissions)
    submission = {
        "id": sid,
        "challenge_id": cid,
        "player": player,
        "type": "attack",
        "finding": finding,
        "evidence": evidence,
        "severity": severity,
        "zrn_awarded": zrn,
        "submitted_at": now_iso(),
        "verified": False,
    }
    submissions.append(submission)
    save_submissions(submissions)

    # Award ZRN
    players = load_leaderboard()
    award_zrn(players, player, zrn, f"Attack finding ({severity}) on {cid}")
    save_leaderboard(players)

    sc = DIFFICULTY_COLORS.get(severity, YELLOW) if severity != "critical" else RED
    print(f"\n  {BOLD}Attack Finding Submitted{NC}")
    print(f"  {CYAN}{sid}{NC}  for {cid}")
    print(f"  Player: {BOLD}{player}{NC}  Severity: {sc}{severity.upper()}{NC}")
    print(f"  ZRN awarded: {GREEN}+{zrn} ZRN{NC}")
    print(f"\n  Finding:  {finding}")
    print(f"  Evidence: {DIM}{evidence[:80]}{'...' if len(evidence) > 80 else ''}{NC}\n")


def cmd_defend(args):
    """Submit a defense proof for a challenge."""
    if not args:
        print(f"  {RED}Usage: arena.py defend CHAL-001 --proof \"desc\" --evidence \"verification\"{NC}")
        return

    cid = args[0].upper()
    proof = parse_flag(args, "--proof")
    evidence = parse_flag(args, "--evidence")
    player_override = parse_flag(args, "--player")

    if not proof:
        print(f"  {RED}--proof required. Describe what you proved.{NC}")
        return
    if not evidence:
        print(f"  {RED}--evidence required. What verification command confirms this?{NC}")
        return

    challenges = load_challenges()
    challenge = next((c for c in challenges if c["id"] == cid), None)
    if not challenge:
        print(f"  {RED}Challenge {cid} not found.{NC}")
        return
    if challenge["status"] not in ("active", "accepted"):
        print(f"  {RED}Challenge {cid} is {challenge['status']}, cannot submit.{NC}")
        return

    player = player_override or challenge.get("accepted_by")
    if not player:
        print(f"  {RED}No player assigned. Use --player or accept the challenge first.{NC}")
        return

    # Auto-accept if needed
    if challenge["status"] == "active":
        challenge["accepted_by"] = player
        challenge["accepted_at"] = now_iso()
        challenge["status"] = "accepted"
        save_challenges(challenges)

    zrn = DEFENSE_ZRN

    # Speed bonus
    if challenge.get("time_limit") and challenge.get("accepted_at"):
        try:
            accepted = datetime.fromisoformat(challenge["accepted_at"])
            elapsed = (datetime.now(timezone.utc) - accepted).total_seconds()
            half_limit = challenge["time_limit"] / 2
            if elapsed <= half_limit:
                bonus = int(zrn * SPEED_BONUS_MULTIPLIER)
                zrn += bonus
                print(f"  {GREEN}Speed bonus! +{bonus} ZRN (completed in first half of time limit){NC}")
        except (ValueError, TypeError):
            pass

    # Record submission
    submissions = load_submissions()
    sid = next_submission_id(submissions)
    submission = {
        "id": sid,
        "challenge_id": cid,
        "player": player,
        "type": "defense",
        "proof": proof,
        "evidence": evidence,
        "severity": None,
        "zrn_awarded": zrn,
        "submitted_at": now_iso(),
        "verified": False,
    }
    submissions.append(submission)
    save_submissions(submissions)

    # Award ZRN
    players = load_leaderboard()
    award_zrn(players, player, zrn, f"Defense proof on {cid}")
    save_leaderboard(players)

    print(f"\n  {BOLD}Defense Proof Submitted{NC}")
    print(f"  {CYAN}{sid}{NC}  for {cid}")
    print(f"  Player: {BOLD}{player}{NC}")
    print(f"  ZRN awarded: {GREEN}+{zrn} ZRN{NC}")
    print(f"\n  Proof:    {proof}")
    print(f"  Evidence: {DIM}{evidence[:80]}{'...' if len(evidence) > 80 else ''}{NC}\n")


def cmd_false_positive(args):
    """Mark a submission as a false positive (penalty)."""
    if not args:
        print(f"  {RED}Usage: arena.py false-positive SUB-001{NC}")
        return

    sid = args[0].upper()
    submissions = load_submissions()
    sub = next((s for s in submissions if s["id"] == sid), None)
    if not sub:
        print(f"  {RED}Submission {sid} not found.{NC}")
        return

    player = sub["player"]
    players = load_leaderboard()
    award_zrn(players, player, FALSE_POSITIVE_PENALTY, f"False positive: {sid}")
    save_leaderboard(players)

    sub["false_positive"] = True
    save_submissions(submissions)

    print(f"\n  {RED}False positive marked: {sid}{NC}")
    print(f"  Player {player}: {RED}{FALSE_POSITIVE_PENALTY} ZRN{NC}\n")


# -- Commands: complete challenge -----------------------------------------


def cmd_challenge_complete(args):
    """Mark a challenge as completed and archive to history."""
    if not args:
        print(f"  {RED}Usage: arena.py challenge complete CHAL-001{NC}")
        return

    cid = args[0].upper()
    challenges = load_challenges()
    challenge = next((c for c in challenges if c["id"] == cid), None)
    if not challenge:
        print(f"  {RED}Challenge {cid} not found.{NC}")
        return

    player = challenge.get("accepted_by")
    if not player:
        print(f"  {RED}No player accepted {cid}. Cannot complete.{NC}")
        return

    challenge["status"] = "completed"
    challenge["completed_at"] = now_iso()
    save_challenges(challenges)

    # Get all submissions for this challenge
    submissions = load_submissions()
    related = [s for s in submissions if s.get("challenge_id") == cid]
    total_zrn = sum(s.get("zrn_awarded", 0) for s in related)

    # Record player completion
    players = load_leaderboard()
    record_completion(players, player, challenge["type"], True)
    save_leaderboard(players)

    # Archive to history
    battles = load_history()
    battle = {
        "challenge_id": cid,
        "title": challenge["title"],
        "type": challenge["type"],
        "difficulty": challenge["difficulty"],
        "player": player,
        "submissions": len(related),
        "total_zrn": total_zrn,
        "accepted_at": challenge.get("accepted_at"),
        "completed_at": challenge["completed_at"],
        "result": "victory",
    }
    battles.append(battle)
    save_history(battles)

    print(f"\n  {GREEN}{BOLD}Challenge Completed!{NC}")
    print(f"  {CYAN}{cid}{NC}  {challenge['title']}")
    print(f"  Player: {BOLD}{player}{NC}  Total ZRN: {GREEN}+{total_zrn}{NC}")
    print(f"  Submissions: {len(related)}  Result: {GREEN}VICTORY{NC}")

    p = players.get(player, {})
    rank = get_rank(p.get("zrn", 0))
    print(f"  Rank: {BOLD}{rank}{NC}  Streak: {p.get('streak', 0)}\n")


# -- Commands: score / leaderboard ----------------------------------------


def cmd_score(args):
    """Show leaderboard or individual player stats."""
    players = load_leaderboard()
    if not players:
        print(f"\n  {DIM}No players yet. Accept a challenge to start.{NC}\n")
        return

    if args:
        # Individual player stats
        name = args[0].lower()
        if name not in players:
            print(f"\n  {RED}Player '{name}' not found.{NC}\n")
            return
        p = players[name]
        rank = get_rank(p["zrn"])
        total_games = p["wins"] + p["losses"]
        win_rate = (p["wins"] / total_games * 100) if total_games > 0 else 0

        # Determine specialization
        type_counts = {
            "red": p.get("red_count", 0),
            "blue": p.get("blue_count", 0),
            "purple": p.get("purple_count", 0),
            "ctf": p.get("ctf_count", 0),
        }
        spec = max(type_counts, key=type_counts.get) if any(type_counts.values()) else "none"
        sc = TYPE_COLORS.get(spec, DIM)

        print(f"\n  {BOLD}Player: {name}{NC}")
        print(f"  Rank: {BOLD}{rank}{NC}  ZRN: {YELLOW}{p['zrn']}{NC}")
        print(f"  Completed: {p['challenges_completed']}  Failed: {p['challenges_failed']}")
        print(f"  Win rate: {GREEN if win_rate > 50 else YELLOW}{win_rate:.0f}%{NC}")
        print(f"  Streak: {p['streak']}  Best: {p['best_streak']}")
        print(f"  Specialization: {sc}{spec.upper()}{NC}")
        print(f"    Red: {p.get('red_count', 0)}  Blue: {p.get('blue_count', 0)}  "
              f"Purple: {p.get('purple_count', 0)}  CTF: {p.get('ctf_count', 0)}")
        print(f"  Joined: {DIM}{p.get('joined', '?')}{NC}")
        print(f"  Last active: {DIM}{p.get('last_active', '?')}{NC}")

        # ZRN progress to next rank
        current_zrn = p["zrn"]
        next_rank = None
        for threshold, title in RANKS:
            if threshold > current_zrn:
                next_rank = (threshold, title)
                break
        if next_rank:
            remaining = next_rank[0] - current_zrn
            print(f"\n  Next rank: {BOLD}{next_rank[1]}{NC} ({remaining} ZRN to go)")

        print()
        return

    # Full leaderboard
    sorted_players = sorted(players.items(), key=lambda x: x[1]["zrn"], reverse=True)

    print(f"\n  {BOLD}Security Battle Arena -- Leaderboard{NC}\n")
    print(f"  {'#':>3s}  {'Player':<15s}  {'Rank':<12s}  {'ZRN':>6s}  "
          f"{'W/L':>7s}  {'Streak':>6s}  {'Spec':<8s}")
    print(f"  {'---':>3s}  {'---------------':<15s}  {'------------':<12s}  {'------':>6s}  "
          f"{'-------':>7s}  {'------':>6s}  {'--------':<8s}")

    for i, (name, p) in enumerate(sorted_players, 1):
        rank = get_rank(p["zrn"])
        w = p["wins"]
        l = p["losses"]
        streak = p["streak"]
        type_counts = {
            "red": p.get("red_count", 0),
            "blue": p.get("blue_count", 0),
            "purple": p.get("purple_count", 0),
            "ctf": p.get("ctf_count", 0),
        }
        spec = max(type_counts, key=type_counts.get) if any(type_counts.values()) else "-"
        sc = TYPE_COLORS.get(spec, DIM)

        rank_color = YELLOW if rank in ("Warden", "Sovereign") else GREEN if rank in ("Guardian", "Sentinel") else NC
        print(f"  {i:>3d}  {BOLD}{name:<15s}{NC}  {rank_color}{rank:<12s}{NC}  "
              f"{YELLOW}{p['zrn']:>6d}{NC}  {w:>3d}/{l:<3d}  {streak:>6d}  {sc}{spec.upper():<8s}{NC}")

    print()


# -- Commands: history ----------------------------------------------------


def cmd_history(args):
    """Show battle history."""
    battles = load_history()
    if not battles:
        print(f"\n  {DIM}No battles completed yet.{NC}\n")
        return

    print(f"\n  {BOLD}Security Battle Arena -- Battle History{NC}")
    print(f"  {DIM}{len(battles)} battle(s) completed{NC}\n")

    for b in reversed(battles[-20:]):
        cid = b.get("challenge_id", "?")
        title = b.get("title", "?")
        ctype = b.get("type", "?")
        player = b.get("player", "?")
        zrn = b.get("total_zrn", 0)
        result = b.get("result", "?")
        completed = b.get("completed_at", "?")[:19] if b.get("completed_at") else "?"

        tc = TYPE_COLORS.get(ctype, "")
        rc = GREEN if result == "victory" else RED

        print(f"  {CYAN}{cid}{NC}  {tc}{ctype.upper():7s}{NC}  {rc}{result.upper():8s}{NC}  "
              f"{YELLOW}{zrn:>+5d} ZRN{NC}  {BOLD}{player}{NC}  {DIM}{completed}{NC}")
        print(f"        {title}")

    print()


# -- Commands: CTF --------------------------------------------------------


def cmd_ctf_start(args):
    """Start a timed CTF session."""
    existing = load_ctf()
    if existing and existing.get("status") == "active":
        print(f"  {RED}CTF already in progress! Use 'arena.py ctf end' to finish it.{NC}")
        return

    duration_str = parse_flag(args, "--duration") or "1h"
    duration = parse_duration(duration_str)

    # Gather active challenges as CTF objectives
    challenges = load_challenges()
    if not challenges:
        seed_challenges()
        challenges = load_challenges()

    active = [c for c in challenges if c["status"] == "active"]
    if not active:
        print(f"  {RED}No active challenges to use as CTF objectives.{NC}")
        return

    ctf = {
        "status": "active",
        "started_at": now_iso(),
        "duration": duration,
        "ends_at": (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat(timespec="seconds"),
        "objectives": [
            {
                "challenge_id": c["id"],
                "title": c["title"],
                "type": c["type"],
                "difficulty": c["difficulty"],
                "zrn": CTF_OBJECTIVE_ZRN.get(c["difficulty"], 100),
                "completed": False,
                "completed_by": None,
                "completed_at": None,
            }
            for c in active
        ],
        "players": [],
        "scores": {},
    }

    save_ctf(ctf)

    print(f"\n  {BOLD}{YELLOW}CTF SESSION STARTED{NC}")
    print(f"  Duration: {BOLD}{format_duration(duration)}{NC}")
    print(f"  Ends at:  {ctf['ends_at']}")
    print(f"  Objectives: {len(active)}\n")

    for obj in ctf["objectives"]:
        tc = TYPE_COLORS.get(obj["type"], "")
        dc = DIFFICULTY_COLORS.get(obj["difficulty"], "")
        print(f"  {CYAN}{obj['challenge_id']}{NC}  {tc}{obj['type'].upper():7s}{NC}  "
              f"{dc}{obj['difficulty'].upper():7s}{NC}  {YELLOW}{obj['zrn']} ZRN{NC}  {obj['title']}")

    print(f"\n  {DIM}Accept and submit findings to earn ZRN. Speed bonus for first-half completions.{NC}\n")


def cmd_ctf_status(args):
    """Show current CTF status."""
    ctf = load_ctf()
    if not ctf:
        print(f"\n  {DIM}No active CTF session. Start one with: arena.py ctf start{NC}\n")
        return

    status = ctf.get("status", "?")
    started = ctf.get("started_at", "?")
    ends = ctf.get("ends_at", "?")
    duration = ctf.get("duration", 0)
    objectives = ctf.get("objectives", [])

    # Check if expired
    try:
        end_time = datetime.fromisoformat(ends)
        now = datetime.now(timezone.utc)
        remaining = (end_time - now).total_seconds()
        if remaining <= 0 and status == "active":
            status = "expired"
            remaining = 0
    except (ValueError, TypeError):
        remaining = 0

    completed = sum(1 for o in objectives if o["completed"])
    total = len(objectives)

    sc = GREEN if status == "active" else YELLOW if status == "expired" else DIM
    print(f"\n  {BOLD}CTF Status{NC}  {sc}{status.upper()}{NC}")
    print(f"  Started: {started[:19]}  Duration: {format_duration(duration)}")
    if remaining > 0:
        print(f"  Remaining: {YELLOW}{format_duration(int(remaining))}{NC}")
    else:
        print(f"  {RED}TIME IS UP{NC}")
    print(f"  Objectives: {completed}/{total} completed\n")

    for obj in objectives:
        tc = TYPE_COLORS.get(obj["type"], "")
        if obj["completed"]:
            status_mark = f"{GREEN}DONE{NC}"
            player_info = f"  {DIM}[{obj.get('completed_by', '?')}]{NC}"
        else:
            status_mark = f"{DIM}OPEN{NC}"
            player_info = ""
        print(f"  {CYAN}{obj['challenge_id']}{NC}  {tc}{obj['type'].upper():7s}{NC}  "
              f"{status_mark}  {YELLOW}{obj['zrn']} ZRN{NC}  {obj['title']}{player_info}")

    # Show CTF scores
    if ctf.get("scores"):
        print(f"\n  {BOLD}CTF Scores:{NC}")
        sorted_scores = sorted(ctf["scores"].items(), key=lambda x: x[1], reverse=True)
        for name, zrn in sorted_scores:
            print(f"    {BOLD}{name:<15s}{NC}  {YELLOW}{zrn:>5d} ZRN{NC}")

    print()


def cmd_ctf_end(args):
    """End the current CTF session and tally scores."""
    ctf = load_ctf()
    if not ctf:
        print(f"\n  {DIM}No active CTF session.{NC}\n")
        return

    ctf["status"] = "completed"
    ctf["ended_at"] = now_iso()

    objectives = ctf.get("objectives", [])
    completed = sum(1 for o in objectives if o["completed"])
    total = len(objectives)

    # Archive CTF to history
    battles = load_history()
    for obj in objectives:
        if obj["completed"]:
            battles.append({
                "challenge_id": obj["challenge_id"],
                "title": obj["title"],
                "type": "ctf",
                "difficulty": obj["difficulty"],
                "player": obj.get("completed_by", "?"),
                "submissions": 1,
                "total_zrn": obj["zrn"],
                "completed_at": obj.get("completed_at"),
                "result": "victory",
            })
    save_history(battles)

    # Update player CTF counts
    players = load_leaderboard()
    ctf_players = set()
    for obj in objectives:
        if obj["completed"] and obj.get("completed_by"):
            p_name = obj["completed_by"]
            ctf_players.add(p_name)
    for p_name in ctf_players:
        p = ensure_player(players, p_name)
        p["ctf_count"] = p.get("ctf_count", 0) + 1
    save_leaderboard(players)

    # Archive CTF session
    archive_file = CTF_DIR / f"ctf-{ctf.get('started_at', 'unknown')[:19].replace(':', '-')}.json"
    save_json(archive_file, ctf)
    clear_ctf()

    print(f"\n  {BOLD}{YELLOW}CTF SESSION ENDED{NC}")
    print(f"  Objectives completed: {completed}/{total}")

    if ctf.get("scores"):
        print(f"\n  {BOLD}Final Scores:{NC}")
        sorted_scores = sorted(ctf["scores"].items(), key=lambda x: x[1], reverse=True)
        for i, (name, zrn) in enumerate(sorted_scores, 1):
            medal = ""
            if i == 1:
                medal = f" {YELLOW}[1st]{NC}"
            elif i == 2:
                medal = f" {DIM}[2nd]{NC}"
            elif i == 3:
                medal = f" {DIM}[3rd]{NC}"
            print(f"    {i}. {BOLD}{name:<15s}{NC}  {YELLOW}{zrn:>5d} ZRN{NC}{medal}")

    print()


# -- Commands: rewards ----------------------------------------------------


def cmd_rewards(args):
    """Show ZRN rewards or generate a claim transaction."""
    players = load_leaderboard()
    if not players:
        print(f"\n  {DIM}No rewards yet. Complete challenges to earn ZRN.{NC}\n")
        return

    if args and args[0] == "claim":
        # Generate ZRN claim transaction
        if len(args) < 2:
            print(f"  {RED}Usage: arena.py rewards claim <player>{NC}")
            return
        player_name = args[1].lower()
        if player_name not in players:
            print(f"  {RED}Player '{player_name}' not found.{NC}")
            return
        p = players[player_name]
        zrn = p["zrn"]
        if zrn <= 0:
            print(f"  {RED}No ZRN to claim.{NC}")
            return

        # Generate claim transaction (tracked locally until Zerone mainnet)
        claim = {
            "type": "zrn_claim",
            "player": player_name,
            "amount": zrn,
            "rank": get_rank(zrn),
            "challenges_completed": p["challenges_completed"],
            "generated_at": now_iso(),
            "status": "pending_mainnet",
            "note": "Tracked locally until Zerone mainnet. Then becomes on-chain claim.",
        }

        # Save to claims log
        claims_file = ARENA_DIR / "claims.json"
        claims = load_json(claims_file, {"claims": []})
        claims["claims"].append(claim)
        save_json(claims_file, claims)

        print(f"\n  {BOLD}ZRN Claim Transaction Generated{NC}")
        print(f"  Player: {BOLD}{player_name}{NC}")
        print(f"  Amount: {YELLOW}{zrn} ZRN{NC}")
        print(f"  Rank: {BOLD}{get_rank(zrn)}{NC}")
        print(f"  Status: {DIM}pending_mainnet{NC}")
        print(f"\n  {DIM}Claim tracked locally. On Zerone mainnet launch, this becomes an on-chain transaction.{NC}\n")
        return

    # Show rewards summary
    sorted_players = sorted(players.items(), key=lambda x: x[1]["zrn"], reverse=True)

    print(f"\n  {BOLD}Security Battle Arena -- ZRN Rewards{NC}\n")
    total_zrn = sum(p["zrn"] for _, p in sorted_players)
    print(f"  Total ZRN distributed: {YELLOW}{total_zrn}{NC}\n")

    for name, p in sorted_players:
        rank = get_rank(p["zrn"])
        print(f"  {BOLD}{name:<15s}{NC}  {YELLOW}{p['zrn']:>6d} ZRN{NC}  "
              f"Rank: {rank}  Completed: {p['challenges_completed']}")

    print(f"\n  {DIM}Generate claim: arena.py rewards claim <player>{NC}\n")


# -- Main Dispatch --------------------------------------------------------


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]

    # -- challenge subcommands
    if cmd == "challenge":
        if len(args) < 2:
            print(f"  {RED}Usage: arena.py challenge [create|list|show|accept|complete]{NC}")
            return
        subcmd = args[1]
        sub_args = args[2:]

        if subcmd == "create":
            cmd_challenge_create(sub_args)
        elif subcmd == "list":
            cmd_challenge_list(sub_args)
        elif subcmd == "show":
            cmd_challenge_show(sub_args)
        elif subcmd == "accept":
            cmd_challenge_accept(sub_args)
        elif subcmd == "complete":
            cmd_challenge_complete(sub_args)
        else:
            print(f"  {RED}Unknown challenge command: {subcmd}{NC}")
            print(f"  Commands: create, list, show, accept, complete")

    # -- attack
    elif cmd == "attack":
        cmd_attack(args[1:])

    # -- defend
    elif cmd == "defend":
        cmd_defend(args[1:])

    # -- false-positive
    elif cmd == "false-positive":
        cmd_false_positive(args[1:])

    # -- score
    elif cmd == "score":
        cmd_score(args[1:])

    # -- history
    elif cmd == "history":
        cmd_history(args[1:])

    # -- ctf
    elif cmd == "ctf":
        if len(args) < 2:
            print(f"  {RED}Usage: arena.py ctf [start|status|end]{NC}")
            return
        subcmd = args[1]
        sub_args = args[2:]

        if subcmd == "start":
            cmd_ctf_start(sub_args)
        elif subcmd == "status":
            cmd_ctf_status(sub_args)
        elif subcmd == "end":
            cmd_ctf_end(sub_args)
        else:
            print(f"  {RED}Unknown ctf command: {subcmd}{NC}")

    # -- rewards
    elif cmd == "rewards":
        cmd_rewards(args[1:])

    # -- seed (manual trigger)
    elif cmd == "seed":
        existing = load_challenges()
        if existing:
            print(f"\n  {DIM}Already seeded ({len(existing)} challenges). Use 'challenge create' to add more.{NC}\n")
        else:
            seed_challenges()
            print(f"\n  {GREEN}Seeded 5 initial challenges. Run 'arena.py challenge list' to view.{NC}\n")

    # -- help
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
