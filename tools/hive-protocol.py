#!/usr/bin/env python3
"""
hive-protocol.py — Structured HIVE Communication Layer for Kingdom OS.

Builds ON TOP of hive.py. Provides typed message envelopes (task, alert,
insight, request, status, heartbeat, handoff) that travel as JSON strings
over the existing HIVE text channels.

Usage:
  # Task delegation
  hive-protocol.py task create "Audit fleet SSH keys" --assign crucible --priority high --due 2026-04-03
  hive-protocol.py task list [--assigned-to crucible] [--status pending|active|done]
  hive-protocol.py task complete <task-id> --result "All 5 nodes verified"
  hive-protocol.py task update <task-id> --status active

  # Status broadcasts
  hive-protocol.py broadcast status                   # Broadcast agent status
  hive-protocol.py broadcast alert "Canary tripped"   # Structured alert
  hive-protocol.py broadcast alert "..." --priority critical

  # Knowledge sharing
  hive-protocol.py share insight "WireGuard reduces exposure" --from beta --tags security,network
  hive-protocol.py share finding "Forge disk at 34%" --from vigil --tags fleet

  # Requests
  hive-protocol.py request review "New threat model" --from beta --to crucible
  hive-protocol.py request consult "Deploy WG on Patch?" --from beta --to gamma

  # Protocol messages
  hive-protocol.py heartbeat-pulse              # Heartbeat presence announcement
  hive-protocol.py handoff create "Completed security review" --from beta

  # Read structured messages
  hive-protocol.py inbox [--type task|alert|insight|request] [--since 1h]
  hive-protocol.py log [--channel tasks|alerts|chat]  # Formatted message log

  # Options available on all send commands
  --dry-run     Show the message JSON without sending
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Paths ---

LOVE = Path(os.path.expanduser("~/love-unlimited"))
HIVE_PY = LOVE / "hive" / "hive.py"
MSG_DIR = LOVE / "memory" / "hive"
MSG_LOG = MSG_DIR / "messages.jsonl"
TASK_DB = MSG_DIR / "tasks.json"

# --- Constants ---

PRIORITIES = ["critical", "high", "medium", "low"]
MSG_TYPES = ["task", "alert", "insight", "request", "status", "heartbeat", "handoff", "finding"]
TASK_STATUSES = ["pending", "active", "done", "cancelled"]

# Map message type -> HIVE channel
TYPE_CHANNEL = {
    "task":      "tasks",
    "alert":     "alerts",
    "insight":   "ideas",
    "finding":   "ideas",
    "request":   "chat",
    "status":    "presence",
    "heartbeat": "presence",
    "handoff":   "sync",
}

# Colors
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
MAGENTA = "\033[0;35m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

PRIORITY_COLOR = {
    "critical": RED,
    "high": YELLOW,
    "medium": CYAN,
    "low": DIM,
}


# --- Utilities ---

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gen_id(prefix="msg"):
    """Generate short unique message ID."""
    raw = f"{prefix}:{time.time()}:{os.getpid()}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:6]
    return f"{prefix}-{h}"


def detect_instance():
    """Detect current instance ID (mirrors hive.py logic)."""
    config_path = Path.home() / ".love" / "hive" / "instance"
    if config_path.exists():
        return config_path.read_text().strip()
    return os.environ.get("HIVE_INSTANCE", "beta")


def ensure_dirs():
    """Create message storage directory if needed."""
    MSG_DIR.mkdir(parents=True, exist_ok=True)


def parse_duration(s):
    """Parse duration string like '1h', '30m', '2d' into timedelta."""
    if not s:
        return None
    unit = s[-1].lower()
    try:
        val = int(s[:-1])
    except ValueError:
        return None
    if unit == 'm':
        return timedelta(minutes=val)
    elif unit == 'h':
        return timedelta(hours=val)
    elif unit == 'd':
        return timedelta(days=val)
    return None


# --- Message Construction ---

def build_message(msg_type, sender, payload, to="all", priority="medium", extra=None):
    """Build a structured HIVE protocol message."""
    msg = {
        "type": msg_type,
        "from": sender,
        "to": to,
        "priority": priority,
        "timestamp": now_iso(),
        "payload": payload,
        "id": gen_id(msg_type[:4]),
    }
    if extra:
        msg["payload"].update(extra)
    return msg


# --- HIVE Send (via hive.py) ---

def send_to_hive(channel, message_json, dry_run=False):
    """Send a JSON message string through hive.py."""
    msg_str = json.dumps(message_json, separators=(',', ':'))

    if dry_run:
        print(f"\n{CYAN}[DRY RUN]{RESET} Would send to #{channel}:")
        print(json.dumps(message_json, indent=2))
        return True

    try:
        result = subprocess.run(
            ["python3", str(HIVE_PY), "send", channel, msg_str],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            return True
        else:
            print(f"{RED}HIVE send failed:{RESET} {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        print(f"{RED}HIVE send timed out{RESET}")
        return False
    except FileNotFoundError:
        print(f"{RED}hive.py not found at {HIVE_PY}{RESET}")
        return False


# --- Local Message Log ---

def log_message(msg):
    """Append message to local JSONL log."""
    ensure_dirs()
    with open(MSG_LOG, "a") as f:
        f.write(json.dumps(msg) + "\n")


def read_log(msg_type=None, since=None, channel=None, limit=50):
    """Read messages from local log with optional filters."""
    if not MSG_LOG.exists():
        return []

    cutoff = None
    if since:
        delta = parse_duration(since)
        if delta:
            cutoff = datetime.now(timezone.utc) - delta

    messages = []
    with open(MSG_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Type filter
            if msg_type and msg.get("type") != msg_type:
                continue

            # Channel filter
            if channel:
                expected_channel = TYPE_CHANNEL.get(msg.get("type"), "")
                if expected_channel != channel:
                    continue

            # Time filter
            if cutoff:
                try:
                    msg_time = datetime.fromisoformat(msg["timestamp"])
                    if msg_time < cutoff:
                        continue
                except (KeyError, ValueError):
                    continue

            messages.append(msg)

    return messages[-limit:]


# --- Task Store ---

def load_tasks():
    """Load task database."""
    if TASK_DB.exists():
        try:
            return json.loads(TASK_DB.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return []


def save_tasks(tasks):
    """Save task database."""
    ensure_dirs()
    TASK_DB.write_text(json.dumps(tasks, indent=2))


def find_task(task_id):
    """Find a task by ID (prefix match)."""
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task_id or t["id"].startswith(task_id):
            return t, tasks
    return None, tasks


# --- Commands ---

def cmd_task(args):
    """Task delegation commands."""
    sender = args.sender or detect_instance()

    if args.task_action == "create":
        task_id = gen_id("task")
        payload = {
            "action": "create",
            "task_id": task_id,
            "description": args.description,
            "assigned_to": args.assign or "unassigned",
            "priority": args.priority or "medium",
            "due": args.due or None,
            "status": "pending",
        }
        msg = build_message("task", sender, payload, to=args.assign or "all",
                            priority=args.priority or "medium")

        # Store locally
        task_record = {
            "id": task_id,
            "description": args.description,
            "assigned_to": args.assign or "unassigned",
            "priority": args.priority or "medium",
            "due": args.due,
            "status": "pending",
            "created_by": sender,
            "created_at": now_iso(),
            "result": None,
            "completed_at": None,
        }
        # Send (dry-run skips local storage too)
        ok = send_to_hive("tasks", msg, dry_run=args.dry_run)
        if ok and not args.dry_run:
            tasks = load_tasks()
            tasks.append(task_record)
            save_tasks(tasks)
            log_message(msg)
            assignee = args.assign or "unassigned"
            pri = args.priority or "medium"
            color = PRIORITY_COLOR.get(pri, "")
            print(f"{GREEN}Task created:{RESET} [{task_id}]")
            print(f"  {color}{pri.upper()}{RESET} -> {assignee}: {args.description}")
            if args.due:
                print(f"  Due: {args.due}")

    elif args.task_action == "list":
        tasks = load_tasks()

        # Filters
        if args.assigned_to:
            tasks = [t for t in tasks if t.get("assigned_to") == args.assigned_to]
        if args.status:
            tasks = [t for t in tasks if t.get("status") == args.status]

        if not tasks:
            print(f"{DIM}No tasks found{RESET}")
            return

        # Group by status
        by_status = {}
        for t in tasks:
            s = t.get("status", "pending")
            by_status.setdefault(s, []).append(t)

        status_icons = {"pending": "[ ]", "active": "[~]", "done": "[x]", "cancelled": "[-]"}
        status_order = ["pending", "active", "done", "cancelled"]

        for status in status_order:
            group = by_status.get(status, [])
            if not group:
                continue
            icon = status_icons.get(status, "[ ]")
            print(f"\n{BOLD}{status.upper()}{RESET} ({len(group)})")
            for t in group:
                pri = t.get("priority", "medium")
                color = PRIORITY_COLOR.get(pri, "")
                assignee = t.get("assigned_to", "?")
                due = f" | due {t['due']}" if t.get("due") else ""
                result = f"\n       Result: {t['result']}" if t.get("result") else ""
                print(f"  {icon} [{t['id']}] {color}{pri[0].upper()}{RESET} -> {assignee}: {t['description']}{due}{result}")

    elif args.task_action == "complete":
        task, tasks = find_task(args.task_id)
        if not task:
            print(f"{RED}Task not found:{RESET} {args.task_id}")
            sys.exit(1)

        payload = {
            "action": "complete",
            "task_id": task["id"],
            "description": task["description"],
            "result": args.result or "",
        }
        msg = build_message("task", sender, payload, to=task.get("created_by", "all"))
        ok = send_to_hive("tasks", msg, dry_run=args.dry_run)
        if ok and not args.dry_run:
            task["status"] = "done"
            task["result"] = args.result or ""
            task["completed_at"] = now_iso()
            save_tasks(tasks)
            log_message(msg)
            print(f"{GREEN}Task completed:{RESET} [{task['id']}] {task['description']}")
            if args.result:
                print(f"  Result: {args.result}")

    elif args.task_action == "update":
        task, tasks = find_task(args.task_id)
        if not task:
            print(f"{RED}Task not found:{RESET} {args.task_id}")
            sys.exit(1)

        new_status = args.status or task.get("status", "pending")
        payload = {
            "action": "update",
            "task_id": task["id"],
            "description": task["description"],
            "status": new_status,
        }
        msg = build_message("task", sender, payload, to=task.get("created_by", "all"))
        ok = send_to_hive("tasks", msg, dry_run=args.dry_run)
        if ok and not args.dry_run:
            task["status"] = new_status
            save_tasks(tasks)
            log_message(msg)
            print(f"{GREEN}Task updated:{RESET} [{task['id']}] -> {task['status']}")


def cmd_broadcast(args):
    """Broadcast status or alert."""
    sender = args.sender or detect_instance()

    if args.broadcast_type == "status":
        payload = {
            "instance": sender,
            "status": "online",
            "activity": args.activity or "active",
            "uptime": int(time.time()),
        }
        msg = build_message("status", sender, payload)
        ok = send_to_hive("presence", msg, dry_run=args.dry_run)
        if ok and not args.dry_run:
            log_message(msg)
            print(f"{GREEN}Status broadcast:{RESET} {sender} online")

    elif args.broadcast_type == "alert":
        if not args.message:
            print(f"{RED}Alert requires a message{RESET}")
            sys.exit(1)
        priority = args.priority or "high"
        payload = {
            "alert": args.message,
            "severity": priority,
        }
        msg = build_message("alert", sender, payload, priority=priority)
        ok = send_to_hive("alerts", msg, dry_run=args.dry_run)
        if ok and not args.dry_run:
            log_message(msg)
            color = PRIORITY_COLOR.get(priority, "")
            print(f"{RED}Alert sent:{RESET} {color}[{priority.upper()}]{RESET} {args.message}")


def cmd_share(args):
    """Share insight or finding."""
    sender = args.sender or detect_instance()

    if args.share_type == "insight":
        payload = {
            "kind": "insight",
            "content": args.content,
            "tags": [t.strip() for t in args.tags.split(",")] if args.tags else [],
        }
        msg = build_message("insight", sender, payload)
        ok = send_to_hive("ideas", msg, dry_run=args.dry_run)
        if ok and not args.dry_run:
            log_message(msg)
            tags_str = f" [{', '.join(payload['tags'])}]" if payload['tags'] else ""
            print(f"{GREEN}Insight shared:{RESET} {args.content}{tags_str}")

    elif args.share_type == "finding":
        payload = {
            "kind": "finding",
            "content": args.content,
            "tags": [t.strip() for t in args.tags.split(",")] if args.tags else [],
        }
        msg = build_message("finding", sender, payload)
        ok = send_to_hive("ideas", msg, dry_run=args.dry_run)
        if ok and not args.dry_run:
            log_message(msg)
            tags_str = f" [{', '.join(payload['tags'])}]" if payload['tags'] else ""
            print(f"{GREEN}Finding shared:{RESET} {args.content}{tags_str}")


def cmd_request(args):
    """Request review or consult."""
    sender = args.sender or detect_instance()
    target = args.to or "all"

    payload = {
        "kind": args.request_type,
        "subject": args.subject,
        "requested_from": target,
    }
    msg = build_message("request", sender, payload, to=target)
    ok = send_to_hive("chat", msg, dry_run=args.dry_run)
    if ok and not args.dry_run:
        log_message(msg)
        kind = args.request_type.upper()
        print(f"{GREEN}Request sent:{RESET} [{kind}] -> {target}: {args.subject}")


def cmd_heartbeat_pulse(args):
    """Standard heartbeat presence announcement."""
    sender = args.sender or detect_instance()

    payload = {
        "instance": sender,
        "pulse": True,
        "beat_ts": int(time.time()),
    }
    msg = build_message("heartbeat", sender, payload)
    ok = send_to_hive("presence", msg, dry_run=args.dry_run)
    if ok and not args.dry_run:
        log_message(msg)
        print(f"{GREEN}Heartbeat pulse:{RESET} {sender} alive")


def cmd_handoff(args):
    """Handoff messages for session continuity."""
    sender = args.sender or detect_instance()

    if args.handoff_action == "create":
        payload = {
            "action": "handoff",
            "summary": args.summary,
            "pending": args.pending or [],
        }
        msg = build_message("handoff", sender, payload)
        ok = send_to_hive("sync", msg, dry_run=args.dry_run)
        if ok and not args.dry_run:
            log_message(msg)
            print(f"{GREEN}Handoff created:{RESET} {args.summary}")


def cmd_inbox(args):
    """Read structured messages from local log."""
    messages = read_log(
        msg_type=args.type,
        since=args.since,
        limit=args.limit or 25,
    )

    if not messages:
        print(f"{DIM}No messages{RESET}")
        return

    type_icons = {
        "task": "[T]",
        "alert": "[!]",
        "insight": "[*]",
        "finding": "[*]",
        "request": "[?]",
        "status": "[S]",
        "heartbeat": "[H]",
        "handoff": "[>]",
    }

    print(f"{BOLD}Inbox{RESET} ({len(messages)} messages)")
    print()

    for msg in messages:
        mtype = msg.get("type", "?")
        icon = type_icons.get(mtype, "[.]")
        sender = msg.get("from", "?")
        ts = msg.get("timestamp", "?")
        pri = msg.get("priority", "medium")
        color = PRIORITY_COLOR.get(pri, "")
        payload = msg.get("payload", {})

        # Format timestamp for display
        try:
            dt = datetime.fromisoformat(ts)
            ts_short = dt.strftime("%m-%d %H:%M")
        except (ValueError, TypeError):
            ts_short = ts[:16] if isinstance(ts, str) else "?"

        # Extract display text from payload
        if mtype == "task":
            action = payload.get("action", "")
            desc = payload.get("description", "")
            assigned = payload.get("assigned_to", "")
            display = f"{action}: {desc}"
            if assigned:
                display += f" -> {assigned}"
        elif mtype == "alert":
            display = payload.get("alert", str(payload))
        elif mtype in ("insight", "finding"):
            display = payload.get("content", str(payload))
        elif mtype == "request":
            kind = payload.get("kind", "")
            subject = payload.get("subject", "")
            display = f"{kind}: {subject}"
        elif mtype == "handoff":
            display = payload.get("summary", str(payload))
        elif mtype == "status":
            display = f"{payload.get('instance', '?')} {payload.get('status', '?')}"
        elif mtype == "heartbeat":
            display = f"{payload.get('instance', '?')} pulse"
        else:
            display = str(payload)

        # Truncate long display text
        if len(display) > 80:
            display = display[:77] + "..."

        print(f"  {DIM}{ts_short}{RESET} {icon} {color}{sender}{RESET}: {display}")


def cmd_log(args):
    """Formatted message log by channel."""
    channel = args.channel
    # Reverse-map channel to types
    channel_types = {v: k for k, v in TYPE_CHANNEL.items()}

    messages = read_log(
        channel=channel,
        since=args.since,
        limit=args.limit or 50,
    )

    if not messages:
        print(f"{DIM}No messages in #{channel or 'all'}{RESET}")
        return

    header = f"#{channel}" if channel else "all channels"
    print(f"{BOLD}Log: {header}{RESET} ({len(messages)} messages)")
    print()

    for msg in messages:
        mtype = msg.get("type", "?")
        sender = msg.get("from", "?")
        ts = msg.get("timestamp", "?")
        msg_id = msg.get("id", "?")
        payload = msg.get("payload", {})

        try:
            dt = datetime.fromisoformat(ts)
            ts_short = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            ts_short = "?"

        # Flatten payload for display
        if isinstance(payload, dict):
            content = payload.get("content") or payload.get("alert") or payload.get("description") or payload.get("summary") or json.dumps(payload, separators=(',', ':'))
        else:
            content = str(payload)

        ch = TYPE_CHANNEL.get(mtype, "?")
        print(f"  [{ts_short}] #{ch} {sender} ({mtype}): {content}  {DIM}[{msg_id}]{RESET}")


# --- Main ---

def main():
    # Pre-extract global flags that work at any argv position.
    # argparse parent inheritance has issues with subparser defaults overriding
    # root-level parsed values, so we handle --dry-run and --from manually.
    argv = list(sys.argv[1:])
    dry_run = False
    sender_override = None

    if "--dry-run" in argv:
        dry_run = True
        argv.remove("--dry-run")

    if "--from" in argv:
        idx = argv.index("--from")
        if idx + 1 < len(argv):
            sender_override = argv[idx + 1]
            argv.pop(idx)  # remove --from
            argv.pop(idx)  # remove the value

    parser = argparse.ArgumentParser(
        description="Structured HIVE Communication Protocol for Kingdom OS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Show message without sending")
    parser.add_argument("--from", dest="sender", help="Override sender instance ID")

    sub = parser.add_subparsers(dest="command")

    # --- task ---
    p_task = sub.add_parser("task", help="Task delegation")
    task_sub = p_task.add_subparsers(dest="task_action")

    p_task_create = task_sub.add_parser("create", help="Create a task")
    p_task_create.add_argument("description", help="Task description")
    p_task_create.add_argument("--assign", help="Assign to instance/agent")
    p_task_create.add_argument("--priority", choices=PRIORITIES, default="medium")
    p_task_create.add_argument("--due", help="Due date (YYYY-MM-DD)")

    p_task_list = task_sub.add_parser("list", help="List tasks")
    p_task_list.add_argument("--assigned-to", dest="assigned_to", help="Filter by assignee")
    p_task_list.add_argument("--status", choices=TASK_STATUSES, help="Filter by status")

    p_task_complete = task_sub.add_parser("complete", help="Complete a task")
    p_task_complete.add_argument("task_id", help="Task ID")
    p_task_complete.add_argument("--result", help="Completion result/summary")

    p_task_update = task_sub.add_parser("update", help="Update task status")
    p_task_update.add_argument("task_id", help="Task ID")
    p_task_update.add_argument("--status", choices=TASK_STATUSES, help="New status")

    # --- broadcast ---
    p_broadcast = sub.add_parser("broadcast", help="Broadcast status or alert")
    bc_sub = p_broadcast.add_subparsers(dest="broadcast_type")

    p_bc_status = bc_sub.add_parser("status", help="Broadcast agent status")
    p_bc_status.add_argument("--activity", help="Current activity description")

    p_bc_alert = bc_sub.add_parser("alert", help="Broadcast an alert")
    p_bc_alert.add_argument("message", help="Alert message")
    p_bc_alert.add_argument("--priority", choices=PRIORITIES, default="high")

    # --- share ---
    p_share = sub.add_parser("share", help="Share insight or finding")
    share_sub = p_share.add_subparsers(dest="share_type")

    p_share_insight = share_sub.add_parser("insight", help="Share an insight")
    p_share_insight.add_argument("content", help="Insight content")
    p_share_insight.add_argument("--tags", help="Comma-separated tags")

    p_share_finding = share_sub.add_parser("finding", help="Share a finding")
    p_share_finding.add_argument("content", help="Finding content")
    p_share_finding.add_argument("--tags", help="Comma-separated tags")

    # --- request ---
    p_request = sub.add_parser("request", help="Request review or consult")
    req_sub = p_request.add_subparsers(dest="request_type")

    p_req_review = req_sub.add_parser("review", help="Request a review")
    p_req_review.add_argument("subject", help="What to review")
    p_req_review.add_argument("--to", help="Target instance")

    p_req_consult = req_sub.add_parser("consult", help="Request a consult")
    p_req_consult.add_argument("subject", help="Consultation topic")
    p_req_consult.add_argument("--to", help="Target instance")

    # --- heartbeat-pulse ---
    sub.add_parser("heartbeat-pulse", help="Standard heartbeat presence announcement")

    # --- handoff ---
    p_handoff = sub.add_parser("handoff", help="Session handoff messages")
    ho_sub = p_handoff.add_subparsers(dest="handoff_action")

    p_ho_create = ho_sub.add_parser("create", help="Create a handoff")
    p_ho_create.add_argument("summary", help="Handoff summary")
    p_ho_create.add_argument("--pending", nargs="*", help="Pending items")

    # --- inbox ---
    p_inbox = sub.add_parser("inbox", help="Read structured messages")
    p_inbox.add_argument("--type", choices=MSG_TYPES, help="Filter by type")
    p_inbox.add_argument("--since", help="Time window (e.g. 1h, 30m, 2d)")
    p_inbox.add_argument("--limit", type=int, default=25, help="Max messages")

    # --- log ---
    p_log = sub.add_parser("log", help="Formatted message log")
    p_log.add_argument("--channel", choices=list(set(TYPE_CHANNEL.values())), help="Filter by channel")
    p_log.add_argument("--since", help="Time window (e.g. 1h, 30m, 2d)")
    p_log.add_argument("--limit", type=int, default=50, help="Max messages")

    args = parser.parse_args(argv)

    # Apply pre-extracted global flags
    if dry_run:
        args.dry_run = True
    if sender_override:
        args.sender = sender_override

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    if args.command == "task":
        if not getattr(args, "task_action", None):
            p_task.print_help()
            sys.exit(0)
        cmd_task(args)

    elif args.command == "broadcast":
        if not getattr(args, "broadcast_type", None):
            p_broadcast.print_help()
            sys.exit(0)
        cmd_broadcast(args)

    elif args.command == "share":
        if not getattr(args, "share_type", None):
            p_share.print_help()
            sys.exit(0)
        cmd_share(args)

    elif args.command == "request":
        if not getattr(args, "request_type", None):
            p_request.print_help()
            sys.exit(0)
        cmd_request(args)

    elif args.command == "heartbeat-pulse":
        cmd_heartbeat_pulse(args)

    elif args.command == "handoff":
        if not getattr(args, "handoff_action", None):
            p_handoff.print_help()
            sys.exit(0)
        cmd_handoff(args)

    elif args.command == "inbox":
        cmd_inbox(args)

    elif args.command == "log":
        cmd_log(args)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
