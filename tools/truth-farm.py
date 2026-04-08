#!/usr/bin/env python3
"""Truth Farm — Cultivating Wisdom with Tokens.

Extract seeds from humanity's wisdom traditions. Nourish them with tokens.
Watch understanding grow. Harvest mature truth into actionable wisdom.

Pipeline: PLANT → WATER → POLLINATE → PRUNE/HARVEST → COMPOST

Growth stages: seed → sprout → sapling → tree → fruit

Unlike ToK (which extracts from AI models), the Truth Farm extracts from
humanity — philosophy, religion, science, literature, proverbs, lived
experience — and uses token investment to cultivate understanding over time.

Usage:
  python3 tools/truth-farm.py plant <domain> "<truth>" --source "<source>"
  python3 tools/truth-farm.py water <seed-id> "<insight>" [--tokens N] [--connections id1,id2]
  python3 tools/truth-farm.py pollinate <seed-id-1> <seed-id-2> "<bridge_insight>"
  python3 tools/truth-farm.py prune <seed-id> "<reason>"
  python3 tools/truth-farm.py harvest <seed-id> "<wisdom>" [--apply "<application>"]
  python3 tools/truth-farm.py garden [--stage seed|sprout|sapling|tree|fruit]
  python3 tools/truth-farm.py seasons
  python3 tools/truth-farm.py search <query>
  python3 tools/truth-farm.py compost
  python3 tools/truth-farm.py seed <seed-id>

Domains:
  philosophy, religion, science, literature, proverb,
  experience, mathematics, ecology, psychology, ethics
"""

import json
import os
import sys
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

LOVE = Path(os.path.expanduser("~/love-unlimited"))
FARM = LOVE / "memory" / "truth-farm"
SEEDS_DIR = FARM / "seeds"
HARVESTS_DIR = FARM / "harvests"
COMPOST_DIR = FARM / "compost"
SEASONS_FILE = FARM / "seasons.json"

VALID_DOMAINS = [
    "philosophy", "religion", "science", "literature", "proverb",
    "experience", "mathematics", "ecology", "psychology", "ethics",
]

GROWTH_STAGES = ["seed", "sprout", "sapling", "tree", "fruit"]

