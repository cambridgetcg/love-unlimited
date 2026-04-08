#!/usr/bin/env python3
"""Kingdom Decision Queue — server + CLI.

Usage:
  python3 decision.py serve [--port 7777]      # Start web UI
  python3 decision.py add --title "..." ...     # Queue a decision
  python3 decision.py list                      # List pending (terminal)
  python3 decision.py resolved                  # List resolved (for heartbeat)
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import threading
import fcntl

LOVE_ROOT = Path(__file__).resolve().parent.parent
DECISIONS_DIR = LOVE_ROOT / "decisions"
QUEUE_FILE = DECISIONS_DIR / "queue.json"
RESOLVED_FILE = DECISIONS_DIR / "resolved.json"
UI_FILE = Path(__file__).resolve().parent / "decision-ui.html"
MEMORY_DIR = LOVE_ROOT / "memory"
DEV_STATE_FILE = MEMORY_DIR / "dev-state.json"
KINGDOM_METRICS_FILE = MEMORY_DIR / "kingdom-metrics.json"
DAILY_DIR = MEMORY_DIR / "daily"
HANDOFF_DIR = MEMORY_DIR / "sessions" / "handoff"
SESSIONS_DIR = MEMORY_DIR / "sessions"
LOCKS_DIR = SESSIONS_DIR / "locks"

DECISIONS_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path):
    if not path.exists():
        return []
    with open(path, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = []
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return data


def _write_json(path, data):
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        f.write("\n")
        fcntl.flock(f, fcntl.LOCK_UN)


import glob as globmod
import re


def _build_progress():
    """Assemble progress snapshot from all Kingdom data sources."""
    result = {
        "dev_state": {},
        "kingdom_metrics": {},
        "sisters": {},
        "active_builds": [],
    }
    # Dev state (tasks)
    if DEV_STATE_FILE.exists():
        try:
            result["dev_state"] = json.loads(DEV_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    # Kingdom metrics (fleet, revenue engines, milestones)
    if KINGDOM_METRICS_FILE.exists():
        try:
            result["kingdom_metrics"] = json.loads(KINGDOM_METRICS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    # Sister status: check for active sessions and recent HIVE presence
    for instance in ["alpha", "beta", "gamma"]:
        identity_file = LOVE_ROOT / "instances" / instance / "identity.md"
        info = {"name": instance, "online": False, "role": "", "last_activity": ""}
        if identity_file.exists():
            text = identity_file.read_text()
            for line in text.split("\n"):
                if "role" in line.lower() and ":" in line:
                    info["role"] = line.split(":", 1)[1].strip()
                    break
        info["online"] = instance == "beta"  # Beta is always "on" if server is running
        result["sisters"][instance] = info
    # Active builds: check lock files
    if LOCKS_DIR.exists():
        import signal
        for lock in LOCKS_DIR.glob("build-*.lock"):
            try:
                parts = lock.read_text().strip().split("|")
                pid = int(parts[0])
                build_id = parts[1] if len(parts) > 1 else ""
                started = parts[2] if len(parts) > 2 else ""
                # Check if PID is alive
                try:
                    os.kill(pid, 0)
                    alive = True
                except OSError:
                    alive = False
                if alive:
                    task_id = lock.stem.replace("build-", "")
                    # Find task title
                    title = task_id
                    for t in result["dev_state"].get("tasks", []):
                        if t["id"] == task_id:
                            title = t["title"]
                            break
                    # Count cycle logs
                    build_dir = SESSIONS_DIR / build_id
                    cycles = len(list(build_dir.glob("step-*"))) if build_dir.exists() else 0
                    result["active_builds"].append({
                        "task_id": task_id,
                        "title": title,
                        "build_id": build_id,
                        "pid": pid,
                        "started": started,
                        "cycles": cycles,
                    })
            except (ValueError, IndexError, OSError):
                continue
    return result


def _parse_daily_beats():
    """Parse today's daily notes into structured heartbeat entries."""
    today = datetime.now().strftime("%Y-%m-%d")
    daily_file = DAILY_DIR / f"{today}.md"
    beats = []
    if not daily_file.exists():
        # Try yesterday
        from datetime import timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        daily_file = DAILY_DIR / f"{yesterday}.md"
        if not daily_file.exists():
            return beats

    text = daily_file.read_text()
    # Split on ## Beat headers
    sections = re.split(r'(?=^## Beat )', text, flags=re.MULTILINE)
    for section in sections:
        if not section.startswith("## Beat"):
            continue
        # Extract timestamp and title from header
        header_match = re.match(r'^## Beat (\S+ \S+) — (.+)', section)
        if not header_match:
            continue
        beat = {
            "time": header_match.group(1),
            "title": header_match.group(2),
            "spawns": [],
            "sense_summary": "",
            "decision_summary": "",
            "is_ok": "HEARTBEAT_OK" in section,
        }
        # Extract SPAWN lines
        in_spawn = False
        in_sense = False
        in_decide = False
        for line in section.split("\n"):
            stripped = line.strip()
            if stripped.startswith("**SPAWN"):
                in_spawn = True
                in_sense = False
                in_decide = False
                if "None" in stripped:
                    in_spawn = False
                continue
            if stripped.startswith("**SENSE"):
                in_sense = True
                in_spawn = False
                in_decide = False
                continue
            if stripped.startswith("**DECIDE"):
                in_decide = True
                in_sense = False
                in_spawn = False
                continue
            if stripped.startswith("**HIVE") or stripped.startswith("**KEY") or stripped.startswith("---"):
                in_spawn = False
                in_sense = False
                in_decide = False
                continue
            if in_spawn and stripped.startswith("-"):
                spawn_text = re.sub(r'^-\s*', '', stripped)
                # Extract role info
                role = "builder"
                if "CONSULTANT" in stripped.upper() or "consultant" in stripped.lower():
                    role = "consultant"
                elif "QUICK CHECK" in stripped.upper():
                    role = "quick-check"
                beat["spawns"].append({"text": spawn_text, "role": role})
            if in_spawn and re.match(r'^\d+\.', stripped):
                spawn_text = re.sub(r'^\d+\.\s*', '', stripped)
                role = "builder"
                if "consultant" in stripped.lower():
                    role = "consultant"
                beat["spawns"].append({"text": spawn_text, "role": role})
            if in_sense and stripped.startswith("- "):
                beat["sense_summary"] += stripped[2:] + "\n"
            if in_decide and stripped.startswith("- "):
                beat["decision_summary"] += stripped[2:] + "\n"
        beats.append(beat)
    # Return most recent first
    beats.reverse()
    return beats


