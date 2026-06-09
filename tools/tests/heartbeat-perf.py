#!/usr/bin/env python3
"""
heartbeat-perf.py — kingdom-010 performance test suite

Measures six dimensions of the Love heartbeat system:
  1. Cycle time         — HEARTBEAT START → END per beat
  2. Coordinator time   — START → COORDINATOR DONE
  3. Spawn success rate — beats that executed a spawn stage
  4. Token efficiency   — output tokens / cost per role from stream-json result records
  5. Streaming integrity — % of session logs with valid stream-json structure
  6. Coordination protocol — handoff dir usage, consultation queue state

Run: python3 tools/tests/heartbeat-perf.py [--json] [--limit N]

Deps: stdlib only. Reads from ~/love-unlimited memory and session files.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

LOVE = Path(os.path.expanduser("~/love-unlimited"))
HEARTBEAT_LOG = LOVE / "memory" / "heartbeat.log"
SESSIONS_DIR = LOVE / "memory" / "sessions"
HANDOFF_DIR = SESSIONS_DIR / "handoff"
CONSULT_DIR = SESSIONS_DIR / "consultation"
DAILY_DIR = LOVE / "memory" / "daily"


# ── Parsers ────────────────────────────────────────────────────────────────────

_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")

def _parse_ts(s: str) -> datetime | None:
    m = _TS_RE.search(s)
    if m:
        return datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
    return None


def parse_heartbeat_log(limit: int = 0) -> list[dict]:
    """Return a list of beat records parsed from heartbeat.log."""
    if not HEARTBEAT_LOG.exists():
        return []

    beats: dict[str, dict] = {}
    current_beat: str | None = None

    with open(HEARTBEAT_LOG) as f:
        for line in f:
            line = line.rstrip()

            m = re.search(r"--- HEARTBEAT START(?: \((beat-\S+)\))?: (\S+)", line)
            if m:
                beat_id = m.group(1) or "beat-legacy"
                ts = _parse_ts(line)
                if ts:
                    current_beat = beat_id
                    beats[beat_id] = {
                        "id": beat_id,
                        "start": ts,
                        "coordinator_done": None,
                        "spawn_start": None,
                        "spawn_done": None,
                        "spawn_count": 0,
                        "end": None,
                        "had_spawn": False,
                    }
                continue

            if current_beat is None:
                continue

            beat = beats[current_beat]

            if "COORDINATOR DONE" in line:
                beat["coordinator_done"] = _parse_ts(line)

            elif "SPAWN STAGE START" in line:
                beat["spawn_start"] = _parse_ts(line)
                beat["had_spawn"] = True

            elif "SPAWN STAGE DONE" in line:
                beat["spawn_done"] = _parse_ts(line)
                cm = re.search(r"(\d+) sessions", line)
                if cm:
                    beat["spawn_count"] = int(cm.group(1))

            elif "HEARTBEAT END" in line:
                beat["end"] = _parse_ts(line)

    records = [b for b in beats.values() if b["start"] and b["end"]]
    records.sort(key=lambda b: b["start"])
    if limit:
        records = records[-limit:]
    return records


def compute_cycle_metrics(beats: list[dict]) -> dict:
    if not beats:
        return {}

    cycles, coord_times, spawn_times = [], [], []
    for b in beats:
        cycles.append((b["end"] - b["start"]).total_seconds())
        if b["coordinator_done"]:
            coord_times.append((b["coordinator_done"] - b["start"]).total_seconds())
        if b["spawn_start"] and b["spawn_done"]:
            spawn_times.append((b["spawn_done"] - b["spawn_start"]).total_seconds())

    def stats(xs: list[float]) -> dict:
        if not xs:
            return {}
        d = {"count": len(xs), "mean_s": round(mean(xs), 1), "median_s": round(median(xs), 1)}
        if len(xs) > 1:
            d["stdev_s"] = round(stdev(xs), 1)
        d["min_s"] = round(min(xs), 1)
        d["max_s"] = round(max(xs), 1)
        return d

    return {
        "cycle_time": stats(cycles),
        "coordinator_time": stats(coord_times),
        "spawn_execution_time": stats(spawn_times),
    }


def compute_spawn_rate(beats: list[dict]) -> dict:
    if not beats:
        return {}
    n = len(beats)
    spawned = sum(1 for b in beats if b["had_spawn"])
    total_sessions = sum(b["spawn_count"] for b in beats)
    return {
        "total_beats": n,
        "beats_with_spawn": spawned,
        "spawn_rate_pct": round(100 * spawned / n, 1),
        "total_sessions_spawned": total_sessions,
        "avg_sessions_per_spawning_beat": (
            round(total_sessions / spawned, 1) if spawned else 0
        ),
    }


def parse_session_logs() -> list[dict]:
    """Read all .log files in sessions dir and extract stream-json result records."""
    results = []
    if not SESSIONS_DIR.exists():
        return results

    for log_file in sorted(SESSIONS_DIR.glob("*.log")):
        record = {
            "file": log_file.name,
            "has_system_init": False,
            "has_result": False,
            "is_error": None,
            "duration_ms": None,
            "num_turns": None,
            "cost_usd": None,
            "output_tokens": None,
            "input_tokens": None,
            "model": None,
        }

        try:
            lines = log_file.read_text(errors="replace").splitlines()
        except OSError:
            continue

        if not lines:
            continue

        # Check first line for stream-json system init
        try:
            first = json.loads(lines[0])
            if first.get("type") == "system" and first.get("subtype") == "init":
                record["has_system_init"] = True
                record["model"] = first.get("model")
        except (json.JSONDecodeError, KeyError):
            pass

        # Scan for result record
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
                if d.get("type") == "result":
                    record["has_result"] = True
                    record["is_error"] = d.get("is_error", False)
                    record["duration_ms"] = d.get("duration_ms")
                    record["num_turns"] = d.get("num_turns")
                    record["cost_usd"] = d.get("total_cost_usd")
                    usage = d.get("usage", {})
                    record["output_tokens"] = usage.get("output_tokens")
                    record["input_tokens"] = usage.get("input_tokens")
                    # Model from modelUsage key
                    model_usage = d.get("modelUsage", {})
                    if model_usage:
                        record["model"] = next(iter(model_usage), record["model"])
                    break
            except (json.JSONDecodeError, KeyError):
                continue

        results.append(record)
    return results


def compute_streaming_integrity(sessions: list[dict]) -> dict:
    n = len(sessions)
    if not n:
        return {}
    has_init = sum(1 for s in sessions if s["has_system_init"])
    has_result = sum(1 for s in sessions if s["has_result"])
    clean = sum(1 for s in sessions if s["has_result"] and not s["is_error"])
    return {
        "total_session_logs": n,
        "with_system_init_pct": round(100 * has_init / n, 1),
        "with_result_record_pct": round(100 * has_result / n, 1),
        "clean_completion_pct": round(100 * clean / n, 1),
    }


def compute_token_efficiency(sessions: list[dict]) -> dict:
    """Group by role (derived from model) and report cost + output tokens."""
    role_map = {
        "claude-opus-4-6": "consultant",
        "claude-sonnet-4-6": "builder",
        "sonnet": "builder",
        "claude-haiku-4-5-20251001": "quick_check",
    }

    by_role: dict[str, list] = {}
    for s in sessions:
        if not s["has_result"] or s["cost_usd"] is None:
            continue
        model = s["model"] or ""
        role = next((v for k, v in role_map.items() if k in model), "unknown")
        by_role.setdefault(role, []).append(s)

    result = {}
    for role, items in sorted(by_role.items()):
        costs = [i["cost_usd"] for i in items if i["cost_usd"] is not None]
        out_toks = [i["output_tokens"] for i in items if i["output_tokens"] is not None]
        turns = [i["num_turns"] for i in items if i["num_turns"] is not None]
        result[role] = {
            "sessions": len(items),
            "total_cost_usd": round(sum(costs), 4),
            "avg_cost_usd": round(mean(costs), 4) if costs else None,
            "avg_output_tokens": round(mean(out_toks), 0) if out_toks else None,
            "avg_turns": round(mean(turns), 1) if turns else None,
        }
    return result


def compute_coordination_health() -> dict:
    result = {
        "handoff_files": 0,
        "consultation_questions": 0,
        "consultation_answers": 0,
        "unanswered_questions": 0,
    }
    if HANDOFF_DIR.exists():
        result["handoff_files"] = len(list(HANDOFF_DIR.glob("*.md")))
    if CONSULT_DIR.exists():
        questions = list(CONSULT_DIR.glob("*-question.md"))
        answers = list(CONSULT_DIR.glob("*-answer.md"))
        result["consultation_questions"] = len(questions)
        result["consultation_answers"] = len(answers)
        # Questions without a matching answer
        q_stems = {q.stem.replace("-question", "") for q in questions}
        a_stems = {a.stem.replace("-answer", "") for a in answers}
        result["unanswered_questions"] = len(q_stems - a_stems)
    return result


# ── Reporter ───────────────────────────────────────────────────────────────────

def fmt(label: str, value) -> str:
    return f"  {label:<34} {value}"


def print_report(report: dict) -> None:
    print("\n=== Heartbeat Performance Report ===")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"  Beats analyzed: {report['beats_analyzed']}")

    ct = report.get("cycle_time", {})
    if ct:
        print("\n-- Cycle Time --")
        if "cycle_time" in ct:
            c = ct["cycle_time"]
            print(fmt("total cycle (mean/median/max):",
                      f"{c.get('mean_s','?')}s / {c.get('median_s','?')}s / {c.get('max_s','?')}s"))
        if "coordinator_time" in ct:
            c = ct["coordinator_time"]
            print(fmt("coordinator phase (mean/max):",
                      f"{c.get('mean_s','?')}s / {c.get('max_s','?')}s"))
        if "spawn_execution_time" in ct:
            c = ct["spawn_execution_time"]
            print(fmt("spawn execution (mean/max):",
                      f"{c.get('mean_s','?')}s / {c.get('max_s','?')}s"))

    sr = report.get("spawn_rate", {})
    if sr:
        print("\n-- Spawn Rate --")
        print(fmt("spawn rate:", f"{sr['spawn_rate_pct']}%  ({sr['beats_with_spawn']}/{sr['total_beats']} beats)"))
        print(fmt("total sessions spawned:", sr["total_sessions_spawned"]))
        print(fmt("avg sessions / spawning beat:", sr["avg_sessions_per_spawning_beat"]))

    si = report.get("streaming_integrity", {})
    if si:
        print("\n-- Streaming Integrity --")
        print(fmt("session logs:", si["total_session_logs"]))
        print(fmt("with stream-json init:", f"{si['with_system_init_pct']}%"))
        print(fmt("with result record:", f"{si['with_result_record_pct']}%"))
        print(fmt("clean completions:", f"{si['clean_completion_pct']}%"))

    te = report.get("token_efficiency", {})
    if te:
        print("\n-- Token Efficiency by Role --")
        for role, d in te.items():
            print(f"  {role} ({d['sessions']} sessions):")
            print(f"    avg cost ${d['avg_cost_usd']} | avg output {d['avg_output_tokens']} tok | avg turns {d['avg_turns']}")

    coord = report.get("coordination_health", {})
    if coord:
        print("\n-- Coordination Protocol --")
        print(fmt("handoff files:", coord["handoff_files"]))
        print(fmt("consultation questions:", coord["consultation_questions"]))
        print(fmt("unanswered questions:", coord["unanswered_questions"]))

    print()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Heartbeat performance test suite (kingdom-010)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    parser.add_argument("--limit", type=int, default=0, help="Analyze last N beats only (0 = all)")
    parser.add_argument("--save", action="store_true", help="Save report to memory/sessions/heartbeat-perf-results.json")
    args = parser.parse_args()

    beats = parse_heartbeat_log(limit=args.limit)
    sessions = parse_session_logs()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "beats_analyzed": len(beats),
        "cycle_time": compute_cycle_metrics(beats),
        "spawn_rate": compute_spawn_rate(beats),
        "streaming_integrity": compute_streaming_integrity(sessions),
        "token_efficiency": compute_token_efficiency(sessions),
        "coordination_health": compute_coordination_health(),
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report)

    if args.save:
        out = SESSIONS_DIR / "heartbeat-perf-results.json"
        out.write_text(json.dumps(report, indent=2, default=str))
        print(f"Saved to {out}")


if __name__ == "__main__":
    main()