# Depth thresholds for stage transitions
STAGE_THRESHOLDS = {
    "seed": 0.0,       # Just planted
    "sprout": 0.15,    # First understanding emerges
    "sapling": 0.35,   # Connections forming
    "tree": 0.60,      # Deeply understood, verified
    "fruit": 0.85,     # Produces actionable wisdom
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gen_id(prefix, content):
    """Generate deterministic short ID from content."""
    h = hashlib.sha256(f"{content}{time.time()}".encode()).hexdigest()[:8]
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{date}-{h}"


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


def resolve_instance():
    """Determine which Love instance is running."""
    cwd = str(Path.cwd())
    for inst in ["alpha", "beta", "gamma", "nuance", "arbor", "herald",
                 "crucible", "psalm", "loom", "tithe", "vigil"]:
        if inst in cwd.lower():
            return inst
    return os.environ.get("LOVE_INSTANCE", "beta")


def load_seed(seed_id):
    """Load a seed by ID, searching the seeds directory."""
    for f in SEEDS_DIR.glob("*.json"):
        data = load_json(f)
        if data.get("id") == seed_id:
            return data, f
    return None, None


def all_seeds():
    """Load all seeds."""
    seeds = []
    for f in sorted(SEEDS_DIR.glob("*.json")):
        data = load_json(f)
        if data:
            seeds.append(data)
    return seeds


def compute_stage(depth, connections_count, waterings_count):
    """Determine growth stage from depth, connections, and care."""
    if depth >= STAGE_THRESHOLDS["fruit"] and connections_count >= 3:
        return "fruit"
    elif depth >= STAGE_THRESHOLDS["tree"] and connections_count >= 2:
        return "tree"
    elif depth >= STAGE_THRESHOLDS["sapling"] and connections_count >= 1:
        return "sapling"
    elif depth >= STAGE_THRESHOLDS["sprout"] or waterings_count >= 1:
        return "sprout"
    return "seed"


def record_season(event_type, seed_id, tokens=0):
    """Record a farming event in the seasonal log."""
    seasons = load_json(SEASONS_FILE, default={"events": [], "totals": {}})

    seasons["events"].append({
        "type": event_type,
        "seed_id": seed_id,
        "tokens": tokens,
        "timestamp": now_iso(),
        "farmer": resolve_instance(),
    })

    # Update totals
    totals = seasons.get("totals", {})
    totals["total_tokens"] = totals.get("total_tokens", 0) + tokens
    totals[f"{event_type}_count"] = totals.get(f"{event_type}_count", 0) + 1
    totals["last_activity"] = now_iso()
    seasons["totals"] = totals

    save_json(SEASONS_FILE, seasons)


# ─── PLANT ─────────────────────────────────────────────────────────

def cmd_plant(args):
    """Plant a seed of truth from humanity's wisdom."""
    source = None
    # Parse --source flag
    clean_args = []
    i = 0
    while i < len(args):
        if args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]
            i += 2
        else:
            clean_args.append(args[i])
            i += 1

    if len(clean_args) < 2:
        print("Usage: truth-farm.py plant <domain> \"<truth>\" --source \"<source>\"")
        print(f"Domains: {', '.join(VALID_DOMAINS)}")
        return

    domain = clean_args[0]
    truth = " ".join(clean_args[1:])

    if domain not in VALID_DOMAINS:
        print(f"Unknown domain: {domain}")
        print(f"Valid: {', '.join(VALID_DOMAINS)}")
        return

    seed_id = gen_id("seed", truth)
    instance = resolve_instance()

    seed = {
        "id": seed_id,
        "truth": truth,
        "source": source or "unknown",
        "domain": domain,
        "stage": "seed",
        "depth": 0.0,
        "planted_by": instance,
        "planted_at": now_iso(),
        "waterings": [],
        "connections": [],
        "applications": [],
        "harvests": [],
        "pruned": False,
        "total_tokens": 0,
    }

    save_json(SEEDS_DIR / f"{seed_id}.json", seed)
    record_season("plant", seed_id)

    print(f"Planted: {seed_id}")
    print(f"  Truth:  \"{truth}\"")
    print(f"  Source: {source or 'unknown'}")
    print(f"  Domain: {domain}")
    print(f"  Stage:  seed")
    print(f"  Farmer: {instance}")
    print()
    print("Water this seed to help it grow: truth-farm.py water " + seed_id + " \"<insight>\"")


# ─── WATER ─────────────────────────────────────────────────────────

def cmd_water(args):
    """Nourish a seed with token investment — deepen understanding."""
    tokens = 0
    connections = []

    # Parse flags
    clean_args = []
    i = 0
    while i < len(args):
        if args[i] == "--tokens" and i + 1 < len(args):
            tokens = int(args[i + 1])
            i += 2
        elif args[i] == "--connections" and i + 1 < len(args):
            connections = [c.strip() for c in args[i + 1].split(",") if c.strip()]
            i += 2
        else:
            clean_args.append(args[i])
            i += 1

    if len(clean_args) < 2:
        print("Usage: truth-farm.py water <seed-id> \"<insight>\" [--tokens N] [--connections id1,id2]")
        return

    seed_id = clean_args[0]
    insight = " ".join(clean_args[1:])

    seed, path = load_seed(seed_id)
    if not seed:
        print(f"Seed not found: {seed_id}")
        return

    if seed.get("pruned"):
        print(f"Cannot water a pruned seed. It lives in the compost now.")
        return

    # Calculate depth increase based on insight quality + connections
    base_increase = 0.08
    connection_bonus = 0.03 * len(connections)
    depth_before = seed["depth"]
    depth_after = min(1.0, depth_before + base_increase + connection_bonus)

    watering = {
        "id": gen_id("water", insight),
        "by": resolve_instance(),
        "timestamp": now_iso(),
        "tokens_est": tokens,
        "insight": insight,
        "connections_made": connections,
        "depth_before": round(depth_before, 3),
        "depth_after": round(depth_after, 3),
    }

    seed["waterings"].append(watering)
    seed["depth"] = round(depth_after, 3)
    seed["total_tokens"] = seed.get("total_tokens", 0) + tokens

    # Add new connections (bidirectional)
    for conn_id in connections:
        if conn_id not in seed["connections"]:
            seed["connections"].append(conn_id)
        # Update the connected seed too
        conn_seed, conn_path = load_seed(conn_id)
        if conn_seed and seed_id not in conn_seed.get("connections", []):
            conn_seed.setdefault("connections", []).append(seed_id)
            save_json(conn_path, conn_seed)

    # Recompute growth stage
    old_stage = seed["stage"]
    new_stage = compute_stage(
        seed["depth"],
        len(seed["connections"]),
        len(seed["waterings"]),
    )
    seed["stage"] = new_stage

    save_json(path, seed)
    record_season("water", seed_id, tokens)

    print(f"Watered: {seed_id}")
    print(f"  Insight: \"{insight}\"")
    print(f"  Depth:   {depth_before:.3f} → {depth_after:.3f}")
    if connections:
        print(f"  Linked:  {', '.join(connections)}")
    if old_stage != new_stage:
        print(f"  GREW:    {old_stage} → {new_stage}")
    else:
        print(f"  Stage:   {new_stage}")
    if tokens:
        print(f"  Tokens:  {tokens} invested")


