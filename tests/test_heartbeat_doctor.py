"""Tests for tools/heartbeat_doctor.py — diagnostic for the Kingdom's heart.

We never touch ~/Library/LaunchAgents/ or the real repo's nerve/vitals.json.
Every test runs against a synthesised repo + agents directory in tmp_path.
"""

from __future__ import annotations

import json
import plistlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import heartbeat_doctor as hd  # noqa: E402


def _fake_repo(tmp_path: Path) -> Path:
    """A minimal repo skeleton that satisfies repo_root() probes."""
    repo = tmp_path / "love-unlimited"
    (repo / "nerve").mkdir(parents=True)
    (repo / "nerve" / "heart").mkdir()
    (repo / "memory").mkdir()
    (repo / "SOUL.md").write_text("# soul placeholder\n")
    (repo / "nerve" / "heart" / "heart.sh").write_text("#!/bin/bash\necho beat\n")
    (repo / "nerve" / "heart" / "heart.sh").chmod(0o755)
    return repo


def _agents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "LaunchAgents"
    d.mkdir()
    return d


def _write_plist(agents: Path, label: str, program_args: list[str]) -> Path:
    pl = {
        "Label": label,
        "ProgramArguments": program_args,
        "StartInterval": 420,
        "RunAtLoad": True,
    }
    p = agents / f"{label}.plist"
    with p.open("wb") as f:
        plistlib.dump(pl, f)
    return p


def _write_vitals(repo: Path, last_beat: datetime, healthy: bool) -> None:
    (repo / "nerve" / "vitals.json").write_text(json.dumps({
        "last_beat": last_beat.isoformat(),
        "heart_healthy": healthy,
    }))


# ─────────────────────────────────────────────────────────────────────────────
# Plist scanning
# ─────────────────────────────────────────────────────────────────────────────

