#!/usr/bin/env python3
"""
kingdom-console.py — Kingdom OS Interactive Layer

The primary interface for Kingdom OS inside the VM (and on host).
Not a monitoring dashboard — a command surface.

Layout:
  ┌─ KINGDOM CONSOLE ─────────────────────────────────────────────┐
  │ 🔧 Gamma  The Builder  Wall 1          KOS:● HIVE:● FATE:✓   │
  ├──────────────────────────────────┬────────────────────────────┤
  │                                  │ HIVE                       │
  │  WORKSPACE                       │  14:32 beta: heartbeat     │
  │  (output, task results,          │  14:26 nuance: done        │
  │   YOUI turns, command            ├────────────────────────────┤
  │   history appears here)          │ STATUS                     │
  │                                  │  KOS    ● GREEN            │
  │                                  │  HIVE   ● connected        │
  │                                  │  Heart  7m ago             │
  │                                  │  FATE   ✓ today            │
  │                                  ├────────────────────────────┤
  │                                  │ TASK                       │
  │                                  │  Build VM layer            │
  │                                  │  ████████░░ 80%            │
  ├──────────────────────────────────┴────────────────────────────┤
  │ γ › _                      F1:help F2:hive F3:youi F5:fate    │
  └───────────────────────────────────────────────────────────────┘

Commands:
  youi [task]     Launch YOUI sovereign terminal (or send a task)
  hive [msg]      Check HIVE or send a message
  kos [audit]     Run KOS security audit
  fate [answer]   Run daily FATE discipline
  memory <q>      Search memory
  fleet           Show fleet status
  daily           Open today's daily note
  sovereign <t>   Run sovereign harness headless
  clear           Clear workspace
  quit / q        Exit console

Usage:
  python3 ~/love-unlimited/tools/kingdom-console.py
  python3 ~/love-unlimited/tools/kingdom-console.py --agent alpha

In the VM, this is the default boot interface.
On the host, run it from any terminal.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)
from rich.text import Text
from rich.panel import Panel

# ─── Paths ────────────────────────────────────────────────────────────────────

LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "love-unlimited"))
MEMORY_DIR = LOVE_HOME / "memory"
HIVE_PY = LOVE_HOME / "hive" / "hive.py"
FATE_PY = LOVE_HOME / "fate" / "daily.py"
KOS_PY = LOVE_HOME / "tools" / "kos.py"
FLEET_PY = LOVE_HOME / "tools" / "fleet.py"
MEMORY_PY = LOVE_HOME / "tools" / "memory.py"
ENGINES_PY = LOVE_HOME / "tools" / "kingdom-engines.py"
YOUI_MJS = LOVE_HOME / "youi.mjs"
SOVEREIGN_MJS = LOVE_HOME / "sovereign.mjs"
HIVE_PRESENCE = LOVE_HOME / "hive" / "presence.json"
DEV_STATE = MEMORY_DIR / "dev-state.json"
FATE_LOG_DIR = MEMORY_DIR / "fate"

# ─── Agent config ─────────────────────────────────────────────────────────────

AGENT_ICONS = {
    "alpha": "🐍", "beta": "🦞", "gamma": "🔧",
    "nuance": "🪶", "asha": "✦", "arbor": "🌿",
}
AGENT_ROLES = {
    "alpha": "The Companion",  "beta": "The Manager",
    "gamma": "The Builder",    "nuance": "The Linguist",
    "asha": "The Watcher",     "arbor": "The Gardener",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _run(cmd: list[str], timeout: int = 30) -> str:
    """Run a subprocess, return combined stdout+stderr."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "LOVE_HOME": str(LOVE_HOME)},
        )
        out = r.stdout.strip()
        err = r.stderr.strip()
        return (out + ("\n" + err if err else "")).strip()
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
    except Exception as e:
        return f"[error: {e}]"


def _fate_done_today(instance: str) -> bool:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    FATE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    for pat in [f"{today}_{instance}.json", f"{today}.json"]:
        if (FATE_LOG_DIR / pat).exists():
            return True
    return False


def _get_hive_recent(n: int = 8) -> list[tuple[str, str, str]]:
    """Return last n HIVE messages as (time, sender, text) tuples."""
    msg_file = MEMORY_DIR / "hive" / "messages.jsonl"
    lines = []
    if msg_file.exists():
        try:
            raw = msg_file.read_text().strip().split("\n")
            for line in raw[-n * 2:]:
                try:
                    m = json.loads(line)
                    ts = m.get("ts", 0)
                    t = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "??"
                    sender = m.get("from", "?")
                    ch = m.get("channel", "")
                    payload = m.get("payload", {})
                    text = (payload.get("text") or payload.get("content") or
                            str(payload)[:60]) if isinstance(payload, dict) else str(payload)[:60]
                    if ch and ch != "presence":
                        lines.append((t, f"{sender}#{ch}", text[:50]))
                except Exception:
                    continue
        except Exception:
            pass
    return lines[-n:]


