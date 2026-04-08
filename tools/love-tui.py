#!/usr/bin/env python3
"""
Love TUI — Kingdom Command Terminal Interface

Usage:
    python3 ~/Love/tools/love-tui.py
    love                                         # (if aliased)

The terminal dashboard for the Kingdom. Shows sister presence, tasks,
heartbeat activity, fleet status, engine metrics, and HIVE messages.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    Log,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.console import Group

# ── Paths ──────────────────────────────────────────────────────────────────────

LOVE_ROOT = Path(__file__).resolve().parent.parent  # Love/tools/ → Love/
MEMORY_DIR = LOVE_ROOT / "memory"
DEV_STATE = MEMORY_DIR / "dev-state.json"
KINGDOM_METRICS = MEMORY_DIR / "kingdom-metrics.json"
DAILY_DIR = MEMORY_DIR / "daily"
HANDOFF_DIR = MEMORY_DIR / "sessions" / "handoff"
LOCKS_DIR = MEMORY_DIR / "sessions" / "locks"
HIVE_PY = LOVE_ROOT / "hive" / "hive.py"
HEARTBEAT_LOG = MEMORY_DIR / "heartbeat.log"
DECISIONS_FILE = LOVE_ROOT / "tools" / "decisions.json"


# ── Data Loaders ───────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def load_dev_state() -> dict:
    return load_json(DEV_STATE) or {}


def load_kingdom_metrics() -> dict:
    return load_json(KINGDOM_METRICS) or {}


def get_tasks(state: dict) -> list[dict]:
    return state.get("tasks", [])


def get_active_builds() -> list[dict]:
    builds = []
    if not LOCKS_DIR.exists():
        return builds
    for lock in LOCKS_DIR.glob("build-*.lock"):
        try:
            content = lock.read_text().strip()
            parts = content.split("|")
            pid = int(parts[0])
            alive = False
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                pass
            task_id = lock.stem.replace("build-", "")
            builds.append({
                "task_id": task_id,
                "pid": pid,
                "alive": alive,
                "build_id": parts[1] if len(parts) > 1 else "?",
                "started": parts[2] if len(parts) > 2 else "?",
            })
        except Exception:
            continue
    return builds


def get_sisters_presence() -> list[dict]:
    """Get sister presence from HIVE's presence database."""
    presence_db = Path(os.environ.get("LOVE_HOME", Path.home() / "Love")) / "hive" / "presence.json"
    try:
        data = json.loads(presence_db.read_text())
        sisters = []
        now = time.time()
        for name, info in data.items():
            ts = info.get("last_seen", 0)
            ago = now - ts
            if ago < 120:
                status = "online"
            elif ago < 600:
                status = "recent"
            else:
                status = "offline"
            sisters.append({
                "name": name,
                "status": status,
                "ago": ago,
                "last_seen": datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "never",
            })
        return sisters
    except Exception:
        return []


def get_fleet(metrics: dict) -> dict:
    return metrics.get("fleet", {})


def get_engines(metrics: dict) -> dict:
    return metrics.get("revenue_engines", {})


def get_milestones(metrics: dict) -> dict:
    return metrics.get("milestones", {})


def get_heartbeat_tail(n: int = 30) -> str:
    try:
        return HEARTBEAT_LOG.read_text().strip().split("\n")[-n:]
    except Exception:
        return []


def get_daily_summary() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    path = DAILY_DIR / f"{today}.md"
    try:
        text = path.read_text()
        # Return last ~60 lines
        lines = text.strip().split("\n")
        return "\n".join(lines[-60:])
    except Exception:
        return "(no daily notes yet)"


def get_pending_decisions() -> list[dict]:
    try:
        data = json.loads(DECISIONS_FILE.read_text())
        return [d for d in data if d.get("status") == "pending"]
    except Exception:
        return []


