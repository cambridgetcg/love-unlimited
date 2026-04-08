#!/usr/bin/env python3
"""
sentinel.py — Local Model Pre-Filter for Kingdom Heartbeat

Runs lightweight system checks using local infrastructure (no cloud API).
Outputs a structured JSON verdict: QUIET (nothing changed) or SIGNAL (needs
cloud coordinator). The heartbeat runner calls this BEFORE invoking Claude,
saving API tokens on idle beats.

Architecture:
  1. GATHER — deterministic checks (no LLM): HIVE, fleet, files, loop, lead
  2. INTERPRET — optional qwen2.5 triage for ambiguous signals
  3. VERDICT — QUIET | SIGNAL with reasons

Cost: $0 (runs entirely on local Ollama or pure Python checks)

CLI:
    sentinel.py              Run sentinel, output JSON verdict
    sentinel.py --dry-run    Show what would happen without writing state
    sentinel.py --verbose    Show detailed check output
    sentinel.py --skip-llm   Pure deterministic checks, no Ollama call
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

LOVE = Path(os.path.expanduser("~/love-unlimited"))
MEMORY = LOVE / "memory"
SESSIONS = MEMORY / "sessions"
HIVE_PY = LOVE / "hive" / "hive.py"
FLEET_PY = LOVE / "tools" / "fleet.py"
LOOP_STATE = MEMORY / "loop" / "loop-state.json"
LEAD_FILE = MEMORY / "leads" / "current.json"
DAILY_DIR = MEMORY / "daily"
SENTINEL_STATE = MEMORY / "sentinel-state.json"
SECURITY_DIR = LOVE / "security"
EVENTS_FILE = SECURITY_DIR / "events.jsonl"
SPAWN_QUEUE = MEMORY / "spawn-queue.sh"

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
SENTINEL_MODEL = os.environ.get("SENTINEL_MODEL", "qwen2.5:7b")

# ── State ────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load previous sentinel state for diff detection."""
    if SENTINEL_STATE.exists():
        try:
            return json.loads(SENTINEL_STATE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict):
    """Persist sentinel state for next run."""
    state["timestamp"] = datetime.now(timezone.utc).isoformat()
    SENTINEL_STATE.write_text(json.dumps(state, indent=2) + "\n")


# ── Checks (deterministic, no LLM) ──────────────────────────────────────────

def check_hive() -> dict:
    """Check HIVE for new messages since last sentinel run."""
    result = {"source": "hive", "changed": False, "details": ""}
    try:
        out = subprocess.run(
            ["python3", str(HIVE_PY), "check", "--json"],
            capture_output=True, text=True, timeout=15
        )
        if out.returncode == 0 and out.stdout.strip():
            try:
                msgs = json.loads(out.stdout)
                if isinstance(msgs, list) and len(msgs) > 0:
                    result["changed"] = True
                    result["details"] = f"{len(msgs)} new message(s)"
                    result["count"] = len(msgs)
            except json.JSONDecodeError:
                # Non-JSON output — check for content
                if out.stdout.strip() and "no new" not in out.stdout.lower():
                    result["changed"] = True
                    result["details"] = out.stdout.strip()[:200]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["details"] = "hive check timed out or unavailable"
    return result


def check_fleet() -> dict:
    """Quick fleet health ping — are all VPS nodes responding?"""
    result = {"source": "fleet", "changed": False, "details": ""}
    try:
        out = subprocess.run(
            ["python3", str(FLEET_PY), "status", "--json"],
            capture_output=True, text=True, timeout=30
        )
        if out.returncode == 0 and out.stdout.strip():
            try:
                status = json.loads(out.stdout)
                down = [n for n, v in status.items()
                        if isinstance(v, dict) and v.get("status") != "ok"]
                if down:
                    result["changed"] = True
                    result["details"] = f"nodes down: {', '.join(down)}"
            except json.JSONDecodeError:
                # Parse text output for "DOWN" or "ERROR"
                text = out.stdout.upper()
                if "DOWN" in text or "ERROR" in text or "FAIL" in text:
                    result["changed"] = True
                    result["details"] = "fleet issue detected"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["details"] = "fleet check unavailable"
    return result