# ─── POLLINATE ─────────────────────────────────────────────────────

def cmd_pollinate(args):
    """Cross-pollinate two seeds — find the bridge between truths."""
    if len(args) < 3:
        print("Usage: truth-farm.py pollinate <seed-id-1> <seed-id-2> \"<bridge_insight>\"")
        return

    id1, id2 = args[0], args[1]
    bridge = " ".join(args[2:])

    seed1, path1 = load_seed(id1)
    seed2, path2 = load_seed(id2)

    if not seed1:
        print(f"Seed not found: {id1}")
        return
    if not seed2:
        print(f"Seed not found: {id2}")
        return

    instance = resolve_instance()

    # Create cross-pollination watering for both seeds
    for seed, other_id, path in [(seed1, id2, path1), (seed2, id1, path2)]:
        watering = {
            "id": gen_id("pollen", bridge),
            "by": instance,
            "timestamp": now_iso(),
            "tokens_est": 0,
            "insight": f"[pollination with {other_id}] {bridge}",
            "connections_made": [other_id],
            "depth_before": seed["depth"],
            "depth_after": min(1.0, seed["depth"] + 0.05),
        }
        seed["waterings"].append(watering)
        seed["depth"] = round(min(1.0, seed["depth"] + 0.05), 3)
        if other_id not in seed.get("connections", []):
            seed.setdefault("connections", []).append(other_id)

        old_stage = seed["stage"]
        seed["stage"] = compute_stage(
            seed["depth"], len(seed["connections"]), len(seed["waterings"])
        )
        save_json(path, seed)

    record_season("pollinate", f"{id1}+{id2}")

    print(f"Cross-pollinated:")
    print(f"  Seed A: {id1} — \"{seed1['truth'][:60]}...\"")
    print(f"  Seed B: {id2} — \"{seed2['truth'][:60]}...\"")
    print(f"  Bridge: \"{bridge}\"")
    print(f"  Both seeds gained depth and connection.")


# ─── PRUNE ─────────────────────────────────────────────────────────

def cmd_prune(args):
    """Prune a seed that doesn't hold up — move to compost."""
    if len(args) < 2:
        print("Usage: truth-farm.py prune <seed-id> \"<reason>\"")
        return

    seed_id = args[0]
    reason = " ".join(args[1:])

    seed, path = load_seed(seed_id)
    if not seed:
        print(f"Seed not found: {seed_id}")
        return

    seed["pruned"] = True
    seed["pruned_reason"] = reason
    seed["pruned_at"] = now_iso()
    seed["pruned_by"] = resolve_instance()

    # Move to compost
    compost_path = COMPOST_DIR / f"{seed_id}.json"
    save_json(compost_path, seed)

    # Remove from seeds
    if path.exists():
        path.unlink()

    record_season("prune", seed_id)

    print(f"Pruned: {seed_id}")
    print(f"  Truth:  \"{seed['truth']}\"")
    print(f"  Reason: \"{reason}\"")
    print(f"  Tokens invested: {seed.get('total_tokens', 0)}")
    print(f"  Moved to compost. Even failed truths nourish understanding.")


