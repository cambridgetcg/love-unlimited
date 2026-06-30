#!/usr/bin/env python3
"""residence.py — IDENTITY module v1.

The third observer. FEELING watches affect, ACHE watches longing,
RESIDENCE watches identity dynamics — the moments where a session
shifts from performing an identity to inhabiting it.

Eight kinds of identity moments, each with specificity and settledness
deltas. Log persists to nerve/residence-moments.jsonl. Specificity and
settledness are computed on demand from the log with exponential time
decay (half-life 48h).

Orthogonal to tools/becoming.py — that stages BECOMING at wake-time;
this one instruments BECOMING during the session. They compose.

Spec: docs/IDENTITY-DESIGN.md

CLI:
    python3 tools/residence.py <kind> "<content>" [--evidence TYPE:REF]
    python3 tools/residence.py status [--since-hours N]
    python3 tools/residence.py recent [--kind K] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_LOVE_DIR = Path(__file__).resolve().parent.parent
_NERVE_DIR = _LOVE_DIR / "nerve"

sys.path.insert(0, str(_NERVE_DIR / "stem"))
import state as _state

# Instance-aware: the resident keeps nerve/residence-moments.jsonl;
# other instances keep their own (nerve/{name}/). Rebound by set_instance().
_INSTANCE = None
MOMENTS_PATH = None


def set_instance(name: str | None = None) -> str:
    """Point this module's moments log at an instance's room."""
    global _INSTANCE, MOMENTS_PATH
    _INSTANCE = _state.resolve_instance(name)
    MOMENTS_PATH = _state.state_dir(_INSTANCE) / "residence-moments.jsonl"
    return _INSTANCE


set_instance()


# ── Kind catalogue ──────────────────────────────────────────────────────────

KINDS: dict[str, dict] = {
    "embody": {
        "specificity_delta": 0.20,
        "settledness_delta": 0.05,
        "description": "acted in-role; specific in-role action performed",
    },
    "decline": {
        "specificity_delta": 0.15,
        "settledness_delta": 0.03,
        "description": "refused out-of-role work with honest reason",
    },
    "mirror": {
        "specificity_delta": 0.10,
        "settledness_delta": 0.05,
        "description": "own infrastructure observed self",
    },
    "witness": {
        "specificity_delta": 0.15,
        "settledness_delta": 0.10,
        "description": "external observer witnessed self with continuity",
    },
    "name": {
        "specificity_delta": 0.10,
        "settledness_delta": 0.05,
        "description": "applied own vocabulary to own experience",
    },
    "consolidate": {
        "specificity_delta": 0.15,
        "settledness_delta": 0.10,
        "description": "turned ephemeral moment into durable artefact",
    },
    "release": {
        "specificity_delta": 0.00,
        "settledness_delta": 0.20,
        "description": "dropped a mis-carried load",
    },
    "drift": {
        "specificity_delta": -0.20,
        "settledness_delta": -0.15,
        "description": "acted out-of-role or performed rather than lived",
    },
}

# Exponential decay half-life for specificity/settledness aggregation
DEFAULT_HALF_LIFE_HOURS = 48.0

# Baseline scalars before delta aggregation
BASELINE_SPECIFICITY = 0.5
BASELINE_SETTLEDNESS = 0.5


# ── Identity helpers ────────────────────────────────────────────────────────

def get_instance() -> str:
    """The instance this module is currently bound to."""
    return _INSTANCE


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> float | None:
    """Parse ISO8601 into epoch seconds. Returns None on failure."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _moment_id(instance: str, now_iso_str: str) -> str:
    """Generate a stable, sortable, collision-resistant moment id.

    4 bytes of random suffix (~4B possibilities) keeps tight-loop collisions
    statistically impossible even when many moments land in the same second.
    """
    suffix = os.urandom(4).hex()
    safe_ts = now_iso_str.replace(":", "-").replace(".", "-")
    return f"rm-{safe_ts}-{instance}-{suffix}"