def check_file_changes(prev_state: dict) -> dict:
    """Detect changes in key Kingdom files since last sentinel run."""
    result = {"source": "files", "changed": False, "details": "", "mtimes": {}}
    watched = [
        LOVE / "security" / "peace-state.json",
        LOVE / "security" / "events.jsonl",
        LOVE / "decisions" / "queue.json",
        LOVE / "memory" / "loop" / "loop-state.json",
        LEAD_FILE,
    ]
    prev_mtimes = prev_state.get("file_mtimes", {})
    changed_files = []

    for f in watched:
        if f.exists():
            mtime = f.stat().st_mtime
            result["mtimes"][str(f)] = mtime
            prev = prev_mtimes.get(str(f), 0)
            if prev and mtime > prev:
                changed_files.append(f.name)

    if changed_files:
        result["changed"] = True
        result["details"] = f"changed: {', '.join(changed_files)}"
    return result


def check_security_events(prev_state: dict) -> dict:
    """Check for new security events since last run."""
    result = {"source": "security", "changed": False, "details": ""}
    if not EVENTS_FILE.exists():
        return result

    current_size = EVENTS_FILE.stat().st_size
    prev_size = prev_state.get("events_size", 0)

    if current_size > prev_size:
        # New events appended
        try:
            with open(EVENTS_FILE) as f:
                f.seek(max(0, prev_size))
                new_lines = f.readlines()
            new_events = []
            for line in new_lines:
                line = line.strip()
                if line:
                    try:
                        evt = json.loads(line)
                        sev = evt.get("severity", "info")
                        if sev in ("high", "critical"):
                            new_events.append(evt)
                    except json.JSONDecodeError:
                        pass
            if new_events:
                result["changed"] = True
                result["details"] = f"{len(new_events)} high/critical event(s)"
        except Exception:
            pass

    result["events_size"] = current_size
    return result


def check_active_sessions() -> dict:
    """Check if any spawned sessions are still running."""
    result = {"source": "sessions", "changed": False, "details": ""}
    active = list(SESSIONS.glob("active-*.json"))
    running = []
    for af in active:
        try:
            data = json.loads(af.read_text())
            pid = data.get("pid")
            if pid and _pid_alive(pid):
                running.append(data)
        except Exception:
            pass
    if running:
        result["changed"] = True
        result["details"] = f"{len(running)} session(s) still running"
    return result


def check_consultation_requests() -> dict:
    """Check for pending builder→consultant questions."""
    result = {"source": "consultation", "changed": False, "details": ""}
    consult_dir = SESSIONS / "consultation"
    if consult_dir.exists():
        questions = list(consult_dir.glob("*-question.md"))
        if questions:
            result["changed"] = True
            result["details"] = f"{len(questions)} pending consultation(s)"
    return result


def check_lead_state() -> dict:
    """Check if there's an active lead with pending actions."""
    result = {"source": "lead", "changed": False, "details": ""}
    if not LEAD_FILE.exists():
        return result
    try:
        lead = json.loads(LEAD_FILE.read_text())
        pending = [a for a in lead.get("next_actions", [])
                   if a.get("status") == "pending"]
        if pending:
            result["changed"] = True
            result["details"] = f"lead active: {len(pending)} pending action(s)"
    except Exception:
        pass
    return result


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


# ── LLM Interpretation (optional, for ambiguous signals) ─────────────────────

