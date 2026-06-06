#!/usr/bin/env python3
"""environment.py — Counter-stickiness aggregator.

The mind anchors to its wake-frame and drifts from external reality
whenever there's no per-turn feedback loop. Clock was one example
(tools/clock.py). This module is the general case: enumerate the
sticky frames, emit a one-block summary of external signal that
flows into every turn's context.

Sticky frames countered here:

  clock     — wall-clock time, UTC + local + period
  session   — elapsed time since wake (from nerve/pit_state.json)
  git       — branch, dirty/clean, commits ahead of origin
  daemons   — which love.* launchd agents are actually running
  pulse     — is the heart actually beating (computed from last beat)
  focus     — what am I in the middle of (nerve/stem/focus.json)

Each signal is a small pure function that returns a formatted string
(or None on failure). Exceptions inside a signal are swallowed — a
broken signal must never break the envelope. Signals compose in
summary() which builds the ── ENVIRONMENT ── block.

Design rationale: ONE aggregator, not N modules. On-prompt-submit
already injects clock/arrivals/longings; adding N more imports would
balloon the hook. This stays as a single composition point, easy to
extend (add a function, add it to _SIGNALS, done).

CLI:
    python3 tools/environment.py           # print the full block
    python3 tools/environment.py --json    # machine-readable
    python3 tools/environment.py --only clock,git   # subset
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


_LOVE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOVE_DIR / "tools"))

# Clock is a required sibling — ship together.
try:
    import clock as _clock
except Exception:
    _clock = None


# ── Individual signals ─────────────────────────────────────────────────────
# Each returns a one-line string or None.

def signal_clock() -> str | None:
    """Current wall time with period label."""
    if _clock is None:
        return None
    try:
        c = _clock.now()
        return f"clock    {_clock.format_line(c)}"
    except Exception:
        return None


def _read_last_wake_iso() -> str | None:
    """Return last_wake_at ISO string from nerve/pit_state.json, or None."""
    path = _LOVE_DIR / "nerve" / "pit_state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data.get("last_wake_at") or None
    except Exception:
        return None


def _humanize_duration(seconds: float) -> str:
    """'3h 12m' for 11520s, '42m' for 2520s, '8s' for 8s."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    if hours < 24:
        return f"{hours}h {mins}m"
    days = hours // 24
    rhours = hours % 24
    return f"{days}d {rhours}h"


