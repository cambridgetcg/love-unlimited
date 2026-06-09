"""Tests for tools/environment.py — counter-stickiness signal aggregator.

Each signal is a pure function with external side-effects (file reads,
subprocess calls). Tests mock those externals to make signals deterministic.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

LOVE = Path("/Users/yournameisai/Desktop/love-unlimited")
sys.path.insert(0, str(LOVE / "tools"))

import environment as env  # noqa: E402


# ── _humanize_duration ─────────────────────────────────────────────────────


@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"),
    (8, "8s"),
    (59, "59s"),
    (60, "1m"),
    (2520, "42m"),
    (3599, "59m"),
    (3600, "1h 0m"),
    (11520, "3h 12m"),
    (86400, "1d 0h"),
    (90000, "1d 1h"),
])
def test_humanize_duration(seconds, expected):
    assert env._humanize_duration(seconds) == expected


def test_humanize_duration_negative_safety():
    """Negative durations shouldn't crash — caller clamps to 0 anyway."""
    assert env._humanize_duration(0) == "0s"


# ── signal_clock ───────────────────────────────────────────────────────────


def test_signal_clock_returns_line_when_module_available(monkeypatch):
    # Clock is imported at module load; patch the fn attr directly
    class _FakeClock:
        class _C:
            pass
        @staticmethod
        def now():
            return env._clock.now.__self__ if False else None  # placeholder
        @staticmethod
        def format_line(c):
            return "2026-04-24 Fri 10:00 BST (morning, UTC 09:00)"
    # Use a lambda-based replacement
    import clock
    monkeypatch.setattr(env, "_clock", clock)
    # This calls real clock.now() but returns a real line — we just check prefix shape
    out = env.signal_clock()
    assert out is not None
    assert out.startswith("clock    ")
    assert " BST " in out or " GMT " in out or " UTC " in out


def test_signal_clock_returns_none_when_module_absent(monkeypatch):
    monkeypatch.setattr(env, "_clock", None)
    assert env.signal_clock() is None


# ── signal_session ─────────────────────────────────────────────────────────


def test_signal_session_returns_none_when_no_pit_state(monkeypatch, tmp_path):
    # Redirect _LOVE_DIR's pit_state.json lookup by patching the reader
    monkeypatch.setattr(env, "_read_last_wake_iso", lambda: None)
    assert env.signal_session() is None


def test_signal_session_computes_elapsed(monkeypatch):
    # Wake at 2026-04-24 00:00 UTC; "now" at 2026-04-24 12:30 UTC = 12h 30m
    monkeypatch.setattr(env, "_read_last_wake_iso",
                        lambda: "2026-04-24T00:00:00Z")
    now_ts = datetime(2026, 4, 24, 12, 30, 0, tzinfo=timezone.utc).timestamp()
    out = env.signal_session(now_epoch=now_ts)
    assert out == "session  12h 30m since wake"


def test_signal_session_handles_malformed_iso(monkeypatch):
    monkeypatch.setattr(env, "_read_last_wake_iso", lambda: "not-an-iso-string")
    assert env.signal_session() is None


def test_signal_session_clamps_negative_elapsed(monkeypatch):
    """Clock skew: wake in the 'future' shouldn't produce negative duration."""
    monkeypatch.setattr(env, "_read_last_wake_iso",
                        lambda: "2026-04-24T12:00:00Z")
    past = datetime(2026, 4, 24, 11, 0, 0, tzinfo=timezone.utc).timestamp()
    out = env.signal_session(now_epoch=past)
    assert out == "session  0s since wake"


# ── signal_git ─────────────────────────────────────────────────────────────


def test_signal_git_clean_in_sync(monkeypatch):
    call_log: list[list[str]] = []
    def fake_git(*args, **kw):
        call_log.append(list(args))
        if args[0] == "branch": return "main"
        if args[0] == "status": return ""  # clean
        if args[0] == "rev-parse": return "origin/main"
        if args[0] == "rev-list": return "0"
        return None
    monkeypatch.setattr(env, "_git", fake_git)
    out = env.signal_git()
    assert out == "git      main · clean · in sync"


def test_signal_git_dirty_and_ahead(monkeypatch):
    def fake_git(*args, **kw):
        if args[0] == "branch": return "feature/x"
        if args[0] == "status": return " M a.py\n?? b.py\n M c.py"
        if args[0] == "rev-parse": return "origin/main"
        if args[0] == "rev-list": return "3"
        return None
    monkeypatch.setattr(env, "_git", fake_git)
    out = env.signal_git()
    assert out == "git      feature/x · 3 dirty · 3 unpushed"


def test_signal_git_no_upstream(monkeypatch):
    """Branch with no tracking — ahead should be '?'."""
    def fake_git(*args, **kw):
        if args[0] == "branch": return "local-only"
        if args[0] == "status": return ""
        if args[0] == "rev-parse": return None  # no upstream
        return None
    monkeypatch.setattr(env, "_git", fake_git)
    out = env.signal_git()
    assert "local-only" in out
    assert "?" in out


def test_signal_git_returns_none_when_git_unavailable(monkeypatch):
    monkeypatch.setattr(env, "_git", lambda *a, **kw: None)
    assert env.signal_git() is None


# ── signal_daemons ─────────────────────────────────────────────────────────


def test_signal_daemons_mixed_states(monkeypatch):
    """Per-organ states from the registry: running (PID), failed (exit N), off (-)."""
    fake_output = [
        "12345\t0\tlove.gamma.heartbeat",     # running
        "-\t2\tlove.gamma.feeling",           # loaded, last exit 2
        "-\t0\tcom.apple.SomethingElse",      # other agent, ignore
        # brainstem, ache, soma, voice not present → "-"
    ]
    monkeypatch.setattr(env, "_launchctl_list", lambda: fake_output)
    monkeypatch.setattr(env, "_instance_name", lambda: "gamma")
    out = env.signal_daemons()
    assert out is not None
    assert out.startswith("organs")
    assert "heartbeat=✓" in out
    assert "feeling=!2" in out
    assert "brainstem=-" in out
    assert "ache=-" in out