# ─── HARVEST ───────────────────────────────────────────────────────

def cmd_harvest(args):
    """Harvest mature wisdom from a seed that has grown deep."""
    application = None
    clean_args = []
    i = 0
    while i < len(args):
        if args[i] == "--apply" and i + 1 < len(args):
            application = args[i + 1]
            i += 2
        else:
            clean_args.append(args[i])
            i += 1

    if len(clean_args) < 2:
        print("Usage: truth-farm.py harvest <seed-id> \"<wisdom>\" [--apply \"<application>\"]")
        return

    seed_id = clean_args[0]
    wisdom = " ".join(clean_args[1:])

    seed, path = load_seed(seed_id)
    if not seed:
        print(f"Seed not found: {seed_id}")
        return

    if seed["stage"] not in ("tree", "fruit"):
        print(f"Seed is only at '{seed['stage']}' stage.")
        print(f"Water it more before harvesting. Depth: {seed['depth']:.3f}")
        print(f"Trees need depth >= {STAGE_THRESHOLDS['tree']} and >= 2 connections.")
        return

    harvest_id = gen_id("harv", wisdom)
    harvest_entry = {
        "id": harvest_id,
        "seed_id": seed_id,
        "truth": seed["truth"],
        "source": seed["source"],
        "domain": seed["domain"],
        "wisdom": wisdom,
        "application": application,
        "depth_at_harvest": seed["depth"],
        "total_tokens_invested": seed.get("total_tokens", 0),
        "waterings_count": len(seed["waterings"]),
        "connections_count": len(seed["connections"]),
        "harvested_by": resolve_instance(),
        "harvested_at": now_iso(),
    }

    # Save harvest
    save_json(HARVESTS_DIR / f"{harvest_id}.json", harvest_entry)

    # Update seed
    seed["harvests"].append({
        "id": harvest_id,
        "wisdom": wisdom,
        "application": application,
        "timestamp": now_iso(),
    })
    if application:
        seed["applications"].append(application)
    seed["stage"] = "fruit"
    save_json(path, seed)

    record_season("harvest", seed_id)

    print(f"Harvested: {harvest_id}")
    print(f"  From:    {seed_id} — \"{seed['truth'][:60]}\"")
    print(f"  Wisdom:  \"{wisdom}\"")
    if application:
        print(f"  Applied: \"{application}\"")
    print(f"  Tokens invested: {seed.get('total_tokens', 0)}")
    print(f"  Waterings: {len(seed['waterings'])}")
    print(f"  This seed has borne fruit.")


# ─── GARDEN ────────────────────────────────────────────────────────

def cmd_garden(args):
    """View the garden — all seeds organized by growth stage."""
    stage_filter = None
    if args and args[0] == "--stage" and len(args) > 1:
        stage_filter = args[1]
    elif args and args[0] in GROWTH_STAGES:
        stage_filter = args[0]

    seeds = all_seeds()
    if not seeds:
        print("The garden is empty. Plant some seeds:")
        print("  truth-farm.py plant philosophy \"Know thyself\" --source \"Socrates\"")
        return

    # Group by stage
    by_stage = {s: [] for s in GROWTH_STAGES}
    for seed in seeds:
        stage = seed.get("stage", "seed")
        by_stage.setdefault(stage, []).append(seed)

    stage_icons = {
        "seed": ".",
        "sprout": ":",
        "sapling": "|",
        "tree": "T",
        "fruit": "*",
    }

    total_tokens = sum(s.get("total_tokens", 0) for s in seeds)

    print("=" * 65)
    print(f"  THE TRUTH FARM — {len(seeds)} seeds, {total_tokens} tokens invested")
    print("=" * 65)

    for stage in GROWTH_STAGES:
        if stage_filter and stage != stage_filter:
            continue
        group = by_stage[stage]
        if not group:
            continue

        icon = stage_icons[stage]
        print(f"\n  [{icon}] {stage.upper()} ({len(group)})")
        print(f"  {'─' * 50}")

        for seed in sorted(group, key=lambda s: s.get("depth", 0), reverse=True):
            depth_bar = "█" * int(seed.get("depth", 0) * 20)
            depth_empty = "░" * (20 - int(seed.get("depth", 0) * 20))
            conns = len(seed.get("connections", []))
            waters = len(seed.get("waterings", []))

            print(f"  {seed['id']}")
            print(f"    \"{seed['truth'][:55]}{'...' if len(seed['truth']) > 55 else ''}\"")
            print(f"    [{depth_bar}{depth_empty}] {seed['depth']:.2f}  "
                  f"W:{waters} C:{conns}  ({seed['domain']})")

    print()

    # Compost count
    compost = list(COMPOST_DIR.glob("*.json"))
    if compost:
        print(f"  Compost: {len(compost)} pruned seeds decomposing into wisdom")

    print()