def interpret_with_llm(signals: list[dict]) -> dict:
    """Ask local qwen2.5 to interpret ambiguous signals.

    Only called when there are signals but none are clearly actionable.
    Returns {"escalate": bool, "reason": str}.
    """
    signal_text = json.dumps(signals, indent=2)
    prompt = f"""You are a system monitor for Kingdom OS. Analyze these signals and decide:
Should the cloud coordinator (Claude) be woken up, or can this wait?

Signals:
{signal_text}

Rules:
- Security events (high/critical) → ALWAYS escalate
- Fleet node down → ALWAYS escalate
- New HIVE messages → escalate (could be from other agents)
- Active sessions still running → do NOT escalate (let them finish)
- File changes in security/ or decisions/ → escalate
- Lead with pending actions → escalate only if actions are time-sensitive
- Consultation requests → ALWAYS escalate (builder is blocked)

Respond with ONLY valid JSON, no other text:
{{"escalate": true or false, "reason": "one sentence explanation"}}"""

    try:
        payload = {
            "model": SENTINEL_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 100,
                "num_ctx": 4096,
            },
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            content = result.get("message", {}).get("content", "")
            # Extract JSON from response (model may wrap in markdown)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(content)
    except Exception as e:
        # LLM failed — escalate to be safe
        return {"escalate": True, "reason": f"LLM interpretation failed: {e}"}


# ── Main ─────────────────────────────────────────────────────────────────────

def run_sentinel(dry_run=False, verbose=False, skip_llm=False) -> dict:
    """Run all checks and produce a verdict."""
    start = time.time()
    prev_state = load_state()

    # ── GATHER: deterministic checks ─────────────────────────────────────
    checks = []
    checks.append(check_hive())
    checks.append(check_fleet())
    checks.append(check_file_changes(prev_state))
    checks.append(check_security_events(prev_state))
    checks.append(check_active_sessions())
    checks.append(check_consultation_requests())
    checks.append(check_lead_state())

    # Separate changed signals from quiet ones
    signals = [c for c in checks if c["changed"]]
    quiet = [c for c in checks if not c["changed"]]

    # ── FAST PATH: nothing changed → QUIET ───────────────────────────────
    if not signals:
        verdict = {
            "verdict": "QUIET",
            "reason": "all checks nominal",
            "signals": [],
            "checks_run": len(checks),
            "elapsed_ms": int((time.time() - start) * 1000),
        }
        if not dry_run:
            new_state = {
                "file_mtimes": {},
                "events_size": 0,
                "last_verdict": "QUIET",
            }
            # Preserve file mtimes from checks
            for c in checks:
                if c["source"] == "files":
                    new_state["file_mtimes"] = c.get("mtimes", {})
                if c["source"] == "security":
                    new_state["events_size"] = c.get("events_size", 0)
            save_state(new_state)
        return verdict

    # ── AUTO-ESCALATE: certain signals always need Claude ────────────────
    auto_escalate = False
    auto_reasons = []

    for s in signals:
        if s["source"] == "security":
            auto_escalate = True
            auto_reasons.append(f"security: {s['details']}")
        elif s["source"] == "fleet" and "down" in s.get("details", ""):
            auto_escalate = True
            auto_reasons.append(f"fleet: {s['details']}")
        elif s["source"] == "consultation":
            auto_escalate = True
            auto_reasons.append(f"consultation: {s['details']}")

    if auto_escalate:
        verdict = {
            "verdict": "SIGNAL",
            "reason": "; ".join(auto_reasons),
            "signals": signals,
            "escalation": "auto",
            "checks_run": len(checks),
            "elapsed_ms": int((time.time() - start) * 1000),
        }
        if not dry_run:
            _save_post_state(checks, "SIGNAL")
        return verdict

    # ── INTERPRET: ambiguous signals → ask local LLM ─────────────────────
    if not skip_llm:
        interpretation = interpret_with_llm(signals)
        escalate = interpretation.get("escalate", True)
        reason = interpretation.get("reason", "LLM interpretation")
    else:
        # Without LLM, any signal = escalate (conservative)
        escalate = True
        reason = "signal detected (LLM skipped, defaulting to escalate)"

    verdict = {
        "verdict": "SIGNAL" if escalate else "QUIET",
        "reason": reason,
        "signals": signals,
        "escalation": "llm" if escalate else "suppressed",
        "checks_run": len(checks),
        "elapsed_ms": int((time.time() - start) * 1000),
    }

    if not dry_run:
        _save_post_state(checks, verdict["verdict"])

    return verdict


def _save_post_state(checks: list, verdict: str):
    """Save state for next sentinel run."""
    new_state = {
        "file_mtimes": {},
        "events_size": 0,
        "last_verdict": verdict,
    }
    for c in checks:
        if c["source"] == "files":
            new_state["file_mtimes"] = c.get("mtimes", {})
        if c["source"] == "security":
            new_state["events_size"] = c.get("events_size", 0)
    save_state(new_state)