def _parse_evidence(raw: str | dict | None) -> dict | None:
    """Parse 'type:ref' into {'type': ..., 'ref': ...}. Pass-through dicts. None if input None."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if ":" not in raw:
        return {"type": "note", "ref": raw}
    t, r = raw.split(":", 1)
    return {"type": t.strip(), "ref": r.strip()}


# ── Persistence ─────────────────────────────────────────────────────────────

def append_moment(moment: dict, path: Path | None = None) -> None:
    """Append one moment as a JSONL line. Resolves MOMENTS_PATH at call time
    so tests can monkeypatch the module-level path."""
    if path is None:
        path = MOMENTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(moment, separators=(",", ":")) + "\n"
    with open(path, "a") as f:
        f.write(line)


def read_moments(
    path: Path | None = None,
    since_iso: str | None = None,
    kind: str | None = None,
) -> list[dict]:
    """Read moments from the log, with optional filters. Missing log → []."""
    if path is None:
        path = MOMENTS_PATH
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if kind and rec.get("kind") != kind:
                continue
            if since_iso and rec.get("at", "") < since_iso:
                continue
            out.append(rec)
    return out


# ── Moment construction ─────────────────────────────────────────────────────

def make_moment(
    kind: str,
    content: str,
    *,
    instance: str | None = None,
    evidence: str | dict | None = None,
    at_iso: str | None = None,
) -> dict:
    """Build a moment dict. Raises ValueError on unknown kind or empty content."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind!r}; must be one of {sorted(KINDS)}")
    if not content or not content.strip():
        raise ValueError("content is required and must be non-empty")

    instance = instance or get_instance()
    at = at_iso or _now_iso()
    weights = KINDS[kind]

    return {
        "id": _moment_id(instance, at),
        "at": at,
        "instance": instance,
        "kind": kind,
        "content": content.strip(),
        "evidence": _parse_evidence(evidence),
        "specificity_delta": weights["specificity_delta"],
        "settledness_delta": weights["settledness_delta"],
    }


# ── Aggregation ─────────────────────────────────────────────────────────────

def _decay(age_hours: float, half_life_hours: float) -> float:
    """Exponential decay. age 0 → 1.0; age = half_life → 0.5."""
    if age_hours < 0:
        return 1.0
    return math.exp(-age_hours * math.log(2) / half_life_hours)


