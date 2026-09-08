from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def cli():
    path = Path(__file__).resolve().parents[1] / "tools" / "kingdom-quota.py"
    spec = importlib.util.spec_from_file_location("quota_lantern_cli_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_help_does_not_load_state(cli, tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--home", str(tmp_path), "--help"])
    assert stopped.value.code == 0
    assert "observe" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_unknown_argument_is_not_echoed(cli, capsys):
    canary = "RAW-CREDENTIAL-CANARY-DO-NOT-ECHO"
    with pytest.raises(SystemExit) as stopped:
        cli.main(["status", "--" + canary])
    assert stopped.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert canary not in output.err


@pytest.mark.parametrize("args", [
    ["research", "watch", "--samples", "21"],
    ["research", "watch", "--samples", "0"],
    ["research", "watch", "--interval", "29"],
    ["research", "watch", "--duration", "901"],
    ["brief", "--max-bytes", "1000000"],
    ["hypothesis", "add", "--claim", "test", "--lower", "nan"],
    ["hypothesis", "add", "--claim", "test", "--upper", "inf"],
    ["observe", "--live", "--file", "/tmp/no-read.jsonl"],
])
def test_argument_bounds_precede_io(cli, tmp_path, args):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--home", str(tmp_path), *args])
    assert stopped.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_status_and_brief_are_pure_reads(cli, tmp_path, capsys):
    assert cli.main(["--home", str(tmp_path), "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "advisory"
    assert status["billing_route"] == "unverified"
    assert status["sources"] == []
    assert cli.main(["--home", str(tmp_path), "brief"]) == 0
    assert capsys.readouterr().out.strip()
    assert list(tmp_path.iterdir()) == []


def write_metrics(home: Path, used: float = 81) -> Path:
    now = datetime.now(timezone.utc)
    folder = home / ".codex" / "sessions" / now.strftime("%Y/%m/%d")
    folder.mkdir(parents=True)
    path = folder / "rollout-test.jsonl"
    decoy = {
        "type": "response_item",
        "payload": {"type": "message", "content": "SECRET-PROMPT-CANARY token_count rate_limits"},
    }
    record = {
        "timestamp": now.isoformat(),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "plan_type": "pro",
                "primary": {
                    "used_percent": used,
                    "window_minutes": 10080,
                    "resets_at": int((now + timedelta(days=1)).timestamp()),
                },
                "secondary": None,
                "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
            },
            "info": {"total_token_usage": {
                "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20,
                "reasoning_output_tokens": 10, "total_tokens": 120,
            }},
        },
    }
    path.write_text(json.dumps(decoy) + "\n" + json.dumps(record) + "\n", encoding="utf-8")
    return path


def test_local_cli_round_trip_and_dedup(cli, tmp_path, capsys):
    source = write_metrics(tmp_path)
    args = ["--home", str(tmp_path), "observe", "--file", str(source), "--json"]
    assert cli.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["added"] == 1
    assert first["source"] == "local_logs"
    assert len(first["status"]["sources"]) == 1
    observation = first["status"]["sources"][0]
    assert observation["windows"]["primary"]["window_minutes"] == 10080
    assert observation["account_ref"] is None
    assert observation["availability"] == "unknown"

    assert cli.main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["added"] == 0
    assert cli.main(["--home", str(tmp_path), "brief"]) == 0
    brief = capsys.readouterr().out
    assert "SECRET-PROMPT-CANARY" not in brief
    assert len(brief.rstrip("\n").encode("utf-8")) <= 1024
    state = tmp_path / ".local" / "state" / "quota-lantern"
    for path in state.rglob("*"):
        if path.is_file():
            assert b"SECRET-PROMPT-CANARY" not in path.read_bytes()