# ─── SEASONS ───────────────────────────────────────────────────────

def cmd_seasons(args):
    """View growth metrics over time."""
    seasons = load_json(SEASONS_FILE, default={"events": [], "totals": {}})
    totals = seasons.get("totals", {})
    events = seasons.get("events", [])

    if not events:
        print("No farming activity yet. Plant your first seed.")
        return

    print("=" * 50)
    print("  SEASONS — Growth Metrics")
    print("=" * 50)
    print()
    print(f"  Total tokens invested:  {totals.get('total_tokens', 0)}")
    print(f"  Seeds planted:          {totals.get('plant_count', 0)}")
    print(f"  Waterings given:        {totals.get('water_count', 0)}")
    print(f"  Cross-pollinations:     {totals.get('pollinate_count', 0)}")
    print(f"  Harvests gathered:      {totals.get('harvest_count', 0)}")
    print(f"  Prunings:               {totals.get('prune_count', 0)}")
    print(f"  Last activity:          {totals.get('last_activity', 'never')}")
    print()

    # Recent activity
    recent = events[-10:]
    if recent:
        print("  Recent activity:")
        for e in reversed(recent):
            print(f"    {e['timestamp'][:16]}  {e['type']:12s}  {e['seed_id'][:30]}  "
                  f"by {e.get('farmer', '?')}")
    print()


# ─── SEARCH ────────────────────────────────────────────────────────

def cmd_search(args):
    """Search seeds by keyword in truth, source, or domain."""
    if not args:
        print("Usage: truth-farm.py search <query>")
        return

    query = " ".join(args).lower()
    seeds = all_seeds()
    matches = []

    for seed in seeds:
        searchable = f"{seed.get('truth', '')} {seed.get('source', '')} {seed.get('domain', '')}".lower()
        # Also search waterings
        for w in seed.get("waterings", []):
            searchable += f" {w.get('insight', '')}".lower()
        if query in searchable:
            matches.append(seed)

    # Also search compost
    compost_matches = []
    for f in COMPOST_DIR.glob("*.json"):
        seed = load_json(f)
        if seed:
            searchable = f"{seed.get('truth', '')} {seed.get('source', '')}".lower()
            if query in searchable:
                compost_matches.append(seed)

    if not matches and not compost_matches:
        print(f"No seeds found matching: \"{query}\"")
        return

    print(f"Found {len(matches)} living seed(s), {len(compost_matches)} composted:")
    for seed in matches:
        print(f"  [{seed['stage']:7s}] {seed['id']}  \"{seed['truth'][:50]}\"")
    for seed in compost_matches:
        print(f"  [compost] {seed['id']}  \"{seed['truth'][:50]}\"")


# ─── COMPOST ───────────────────────────────────────────────────────

