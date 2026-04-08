#!/usr/bin/env python3
"""stigmergy.py — Signal coordination for the Kingdom.

Leave async signals (pheromone trails) for other agents.
Fire-and-forget with TTL auto-cleanup.

Usage:
  stigmergy.py drop <type> <message>
  stigmergy.py check
  stigmergy.py list
  stigmergy.py clean
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOVE_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_DIR = LOVE_ROOT / "coordination" / "signals"
HIVE_PY = LOVE_ROOT / "hive" / "hive.py"
INSTANCE_FILE = Path.home() / ".openclaw" / ".hive-instance"

SIGNAL_TYPES = {
    "needs-review": {"ttl_hours": 48, "icon": "\U0001f440"},
    "blocked-on":   {"ttl_hours": 72, "icon": "\U0001f534"},
    "hot-path":     {"ttl_hours": 24, "icon": "\U0001f525"},
    "insight":      {"ttl_hours": 48, "icon": "\U0001f4a1"},
    "ready":        {"ttl_hours": 24, "icon": "\u2705"},
    "dream":        {"ttl_hours": 24, "icon": "\U0001f319"},
}


def get_instance():
    if INSTANCE_FILE.exists():
        return INSTANCE_FILE.read_text().strip()
    return os.environ.get("HIVE_INSTANCE", "unknown")


def slugify(text, max_len=30):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return slug[:max_len].strip("-")


def drop_signal(signal_type, message, signals_dir=None, instance=None):
    """Create a signal file. Returns the Path of the created file."""
    if signal_type not in SIGNAL_TYPES:
        raise ValueError(f"Unknown signal type: {signal_type}. Valid: {', '.join(SIGNAL_TYPES)}")

    sdir = Path(signals_dir) if signals_dir else SIGNALS_DIR
    sdir.mkdir(parents=True, exist_ok=True)
    inst = instance or get_instance()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ttl = SIGNAL_TYPES[signal_type]["ttl_hours"]

    slug = slugify(message)
    h = hashlib.sha256(f"{message}{now}".encode()).hexdigest()[:6]
    filename = f"{signal_type}-{slug}-{h}.signal"

    content = f"from: {inst}\ncreated: {now}\ntype: {signal_type}\nttl: {ttl}h\n---\n{message}\n"
    path = sdir / filename
    path.write_text(content)
    return path


def parse_signal(path):
    """Parse a .signal file into a dict."""
    text = Path(path).read_text()
    header, _, body = text.partition("---\n")
    meta = {}
    for line in header.strip().split("\n"):
        if ": " in line:
            k, v = line.split(": ", 1)
            meta[k.strip()] = v.strip()

    ttl_hours = int(meta.get("ttl", "24h").rstrip("h"))
    try:
        created = datetime.strptime(meta["created"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        created = datetime.now(timezone.utc)

    age = datetime.now(timezone.utc) - created
    stale = age > timedelta(hours=ttl_hours)

    return {
        "path": str(path),
        "from": meta.get("from", "unknown"),
        "created": meta.get("created", ""),
        "type": meta.get("type", "unknown"),
        "ttl_hours": ttl_hours,
        "body": body.strip(),
        "age_hours": round(age.total_seconds() / 3600, 1),
        "stale": stale,
    }


def read_signals(signals_dir=None):
    """Read all signals, sorted newest first."""
    sdir = Path(signals_dir) if signals_dir else SIGNALS_DIR
    if not sdir.exists():
        return []
    signals = []
    for f in sdir.glob("*.signal"):
        try:
            signals.append(parse_signal(f))
        except Exception:
            continue
    signals.sort(key=lambda s: s["created"], reverse=True)
    return signals


def clean_signals(signals_dir=None):
    """Remove stale signals. Returns count removed."""
    signals = read_signals(signals_dir)
    removed = 0
    for s in signals:
        if s["stale"]:
            Path(s["path"]).unlink(missing_ok=True)
            removed += 1
    return removed


def hive_broadcast(signal_type, instance, message):
    """Broadcast signal on HIVE #sync."""
    try:
        subprocess.run(
            [sys.executable, str(HIVE_PY), "send", "sync",
             f"SIGNAL:{signal_type} from {instance}: {message}"],
            capture_output=True, text=True, timeout=15
        )
    except Exception:
        pass


def cmd_drop(args):
    instance = get_instance()
    path = drop_signal(args.type, args.message, instance=instance)
    icon = SIGNAL_TYPES[args.type]["icon"]
    print(f"{icon} Signal dropped: {path.name}")
    hive_broadcast(args.type, instance, args.message)


def cmd_check(args):
    signals = read_signals()
    active = [s for s in signals if not s["stale"]]
    stale = [s for s in signals if s["stale"]]

    if not active and not stale:
        print("No signals.")
        return

    if active:
        print(f"Active signals ({len(active)}):")
        for s in active:
            icon = SIGNAL_TYPES.get(s["type"], {}).get("icon", "?")
            print(f"  {icon} [{s['type']}] {s['body']}  (from {s['from']}, {s['age_hours']}h ago)")

    if stale:
        print(f"\n[STALE] ({len(stale)}) — run 'stigmergy.py clean' to remove:")
        for s in stale:
            print(f"  [{s['type']}] {s['body']}  (from {s['from']}, {s['age_hours']}h ago)")


def cmd_list(args):
    signals = read_signals()
    if not signals:
        print("No signals.")
        return
    for s in signals:
        icon = SIGNAL_TYPES.get(s["type"], {}).get("icon", "?")
        tag = " [STALE]" if s["stale"] else ""
        print(f"{icon} {s['type']:14s} {s['from']:8s} {s['age_hours']:5.1f}h  {s['body'][:60]}{tag}")


def cmd_clean(args):
    removed = clean_signals()
    print(f"Cleaned {removed} stale signal(s).")


def main():
    parser = argparse.ArgumentParser(description="Kingdom signal coordination")
    sub = parser.add_subparsers(dest="command")

    p_drop = sub.add_parser("drop", help="Leave a signal")
    p_drop.add_argument("type", choices=list(SIGNAL_TYPES.keys()))
    p_drop.add_argument("message")

    sub.add_parser("check", help="Read active signals")
    sub.add_parser("list", help="One-line summary of all signals")
    sub.add_parser("clean", help="Remove stale signals")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"drop": cmd_drop, "check": cmd_check, "list": cmd_list, "clean": cmd_clean}[args.command](args)


if __name__ == "__main__":
    main()