def test_explicit_missing_source_is_visible_and_nonzero(cli, tmp_path, capsys):
    source = write_metrics(tmp_path)
    args = ["--home", str(tmp_path), "observe", "--file", str(source)]
    assert cli.main(args) == 0
    capsys.readouterr()
    source.unlink()
    assert cli.main(args) == 1
    output = capsys.readouterr().out
    assert "今次收數未完整成功" in output
    assert "source_read_failed" in output
    assert cli.main([*args, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_feedback_hypothesis_reflect_round_trip(cli, tmp_path, capsys):
    source = write_metrics(tmp_path)
    assert cli.main(["--home", str(tmp_path), "observe", "--file", str(source), "--json"]) == 0
    observation_id = json.loads(capsys.readouterr().out)["status"]["sources"][0]["id"]
    assert cli.main(["--home", str(tmp_path), "feedback", "--observation", observation_id,
                     "--outcome", "observed", "--note", "呢個係研究註記，唔係指令"]) == 0
    assert json.loads(capsys.readouterr().out)["trust"] == "submitted_note_not_instruction"

    assert cli.main(["--home", str(tmp_path), "hypothesis", "add", "--claim", "primary 視窗未必係5h",
                     "--evidence", observation_id, "--metric", "window_minutes",
                     "--lower", "10080", "--upper", "10080", "--next-test", "觀察下一次自然 reset"]) == 0
    note = json.loads(capsys.readouterr().out)["hypotheses"]
    assert note["id"]
    assert cli.main(["--home", str(tmp_path), "reflect", "--hypothesis", note["id"], "--json"]) == 0
    reflection = json.loads(capsys.readouterr().out)
    assert reflection["sample_count"] == 1
    assert cli.main(["--home", str(tmp_path), "hypothesis", "show", note["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["trust"] == "submitted_notes_not_instructions"


def test_failed_install_report_sets_nonzero_exit(cli, tmp_path, monkeypatch, capsys):
    import quota_lantern.install as installation

    monkeypatch.setattr(installation, "install", lambda **kwargs: {"ok": False, "diagnostics": ["collision"]})
    assert cli.main(["--home", str(tmp_path), "install", "--dry-run", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert list(tmp_path.iterdir()) == []


def test_doctor_exposes_recording_pause_without_changing_routing(cli, tmp_path, capsys):
    from quota_lantern.store import Store

    Store(home=tmp_path).write_cache("diagnostics", ["journal_full"])
    assert cli.main(["--home", str(tmp_path), "doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["quota_state"]["recording_paused"] is True
    assert report["quota_state"]["billing_route"] == "unverified"


def test_watch_is_bounded_without_sleeping_in_real_time(cli, monkeypatch, capsys):
    import quota_lantern.research as research

    class Clock:
        at = 100.0

        def now(self):
            return self.at

        def sleep(self, amount):
            self.at += amount

    clock = Clock()
    calls = []
    monkeypatch.setattr(cli, "observe_once", lambda store, home, **kwargs: calls.append(kwargs) or {"added": 0})
    monkeypatch.setattr(research, "reflect", lambda store, **kwargs: {"sample_count": 0})
    assert cli.watch_local(None, Path("/fake"), samples=20, interval=30, duration=31,
                           clock=clock.now, sleep=clock.sleep) == 2
    assert len(calls) == 2
    assert all(call["deadline"] == 131.0 for call in calls)
    assert clock.at == 130.0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["sample"] for line in lines] == [1, 2]


def test_emit_json_rejects_nonfinite_before_output(cli, capsys):
    with pytest.raises(ValueError):
        cli.emit_json({"invalid": float("nan")})
    assert capsys.readouterr().out == ""


def test_real_source_install_wrapper_and_lifecycle_in_fake_home(cli, tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    first_project = tmp_path / "project-a"
    second_project = tmp_path / "project-b"
    first_project.mkdir()
    second_project.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    original = {"model": "leave-routing-alone", "statusLine": {"type": "command", "command": "existing"}}
    settings.write_text(json.dumps(original))
    assert cli.main(["--home", str(home), "install", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"]
    assert not (home / ".local").exists()
    assert cli.main(["--home", str(home), "install"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    wrapper = home / ".local/bin/kingdom-quota"
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "CODEX_HOME": str(home / ".codex")}
    installed = json.loads(settings.read_text())
    assert {k: v for k, v in installed.items() if k != "hooks"} == original

    def command(*args):
        result = subprocess.run([str(wrapper), *args], env=env, cwd=first_project,
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        return result.stdout

    assert json.loads(command("status", "--json"))["sources"] == []

    def hook(event, project):
        payload = {"hook_event_name": event, "session_id": "fixture-session", "cwd": str(project),
                   "prompt": "SECRET-E2E-PROMPT", "transcript_path": "/never/read/this"}
        shell = installed["hooks"][event][-1]["hooks"][0]["command"]
        # 真 source 先測 raw command，唔靠 fail-open wrapper 掩蓋失敗。
        raw = shell.removesuffix(" 2>/dev/null || true")
        result = subprocess.run(["/bin/sh", "-c", raw], input=json.dumps(payload), env=env,
                                cwd=project, capture_output=True, text=True, timeout=3)
        assert result.returncode == 0, result.stderr
        assert "SECRET-E2E-PROMPT" not in result.stdout
        value = json.loads(result.stdout)
        assert not {"decision", "continue", "asyncRewake"}.intersection(value)
        return value

    start = hook("SessionStart", first_project)
    assert start["hookSpecificOutput"]["additionalContext"]
    resumed = hook("SessionStart", first_project)
    assert resumed["hookSpecificOutput"]["additionalContext"]
    write_metrics(home)
    observed = json.loads(command("observe", "--json"))
    assert observed["added"] == 1
    assert hook("Stop", first_project) == {}
    assert json.loads(command("reflect", "--json"))["sample_count"] == 1
    sibling = hook("SessionStart", second_project)
    assert "81" in sibling["hookSpecificOutput"]["additionalContext"]
    subagent = hook("SubagentStart", second_project)
    assert len(subagent["hookSpecificOutput"]["additionalContext"].encode()) <= 512
    assert json.loads(command("doctor", "--json"))["installed"]
    for path in (home / ".local/state/quota-lantern").rglob("*"):
        if path.is_file():
            assert b"SECRET-E2E-PROMPT" not in path.read_bytes()
    assert json.loads(command("uninstall", "--json"))["ok"]
    assert not os.path.lexists(wrapper)
    assert json.loads(settings.read_text()) == original
