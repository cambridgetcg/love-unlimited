"""Fake-HOME 真入口 E2E：原命令、兩燈、收據同失效提示串埋。"""

from pathlib import Path
import json
import os
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
QUOTA = REPO / "tools/kingdom-quota.py"
REALITY = REPO / "tools/kingdom-reality.py"


def run(command, *, home, cwd, data=None):
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(command, input=data, text=True, capture_output=True,
                          cwd=cwd, env=env, timeout=30)


def cli(source, *arguments, home, cwd):
    return run([sys.executable, "-I", "-S", "-B", str(source),
                "--home", str(home), *map(str, arguments)], home=home, cwd=cwd)


def successful_json(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def hook(home, cwd, event, **extra):
    settings = json.loads((home / ".claude/settings.json").read_text())
    commands = [entry["command"] for group in settings["hooks"][event]
                for entry in group["hooks"] if "reality-lantern.py" in entry.get("command", "")]
    assert len(commands) == 1
    payload = {"hook_event_name": event, "cwd": str(cwd), "session_id": "e2e-session", **extra}
    # 呢條 command 係 fake HOME 真 installer 剛產生，唔執行外來設定。
    result = run(["/bin/sh", "-c", commands[0]], home=home, cwd=cwd, data=json.dumps(payload))
    assert not result.stderr
    output = successful_json(result)
    assert not any(key in output for key in ("decision", "continue", "stopReason"))
    return output.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_two_lanterns_actual_check_edit_failure_recheck_and_uninstall(tmp_path):
    home, app, other = (tmp_path / name for name in ("home", "app", "other"))
    for path in (home, app, other):
        path.mkdir(mode=0o700)
    (home / ".claude").mkdir(mode=0o700)
    original = {"model": "original-model", "permissions": {"defaultMode": "default"},
                "statusLine": {"type": "command", "command": "original-status"}}
    (home / ".claude/settings.json").write_text(json.dumps(original))
    assert run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "init", "-q", str(app)],
               home=home, cwd=app).returncode == 0
    (app / "program.py").write_text("value = 1\n")
    test_file = app / "test_program.py"
    test_file.write_text("from pathlib import Path\ndef test_value():\n"
                         "    assert Path('program.py').read_text() == 'value = 1\\n'\n")
    assert run(["/usr/bin/git", "-C", str(app), "-c", "core.hooksPath=/dev/null", "add", "."],
               home=home, cwd=app).returncode == 0
    assert successful_json(cli(QUOTA, "install", "--json", home=home, cwd=app))["ok"]
    assert successful_json(cli(REALITY, "install", "--json", home=home, cwd=app))["ok"]
    wrapper = home / ".local/bin/kingdom-reality"
    check = [str(wrapper), "--home", str(home), "check", "--kind", "tests", "--cwd", str(app),
             "--", sys.executable, "-B", "-W", "error", "-m", "pytest", "-q",
             "test_program.py", "-p", "no:cacheprovider"]
    first = run(check, home=home, cwd=app)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "1 passed" in first.stdout
    status = successful_json(cli(REALITY, "status", "--cwd", app, "--json", home=home, cwd=app))
    assert [(item["outcome"], item["validity"]) for item in status["checks"]] == [("exit0", "match")]
    installed = json.loads((home / ".claude/settings.json").read_text())
    pair = [entry["command"] for group in installed["hooks"]["SessionStart"] for entry in group["hooks"]]
    assert len(pair) == 2
    started = time.perf_counter()
    for command in pair:
        result = run(["/bin/sh", "-c", command], home=home, cwd=app,
                     data=json.dumps({"hook_event_name": "SessionStart", "cwd": str(app),
                                      "session_id": "e2e-paired"}))
        assert successful_json(result)["hookSpecificOutput"]["additionalContext"]
    elapsed = time.perf_counter() - started
    assert elapsed < 3.5
    print(f"paired_session_start_seconds={elapsed:.6f}")
    assert "match@" in hook(home, app, "SessionStart")
    assert "match@" in hook(home, app, "SubagentStart")

    assert hook(home, app, "PreToolUse", tool_name="Bash", tool_input={"command": "ordinary command"}) == ""
    status = successful_json(cli(REALITY, "status", "--cwd", app, "--json", home=home, cwd=app))
    assert status["checks"][0]["validity"] == "unknown"
    ordinary = hook(home, app, "PostToolUse", tool_name="Bash",
                    tool_response={"stdout": "pretend passed", "exit_code": 0, "backgroundTaskId": "synthetic"})
    assert "match@" not in ordinary
    observed = successful_json(cli(REALITY, "observe", "--cwd", app, "--json", home=home, cwd=app))
    assert observed["status"]["checks"][0]["validity"] == "match"

    (app / "program.py").write_text("value = 2\n")
    assert "stale" in hook(home, app, "PostToolUse", tool_name="Edit",
                           tool_input={"file_path": str(app / "program.py")})
    hook(home, app, "PreToolUse", tool_name="Bash")
    failed = run(check, home=home, cwd=app)
    assert failed.returncode != 0 and "1 failed" in failed.stdout
    hook(home, app, "PostToolUseFailure", tool_name="Bash", error="synthetic child failure")
    status = successful_json(cli(REALITY, "status", "--cwd", app, "--json", home=home, cwd=app))
    assert [(item["outcome"], item["validity"]) for item in status["checks"]] == [("nonzero", "match")]

    test_file.write_text(test_file.read_text().replace("value = 1", "value = 2"))
    hook(home, app, "PostToolUse", tool_name="Edit", tool_input={"file_path": str(test_file)})
    hook(home, app, "PreToolUse", tool_name="Bash")
    final_check = run(check, home=home, cwd=app)
    assert final_check.returncode == 0 and "1 passed" in final_check.stdout
    assert "match@" in hook(home, app, "PostToolUse", tool_name="Bash")
    status = successful_json(cli(REALITY, "status", "--cwd", app, "--json", home=home, cwd=app))
    assert status["checks"][0]["outcome"] == "exit0"
    assert status["checks"][0]["validity"] == "match"
    assert status["remote"] == "unverified"
    assert successful_json(cli(REALITY, "status", "--cwd", other, "--json", home=home, cwd=other))["checks"] == []
    assert successful_json(cli(REALITY, "doctor", "--json", home=home, cwd=app))["installed"]
    assert successful_json(cli(QUOTA, "doctor", "--json", home=home, cwd=app))["installed"]
    receipt = home / ".local/state/reality-lantern/state.json"
    preserved = receipt.read_bytes()
    assert str(app).encode() not in preserved
    assert b"pretend passed" not in preserved and b"e2e-session" not in preserved
    assert successful_json(cli(REALITY, "uninstall", "--json", home=home, cwd=app))["ok"]
    assert receipt.read_bytes() == preserved
    assert successful_json(cli(QUOTA, "doctor", "--json", home=home, cwd=app))["installed"]
    assert not wrapper.exists()
    assert successful_json(cli(QUOTA, "uninstall", "--json", home=home, cwd=app))["ok"]
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert {key: settings[key] for key in original} == original