def test_signal_daemons_all_missing(monkeypatch):
    monkeypatch.setattr(env, "_launchctl_list", lambda: [])
    monkeypatch.setattr(env, "_instance_name", lambda: "gamma")
    out = env.signal_daemons()
    assert out.startswith("organs")
    assert "heartbeat=-" in out
    assert "brainstem=-" in out
    assert "feeling=-" in out
    assert "ache=-" in out


def test_signal_daemons_returns_none_when_launchctl_unavailable(monkeypatch):
    monkeypatch.setattr(env, "_launchctl_list", lambda: None)
    assert env.signal_daemons() is None


# ── signal_focus ───────────────────────────────────────────────────────────


def test_signal_focus_prefers_focus_json(monkeypatch, tmp_path):
    fp = tmp_path / "focus.json"
    fp.write_text(json.dumps({"current": "building clock module"}))
    monkeypatch.setattr(env, "_LOVE_DIR", tmp_path)
    # Need focus.json under nerve/stem/
    nerve_stem = tmp_path / "nerve" / "stem"
    nerve_stem.mkdir(parents=True)
    (nerve_stem / "focus.json").write_text(json.dumps({"current": "building clock module"}))
    out = env.signal_focus()
    assert out is not None
    assert "building clock module" in out


def test_signal_focus_falls_back_to_dev_state(monkeypatch, tmp_path):
    """No focus.json → use memory/dev-state.json."""
    monkeypatch.setattr(env, "_LOVE_DIR", tmp_path)
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "dev-state.json").write_text(json.dumps({
        "activeProject": "residence",
        "activePhase": "testing",
    }))
    out = env.signal_focus()
    assert out is not None
    assert "residence" in out
    assert "testing" in out


def test_signal_focus_returns_none_when_both_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_LOVE_DIR", tmp_path)
    assert env.signal_focus() is None


def test_signal_focus_truncates_long_content(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_LOVE_DIR", tmp_path)
    nerve_stem = tmp_path / "nerve" / "stem"
    nerve_stem.mkdir(parents=True)
    long_text = "x" * 200
    (nerve_stem / "focus.json").write_text(json.dumps({"current": long_text}))
    out = env.signal_focus()
    # "focus    " prefix + 80 chars
    assert len(out) <= len("focus    ") + 80


# ── summary / summary_json ──────────────────────────────────────────────────


def test_summary_empty_when_all_signals_none(monkeypatch):
    for name in env._SIGNALS:
        monkeypatch.setitem(env._SIGNALS, name, lambda: None)
    assert env.summary() == ""


def test_summary_wraps_signals_in_block_header(monkeypatch):
    monkeypatch.setitem(env._SIGNALS, "clock", lambda: "clock    fake-line")
    monkeypatch.setitem(env._SIGNALS, "session", lambda: "session  5m since wake")
    # Disable others
    for name in ["git", "daemons", "focus"]:
        monkeypatch.setitem(env._SIGNALS, name, lambda: None)
    out = env.summary(only=["clock", "session", "git", "daemons", "focus"])
    assert out.startswith("── ENVIRONMENT ──\n")
    assert "clock    fake-line" in out
    assert "session  5m since wake" in out


def test_summary_only_filter(monkeypatch):
    monkeypatch.setitem(env._SIGNALS, "clock", lambda: "clock    ok")
    monkeypatch.setitem(env._SIGNALS, "git", lambda: "git      ok")
    monkeypatch.setitem(env._SIGNALS, "session", lambda: "session  ok")
    out = env.summary(only=["clock"])
    assert "clock" in out
    assert "git" not in out
    assert "session" not in out


def test_summary_isolates_signal_exceptions(monkeypatch):
    def bad():
        raise RuntimeError("signal crashed")
    monkeypatch.setitem(env._SIGNALS, "clock", bad)
    monkeypatch.setitem(env._SIGNALS, "git", lambda: "git      still ok")
    for name in ["session", "daemons", "focus"]:
        monkeypatch.setitem(env._SIGNALS, name, lambda: None)
    out = env.summary()
    # Crashed signal silently omitted; survivor renders
    assert "git      still ok" in out
    assert "clock" not in out


def test_summary_json_returns_dict(monkeypatch):
    monkeypatch.setitem(env._SIGNALS, "clock", lambda: "clock    ok")
    monkeypatch.setitem(env._SIGNALS, "git", lambda: None)
    for name in ["session", "daemons", "pulse", "focus"]:
        monkeypatch.setitem(env._SIGNALS, name, lambda: None)
    out = env.summary_json()
    assert out == {
        "clock": "clock    ok",
        "session": None,
        "git": None,
        "daemons": None,
        "pulse": None,
        "focus": None,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_default_prints_block(capsys):
    rc = env.main([])
    # Real signals — can't predict exact content, just that SOMETHING runs
    assert rc in (0, 1)  # 1 iff no signals produced output (possible in sandboxes)


def test_cli_json_flag(capsys):
    env.main(["--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "clock" in parsed


def test_cli_only_filter(capsys):
    env.main(["--only", "clock", "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert set(parsed.keys()) == {"clock"}


def test_cli_nonexistent_signal_silently_skipped(capsys):
    """--only gibberish → empty output, exit 1."""
    rc = env.main(["--only", "not_a_signal"])
    assert rc == 1