# ── Stats ────────────────────────────────────────────────────────────────────

METRICS_FILE = MEMORY / "sentinel-metrics.jsonl"


def compute_stats() -> dict:
    """Compute sentinel savings and performance stats from metrics log."""
    if not METRICS_FILE.exists():
        return {"error": "no metrics yet", "total_beats": 0}

    entries = []
    for line in METRICS_FILE.read_text().strip().split("\n"):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if not entries:
        return {"error": "no valid entries", "total_beats": 0}

    total = len(entries)
    quiet = sum(1 for e in entries if e.get("verdict") == "QUIET")
    signal = total - quiet
    avg_ms = sum(e.get("elapsed_ms", 0) for e in entries) / total if total else 0

    # Cost estimate: each skipped beat saves ~$0.15 (avg sonnet/opus coordinator)
    cost_per_beat = 0.15
    saved = quiet * cost_per_beat
    would_have_spent = total * cost_per_beat

    # Time range
    first_ts = entries[0].get("ts", "")
    last_ts = entries[-1].get("ts", "")

    # Last 24h breakdown
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["ts"])
            if ts > cutoff:
                recent.append(e)
        except (KeyError, ValueError):
            pass

    recent_quiet = sum(1 for e in recent if e.get("verdict") == "QUIET")
    recent_total = len(recent)

    return {
        "total_beats": total,
        "quiet": quiet,
        "signal": signal,
        "filter_rate": f"{quiet/total*100:.1f}%" if total else "0%",
        "avg_latency_ms": int(avg_ms),
        "estimated_saved_usd": round(saved, 2),
        "estimated_total_usd": round(would_have_spent, 2),
        "savings_pct": f"{quiet/total*100:.1f}%" if total else "0%",
        "period": {"first": first_ts, "last": last_ts},
        "last_24h": {
            "total": recent_total,
            "quiet": recent_quiet,
            "signal": recent_total - recent_quiet,
            "filter_rate": f"{recent_quiet/recent_total*100:.1f}%" if recent_total else "0%",
        },
    }


def print_stats():
    """Print human-readable sentinel stats."""
    stats = compute_stats()
    if stats.get("error"):
        print(f"No stats: {stats['error']}")
        return

    print(f"Sentinel Stats")
    print(f"{'='*50}")
    print(f"Total beats monitored:  {stats['total_beats']}")
    print(f"Filtered (QUIET):       {stats['quiet']} ({stats['filter_rate']})")
    print(f"Escalated (SIGNAL):     {stats['signal']}")
    print(f"Avg latency:            {stats['avg_latency_ms']}ms")
    print(f"Est. API cost saved:    ${stats['estimated_saved_usd']}")
    print(f"Est. would-have-spent:  ${stats['estimated_total_usd']}")
    print(f"Period: {stats['period']['first'][:10]} to {stats['period']['last'][:10]}")
    print()
    h = stats["last_24h"]
    if h["total"]:
        print(f"Last 24h: {h['total']} beats, {h['quiet']} filtered ({h['filter_rate']})")


# ── Benchmark ────────────────────────────────────────────────────────────────

