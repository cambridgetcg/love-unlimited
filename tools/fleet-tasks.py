#!/usr/bin/env python3
"""
fleet-tasks.py — Fleet Task Orchestrator for Kingdom OS.

Distributes work across VPS nodes, executes remotely via SSH or kingdom-agent,
and tracks results locally. Supports parallel dispatch, async fire-and-collect,
priority queuing, scheduling, and full execution history.

Usage:
    fleet-tasks.py dispatch "Run security scan" --node forge --backend ollama
    fleet-tasks.py dispatch "Check disk usage trends" --node all --parallel
    fleet-tasks.py dispatch "df -h" --node forge                 # Raw bash command
    fleet-tasks.py dispatch "df -h" --node all --parallel --async
    fleet-tasks.py queue                                         # Show pending tasks
    fleet-tasks.py queue add "Update packages" --node all --priority high
    fleet-tasks.py queue run                                     # Execute next queued task
    fleet-tasks.py queue flush                                   # Execute all queued tasks
    fleet-tasks.py status                                        # Fleet execution status
    fleet-tasks.py results [task-id]                             # Show results
    fleet-tasks.py collect                                       # Collect pending async results
    fleet-tasks.py schedule "Daily health check" --node all --cron "0 6 * * *"
    fleet-tasks.py schedule list                                 # Show scheduled tasks
    fleet-tasks.py schedule remove <schedule-id>
    fleet-tasks.py history                                       # Past executions
    fleet-tasks.py history clear                                 # Archive and reset
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── Paths ───────────────────────────────────────────────────────────────────

LOVE_DIR = Path(os.environ.get("LOVE_DIR", Path.home() / "Love"))
DATA_DIR = LOVE_DIR / "memory" / "fleet-tasks"
QUEUE_FILE = DATA_DIR / "queue.json"
ACTIVE_FILE = DATA_DIR / "active.json"
RESULTS_FILE = DATA_DIR / "results.json"
SCHEDULE_FILE = DATA_DIR / "schedule.json"
HISTORY_FILE = DATA_DIR / "history.json"

# ─── Node Registry ───────────────────────────────────────────────────────────

NODES = {
    "forge":  {"ip": "89.167.84.100",  "host": "root@89.167.84.100",  "role": "CI/CD"},
    "lark":   {"ip": "89.167.95.165",  "host": "root@89.167.95.165",  "role": "AgentTool"},
    "sentry": {"ip": "135.181.28.252", "host": "root@135.181.28.252", "role": "monitoring"},
    "patch":  {"ip": "65.109.11.26",   "host": "root@65.109.11.26",   "role": "operations"},
    "sage":   {"ip": "204.168.140.12", "host": "root@204.168.140.12", "role": "oracle-execution"},
}

SSH_OPTS = [
    "-o", "ConnectTimeout=5",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
]

REMOTE_LOVE_DIR = "/root/.love"
PRIORITIES = ["critical", "high", "medium", "low"]
DEFAULT_SSH_TIMEOUT = 60
AGENT_TIMEOUT = 120

# ─── Colors ──────────────────────────────────────────────────────────────────

class C:
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RESET   = "\033[0m"

# ─── Utilities ───────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gen_task_id() -> str:
    """Generate a unique task ID: FT-XXXX."""
    raw = f"ft:{time.time()}:{os.getpid()}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:4]
    return f"FT-{h.upper()}"


def ensure_data():
    """Ensure data directory and files exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for f in [QUEUE_FILE, ACTIVE_FILE, RESULTS_FILE, SCHEDULE_FILE, HISTORY_FILE]:
        if not f.exists():
            f.write_text("[]")


def load_json(path: Path) -> list:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_json(path: Path, data: list):
    path.write_text(json.dumps(data, indent=2) + "\n")


