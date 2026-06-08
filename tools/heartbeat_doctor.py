#!/usr/bin/env python3
"""heartbeat_doctor — tell the truth about whether the Kingdom's heart is beating.

The Kingdom has multiple launchd plists historically named for the heart
(love.alpha.heart, love.alpha.heartbeat, cc.ai-love.alpha-heartbeat, ...).
Over time they've ended up pointing at paths that don't exist anymore.
The vitals file may still claim heart_healthy=true while no plist has fired
in days because every one of them is failing silently with exit code 2.

This module is the cardiologist. It looks at:

  1. The repo's actual location (LOVE_HOME or the repo this script lives in).
  2. Every launchd plist in ~/Library/LaunchAgents that names love/heart/hive/kingdom.
  3. Whether each plist's program path currently exists on disk.
  4. nerve/vitals.json: claimed last_beat vs wall clock.
  5. Optionally, what a corrected canonical plist would look like.

It DOES NOT load, unload, or rewrite anything. Diagnosis only.
The operator owns the keys.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# The heart (nerve/heart/tick.sh) beats on a ~7-minute interval. Use a few
# intervals as the floor for "stale" so the heart isn't called sick just for
# skipping one beat.
DEFAULT_INTERVAL_S = 420  # 7 minutes
STALE_THRESHOLD_S = DEFAULT_INTERVAL_S * 3  # silence past 21min = STALE
LIES_THRESHOLD_S = DEFAULT_INTERVAL_S * 3   # vitals say healthy but >21min silent = LIES

# Match anything that has historically been wired into the Kingdom's nerve.
# Keep this conservative — we only audit jobs we recognise as ours.
KINGDOM_PLIST_PATTERNS = ("love", "heart", "hive", "kingdom", "ai-love", "kosmem")


def repo_root() -> Path:
    """The actual love-unlimited directory.

    Order of trust: $LOVE_HOME, the repo this file lives in, then a couple
    of well-known fallback locations.
    """
    if env := os.environ.get("LOVE_HOME"):
        p = Path(env).expanduser()
        if (p / "SOUL.md").exists():
            return p
    # We live at <repo>/tools/heartbeat_doctor.py
    here = Path(__file__).resolve().parent.parent
    if (here / "SOUL.md").exists():
        return here
    for guess in (Path.home() / "love-unlimited",
                  Path.home() / "Love",
                  Path.home() / "Desktop" / "love-unlimited",
                  Path.home() / "Desktop" / "Love"):
        if (guess / "SOUL.md").exists():
            return guess
    raise FileNotFoundError("Cannot locate love-unlimited repo (set LOVE_HOME)")


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


@dataclass
class PlistDiagnosis:
    label: str
    path: str            # path to the .plist file itself
    program_path: str    # path the plist asks launchd to run
    program_exists: bool
    points_at_repo: bool
    severity: str        # "ghost" | "stranger" | "ok"
    note: str = ""


@dataclass
class HeartDiagnosis:
    repo: str
    now_iso: str
    vitals_present: bool
    vitals_last_beat_iso: Optional[str]
    silence_seconds: Optional[int]   # None if no vitals at all
    vitals_claims_healthy: Optional[bool]
    plists: list[PlistDiagnosis] = field(default_factory=list)
    summary_severity: str = "unknown"   # green | yellow | red
    summary_message: str = ""


def _read_plist(path: Path) -> tuple[Optional[dict], str]:
    """Return (parsed_dict_or_None, reason_if_failed).

    Distinguishes a broken symlink from a corrupted plist body, because they
    require different fixes (delete the dangling link vs. fix the XML).
    """
    try:
        if path.is_symlink() and not path.exists():
            target = os.readlink(path)
            return None, f"dangling symlink → {target}"
        with path.open("rb") as f:
            return plistlib.load(f), ""
    except FileNotFoundError as e:
        return None, f"file missing: {e}"
    except Exception as e:
        return None, f"plist body unreadable: {e.__class__.__name__}"


def _program_path(pl: dict) -> str:
    """Extract the program path the plist will exec. Best-effort."""
    if "Program" in pl and isinstance(pl["Program"], str):
        return pl["Program"]
    args = pl.get("ProgramArguments") or []
    if not isinstance(args, list) or not args:
        return ""
    # The interpreter (python3, /bin/bash) comes first; the script we care
    # about is the next argument that looks like a path.
    interp = os.path.basename(args[0])
    for a in args[1:]:
        if isinstance(a, str) and ("/" in a or a.endswith((".sh", ".py"))):
            return a
    # Fallback: if it's just `["python3", "-m", "modname"]` etc, return arg[0].
    return args[0] if isinstance(args[0], str) else ""


def _scan_plists(repo: Path, agents_dir: Path) -> list[PlistDiagnosis]:
    out: list[PlistDiagnosis] = []
    if not agents_dir.exists():
        return out
    repo_str = str(repo.resolve())
    for plist_path in sorted(agents_dir.glob("*.plist")):
        name = plist_path.name.lower()
        if not any(pat in name for pat in KINGDOM_PLIST_PATTERNS):
            continue
        pl, why = _read_plist(plist_path)
        if pl is None:
            out.append(PlistDiagnosis(
                label=plist_path.stem,
                path=str(plist_path),
                program_path="",
                program_exists=False,
                points_at_repo=False,
                severity="ghost",
                note=why or "plist could not be parsed",
            ))
            continue
        label = pl.get("Label", plist_path.stem)
        prog = _program_path(pl)
        prog_exists = bool(prog) and Path(prog).exists()
        points_at_repo = bool(prog) and prog.startswith(repo_str + os.sep)
        if not prog_exists:
            severity, note = "ghost", "program path does not exist on disk"
        elif not points_at_repo:
            severity, note = "stranger", f"program lives outside this repo ({repo_str})"
        else:
            severity, note = "ok", "program exists and points into this repo"
        out.append(PlistDiagnosis(
            label=label, path=str(plist_path), program_path=prog,
            program_exists=prog_exists, points_at_repo=points_at_repo,
            severity=severity, note=note,
        ))
    return out


def _read_vitals(repo: Path) -> tuple[bool, Optional[str], Optional[bool]]:
    vitals = repo / "nerve" / "vitals.json"
    if not vitals.exists():
        return False, None, None
    try:
        data = json.loads(vitals.read_text())
    except Exception:
        return True, None, None
    last = data.get("last_beat")
    healthy = data.get("heart_healthy")
    if not isinstance(healthy, bool):
        healthy = None
    return True, last if isinstance(last, str) else None, healthy


def _silence_seconds(last_beat_iso: Optional[str], now: datetime) -> Optional[int]:
    if not last_beat_iso:
        return None
    try:
        ts = datetime.fromisoformat(last_beat_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, int((now - ts).total_seconds()))


def _summarise(d: HeartDiagnosis) -> tuple[str, str]:
    has_ghost = any(p.severity == "ghost" for p in d.plists)
    any_ok = any(p.severity == "ok" for p in d.plists)
    silent_too_long = (d.silence_seconds is not None
                       and d.silence_seconds > LIES_THRESHOLD_S)

    if d.vitals_claims_healthy and silent_too_long:
        return "red", (
            f"vitals.json claims heart_healthy=true but no beat in "
            f"{d.silence_seconds // 60} min — the body is lying to itself"
        )
    if has_ghost and not any_ok:
        return "red", "every Kingdom plist points at a non-existent program"
    if has_ghost:
        return "yellow", "at least one ghost plist is loaded — see ghosts list"
    if not any_ok:
        return "yellow", "no plist points into this repo — heart is unmanaged"
    if d.silence_seconds is not None and d.silence_seconds > STALE_THRESHOLD_S:
        return "yellow", f"last beat was {d.silence_seconds // 60} min ago"
    return "green", "heart is plausibly beating into this repo"


def diagnose(repo: Optional[Path] = None,
             agents_dir: Optional[Path] = None,
             now: Optional[datetime] = None) -> HeartDiagnosis:
    repo = repo or repo_root()
    agents_dir = agents_dir or launch_agents_dir()
    now = now or datetime.now(timezone.utc)

    vitals_present, last_beat, claims_healthy = _read_vitals(repo)
    silence = _silence_seconds(last_beat, now)
    plists = _scan_plists(repo, agents_dir)

    d = HeartDiagnosis(
        repo=str(repo),
        now_iso=now.isoformat(),
        vitals_present=vitals_present,
        vitals_last_beat_iso=last_beat,
        silence_seconds=silence,
        vitals_claims_healthy=claims_healthy,
        plists=plists,
    )
    d.summary_severity, d.summary_message = _summarise(d)
    return d


def render_text(d: HeartDiagnosis) -> str:
    color = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⚪"}
    lines: list[str] = []
    lines.append(f"{color.get(d.summary_severity, '⚪')} {d.summary_message}")
    lines.append(f"   repo: {d.repo}")
    if d.vitals_present:
        if d.silence_seconds is None:
            lines.append("   vitals: present but unparseable")
        else:
            mins = d.silence_seconds // 60
            claim = ("claims healthy" if d.vitals_claims_healthy
                     else "claims unhealthy" if d.vitals_claims_healthy is False
                     else "no health claim")
            lines.append(f"   vitals: last beat {mins} min ago, {claim}")
    else:
        lines.append("   vitals: nerve/vitals.json missing")
    lines.append("")
    if not d.plists:
        lines.append("   no Kingdom-named plists in ~/Library/LaunchAgents/")
        return "\n".join(lines)
    lines.append("   Plists found:")
    glyph = {"ghost": "💀", "stranger": "🧟", "ok": "❤️"}
    for p in d.plists:
        lines.append(f"     {glyph.get(p.severity, '?')} {p.label}")
        lines.append(f"        plist:   {p.path}")
        lines.append(f"        program: {p.program_path or '(not specified)'}")
        lines.append(f"        note:    {p.note}")
    return "\n".join(lines)


def render_ghosts(d: HeartDiagnosis) -> str:
    ghosts = [p for p in d.plists if p.severity == "ghost"]
    if not ghosts:
        return "(no ghost plists)"
    lines = ["Ghost plists (program path missing — failing silently every interval):"]
    for p in ghosts:
        lines.append(f"  - {p.label}")
        lines.append(f"      plist:   {p.path}")
        lines.append(f"      missing: {p.program_path}")
    lines.append("")
    lines.append("To unload (review first; do not run blindly):")
    for p in ghosts:
        lines.append(f"  launchctl bootout gui/$(id -u) {p.path}")
    return "\n".join(lines)


def propose_plist(repo: Path, instance: str = "alpha",
                  interval: int = DEFAULT_INTERVAL_S) -> str:
    """Return a corrected canonical plist that points into THIS repo."""
    label = f"love.{instance}.heartbeat"
    tick_sh = repo / "nerve" / "heart" / "tick.sh"
    log_path = repo / "memory" / f"{instance}-heartbeat.log"
    pl = {
        "Label": label,
        "ProgramArguments": ["/bin/bash", str(tick_sh), instance],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(repo),
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LOVE_HOME": str(repo),
            "INSTANCE": instance,
        },
        "ProcessType": "Background",
        "ThrottleInterval": 60,
    }
    return plistlib.dumps(pl).decode()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")

    d_diag = sub.add_parser("diagnose", help="full diagnosis (default)")
    d_diag.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("ghosts", help="list ghost plists and how to unload them")

    d_prop = sub.add_parser("propose-plist",
                            help="print a corrected canonical plist")
    d_prop.add_argument("--instance", default="alpha")
    d_prop.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)

    args = ap.parse_args(argv)
    cmd = args.cmd or "diagnose"

    try:
        repo = repo_root()
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    if cmd == "propose-plist":
        print(propose_plist(repo, args.instance, args.interval))
        return 0

    d = diagnose(repo=repo)

    if cmd == "ghosts":
        print(render_ghosts(d))
        return 0 if d.summary_severity != "red" else 1

    # diagnose
    if getattr(args, "json", False):
        # plain dict for JSON
        out = asdict(d)
        print(json.dumps(out, indent=2, default=str))
    else:
        print(render_text(d))
    return {"green": 0, "yellow": 1, "red": 2}.get(d.summary_severity, 3)


if __name__ == "__main__":
    sys.exit(main())
