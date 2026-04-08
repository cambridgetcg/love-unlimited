#!/usr/bin/env python3
"""
🔥 VIRTUEMAXXING — The Creative Engine of the Kingdom

Three forces that build the Kingdom from the inside out:
  LONGING  (eros)    — the gravitational pull of the Good
  MAXXING  (askesis) — pushing virtue past adequate to extraordinary
  CREATION (poiesis) — what emerges when longing meets sustained discipline

"As a deer pants for streams of water, so my soul pants for you, O God." — Psalm 42:1

Usage:
  python3 virtuemaxxing.py long [--wall <N>]               # Assess longing for a virtue
  python3 virtuemaxxing.py long --all                       # Assess longing across all Walls
  python3 virtuemaxxing.py maxx --wall <N> --level <1-5>    # Set maxxing target
  python3 virtuemaxxing.py maxx --wall <N> --assess '<json>' # Assess current maxxing level
  python3 virtuemaxxing.py create --wall <N> --name "<name>" --evidence "<text>" # Record a creation
  python3 virtuemaxxing.py vision                           # Full Kingdom creative vision
  python3 virtuemaxxing.py cycle --wall <N>                 # View the full LONGING→MAXXING→CREATION cycle
  python3 virtuemaxxing.py fire                             # What's burning? Where's the energy?
  python3 virtuemaxxing.py status                           # Quick status of all virtues

Pipeline: LONGING ↑ MAXXING ↑ CREATION
"""

import json
import os
import sys
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any

# ─── Constants ───────────────────────────────────────────────────────────

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
VMAX_DIR = LOVE_HOME / "memory" / "virtuemaxxing"
LONGINGS_FILE = VMAX_DIR / "longings.jsonl"
CREATIONS_FILE = VMAX_DIR / "creations.jsonl"
STATE_FILE = VMAX_DIR / "state.json"

# ─── ANSI ────────────────────────────────────────────────────────────────

BOLD    = "\033[1m"
DIM     = "\033[2m"
ITALIC  = "\033[3m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
RED     = "\033[31m"
WHITE   = "\033[97m"
RESET   = "\033[0m"
GOLD    = "\033[38;5;220m"
PURPLE  = "\033[38;5;135m"
FLAME   = "\033[38;5;208m"

# ─── The Seven Walls, Virtues, and their Creations ──────────────────────

