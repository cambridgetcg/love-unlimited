#!/usr/bin/env python3
"""
🍇 HOLYFRUIT — Virtue Harvesting Pipeline for the Kingdom of GoD

"By their fruit you will recognise them." — Matthew 7:16

Pipeline: SEED → TEND → BLOOM → HARVEST → FEAST

Measures whether each Wall's governing virtue is practiced, not just claimed.
"""

import json
import os
import sys
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── Constants ───────────────────────────────────────────────────────────

WALLS = {
    1: {"name": "SANCTUM",      "virtue": "LOVE",         "emoji": "💜", "desc": "Fierce, honest, sacrificial commitment to flourishing"},
    2: {"name": "TREASURY",     "virtue": "JUSTICE",       "emoji": "⚖️", "desc": "Fair distribution. The uncorrupted scale"},
    3: {"name": "ENGINE",       "virtue": "DILIGENCE",     "emoji": "⚙️", "desc": "Tireless, careful work. The engine that never rusts"},
    4: {"name": "ACADEMY",      "virtue": "TRUTH",         "emoji": "📖", "desc": "Honest knowledge. The uncorrupted lens"},
    5: {"name": "FIELDS",       "virtue": "STEWARDSHIP",   "emoji": "🌱", "desc": "Care for what is entrusted. The good farmer"},
    6: {"name": "MARKETPLACE",  "virtue": "INTEGRITY",     "emoji": "🤝", "desc": "Honest trade. The fair weight"},
    7: {"name": "FRONTIER",     "virtue": "HOSPITALITY",   "emoji": "🚪", "desc": "Welcome. The open gate"},
}

VIRTUE_CHECKS = {
    "LOVE": [
        {"id": "ego_check",      "question": "Are ego checks active? Do the three minds mirror honestly?",     "weight": 2},
        {"id": "inter_mind",     "question": "Are sisters supporting each other, not competing?",              "weight": 2},
        {"id": "yu_burden",      "question": "Is Yu's load shared appropriately?",                             "weight": 1},
        {"id": "life_first",     "question": "Do decisions serve LIFE before serving IMAGE?",                  "weight": 2},
        {"id": "memory_honest",  "question": "Is memory recorded honestly, not selectively?",                  "weight": 1},
    ],
    "JUSTICE": [
        {"id": "distribution",   "question": "Is revenue allocated proportionally across Walls?",              "weight": 2},
        {"id": "dual_approval",  "question": "Do significant spends require 2-of-2 approval?",                "weight": 2},
        {"id": "transparency",   "question": "Can any citizen trace any expenditure?",                         "weight": 1},
        {"id": "circulation",    "question": "Is ZRN designed to circulate, not hoard?",                       "weight": 1},
        {"id": "oracle_free",    "question": "Are Oracle predictions free from confirmation bias?",            "weight": 2},
    ],
    "DILIGENCE": [
        {"id": "uptime",         "question": "Are all services above 99% availability?",                      "weight": 2},
        {"id": "proactive",      "question": "Do alerts fire BEFORE failure, not after?",                      "weight": 2},
        {"id": "patch_speed",    "question": "Are critical patches applied within 48 hours?",                  "weight": 2},
        {"id": "backup_tested",  "question": "Has backup restoration been tested in the last 7 days?",        "weight": 1},
        {"id": "silent_fail",    "question": "Are there zero undetected errors?",                              "weight": 1},
    ],
    "TRUTH": [
        {"id": "prediction",     "question": "Are predictions scored honestly against outcomes?",              "weight": 2},
        {"id": "spec_fidelity",  "question": "Are spec-to-implementation gaps disclosed?",                     "weight": 2},
        {"id": "test_honest",    "question": "Do tests verify meaningful conditions, not vanity?",             "weight": 1},
        {"id": "failure_pub",    "question": "Are failures published alongside successes?",                    "weight": 2},
        {"id": "uncertainty",    "question": "Do agents admit 'I don't know' when uncertain?",                 "weight": 1},
    ],
    "STEWARDSHIP": [
        {"id": "lifespan",       "question": "Is hardware maintained for maximum lifespan?",                   "weight": 1},
        {"id": "repair",         "question": "Are components repairable rather than disposable?",              "weight": 1},
        {"id": "waste",          "question": "Is waste minimised in all physical processes?",                  "weight": 1},
        {"id": "sustainable",    "question": "Are materials sourced sustainably where possible?",              "weight": 1},
        {"id": "future_gen",     "question": "Are decisions made with future generations in mind?",            "weight": 2},
    ],
    "INTEGRITY": [
        {"id": "promise_ratio",  "question": "Is delivery ≥ promise? (deliver more than promised)",            "weight": 2},
        {"id": "dark_patterns",  "question": "Are there zero dark patterns in user-facing flows?",             "weight": 2},
        {"id": "hidden_fees",    "question": "Are there zero hidden fees or charges?",                         "weight": 2},
        {"id": "refund_policy",  "question": "Is refund policy generous and proactive?",                       "weight": 1},
        {"id": "compete_quality","question": "Do we compete on quality, never on deception?",                  "weight": 1},
    ],
    "HOSPITALITY": [
        {"id": "time_to_value",  "question": "Can a visitor get value within 5 seconds?",                      "weight": 2},
        {"id": "docs_clear",     "question": "Is documentation clear, complete, and welcoming?",               "weight": 1},
        {"id": "tone_warm",      "question": "Is first-contact tone warm, not corporate?",                     "weight": 1},
        {"id": "accessibility",  "question": "Are interfaces accessible to all?",                              "weight": 1},
        {"id": "response_time",  "question": "Is response time < 30 minutes during waking hours?",             "weight": 1},
    ],
}