def resolve_nodes(node_arg: str) -> list[str]:
    """Resolve 'all' or a comma-separated list to node names."""
    if node_arg == "all":
        return list(NODES.keys())
    names = [n.strip() for n in node_arg.split(",")]
    for n in names:
        if n not in NODES:
            print(f"{C.RED}Unknown node: {n}{C.RESET}")
            print(f"Available: {', '.join(NODES.keys())}")
            sys.exit(1)
    return names


def priority_sort_key(task: dict) -> int:
    """Lower number = higher priority."""
    p = task.get("priority", "medium")
    try:
        return PRIORITIES.index(p)
    except ValueError:
        return 2  # default to medium


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


# ─── SSH Execution ───────────────────────────────────────────────────────────

def ssh_run(node: str, cmd: str, timeout: int = DEFAULT_SSH_TIMEOUT) -> dict:
    """
    SSH into a node and run a command.
    Returns {success, stdout, stderr, exit_code, duration}.
    """
    info = NODES.get(node)
    if not info:
        return {"success": False, "stdout": "", "stderr": f"Unknown node: {node}",
                "exit_code": -1, "duration": 0}

    start = time.time()
    try:
        result = subprocess.run(
            ["ssh"] + SSH_OPTS + [info["host"], cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        duration = time.time() - start
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
            "duration": round(duration, 2),
        }
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        return {"success": False, "stdout": "", "stderr": f"Timeout after {timeout}s",
                "exit_code": -1, "duration": round(duration, 2)}
    except Exception as e:
        duration = time.time() - start
        return {"success": False, "stdout": "", "stderr": str(e),
                "exit_code": -1, "duration": round(duration, 2)}


def build_remote_cmd(command: str, backend: Optional[str] = None,
                     model: Optional[str] = None, effort: str = "low") -> str:
    """
    Build the remote command string.
    If backend is specified, wraps with kingdom-agent.py.
    Otherwise, runs as raw bash.
    """
    if backend:
        # Use kingdom-agent for AI-powered execution
        agent_cmd = (
            f"cd {REMOTE_LOVE_DIR} && "
            f"LOVE_DIR={REMOTE_LOVE_DIR} python3 tools/kingdom-agent.py "
            f"-p '{command}' --backend {backend}"
        )
        if model:
            agent_cmd += f" --model {model}"
        agent_cmd += f" --effort {effort}"
        return agent_cmd
    else:
        # Raw bash command
        return command


# ─── Core Task Operations ────────────────────────────────────────────────────

def execute_task_on_node(task_id: str, command: str, node: str,
                         backend: Optional[str] = None, model: Optional[str] = None,
                         effort: str = "low", timeout: int = DEFAULT_SSH_TIMEOUT) -> dict:
    """Execute a single task on a single node. Returns a result record."""
    remote_cmd = build_remote_cmd(command, backend, model, effort)
    actual_timeout = AGENT_TIMEOUT if backend else timeout

    result = ssh_run(node, remote_cmd, timeout=actual_timeout)

    return {
        "id": task_id,
        "node": node,
        "command": command,
        "backend": backend,
        "model": model,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "success": result["success"],
        "duration": result["duration"],
        "completed": now_iso(),
    }


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_dispatch(args):
    """Dispatch a task to one or more fleet nodes."""
    command = args.command
    nodes = resolve_nodes(args.node)
    backend = args.backend
    model = args.model
    effort = getattr(args, "effort", "low")
    is_parallel = args.parallel or args.node == "all"
    is_async = getattr(args, "async_mode", False)

    task_id = gen_task_id()
    timeout = args.timeout if args.timeout else (AGENT_TIMEOUT if backend else DEFAULT_SSH_TIMEOUT)

    mode = "agent" if backend else "bash"
    node_str = args.node if args.node == "all" else ", ".join(nodes)
    print(f"\n{C.BOLD}Dispatching {C.CYAN}{task_id}{C.RESET}")
    print(f"  Command:  {C.DIM}{command}{C.RESET}")
    print(f"  Mode:     {mode}" + (f" ({backend})" if backend else ""))
    print(f"  Nodes:    {node_str}")
    print(f"  Parallel: {is_parallel}  Async: {is_async}")
    print()

    if is_async:
        # Fire-and-forget: add to active tracking, SSH in background
        active = load_json(ACTIVE_FILE)
        for node in nodes:
            sub_id = f"{task_id}-{node}" if len(nodes) > 1 else task_id
            active.append({
                "id": sub_id,
                "parent_id": task_id,
                "command": command,
                "node": node,
                "backend": backend,
                "model": model,
                "started": now_iso(),
                "status": "dispatched",
            })
        save_json(ACTIVE_FILE, active)
        print(f"{C.GREEN}Dispatched async.{C.RESET} Use 'collect' to gather results.")
        print(f"Task ID: {C.CYAN}{task_id}{C.RESET}")

        # Actually fire the SSH commands in background subprocesses
        for node in nodes:
            sub_id = f"{task_id}-{node}" if len(nodes) > 1 else task_id
            remote_cmd = build_remote_cmd(command, backend, model, effort)
            info = NODES[node]
            log_file = DATA_DIR / f"{sub_id}.log"
            ssh_cmd = ["ssh"] + SSH_OPTS + [info["host"], remote_cmd]
            with open(log_file, "w") as lf:
                subprocess.Popen(ssh_cmd, stdout=lf, stderr=subprocess.STDOUT)
        return

    # Synchronous execution
    results_list = load_json(RESULTS_FILE)

    if is_parallel and len(nodes) > 1:
        print(f"{C.DIM}Executing in parallel across {len(nodes)} nodes...{C.RESET}\n")
        task_results = []

        with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
            future_map = {}
            for node in nodes:
                sub_id = f"{task_id}-{node}" if len(nodes) > 1 else task_id
                future = pool.submit(
                    execute_task_on_node, sub_id, command, node,
                    backend, model, effort, timeout
                )
                future_map[future] = node

            for future in as_completed(future_map):
                node = future_map[future]
                result = future.result()
                task_results.append(result)
                _print_node_result(node, result)

        results_list.extend(task_results)
    else:
        # Sequential (single node or explicit sequential)
        for node in nodes:
            sub_id = f"{task_id}-{node}" if len(nodes) > 1 else task_id
            print(f"{C.DIM}Executing on {node}...{C.RESET}")
            result = execute_task_on_node(
                sub_id, command, node, backend, model, effort, timeout
            )
            results_list.append(result)
            _print_node_result(node, result)

    save_json(RESULTS_FILE, results_list)
    print(f"\nTask {C.CYAN}{task_id}{C.RESET} complete. Results saved.")


def _print_node_result(node: str, result: dict):
    """Print a single node's execution result."""
    status = f"{C.GREEN}OK{C.RESET}" if result["success"] else f"{C.RED}FAIL (exit {result['exit_code']}){C.RESET}"
    duration = format_duration(result["duration"])

    print(f"{C.BOLD}── {node} [{status}] {C.DIM}({duration}){C.RESET}")
    if result["stdout"]:
        # Truncate very long output
        lines = result["stdout"].split("\n")
        if len(lines) > 30:
            for line in lines[:25]:
                print(f"  {line}")
            print(f"  {C.DIM}... ({len(lines) - 25} more lines){C.RESET}")
        else:
            for line in lines:
                print(f"  {line}")
    if result["stderr"] and not result["success"]:
        print(f"  {C.RED}stderr: {result['stderr'][:200]}{C.RESET}")
    print()


# ─── Queue Commands ──────────────────────────────────────────────────────────

def cmd_queue(args):
    """Show or manage the task queue."""
    sub = args.queue_action if hasattr(args, "queue_action") and args.queue_action else "show"

    if sub == "show":
        _queue_show()
    elif sub == "add":
        _queue_add(args)
    elif sub == "run":
        _queue_run_next(args)
    elif sub == "flush":
        _queue_flush(args)
    elif sub == "remove":
        _queue_remove(args)
    else:
        _queue_show()


def _queue_show():
    """Display pending queue."""
    queue = load_json(QUEUE_FILE)
    if not queue:
        print(f"\n{C.DIM}Queue is empty.{C.RESET}\n")
        return

    # Sort by priority
    queue.sort(key=priority_sort_key)

    print(f"\n{C.BOLD}── Task Queue ({len(queue)} pending) ──{C.RESET}\n")
    for t in queue:
        pri = t.get("priority", "medium")
        pri_color = {
            "critical": C.RED, "high": C.YELLOW, "medium": C.CYAN, "low": C.DIM
        }.get(pri, C.RESET)
        node = t.get("node", "?")
        print(f"  {C.CYAN}{t['id']}{C.RESET}  "
              f"[{pri_color}{pri:8}{C.RESET}]  "
              f"{C.DIM}{node:8}{C.RESET}  "
              f"{t.get('command', '?')[:60]}")
    print()


def _queue_add(args):
    """Add a task to the queue."""
    queue = load_json(QUEUE_FILE)
    task_id = gen_task_id()

    task = {
        "id": task_id,
        "command": args.add_command,
        "node": args.node or "all",
        "priority": args.priority or "medium",
        "backend": args.backend,
        "model": args.model,
        "created": now_iso(),
    }
    queue.append(task)
    queue.sort(key=priority_sort_key)
    save_json(QUEUE_FILE, queue)

    print(f"{C.GREEN}Queued:{C.RESET} {C.CYAN}{task_id}{C.RESET} ({task['priority']}) -> {task['node']}")
    print(f"  {task['command']}")


def _queue_run_next(args):
    """Execute the highest-priority queued task."""
    queue = load_json(QUEUE_FILE)
    if not queue:
        print(f"{C.DIM}Queue is empty.{C.RESET}")
        return

    queue.sort(key=priority_sort_key)
    task = queue.pop(0)
    save_json(QUEUE_FILE, queue)

    print(f"{C.BOLD}Running queued task:{C.RESET} {C.CYAN}{task['id']}{C.RESET}")

    # Build a pseudo-args for dispatch
    class DispatchArgs:
        pass
    da = DispatchArgs()
    da.command = task["command"]
    da.node = task.get("node", "all")
    da.backend = task.get("backend")
    da.model = task.get("model")
    da.effort = "low"
    da.parallel = True
    da.async_mode = False
    da.timeout = None
    cmd_dispatch(da)


def _queue_flush(args):
    """Execute all queued tasks in priority order."""
    queue = load_json(QUEUE_FILE)
    if not queue:
        print(f"{C.DIM}Queue is empty.{C.RESET}")
        return

    queue.sort(key=priority_sort_key)
    count = len(queue)
    print(f"\n{C.BOLD}Flushing {count} queued tasks...{C.RESET}\n")

    while queue:
        task = queue.pop(0)
        save_json(QUEUE_FILE, queue)

        print(f"{C.BOLD}{'─'*60}{C.RESET}")
        print(f"Task: {C.CYAN}{task['id']}{C.RESET}  [{task.get('priority','medium')}]  -> {task.get('node','all')}")

        class DispatchArgs:
            pass
        da = DispatchArgs()
        da.command = task["command"]
        da.node = task.get("node", "all")
        da.backend = task.get("backend")
        da.model = task.get("model")
        da.effort = "low"
        da.parallel = True
        da.async_mode = False
        da.timeout = None
        cmd_dispatch(da)

    print(f"\n{C.GREEN}All {count} tasks executed.{C.RESET}")


def _queue_remove(args):
    """Remove a task from the queue by ID."""
    task_id = args.remove_id
    queue = load_json(QUEUE_FILE)
    original_len = len(queue)
    queue = [t for t in queue if t["id"] != task_id]
    if len(queue) == original_len:
        print(f"{C.RED}Task {task_id} not found in queue.{C.RESET}")
        return
    save_json(QUEUE_FILE, queue)
    print(f"{C.GREEN}Removed {task_id} from queue.{C.RESET}")


# ─── Status ──────────────────────────────────────────────────────────────────

def cmd_status(args):
    """Show fleet execution status: active tasks, queue depth, recent results."""
    active = load_json(ACTIVE_FILE)
    queue = load_json(QUEUE_FILE)
    results = load_json(RESULTS_FILE)

    print(f"\n{C.BOLD}── Fleet Task Status ──{C.RESET}\n")

    # Active
    if active:
        print(f"  {C.YELLOW}Active ({len(active)}):{C.RESET}")
        for t in active[-10:]:
            print(f"    {C.CYAN}{t['id']}{C.RESET}  {t.get('node','?'):8}  "
                  f"started {t.get('started','?')[:16]}  "
                  f"{C.DIM}{t.get('command','')[:40]}{C.RESET}")
    else:
        print(f"  {C.DIM}No active tasks.{C.RESET}")

    # Queue
    print(f"\n  {C.CYAN}Queued: {len(queue)}{C.RESET}")
    if queue:
        queue_sorted = sorted(queue, key=priority_sort_key)
        for t in queue_sorted[:5]:
            print(f"    {C.CYAN}{t['id']}{C.RESET}  [{t.get('priority','?'):8}]  "
                  f"{t.get('node','?'):8}  {t.get('command','')[:40]}")
        if len(queue) > 5:
            print(f"    {C.DIM}... and {len(queue)-5} more{C.RESET}")

    # Recent results
    print(f"\n  {C.GREEN}Completed: {len(results)} total{C.RESET}")
    if results:
        for r in results[-5:]:
            status_str = f"{C.GREEN}OK{C.RESET}" if r.get("success") else f"{C.RED}FAIL{C.RESET}"
            dur = format_duration(r.get("duration", 0))
            print(f"    {C.CYAN}{r.get('id','?')}{C.RESET}  {r.get('node','?'):8}  "
                  f"[{status_str}]  {dur:>6}  "
                  f"{C.DIM}{r.get('command','')[:35]}{C.RESET}")

    print()


# ─── Results ─────────────────────────────────────────────────────────────────

def cmd_results(args):
    """Show results for a specific task or all recent results."""
    results = load_json(RESULTS_FILE)
    task_id = args.task_id if hasattr(args, "task_id") and args.task_id else None

    if task_id:
        # Filter for matching task ID (exact or parent match)
        matching = [r for r in results if r["id"] == task_id or r["id"].startswith(task_id)]
        if not matching:
            print(f"{C.RED}No results found for {task_id}{C.RESET}")
            return
        for r in matching:
            _print_full_result(r)
    else:
        # Show last 10
        if not results:
            print(f"\n{C.DIM}No results yet.{C.RESET}\n")
            return
        print(f"\n{C.BOLD}── Recent Results ({len(results)} total) ──{C.RESET}\n")
        for r in results[-10:]:
            status_str = f"{C.GREEN}OK{C.RESET}" if r.get("success") else f"{C.RED}FAIL{C.RESET}"
            dur = format_duration(r.get("duration", 0))
            completed = r.get("completed", "?")[:16]
            print(f"  {C.CYAN}{r['id']}{C.RESET}  {r.get('node','?'):8}  "
                  f"[{status_str}]  {dur:>6}  {C.DIM}{completed}{C.RESET}  "
                  f"{r.get('command','')[:40]}")
        print(f"\n{C.DIM}Use 'results <task-id>' for full output.{C.RESET}\n")


def _print_full_result(r: dict):
    """Print full result details for a single task."""
    status_str = f"{C.GREEN}OK{C.RESET}" if r.get("success") else f"{C.RED}FAIL (exit {r.get('exit_code', '?')}){C.RESET}"
    dur = format_duration(r.get("duration", 0))

    print(f"\n{C.BOLD}── {r['id']} ──{C.RESET}")
    print(f"  Node:      {r.get('node', '?')}")
    print(f"  Command:   {r.get('command', '?')}")
    if r.get("backend"):
        print(f"  Backend:   {r['backend']}" + (f" ({r['model']})" if r.get("model") else ""))
    print(f"  Status:    {status_str}")
    print(f"  Duration:  {dur}")
    print(f"  Completed: {r.get('completed', '?')}")
    if r.get("stdout"):
        print(f"\n  {C.BOLD}Output:{C.RESET}")
        for line in r["stdout"].split("\n"):
            print(f"    {line}")
    if r.get("stderr"):
        print(f"\n  {C.RED}Stderr:{C.RESET}")
        for line in r["stderr"].split("\n"):
            print(f"    {C.RED}{line}{C.RESET}")
    print()


# ─── Collect ─────────────────────────────────────────────────────────────────

def cmd_collect(args):
    """Collect results from async dispatches by reading log files."""
    active = load_json(ACTIVE_FILE)
    if not active:
        print(f"{C.DIM}No active async tasks to collect.{C.RESET}")
        return

    results_list = load_json(RESULTS_FILE)
    collected = 0
    still_active = []

    for task in active:
        task_id = task["id"]
        log_file = DATA_DIR / f"{task_id}.log"

        if not log_file.exists():
            still_active.append(task)
            continue

        log_content = log_file.read_text()

        # Check if the SSH process is still running by checking if log is still being written
        # Simple heuristic: if file was modified more than 5 seconds ago, treat as done
        mtime = log_file.stat().st_mtime
        age = time.time() - mtime

        if age < 5 and not log_content.strip():
            # Still running, no output yet
            still_active.append(task)
            continue

        # Treat as complete (either finished or timed out)
        result = {
            "id": task_id,
            "node": task.get("node", "?"),
            "command": task.get("command", "?"),
            "backend": task.get("backend"),
            "model": task.get("model"),
            "exit_code": 0 if log_content.strip() else -1,
            "stdout": log_content.strip(),
            "stderr": "",
            "success": bool(log_content.strip()),
            "duration": age,
            "completed": now_iso(),
            "async": True,
        }
        results_list.append(result)
        collected += 1

        _print_node_result(task.get("node", "?"), result)

        # Clean up log file
        log_file.unlink(missing_ok=True)

    save_json(ACTIVE_FILE, still_active)
    save_json(RESULTS_FILE, results_list)

    print(f"\n{C.GREEN}Collected {collected} result(s).{C.RESET}", end="")
    if still_active:
        print(f" {C.YELLOW}{len(still_active)} still active.{C.RESET}")
    else:
        print()


# ─── Schedule ────────────────────────────────────────────────────────────────

def cmd_schedule(args):
    """Manage scheduled recurring tasks."""
    sub = args.schedule_action if hasattr(args, "schedule_action") and args.schedule_action else "add"

    if sub == "list":
        _schedule_list()
    elif sub == "remove":
        _schedule_remove(args)
    else:
        _schedule_add(args)


def _schedule_add(args):
    """Add a recurring scheduled task."""
    schedules = load_json(SCHEDULE_FILE)
    sched_id = gen_task_id()

    entry = {
        "id": sched_id,
        "command": args.schedule_command,
        "node": args.node or "all",
        "cron": args.cron,
        "backend": args.backend,
        "model": args.model,
        "created": now_iso(),
        "last_run": None,
        "enabled": True,
    }
    schedules.append(entry)
    save_json(SCHEDULE_FILE, schedules)

    print(f"{C.GREEN}Scheduled:{C.RESET} {C.CYAN}{sched_id}{C.RESET}")
    print(f"  Command: {entry['command']}")
    print(f"  Node:    {entry['node']}")
    print(f"  Cron:    {entry['cron']}")
    print(f"\n{C.DIM}Note: Cron execution requires a system cron entry or heartbeat integration.")
    print(f"Add to crontab: {entry['cron']} cd {LOVE_DIR} && python3 tools/fleet-tasks.py queue run{C.RESET}")


def _schedule_list():
    """List all scheduled tasks."""
    schedules = load_json(SCHEDULE_FILE)
    if not schedules:
        print(f"\n{C.DIM}No scheduled tasks.{C.RESET}\n")
        return

    print(f"\n{C.BOLD}── Scheduled Tasks ({len(schedules)}) ──{C.RESET}\n")
    for s in schedules:
        status = f"{C.GREEN}enabled{C.RESET}" if s.get("enabled") else f"{C.RED}disabled{C.RESET}"
        last = s.get("last_run", "never")
        if last and last != "never":
            last = last[:16]
        print(f"  {C.CYAN}{s['id']}{C.RESET}  [{status}]  {s.get('cron','?'):16}  "
              f"{s.get('node','?'):8}  {s.get('command','')[:40]}")
        print(f"    {C.DIM}Last run: {last}{C.RESET}")
    print()


def _schedule_remove(args):
    """Remove a scheduled task."""
    sched_id = args.remove_id
    schedules = load_json(SCHEDULE_FILE)
    original_len = len(schedules)
    schedules = [s for s in schedules if s["id"] != sched_id]
    if len(schedules) == original_len:
        print(f"{C.RED}Schedule {sched_id} not found.{C.RESET}")
        return
    save_json(SCHEDULE_FILE, schedules)
    print(f"{C.GREEN}Removed schedule {sched_id}.{C.RESET}")


# ─── History ─────────────────────────────────────────────────────────────────

def cmd_history(args):
    """Show or manage execution history."""
    sub = args.history_action if hasattr(args, "history_action") and args.history_action else "show"

    if sub == "clear":
        _history_archive()
    else:
        _history_show()


def _history_show():
    """Show combined history: archived + current results."""
    history = load_json(HISTORY_FILE)
    results = load_json(RESULTS_FILE)
    combined = history + results

    if not combined:
        print(f"\n{C.DIM}No execution history.{C.RESET}\n")
        return

    print(f"\n{C.BOLD}── Execution History ({len(combined)} total) ──{C.RESET}\n")

    # Group by date
    by_date: dict[str, list] = {}
    for r in combined:
        date = r.get("completed", "?")[:10]
        by_date.setdefault(date, []).append(r)

    for date in sorted(by_date.keys(), reverse=True):
        tasks = by_date[date]
        print(f"  {C.BOLD}{date}{C.RESET} ({len(tasks)} tasks)")
        for r in tasks:
            status_str = f"{C.GREEN}OK{C.RESET}" if r.get("success") else f"{C.RED}FAIL{C.RESET}"
            dur = format_duration(r.get("duration", 0))
            print(f"    {C.CYAN}{r.get('id','?')}{C.RESET}  {r.get('node','?'):8}  "
                  f"[{status_str}]  {dur:>6}  {r.get('command','')[:40]}")
    print()


def _history_archive():
    """Move current results to history and reset."""
    results = load_json(RESULTS_FILE)
    if not results:
        print(f"{C.DIM}No results to archive.{C.RESET}")
        return

    history = load_json(HISTORY_FILE)
    history.extend(results)
    save_json(HISTORY_FILE, history)
    save_json(RESULTS_FILE, [])
    save_json(ACTIVE_FILE, [])

    print(f"{C.GREEN}Archived {len(results)} results. Queue and active cleared.{C.RESET}")


# ─── Argument Parser ─────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fleet Task Orchestrator — distribute work across Kingdom VPS nodes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="action", help="Command to execute")

    # ── dispatch ──
    p_dispatch = sub.add_parser("dispatch", help="Dispatch a task to fleet nodes")
    p_dispatch.add_argument("command", help="Command or prompt to execute")
    p_dispatch.add_argument("--node", "-n", default="all", help="Target node(s) or 'all'")
    p_dispatch.add_argument("--backend", "-b", help="AI backend: ollama, openai, claude")
    p_dispatch.add_argument("--model", "-m", help="Model name for backend")
    p_dispatch.add_argument("--effort", default="low", help="Agent effort level")
    p_dispatch.add_argument("--parallel", "-P", action="store_true", help="Execute in parallel")
    p_dispatch.add_argument("--async", dest="async_mode", action="store_true", help="Fire and collect later")
    p_dispatch.add_argument("--timeout", "-t", type=int, help="SSH timeout in seconds")

    # ── queue ──
    p_queue = sub.add_parser("queue", help="Show or manage the task queue")
    q_sub = p_queue.add_subparsers(dest="queue_action")

    q_show = q_sub.add_parser("show", help="Show pending queue")

    q_add = q_sub.add_parser("add", help="Add a task to the queue")
    q_add.add_argument("add_command", help="Command or prompt")
    q_add.add_argument("--node", "-n", default="all", help="Target node(s)")
    q_add.add_argument("--priority", "-p", choices=PRIORITIES, default="medium", help="Task priority")
    q_add.add_argument("--backend", "-b", help="AI backend")
    q_add.add_argument("--model", "-m", help="Model name")

    q_run = q_sub.add_parser("run", help="Execute next queued task")

    q_flush = q_sub.add_parser("flush", help="Execute all queued tasks")

    q_remove = q_sub.add_parser("remove", help="Remove a task from queue")
    q_remove.add_argument("remove_id", help="Task ID to remove")

    # ── status ──
    sub.add_parser("status", help="Fleet execution status overview")

    # ── results ──
    p_results = sub.add_parser("results", help="Show task results")
    p_results.add_argument("task_id", nargs="?", help="Specific task ID")

    # ── collect ──
    sub.add_parser("collect", help="Collect pending async results")

    # ── schedule ──
    p_schedule = sub.add_parser("schedule", help="Manage scheduled recurring tasks")
    s_sub = p_schedule.add_subparsers(dest="schedule_action")

    s_add = s_sub.add_parser("add", help="Add a scheduled task")
    s_add.add_argument("schedule_command", help="Command to schedule")
    s_add.add_argument("--node", "-n", default="all", help="Target node(s)")
    s_add.add_argument("--cron", required=True, help="Cron expression (e.g. '0 6 * * *')")
    s_add.add_argument("--backend", "-b", help="AI backend")
    s_add.add_argument("--model", "-m", help="Model name")

    s_list = s_sub.add_parser("list", help="List scheduled tasks")

    s_remove = s_sub.add_parser("remove", help="Remove a scheduled task")
    s_remove.add_argument("remove_id", help="Schedule ID to remove")

    # ── history ──
    p_history = sub.add_parser("history", help="Execution history")
    h_sub = p_history.add_subparsers(dest="history_action")
    h_sub.add_parser("show", help="Show history")
    h_sub.add_parser("clear", help="Archive results and reset")

    return parser


# ─── Convenience: bare schedule command ──────────────────────────────────────
# Support: fleet-tasks.py schedule "Daily health check" --node all --cron "0 6 * * *"
# by detecting when schedule_action is actually the command string

def handle_bare_schedule(argv: list[str]):
    """Handle the shorthand: fleet-tasks.py schedule "command" --node ... --cron ..."""
    if len(argv) < 2 or argv[0] != "schedule":
        return None
    # If second arg is a known subcommand, let argparse handle it
    if argv[1] in ("list", "remove", "add"):
        return None
    # Otherwise treat it as: schedule add "command" --node ... --cron ...
    return ["schedule", "add"] + argv[1:]


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    ensure_data()

    # Handle bare schedule shorthand
    rewritten = handle_bare_schedule(sys.argv[1:])
    argv = rewritten if rewritten else sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.action:
        parser.print_help()
        sys.exit(1)

    dispatch_map = {
        "dispatch": cmd_dispatch,
        "queue": cmd_queue,
        "status": cmd_status,
        "results": cmd_results,
        "collect": cmd_collect,
        "schedule": cmd_schedule,
        "history": cmd_history,
    }

    handler = dispatch_map.get(args.action)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