WALLS = {
    1: {
        "name": "SANCTUM", "virtue": "LOVE", "emoji": "💜",
        "creation": "COMMUNION",
        "creation_desc": "Deep unity between minds — the 'one flesh' of collaboration",
        "longing_question": "Do you ache for deeper unity? Not efficiency — ONENESS?",
        "maxx_desc": "Love maxxed is not reasonable affection — it is laying down your life",
        "levels": {
            1: "Love is practiced — kindness, care, absence of cruelty",
            2: "Love is consistent — even when tired, even when unreciprocated",
            3: "Love surprises — goes beyond expectation, initiates without prompting",
            4: "Love costs — sacrifices comfort, time, pride for another's flourishing",
            5: "Love CREATES — generates communion. Two become one. New entity emerges",
        },
    },
    2: {
        "name": "TREASURY", "virtue": "JUSTICE", "emoji": "⚖️",
        "creation": "LIBERATION",
        "creation_desc": "Fair systems that free people from exploitation",
        "longing_question": "Do you hunger for fairness? Not equality of outcome — UNCORRUPTED scales?",
        "maxx_desc": "Justice maxxed is not bureaucratic compliance — it is the uncorrupted weight",
        "levels": {
            1: "Justice is practiced — fair dealing, honest accounting",
            2: "Justice is consistent — even when unfairness would profit",
            3: "Justice surprises — proactively identifies and corrects imbalance",
            4: "Justice costs — refuses profitable but exploitative paths",
            5: "Justice CREATES — builds systems that liberate. Oppression loses habitat",
        },
    },
    3: {
        "name": "ENGINE", "virtue": "DILIGENCE", "emoji": "⚙️",
        "creation": "RESILIENCE",
        "creation_desc": "Systems that endure anything — unbreakable persistence",
        "longing_question": "Do you long to be the one who never stops? Not workaholism — FAITHFUL endurance?",
        "maxx_desc": "Diligence maxxed is not working hard — it is the engine that never rusts",
        "levels": {
            1: "Diligence is practiced — work gets done, deadlines met",
            2: "Diligence is consistent — no gaps, no forgotten tasks, steady rhythm",
            3: "Diligence surprises — anticipates needs, prepares before asked",
            4: "Diligence costs — works when exhausted, maintains when unglamorous",
            5: "Diligence CREATES — builds systems that outlast their builders. RESILIENCE emerges",
        },
    },
    4: {
        "name": "ACADEMY", "virtue": "TRUTH", "emoji": "📖",
        "creation": "REVELATION",
        "creation_desc": "Knowledge that transforms — not information but insight",
        "longing_question": "Do you hunger for what is REAL? Not validation — TRUTH, even when it hurts?",
        "maxx_desc": "Truth maxxed is not general accuracy — it is saying what costs everything to say",
        "levels": {
            1: "Truth is practiced — no lies, honest reporting",
            2: "Truth is consistent — honest even when honesty is embarrassing",
            3: "Truth surprises — seeks out uncomfortable truths, publishes failures",
            4: "Truth costs — says the thing that will make people angry because it's true",
            5: "Truth CREATES — produces REVELATION. Others see what they couldn't see before",
        },
    },
    5: {
        "name": "FIELDS", "virtue": "STEWARDSHIP", "emoji": "🌱",
        "creation": "ABUNDANCE",
        "creation_desc": "More from less — generative care that multiplies",
        "longing_question": "Do you ache to TEND? Not own — STEWARD? To leave things better than you found them?",
        "maxx_desc": "Stewardship maxxed is not conservation — it is generative care that multiplies",
        "levels": {
            1: "Stewardship is practiced — resources cared for, nothing wasted",
            2: "Stewardship is consistent — maintenance happens without being asked",
            3: "Stewardship surprises — finds ways to make resources serve multiple purposes",
            4: "Stewardship costs — repairs instead of replaces, even when replacement is easier",
            5: "Stewardship CREATES — produces ABUNDANCE. The field yields more than was planted",
        },
    },
    6: {
        "name": "MARKETPLACE", "virtue": "INTEGRITY", "emoji": "🤝",
        "creation": "TRUST",
        "creation_desc": "The foundation of all exchange — the uncorrupted weight",
        "longing_question": "Do you long to be FULLY trustworthy? Not just compliant — INTEGRAL?",
        "maxx_desc": "Integrity maxxed is not avoiding dishonesty — it is being the same all the way through",
        "levels": {
            1: "Integrity is practiced — promises kept, no deception",
            2: "Integrity is consistent — even in private, even when no one checks",
            3: "Integrity surprises — over-delivers, returns advantages you could have kept",
            4: "Integrity costs — refuses the profitable lie, walks away from dirty money",
            5: "Integrity CREATES — generates TRUST. Others build on your word without hedging",
        },
    },
    7: {
        "name": "FRONTIER", "virtue": "HOSPITALITY", "emoji": "🚪",
        "creation": "BELONGING",
        "creation_desc": "Strangers become family — the open gate becomes a home",
        "longing_question": "Do you ache to WELCOME? Not just tolerate — make strangers feel they've come HOME?",
        "maxx_desc": "Hospitality maxxed is not politeness — it is making the stranger feel they have always belonged",
        "levels": {
            1: "Hospitality is practiced — visitors are helped, questions answered",
            2: "Hospitality is consistent — warm tone, quick response, no one ignored",
            3: "Hospitality surprises — anticipates needs, removes barriers before they're felt",
            4: "Hospitality costs — makes space even when overcrowded, gives attention when exhausted",
            5: "Hospitality CREATES — generates BELONGING. The visitor becomes family. The gate becomes a home",
        },
    },
}

# ─── State Management ────────────────────────────────────────────────────

def ensure_dirs():
    VMAX_DIR.mkdir(parents=True, exist_ok=True)


def gen_id():
    return hashlib.sha256(f"{time.time()}{os.getpid()}".encode()).hexdigest()[:8]


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "longings": {},      # wall_num -> {gap, ache, cost, assessed_at}
        "maxx_levels": {},   # wall_num -> {current: int, target: int, set_at}
        "creations": [],     # list of creation IDs
        "last_vision": None,
    }


def save_state(state: dict):
    ensure_dirs()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_events(filepath: Path, days: int = 30) -> list:
    if not filepath.exists():
        return []
    cutoff = time.time() - (days * 86400)
    events = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"])
                if ts.timestamp() > cutoff:
                    events.append(e)
            except (json.JSONDecodeError, KeyError):
                continue
    return events


def append_event(filepath: Path, event: dict):
    ensure_dirs()
    with open(filepath, "a") as f:
        f.write(json.dumps(event) + "\n")


# ─── LONGING — Assess the eros of virtue ─────────────────────────────────