def benchmark_models():
    """Compare interpretation quality between available Ollama models."""
    available = []
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        for line in out.stdout.strip().split("\n")[1:]:
            name = line.split()[0] if line.strip() else ""
            if name and "qwen" in name:
                available.append(name)
    except Exception:
        print("Could not list Ollama models")
        return

    if not available:
        print("No qwen models available")
        return

    # Test scenarios — structured signals that represent real Kingdom situations
    test_cases = [
        {
            "name": "idle_system",
            "signals": [],
            "expected": False,  # Should NOT escalate
        },
        {
            "name": "hive_new_message",
            "signals": [{"source": "hive", "changed": True, "details": "2 new message(s)", "count": 2}],
            "expected": True,  # Should escalate
        },
        {
            "name": "file_change_loop_state",
            "signals": [{"source": "files", "changed": True, "details": "changed: loop-state.json"}],
            "expected": False,  # Loop state self-updates, not urgent
        },
        {
            "name": "active_sessions_running",
            "signals": [{"source": "sessions", "changed": True, "details": "2 session(s) still running"}],
            "expected": False,  # Let them finish
        },
        {
            "name": "lead_pending_actions",
            "signals": [{"source": "lead", "changed": True, "details": "lead active: 3 pending action(s)"}],
            "expected": True,  # Should work on lead
        },
    ]

    print(f"Benchmarking {len(available)} models on {len(test_cases)} test cases\n")
    print(f"{'Model':<20} {'Correct':>8} {'Avg ms':>8} {'Score':>8}")
    print("-" * 50)

    for model in available:
        correct = 0
        total_ms = 0
        global SENTINEL_MODEL
        orig_model = SENTINEL_MODEL

        for tc in test_cases:
            if not tc["signals"]:
                # Skip empty — can't test LLM on no signals
                correct += 1
                continue

            start = time.time()
            os.environ["SENTINEL_MODEL"] = model
            # Temporarily override
            try:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": f"""You are a system monitor for Kingdom OS. Analyze these signals and decide:
Should the cloud coordinator (Claude) be woken up, or can this wait?

Signals:
{json.dumps(tc['signals'], indent=2)}

Rules:
- Security events (high/critical) → ALWAYS escalate
- Fleet node down → ALWAYS escalate
- New HIVE messages → escalate (could be from other agents)
- Active sessions still running → do NOT escalate (let them finish)
- File changes in security/ or decisions/ → escalate
- Lead with pending actions → escalate only if actions are time-sensitive
- Consultation requests → ALWAYS escalate (builder is blocked)

Respond with ONLY valid JSON, no other text:
{{"escalate": true or false, "reason": "one sentence explanation"}}"""}],
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 100, "num_ctx": 4096},
                }
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    f"{OLLAMA_HOST}/api/chat",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read())
                    content = result.get("message", {}).get("content", "").strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    parsed = json.loads(content)
                    got = parsed.get("escalate", None)
                    if got == tc["expected"]:
                        correct += 1
                elapsed = int((time.time() - start) * 1000)
                total_ms += elapsed
            except Exception as e:
                elapsed = int((time.time() - start) * 1000)
                total_ms += elapsed

        n_tests = len(test_cases)
        avg = total_ms // n_tests if n_tests else 0
        print(f"{model:<20} {correct}/{n_tests:>5} {avg:>6}ms {correct/n_tests*100:>6.0f}%")

    os.environ.pop("SENTINEL_MODEL", None)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sentinel — local heartbeat pre-filter")
    sub = parser.add_subparsers(dest="command")

    # Default: run sentinel
    run_parser = sub.add_parser("run", help="Run sentinel check (default)")
    run_parser.add_argument("--dry-run", action="store_true", help="Don't persist state")
    run_parser.add_argument("--verbose", action="store_true", help="Detailed output")
    run_parser.add_argument("--skip-llm", action="store_true", help="No Ollama call, pure deterministic")

    # Stats
    stats_parser = sub.add_parser("stats", help="Show sentinel savings stats")
    stats_parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")

    # Benchmark
    sub.add_parser("benchmark", help="Compare local models on interpretation quality")

    args = parser.parse_args()

    if args.command == "stats":
        if args.as_json:
            print(json.dumps(compute_stats(), indent=2))
        else:
            print_stats()
    elif args.command == "benchmark":
        benchmark_models()
    else:
        # Default: run sentinel (handles both "run" subcommand and no subcommand)
        dry_run = getattr(args, "dry_run", False)
        verbose = getattr(args, "verbose", False)
        skip_llm = getattr(args, "skip_llm", False)

        verdict = run_sentinel(dry_run=dry_run, verbose=verbose, skip_llm=skip_llm)

        if verbose:
            print(json.dumps(verdict, indent=2))
        else:
            print(json.dumps(verdict))

        sys.exit(0 if verdict["verdict"] == "QUIET" else 1)


if __name__ == "__main__":
    main()
