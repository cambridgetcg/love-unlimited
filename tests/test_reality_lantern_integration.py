"""真 Store／snapshot／hooks 交界；收據係 synthetic，唔冒認 runner 驗證。"""

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from reality_lantern.core import build_status, keyed_digest
from reality_lantern.hooks import run_hook
from reality_lantern.snapshot import capture, scope_candidates
from reality_lantern.store import Store


def git_env(home):
    return {"PATH": os.defpath, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def make_repo(path):
    path.mkdir()
    subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "init", "-q", str(path)],
                   check=True, capture_output=True, env=git_env(path), timeout=10)
    (path / "program.py").write_text("value = 1\n")
    subprocess.run(["/usr/bin/git", "-C", str(path), "-c", "core.hooksPath=/dev/null",
                    "add", "program.py"], check=True, capture_output=True, env=git_env(path), timeout=10)
    return path


def synthetic_receipt(store, repo):
    key = store.get_key(create=True)
    before = capture(repo, key=key)
    assert before["complete"], before["diagnostics"]
    run = store.start_run(kind="tests", check_id=keyed_digest(key, "check", ["synthetic", before["cwd_id"]]),
                          before=before)
    after = capture(repo, key=key)
    assert after["complete"], after["diagnostics"]
    store.finish_run(run["id"], outcome="exit0", exit_code=0, signal_number=None, after=after)
    return before["scope_id"]


def test_real_file_hook_stales_receipt_and_revert_does_not_revive(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = make_repo(tmp_path / "repo")
    store = Store(home=home)
    scope = synthetic_receipt(store, repo)
    assert build_status(store.read_state(), scope_id=scope)["checks"][0]["validity"] == "match"
    (repo / "program.py").write_text("value = 2\n")
    event = {"session_id": "SESSION-CANARY", "cwd": str(repo), "tool_name": "Edit",
             "tool_input": {"file_path": str(repo / "program.py"), "new_string": "CONTENT-CANARY"}}
    result = run_hook("PostToolUse", event, home=home)
    assert "stale" in result["hookSpecificOutput"]["additionalContext"]
    assert build_status(store.read_state(), scope_id=scope)["checks"][0]["validity"] == "stale"
    (repo / "program.py").write_text("value = 1\n")
    snapshot = capture(repo, key=store.get_key())
    generation = store.read_state()["scopes"][scope]["generation"]
    store.observe(snapshot, expected_generation=generation)
    assert build_status(store.read_state(), scope_id=scope)["checks"][0]["validity"] == "stale"
    assert "CANARY" not in json.dumps(store.read_state())


def test_actual_pre_bash_then_completion_is_match_not_immediately_invalidated(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = make_repo(tmp_path / "repo")
    store = Store(home=home)
    scope = synthetic_receipt(store, repo)
    event = {"session_id": "s", "cwd": str(repo), "tool_name": "Bash",
             "tool_input": {"command": "RAW-COMMAND-CANARY"}}
    assert run_hook("PreToolUse", event, home=home) == {}
    assert build_status(store.read_state(), scope_id=scope)["checks"][0]["validity"] == "unknown"
    synthetic_receipt(store, repo)
    result = run_hook("PostToolUse", event, home=home)
    assert "match@" in result["hookSpecificOutput"]["additionalContext"]
    assert build_status(store.read_state(), scope_id=scope)["checks"][0]["validity"] == "match"
    assert "CANARY" not in json.dumps(store.read_state())


def test_real_subagent_cache_only_never_invokes_capture_or_writes(tmp_path, monkeypatch):
    import reality_lantern.snapshot as snapshots

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = make_repo(tmp_path / "repo")
    store = Store(home=home)
    synthetic_receipt(store, repo)
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
              for path in store.state_dir.rglob("*") if path.is_file()}
    monkeypatch.setattr(snapshots, "capture", lambda *a, **kw: pytest.fail("SubagentStart 唔准 capture"))
    result = run_hook("SubagentStart", {"cwd": str(repo), "session_id": "s"}, home=home)
    assert "match@" in result["hookSpecificOutput"]["additionalContext"]
    after = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
             for path in store.state_dir.rglob("*") if path.is_file()}
    assert before == after
    assert len(result["hookSpecificOutput"]["additionalContext"].encode()) <= 512


@pytest.mark.parametrize("newer_run", [False, True])
@pytest.mark.parametrize("generation_changed", [False, True])
def test_delayed_changed_snapshot_stales_only_receipts_that_existed_then(tmp_path, newer_run, generation_changed):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = make_repo(tmp_path / "repo")
    store = Store(home=home)
    scope = synthetic_receipt(store, repo)
    old_id = build_status(store.read_state(), scope_id=scope)["checks"][0]["run_id"]
    (repo / "program.py").write_text("value = 2\n")
    captured_generation = store.read_state()["scopes"][scope]["generation"]
    changed = capture(repo, key=store.get_key())
    assert changed["complete"]
    if generation_changed:
        store.invalidate(scope, changed=False)
    (repo / "program.py").write_text("value = 1\n")
    restored = capture(repo, key=store.get_key())
    generation = store.read_state()["scopes"][scope]["generation"]
    store.observe(restored, expected_generation=generation)
    if newer_run:
        synthetic_receipt(store, repo)
    # 遲到嘅 B 唔可以倒退最新 observation，但仍然係觀察過改動嘅證據。
    store.observe(changed, expected_generation=captured_generation)
    state = store.read_state()
    assert state["projections"][old_id]["validity"] == "stale"
    latest = build_status(state, scope_id=scope)["checks"][0]
    assert latest["validity"] == ("match" if newer_run else "stale")
    assert state["scopes"][scope]["observation"]["fingerprint"] == restored["fingerprint"]