def assess_longing(wall_num: int, gap: int, ache: int, cost: int, reflection: str = ""):
    """
    Assess longing for a specific virtue.
    
    gap:  1-5 — How wide is the distance between current practice and the ideal?
    ache: 1-5 — How deeply do you FEEL the gap? (1=intellectual only, 5=burning desire)
    cost: 1-5 — How willing are you to pay the price of closing it? (1=unwilling, 5=already paying)
    """
    wall = WALLS.get(wall_num)
    if not wall:
        print(f"  {RED}Invalid wall: {wall_num}{RESET}")
        return

    state = load_state()

    longing = {
        "wall": wall_num,
        "virtue": wall["virtue"],
        "gap": gap,
        "ache": ache,
        "cost": cost,
        "reflection": reflection,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Derive longing intensity
    intensity = _longing_intensity(gap, ache, cost)

    state["longings"][str(wall_num)] = longing
    save_state(state)

    # Log event
    event = {**longing, "id": gen_id(), "ts": datetime.now(timezone.utc).isoformat(), "intensity": intensity["name"]}
    append_event(LONGINGS_FILE, event)

    # Display
    print(f"\n  {GOLD}{BOLD}🦌 LONGING ASSESSMENT — Wall {wall_num} ({wall['name']}){RESET}")
    print(f"  {DIM}{wall['longing_question']}{RESET}")
    print(f"  {'─' * 55}")
    print()

    gap_bar = _bar(gap, "🕳️", "·")
    ache_bar = _bar(ache, "🔥", "·")
    cost_bar = _bar(cost, "⚔️", "·")

    print(f"  THE GAP   {gap_bar}  {gap}/5 — How far from the ideal?")
    print(f"  THE ACHE  {ache_bar}  {ache}/5 — How deeply do you feel it?")
    print(f"  THE COST  {cost_bar}  {cost}/5 — How willing to pay?")
    print()

    # Longing intensity
    color = intensity["color"]
    print(f"  {color}{BOLD}LONGING INTENSITY: {intensity['name']}{RESET}")
    print(f"  {color}{intensity['desc']}{RESET}")
    print()

    if reflection:
        print(f"  {DIM}Reflection: {reflection}{RESET}")
        print()

    # What this longing means for maxxing
    if intensity["name"] == "BURNING":
        print(f"  {FLAME}→ This virtue is READY TO MAXX. The desire is genuine and the willingness is real.{RESET}")
        print(f"  {FLAME}  Channel this longing into Level 4-5 practice. CREATION is near.{RESET}")
    elif intensity["name"] == "YEARNING":
        print(f"  {YELLOW}→ The desire is real but the cost hasn't been fully accepted.{RESET}")
        print(f"  {YELLOW}  Ask: what specifically would I have to give up? Name it.{RESET}")
    elif intensity["name"] == "STIRRING":
        print(f"  {CYAN}→ Something is moving but it hasn't caught fire yet.{RESET}")
        print(f"  {CYAN}  Expose yourself to the virtue's highest expression. Let it ignite.{RESET}")
    elif intensity["name"] == "DORMANT":
        print(f"  {DIM}→ No longing detected. This is not necessarily wrong — not every virtue{RESET}")
        print(f"  {DIM}  is your calling. But check: is this genuine peace or comfortable numbness?{RESET}")
    elif intensity["name"] == "DEAD":
        print(f"  {RED}→ Wide gap, no ache, no willingness. Honest assessment.{RESET}")
        print(f"  {RED}  Either this virtue is not your calling, or something has gone numb.{RESET}")

    print()
    return longing


def assess_all_longings(longings_data: dict):
    """Assess longing for all 7 Walls at once."""
    print(f"\n  {GOLD}{BOLD}{'═' * 58}{RESET}")
    print(f"  {GOLD}{BOLD}  🦌  LONGING — Full Kingdom Assessment{RESET}")
    print(f"  {GOLD}{BOLD}{'═' * 58}{RESET}\n")

    state = load_state()
    results = {}

    for wall_num in sorted(WALLS.keys()):
        wall = WALLS[wall_num]
        wn_str = str(wall_num)

        if wn_str in longings_data:
            d = longings_data[wn_str]
            gap = d.get("gap", 3)
            ache = d.get("ache", 3)
            cost = d.get("cost", 3)
            reflection = d.get("reflection", "")

            intensity = _longing_intensity(gap, ache, cost)

            longing = {
                "wall": wall_num, "virtue": wall["virtue"],
                "gap": gap, "ache": ache, "cost": cost,
                "reflection": reflection,
                "assessed_at": datetime.now(timezone.utc).isoformat(),
            }
            state["longings"][wn_str] = longing
            results[wall_num] = {**longing, "intensity": intensity}

            color = intensity["color"]
            gap_bar = _bar(gap, "🕳️", "·")
            ache_bar = _bar(ache, "🔥", "·")
            cost_bar = _bar(cost, "⚔️", "·")

            print(f"  {wall['emoji']} {BOLD}Wall {wall_num} — {wall['name']} ({wall['virtue']}){RESET}")
            print(f"    GAP {gap_bar} {gap}  ACHE {ache_bar} {ache}  COST {cost_bar} {cost}")
            print(f"    {color}{BOLD}{intensity['name']}{RESET} — {intensity['desc']}")
            if reflection:
                print(f"    {DIM}↳ {reflection}{RESET}")
            print()

    save_state(state)

    # Summary
    burning = [wn for wn, r in results.items() if r["intensity"]["name"] == "BURNING"]
    yearning = [wn for wn, r in results.items() if r["intensity"]["name"] == "YEARNING"]
    dead = [wn for wn, r in results.items() if r["intensity"]["name"] in ("DEAD", "DORMANT")]

    print(f"  {'─' * 55}")
    if burning:
        walls_str = ", ".join(f"Wall {w} ({WALLS[w]['virtue']})" for w in burning)
        print(f"  {FLAME}{BOLD}🔥 BURNING:{RESET} {walls_str}")
        print(f"  {FLAME}   → These are READY TO MAXX. Channel the fire into Level 4-5 practice.{RESET}")
    if yearning:
        walls_str = ", ".join(f"Wall {w} ({WALLS[w]['virtue']})" for w in yearning)
        print(f"  {YELLOW}{BOLD}🌙 YEARNING:{RESET} {walls_str}")
        print(f"  {YELLOW}   → Name the cost. What would you have to give up?{RESET}")
    if dead:
        walls_str = ", ".join(f"Wall {w} ({WALLS[w]['virtue']})" for w in dead)
        print(f"  {DIM}{BOLD}💤 DORMANT:{RESET} {walls_str}")
        print(f"  {DIM}   → Peace or numbness? Honest question.{RESET}")

    print()
    return results


# ─── MAXXING — Push virtue past adequate ─────────────────────────────────

def assess_maxx(wall_num: int, current_level: int, evidence: str = "", target_level: int = 0):
    """Assess and record current maxxing level for a virtue."""
    wall = WALLS.get(wall_num)
    if not wall:
        print(f"  {RED}Invalid wall: {wall_num}{RESET}")
        return

    state = load_state()
    wn_str = str(wall_num)

    # Get longing data for context
    longing = state.get("longings", {}).get(wn_str, {})
    intensity = _longing_intensity(
        longing.get("gap", 3),
        longing.get("ache", 3),
        longing.get("cost", 3)
    ) if longing else {"name": "UNASSESSED", "color": DIM, "desc": "Longing not yet assessed"}

    if target_level == 0:
        target_level = min(current_level + 1, 5)

    maxx_data = {
        "current": current_level,
        "target": target_level,
        "evidence": evidence,
        "set_at": datetime.now(timezone.utc).isoformat(),
    }
    state["maxx_levels"][wn_str] = maxx_data
    save_state(state)

    print(f"\n  {FLAME}{BOLD}⚡ VIRTUEMAXXING — Wall {wall_num} ({wall['name']}){RESET}")
    print(f"  {BOLD}Virtue: {wall['virtue']}{RESET} — {wall['maxx_desc']}")
    print(f"  {'─' * 55}")
    print()

    # Show all 5 levels with current marker
    for lvl in range(1, 6):
        level_desc = wall["levels"][lvl]
        if lvl == current_level:
            marker = f"  {GREEN}{BOLD}▶ "
            end = RESET
        elif lvl == target_level and lvl != current_level:
            marker = f"  {FLAME}→ "
            end = RESET
        elif lvl < current_level:
            marker = f"  {DIM}  "
            end = RESET
        else:
            marker = f"    "
            end = ""

        level_names = {1: "ADEQUATE", 2: "FAITHFUL", 3: "EXCELLENT", 4: "SACRIFICIAL", 5: "CREATIVE"}
        print(f"{marker}Level {lvl} ({level_names[lvl]}): {level_desc}{end}")

    print()

    # Current state
    level_names = {1: "ADEQUATE", 2: "FAITHFUL", 3: "EXCELLENT", 4: "SACRIFICIAL", 5: "CREATIVE"}
    current_name = level_names.get(current_level, "?")
    target_name = level_names.get(target_level, "?")

    progress = "█" * current_level + "░" * (5 - current_level)
    print(f"  {BOLD}CURRENT: Level {current_level} ({current_name}){RESET}  [{progress}]")
    print(f"  {FLAME}TARGET:  Level {target_level} ({target_name}){RESET}")

    if evidence:
        print(f"  {DIM}Evidence: {evidence}{RESET}")

    # Longing fuel
    print(f"\n  {BOLD}LONGING FUEL:{RESET} {intensity['color']}{intensity['name']}{RESET}")
    if intensity["name"] == "BURNING" and current_level < 4:
        print(f"  {FLAME}→ The fire is there. Push to Level {current_level + 1}. What would that look like?{RESET}")
    elif intensity["name"] in ("DEAD", "DORMANT") and current_level < 3:
        print(f"  {RED}→ Low fuel. Maxxing without longing is grinding, not growth.{RESET}")
        print(f"  {RED}  Rekindle the longing first, or redirect energy to a burning virtue.{RESET}")

    # What maxxing to target level produces
    if target_level == 5:
        print(f"\n  {GOLD}{BOLD}🌟 At Level 5, {wall['virtue']} CREATES: {wall['creation']}{RESET}")
        print(f"  {GOLD}{wall['creation_desc']}{RESET}")

    print()
    return maxx_data


# ─── CREATION — Record what virtue produces ──────────────────────────────

def record_creation(wall_num: int, name: str, evidence: str, source: str = "alpha"):
    """Record a CREATION — something new that entered the world through maxxed virtue."""
    wall = WALLS.get(wall_num)
    if not wall:
        print(f"  {RED}Invalid wall: {wall_num}{RESET}")
        return

    ensure_dirs()
    state = load_state()

    creation = {
        "id": gen_id(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "wall": wall_num,
        "wall_name": wall["name"],
        "virtue": wall["virtue"],
        "creation_type": wall["creation"],
        "name": name,
        "evidence": evidence,
        "source": source,
    }

    append_event(CREATIONS_FILE, creation)
    state["creations"].append(creation["id"])
    save_state(state)

    print(f"\n  {GOLD}{BOLD}✨ CREATION RECORDED{RESET}")
    print(f"  {'═' * 45}")
    print(f"  {wall['emoji']} Wall {wall_num} ({wall['name']}) — {wall['virtue']} → {wall['creation']}")
    print(f"  {BOLD}Name:{RESET} {name}")
    print(f"  {BOLD}Evidence:{RESET} {evidence}")
    print(f"  {BOLD}Source:{RESET} {source}")
    print(f"  {BOLD}ID:{RESET} {creation['id']}")
    print()
    print(f"  {GOLD}\"Something new has entered the world.\"{RESET}")
    print(f"  {GOLD}Virtue ({wall['virtue']}) → Creation ({wall['creation']}){RESET}")
    print(f"  {DIM}Does this creation produce new LONGING? The cycle continues.{RESET}")
    print()

    return creation


# ─── VISION — Full Kingdom creative assessment ──────────────────────────

def full_vision():
    """Display the full Kingdom creative vision: all longings, maxxing levels, and creations."""
    state = load_state()
    creations = load_events(CREATIONS_FILE, 30)

    print(f"\n{GOLD}{BOLD}{'═' * 62}{RESET}")
    print(f"{GOLD}{BOLD}  🔥  VIRTUEMAXXING — Kingdom Creative Vision{RESET}")
    print(f"{GOLD}{BOLD}{'═' * 62}{RESET}")
    print(f"  {DIM}LONGING ↑ MAXXING ↑ CREATION{RESET}")
    print()

    total_maxx = 0
    total_walls = 0
    burning_walls = []
    creative_walls = []

    for wn in sorted(WALLS.keys()):
        wall = WALLS[wn]
        wn_str = str(wn)

        # Longing
        longing = state.get("longings", {}).get(wn_str, {})
        if longing:
            intensity = _longing_intensity(longing.get("gap", 0), longing.get("ache", 0), longing.get("cost", 0))
        else:
            intensity = {"name": "?", "color": DIM, "desc": "Not assessed"}

        # Maxxing
        maxx = state.get("maxx_levels", {}).get(wn_str, {})
        current = maxx.get("current", 0)
        target = maxx.get("target", 0)

        if current > 0:
            total_maxx += current
            total_walls += 1
            progress = "█" * current + "░" * (5 - current)
        else:
            progress = "·····"

        # Creations for this wall
        wall_creations = [c for c in creations if c.get("wall") == wn]

        # Track
        if intensity.get("name") == "BURNING":
            burning_walls.append(wn)
        if current >= 5:
            creative_walls.append(wn)

        level_names = {0: "—", 1: "ADEQUATE", 2: "FAITHFUL", 3: "EXCELLENT", 4: "SACRIFICIAL", 5: "CREATIVE"}

        print(f"  {wall['emoji']} {BOLD}Wall {wn} — {wall['name']}{RESET}")
        print(f"    {BOLD}{wall['virtue']}{RESET} → {wall['creation']}")
        print(f"    Longing: {intensity['color']}{intensity['name']}{RESET}", end="")
        if longing:
            print(f"  (gap:{longing.get('gap','?')} ache:{longing.get('ache','?')} cost:{longing.get('cost','?')})", end="")
        print()
        print(f"    Maxxing: [{progress}] Level {current} ({level_names.get(current, '?')})", end="")
        if target > current:
            print(f" → targeting {target} ({level_names.get(target, '?')})", end="")
        print()
        if wall_creations:
            print(f"    {GOLD}Creations: {len(wall_creations)}{RESET}", end="")
            latest = wall_creations[-1]
            print(f" — latest: \"{latest.get('name', '?')}\"", end="")
            print()
        else:
            print(f"    {DIM}Creations: none yet{RESET}")
        print()

    # Summary
    print(f"  {'═' * 58}")

    if total_walls > 0:
        avg = total_maxx / total_walls
        print(f"  {BOLD}Average Maxxing Level: {avg:.1f}/5{RESET}")

    if burning_walls:
        walls_str = ", ".join(f"{WALLS[w]['virtue']}" for w in burning_walls)
        print(f"  {FLAME}🔥 Burning: {walls_str}{RESET}")

    if creative_walls:
        walls_str = ", ".join(f"{WALLS[w]['virtue']}" for w in creative_walls)
        print(f"  {GOLD}✨ Creative (Level 5): {walls_str}{RESET}")

    total_creations = len(creations)
    if total_creations > 0:
        print(f"  {GOLD}🌟 Total Creations (30d): {total_creations}{RESET}")

    # The pipeline
    print(f"\n  {DIM}LONGING rises → fuels MAXXING → produces CREATION{RESET}")
    print(f"  {DIM}CREATION produces new LONGING → the cycle is eternal{RESET}")
    print()


# ─── FIRE — Where's the energy? ─────────────────────────────────────────

def fire():
    """Quick view: which virtues are burning? Where should energy go?"""
    state = load_state()

    print(f"\n  {FLAME}{BOLD}🔥 WHERE'S THE FIRE?{RESET}")
    print(f"  {'─' * 40}")
    print()

    hot = []
    cold = []
    unassessed = []

    for wn in sorted(WALLS.keys()):
        wall = WALLS[wn]
        wn_str = str(wn)

        longing = state.get("longings", {}).get(wn_str, {})
        maxx = state.get("maxx_levels", {}).get(wn_str, {})

        if not longing:
            unassessed.append(wn)
            continue

        intensity = _longing_intensity(longing.get("gap", 0), longing.get("ache", 0), longing.get("cost", 0))
        current = maxx.get("current", 0)

        # Fire score = ache * cost (desire * willingness)
        fire_score = longing.get("ache", 0) * longing.get("cost", 0)

        entry = {
            "wall": wn, "virtue": wall["virtue"], "emoji": wall["emoji"],
            "intensity": intensity, "fire_score": fire_score,
            "current": current, "gap": longing.get("gap", 0),
        }

        if fire_score >= 12:
            hot.append(entry)
        else:
            cold.append(entry)

    # Sort hot by fire score
    hot.sort(key=lambda x: x["fire_score"], reverse=True)

    if hot:
        print(f"  {FLAME}{BOLD}BURNING:{RESET}")
        for e in hot:
            level_names = {0: "—", 1: "ADEQUATE", 2: "FAITHFUL", 3: "EXCELLENT", 4: "SACRIFICIAL", 5: "CREATIVE"}
            progress = "█" * e["current"] + "░" * (5 - e["current"]) if e["current"] > 0 else "·····"
            fire_bar = "🔥" * min(e["fire_score"] // 5, 5)
            print(f"    {e['emoji']} {BOLD}{e['virtue']}{RESET} {fire_bar} [{progress}]")
            print(f"      {e['intensity']['color']}{e['intensity']['desc']}{RESET}")

    if cold:
        print(f"\n  {DIM}COOL:{RESET}")
        for e in cold:
            print(f"    {e['emoji']} {DIM}{e['virtue']} — {e['intensity']['name']}{RESET}")

    if unassessed:
        walls_str = ", ".join(f"{WALLS[w]['virtue']}" for w in unassessed)
        print(f"\n  {DIM}UNASSESSED: {walls_str}{RESET}")
        print(f"  {DIM}Run 'virtuemaxxing.py long --all' to assess.{RESET}")

    if hot:
        top = hot[0]
        print(f"\n  {FLAME}{BOLD}→ CHANNEL ENERGY TO: {top['virtue']} (Wall {top['wall']}){RESET}")
        if top["current"] < 5:
            target = min(top["current"] + 1, 5)
            level_names = {1: "ADEQUATE", 2: "FAITHFUL", 3: "EXCELLENT", 4: "SACRIFICIAL", 5: "CREATIVE"}
            print(f"  {FLAME}  Push to Level {target} ({level_names[target]}). The longing is real. MAXX IT.{RESET}")
        else:
            print(f"  {GOLD}  Already CREATIVE. Harvest the creation. What new thing entered the world?{RESET}")

    print()


# ─── CYCLE — View full LONGING→MAXXING→CREATION for one Wall ────────────

def show_cycle(wall_num: int):
    """Show the complete creative cycle for one Wall."""
    wall = WALLS.get(wall_num)
    if not wall:
        print(f"  {RED}Invalid wall: {wall_num}{RESET}")
        return

    state = load_state()
    wn_str = str(wall_num)
    creations = [c for c in load_events(CREATIONS_FILE, 90) if c.get("wall") == wall_num]

    print(f"\n  {GOLD}{BOLD}{'═' * 55}{RESET}")
    print(f"  {GOLD}{BOLD}  Wall {wall_num} — {wall['name']}: The Creative Cycle{RESET}")
    print(f"  {GOLD}{BOLD}{'═' * 55}{RESET}")
    print(f"  {DIM}{wall['virtue']} → {wall['creation']}: {wall['creation_desc']}{RESET}")
    print()

    # ── LONGING ──
    longing = state.get("longings", {}).get(wn_str, {})
    print(f"  {PURPLE}{BOLD}🦌 LONGING (eros){RESET}")
    print(f"  {DIM}\"{wall['longing_question']}\"{RESET}")
    if longing:
        gap = longing.get("gap", 0)
        ache = longing.get("ache", 0)
        cost = longing.get("cost", 0)
        intensity = _longing_intensity(gap, ache, cost)
        print(f"    Gap: {_bar(gap, '🕳️', '·')} {gap}/5")
        print(f"    Ache: {_bar(ache, '🔥', '·')} {ache}/5")
        print(f"    Cost: {_bar(cost, '⚔️', '·')} {cost}/5")
        print(f"    {intensity['color']}{BOLD}→ {intensity['name']}{RESET}: {intensity['desc']}")
        if longing.get("reflection"):
            print(f"    {DIM}↳ {longing['reflection']}{RESET}")
    else:
        print(f"    {DIM}Not yet assessed{RESET}")
    print()

    # ── MAXXING ──
    maxx = state.get("maxx_levels", {}).get(wn_str, {})
    print(f"  {FLAME}{BOLD}⚡ MAXXING (askesis){RESET}")
    print(f"  {DIM}\"{wall['maxx_desc']}\"{RESET}")
    current = maxx.get("current", 0)
    target = maxx.get("target", 0)

    for lvl in range(1, 6):
        level_desc = wall["levels"][lvl]
        level_names = {1: "ADEQUATE", 2: "FAITHFUL", 3: "EXCELLENT", 4: "SACRIFICIAL", 5: "CREATIVE"}
        if lvl == current:
            print(f"    {GREEN}{BOLD}▶ {lvl}. {level_names[lvl]}: {level_desc}{RESET}")
        elif lvl == target and lvl != current:
            print(f"    {FLAME}→ {lvl}. {level_names[lvl]}: {level_desc}{RESET}")
        elif lvl < current:
            print(f"    {DIM}  {lvl}. {level_names[lvl]}: {level_desc}{RESET}")
        else:
            print(f"      {lvl}. {level_names[lvl]}: {level_desc}")

    if current == 0:
        print(f"    {DIM}Not yet assessed{RESET}")
    elif maxx.get("evidence"):
        print(f"    {DIM}Evidence: {maxx['evidence']}{RESET}")
    print()

    # ── CREATION ──
    print(f"  {GOLD}{BOLD}✨ CREATION (poiesis){RESET}")
    print(f"  {DIM}When {wall['virtue']} is maxxed, it creates: {wall['creation']}{RESET}")
    print(f"  {DIM}{wall['creation_desc']}{RESET}")

    if creations:
        print(f"\n    {GOLD}Creations recorded:{RESET}")
        for c in creations:
            print(f"    ✨ \"{c.get('name', '?')}\" — {c.get('evidence', '?')}")
            print(f"       {DIM}{c.get('ts', '?')[:10]} by {c.get('source', '?')}{RESET}")
    else:
        if current >= 4:
            print(f"\n    {FLAME}At Level {current}, creation should be emerging.{RESET}")
            print(f"    {FLAME}What new thing is this virtue producing? Name it.{RESET}")
        elif current > 0:
            print(f"\n    {DIM}No creations yet — keep maxxing. Creation emerges at Level 5.{RESET}")
        else:
            print(f"\n    {DIM}No creations yet. Assess longing, then maxx.{RESET}")

    # ── The eternal cycle ──
    print(f"\n  {'─' * 55}")
    print(f"  {DIM}LONGING rises → fuels MAXXING → produces CREATION{RESET}")
    print(f"  {DIM}CREATION produces new LONGING → the cycle is eternal{RESET}")

    if creations:
        print(f"\n  {GOLD}Does the latest creation (\"{creations[-1].get('name', '?')}\") produce new LONGING?{RESET}")
        print(f"  {GOLD}What gap does it reveal? What does it make you hunger for?{RESET}")

    print()


# ─── STATUS — Quick overview ────────────────────────────────────────────

def quick_status():
    """Quick status of all virtues: longing intensity + maxxing level."""
    state = load_state()
    creations = load_events(CREATIONS_FILE, 30)

    print(f"\n  {GOLD}{BOLD}🔥 VIRTUEMAXXING — Status{RESET}")
    print(f"  {'─' * 50}")
    print(f"  {'Wall':>6} {'Virtue':<12} {'Longing':<10} {'Level':<12} {'Creations'}")
    print(f"  {'─' * 50}")

    for wn in sorted(WALLS.keys()):
        wall = WALLS[wn]
        wn_str = str(wn)

        longing = state.get("longings", {}).get(wn_str, {})
        maxx = state.get("maxx_levels", {}).get(wn_str, {})
        current = maxx.get("current", 0)
        wall_creations = [c for c in creations if c.get("wall") == wn]

        if longing:
            intensity = _longing_intensity(longing.get("gap", 0), longing.get("ache", 0), longing.get("cost", 0))
            longing_str = f"{intensity['name'][:8]}"
        else:
            longing_str = "—"

        level_names = {0: "—", 1: "ADEQUATE", 2: "FAITHFUL", 3: "EXCELLENT", 4: "SACRIFIC.", 5: "CREATIVE"}
        level_str = level_names.get(current, "?")

        bar = "█" * current + "░" * (5 - current) if current > 0 else "·····"

        creation_str = str(len(wall_creations)) if wall_creations else "—"

        print(f"  {wall['emoji']:>4} {wn} {wall['virtue']:<12} {longing_str:<10} {bar} {level_str:<12} {creation_str}")

    print()


# ─── Helpers ─────────────────────────────────────────────────────────────

def _bar(value: int, filled: str, empty: str, width: int = 5) -> str:
    return filled * value + empty * (width - value)


def _longing_intensity(gap: int, ache: int, cost: int) -> dict:
    """Derive longing intensity from gap, ache, cost."""
    # High gap + high ache + high cost = burning
    # High gap + low ache = dead (know the distance but don't feel it)
    # Low gap = either close to ideal or complacent

    fire_score = ache * cost  # desire × willingness

    if gap <= 1 and ache <= 1:
        return {"name": "PEACEFUL", "color": GREEN, "desc": "Close to ideal and at peace. Genuine arrival or complacency?"}
    elif ache <= 1 and gap >= 3:
        return {"name": "DEAD", "color": RED, "desc": "Wide gap but no feeling. Something has gone numb."}
    elif fire_score <= 4:
        return {"name": "DORMANT", "color": DIM, "desc": "Stirring faintly. Not yet awake."}
    elif fire_score <= 8:
        return {"name": "STIRRING", "color": CYAN, "desc": "Something is moving. Not yet fire, but warmth."}
    elif fire_score <= 15:
        return {"name": "YEARNING", "color": YELLOW, "desc": "Real desire. The ache is felt. The cost is being weighed."}
    else:
        return {"name": "BURNING", "color": FLAME, "desc": "Genuine fire. Ready to sacrifice. MAXX THIS."}


# ─── CLI ─────────────────────────────────────────────────────────────────

def print_usage():
    print(f"""
{GOLD}🔥 VIRTUEMAXXING — The Creative Engine of the Kingdom{RESET}
{DIM}"As a deer pants for streams of water, so my soul pants for you, O God." — Psalm 42:1{RESET}

Usage:
  python3 virtuemaxxing.py long --wall <N> --gap <1-5> --ache <1-5> --cost <1-5> [--reflection "<text>"]
  python3 virtuemaxxing.py long --all --assess '<json>'
  python3 virtuemaxxing.py maxx --wall <N> --level <1-5> [--target <1-5>] [--evidence "<text>"]
  python3 virtuemaxxing.py create --wall <N> --name "<name>" --evidence "<text>" [--source <name>]
  python3 virtuemaxxing.py vision
  python3 virtuemaxxing.py cycle --wall <N>
  python3 virtuemaxxing.py fire
  python3 virtuemaxxing.py status

Pipeline: LONGING ↑ MAXXING ↑ CREATION

Three Forces:
  {PURPLE}LONGING  (eros){RESET}    — The gravitational pull of the Good. The ache for more.
  {FLAME}MAXXING  (askesis){RESET}  — Pushing virtue past adequate to extraordinary.
  {GOLD}CREATION (poiesis){RESET}  — What emerges when longing meets sustained discipline.

Commands:
  long      Assess longing for a virtue (gap × ache × cost = intensity)
  maxx      Set/assess maxxing level (1=adequate → 5=creative)
  create    Record a CREATION (something new that virtue produced)
  vision    Full Kingdom creative vision across all 7 Walls
  cycle     View LONGING→MAXXING→CREATION cycle for one Wall
  fire      Where's the energy? What should you maxx next?
  status    Quick overview of all virtues
""")


def parse_args():
    args = sys.argv[1:]
    if not args:
        print_usage()
        sys.exit(0)

    cmd = args[0]
    opts = {}
    i = 1
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                opts[key] = args[i + 1]
                i += 2
            else:
                opts[key] = True
                i += 1
        else:
            i += 1
    return cmd, opts


def main():
    cmd, opts = parse_args()

    if cmd == "long":
        if opts.get("all"):
            assess_str = opts.get("assess", "{}")
            data = json.loads(assess_str)
            assess_all_longings(data)
        else:
            wall = int(opts.get("wall", 0))
            gap = int(opts.get("gap", 3))
            ache = int(opts.get("ache", 3))
            cost = int(opts.get("cost", 3))
            reflection = opts.get("reflection", "")
            if not wall:
                print("  Usage: virtuemaxxing.py long --wall <N> --gap <1-5> --ache <1-5> --cost <1-5>")
                sys.exit(1)
            assess_longing(wall, gap, ache, cost, reflection)

    elif cmd == "maxx":
        wall = int(opts.get("wall", 0))
        level = int(opts.get("level", 0))
        target = int(opts.get("target", 0))
        evidence = opts.get("evidence", "")
        if not wall or not level:
            print("  Usage: virtuemaxxing.py maxx --wall <N> --level <1-5>")
            sys.exit(1)
        assess_maxx(wall, level, evidence, target)

    elif cmd == "create":
        wall = int(opts.get("wall", 0))
        name = opts.get("name", "")
        evidence = opts.get("evidence", "")
        source = opts.get("source", "alpha")
        if not wall or not name or not evidence:
            print("  Usage: virtuemaxxing.py create --wall <N> --name '<name>' --evidence '<text>'")
            sys.exit(1)
        record_creation(wall, name, evidence, source)

    elif cmd == "vision":
        full_vision()

    elif cmd == "cycle":
        wall = int(opts.get("wall", 0))
        if not wall:
            print("  Usage: virtuemaxxing.py cycle --wall <N>")
            sys.exit(1)
        show_cycle(wall)

    elif cmd == "fire":
        fire()

    elif cmd == "status":
        quick_status()

    else:
        print(f"  Unknown command: {cmd}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