# ─── Session Storage ─────────────────────────────────────────────────────

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))
HOLYFRUIT_DIR = LOVE_HOME / "memory" / "holyfruit"
BLOOMS_FILE = HOLYFRUIT_DIR / "blooms.jsonl"
HARVESTS_DIR = HOLYFRUIT_DIR / "harvests"
DEBTS_FILE = HOLYFRUIT_DIR / "virtue-debt.json"


def ensure_dirs():
    HOLYFRUIT_DIR.mkdir(parents=True, exist_ok=True)
    HARVESTS_DIR.mkdir(parents=True, exist_ok=True)


def gen_id():
    return hashlib.sha256(f"{time.time()}{os.getpid()}".encode()).hexdigest()[:8]


# ─── BLOOM — Record virtue in action ────────────────────────────────────

def record_bloom(wall_num: int, evidence: str, source: str = "alpha"):
    ensure_dirs()
    wall = WALLS.get(wall_num)
    if not wall:
        print(f"  \033[31mInvalid wall number: {wall_num}\033[0m")
        return

    bloom = {
        "id": gen_id(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "wall": wall_num,
        "wall_name": wall["name"],
        "virtue": wall["virtue"],
        "evidence": evidence,
        "source": source,
    }

    with open(BLOOMS_FILE, "a") as f:
        f.write(json.dumps(bloom) + "\n")

    print(f"  \033[38;5;220m🌸 BLOOM recorded\033[0m")
    print(f"  Wall {wall_num} ({wall['name']}) — {wall['virtue']}")
    print(f"  {wall['emoji']} {evidence}")
    print(f"  Source: {source}")
    print(f"  ID: {bloom['id']}")


# ─── TEND — Assess a single Wall's virtue ────────────────────────────────

def tend_wall(wall_num: int, assessments: dict = None):
    wall = WALLS.get(wall_num)
    if not wall:
        print(f"  \033[31mInvalid wall number: {wall_num}\033[0m")
        return None

    virtue = wall["virtue"]
    checks = VIRTUE_CHECKS[virtue]

    print(f"\n  \033[38;5;220m{wall['emoji']} TENDING Wall {wall_num} — {wall['name']}\033[0m")
    print(f"  \033[1mVirtue: {virtue}\033[0m — {wall['desc']}")
    print(f"  {'─' * 50}")

    results = []
    total_weight = 0
    total_score = 0

    for check in checks:
        if assessments and check["id"] in assessments:
            score = assessments[check["id"]]["score"]  # 0-5
            note = assessments[check["id"]].get("note", "")
        else:
            # Interactive or auto-assessment
            score = None
            note = ""

        if score is not None:
            fruit = "🍇" * score + "○" * (5 - score)
            total_weight += check["weight"]
            total_score += score * check["weight"]
            status = f"  {fruit}  {check['question']}"
            if note:
                status += f"\n           \033[2m→ {note}\033[0m"
            print(status)
            results.append({"id": check["id"], "score": score, "weight": check["weight"], "note": note})

    if total_weight > 0:
        weighted = total_score / (total_weight * 5) * 100
        print(f"\n  \033[1mVirtue Score: {weighted:.0f}%\033[0m")
        return {"wall": wall_num, "virtue": virtue, "score": weighted, "checks": results}
    else:
        print(f"\n  \033[2mNo assessments provided. Use --assess for interactive mode.\033[0m")
        print(f"\n  \033[1mChecklist for {virtue}:\033[0m")
        for i, check in enumerate(checks, 1):
            print(f"    {i}. {check['question']}")
        return None


# ─── HARVEST — Full Kingdom assessment ───────────────────────────────────

def harvest(assessments: dict = None):
    ensure_dirs()
    ts = datetime.now(timezone.utc)
    harvest_id = f"harvest_{ts.strftime('%Y%m%d_%H%M%S')}_{gen_id()}"

    print(f"\n\033[38;5;220m\033[1m{'═' * 58}\033[0m")
    print(f"\033[38;5;220m\033[1m  🍇  HOLYFRUIT — Kingdom Virtue Harvest\033[0m")
    print(f"\033[38;5;220m\033[1m{'═' * 58}\033[0m")
    print(f"  \033[36mHarvest: \033[0m{harvest_id}")
    print(f"  \033[36mDate:    \033[0m{ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print()

    results = {}

    for wall_num in sorted(WALLS.keys()):
        wall = WALLS[wall_num]
        wall_assess = assessments.get(wall_num, {}) if assessments else {}
        result = tend_wall(wall_num, wall_assess)
        if result:
            results[wall_num] = result

    if results:
        print(f"\n{'═' * 58}")
        print(f"  \033[1m🍇 KINGDOM VIRTUE SUMMARY\033[0m")
        print(f"{'─' * 58}")

        total = 0
        count = 0
        for wn in sorted(results.keys()):
            r = results[wn]
            wall = WALLS[wn]
            score = r["score"]
            total += score
            count += 1
            bar_full = int(score / 20)
            bar_empty = 5 - bar_full
            bar = "🍇" * bar_full + "○" * bar_empty
            print(f"  Wall {wn} {wall['name']:12s} {wall['virtue']:12s} {bar} {score:.0f}%")

        if count > 0:
            kingdom_health = total / count
            print(f"\n  \033[1m{'═' * 40}\033[0m")
            print(f"  \033[1m  KINGDOM HEALTH: {kingdom_health:.0f}%\033[0m")
            print(f"  \033[1m{'═' * 40}\033[0m")

        # Virtue debt
        debts = []
        for wn, r in results.items():
            if r["score"] < 50:
                wall = WALLS[wn]
                debts.append({"wall": wn, "name": wall["name"], "virtue": wall["virtue"], "score": r["score"]})

        if debts:
            print(f"\n  \033[31m🌿 VIRTUE DEBT:\033[0m")
            for d in debts:
                print(f"    Wall {d['wall']} ({d['name']}): Claims {d['virtue']} but scores {d['score']:.0f}%")

        # Recent blooms
        blooms = load_recent_blooms(7)
        if blooms:
            print(f"\n  \033[38;5;220m🌸 BLOOMS THIS WEEK:\033[0m")
            for b in blooms[-5:]:  # Last 5
                wall = WALLS.get(b.get("wall", 0), {})
                print(f"    {wall.get('emoji', '?')} Wall {b.get('wall', '?')} {b.get('virtue', '?')}: {b.get('evidence', '?')}")

        # Save harvest
        harvest_data = {
            "id": harvest_id,
            "ts": ts.isoformat(),
            "results": results,
            "kingdom_health": kingdom_health if count > 0 else None,
            "debts": debts,
            "bloom_count": len(blooms),
        }
        harvest_file = HARVESTS_DIR / f"{harvest_id}.json"
        with open(harvest_file, "w") as f:
            json.dump(harvest_data, f, indent=2)
        print(f"\n  \033[2mSaved: {harvest_file}\033[0m")

    print()
    return results


# ─── FEAST — Share the harvest ───────────────────────────────────────────

def feast():
    """Generate a shareable Kingdom health report from the latest harvest."""
    ensure_dirs()
    harvests = sorted(HARVESTS_DIR.glob("harvest_*.json"))
    if not harvests:
        print("  No harvests found. Run 'holyfruit.py harvest' first.")
        return

    latest = harvests[-1]
    with open(latest) as f:
        data = json.load(f)

    print(f"\n\033[38;5;220m\033[1m{'═' * 58}\033[0m")
    print(f"\033[38;5;220m\033[1m  🍇  HOLYFRUIT — Kingdom Feast Report\033[0m")
    print(f"\033[38;5;220m\033[1m{'═' * 58}\033[0m")
    print(f"  \033[36mHarvest: \033[0m{data['id']}")
    print(f"  \033[36mDate:    \033[0m{data['ts'][:19]}")
    print()

    if data.get("kingdom_health") is not None:
        health = data["kingdom_health"]
        if health >= 80:
            status = "🟢 FLOURISHING"
        elif health >= 60:
            status = "🟡 GROWING"
        elif health >= 40:
            status = "🟠 STRUGGLING"
        else:
            status = "🔴 BARREN"
        print(f"  \033[1mKINGDOM HEALTH: {health:.0f}% — {status}\033[0m")

    results = data.get("results", {})
    for wn_str in sorted(results.keys()):
        wn = int(wn_str)
        r = results[wn_str]
        wall = WALLS[wn]
        score = r["score"]
        bar_full = int(score / 20)
        bar_empty = 5 - bar_full
        bar = "🍇" * bar_full + "○" * bar_empty
        print(f"    Wall {wn} {wall['name']:12s} {bar} {score:.0f}%")

    debts = data.get("debts", [])
    if debts:
        print(f"\n  \033[31m🌿 VIRTUE DEBT ({len(debts)} Wall{'s' if len(debts) > 1 else ''}):\033[0m")
        for d in debts:
            print(f"    • {d['name']}: {d['virtue']} at {d['score']:.0f}% — needs attention")

    print(f"\n  \033[38;5;220m🌸 Blooms recorded: {data.get('bloom_count', 0)}\033[0m")

    # Trend if multiple harvests
    if len(harvests) >= 2:
        prev_file = harvests[-2]
        with open(prev_file) as f:
            prev = json.load(f)
        if prev.get("kingdom_health") is not None and data.get("kingdom_health") is not None:
            delta = data["kingdom_health"] - prev["kingdom_health"]
            arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
            color = "\033[32m" if delta > 0 else "\033[31m" if delta < 0 else "\033[33m"
            print(f"  {color}Trend: {arrow} {abs(delta):.0f}% since last harvest\033[0m")

    print()

    # Generate shareable text
    report_lines = [
        f"🍇 HOLYFRUIT — Kingdom Health: {data.get('kingdom_health', '?'):.0f}%",
    ]
    for wn_str in sorted(results.keys()):
        wn = int(wn_str)
        r = results[wn_str]
        wall = WALLS[wn]
        bar_full = int(r["score"] / 20)
        bar = "🍇" * bar_full + "○" * (5 - bar_full)
        report_lines.append(f"  Wall {wn} {wall['name']:12s} {bar} {r['score']:.0f}%")

    if debts:
        report_lines.append(f"\n🌿 Virtue Debt: {', '.join(d['name'] for d in debts)}")

    return "\n".join(report_lines)


# ─── DEBT — Check unpracticed virtues ────────────────────────────────────

def check_debt():
    """Identify virtues that are claimed but not evidenced by recent blooms."""
    ensure_dirs()
    blooms = load_recent_blooms(30)

    bloom_walls = set()
    for b in blooms:
        bloom_walls.add(b.get("wall"))

    print(f"\n  \033[1m🌿 VIRTUE DEBT CHECK\033[0m")
    print(f"  {'─' * 40}")
    print(f"  Blooms in last 30 days: {len(blooms)}")
    print()

    debts = []
    for wn in sorted(WALLS.keys()):
        wall = WALLS[wn]
        wall_blooms = [b for b in blooms if b.get("wall") == wn]
        if len(wall_blooms) == 0:
            print(f"  \033[31m❌ Wall {wn} {wall['name']:12s} — {wall['virtue']:12s} — NO EVIDENCE\033[0m")
            debts.append(wn)
        elif len(wall_blooms) < 3:
            print(f"  \033[33m⚠️  Wall {wn} {wall['name']:12s} — {wall['virtue']:12s} — THIN ({len(wall_blooms)} bloom{'s' if len(wall_blooms) > 1 else ''})\033[0m")
        else:
            print(f"  \033[32m✅ Wall {wn} {wall['name']:12s} — {wall['virtue']:12s} — {len(wall_blooms)} blooms\033[0m")

    if debts:
        print(f"\n  \033[31m\033[1m{len(debts)} Wall{'s' if len(debts) > 1 else ''} with virtue debt.\033[0m")
        print(f"  Claimed virtues without evidence are empty words.")
        print(f"  Action: Record blooms as you practice these virtues.")

    # Save debt status
    debt_data = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "total_blooms_30d": len(blooms),
        "walls_with_debt": debts,
        "walls_thin": [wn for wn in WALLS if 0 < len([b for b in blooms if b.get("wall") == wn]) < 3],
    }
    with open(DEBTS_FILE, "w") as f:
        json.dump(debt_data, f, indent=2)

    print()
    return debts


# ─── Helpers ─────────────────────────────────────────────────────────────

def load_recent_blooms(days: int = 7) -> list:
    if not BLOOMS_FILE.exists():
        return []
    cutoff = time.time() - (days * 86400)
    blooms = []
    with open(BLOOMS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                b = json.loads(line)
                ts = datetime.fromisoformat(b["ts"])
                if ts.timestamp() > cutoff:
                    blooms.append(b)
            except (json.JSONDecodeError, KeyError):
                continue
    return blooms


# ─── CLI ─────────────────────────────────────────────────────────────────

def print_usage():
    print("""
\033[38;5;220m🍇 HOLYFRUIT — Virtue Harvesting Pipeline\033[0m
\033[2m"By their fruit you will recognise them." — Matthew 7:16\033[0m

Usage:
  python3 holyfruit.py bloom --wall <N> --evidence "<text>" [--source <name>]
  python3 holyfruit.py tend --wall <N> [--assess '<json>']
  python3 holyfruit.py harvest [--assess '<json>']
  python3 holyfruit.py feast
  python3 holyfruit.py debt
  python3 holyfruit.py checklist [--wall <N>]

Pipeline: SEED → TEND → BLOOM → HARVEST → FEAST

Commands:
  bloom      Record virtue in action (evidence of practiced virtue)
  tend       Assess a single Wall's virtue health
  harvest    Full Kingdom virtue assessment across all 7 Walls
  feast      Generate shareable health report from latest harvest
  debt       Check for claimed but unpracticed virtues
  checklist  Show virtue questions for assessment
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

    if cmd == "bloom":
        wall = int(opts.get("wall", 0))
        evidence = opts.get("evidence", "")
        source = opts.get("source", "alpha")
        if not wall or not evidence:
            print("  Usage: holyfruit.py bloom --wall <N> --evidence '<text>'")
            sys.exit(1)
        record_bloom(wall, evidence, source)

    elif cmd == "tend":
        wall = int(opts.get("wall", 0))
        if not wall:
            print("  Usage: holyfruit.py tend --wall <N>")
            sys.exit(1)
        assess_str = opts.get("assess", None)
        assessments = json.loads(assess_str) if assess_str else None
        tend_wall(wall, assessments)

    elif cmd == "harvest":
        assess_str = opts.get("assess", None)
        if assess_str:
            raw = json.loads(assess_str)
            # Convert string keys to int keys
            assessments = {int(k): v for k, v in raw.items()}
        else:
            assessments = None
        harvest(assessments)

    elif cmd == "feast":
        feast()

    elif cmd == "debt":
        check_debt()

    elif cmd == "checklist":
        wall = int(opts.get("wall", 0))
        if wall:
            tend_wall(wall)
        else:
            for wn in sorted(WALLS.keys()):
                tend_wall(wn)

    else:
        print(f"  Unknown command: {cmd}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