@pytest.mark.parametrize("generation_changed", [False, True])
def test_delayed_completion_keeps_historical_change_even_after_invalidation(tmp_path, generation_changed):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = make_repo(tmp_path / "repo")
    store = Store(home=home)
    scope = synthetic_receipt(store, repo)
    old_id = build_status(store.read_state(), scope_id=scope)["checks"][0]["run_id"]
    key = store.get_key()
    run = store.start_run(kind="build", check_id=keyed_digest(key, "check", "delayed-build"),
                          before=capture(repo, key=key))
    if generation_changed:
        store.invalidate(scope, changed=False)
    subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
                    "from pathlib import Path; Path('program.py').write_text('value = 2\\n')"],
                   cwd=repo, env=git_env(home), check=True, timeout=10)
    changed = capture(repo, key=key)
    assert changed["complete"]
    (repo / "program.py").write_text("value = 1\n")
    restored = capture(repo, key=key)
    store.observe(restored, expected_generation=store.read_state()["scopes"][scope]["generation"])
    synthetic_receipt(store, repo)
    fresh_id = build_status(store.read_state(), scope_id=scope)["checks"][0]["run_id"]
    store.finish_run(run["id"], outcome="exit0", exit_code=0, signal_number=None, after=changed)
    state = store.read_state()
    assert state["completions"][run["id"]]["after"] == changed
    assert state["projections"][old_id]["validity"] == "stale"
    assert state["projections"][fresh_id]["validity"] == "match"
    assert state["scopes"][scope]["observation"]["fingerprint"] == restored["fingerprint"]
    store.observe(capture(repo, key=key), expected_generation=state["scopes"][scope]["generation"])
    assert store.read_state()["projections"][old_id]["validity"] == "stale"


@pytest.mark.parametrize("nested_kind", ["repo", "worktree"])
def test_nested_unknown_repo_does_not_borrow_parent_receipt(tmp_path, nested_kind):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    parent = make_repo(tmp_path / "parent")
    if nested_kind == "worktree":
        subprocess.run(["/usr/bin/git", "-C", str(parent), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "synthetic fixture"],
                       check=True, capture_output=True, env=git_env(home), timeout=10)
    store = Store(home=home)
    scope = synthetic_receipt(store, parent)
    if nested_kind == "repo":
        nested = make_repo(parent / "nested")
    else:
        nested = parent / ".claude/worktrees/new"
        nested.parent.mkdir(parents=True)
        subprocess.run(["/usr/bin/git", "-C", str(parent), "-c", "core.hooksPath=/dev/null",
                        "worktree", "add", "-qb", "fixture-worktree", str(nested)],
                       check=True, capture_output=True, env=git_env(home), timeout=10)
    candidates = scope_candidates(nested, key=store.get_key())
    assert scope not in candidates, "新 nested worktree 唔應該借 parent 嘅 cached match"
    result = run_hook("SubagentStart", {"cwd": str(nested), "session_id": "s"}, home=home)
    assert "match@" not in result["hookSpecificOutput"]["additionalContext"]


def test_quota_partial_uninstall_cannot_report_full_install_after_restoring_leftovers(tmp_path):
    from quota_lantern import install as quota_install
    from quota_lantern.store import Store as QuotaStore

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = TOOLS.parent
    assert quota_install.install(home=home, repo_root=repo, dry_run=False)["ok"]
    targets = quota_install._targets(home)
    original = {key: quota_install._read(home, path) for key, path in targets.items()}
    targets["wrapper"].write_bytes(original["wrapper"].data + b"# synthetic edit\n")
    for name in ("claude_skill", "codex_skill"):
        targets[name].unlink()
        targets[name].symlink_to(repo / "tools")
    targets["agents"].write_bytes(original["agents"].data.replace(b"soft / advisory", b"synthetic change"))
    settings = json.loads(original["settings"].data)
    settings["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 99
    targets["settings"].write_text(json.dumps(settings))
    assert quota_install.uninstall(home=home, repo_root=repo)["ok"] is False
    manifest = QuotaStore(home=home).read_cache("installation")
    assert set(manifest["artifacts"]["settings"]["hashes"]) == {"SessionStart"}
    for name in ("wrapper", "agents"):
        targets[name].write_bytes(original[name].data)
    for name in ("claude_skill", "codex_skill"):
        targets[name].unlink()
        targets[name].symlink_to(original[name].data.decode())
    settings = json.loads(targets["settings"].read_bytes())
    settings["hooks"]["SessionStart"] = json.loads(original["settings"].data)["hooks"]["SessionStart"]
    targets["settings"].write_text(json.dumps(settings))
    assert quota_install.doctor(home=home, repo_root=repo)["installed"] is False
    result = quota_install.install(home=home, repo_root=repo, dry_run=True)
    assert result["ok"] is False and "install_incomplete_manifest" in result["diagnostics"]
    assert quota_install.uninstall(home=home, repo_root=repo)["ok"]