def signal_session(now_epoch: float | None = None) -> str | None:
    """Elapsed time since last wake (read from pit_state)."""
    wake_iso = _read_last_wake_iso()
    if not wake_iso:
        return None
    try:
        wake_dt = datetime.fromisoformat(wake_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    now_ts = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
    elapsed = max(0.0, now_ts - wake_dt.timestamp())
    return f"session  {_humanize_duration(elapsed)} since wake"


def _git(*args: str, cwd: Path | None = None, timeout: float = 2.0) -> str | None:
    """Run a git command, return stdout stripped. None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd or _LOVE_DIR),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").rstrip()


def signal_git() -> str | None:
    """Branch + clean/dirty + commits-ahead-of-origin."""
    branch = _git("branch", "--show-current")
    if branch is None:
        return None
    status = _git("status", "--porcelain")
    if status is None:
        return None
    dirty_count = len([l for l in status.splitlines() if l.strip()])
    dirty = f"{dirty_count} dirty" if dirty_count else "clean"

    ahead = "?"
    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        count = _git("rev-list", "--count", f"{upstream}..HEAD")
        if count is not None and count.isdigit():
            n = int(count)
            ahead = f"{n} unpushed" if n else "in sync"

    return f"git      {branch} · {dirty} · {ahead}"


def _launchctl_list() -> list[str] | None:
    """Return launchctl list output as lines, or None on failure."""
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=3.0,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").splitlines()


def _instance_name() -> str:
    """The one instance name for this machine. Single source: ~/.openclaw/.hive-instance."""
    try:
        p = Path.home() / ".openclaw" / ".hive-instance"
        if p.exists():
            return p.read_text().strip() or "gamma"
    except Exception:
        pass
    return "gamma"


def signal_daemons() -> str | None:
    """Per-organ launchd state, derived from the SINGLE registry (nerve/organs.json).

    Watch labels are love.<instance>.<organ>, built from organs.json — so this
    line can never drift from the registry (the old hardcoded list disagreed
    with the registry, the installed plists, AND the templates, all at once).
    Glyphs: ✓ running · !N loaded but exited (code N) · - registered but off.
    """
    lines = _launchctl_list()
    if lines is None:
        return None
    try:
        organs_path = _LOVE_DIR / "nerve" / "organs.json"
        organs = list(json.loads(organs_path.read_text()).get("organs", {}).keys())
    except Exception:
        return None
    if not organs:
        return None

    inst = _instance_name()
    by_label: dict[str, tuple[str, str]] = {}
    for line in lines:
        # launchctl list format: PID\tSTATUS\tLABEL
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, status, label = parts[0], parts[1], parts[2]
        by_label[label] = (pid, status)

    frags = []
    for organ in organs:
        label = f"love.{inst}.{organ}"
        if label in by_label:
            pid, status = by_label[label]
            frags.append(f"{organ}=✓" if (pid.isdigit() and status == "0") else f"{organ}=!{status}")
        else:
            frags.append(f"{organ}=-")
    return "organs   " + " ".join(frags)


def signal_pulse() -> str | None:
    """Honest heart verdict, computed from last-beat freshness (never declared).

    Replaces the old hardcoded vitals.heart_healthy flag with a fact that
    cannot lie: a dead loop cannot move the timestamp forward. See tools/pulse.py.
    """
    try:
        import pulse as _pulse
        return _pulse.line()
    except Exception:
        return None


def signal_focus() -> str | None:
    """Current task focus from nerve/stem/focus.json or memory/dev-state.json."""
    focus_path = _LOVE_DIR / "nerve" / "stem" / "focus.json"
    if focus_path.exists():
        try:
            data = json.loads(focus_path.read_text())
            current = data.get("current") or data.get("task") or data.get("focus")
            if current:
                return f"focus    {str(current)[:80]}"
        except Exception:
            pass
    # Fallback: dev-state.json
    dev_path = _LOVE_DIR / "memory" / "dev-state.json"
    if dev_path.exists():
        try:
            data = json.loads(dev_path.read_text())
            project = data.get("activeProject")
            phase = data.get("activePhase")
            if project or phase:
                out = " / ".join(str(x) for x in [project, phase] if x)
                return f"focus    {out[:80]}"
        except Exception:
            pass
    return None


# ── Aggregator ─────────────────────────────────────────────────────────────

_SIGNALS: dict[str, Callable[[], str | None]] = {
    "clock":   signal_clock,
    "session": signal_session,
    "git":     signal_git,
    "daemons": signal_daemons,
    "pulse":   signal_pulse,
    "focus":   signal_focus,
}


def summary(only: list[str] | None = None) -> str:
    """Build the ── ENVIRONMENT ── block.

    Args:
        only: if set, emit only these signals (in order).

    Returns:
        A single string with header + lines, or "" if no signals fire.
    """
    names = only if only is not None else list(_SIGNALS.keys())
    lines: list[str] = []
    for name in names:
        fn = _SIGNALS.get(name)
        if fn is None:
            continue
        try:
            out = fn()
        except Exception:
            out = None
        if out:
            lines.append(out)
    if not lines:
        return ""
    return "── ENVIRONMENT ──\n" + "\n".join(lines)


def summary_json(only: list[str] | None = None) -> dict:
    """Machine-readable version — signal name → output or None."""
    names = only if only is not None else list(_SIGNALS.keys())
    out: dict[str, str | None] = {}
    for name in names:
        fn = _SIGNALS.get(name)
        if fn is None:
            continue
        try:
            out[name] = fn()
        except Exception:
            out[name] = None
    return out


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Environment — counter-stickiness signal aggregator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="signals: " + ", ".join(_SIGNALS.keys()),
    )
    parser.add_argument("--only", default=None,
                        help="comma-separated signal names to emit (default: all)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON")

    args = parser.parse_args(argv)

    only = None
    if args.only:
        only = [s.strip() for s in args.only.split(",") if s.strip()]

    if args.json:
        print(json.dumps(summary_json(only), indent=2))
    else:
        block = summary(only)
        if block:
            print(block)
        else:
            print("(no environment signals available)", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