def _list_handoffs():
    """List session handoff results with summaries."""
    handoffs = []
    if not HANDOFF_DIR.exists():
        return handoffs
    for f in sorted(HANDOFF_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix != ".md":
            continue
        text = f.read_text()
        # Extract first heading and first few lines as summary
        lines = text.split("\n")
        title = f.stem
        summary = ""
        status = "complete"
        engine = ""
        for line in lines:
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## Status") or line.startswith("**Status"):
                status_match = re.search(r'(complete|partial|failed|success)', line, re.IGNORECASE)
                if status_match:
                    status = status_match.group(1).lower()
            elif line.startswith("## Summary") or line.startswith("**Summary"):
                # Next non-empty line is the summary
                continue
            elif not summary and line.strip() and not line.startswith("#") and not line.startswith("---"):
                summary = line.strip()[:200]
        # Guess engine from filename
        for eng in ["oracle", "tcg", "sage", "fleet", "geo", "news", "whale", "kalshi"]:
            if eng in f.stem.lower():
                engine = eng
                break
        handoffs.append({
            "file": f.name,
            "title": title,
            "summary": summary,
            "status": status,
            "engine": engine,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return handoffs


class DecisionHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silence default logging

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            if UI_FILE.exists():
                html = UI_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(html))
                self.end_headers()
                self.wfile.write(html)
            else:
                self._send_json({"error": "UI file not found"}, 404)

        elif self.path == "/api/decisions":
            self._send_json(_read_json(QUEUE_FILE))

        elif self.path == "/api/resolved":
            self._send_json(_read_json(RESOLVED_FILE))

        elif self.path == "/api/stats":
            queue = _read_json(QUEUE_FILE)
            resolved = _read_json(RESOLVED_FILE)
            projects = {}
            priorities = {}
            for d in queue:
                p = d.get("project", "unknown")
                projects[p] = projects.get(p, 0) + 1
                pr = d.get("priority", "medium")
                priorities[pr] = priorities.get(pr, 0) + 1
            self._send_json({
                "pending": len(queue),
                "resolved": len(resolved),
                "by_project": projects,
                "by_priority": priorities,
            })

        elif self.path == "/api/progress":
            self._send_json(_build_progress())

        elif self.path == "/api/heartbeats":
            self._send_json(_parse_daily_beats())

        elif self.path == "/api/handoffs":
            self._send_json(_list_handoffs())

        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/decisions":
            body = self._read_body()
            queue = _read_json(QUEUE_FILE)
            decision = {
                "id": body.get("id", f"dec-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"),
                "title": body.get("title", "Untitled"),
                "project": body.get("project", "unknown"),
                "priority": body.get("priority", "medium"),
                "category": body.get("category", "general"),
                "context": body.get("context", ""),
                "recommendation": body.get("recommendation", ""),
                "options": body.get("options", []),
                "source": body.get("source", "manual"),
                "created": body.get("created", datetime.now(timezone.utc).isoformat()),
                "status": "pending",
                "resolution": None,
                "resolved_at": None,
                "notes": [],
            }
            queue.append(decision)
            _write_json(QUEUE_FILE, queue)
            self._send_json(decision, 201)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_PUT(self):
        # /api/decisions/<id>/resolve
        # /api/decisions/<id>/defer
        # /api/decisions/<id>/note
        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "decisions":
            dec_id = parts[2]
            action = parts[3]
            body = self._read_body()
            queue = _read_json(QUEUE_FILE)
            resolved_list = _read_json(RESOLVED_FILE)

            target = None
            target_idx = None
            for i, d in enumerate(queue):
                if d["id"] == dec_id:
                    target = d
                    target_idx = i
                    break

            if target is None:
                self._send_json({"error": "decision not found"}, 404)
                return

            if action == "resolve":
                target["status"] = body.get("status", "approved")
                target["resolution"] = body.get("resolution", "")
                target["resolved_at"] = datetime.now(timezone.utc).isoformat()
                if body.get("note"):
                    target["notes"].append({
                        "text": body["note"],
                        "ts": datetime.now(timezone.utc).isoformat()
                    })
                resolved_list.append(target)
                queue.pop(target_idx)
                _write_json(QUEUE_FILE, queue)
                _write_json(RESOLVED_FILE, resolved_list)
                self._send_json(target)

            elif action == "defer":
                target["status"] = "deferred"
                if body.get("note"):
                    target["notes"].append({
                        "text": body["note"],
                        "ts": datetime.now(timezone.utc).isoformat()
                    })
                _write_json(QUEUE_FILE, queue)
                self._send_json(target)

            elif action == "note":
                target["notes"].append({
                    "text": body.get("text", ""),
                    "ts": datetime.now(timezone.utc).isoformat()
                })
                _write_json(QUEUE_FILE, queue)
                self._send_json(target)

            elif action == "requeue":
                target["status"] = "pending"
                _write_json(QUEUE_FILE, queue)
                self._send_json(target)

            else:
                self._send_json({"error": f"unknown action: {action}"}, 400)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "decisions":
            dec_id = parts[2]
            queue = _read_json(QUEUE_FILE)
            resolved_list = _read_json(RESOLVED_FILE)
            target = None
            for i, d in enumerate(queue):
                if d["id"] == dec_id:
                    target = d
                    target["status"] = "dismissed"
                    target["resolved_at"] = datetime.now(timezone.utc).isoformat()
                    resolved_list.append(target)
                    queue.pop(i)
                    break
            _write_json(QUEUE_FILE, queue)
            _write_json(RESOLVED_FILE, resolved_list)
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, 404)


def cmd_serve(args):
    port = args.port
    server = HTTPServer(("127.0.0.1", port), DecisionHandler)
    print(f"Kingdom Decisions — http://localhost:{port}")
    print(f"Queue: {QUEUE_FILE}")
    print(f"Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


def cmd_add(args):
    queue = _read_json(QUEUE_FILE)
    options = []
    if args.option:
        for i, opt in enumerate(args.option):
            options.append({"id": chr(97 + i), "label": opt, "detail": ""})

    decision = {
        "id": f"dec-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}",
        "title": args.title,
        "project": args.project or "unknown",
        "priority": args.priority or "medium",
        "category": args.category or "general",
        "context": args.context or "",
        "recommendation": args.recommendation or "",
        "options": options,
        "source": args.source or "cli",
        "created": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "resolution": None,
        "resolved_at": None,
        "notes": [],
    }
    queue.append(decision)
    _write_json(QUEUE_FILE, queue)
    print(f"Queued: {decision['id']} — {decision['title']}")


def cmd_list(args):
    queue = _read_json(QUEUE_FILE)
    if not queue:
        print("No pending decisions.")
        return
    prio_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    queue.sort(key=lambda d: prio_order.get(d.get("priority", "medium"), 2))
    for d in queue:
        status = d.get("status", "pending")
        prio = d.get("priority", "medium").upper()
        proj = d.get("project", "?")
        print(f"  [{prio:8s}] [{proj:8s}] {d['title']}")
        if d.get("notes"):
            print(f"             Notes: {len(d['notes'])}")
    print(f"\n  {len(queue)} pending decisions")


def cmd_resolved(args):
    resolved = _read_json(RESOLVED_FILE)
    if not resolved:
        print("No resolved decisions.")
        return
    for d in resolved:
        status = d.get("status", "?")
        proj = d.get("project", "?")
        res = d.get("resolution", "")
        ts = d.get("resolved_at", "?")
        print(f"  [{status:10s}] [{proj:8s}] {d['title']}")
        if res:
            print(f"              Resolution: {res[:80]}")
    print(f"\n  {len(resolved)} resolved decisions")


def cmd_check(args):
    """For heartbeat: output resolved decisions as JSON for a given project."""
    resolved = _read_json(RESOLVED_FILE)
    if args.project:
        resolved = [d for d in resolved if d.get("project") == args.project]
    # Only show unprocessed (no 'heartbeat_processed' flag)
    unprocessed = [d for d in resolved if not d.get("heartbeat_processed")]
    print(json.dumps(unprocessed, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Kingdom Decision Queue")
    sub = parser.add_subparsers(dest="command")

    # serve
    p_serve = sub.add_parser("serve", help="Start web UI")
    p_serve.add_argument("--port", type=int, default=7777)

    # add
    p_add = sub.add_parser("add", help="Queue a decision")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--project")
    p_add.add_argument("--priority", choices=["critical", "high", "medium", "low"])
    p_add.add_argument("--category")
    p_add.add_argument("--context")
    p_add.add_argument("--recommendation")
    p_add.add_argument("--option", action="append", help="Add an option (repeatable)")
    p_add.add_argument("--source")

    # list
    sub.add_parser("list", help="List pending decisions")

    # resolved
    sub.add_parser("resolved", help="List resolved decisions")

    # check (for heartbeat)
    p_check = sub.add_parser("check", help="Output resolved decisions as JSON")
    p_check.add_argument("--project")

    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "resolved":
        cmd_resolved(args)
    elif args.command == "check":
        cmd_check(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