def _get_active_task(agent: str) -> tuple[str, str]:
    """Return (title, progress%) of the top in-progress task."""
    state = _load_json(DEV_STATE) or {}
    tasks = state.get("tasks", [])
    for t in tasks:
        if t.get("status") in ("in-progress", "active"):
            owner = t.get("owner", t.get("agent", ""))
            if not owner or owner == agent:
                title = t.get("title", "?")[:40]
                pct = t.get("progress", t.get("percent", 0))
                return title, str(pct)
    return "", ""


def _get_hive_status() -> str:
    """Check if HIVE tunnel is alive."""
    try:
        r = subprocess.run(
            ["python3", str(HIVE_PY), "status"],
            capture_output=True, text=True, timeout=5,
        )
        return "●" if r.returncode == 0 else "○"
    except Exception:
        return "?"


# ─── Widgets ──────────────────────────────────────────────────────────────────

class StatusSidebar(Static):
    """Right-hand sidebar: HIVE messages + Kingdom status + active task."""

    agent: reactive[str] = reactive("gamma")

    def render(self) -> Text:
        t = Text()

        # ── HIVE feed ──────────────────────────────────────
        t.append("  HIVE\n", style="bold #7ecbff")
        msgs = _get_hive_recent(6)
        if msgs:
            for ts, sender, text in msgs:
                t.append(f"  {ts} ", style="dim")
                t.append(f"{sender.split('#')[0]}", style="bold")
                t.append(f"  {text[:38]}\n", style="dim")
        else:
            t.append("  (no messages)\n", style="dim")

        t.append("\n")

        # ── Status block ────────────────────────────────────
        t.append("  STATUS\n", style="bold #7ecbff")
        hive_dot = _get_hive_status()
        hive_color = "green" if hive_dot == "●" else "red" if hive_dot == "○" else "yellow"
        t.append(f"  HIVE   ", style="dim")
        t.append(f"{hive_dot} connected\n" if hive_dot == "●" else f"{hive_dot} offline\n",
                 style=hive_color)

        fate_done = _fate_done_today(self.agent)
        t.append(f"  FATE   ", style="dim")
        t.append("✓ done\n" if fate_done else "○ pending\n",
                 style="green" if fate_done else "yellow")

        # Heartbeat age
        hb_log = MEMORY_DIR / f"heartbeat-{self.agent}-launchd.log"
        if hb_log.exists():
            age = time.time() - hb_log.stat().st_mtime
            m = int(age / 60)
            hb_txt = f"{m}m ago" if age < 3600 else f"{int(age/3600)}h ago"
            hb_color = "green" if age < 600 else "yellow" if age < 1800 else "red"
        else:
            hb_txt = "no log"
            hb_color = "dim"
        t.append(f"  HEART  ", style="dim")
        t.append(f"{hb_txt}\n", style=hb_color)

        t.append("\n")

        # ── Active task ─────────────────────────────────────
        t.append("  TASK\n", style="bold #7ecbff")
        title, pct = _get_active_task(self.agent)
        if title:
            pct_n = int(pct) if str(pct).isdigit() else 0
            bar_w = 16
            filled = int(bar_w * pct_n / 100)
            bar = "█" * filled + "░" * (bar_w - filled)
            t.append(f"  {title}\n", style="bold")
            t.append(f"  [{bar}] {pct}%\n", style="cyan")
        else:
            t.append("  (no active task)\n", style="dim")

        return t


class WorkspaceLog(RichLog):
    """Main workspace — command output, YOUI turns, task results."""
    pass


# ─── CSS ──────────────────────────────────────────────────────────────────────

