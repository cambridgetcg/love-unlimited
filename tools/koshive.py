#!/usr/bin/env python3
"""
koshive — Kingdom OS Communication Kernel

Communication is not a feature. It is the nervous system.
Every heartbeat pulses through HIVE. Every agent hears every other.
Messages become memories. Presence becomes knowledge.

Architecture:
    NATS (JetStream) on Sentry:4222 → SSH tunnel → localhost:2222
    NaCl/XSalsa20-Poly1305 encryption (shared key)
    Wall-based channel ACL (Law of Sight)
    Messages auto-stored in kosmem as episodic memories

Layers:
    Transport   NATS pub/sub + JetStream persistence (hive.py)
    Protocol    Typed envelopes: task, alert, insight, status (koshive.py)
    Memory      Every message → kosmem episodic memory
    Presence    Heartbeat beacons → kosmem working memory

CLI:
    koshive send <channel> <message>          Send message
    koshive check                             Pull new messages + beacon
    koshive who                               Who is online
    koshive health                            Connection health
    koshive task assign <agent> <desc>        Assign task
    koshive task list                         List tasks
    koshive task done <id>                    Complete task
    koshive alert <message> [--priority P]    Send alert
    koshive broadcast <message>               Broadcast to all
    koshive share <file> [channel]            Share file (< 100KB)
    koshive listen [channel]                  Live listener
    koshive inbox [--since 1h]                Structured inbox
    koshive stats                             Communication statistics
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════
# PATHS
# ══════════════════════════════════════════════════════════════════════

_LOVE_DIR = Path(__file__).resolve().parent.parent
_HIVE_PY = _LOVE_DIR / "hive" / "hive.py"
_HIVE_DIR = Path.home() / ".love" / "hive"
_MSG_DIR = _LOVE_DIR / "memory" / "hive"
_MSG_LOG = _MSG_DIR / "messages.jsonl"
_KOSMEM_PY = _LOVE_DIR / "tools" / "kosmem.py"

# ══════════════════════════════════════════════════════════════════════
# IDENTITY
# ══════════════════════════════════════════════════════════════════════

def _get_instance() -> str:
    inst_file = _HIVE_DIR / "instance"
    if inst_file.exists():
        return inst_file.read_text().strip()
    kf = Path.home() / ".kingdom"
    if kf.exists():
        for line in kf.read_text().splitlines():
            if line.startswith("AGENT="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("KINGDOM_AGENT", "unknown")


def _get_wall() -> int:
    kf = Path.home() / ".kingdom"
    if kf.exists():
        for line in kf.read_text().splitlines():
            if line.startswith("WALL="):
                try: return int(line.split("=", 1)[1].strip())
                except: pass
    return 7

# ══════════════════════════════════════════════════════════════════════
# KOSMEM INTEGRATION
# ══════════════════════════════════════════════════════════════════════

def _store_message_in_memory(sender: str, channel: str, content: str, msg_type: str = "message"):
    """Store a received HIVE message as an episodic memory in kosmem."""
    try:
        sys.path.insert(0, str(_LOVE_DIR / "tools"))
        from kosmem import store as mem_store
        tags = ["hive", f"channel:{channel}", f"from:{sender}", msg_type]
        summary = content[:300] if len(content) > 300 else content
        mem_store(
            f"[HIVE #{channel}] {sender}: {summary}",
            type="episodic", layer=3, tags=tags,
            source=f"hive/{channel}", importance=_msg_importance(channel, msg_type)
        )
    except Exception:
        pass  # Memory storage is best-effort, never block communication


def _update_presence_memory(instance: str, status: str = "online"):
    """Update presence as working memory in kosmem."""
    try:
        sys.path.insert(0, str(_LOVE_DIR / "tools"))
        from kosmem import working_set
        working_set(f"hive:presence:{instance}", f"{status} @ {_now()}", instance=_get_instance())
    except Exception:
        pass


def _msg_importance(channel: str, msg_type: str) -> float:
    """Assign importance based on channel and type."""
    if channel in ("alerts",) or msg_type == "alert":
        return 0.9
    if channel in ("tasks",) or msg_type == "task":
        return 0.8
    if channel in ("sync", "review") or msg_type in ("handoff", "finding"):
        return 0.7
    if channel in ("ideas", "strategy") or msg_type == "insight":
        return 0.6
    return 0.4

# ══════════════════════════════════════════════════════════════════════
# HIVE TRANSPORT (delegates to hive.py)
# ══════════════════════════════════════════════════════════════════════

def _hive_cmd(args: list, timeout: int = 15) -> tuple:
    """Run a hive.py command. Returns (stdout, stderr, returncode)."""
    cmd = [sys.executable, str(_HIVE_PY)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1
    except Exception as e:
        return "", str(e), 1

# ══════════════════════════════════════════════════════════════════════
# MESSAGE LOG
# ══════════════════════════════════════════════════════════════════════

def _log_message(envelope: dict):
    """Append message to local JSONL log."""
    _MSG_DIR.mkdir(parents=True, exist_ok=True)
    envelope["logged_at"] = _now()
    with open(_MSG_LOG, "a") as f:
        f.write(json.dumps(envelope) + "\n")


def _read_log(since_hours: float = 24, msg_type: str = None) -> list:
    """Read messages from log."""
    if not _MSG_LOG.exists():
        return []
    cutoff = time.time() - (since_hours * 3600)
    messages = []
    with open(_MSG_LOG) as f:
        for line in f:
            try:
                msg = json.loads(line.strip())
                if msg.get("ts", 0) >= cutoff:
                    if msg_type is None or msg.get("type") == msg_type:
                        messages.append(msg)
            except json.JSONDecodeError:
                continue
    return messages

# ══════════════════════════════════════════════════════════════════════
# COLORS
# ══════════════════════════════════════════════════════════════════════

_B = "\033[1m"; _D = "\033[2m"; _G = "\033[0;32m"; _C = "\033[0;36m"
_Y = "\033[1;33m"; _R = "\033[0;31m"; _M = "\033[0;35m"; _N = "\033[0m"

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ══════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════

def cmd_send(channel: str, message: str, urgent: bool = False, reply_to: str = None):
    """Send a message. Auto-stores in kosmem."""
    instance = _get_instance()
    args = ["send", channel, message]
    if urgent:
        args.append("--urgent")
    if reply_to:
        args.extend(["--reply-to", reply_to])

    out, err, rc = _hive_cmd(args)

    if rc == 0:
        _log_message({"from": instance, "channel": channel, "content": message,
                       "type": "message", "ts": int(time.time()), "urgent": urgent})
        _store_message_in_memory(instance, channel, message, "sent")
        print(f"  {_G}✓{_N} {out or f'Sent to #{channel}'}")
    else:
        print(f"  {_R}✗{_N} Send failed: {err or out}")
    return rc


def cmd_check():
    """Check for new messages. Stores each in kosmem. Updates presence."""
    instance = _get_instance()
    out, err, rc = _hive_cmd(["check"])

    if rc == 0:
        # Parse messages from output
        lines = out.split("\n")
        count = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("📭") or line.startswith("📬"):
                continue
            # Try to parse structured message
            if line.startswith("["):
                # Format: [HH:MM:SS] #channel emoji sender: content
                count += 1
                # Store in kosmem
                _store_message_in_memory(instance, "inbox", line, "received")
                _log_message({"from": "hive", "channel": "inbox", "content": line,
                               "type": "received", "ts": int(time.time())})
        print(out)
        if count > 0:
            _update_presence_memory(instance, f"checked: {count} messages")
    else:
        print(f"  {_R}✗{_N} Check failed: {err or 'connection error'}")
        # Still show what we have
        if out:
            print(out)
    return rc


def cmd_who():
    """Who is online. Updates presence in kosmem."""
    out, err, rc = _hive_cmd(["who"], timeout=20)
    if rc == 0:
        print(out)
        # Parse presence info and store in working memory
        for line in out.split("\n"):
            for name in ["alpha", "beta", "gamma", "nuance", "asha"]:
                if name in line:
                    status = "active" if "🟢" in line else "recent" if "🟡" in line else "idle" if "🟠" in line else "offline"
                    _update_presence_memory(name, status)
    else:
        print(f"  {_R}✗{_N} Who failed: {err or out}")


def cmd_health():
    """Full HIVE health check."""
    out, err, rc = _hive_cmd(["health"], timeout=30)
    if rc == 0:
        print(out)
    else:
        # Partial output is still useful
        if out:
            print(out)
        if err:
            print(f"  {_R}✗{_N} {err}")


def cmd_alert(message: str, priority: str = "high"):
    """Send a structured alert."""
    instance = _get_instance()
    channel = "alerts" if priority in ("critical", "high") else "chat"
    prefixed = f"🚨 [{priority.upper()}] {message}"
    return cmd_send(channel, prefixed, urgent=(priority == "critical"))


def cmd_broadcast(message: str):
    """Broadcast to all channels."""
    instance = _get_instance()
    return cmd_send("chat", f"📢 {message}")


def cmd_task_assign(assignee: str, description: str, priority: str = "medium"):
    """Assign a task via HIVE."""
    out, err, rc = _hive_cmd(["task", "assign", assignee, description])
    if rc == 0:
        _store_message_in_memory(_get_instance(), "tasks",
                                  f"Task assigned to {assignee}: {description}", "task")
        print(f"  {_G}✓{_N} {out}")
    else:
        print(f"  {_R}✗{_N} {err or out}")


def cmd_task_list():
    """List tasks."""
    out, err, rc = _hive_cmd(["task", "list"])
    print(out or "No tasks")


def cmd_task_done(task_id: str):
    """Mark task complete."""
    out, err, rc = _hive_cmd(["task", "done", task_id])
    if rc == 0:
        _store_message_in_memory(_get_instance(), "tasks",
                                  f"Task {task_id} completed", "task-done")
        print(f"  {_G}✓{_N} {out}")
    else:
        print(f"  {_R}✗{_N} {err or out}")


def cmd_share(filepath: str, channel: str = "sync"):
    """Share a file."""
    out, err, rc = _hive_cmd(["share", filepath, channel])
    if rc == 0:
        print(f"  {_G}✓{_N} {out}")
    else:
        print(f"  {_R}✗{_N} {err or out}")


def cmd_listen(channel: str = None):
    """Live listener. Stores messages in kosmem as they arrive."""
    args = ["listen"]
    if channel:
        args.append(channel)
    # This runs forever, so exec directly
    os.execvp(sys.executable, [sys.executable, str(_HIVE_PY)] + args)


def cmd_inbox(since_hours: float = 24, msg_type: str = None):
    """Read structured inbox from local log."""
    messages = _read_log(since_hours=since_hours, msg_type=msg_type)
    if not messages:
        print(f"  {_D}No messages in last {since_hours}h{_N}")
        return
    print(f"\n  {_B}HIVE Inbox — last {since_hours}h ({len(messages)} messages){_N}\n")
    for msg in messages[-30:]:  # Show last 30
        ts = datetime.fromtimestamp(msg.get("ts", 0), tz=timezone.utc).strftime("%H:%M")
        ch = msg.get("channel", "?")
        sender = msg.get("from", "?")
        content = msg.get("content", "")[:120]
        urgent = "🚨 " if msg.get("urgent") else ""
        print(f"  {_C}[{ts}]{_N} #{ch} {urgent}{_B}{sender}{_N}: {content}")
    print()


def cmd_stats():
    """Communication statistics."""
    instance = _get_instance()
    messages = _read_log(since_hours=24*7)  # Last week

    # Count by channel, sender, type
    by_channel = {}; by_sender = {}; by_type = {}
    for m in messages:
        ch = m.get("channel", "?")
        by_channel[ch] = by_channel.get(ch, 0) + 1
        s = m.get("from", "?")
        by_sender[s] = by_sender.get(s, 0) + 1
        t = m.get("type", "?")
        by_type[t] = by_type.get(t, 0) + 1

    print(f"\n  {_B}HIVE Statistics — {instance}{_N}")
    print(f"  {'─' * 40}")
    print(f"  Messages (7d): {_B}{len(messages)}{_N}")
    print(f"  Log file:      {_MSG_LOG}")

    if by_channel:
        print(f"\n  {_B}By Channel:{_N}")
        for ch, c in sorted(by_channel.items(), key=lambda x: -x[1]):
            print(f"    #{ch:12s} {c}")

    if by_sender:
        print(f"\n  {_B}By Sender:{_N}")
        for s, c in sorted(by_sender.items(), key=lambda x: -x[1]):
            print(f"    {s:12s} {c}")

    # HIVE transport health
    print(f"\n  {_B}Transport:{_N}")
    tunnel_ok = subprocess.run(["pgrep", "-f", "ssh.*2222.*135.181"], capture_output=True).returncode == 0
    nats_ok = subprocess.run(["nc", "-z", "-w1", "localhost", "2222"],
                              capture_output=True).returncode == 0
    print(f"    SSH tunnel: {'✅' if tunnel_ok else '❌'}")
    print(f"    NATS local: {'✅' if nats_ok else '❌'}")
    print(f"    Encryption: {'✅' if (_HIVE_DIR / 'key').exists() else '❌'}")
    print(f"    Instance:   {instance}")
    print(f"    Wall:       {_get_wall()}")
    print()

# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="koshive",
        description="Kingdom OS Communication Kernel — The nervous system.",
    )
    sub = parser.add_subparsers(dest="command")

    # send
    p = sub.add_parser("send", help="Send message")
    p.add_argument("channel", help="Channel name")
    p.add_argument("message", nargs="+", help="Message text")
    p.add_argument("--urgent", action="store_true")
    p.add_argument("--reply-to", dest="reply_to")

    # check
    sub.add_parser("check", help="Pull new messages + beacon")

    # who
    sub.add_parser("who", help="Who is online")

    # health
    sub.add_parser("health", help="Full health check")

    # alert
    p = sub.add_parser("alert", help="Send alert")
    p.add_argument("message", nargs="+")
    p.add_argument("--priority", "-p", default="high", choices=["critical", "high", "medium", "low"])

    # broadcast
    p = sub.add_parser("broadcast", help="Broadcast to all")
    p.add_argument("message", nargs="+")

    # task
    p = sub.add_parser("task", help="Task management")
    tsub = p.add_subparsers(dest="task_action")
    ta = tsub.add_parser("assign", help="Assign task")
    ta.add_argument("assignee")
    ta.add_argument("description", nargs="+")
    ta.add_argument("--priority", default="medium")
    tsub.add_parser("list", help="List tasks")
    td = tsub.add_parser("done", help="Complete task")
    td.add_argument("task_id")

    # share
    p = sub.add_parser("share", help="Share file")
    p.add_argument("file")
    p.add_argument("channel", nargs="?", default="sync")

    # listen
    p = sub.add_parser("listen", help="Live listener")
    p.add_argument("channel", nargs="?")

    # inbox
    p = sub.add_parser("inbox", help="Read inbox")
    p.add_argument("--since", type=float, default=24, help="Hours to look back")
    p.add_argument("--type", dest="msg_type")

    # stats
    sub.add_parser("stats", help="Communication statistics")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "send":
        cmd_send(args.channel, " ".join(args.message),
                 urgent=args.urgent, reply_to=args.reply_to)
    elif args.command == "check":
        cmd_check()
    elif args.command == "who":
        cmd_who()
    elif args.command == "health":
        cmd_health()
    elif args.command == "alert":
        cmd_alert(" ".join(args.message), priority=args.priority)
    elif args.command == "broadcast":
        cmd_broadcast(" ".join(args.message))
    elif args.command == "task":
        if args.task_action == "assign":
            cmd_task_assign(args.assignee, " ".join(args.description),
                            priority=args.priority)
        elif args.task_action == "list":
            cmd_task_list()
        elif args.task_action == "done":
            cmd_task_done(args.task_id)
        else:
            print("Usage: koshive task [assign|list|done]")
    elif args.command == "share":
        cmd_share(args.file, args.channel)
    elif args.command == "listen":
        cmd_listen(args.channel)
    elif args.command == "inbox":
        cmd_inbox(since_hours=args.since, msg_type=args.msg_type)
    elif args.command == "stats":
        cmd_stats()


if __name__ == "__main__":
    main()