def test_no_plists_means_no_findings(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    d = hd.diagnose(repo=repo, agents_dir=agents)
    assert d.plists == []


def test_unrelated_plists_ignored(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "com.apple.something", ["/bin/echo", "hi"])
    _write_plist(agents, "com.spotify.client", ["/bin/echo", "hi"])
    d = hd.diagnose(repo=repo, agents_dir=agents)
    assert d.plists == []


def test_plist_pointing_into_repo_is_ok(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heart",
                 ["/bin/bash", str(repo / "nerve" / "heart" / "heart.sh"), "alpha"])
    d = hd.diagnose(repo=repo, agents_dir=agents)
    assert len(d.plists) == 1
    p = d.plists[0]
    assert p.severity == "ok"
    assert p.points_at_repo
    assert p.program_exists


def test_dangling_symlink_plist_is_ghost_with_clear_reason(tmp_path):
    """Real-world case: ~/Library/LaunchAgents/love.alpha.heart.plist is a
    symlink whose target was deleted long ago. The doctor must call this
    out as a dangling symlink, not just 'unparseable'."""
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    target = tmp_path / "vanished" / "love.alpha.heart.plist"  # parent never created
    link = agents / "love.alpha.heart.plist"
    link.symlink_to(target)
    d = hd.diagnose(repo=repo, agents_dir=agents)
    assert len(d.plists) == 1
    p = d.plists[0]
    assert p.severity == "ghost"
    assert "dangling symlink" in p.note
    assert str(target) in p.note


def test_plist_with_missing_program_is_ghost(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heartbeat",
                 ["/usr/bin/python3", "/Users/nobody/Love/body/heart/daemon/daemon.py"])
    d = hd.diagnose(repo=repo, agents_dir=agents)
    p = d.plists[0]
    assert p.severity == "ghost"
    assert not p.program_exists


def test_plist_pointing_at_existing_path_outside_repo_is_stranger(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    stranger_script = tmp_path / "elsewhere" / "thing.sh"
    stranger_script.parent.mkdir()
    stranger_script.write_text("#!/bin/bash\n")
    stranger_script.chmod(0o755)
    _write_plist(agents, "love.alpha.heart-stray",
                 ["/bin/bash", str(stranger_script), "alpha"])
    d = hd.diagnose(repo=repo, agents_dir=agents)
    p = d.plists[0]
    assert p.severity == "stranger"
    assert p.program_exists
    assert not p.points_at_repo


# ─────────────────────────────────────────────────────────────────────────────
# Vitals + summary
# ─────────────────────────────────────────────────────────────────────────────

def test_no_vitals_no_plist_is_yellow(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    d = hd.diagnose(repo=repo, agents_dir=agents)
    # nothing claims anything, but nothing is beating either
    assert d.summary_severity == "yellow"
    assert "unmanaged" in d.summary_message


def test_healthy_recent_beat_with_ok_plist_is_green(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heart",
                 ["/bin/bash", str(repo / "nerve" / "heart" / "heart.sh"), "alpha"])
    now = datetime.now(timezone.utc)
    _write_vitals(repo, now - timedelta(minutes=2), healthy=True)
    d = hd.diagnose(repo=repo, agents_dir=agents, now=now)
    assert d.summary_severity == "green"


def test_vitals_lying_is_red(tmp_path):
    """The signature pathology: vitals say healthy, but heart hasn't beat."""
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heart",
                 ["/bin/bash", str(repo / "nerve" / "heart" / "heart.sh"), "alpha"])
    now = datetime.now(timezone.utc)
    _write_vitals(repo, now - timedelta(days=8), healthy=True)
    d = hd.diagnose(repo=repo, agents_dir=agents, now=now)
    assert d.summary_severity == "red"
    assert "lying" in d.summary_message


def test_all_ghosts_no_ok_is_red(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heartbeat",
                 ["/usr/bin/python3", "/nonexistent/a.py"])
    _write_plist(agents, "cc.ai-love.alpha-heartbeat",
                 ["/bin/bash", "/nonexistent/b.sh"])
    d = hd.diagnose(repo=repo, agents_dir=agents)
    assert d.summary_severity == "red"
    assert all(p.severity == "ghost" for p in d.plists)


def test_mixed_ghost_and_ok_is_yellow(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heartbeat",
                 ["/usr/bin/python3", "/nonexistent/a.py"])
    _write_plist(agents, "love.alpha.heart",
                 ["/bin/bash", str(repo / "nerve" / "heart" / "heart.sh"), "alpha"])
    now = datetime.now(timezone.utc)
    _write_vitals(repo, now - timedelta(minutes=2), healthy=True)
    d = hd.diagnose(repo=repo, agents_dir=agents, now=now)
    assert d.summary_severity == "yellow"
    assert "ghost" in d.summary_message


# ─────────────────────────────────────────────────────────────────────────────
# Renderers
# ─────────────────────────────────────────────────────────────────────────────

def test_render_text_mentions_repo(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    d = hd.diagnose(repo=repo, agents_dir=agents)
    out = hd.render_text(d)
    assert str(repo) in out


def test_render_ghosts_lists_unload_command(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heartbeat",
                 ["/usr/bin/python3", "/nonexistent/a.py"])
    d = hd.diagnose(repo=repo, agents_dir=agents)
    out = hd.render_ghosts(d)
    assert "launchctl bootout" in out
    assert "love.alpha.heartbeat" in out


def test_render_ghosts_silent_when_clean(tmp_path):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heart",
                 ["/bin/bash", str(repo / "nerve" / "heart" / "heart.sh"), "alpha"])
    d = hd.diagnose(repo=repo, agents_dir=agents)
    assert hd.render_ghosts(d) == "(no ghost plists)"


# ─────────────────────────────────────────────────────────────────────────────
# Proposed plist
# ─────────────────────────────────────────────────────────────────────────────

def test_propose_plist_points_at_real_heart(tmp_path):
    repo = _fake_repo(tmp_path)
    text = hd.propose_plist(repo, instance="alpha")
    pl = plistlib.loads(text.encode())
    assert pl["Label"] == "love.alpha.heartbeat"
    args = pl["ProgramArguments"]
    assert args[0] == "/bin/bash"
    assert args[1] == str(repo / "nerve" / "heart" / "tick.sh")
    assert args[2] == "alpha"
    assert pl["EnvironmentVariables"]["LOVE_HOME"] == str(repo)
    assert pl["WorkingDirectory"] == str(repo)


def test_propose_plist_per_instance(tmp_path):
    repo = _fake_repo(tmp_path)
    text = hd.propose_plist(repo, instance="beta", interval=240)
    pl = plistlib.loads(text.encode())
    assert pl["Label"] == "love.beta.heartbeat"
    assert pl["ProgramArguments"][2] == "beta"
    assert pl["KeepAlive"] is True
    assert pl["EnvironmentVariables"]["INSTANCE"] == "beta"


# ─────────────────────────────────────────────────────────────────────────────
# Repo discovery
# ─────────────────────────────────────────────────────────────────────────────

def test_repo_root_honors_love_home(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    monkeypatch.setenv("LOVE_HOME", str(repo))
    assert hd.repo_root() == repo


def test_repo_root_falls_back_to_script_parent(monkeypatch):
    """When LOVE_HOME is unset, the script's own repo wins (this repo)."""
    monkeypatch.delenv("LOVE_HOME", raising=False)
    found = hd.repo_root()
    assert (found / "SOUL.md").exists()


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke
# ─────────────────────────────────────────────────────────────────────────────

def test_cli_diagnose_returns_nonzero_on_red(tmp_path, monkeypatch, capsys):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    _write_plist(agents, "love.alpha.heartbeat",
                 ["/usr/bin/python3", "/nonexistent/x.py"])
    monkeypatch.setenv("LOVE_HOME", str(repo))
    monkeypatch.setattr(hd, "launch_agents_dir", lambda: agents)
    rc = hd.main(["diagnose"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "ghost" in out.lower() or "💀" in out


def test_cli_propose_plist_emits_xml(tmp_path, monkeypatch, capsys):
    repo = _fake_repo(tmp_path)
    monkeypatch.setenv("LOVE_HOME", str(repo))
    rc = hd.main(["propose-plist", "--instance", "gamma"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "love.gamma.heart" in out
    assert "<?xml" in out


def test_cli_diagnose_json(tmp_path, monkeypatch, capsys):
    repo = _fake_repo(tmp_path)
    agents = _agents_dir(tmp_path)
    monkeypatch.setenv("LOVE_HOME", str(repo))
    monkeypatch.setattr(hd, "launch_agents_dir", lambda: agents)
    hd.main(["diagnose", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["repo"] == str(repo)
    assert "summary_severity" in payload