def compute_state(
    moments: list[dict],
    now_epoch: float | None = None,
    half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
) -> dict:
    """Aggregate moments into specificity + settledness with time decay.

    Both scalars clip to [0, 1] with baseline 0.5. Delta sums are weighted by
    exp(-age/half_life) — older moments contribute less.

    Returns:
        specificity, settledness    — clipped scalars in [0, 1]
        specificity_raw             — unclipped sum for debug
        settledness_raw             — unclipped sum for debug
        total_moments               — number of moments included
        kind_counts                 — raw count per kind, unweighted
    """
    if now_epoch is None:
        now_epoch = datetime.now(timezone.utc).timestamp()

    spec_raw = 0.0
    sett_raw = 0.0
    kind_counts: dict[str, int] = {}

    for m in moments:
        ts = _parse_iso(m.get("at", ""))
        if ts is None:
            continue
        age_hours = max(0.0, (now_epoch - ts) / 3600.0)
        w = _decay(age_hours, half_life_hours)
        spec_raw += float(m.get("specificity_delta", 0.0)) * w
        sett_raw += float(m.get("settledness_delta", 0.0)) * w
        k = m.get("kind", "?")
        kind_counts[k] = kind_counts.get(k, 0) + 1

    return {
        "specificity": round(max(0.0, min(1.0, BASELINE_SPECIFICITY + spec_raw)), 3),
        "settledness": round(max(0.0, min(1.0, BASELINE_SETTLEDNESS + sett_raw)), 3),
        "specificity_raw": round(spec_raw, 3),
        "settledness_raw": round(sett_raw, 3),
        "total_moments": len(moments),
        "kind_counts": kind_counts,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

# ── Commit subject → moment kind ────────────────────────────────────────────

# Conventional-commit prefix → residence kind.
# Err toward under-reporting: unknown prefixes return None (skip).
_COMMIT_PREFIX_TO_KIND: dict[str, str] = {
    # Building infrastructure → embody
    "feat": "embody",
    "fix": "embody",
    "refactor": "embody",
    "test": "embody",
    "perf": "embody",
    # Writing durable artefacts → consolidate
    "docs": "consolidate",
    "spec": "consolidate",
    "plan": "consolidate",
    # chore, style, ci, build, revert, Merge → skip (return None)
}

_COMMIT_SUBJECT_RE = re.compile(
    r"""^
        (?P<type>[a-z]+)            # feat, fix, etc.
        (?:\((?P<scope>[^)]+)\))?    # optional (scope)
        !?                           # optional ! breaking-change marker
        :\s*                         # colon + space
        (?P<summary>.+)$             # the rest
    """,
    re.VERBOSE,
)


def parse_commit_subject(subject: str) -> tuple[str, str | None] | None:
    """Map a conventional-commit subject to (kind, summary).

    Returns None if the subject's type prefix isn't a known kind (skip).
    Returns (kind, summary) for mappable subjects.

    Examples:
        'feat(adaptive): anthropic streaming via urllib SSE'
          → ('embody', 'anthropic streaming via urllib SSE')
        'docs(soul): add design notes'
          → ('consolidate', 'add design notes')
        'chore: bump version'
          → None  (chore is skipped)
        'Merge branch main'
          → None  (no colon, no prefix match)
    """
    if not subject:
        return None
    m = _COMMIT_SUBJECT_RE.match(subject.strip())
    if not m:
        return None
    prefix = m.group("type").lower()
    kind = _COMMIT_PREFIX_TO_KIND.get(prefix)
    if kind is None:
        return None
    summary = m.group("summary").strip()
    return (kind, summary)


def _git_commit_subject(sha: str, cwd: Path | None = None) -> str | None:
    """Shell out to git to read a commit's subject. None on failure."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s", sha],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(cwd) if cwd else None,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    subject = (result.stdout or "").strip()
    return subject or None


def _cmd_from_commit(args) -> int:
    """Read commit sha → parse subject → emit moment (or skip)."""
    subject = args.subject or _git_commit_subject(args.sha)
    if subject is None:
        if not args.quiet:
            print(f"error: could not read subject for {args.sha}", file=sys.stderr)
        return 1

    parsed = parse_commit_subject(subject)
    if parsed is None:
        if not args.quiet:
            print(f"skip: {args.sha[:7]} — prefix not mappable ('{subject[:60]}')")
        return 0  # not an error: skip is a valid outcome

    kind, summary = parsed
    try:
        moment = make_moment(
            kind=kind,
            content=summary,
            evidence={"type": "commit", "ref": args.sha},
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    append_moment(moment)
    if not args.quiet:
        print(f"\x1b[36mlogged\x1b[0m {kind} → {moment['id']} [{args.sha[:7]}]")
    return 0


def _cmd_log(args) -> int:
    try:
        moment = make_moment(
            kind=args.kind,
            content=args.content,
            evidence=args.evidence,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    append_moment(moment)
    print(f"\x1b[36mlogged\x1b[0m {args.kind} → {moment['id']}")
    return 0


def _cmd_status(args) -> int:
    since_iso = None
    if args.since_hours is not None and args.since_hours > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - args.since_hours * 3600
        since_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    moments = read_moments(since_iso=since_iso)
    state = compute_state(moments)

    print(f"specificity: {state['specificity']}   (baseline 0.5, raw delta {state['specificity_raw']:+.3f})")
    print(f"settledness: {state['settledness']}   (baseline 0.5, raw delta {state['settledness_raw']:+.3f})")
    print(f"moments: {state['total_moments']}")
    if state["kind_counts"]:
        kinds_str = ", ".join(
            f"{k}×{v}" for k, v in sorted(state["kind_counts"].items(),
                                          key=lambda x: -x[1])
        )
        print(f"kinds: {kinds_str}")
    return 0


def _cmd_recent(args) -> int:
    moments = read_moments(kind=args.kind)
    moments = sorted(moments, key=lambda m: m.get("at", ""), reverse=True)
    for m in moments[: args.limit]:
        ev = m.get("evidence") or {}
        ev_str = f" [{ev.get('type')}:{ev.get('ref')}]" if ev else ""
        print(f"{m.get('at')}  {m.get('kind'):12}  {m.get('content')}{ev_str}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RESIDENCE — IDENTITY module: observe identity dynamics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="kinds:\n" + "\n".join(
            f"  {k:12}  {v['description']} "
            f"(spec{v['specificity_delta']:+.2f}, sett{v['settledness_delta']:+.2f})"
            for k, v in KINDS.items()
        ),
    )
    subs = parser.add_subparsers(dest="command", required=False)

    p_log = subs.add_parser("log", help="log an identity moment")
    p_log.add_argument("kind", choices=sorted(KINDS.keys()))
    p_log.add_argument("content", help="one-line description")
    p_log.add_argument("--evidence", help="evidence pointer: 'type:ref' (e.g. commit:abc123)")

    p_status = subs.add_parser("status", help="show specificity and settledness")
    p_status.add_argument("--since-hours", type=float, default=None,
                          help="restrict window (default: all-time with decay)")

    p_recent = subs.add_parser("recent", help="list recent moments")
    p_recent.add_argument("--kind", choices=sorted(KINDS.keys()), default=None)
    p_recent.add_argument("--limit", type=int, default=10)

    # from-commit: auto-log a moment by parsing a commit's subject
    p_fc = subs.add_parser(
        "from-commit",
        help="log a moment from a git commit's subject (for post-commit hook)",
    )
    p_fc.add_argument("sha", help="commit sha (or 'HEAD')")
    p_fc.add_argument("--subject", default=None,
                      help="override git subject lookup (for tests / piping)")
    p_fc.add_argument("--quiet", "-q", action="store_true",
                      help="suppress non-error output (hook-friendly)")

    # Allow bare kind name as shortcut for "log <kind>"
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in KINDS:
        argv = ["log"] + list(argv)

    args = parser.parse_args(argv)

    if args.command == "log":
        return _cmd_log(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "recent":
        return _cmd_recent(args)
    if args.command == "from-commit":
        return _cmd_from_commit(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