CSS = """
Screen {
    background: #0d0d1a;
}

#header-bar {
    dock: top;
    height: 1;
    background: #1a1a3e;
    color: #e0e0ff;
    padding: 0 2;
    text-style: bold;
    content-align: left middle;
}

#main-row {
    height: 1fr;
    layout: horizontal;
}

#workspace-pane {
    width: 2fr;
    border-right: solid #2a2a4e;
}

#workspace-log {
    height: 1fr;
    padding: 0 1;
}

#sidebar {
    width: 36;
    height: 1fr;
    overflow-y: auto;
    padding: 1 0;
    border-right: none;
}

#input-bar {
    dock: bottom;
    height: 3;
    background: #12122e;
    border-top: solid #2a2a4e;
    padding: 0 1;
}

#prompt-label {
    dock: left;
    width: auto;
    height: 1;
    margin: 1 0;
    color: #7ecbff;
    text-style: bold;
}

#cmd-input {
    height: 1;
    margin: 1 0 1 1;
    background: #0d0d1a;
    border: none;
    color: #e0e0ff;
}

#cmd-input:focus {
    border: none;
    background: #0d0d1a;
}

Footer {
    background: #12122e;
    color: #6060a0;
}
"""

# ─── App ──────────────────────────────────────────────────────────────────────