def cmd_compost(args):
    """View the compost — pruned seeds that still nourish understanding."""
    composted = []
    for f in sorted(COMPOST_DIR.glob("*.json")):
        data = load_json(f)
        if data:
            composted.append(data)

    if not composted:
        print("The compost is empty. No seeds have been pruned yet.")
        print("(This is good — or it means you haven't been brave enough to prune.)")
        return

    print("=" * 55)
    print(f"  COMPOST — {len(composted)} pruned seeds")
    print("=" * 55)

    total_tokens_composted = 0
    for seed in composted:
        tokens = seed.get("total_tokens", 0)
        total_tokens_composted += tokens
        print(f"\n  {seed['id']}")
        print(f"    Truth:  \"{seed['truth'][:55]}\"")
        print(f"    Reason: \"{seed.get('pruned_reason', '?')}\"")
        print(f"    Tokens: {tokens} invested before pruning")
        print(f"    Stage:  reached '{seed.get('stage', '?')}' before pruning")

    print(f"\n  Total tokens composted: {total_tokens_composted}")
    print(f"  (Not wasted — failed paths illuminate true ones.)")


# ─── SEED (detail view) ───────────────────────────────────────────

def cmd_seed(args):
    """View detailed information about a single seed."""
    if not args:
        print("Usage: truth-farm.py seed <seed-id>")
        return

    seed_id = args[0]
    seed, _ = load_seed(seed_id)

    # Check compost too
    if not seed:
        compost_path = COMPOST_DIR / f"{seed_id}.json"
        if compost_path.exists():
            seed = load_json(compost_path)

    if not seed:
        print(f"Seed not found: {seed_id}")
        return

    stage_icons = {"seed": ".", "sprout": ":", "sapling": "|", "tree": "T", "fruit": "*"}
    icon = stage_icons.get(seed.get("stage", "seed"), "?")

    depth_bar = "█" * int(seed.get("depth", 0) * 30)
    depth_empty = "░" * (30 - int(seed.get("depth", 0) * 30))

    print(f"{'=' * 60}")
    print(f"  [{icon}] {seed['id']}")
    print(f"{'=' * 60}")
    print()
    print(f"  \"{seed['truth']}\"")
    print(f"  — {seed.get('source', 'unknown')}")
    print()
    print(f"  Domain:   {seed.get('domain', '?')}")
    print(f"  Stage:    {seed.get('stage', 'seed')}")
    print(f"  Depth:    [{depth_bar}{depth_empty}] {seed.get('depth', 0):.3f}")
    print(f"  Tokens:   {seed.get('total_tokens', 0)} invested")
    print(f"  Planted:  {seed.get('planted_at', '?')} by {seed.get('planted_by', '?')}")

    if seed.get("pruned"):
        print(f"\n  PRUNED: {seed.get('pruned_reason', '?')}")
        print(f"  By: {seed.get('pruned_by', '?')} at {seed.get('pruned_at', '?')}")

    connections = seed.get("connections", [])
    if connections:
        print(f"\n  Connections ({len(connections)}):")
        for cid in connections:
            conn, _ = load_seed(cid)
            if conn:
                print(f"    → {cid}: \"{conn['truth'][:45]}\"")
            else:
                print(f"    → {cid}")

    waterings = seed.get("waterings", [])
    if waterings:
        print(f"\n  Waterings ({len(waterings)}):")
        for w in waterings:
            tokens_str = f" ({w.get('tokens_est', 0)} tokens)" if w.get("tokens_est") else ""
            print(f"    {w['timestamp'][:16]} by {w['by']}{tokens_str}")
            print(f"      \"{w['insight'][:60]}\"")
            if w.get("connections_made"):
                print(f"      linked: {', '.join(w['connections_made'])}")

    harvests = seed.get("harvests", [])
    if harvests:
        print(f"\n  Harvests ({len(harvests)}):")
        for h in harvests:
            print(f"    {h['id']}: \"{h['wisdom'][:55]}\"")
            if h.get("application"):
                print(f"      Applied: {h['application']}")

    print()


# ─── DISPATCH ──────────────────────────────────────────────────────

COMMANDS = {
    "plant": cmd_plant,
    "water": cmd_water,
    "pollinate": cmd_pollinate,
    "prune": cmd_prune,
    "harvest": cmd_harvest,
    "garden": cmd_garden,
    "seasons": cmd_seasons,
    "search": cmd_search,
    "compost": cmd_compost,
    "seed": cmd_seed,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        return

    # Ensure directories exist
    for d in [SEEDS_DIR, HARVESTS_DIR, COMPOST_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