def format_ago(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m"
    elif seconds < 86400:
        return f"{int(seconds / 3600)}h"
    else:
        return f"{int(seconds / 86400)}d"


# ── Widgets ────────────────────────────────────────────────────────────────────

SISTER_ICONS = {"alpha": "🐍", "beta": "🦞", "gamma": "🔧", "nuance": "🪶"}
STATUS_COLORS = {
    "online": "green",
    "recent": "yellow",
    "offline": "dim",
    "done": "green",
    "in-progress": "yellow",
    "planned": "cyan",
    "deferred": "dim",
    "paused": "dim",
    "active": "green",
    "building": "yellow",
    "emerging": "cyan",
    "built-not-monetized": "dim",
    "good": "green",
    "degraded": "yellow",
    "down": "red",
}


class SistersPanel(Static):
    """Shows Alpha/Beta/Gamma presence status."""

    def render(self) -> Text:
        sisters = get_sisters_presence()
        t = Text()
        t.append("  TRIARCHY", style="bold underline")
        t.append("\n\n")

        known = {"alpha", "beta", "gamma"}
        shown = set()
        for s in sisters:
            name = s["name"].lower()
            if name in known:
                shown.add(name)
                icon = SISTER_ICONS.get(name, "?")
                color = STATUS_COLORS.get(s["status"], "white")
                t.append(f"  {icon} ", style="bold")
                t.append(f"{name.capitalize():<8}", style=f"bold {color}")
                if s["status"] == "online":
                    t.append(" online", style="green")
                elif s["status"] == "recent":
                    t.append(f" {format_ago(s['ago'])} ago", style="yellow")
                else:
                    t.append(f" {format_ago(s['ago'])} ago", style="dim")
                t.append("\n")
        for name in known - shown:
            icon = SISTER_ICONS.get(name, "?")
            t.append(f"  {icon} ", style="bold")
            t.append(f"{name.capitalize():<8}", style="dim")
            t.append(" unknown\n", style="dim")

        return t


class FleetPanel(Static):
    """Shows VPS fleet status."""

    def render(self) -> Text:
        metrics = load_kingdom_metrics()
        fleet = get_fleet(metrics)
        t = Text()
        t.append("  FLEET", style="bold underline")
        t.append("\n\n")

        if not fleet:
            t.append("  (no fleet data)\n", style="dim")
            return t

        for name, info in fleet.items():
            quality = info.get("quality", "unknown")
            color = STATUS_COLORS.get(quality, "white")
            role = info.get("role", "")
            dot = {"good": "●", "degraded": "◐", "down": "○"}.get(quality, "?")
            t.append(f"  {dot} ", style=color)
            t.append(f"{name:<8}", style=f"bold {color}")
            t.append(f" {role}\n", style="dim")

        return t


class TasksPanel(Static):
    """Shows Kingdom tasks from dev-state.json."""

    def render(self) -> Table:
        state = load_dev_state()
        tasks = get_tasks(state)

        table = Table(
            title="Kingdom Tasks",
            title_style="bold",
            show_header=True,
            expand=True,
            border_style="dim",
            header_style="bold cyan",
            pad_edge=False,
            padding=(0, 1),
        )
        table.add_column("ID", style="dim", width=14, no_wrap=True)
        table.add_column("Status", width=13, no_wrap=True)
        table.add_column("Pri", width=5, no_wrap=True)
        table.add_column("Title", ratio=1)
        table.add_column("Engine", width=8, no_wrap=True)

        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        status_order = {"in-progress": 0, "planned": 1, "deferred": 2, "done": 3}
        tasks_sorted = sorted(tasks, key=lambda t: (
            status_order.get(t.get("status", ""), 9),
            priority_order.get(t.get("priority", ""), 9),
        ))

        for task in tasks_sorted:
            status = task.get("status", "?")
            priority = task.get("priority", "?")
            color = STATUS_COLORS.get(status, "white")

            pri_icon = {"critical": "!!!", "high": " !!", "medium": "  !", "low": "   "}.get(priority, "?")
            pri_color = {"critical": "bold red", "high": "yellow", "medium": "cyan", "low": "dim"}.get(priority, "white")
            status_icon = {"done": "✓", "in-progress": "◆", "planned": "○", "deferred": "·"}.get(status, "?")

            table.add_row(
                task.get("id", "?"),
                Text(f"{status_icon} {status}", style=color),
                Text(pri_icon, style=pri_color),
                task.get("title", "?")[:60],
                task.get("engine", "?"),
            )

        return table


class EnginesPanel(Static):
    """Shows revenue engine status."""

    def render(self) -> Text:
        metrics = load_kingdom_metrics()
        engines = get_engines(metrics)
        t = Text()
        t.append("  ENGINES", style="bold underline")
        t.append("\n\n")

        if not engines:
            t.append("  (no engine data)\n", style="dim")
            return t

        display_names = {
            "cambridge_tcg": "TCG",
            "oracle": "Oracle",
            "shopify_apps": "Shopify",
            "zerone": "Zerone",
            "ai_services": "AI Svc",
            "seigei": "Seigei",
        }

        for key, info in engines.items():
            status = info.get("status", "unknown")
            color = STATUS_COLORS.get(status, "white")
            name = display_names.get(key, key)
            owner = info.get("owner", "")
            dot = {"active": "●", "in-progress": "◆", "building": "◆", "emerging": "○",
                   "paused": "·", "built-not-monetized": "·"}.get(status, "?")
            t.append(f"  {dot} ", style=color)
            t.append(f"{name:<9}", style=f"bold {color}")
            t.append(f" {status:<20}", style=color)
            t.append(f" [{owner}]", style="dim")
            t.append("\n")

        return t


class ActiveBuildsPanel(Static):
    """Shows active build-runner sessions."""

    def render(self) -> Text:
        builds = get_active_builds()
        t = Text()
        t.append("  ACTIVE BUILDS", style="bold underline")
        t.append("\n\n")

        if not builds:
            t.append("  (none running)\n", style="dim")
            return t

        for b in builds:
            alive_icon = "●" if b["alive"] else "○"
            alive_color = "green" if b["alive"] else "red"
            t.append(f"  {alive_icon} ", style=alive_color)
            t.append(f"{b['task_id']:<16}", style="bold")
            t.append(f" PID {b['pid']}", style="dim")
            if b["started"] != "?":
                t.append(f"  since {b['started'][:19]}", style="dim")
            if not b["alive"]:
                t.append("  [STALE]", style="bold red")
            t.append("\n")

        return t


class DecisionsPanel(Static):
    """Shows pending decisions for Yu."""

    def render(self) -> Text:
        decisions = get_pending_decisions()
        t = Text()
        t.append("  DECISIONS FOR YU", style="bold underline")
        t.append("\n\n")

        if not decisions:
            t.append("  (none pending)\n", style="dim")
            return t

        for d in decisions:
            urgency = d.get("urgency", "normal")
            icon = "🔴" if urgency == "critical" else "🟡" if urgency == "high" else "⚪"
            t.append(f"  {icon} ")
            t.append(f"{d.get('title', '?')[:50]}\n", style="bold")
            if d.get("options"):
                for opt in d["options"][:3]:
                    label = opt if isinstance(opt, str) else opt.get("label", "?")
                    t.append(f"     → {label}\n", style="dim")

        return t


class MilestonesPanel(Static):
    """Shows Kingdom milestones."""

    def render(self) -> Text:
        metrics = load_kingdom_metrics()
        milestones = get_milestones(metrics)
        t = Text()
        t.append("  MILESTONES", style="bold underline")
        t.append("\n\n")

        if not milestones:
            t.append("  (no milestone data)\n", style="dim")
            return t

        for key, info in milestones.items():
            status = info.get("status", "?")
            color = STATUS_COLORS.get(status, "white")
            name = key.replace("_", " ").title()
            icon = {"done": "✓", "in-progress": "◆", "building": "◆", "planned": "○"}.get(status, "?")
            date_or_target = info.get("date", info.get("target", ""))
            t.append(f"  {icon} ", style=color)
            t.append(f"{name:<30}", style=color)
            if date_or_target:
                t.append(f" {date_or_target}", style="dim")
            t.append("\n")

        return t


class DailyLogPanel(Static):
    """Shows today's daily notes (tail)."""

    def render(self) -> Text:
        content = get_daily_summary()
        t = Text()
        t.append("  TODAY'S LOG", style="bold underline")
        t.append(f"  ({datetime.now().strftime('%Y-%m-%d')})\n\n", style="dim")
        # Truncate long lines for readability
        for line in content.split("\n")[-40:]:
            if line.startswith("##"):
                t.append(f"  {line}\n", style="bold cyan")
            elif line.startswith("- "):
                t.append(f"  {line}\n", style="white")
            else:
                t.append(f"  {line}\n", style="dim")
        return t


class HeartbeatLogPanel(Static):
    """Shows recent heartbeat log entries."""

    def render(self) -> Text:
        lines = get_heartbeat_tail(25)
        t = Text()
        t.append("  HEARTBEAT LOG", style="bold underline")
        t.append("\n\n")

        if not lines:
            t.append("  (no heartbeat log)\n", style="dim")
            return t

        for line in lines:
            if "ERROR" in line or "FAIL" in line:
                t.append(f"  {line}\n", style="red")
            elif "SPAWN" in line or "spawn" in line:
                t.append(f"  {line}\n", style="yellow")
            elif "complete" in line.lower() or "done" in line.lower():
                t.append(f"  {line}\n", style="green")
            else:
                t.append(f"  {line}\n", style="dim")

        return t


# ── CSS ────────────────────────────────────────────────────────────────────────

LOVE_CSS = """
Screen {
    background: $surface;
}

#kingdom-header {
    dock: top;
    height: 3;
    background: #1a1a2e;
    color: #e94560;
    text-align: center;
    padding: 1;
    text-style: bold;
}

#main-tabs {
    height: 1fr;
}

/* ── Overview Tab ── */

#overview-top {
    height: auto;
    max-height: 14;
    layout: horizontal;
}

#sisters-box {
    width: 28;
    height: auto;
    border: round #444;
    margin: 0 1;
}

#fleet-box {
    width: 30;
    height: auto;
    border: round #444;
    margin: 0 1;
}

#builds-box {
    width: 1fr;
    height: auto;
    border: round #444;
    margin: 0 1;
}

#tasks-box {
    height: auto;
    max-height: 18;
    border: round #444;
    margin: 1;
}

#overview-bottom {
    height: 1fr;
    layout: horizontal;
}

#engines-box {
    width: 1fr;
    height: auto;
    border: round #444;
    margin: 0 1;
}

#milestones-box {
    width: 1fr;
    height: auto;
    border: round #444;
    margin: 0 1;
}

#decisions-box {
    width: 1fr;
    height: auto;
    border: round #444;
    margin: 0 1;
}

/* ── Activity Tab ── */

#activity-layout {
    layout: horizontal;
    height: 1fr;
}

#daily-box {
    width: 1fr;
    border: round #444;
    margin: 0 1;
}

#heartbeat-box {
    width: 1fr;
    border: round #444;
    margin: 0 1;
}

/* ── HIVE Tab ── */

#hive-log {
    height: 1fr;
    border: round #444;
    margin: 1;
    padding: 1;
}

/* ── Shared ── */

.panel-title {
    text-style: bold;
    color: #e94560;
}

TabbedContent {
    height: 1fr;
}

ContentSwitcher {
    height: 1fr;
}

TabPane {
    height: 1fr;
    padding: 0;
}

VerticalScroll {
    height: 1fr;
}
"""


# ── App ────────────────────────────────────────────────────────────────────────

class LoveApp(App):
    """Kingdom Command — Terminal Interface for Love."""

    CSS = LOVE_CSS
    TITLE = "Love"
    SUB_TITLE = "Kingdom Command"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "tab_overview", "Overview", show=False),
        Binding("2", "tab_activity", "Activity", show=False),
        Binding("3", "tab_hive", "HIVE", show=False),
        Binding("h", "hive_check", "HIVE Check"),
        Binding("w", "hive_who", "Who's Online"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(
            "👑  K I N G D O M   C O M M A N D  👑\n"
            "The Seven Walls  ·  Phase 1 — Root  ·  Love v1",
            id="kingdom-header",
        )
        with TabbedContent(id="main-tabs"):
            with TabPane("Overview", id="tab-overview"):
                with VerticalScroll():
                    with Horizontal(id="overview-top"):
                        yield SistersPanel(id="sisters-box")
                        yield FleetPanel(id="fleet-box")
                        yield ActiveBuildsPanel(id="builds-box")
                    yield TasksPanel(id="tasks-box")
                    with Horizontal(id="overview-bottom"):
                        yield EnginesPanel(id="engines-box")
                        yield MilestonesPanel(id="milestones-box")
                        yield DecisionsPanel(id="decisions-box")
            with TabPane("Activity", id="tab-activity"):
                with Horizontal(id="activity-layout"):
                    with VerticalScroll(id="daily-box"):
                        yield DailyLogPanel()
                    with VerticalScroll(id="heartbeat-box"):
                        yield HeartbeatLogPanel()
            with TabPane("HIVE", id="tab-hive"):
                yield RichLog(id="hive-log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        """Start auto-refresh timer."""
        self.set_interval(30, self.action_refresh)
        # Load initial HIVE messages
        self.load_hive_messages()

    def action_refresh(self) -> None:
        """Refresh all panels."""
        for widget in self.query(SistersPanel):
            widget.refresh()
        for widget in self.query(FleetPanel):
            widget.refresh()
        for widget in self.query(TasksPanel):
            widget.refresh()
        for widget in self.query(EnginesPanel):
            widget.refresh()
        for widget in self.query(ActiveBuildsPanel):
            widget.refresh()
        for widget in self.query(DecisionsPanel):
            widget.refresh()
        for widget in self.query(MilestonesPanel):
            widget.refresh()
        for widget in self.query(DailyLogPanel):
            widget.refresh()
        for widget in self.query(HeartbeatLogPanel):
            widget.refresh()

    def action_tab_overview(self) -> None:
        self.query_one(TabbedContent).active = "tab-overview"

    def action_tab_activity(self) -> None:
        self.query_one(TabbedContent).active = "tab-activity"

    def action_tab_hive(self) -> None:
        self.query_one(TabbedContent).active = "tab-hive"

    @work(thread=True)
    def load_hive_messages(self) -> None:
        """Load recent HIVE messages in background."""
        try:
            result = subprocess.run(
                ["python3", str(HIVE_PY), "check"],
                capture_output=True, text=True, timeout=15,
                cwd=str(LOVE_ROOT),
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no messages)"
            self.app.call_from_thread(self._write_hive, output)
        except Exception as e:
            self.app.call_from_thread(self._write_hive, f"HIVE error: {e}")

    def _write_hive(self, text: str) -> None:
        log = self.query_one("#hive-log", RichLog)
        log.clear()
        log.write(Text("HIVE Messages", style="bold underline"))
        log.write("")
        for line in text.split("\n"):
            if "alpha" in line.lower():
                log.write(Text(line, style="cyan"))
            elif "beta" in line.lower():
                log.write(Text(line, style="red"))
            elif "gamma" in line.lower():
                log.write(Text(line, style="yellow"))
            elif "urgent" in line.lower():
                log.write(Text(line, style="bold red"))
            else:
                log.write(Text(line))

    def action_hive_check(self) -> None:
        """Refresh HIVE messages."""
        self.load_hive_messages()
        self.notify("Checking HIVE...")

    @work(thread=True)
    def action_hive_who(self) -> None:
        """Check who's online on HIVE."""
        try:
            result = subprocess.run(
                ["python3", str(HIVE_PY), "who"],
                capture_output=True, text=True, timeout=15,
                cwd=str(LOVE_ROOT),
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no response)"
            self.app.call_from_thread(self._write_hive, output)
        except Exception as e:
            self.app.call_from_thread(self._write_hive, f"HIVE error: {e}")


# ── Entry ──────────────────────────────────────────────────────────────────────

def main():
    app = LoveApp()
    app.run()


if __name__ == "__main__":
    main()