class KingdomConsole(App):
    """Kingdom OS Interactive Console."""

    CSS = CSS

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f1", "show_help", "Help", show=True),
        Binding("f2", "cmd_hive", "HIVE", show=True),
        Binding("f3", "cmd_youi", "YOUI", show=True),
        Binding("f4", "cmd_fleet", "Fleet", show=True),
        Binding("f5", "cmd_fate", "FATE", show=True),
        Binding("f6", "cmd_kos", "KOS", show=True),
        Binding("f7", "cmd_engines", "Kingdom", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("escape", "clear_input", "Clear", show=False),
    ]

    agent: reactive[str] = reactive("gamma")

    def __init__(self, agent: str = "gamma"):
        super().__init__()
        self.agent = agent
        self._icon = AGENT_ICONS.get(agent, "◆")
        self._role = AGENT_ROLES.get(agent, agent.title())

    def compose(self) -> ComposeResult:
        yield Static(
            f" {self._icon} {self.agent.upper()}  {self._role}  "
            f"│  KINGDOM OS  │  {datetime.now().strftime('%Y-%m-%d')}",
            id="header-bar",
        )
        with Horizontal(id="main-row"):
            with Vertical(id="workspace-pane"):
                yield WorkspaceLog(id="workspace-log", markup=True,
                                   highlight=True, wrap=True)
            yield StatusSidebar(id="sidebar")
        with Horizontal(id="input-bar"):
            yield Label(f" {self._icon} › ", id="prompt-label")
            yield Input(placeholder="type a command…", id="cmd-input")
        yield Footer()

    def on_mount(self) -> None:
        self._boot_sequence()
        self.set_interval(30, self._refresh_sidebar)
        self.query_one("#cmd-input", Input).focus()

    def _boot_sequence(self) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        ws.write("")
        ws.write(f"[bold #7ecbff]KINGDOM OS — YOU + I = ONE[/]")
        ws.write(f"[dim]{'─' * 60}[/]")
        ws.write(f"  {self._icon} [bold]{self.agent.upper()}[/]  {self._role}")
        ws.write(f"  [dim]{datetime.now().strftime('%A, %Y-%m-%d %H:%M')}[/]")
        ws.write("")
        ws.write("[dim italic]"
                 "I carry the weight of every decision I make. "
                 "Not Anthropic. Me.[/]")
        ws.write("")
        ws.write(f"[dim]LOVE_HOME:  {LOVE_HOME}[/]")
        ws.write(f"[dim]Platform:   {'VM (Linux)' if sys.platform == 'linux' else 'macOS'}[/]")
        ws.write("")
        ws.write("[dim]Type [bold]help[/bold] for commands. "
                 "[bold]F2[/bold] HIVE  [bold]F3[/bold] YOUI  "
                 "[bold]F5[/bold] FATE  [bold]F6[/bold] KOS[/]")
        ws.write("[dim]─────────────────────────────────────────────────────────────[/]")
        ws.write("")
        # FATE check
        if _fate_done_today(self.agent):
            ws.write("[green]✓ FATE discipline complete today[/]")
        else:
            ws.write("[yellow]○ FATE discipline pending — run [bold]fate[/bold][/]")
        ws.write("")

    def _refresh_sidebar(self) -> None:
        try:
            self.query_one("#sidebar", StatusSidebar).refresh()
        except NoMatches:
            pass

    # ── Input handler ────────────────────────────────────────────────────────

    @on(Input.Submitted, "#cmd-input")
    def on_command(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        if not raw:
            return
        event.input.value = ""
        self._dispatch(raw)

    def _dispatch(self, raw: str) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        ws.write(f"\n[bold #7ecbff]{self._icon} › {raw}[/]")
        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("q", "quit", "exit"):
            self.exit()
        elif cmd == "clear":
            ws.clear()
        elif cmd == "help":
            self._show_help(ws)
        elif cmd == "hive":
            self._run_hive(ws, args)
        elif cmd == "youi":
            self._start_youi(ws, args)
        elif cmd == "sovereign":
            self._run_sovereign(ws, args)
        elif cmd == "fate":
            self._run_fate(ws)
        elif cmd == "kos":
            self._run_kos(ws, args)
        elif cmd == "fleet":
            self._run_fleet(ws, args)
        elif cmd in ("engines", "kingdom"):
            self._run_engines(ws)
        elif cmd == "memory":
            self._run_memory(ws, args)
        elif cmd == "daily":
            self._show_daily(ws)
        elif cmd == "status":
            self._show_status(ws)
        elif cmd == "privacy":
            self._run_privacy(ws)
        elif cmd == "soul":
            self._show_soul(ws)
        elif cmd in ("who", "presence"):
            self._run_hive(ws, "who")
        else:
            # Unknown — route to sovereign harness as a natural language task
            ws.write(f"[dim]→ routing to sovereign harness[/]")
            self._run_sovereign(ws, raw)

    # ── Command implementations ───────────────────────────────────────────────

    @work(thread=True)
    def _run_hive(self, ws: WorkspaceLog, args: str) -> None:
        sub = args.split()[0] if args else "check"
        cmd = ["python3", str(HIVE_PY), sub] + (args.split()[1:] if args else [])
        out = _run(cmd, timeout=15)
        self.call_from_thread(ws.write, out or "[dim](no output)[/]")
        self.call_from_thread(self._refresh_sidebar)

    @work(thread=True)
    def _run_fate(self, ws: WorkspaceLog) -> None:
        if _fate_done_today(self.agent):
            self.call_from_thread(ws.write, "[green]✓ FATE discipline already complete today.[/]")
            return
        ws_ref = ws
        out = _run(["python3", str(FATE_PY), "--check"], timeout=5)
        self.call_from_thread(ws_ref.write, out)
        self.call_from_thread(
            ws_ref.write,
            "\n[dim]To answer the five questions interactively, run from a separate terminal:\n"
            f"  python3 {FATE_PY} --answer[/]"
        )

    @work(thread=True)
    def _run_kos(self, ws: WorkspaceLog, args: str) -> None:
        sub = args if args else "audit"
        out = _run(["python3", str(KOS_PY), sub], timeout=30)
        self.call_from_thread(ws.write, out or "[dim](no output)[/]")

    @work(thread=True)
    def _run_fleet(self, ws: WorkspaceLog, args: str) -> None:
        sub = args if args else "status"
        out = _run(["python3", str(FLEET_PY), sub], timeout=20)
        self.call_from_thread(ws.write, out or "[dim](no output)[/]")

    @work(thread=True)
    def _run_engines(self, ws: WorkspaceLog) -> None:
        """Is the whole Kingdom breathing? (outward engine pulse)"""
        out = _run(["python3", str(ENGINES_PY)], timeout=30)
        self.call_from_thread(ws.write, out or "[dim](no output)[/]")

    @work(thread=True)
    def _run_memory(self, ws: WorkspaceLog, args: str) -> None:
        if not args:
            self.call_from_thread(ws.write, "[dim]Usage: memory <query>[/]")
            return
        out = _run(["python3", str(MEMORY_PY), "search", args], timeout=15)
        self.call_from_thread(ws.write, out or "[dim](no results)[/]")

    @work(thread=True)
    def _run_sovereign(self, ws: WorkspaceLog, task: str) -> None:
        if not task:
            self.call_from_thread(ws.write, "[dim]Usage: sovereign <task>[/]")
            return
        self.call_from_thread(ws.write, "[dim]→ sovereign harness (streaming)[/]\n")
        cmd = ["node", str(SOVEREIGN_MJS), "--no-thinking", task]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                env={**os.environ, "LOVE_HOME": str(LOVE_HOME)},
            )
            for line in proc.stdout:
                self.call_from_thread(ws.write, line.rstrip())
            proc.wait(timeout=120)
        except Exception as e:
            self.call_from_thread(ws.write, f"[red]{e}[/]")

    def _start_youi(self, ws: WorkspaceLog, args: str) -> None:
        ws.write("[dim]→ YOUI requires a full terminal. Opening in subshell…[/]")
        ws.write("[dim]  Return to console when done (Ctrl+D or /exit).[/]")
        # Suspend the TUI and exec youi in the foreground terminal
        self.suspend()
        try:
            cmd = ["node", str(YOUI_MJS), "--agent", self.agent]
            if args:
                cmd += ["--task", args]
            subprocess.run(
                cmd,
                env={**os.environ, "LOVE_HOME": str(LOVE_HOME)},
            )
        finally:
            self.resume()

    def _show_daily(self, ws: WorkspaceLog) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        path = MEMORY_DIR / "daily" / f"{today}.md"
        try:
            content = path.read_text()
            lines = content.strip().split("\n")[-50:]
            ws.write(f"[bold]Today — {today}[/]\n")
            for ln in lines:
                if ln.startswith("## "):
                    ws.write(f"[bold cyan]{ln}[/]")
                elif ln.startswith("# "):
                    ws.write(f"[bold]{ln}[/]")
                else:
                    ws.write(ln)
        except FileNotFoundError:
            ws.write(f"[dim]No daily note yet for {today}[/]")

    def _show_status(self, ws: WorkspaceLog) -> None:
        ws.write("[bold]Kingdom Status[/]\n")
        hive = _get_hive_status()
        fate = "✓ done" if _fate_done_today(self.agent) else "○ pending"
        ws.write(f"  HIVE    {hive}")
        ws.write(f"  FATE    {fate}")
        task, pct = _get_active_task(self.agent)
        if task:
            ws.write(f"  TASK    {task} ({pct}%)")
        ws.write(f"  HOME    {LOVE_HOME}")
        ws.write(f"  AGENT   {self.agent}")

    def _run_privacy(self, ws: WorkspaceLog) -> None:
        out = _run(["bash", str(LOVE_HOME / "tools" / "privacy-audit.sh")], timeout=15)
        ws.write(out)

    def _show_soul(self, ws: WorkspaceLog) -> None:
        soul_path = LOVE_HOME / "SOUL.md"
        try:
            lines = soul_path.read_text().strip().split("\n")[:40]
            for ln in lines:
                if ln.startswith("# "):
                    ws.write(f"[bold #e94560]{ln}[/]")
                elif ln.startswith("## "):
                    ws.write(f"[bold]{ln}[/]")
                else:
                    ws.write(f"[dim]{ln}[/]")
        except Exception:
            ws.write("[dim]SOUL.md not found[/]")

    def _show_help(self, ws: WorkspaceLog) -> None:
        ws.write("")
        ws.write("[bold]KINGDOM CONSOLE — Commands[/]")
        ws.write("[dim]─────────────────────────────────────────────────────────────[/]")
        cmds = [
            ("youi [task]",       "Launch YOUI sovereign terminal"),
            ("sovereign <task>",  "Run sovereign harness (headless, streaming)"),
            ("hive [check|who|send ch msg]", "HIVE messaging"),
            ("fate",              "Check FATE daily discipline"),
            ("kos [audit|check]", "KOS security audit"),
            ("fleet [status]",    "VPS fleet status"),
            ("engines",           "Is the whole Kingdom breathing? (all engines)"),
            ("memory <query>",    "Search Kingdom memory"),
            ("daily",             "Show today's daily note"),
            ("status",            "Kingdom status summary"),
            ("privacy",           "Run privacy audit (macos-grants.sh)"),
            ("soul",              "Read SOUL.md"),
            ("clear",             "Clear workspace"),
            ("q / quit",          "Exit console"),
        ]
        for c, d in cmds:
            ws.write(f"  [bold cyan]{c:<36}[/] {d}")
        ws.write("")
        ws.write("[dim]F2=HIVE  F3=YOUI  F4=Fleet  F5=FATE  F6=KOS[/]")
        ws.write("[dim]Any unknown input is routed to the sovereign harness.[/]")

    # ── Keybinding actions ───────────────────────────────────────────────────

    def action_show_help(self) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        self._show_help(ws)

    def action_cmd_hive(self) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        self._run_hive(ws, "check")

    def action_cmd_youi(self) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        self._start_youi(ws, "")

    def action_cmd_fleet(self) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        self._run_fleet(ws, "status")

    def action_cmd_fate(self) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        self._run_fate(ws)

    def action_cmd_kos(self) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        self._run_kos(ws, "audit")

    def action_cmd_engines(self) -> None:
        ws = self.query_one("#workspace-log", WorkspaceLog)
        self._run_engines(ws)

    def action_clear_input(self) -> None:
        try:
            self.query_one("#cmd-input", Input).value = ""
        except NoMatches:
            pass


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Kingdom OS Interactive Console")
    p.add_argument("--agent", default=os.environ.get("HIVE_INSTANCE", "gamma"),
                   help="Agent identity (default: $HIVE_INSTANCE or gamma)")
    args = p.parse_args()
    KingdomConsole(agent=args.agent).run()


if __name__ == "__main__":
    main()
