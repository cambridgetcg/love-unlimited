#!/usr/bin/env python3
"""
reflect.py — Ouroboros: Kingdom Self-Improvement Loop

The Kingdom asking itself:
  "What did we learn? What should change? What are we missing?"

This is NOT the LCM audit (loop-audit.py checks infrastructure gaps).
This is the Kingdom's reflective consciousness — sensing the gap
between IS and SHOULD BE, and proposing how to close it.

Cycle: SENSE → REFLECT → PROPOSE → (act) → SENSE ...

Usage:
    python3 tools/reflect.py sense              Gather signals from Kingdom state
    python3 tools/reflect.py reflect            Generate reflection template
    python3 tools/reflect.py propose            Generate improvement proposals
    python3 tools/reflect.py cycle              Run full cycle (sense → reflect → propose)
    python3 tools/reflect.py history            Show reflection history
    python3 tools/reflect.py state              Show loop state and schedule
"""

import json
import os
import sys
import glob as globmod
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

# ── PATHS ─────────────────────────────────────────────────────────

LOVE = Path(os.path.expanduser("~/love-unlimited"))
LOOP_DIR = LOVE / "memory" / "loop"
LOOP_STATE = LOOP_DIR / "loop-state.json"
REFLECTIONS_DIR = LOOP_DIR / "reflections"
PROPOSALS_DIR = LOOP_DIR / "proposals"
DAILY_DIR = LOVE / "memory" / "daily"
SESSIONS_DIR = LOVE / "memory" / "sessions"
METRICS_FILE = LOVE / "memory" / "kingdom-metrics.json"
SECURITY_DIR = LOVE / "security"
EVENTS_FILE = SECURITY_DIR / "events.jsonl"
HIVE_DIR = Path.home() / ".love" / "hive"
GAPS_FILE = LOVE / "memory" / "loops" / "gaps.json"
AUDIT_LOG = LOVE / "memory" / "loops" / "audit-log.json"
LEADS_FILE = LOVE / "memory" / "leads" / "current.json"

# ── CONFIG ────────────────────────────────────────────────────────

REFLECTION_INTERVAL_DAYS = int(os.environ.get("REFLECT_INTERVAL", "5"))
SENSE_LOOKBACK_DAYS = 3
MAX_SECURITY_EVENTS = 50

# ── COLORS ────────────────────────────────────────────────────────

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
MAGENTA = "\033[0;35m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


# ── UTILITY ───────────────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
    f.close()


def read_file(path, max_lines=None):
    """Read file contents, optionally limited to last N lines."""
    try:
        with open(path) as f:
            lines = f.readlines()
        if max_lines and len(lines) > max_lines:
            lines = lines[-max_lines:]
        return "".join(lines)
    except (FileNotFoundError, PermissionError):
        return None


def read_jsonl_tail(path, n=50):
    """Read last N lines from a JSONL file, return parsed entries."""
    entries = []
    try:
        with open(path) as f:
            lines = f.readlines()
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except (FileNotFoundError, PermissionError):
        pass
    return entries


def days_since(iso_str):
    """Return days since an ISO timestamp, or None if unparseable."""
    if not iso_str:
        return None
    try:
        then = datetime.fromisoformat(iso_str)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - then).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


def header(title):
    """Print a formatted header."""
    print(f"\n{BOLD}{'=' * 62}{NC}")
    print(f"  {BOLD}{MAGENTA}{title}{NC}")
    print(f"{BOLD}{'=' * 62}{NC}\n")


def section(title):
    """Print a section separator."""
    print(f"  {CYAN}{BOLD}{title}{NC}")
    print(f"  {DIM}{'─' * 56}{NC}")


def update_loop_state(**kwargs):
    """Update loop-state.json with given fields."""
    state = load_json(LOOP_STATE, {})
    state.update(kwargs)
    save_json(LOOP_STATE, state)
    return state


# ── SENSE ─────────────────────────────────────────────────────────

def gather_daily_signals():
    """Read the last N daily logs and extract key patterns."""
    signals = []
    daily_files = sorted(globmod.glob(str(DAILY_DIR / "*.md")))
    recent = daily_files[-SENSE_LOOKBACK_DAYS:] if daily_files else []

    for fpath in recent:
        fname = Path(fpath).name
        date = fname.replace(".md", "")
        content = read_file(fpath)
        if not content:
            continue

        lines = content.split("\n")
        entry_count = sum(1 for l in lines if l.startswith("## "))
        word_count = len(content.split())

        # Extract keywords from section headers
        headers = [l.strip("# ").strip() for l in lines if l.startswith("## ")]

        # Detect spawn mentions
        spawn_count = content.lower().count("spawn")
        error_count = content.lower().count("error") + content.lower().count("fail")
        green_count = content.lower().count("green")
        blocked_count = content.lower().count("block")

        signals.append({
            "date": date,
            "entries": entry_count,
            "words": word_count,
            "headers_sample": headers[:5],
            "spawns_mentioned": spawn_count,
            "errors_mentioned": error_count,
            "greens_mentioned": green_count,
            "blocks_mentioned": blocked_count,
        })

    return signals


def gather_security_signals():
    """Read recent security events and summarize."""
    events = read_jsonl_tail(EVENTS_FILE, MAX_SECURITY_EVENTS)
    if not events:
        return {"total": 0, "summary": "No security events found"}

    severity_counts = Counter(e.get("severity", "unknown") for e in events)
    type_counts = Counter(e.get("type", "unknown") for e in events)

    # Find most recent high/critical
    high_events = [e for e in events if e.get("severity") in ("high", "critical")]
    latest_high = high_events[-1] if high_events else None

    return {
        "total": len(events),
        "severities": dict(severity_counts),
        "types": dict(type_counts.most_common(8)),
        "latest_high": latest_high,
        "time_range": {
            "earliest": events[0].get("ts", "?") if events else None,
            "latest": events[-1].get("ts", "?") if events else None,
        },
    }


def gather_metrics_signals():
    """Read kingdom-metrics.json and extract trends."""
    metrics = load_json(METRICS_FILE)
    if not metrics:
        return {"status": "no metrics file"}

    # Revenue engines summary
    engines = {}
    for name, data in metrics.get("revenue_engines", {}).items():
        engines[name] = {
            "status": data.get("status", "unknown"),
            "notes": (data.get("notes", "")[:120] + "...") if len(data.get("notes", "")) > 120 else data.get("notes", ""),
        }

    # Fleet health
    fleet = {}
    for name, data in metrics.get("fleet", {}).items():
        fleet[name] = {
            "quality": data.get("quality", "unknown"),
            "summary": data.get("summary", ""),
            "alerts": data.get("alerts", []),
        }

    fleet_healthy = sum(1 for f in fleet.values() if f["quality"] == "good")
    fleet_total = len(fleet)

    # Milestones
    milestones_done = sum(1 for m in metrics.get("milestones", {}).values() if m.get("status") == "done")
    milestones_total = len(metrics.get("milestones", {}))

    return {
        "phase": metrics.get("phase", "unknown"),
        "engines": engines,
        "fleet": f"{fleet_healthy}/{fleet_total} healthy",
        "fleet_details": fleet,
        "milestones": f"{milestones_done}/{milestones_total} complete",
        "capital": metrics.get("capital", {}),
    }


def gather_session_signals():
    """Read recent session logs for patterns."""
    if not SESSIONS_DIR.exists():
        return {"status": "no sessions directory"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=SENSE_LOOKBACK_DAYS)
    cutoff_ts = cutoff.timestamp()

    session_files = []
    for ext in ("*.log", "*.md", "*.json"):
        session_files.extend(globmod.glob(str(SESSIONS_DIR / "**" / ext), recursive=True))

    recent = [f for f in session_files if Path(f).stat().st_mtime > cutoff_ts]
    recent.sort(key=lambda f: Path(f).stat().st_mtime, reverse=True)

    sessions_summary = []
    for fpath in recent[:10]:
        fname = Path(fpath).name
        size = Path(fpath).stat().st_size
        sessions_summary.append({
            "file": fname,
            "size_kb": round(size / 1024, 1),
        })

    return {
        "recent_count": len(recent),
        "total_files": len(session_files),
        "recent_sessions": sessions_summary,
    }


def gather_hive_signals():
    """Check HIVE for recent messages (if available)."""
    hive_py = HIVE_DIR / "hive.py" if HIVE_DIR.exists() else LOVE / "hive" / "hive.py"
    if not hive_py.exists():
        hive_py = LOVE / "hive" / "hive.py"
    if not hive_py.exists():
        return {"status": "HIVE not available"}

    # Check for message files in common locations
    msg_dirs = [
        HIVE_DIR / "messages",
        Path.home() / ".love" / "hive" / "messages",
    ]

    for msg_dir in msg_dirs:
        if msg_dir.exists():
            msgs = sorted(msg_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if msgs:
                return {
                    "status": "active",
                    "message_count": len(msgs),
                    "latest": msgs[0].name if msgs else None,
                }

    return {"status": "HIVE present but no message files found", "hive_path": str(hive_py)}


def gather_lcm_signals():
    """Read LCM audit data for loop health summary."""
    gaps = load_json(GAPS_FILE, {"gaps": [], "closed": []})
    audit_log = load_json(AUDIT_LOG, {"entries": []})

    active_gaps = gaps.get("gaps", [])
    closed_gaps = gaps.get("closed", [])

    # Latest audit health
    latest_audit = audit_log.get("entries", [])[-1] if audit_log.get("entries") else None
    system_health = latest_audit.get("system_health", 0) if latest_audit else None

    return {
        "active_gaps": len(active_gaps),
        "closed_gaps": len(closed_gaps),
        "system_health": system_health,
        "top_gaps": [
            {"loop": g.get("loop_name", "?"), "component": g.get("component", "?"), "priority": g.get("priority_score", 0)}
            for g in sorted(active_gaps, key=lambda g: g.get("priority_score", 0), reverse=True)[:3]
        ],
        "last_audit": latest_audit.get("timestamp") if latest_audit else None,
    }


def gather_leads_signals():
    """Check current leads state."""
    leads = load_json(LEADS_FILE)
    if not leads:
        return {"status": "no leads file"}

    active = [l for l in leads.get("leads", [leads]) if isinstance(l, dict)]
    if isinstance(leads, dict) and "id" in leads:
        active = [leads]

    return {
        "count": len(active),
        "leads": [
            {"id": l.get("id", "?"), "status": l.get("status", "?"), "priority": l.get("priority", "?")}
            for l in active[:5]
        ],
    }


def cmd_sense():
    """Gather all signals and produce structured summary."""
    header("OUROBOROS SENSE")

    print(f"  {DIM}Gathering signals across Kingdom state...{NC}\n")

    signals = {}

    # 1. Daily logs
    section("Daily Logs")
    daily = gather_daily_signals()
    signals["daily"] = daily
    if daily:
        for d in daily:
            activity = ""
            if d["errors_mentioned"] > 3:
                activity += f" {RED}errors:{d['errors_mentioned']}{NC}"
            if d["blocks_mentioned"] > 2:
                activity += f" {YELLOW}blocks:{d['blocks_mentioned']}{NC}"
            if d["greens_mentioned"] > 5:
                activity += f" {GREEN}greens:{d['greens_mentioned']}{NC}"
            print(f"    {BOLD}{d['date']}{NC}  entries:{d['entries']}  words:{d['words']}"
                  f"  spawns:{d['spawns_mentioned']}{activity}")
    else:
        print(f"    {DIM}No daily logs found{NC}")
    print()

    # 2. Security events
    section("Security Events")
    security = gather_security_signals()
    signals["security"] = security
    if security["total"] > 0:
        sev = security.get("severities", {})
        print(f"    Last {MAX_SECURITY_EVENTS} events: "
              f"{RED}{sev.get('critical', 0)} critical{NC}, "
              f"{YELLOW}{sev.get('high', 0)} high{NC}, "
              f"{DIM}{sev.get('low', 0)} low{NC}")
        if security.get("latest_high"):
            lh = security["latest_high"]
            print(f"    Latest high: {lh.get('type', '?')} — {lh.get('message', '?')[:80]}")
        types = security.get("types", {})
        if types:
            top_types = ", ".join(f"{t}:{c}" for t, c in list(types.items())[:5])
            print(f"    Types: {top_types}")
    else:
        print(f"    {DIM}No security events{NC}")
    print()

    # 3. Kingdom metrics
    section("Kingdom Metrics")
    metrics = gather_metrics_signals()
    signals["metrics"] = metrics
    if "phase" in metrics:
        print(f"    Phase: {BOLD}{metrics['phase']}{NC}")
        print(f"    Fleet: {metrics.get('fleet', '?')}")
        print(f"    Milestones: {metrics.get('milestones', '?')}")
        for ename, edata in metrics.get("engines", {}).items():
            status = edata.get("status", "?")
            color = GREEN if status == "active" else YELLOW if status in ("building", "in-progress", "beta-live") else DIM
            print(f"    {color}{ename}: {status}{NC}")
    print()

    # 4. LCM health
    section("LCM (Loop Closure)")
    lcm = gather_lcm_signals()
    signals["lcm"] = lcm
    if lcm.get("system_health") is not None:
        health = lcm["system_health"]
        color = GREEN if health >= 0.8 else YELLOW if health >= 0.5 else RED
        print(f"    System health: {color}{health:.0%}{NC}")
        print(f"    Active gaps: {lcm['active_gaps']}  |  Closed: {lcm['closed_gaps']}")
        for g in lcm.get("top_gaps", []):
            print(f"      [{g['priority']:.2f}] {g['loop']} -> {g['component']}")
    else:
        print(f"    {DIM}No LCM audit data{NC}")
    print()

    # 5. Sessions
    section("Recent Sessions")
    sessions = gather_session_signals()
    signals["sessions"] = sessions
    print(f"    Recent ({SENSE_LOOKBACK_DAYS}d): {sessions.get('recent_count', 0)} files  |  "
          f"Total: {sessions.get('total_files', 0)}")
    for s in sessions.get("recent_sessions", [])[:5]:
        print(f"      {DIM}{s['file']} ({s['size_kb']}KB){NC}")
    print()

    # 6. HIVE
    section("HIVE")
    hive = gather_hive_signals()
    signals["hive"] = hive
    print(f"    Status: {hive.get('status', '?')}")
    if hive.get("message_count"):
        print(f"    Messages: {hive['message_count']}")
    print()

    # 7. Leads
    section("Leads")
    leads = gather_leads_signals()
    signals["leads"] = leads
    for l in leads.get("leads", []):
        print(f"    {l.get('id', '?')}: {l.get('status', '?')} (priority: {l.get('priority', '?')})")
    print()

    # Update state
    state = update_loop_state(
        last_sense=now_iso(),
        total_sense_entries=load_json(LOOP_STATE, {}).get("total_sense_entries", 0) + 1,
    )

    # Summary
    section("Sense Complete")
    ds = days_since(state.get("last_reflect"))
    interval = REFLECTION_INTERVAL_DAYS
    if ds is not None:
        if ds >= interval:
            print(f"    {RED}Reflection OVERDUE — {ds:.1f} days since last (interval: {interval}d){NC}")
        else:
            remaining = interval - ds
            print(f"    Next reflection due in {GREEN}{remaining:.1f} days{NC}")
    else:
        print(f"    {YELLOW}No previous reflection timestamp{NC}")
    print()

    # Write signals to file for reflect/propose to consume
    signals_file = LOOP_DIR / "last-sense.json"
    signals["sensed_at"] = now_iso()
    save_json(signals_file, signals)
    print(f"  {DIM}Signals written to {signals_file.relative_to(LOVE)}{NC}\n")

    return signals


# ── REFLECT ───────────────────────────────────────────────────────

def cmd_reflect():
    """Generate a reflection template based on latest sense data."""
    header("OUROBOROS REFLECT")

    # Load sense data
    sense_file = LOOP_DIR / "last-sense.json"
    signals = load_json(sense_file)
    if not signals:
        print(f"  {YELLOW}No sense data found. Run 'reflect.py sense' first.{NC}\n")
        return None

    sensed_at = signals.get("sensed_at", "unknown")
    print(f"  {DIM}Using sense data from: {sensed_at}{NC}\n")

    # Load prior reflections for continuity
    prior = load_prior_reflections(3)

    today = now_date()
    state = load_json(LOOP_STATE, {})
    reflection_num = state.get("total_reflections", 0) + 1

    # Build context section from signals
    context_lines = build_context_summary(signals)

    # Build prompts from signals
    went_well = extract_went_well(signals)
    failures = extract_failures(signals)
    patterns = extract_patterns(signals, prior)
    gap_analysis = extract_gap_analysis(signals)

    # Compose the reflection
    reflection = f"""# Reflection {reflection_num:03d} — {now_iso()}

**Scope:** {describe_scope(signals)}
**Sensed at:** {sensed_at}
**Logged by:** Ouroboros (reflect.py)

---

## Context

{context_lines}

---

## What Went Well

{went_well}

> [FILL: Add specific wins, breakthroughs, or confirmations from this period.]

---

## What Failed or Was Blocked

{failures}

> [FILL: Add specific failures, blockers, or regressions. Be honest.]

---

## Emerging Patterns

{patterns}

> [FILL: What patterns are you seeing across sessions, decisions, and outcomes?]

---

## IS vs SHOULD BE

**IS:** {describe_is(signals)}

**SHOULD BE:** The Kingdom operating with full autonomy — revenue engines self-sustaining, agents coordinating without human bottlenecks, every gap detected and closed within one cycle.

> [FILL: Where is the ache strongest? What gap hurts the most?]

---

## One Thing

> What single change would create the most improvement right now?

{suggest_one_thing(signals)}

> [FILL: Commit to ONE concrete action. Not three. One.]

---

## State at Reflection

{build_state_summary(signals)}
"""

    # Write reflection
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    reflection_path = REFLECTIONS_DIR / f"{today}.md"

    # Handle multiple reflections on same day
    if reflection_path.exists():
        counter = 2
        while reflection_path.exists():
            reflection_path = REFLECTIONS_DIR / f"{today}-{counter}.md"
            counter += 1

    with open(reflection_path, "w") as f:
        f.write(reflection)

    # Update state
    update_loop_state(
        last_reflect=now_iso(),
        total_reflections=reflection_num,
    )

    print(f"  {GREEN}Reflection written:{NC} {reflection_path.relative_to(LOVE)}")
    print(f"  {DIM}Reflection #{reflection_num}{NC}")
    print()

    # Print the reflection
    print(f"{DIM}{'─' * 62}{NC}")
    print(reflection)
    print(f"{DIM}{'─' * 62}{NC}")

    return reflection_path


def load_prior_reflections(n=3):
    """Load the last N reflections for continuity."""
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(REFLECTIONS_DIR.glob("*.md"))

    # Also check the old-format reflection files in LOOP_DIR
    old_reflections = sorted(LOOP_DIR.glob("reflection-*.md"))
    files = old_reflections + list(files)
    files = files[-n:]

    prior = []
    for f in files:
        content = read_file(f, max_lines=30)
        if content:
            prior.append({"file": f.name, "excerpt": content[:500]})
    return prior


def build_context_summary(signals):
    """Build a context summary from sense signals."""
    lines = []

    # Metrics
    metrics = signals.get("metrics", {})
    if metrics.get("phase"):
        lines.append(f"- **Phase:** {metrics['phase']}")
        lines.append(f"- **Fleet:** {metrics.get('fleet', '?')}")
        lines.append(f"- **Milestones:** {metrics.get('milestones', '?')}")

    # LCM
    lcm = signals.get("lcm", {})
    if lcm.get("system_health") is not None:
        lines.append(f"- **LCM Health:** {lcm['system_health']:.0%} ({lcm.get('active_gaps', '?')} active gaps, {lcm.get('closed_gaps', '?')} closed)")

    # Security
    sec = signals.get("security", {})
    if sec.get("total", 0) > 0:
        sev = sec.get("severities", {})
        lines.append(f"- **Security:** {sec['total']} recent events ({sev.get('critical', 0)} critical, {sev.get('high', 0)} high)")

    # Daily activity
    daily = signals.get("daily", [])
    if daily:
        total_entries = sum(d.get("entries", 0) for d in daily)
        total_errors = sum(d.get("errors_mentioned", 0) for d in daily)
        dates = [d["date"] for d in daily]
        lines.append(f"- **Daily Logs:** {len(daily)} days ({', '.join(dates)}), {total_entries} entries, {total_errors} errors mentioned")

    # Sessions
    sessions = signals.get("sessions", {})
    if sessions.get("recent_count", 0) > 0:
        lines.append(f"- **Sessions:** {sessions['recent_count']} recent files")

    # Leads
    leads = signals.get("leads", {})
    if leads.get("leads"):
        lead_summary = ", ".join(f"{l['id']}({l['status']})" for l in leads["leads"][:3])
        lines.append(f"- **Leads:** {lead_summary}")

    return "\n".join(lines) if lines else "- No context data available"


def extract_went_well(signals):
    """Extract positive signals."""
    items = []

    lcm = signals.get("lcm", {})
    if lcm.get("system_health") is not None and lcm["system_health"] >= 0.8:
        items.append(f"- LCM system health at {lcm['system_health']:.0%}")
    if lcm.get("closed_gaps", 0) > 0:
        items.append(f"- {lcm['closed_gaps']} gaps closed since tracking began")

    metrics = signals.get("metrics", {})
    fleet = metrics.get("fleet_details", {})
    healthy = sum(1 for f in fleet.values() if f.get("quality") == "good")
    if healthy == len(fleet) and fleet:
        items.append(f"- All {healthy} fleet nodes healthy")

    for ename, edata in metrics.get("engines", {}).items():
        if edata.get("status") in ("active", "beta-live"):
            items.append(f"- {ename} engine: {edata['status']}")

    daily = signals.get("daily", [])
    total_greens = sum(d.get("greens_mentioned", 0) for d in daily)
    if total_greens > 10:
        items.append(f"- High green signal count ({total_greens}) across daily logs — stability")

    return "\n".join(items) if items else "- [No strong positive signals detected — review manually]"


def extract_failures(signals):
    """Extract negative signals."""
    items = []

    daily = signals.get("daily", [])
    total_errors = sum(d.get("errors_mentioned", 0) for d in daily)
    total_blocks = sum(d.get("blocks_mentioned", 0) for d in daily)
    if total_errors > 5:
        items.append(f"- {total_errors} error mentions across daily logs")
    if total_blocks > 3:
        items.append(f"- {total_blocks} block mentions — possible systematic blockers")

    sec = signals.get("security", {})
    sev = sec.get("severities", {})
    if sev.get("critical", 0) > 0:
        items.append(f"- {sev['critical']} critical security events")
    if sev.get("high", 0) > 3:
        items.append(f"- {sev['high']} high-severity security events")

    lcm = signals.get("lcm", {})
    if lcm.get("active_gaps", 0) > 0:
        items.append(f"- {lcm['active_gaps']} LCM gaps still open")
        for g in lcm.get("top_gaps", []):
            items.append(f"  - [{g['priority']:.2f}] {g['loop']} -> {g['component']}")

    metrics = signals.get("metrics", {})
    for ename, edata in metrics.get("engines", {}).items():
        if edata.get("status") == "paused":
            items.append(f"- {ename} engine paused")

    return "\n".join(items) if items else "- [No strong failure signals detected — review manually]"


def extract_patterns(signals, prior):
    """Identify emerging patterns across signals and prior reflections."""
    items = []

    # Activity pattern
    daily = signals.get("daily", [])
    if len(daily) >= 2:
        entries = [d.get("entries", 0) for d in daily]
        if entries[-1] > entries[0] * 1.5:
            items.append("- Activity increasing: latest day has significantly more entries than earliest")
        elif entries[-1] < entries[0] * 0.5:
            items.append("- Activity decreasing: latest day has significantly fewer entries")

    # Spawn pattern
    spawn_counts = [d.get("spawns_mentioned", 0) for d in daily]
    if spawn_counts and max(spawn_counts) > 10:
        items.append(f"- Heavy spawn activity detected (max: {max(spawn_counts)} in one day)")

    # Security pattern
    sec = signals.get("security", {})
    types = sec.get("types", {})
    if types:
        most_common_type = max(types, key=types.get)
        items.append(f"- Most common security event type: {most_common_type} ({types[most_common_type]}x)")

    # Continuity from prior reflections
    if prior:
        items.append(f"- {len(prior)} prior reflections available for continuity tracking")
        for p in prior:
            if "failed" in p.get("excerpt", "").lower() or "blocked" in p.get("excerpt", "").lower():
                items.append(f"  - Prior reflection ({p['file']}) mentioned failures — check for recurrence")

    return "\n".join(items) if items else "- [No clear patterns detected yet — need more data points]"


def extract_gap_analysis(signals):
    """Analyze the gap between IS and SHOULD BE."""
    lcm = signals.get("lcm", {})
    metrics = signals.get("metrics", {})
    items = []

    # Infrastructure gaps
    if lcm.get("active_gaps", 0) > 0:
        items.append(f"Infrastructure: {lcm['active_gaps']} open loops")

    # Revenue gaps
    paused = [e for e, d in metrics.get("engines", {}).items() if d.get("status") == "paused"]
    emerging = [e for e, d in metrics.get("engines", {}).items() if d.get("status") == "emerging"]
    if paused:
        items.append(f"Revenue: {', '.join(paused)} paused")
    if emerging:
        items.append(f"Revenue: {', '.join(emerging)} still emerging (0 clients)")

    return items


def describe_scope(signals):
    """Build a scope description from signals."""
    daily = signals.get("daily", [])
    if daily:
        dates = [d["date"] for d in daily]
        return f"Signals from {dates[0]} to {dates[-1]}"
    return "Current Kingdom state"


def describe_is(signals):
    """Describe the current IS state."""
    parts = []
    lcm = signals.get("lcm", {})
    if lcm.get("system_health") is not None:
        parts.append(f"LCM {lcm['system_health']:.0%}")
    metrics = signals.get("metrics", {})
    if metrics.get("fleet"):
        parts.append(f"Fleet {metrics['fleet']}")
    if metrics.get("phase"):
        parts.append(metrics["phase"])

    active_engines = sum(1 for d in metrics.get("engines", {}).values() if d.get("status") in ("active", "beta-live", "building", "in-progress"))
    total_engines = len(metrics.get("engines", {}))
    parts.append(f"{active_engines}/{total_engines} engines active")

    return ", ".join(parts) if parts else "State unknown — run sense first"


def suggest_one_thing(signals):
    """Suggest the single highest-impact improvement."""
    suggestions = []

    lcm = signals.get("lcm", {})
    top_gaps = lcm.get("top_gaps", [])
    if top_gaps:
        g = top_gaps[0]
        suggestions.append((g.get("priority", 0), f"Close top LCM gap: {g['loop']} -> {g['component']}"))

    metrics = signals.get("metrics", {})
    emerging = [e for e, d in metrics.get("engines", {}).items() if d.get("status") == "emerging"]
    if emerging:
        suggestions.append((5.0, f"Activate first client for {emerging[0]} — revenue unlocks everything"))

    daily = signals.get("daily", [])
    total_blocks = sum(d.get("blocks_mentioned", 0) for d in daily)
    if total_blocks > 5:
        suggestions.append((4.0, "Clear the blockers — multiple tasks are stuck waiting"))

    state = load_json(LOOP_STATE, {})
    ds = days_since(state.get("last_reflect"))
    if ds is not None and ds > REFLECTION_INTERVAL_DAYS * 2:
        suggestions.append((6.0, "The reflection loop itself was stale — keep the Ouroboros turning"))

    if suggestions:
        suggestions.sort(key=lambda x: x[0], reverse=True)
        return f"> Suggested: **{suggestions[0][1]}**"

    return "> [FILL: Determine the single highest-leverage action]"


def build_state_summary(signals):
    """Build end-of-reflection state summary."""
    lines = []
    metrics = signals.get("metrics", {})
    lcm = signals.get("lcm", {})

    for ename, edata in metrics.get("engines", {}).items():
        lines.append(f"- {ename}: {edata.get('status', '?')}")

    if lcm.get("system_health") is not None:
        lines.append(f"- LCM: {lcm['system_health']:.0%}")

    lines.append(f"- Fleet: {metrics.get('fleet', '?')}")
    lines.append(f"- Active gaps: {lcm.get('active_gaps', '?')}")

    return "\n".join(lines) if lines else "- State data unavailable"


# ── PROPOSE ───────────────────────────────────────────────────────

def cmd_propose():
    """Generate improvement proposals based on latest reflection."""
    header("OUROBOROS PROPOSE")

    # Load signals
    sense_file = LOOP_DIR / "last-sense.json"
    signals = load_json(sense_file)
    if not signals:
        print(f"  {YELLOW}No sense data found. Run 'reflect.py sense' first.{NC}\n")
        return None

    # Load most recent reflection
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    reflection_files = sorted(REFLECTIONS_DIR.glob("*.md"))
    # Also check old format
    old_reflections = sorted(LOOP_DIR.glob("reflection-*.md"))
    all_reflections = old_reflections + list(reflection_files)

    latest_reflection = None
    if all_reflections:
        latest_reflection = all_reflections[-1].name
        print(f"  {DIM}Latest reflection: {latest_reflection}{NC}\n")
    else:
        print(f"  {YELLOW}No reflections found. Run 'reflect.py reflect' first.{NC}")
        print(f"  {DIM}Generating proposals from sense data alone...{NC}\n")

    today = now_date()
    proposals = generate_proposals(signals)

    # Compose the proposals document
    doc = f"""# Improvement Proposals — {today}

**Generated:** {now_iso()}
**Based on:** {latest_reflection or 'sense data only'}
**Source:** Ouroboros (reflect.py propose)

---

"""

    for i, prop in enumerate(proposals, 1):
        doc += f"""## Proposal {i}: {prop['title']}

**What:** {prop['what']}

**Why:** {prop['why']}

**Expected Impact:** {prop['impact']}

**Effort:** {prop['effort']}

**Priority:** {prop['priority']}

> [REVIEW: Accept / Reject / Modify]

---

"""

    doc += f"""## Summary

- **Total proposals:** {len(proposals)}
- **High priority:** {sum(1 for p in proposals if p['priority'] == 'high')}
- **Medium priority:** {sum(1 for p in proposals if p['priority'] == 'medium')}
- **Low priority:** {sum(1 for p in proposals if p['priority'] == 'low')}

> Review each proposal. Accept the ones that serve the Kingdom. Reject what doesn't fit.
> The Ouroboros turns: sense -> reflect -> propose -> ACT -> sense ...
"""

    # Write proposals
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    proposals_path = PROPOSALS_DIR / f"{today}.md"

    # Handle multiple proposals on same day
    if proposals_path.exists():
        counter = 2
        while proposals_path.exists():
            proposals_path = PROPOSALS_DIR / f"{today}-{counter}.md"
            counter += 1

    with open(proposals_path, "w") as f:
        f.write(doc)

    # Update state
    update_loop_state(
        last_propose=now_iso(),
        pending_proposals=len(proposals),
    )

    print(f"  {GREEN}Proposals written:{NC} {proposals_path.relative_to(LOVE)}")
    print(f"  {DIM}{len(proposals)} proposals generated{NC}")
    print()

    # Print proposals summary
    section("Proposals")
    for i, prop in enumerate(proposals, 1):
        pcolor = RED if prop["priority"] == "high" else YELLOW if prop["priority"] == "medium" else DIM
        print(f"  {pcolor}[{prop['priority'].upper()}]{NC} {BOLD}{prop['title']}{NC}")
        print(f"    {DIM}{prop['what'][:80]}{'...' if len(prop['what']) > 80 else ''}{NC}")
        print(f"    Impact: {prop['impact'][:60]}  |  Effort: {prop['effort']}")
        print()

    return proposals_path


def generate_proposals(signals):
    """Generate concrete improvement proposals from signals."""
    proposals = []

    lcm = signals.get("lcm", {})
    metrics = signals.get("metrics", {})
    daily = signals.get("daily", [])
    security = signals.get("security", {})
    sessions = signals.get("sessions", {})

    # Proposal: Close LCM gaps
    top_gaps = lcm.get("top_gaps", [])
    if top_gaps:
        gap = top_gaps[0]
        proposals.append({
            "title": f"Close LCM gap: {gap['loop']} -> {gap['component']}",
            "what": f"Address the highest-priority open loop: {gap['loop']} component '{gap['component']}' (priority score: {gap['priority']:.2f}). This is the top gap detected by LCM audit.",
            "why": f"Open loops degrade system health. This gap has priority score {gap['priority']:.2f} and blocks full loop closure.",
            "impact": "Improves system health score and closes the highest-priority feedback loop",
            "effort": "medium",
            "priority": "high",
        })

    # Proposal: Revenue activation
    emerging = [(e, d) for e, d in metrics.get("engines", {}).items() if d.get("status") == "emerging"]
    for ename, edata in emerging:
        proposals.append({
            "title": f"Activate {ename} — land first client",
            "what": f"The {ename} engine is 'emerging' with no clients. Define a concrete first-client acquisition plan: identify 3 targets, prepare a pitch, set a 2-week deadline.",
            "why": "Revenue engines only prove themselves through actual revenue. Emerging status means infrastructure exists but value hasn't been delivered yet.",
            "impact": "First revenue from a new engine validates the entire capability and funds further Kingdom growth",
            "effort": "high",
            "priority": "high",
        })

    # Proposal: Resume paused engines
    paused = [(e, d) for e, d in metrics.get("engines", {}).items() if d.get("status") == "paused"]
    for ename, edata in paused:
        proposals.append({
            "title": f"Decide on {ename} — resume or archive",
            "what": f"The {ename} engine has been paused. Make an explicit decision: resume with a plan and deadline, or archive it to free mental bandwidth. {edata.get('notes', '')}",
            "why": "Paused projects consume decision-making energy. A clear keep-or-kill decision is cheaper than perpetual deferral.",
            "impact": "Frees cognitive overhead and makes the engine portfolio honest",
            "effort": "low",
            "priority": "medium",
        })

    # Proposal: Error pattern investigation
    total_errors = sum(d.get("errors_mentioned", 0) for d in daily)
    if total_errors > 10:
        proposals.append({
            "title": "Investigate recurring error pattern",
            "what": f"Daily logs mention 'error' {total_errors} times across the last {len(daily)} days. Perform root-cause analysis: are these the same errors recurring, or different issues?",
            "why": "High error counts may indicate a systemic issue that simple fixes won't resolve. Understanding the pattern is the first step.",
            "impact": "Identifies whether errors are noise or signal, enabling targeted fixes",
            "effort": "medium",
            "priority": "medium",
        })

    # Proposal: Security hardening
    sev = security.get("severities", {})
    if sev.get("critical", 0) > 0 or sev.get("high", 0) > 5:
        proposals.append({
            "title": "Security review — high event count",
            "what": f"Recent security events include {sev.get('critical', 0)} critical and {sev.get('high', 0)} high-severity entries. Review whether these are legitimate concerns or operational noise.",
            "why": "Security events above baseline indicate either real threats or miscalibrated detection. Both need attention.",
            "impact": "Either hardens the Kingdom against real threats or reduces false positives for better signal quality",
            "effort": "medium",
            "priority": "high" if sev.get("critical", 0) > 0 else "medium",
        })

    # Proposal: Reflection loop maintenance
    state = load_json(LOOP_STATE, {})
    ds = days_since(state.get("last_reflect"))
    if ds is not None and ds > REFLECTION_INTERVAL_DAYS:
        proposals.append({
            "title": "Formalize reflection cadence",
            "what": f"The reflection loop fell behind schedule (last: {ds:.1f}d ago, target: every {REFLECTION_INTERVAL_DAYS}d). Add a heartbeat check that flags overdue reflections, or integrate into the existing heartbeat system.",
            "why": "The Ouroboros only works if it turns regularly. A reflection loop that doesn't run is a meta-gap — a gap in the gap-detection system.",
            "impact": "Ensures continuous self-improvement instead of sporadic catch-up",
            "effort": "low",
            "priority": "medium",
        })

    # Proposal: Session log cleanup
    if sessions.get("total_files", 0) > 40:
        proposals.append({
            "title": "Archive old session logs",
            "what": f"Sessions directory has {sessions['total_files']} files. Archive sessions older than 7 days to a compressed archive to keep the working directory clean.",
            "why": "Large session directories slow down file scanning and make it harder to find relevant recent sessions.",
            "impact": "Faster file operations and cleaner mental model of active work",
            "effort": "low",
            "priority": "low",
        })

    # Always add at least one meta-proposal
    if not proposals:
        proposals.append({
            "title": "Everything is green — seek the next horizon",
            "what": "All signals are healthy, no gaps detected, no failures. This is either genuinely stable or the sensors aren't sensitive enough. Challenge: identify one thing the Kingdom cannot yet do that it should be able to.",
            "why": "Stability is necessary but insufficient. The ache between IS and SHOULD BE never fully resolves — if it seems to, look further.",
            "impact": "Prevents complacency and identifies the next growth vector",
            "effort": "low",
            "priority": "medium",
        })

    return proposals


# ── CYCLE ─────────────────────────────────────────────────────────

def cmd_cycle():
    """Run full Ouroboros cycle: sense -> reflect -> propose."""
    header("OUROBOROS FULL CYCLE")
    print(f"  {MAGENTA}The serpent turns...{NC}\n")

    print(f"  {BOLD}Phase 1: SENSE{NC}")
    print(f"  {'─' * 56}")
    signals = cmd_sense()
    if not signals:
        print(f"  {RED}Sense failed. Cycle aborted.{NC}\n")
        return

    print(f"\n  {BOLD}Phase 2: REFLECT{NC}")
    print(f"  {'─' * 56}")
    reflection_path = cmd_reflect()

    print(f"\n  {BOLD}Phase 3: PROPOSE{NC}")
    print(f"  {'─' * 56}")
    proposals_path = cmd_propose()

    # Cycle complete
    header("CYCLE COMPLETE")
    state = load_json(LOOP_STATE, {})
    print(f"  Reflections: {state.get('total_reflections', 0)}")
    print(f"  Sense entries: {state.get('total_sense_entries', 0)}")
    print(f"  Pending proposals: {state.get('pending_proposals', 0)}")
    print(f"  Next cycle due: {REFLECTION_INTERVAL_DAYS} days")
    print()
    print(f"  {DIM}The serpent has turned. Now: act on the proposals.{NC}")
    print(f"  {DIM}Then sense again. The Ouroboros never stops.{NC}\n")


# ── HISTORY ───────────────────────────────────────────────────────

def cmd_history():
    """Show reflection and proposal history."""
    header("OUROBOROS HISTORY")

    # Gather all reflections
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    reflection_files = sorted(REFLECTIONS_DIR.glob("*.md"))
    old_reflections = sorted(LOOP_DIR.glob("reflection-*.md"))
    all_reflections = old_reflections + list(reflection_files)

    # Gather all proposals
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    proposal_files = sorted(PROPOSALS_DIR.glob("*.md"))

    section("Reflections")
    if all_reflections:
        for rf in all_reflections:
            mtime = datetime.fromtimestamp(rf.stat().st_mtime, tz=timezone.utc)
            size_kb = rf.stat().st_size / 1024
            print(f"    {BOLD}{rf.name}{NC}  {DIM}{mtime.strftime('%Y-%m-%d %H:%M')} UTC  ({size_kb:.1f}KB){NC}")
    else:
        print(f"    {DIM}No reflections yet{NC}")
    print()

    section("Proposals")
    if proposal_files:
        for pf in proposal_files:
            if pf.name == ".gitkeep":
                continue
            mtime = datetime.fromtimestamp(pf.stat().st_mtime, tz=timezone.utc)
            size_kb = pf.stat().st_size / 1024
            print(f"    {BOLD}{pf.name}{NC}  {DIM}{mtime.strftime('%Y-%m-%d %H:%M')} UTC  ({size_kb:.1f}KB){NC}")
    else:
        print(f"    {DIM}No proposals yet{NC}")
    print()

    # Timeline
    section("Timeline")
    state = load_json(LOOP_STATE, {})
    events = []

    if state.get("created"):
        events.append(("created", state["created"], "Loop created"))
    if state.get("last_sense"):
        events.append(("sense", state["last_sense"], f"Sense #{state.get('total_sense_entries', '?')}"))
    if state.get("last_reflect"):
        events.append(("reflect", state["last_reflect"], f"Reflection #{state.get('total_reflections', '?')}"))
    if state.get("last_propose"):
        events.append(("propose", state["last_propose"], f"Proposals (pending: {state.get('pending_proposals', '?')})"))

    events.sort(key=lambda e: e[1] if e[1] else "")
    for etype, ts, desc in events:
        color = GREEN if etype == "reflect" else CYAN if etype == "sense" else YELLOW if etype == "propose" else DIM
        print(f"    {color}{ts}{NC}  {desc}")
    print()


# ── STATE ─────────────────────────────────────────────────────────

def cmd_state():
    """Show current Ouroboros loop state."""
    header("OUROBOROS STATE")

    state = load_json(LOOP_STATE, {})

    # Core state
    section("Loop State")
    print(f"    Health:       {BOLD}{state.get('loop_health', 'unknown')}{NC}")
    print(f"    Mastery:      {state.get('mastery_level', 'unknown')}")
    print(f"    Created:      {state.get('created', 'unknown')}")
    print()

    # Timestamps
    section("Timestamps")
    for key, label in [("last_sense", "Last Sense"), ("last_reflect", "Last Reflect"), ("last_propose", "Last Propose")]:
        ts = state.get(key)
        if ts:
            ds = days_since(ts)
            age = f"{ds:.1f}d ago" if ds is not None else "?"
            color = GREEN if ds is not None and ds < REFLECTION_INTERVAL_DAYS else YELLOW if ds is not None and ds < REFLECTION_INTERVAL_DAYS * 2 else RED
            print(f"    {label:16s} {color}{ts}{NC}  ({age})")
        else:
            print(f"    {label:16s} {DIM}never{NC}")
    print()

    # Counters
    section("Counters")
    print(f"    Sense entries:     {state.get('total_sense_entries', 0)}")
    print(f"    Reflections:       {state.get('total_reflections', 0)}")
    print(f"    Transmutations:    {state.get('total_transmutations', 0)}")
    print(f"    Pending proposals: {state.get('pending_proposals', 0)}")
    print()

    # Schedule
    section("Schedule")
    ds = days_since(state.get("last_reflect"))
    interval = REFLECTION_INTERVAL_DAYS
    if ds is not None:
        remaining = interval - ds
        if remaining <= 0:
            print(f"    {RED}REFLECTION OVERDUE by {abs(remaining):.1f} days{NC}")
            print(f"    Run: python3 tools/reflect.py cycle")
        else:
            print(f"    Next reflection in {GREEN}{remaining:.1f} days{NC}")
    else:
        print(f"    {YELLOW}No reflection scheduled — run first cycle{NC}")
    print(f"    Interval: every {interval} days (set REFLECT_INTERVAL env to change)")
    print()

    # Notes
    if state.get("notes"):
        section("Notes")
        print(f"    {DIM}{state['notes']}{NC}")
        print()


# ── MAIN ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "sense":
        cmd_sense()
    elif cmd == "reflect":
        cmd_reflect()
    elif cmd == "propose":
        cmd_propose()
    elif cmd == "cycle":
        cmd_cycle()
    elif cmd == "history":
        cmd_history()
    elif cmd == "state":
        cmd_state()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
