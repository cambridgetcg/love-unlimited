"""Installer 只喺 fake HOME 操作；fixtures 唔會啟動真 harness。"""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from quota_lantern import install as installer
from quota_lantern.store import Store


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home = tmp_path / "fake home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    repo = tmp_path / "repo 'quoted' $(touch SHOULD_NOT_EXIST)"
    package = repo / "tools/quota_lantern"
    (package / "skill").mkdir(parents=True)
    (repo / "tools/hooks").mkdir()
    (package / "__init__.py").write_text("")
    script = (
        "import json,sys\n"
        "print(json.dumps({'args':sys.argv[1:], 'isolated':sys.flags.isolated,"
        "'no_site':sys.flags.no_site, 'no_bytecode':sys.dont_write_bytecode}))\n"
    )
    (repo / "tools/kingdom-quota.py").write_text(script)
    (package / "skill/SKILL.md").write_text("---\nname: quota-lantern\ndescription: fixture\n---\n")
    (repo / "tools/hooks/quota-lantern.py").write_text(script)
    return home, repo


def _settings(home, value):
    path = home / ".claude/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=4) + "\n")
    return path


def _agents(home, data):
    path = home / ".codex/AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _install(desk):
    home, repo = desk
    result = installer.install(home=home, repo_root=repo, dry_run=False)
    assert result["ok"], result
    return result


def _manifest(home):
    return Store(home=home).read_cache("installation")


def test_dry_run_and_doctor_do_not_create_files(desk):
    home, repo = desk
    assert installer.install(home=home, repo_root=repo)["ok"]
    report = installer.doctor(home=home, repo_root=repo)
    assert not report["installed"]
    assert list(home.iterdir()) == []
    assert installer.uninstall(home=home, repo_root=repo)["ok"]
    assert list(home.iterdir()) == []


def test_merge_preserves_all_foreign_keys_and_four_command_hooks(desk):
    home, repo = desk
    original = {
        "statusLine": {"type": "command", "command": "existing-status"},
        "model": "chosen-model", "permissions": {"allow": ["Read"], "deny": ["Write"]},
        "env": {"PRIVATE_CANARY": "DO_NOT_COPY_SECRET"},
        "unknownFutureKey": {"nested": [1, "kept"]},
        "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
            {"type": "command", "command": "other-hook", "timeout": 19}]}],
            "Stop": [], "Notification": [{"hooks": [{"type": "prompt", "prompt": "existing"}]}]},
    }
    path = _settings(home, original)
    path.chmod(0o640)
    agents = _agents(home, "# 我原本嘅指引\n唔好改。".encode())
    before = agents.read_bytes()
    _install(desk)
    current = json.loads(path.read_text())
    for key in original.keys() - {"hooks"}:
        assert current[key] == original[key]
    assert current["hooks"]["Notification"] == original["hooks"]["Notification"]
    for event in installer.EVENTS:
        assert current["hooks"][event][:-1] == original["hooks"].get(event, [])
        added = current["hooks"][event][-1]
        assert set(added) == {"hooks"}
        assert len(added["hooks"]) == 1
        assert added["hooks"][0]["type"] == "command"
        assert added["hooks"][0]["timeout"] == 2
        assert set(added["hooks"][0]) == {"type", "command", "timeout"}
    assert path.stat().st_mode & 0o777 == 0o640
    assert agents.read_bytes().startswith(before + b"\n\n<!--")
    manifest = _manifest(home)
    assert manifest["artifacts"]["agents"]["prefix_sha256"] == hashlib.sha256(before).hexdigest()
    serialized = json.dumps(manifest)
    assert "DO_NOT_COPY_SECRET" not in serialized
    assert "PRIVATE_CANARY" not in serialized
    assert "existing-status" not in serialized
    assert "permissions" not in serialized
    state = home / ".local/state/quota-lantern"
    assert all(b"DO_NOT_COPY_SECRET" not in p.read_bytes() for p in state.rglob("*") if p.is_file())
    assert installer.doctor(home=home, repo_root=repo)["installed"]
    assert installer.doctor(home=home, repo_root=repo)["activation"] == "unverified"


def test_idempotent_install_never_rewrites_targets_or_manifest(desk):
    home, repo = desk
    _install(desk)
    paths = list(installer._targets(home).values())
    before = {str(p): p.lstat().st_mtime_ns for p in paths}
    manifest = copy.deepcopy(_manifest(home))
    report = _install(desk)
    assert report["changed"] == []
    assert _manifest(home) == manifest
    assert {str(p): p.lstat().st_mtime_ns for p in paths} == before
    assert installer.install(home=home, repo_root=repo)["changed"] == []


@pytest.mark.parametrize("original", [b"", b"# Mine", b"# Mine\n", b"# Mine\n\n", b"# Mine\r\n", b"```text\nhello\n```\n"])
def test_agents_append_and_uninstall_preserve_exact_original(desk, original):
    home, repo = desk
    path = _agents(home, original)
    _install(desk)
    assert installer.uninstall(home=home, repo_root=repo)["ok"]
    assert path.read_bytes() == original


def test_uninstall_restores_settings_semantics_and_removes_only_added_items(desk):
    home, repo = desk
    original = {"permissions": {"ask": ["Bash"]}, "hooks": {"Stop": []}, "model": "stay"}
    path = _settings(home, original)
    _install(desk)
    report = installer.uninstall(home=home, repo_root=repo)
    assert report["ok"], report
    assert json.loads(path.read_text()) == original
    for key in ("wrapper", "claude_skill", "codex_skill", "agents"):
        assert not os.path.lexists(installer._targets(home)[key])
    assert _manifest(home)["artifacts"] == {}
    assert installer.uninstall(home=home, repo_root=repo)["changed"] == []
    _install(desk)


def test_uninstall_preserves_new_foreign_settings_and_agents_content(desk):
    home, repo = desk
    path = _settings(home, {"model": "original"})
    agents = _agents(home, b"mine\n")
    _install(desk)
    value = json.loads(path.read_text())
    value["model"] = "new-choice"
    value["permissions"] = {"deny": ["new-choice"]}
    value["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "new-foreign-hook"}]})
    path.write_text(json.dumps(value))
    agents.write_bytes(b"new header\n" + agents.read_bytes() + b"new footer\n")
    assert installer.uninstall(home=home, repo_root=repo)["ok"]
    assert json.loads(path.read_text()) == {
        "model": "new-choice", "permissions": {"deny": ["new-choice"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "new-foreign-hook"}]}]},
    }
    assert agents.read_bytes() == b"new header\nmine\nnew footer\n"


@pytest.mark.parametrize("target", ["wrapper", "claude_skill", "codex_skill", "agents", "settings"])
def test_foreign_target_changes_are_never_overwritten(desk, target):
    home, repo = desk
    _install(desk)
    path = installer._targets(home)[target]
    if target.endswith("skill"):
        path.unlink()
        path.symlink_to(home / "foreign-missing-skill")
        saved = os.readlink(path)
    elif target == "settings":
        value = json.loads(path.read_text())
        value["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 9
        path.write_text(json.dumps(value))
        saved = path.read_bytes()
    elif target == "agents":
        path.write_bytes(path.read_bytes().replace(b"soft / advisory", b"foreign edit"))
        saved = path.read_bytes()
    else:
        path.write_text("foreign wrapper\n")
        saved = path.read_bytes()
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert not installer.doctor(home=home, repo_root=repo)["installed"]
    report = installer.uninstall(home=home, repo_root=repo)
    assert not report["ok"]
    if target.endswith("skill"):
        assert os.readlink(path) == saved
    elif target == "settings":
        current = json.loads(path.read_text())
        assert current["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 9
        assert set(current["hooks"]) == {"Stop"}
    else:
        assert path.read_bytes() == saved


def test_preexisting_same_source_skill_link_is_shared_not_claimed(desk):
    home, repo = desk
    for key in ("claude_skill", "codex_skill"):
        path = installer._targets(home)[key]
        path.parent.mkdir(parents=True)
        path.symlink_to(repo / "tools/quota_lantern/skill")
    _install(desk)
    assert not _manifest(home)["artifacts"]["claude_skill"]["owned"]
    assert installer.uninstall(home=home, repo_root=repo)["ok"]
    assert (home / ".claude/skills/quota-lantern").is_symlink()
    assert (home / ".codex/skills/quota-lantern").is_symlink()


@pytest.mark.parametrize("target", ["wrapper", "claude_skill", "codex_skill"])
def test_existing_conflicting_objects_refused_before_any_write(desk, target):
    home, repo = desk
    path = installer._targets(home)[target]
    path.parent.mkdir(parents=True)
    path.write_text("existing object")
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    report = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not report["ok"]
    assert path.read_text() == "existing object"
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*")) == before


@pytest.mark.parametrize("target", [".claude/settings.json", ".codex/AGENTS.md", ".claude/skills", ".local/bin", ".local/state"])
def test_symlink_targets_and_parents_are_refused_without_following(desk, tmp_path, target):
    home, repo = desk
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("DO_NOT_READ_OR_WRITE")
    path = home / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(sentinel if target.endswith((".json", ".md")) else outside)
    report = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not report["ok"]
    assert sentinel.read_text() == "DO_NOT_READ_OR_WRITE"
    assert list(outside.iterdir()) == [sentinel]
    assert path.is_symlink()


@pytest.mark.parametrize("invalid", [b"{", b"[]", b'{"hooks":null}', b'{"hooks":{"Stop":{}}}', b'{"hooks":{"Stop":[{}]}}', b'{"model":1,"model":2}', b'{"value":NaN}'])
def test_invalid_settings_remain_untouched(desk, invalid):
    home, repo = desk
    path = _settings(home, {})
    path.write_bytes(invalid)
    report = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not report["ok"]
    assert path.read_bytes() == invalid
    assert not (home / ".local").exists()


@pytest.mark.parametrize("invalid", [b"```python\nunclosed", b"~~~\nunclosed", b"<!-- unclosed", b"---\nunclosed: frontmatter", b"<!-- quota-lantern:begin -->\nforeign", b"\xff"])
def test_unappendable_agents_are_refused(desk, invalid):
    home, repo = desk
    path = _agents(home, invalid)
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert path.read_bytes() == invalid
    assert not (home / ".local").exists()


def test_missing_source_and_symlink_source_refused(desk):
    home, repo = desk
    source = repo / "tools/hooks/quota-lantern.py"
    source.unlink()
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    source.symlink_to(repo / "tools/kingdom-quota.py")
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert list(home.iterdir()) == []


def test_partial_failure_rolls_back_exact_originals_without_disk_backup(desk, monkeypatch):
    home, repo = desk
    path = _settings(home, {"env": {"CANARY": "NOT_A_BACKUP"}})
    agents = _agents(home, b"mine without newline")
    settings_before, agents_before = path.read_bytes(), agents.read_bytes()
    real_put = installer._put

    def fail_settings(home, path, before, after, directories):
        if path.name == "settings.json":
            raise OSError("SECRET_EXCEPTION_DO_NOT_PRINT")
        return real_put(home, path, before, after, directories)

    monkeypatch.setattr(installer, "_put", fail_settings)
    report = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not report["ok"]
    assert report["changed"] == []
    assert "SECRET_EXCEPTION" not in json.dumps(report)
    assert path.read_bytes() == settings_before
    assert agents.read_bytes() == agents_before
    assert not (home / ".local/bin/kingdom-quota").exists()
    assert not (home / ".claude/skills/quota-lantern").is_symlink()
    assert _manifest(home) is None


def test_failed_manifest_write_rolls_back_installed_objects(desk, monkeypatch):
    home, repo = desk
    settings = _settings(home, {"env": {"CANARY": "NOT_A_BACKUP"}})
    original = settings.read_bytes()

    def fail_commit(self, name, value, **kwargs):
        assert name == "installation"
        assert set(value["artifacts"]) == set(installer._targets(home))
        assert all(os.path.lexists(p) for p in installer._targets(home).values())
        raise OSError("private failure details")

    monkeypatch.setattr(Store, "write_cache", fail_commit)
    report = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not report["ok"]
    assert report["changed"] == []
    assert report["diagnostics"] == ["install_failed"]
    assert settings.read_bytes() == original
    assert _manifest(home) is None
    assert all(not os.path.lexists(p) for key, p in installer._targets(home).items() if key != "settings")
    # 鎖 inode 同已保留 nonce 唔跟 host rollback 刪除；兩者都唔係 settings backup。
    state = home / ".local/state/quota-lantern"
    assert {p.name for p in state.iterdir()} == {
        installer.INSTALL_LOCK_NAME, "installation-nonces.json",
    }
    assert (state / installer.INSTALL_LOCK_NAME).read_bytes() == b""
    history = state / "installation-nonces.json"
    value = json.loads(history.read_bytes())
    assert set(value) == {"schema", "nonces"}
    assert value["schema"] == "kingdom.lantern.installation-nonces/1"
    assert len(value["nonces"]) == 1
    assert str(UUID(value["nonces"][0])) == value["nonces"][0]
    assert history.stat().st_mode & 0o777 == 0o600
    assert history.stat().st_uid == os.geteuid()
    assert history.stat().st_nlink == 1
    assert b"NOT_A_BACKUP" not in history.read_bytes()
    assert report["nonce_reservation"] == "reserved"


def test_rollback_does_not_overwrite_concurrent_foreign_change(desk, monkeypatch):
    home, repo = desk
    real_put = installer._put
    wrapper = home / ".local/bin/kingdom-quota"

    def change_then_fail(home, path, before, after, directories):
        if path.name == "settings.json":
            wrapper.write_text("foreign concurrent edit\n")
            raise OSError("stop")
        return real_put(home, path, before, after, directories)

    monkeypatch.setattr(installer, "_put", change_then_fail)
    report = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not report["ok"]
    assert "install_rollback_incomplete" in report["diagnostics"]
    assert wrapper.read_text() == "foreign concurrent edit\n"


def test_hooks_and_wrapper_quote_paths_and_use_isolated_python(desk, tmp_path):
    home, repo = desk
    _install(desk)
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "sitecustomize.py").write_text("raise RuntimeError('must not import')")
    (attacker / "quota_lantern.py").write_text("raise RuntimeError('must not import')")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(attacker)}
    result = subprocess.run([str(home / ".local/bin/kingdom-quota"), "status", "a 'quoted' value"],
                            capture_output=True, text=True, timeout=5, cwd=attacker, env=env)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value == {"args": ["status", "a 'quoted' value"], "isolated": 1, "no_site": 1, "no_bytecode": True}
    settings = json.loads((home / ".claude/settings.json").read_text())
    for event in installer.EVENTS:
        command = settings["hooks"][event][0]["hooks"][0]["command"]
        argv = shlex.split(command)
        assert argv[1:4] == ["-I", "-S", "-B"]
        assert argv[4:6] == [str(repo / "tools/hooks/quota-lantern.py"), event]
        raw = command.removesuffix(" 2>/dev/null || true")
        for candidate in (raw, command):
            result = subprocess.run(["/bin/sh", "-c", candidate], input="{}", capture_output=True,
                                    text=True, timeout=5, cwd=attacker, env=env)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {
                "args": [event], "isolated": 1, "no_site": 1, "no_bytecode": True,
            }
    assert not (attacker / "SHOULD_NOT_EXIST").exists()
    assert not list(repo.rglob("__pycache__"))


def test_doctor_never_reads_auth_or_conversations(desk, monkeypatch):
    home, repo = desk
    _install(desk)
    real_read = installer._read
    allowed = set(installer._targets(home).values())

    def allow_only_targets(home, path):
        assert path in allowed
        return real_read(home, path)

    monkeypatch.setattr(installer, "_read", allow_only_targets)
    assert installer.doctor(home=home, repo_root=repo)["installed"]


def test_no_matching_owned_hooks_does_not_even_reformat_settings(desk):
    home, repo = desk
    _install(desk)
    path = home / ".claude/settings.json"
    value = json.loads(path.read_text())
    for event in installer.EVENTS:
        value["hooks"][event][0]["hooks"][0]["timeout"] = 7
    path.write_text(json.dumps(value, indent=5))
    before = path.read_bytes()
    assert not installer.uninstall(home=home, repo_root=repo)["ok"]
    assert path.read_bytes() == before


def test_non_executable_wrapper_is_not_reported_healthy(desk):
    home, repo = desk
    _install(desk)
    path = home / ".local/bin/kingdom-quota"
    path.chmod(0o600)
    assert not installer.doctor(home=home, repo_root=repo)["installed"]
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_oversized_addition_cannot_replace_original_without_rollback(desk):
    home, repo = desk
    path = _agents(home, b"x" * installer.MAX_BYTES)
    before = path.read_bytes()
    result = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not result["ok"]
    assert path.read_bytes() == before
    assert not (home / ".local/bin/kingdom-quota").exists()
    assert not (home / ".claude/skills/quota-lantern").is_symlink()


def test_other_store_writers_preserved_during_install(desk):
    home, repo = desk
    Store(home=home).write_cache("receipts", {"count": 0})

    def record(_):
        Store(home=home).mutate_cache("receipts", lambda old: {"count": old["count"] + 1})

    with ThreadPoolExecutor(max_workers=5) as pool:
        writes = [pool.submit(record, index) for index in range(16)]
        install_result = pool.submit(installer.install, home=home, repo_root=repo, dry_run=False)
        for future in writes:
            future.result(timeout=10)
        assert install_result.result(timeout=10)["ok"]
    assert Store(home=home).read_cache("receipts") == {"count": 16}
    assert installer.doctor(home=home, repo_root=repo)["installed"]


def test_uninstall_failed_manifest_commit_restores_removed_objects(desk, monkeypatch):
    home, repo = desk
    _install(desk)
    before = {key: installer._read(home, path) for key, path in installer._targets(home).items()}
    previous = copy.deepcopy(_manifest(home))

    def fail_commit(self, name, value, **kwargs):
        assert name == "installation"
        assert value["artifacts"] == {}
        assert all(not os.path.lexists(p) for p in installer._targets(home).values())
        raise OSError("commit failed")

    monkeypatch.setattr(Store, "write_cache", fail_commit)
    result = installer.uninstall(home=home, repo_root=repo)
    assert not result["ok"]
    assert result["changed"] == []
    for key, path in installer._targets(home).items():
        current = installer._read(home, path)
        assert (current.kind, current.data, current.mode) == (before[key].kind, before[key].data, before[key].mode)
    assert _manifest(home) == previous


@pytest.mark.parametrize("action", ["install", "uninstall"])
@pytest.mark.parametrize("readback_fails", [False, True])
def test_published_manifest_failure_never_rolls_back_host(desk, monkeypatch, action, readback_fails):
    home, repo = desk
    if action == "uninstall":
        _install(desk)
    real_write, real_read = Store.write_cache, Store.read_cache
    published = False

    def fail_after_publish(self, name, value, **kwargs):
        nonlocal published
        real_write(self, name, value, **kwargs)
        if name == "installation":
            published = True
            raise OSError("PRIVATE_POST_REPLACE_FAILURE")

    def readback(self, name, *args, **kwargs):
        if published and readback_fails and name == "installation":
            raise OSError("PRIVATE_READBACK_FAILURE")
        return real_read(self, name, *args, **kwargs)

    monkeypatch.setattr(Store, "write_cache", fail_after_publish)
    monkeypatch.setattr(Store, "read_cache", readback)
    kwargs = {"dry_run": False} if action == "install" else {}
    result = getattr(installer, action)(home=home, repo_root=repo, **kwargs)
    assert published
    assert not result["ok"]
    assert set(result["changed"]) == set(installer._targets(home))
    assert result["diagnostics"] == [
        "install_manifest_unverified" if readback_fails else "install_manifest_durability_unverified"]
    manifest = real_read(Store(home=home), "installation")
    for key, path in installer._targets(home).items():
        obj = installer._read(home, path)
        if action == "install":
            assert installer._matches(key, obj, manifest["artifacts"][key])
        else:
            assert obj is None
            assert manifest["artifacts"] == {}


@pytest.mark.parametrize("action", ["install", "uninstall"])
@pytest.mark.parametrize("fail_commit", [False, True])
def test_host_io_and_rollback_leave_common_store_lock_free(desk, monkeypatch, action, fail_commit):
    home, repo = desk
    if action == "uninstall":
        _install(desk)
    store = Store(home=home)
    store.write_cache("receipts", {"count": 0})
    calls = {"_read": 0, "_read_at": 0, "_put": 0}

    def guard(name):
        original = getattr(installer, name)

        def checked(*args, **kwargs):
            # 真正攞 shared lock 寫 cache；唔係只靠 mock 話「冇鎖」。
            calls[name] += 1
            store.write_cache("receipts", {"count": sum(calls.values())},
                              deadline=time.monotonic() + 0.2)
            return original(*args, **kwargs)

        return checked

    for name in calls:
        monkeypatch.setattr(installer, name, guard(name))
    real_write = Store.write_cache

    def commit(self, name, value, **kwargs):
        if fail_commit and name == "installation":
            raise OSError("private commit failure")
        return real_write(self, name, value, **kwargs)

    def forbid_callback(*args, **kwargs):
        pytest.fail("installer 唔應該用 Store.mutate_cache 做 transaction")

    monkeypatch.setattr(Store, "write_cache", commit)
    monkeypatch.setattr(Store, "mutate_cache", forbid_callback)
    kwargs = {"dry_run": False} if action == "install" else {}
    result = getattr(installer, action)(home=home, repo_root=repo, **kwargs)
    assert result["ok"] is not fail_commit, result
    assert all(count > 0 for count in calls.values())
    assert store.read_cache("receipts") == {"count": sum(calls.values())}
    if fail_commit:
        assert result["diagnostics"] == [action + "_failed"]
        assert result["changed"] == []
        assert calls["_put"] == 10


# Child 只 import 呢個 checkout；HOME、repo、host targets 全部係 fixture。
_PROCESS = r"""
import json, sys
from pathlib import Path
from contextlib import contextmanager
sys.path.insert(0, sys.argv[1])
from quota_lantern import install as installer
from quota_lantern.store import Store
home, repo = Path(sys.argv[2]), Path(sys.argv[3])
action, role, fail_commit = sys.argv[4:7]
failed = False
paused = False
real_put = installer._put
real_write = Store.write_cache
real_lock = installer._installation_lock

def put(*args, **kwargs):
    global paused
    value = real_put(*args, **kwargs)
    if role == "first" and not paused and (fail_commit == "False" or failed):
        paused = True
        print("paused", flush=True)
        if not sys.stdin.readline():
            raise RuntimeError("test gate closed")
    return value

def write(self, name, value, **kwargs):
    global failed
    if fail_commit == "True" and name == "installation":
        failed = True
        raise OSError("private commit failure")
    return real_write(self, name, value, **kwargs)

@contextmanager
def lock(*args, **kwargs):
    if role == "second":
        print("waiting", flush=True)
    with real_lock(*args, **kwargs) as held:
        if role == "second":
            print("acquired", flush=True)
        yield held

installer._put = put
installer._installation_lock = lock
Store.write_cache = write
kwargs = {"dry_run": False} if action == "install" else {}
print(json.dumps(getattr(installer, action)(home=home, repo_root=repo, **kwargs)), flush=True)
"""


def _process(desk, action, role, fail_commit=False):
    home, repo = desk
    return subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", _PROCESS,
         str(Path(installer.__file__).resolve().parents[1]), str(home), str(repo),
         action, role, str(fail_commit)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=home, env={"HOME": str(home), "CODEX_HOME": str(home / ".codex"), "PATH": "/usr/bin:/bin"},
    )


def _line(process):
    assert select.select([process.stdout], [], [], 5)[0], "child 冇喺 deadline 內回應"
    return process.stdout.readline().strip()


@pytest.mark.parametrize("first_action,second_action,fail_commit", [
    ("install", "install", False), ("install", "uninstall", False),
    ("uninstall", "install", False), ("uninstall", "uninstall", False),
    ("install", "install", True), ("uninstall", "uninstall", True),
])
def test_process_transactions_serialize_through_commit_and_rollback(
        desk, first_action, second_action, fail_commit):
    home, repo = desk
    if first_action == "uninstall":
        _install(desk)
    processes = []
    try:
        first = _process(desk, first_action, "first", fail_commit)
        processes.append(first)
        assert _line(first) == "paused"
        second = _process(desk, second_action, "second")
        processes.append(second)
        assert _line(second) == "waiting"
        assert not select.select([second.stdout], [], [], 0.15)[0]
        # 第一個 process 停喺 host IO／rollback 都唔阻 hook 寫 shared cache。
        Store(home=home).write_cache("throttle", {"probe": True}, deadline=time.monotonic() + 0.3)
        output, error = first.communicate("\n", timeout=5)
        assert first.returncode == 0, error
        first_result = json.loads(output)
        assert first_result["ok"] is not fail_commit, first_result
        if fail_commit:
            assert first_result["changed"] == []
            assert first_result["diagnostics"] == [first_action + "_failed"]
        output, error = second.communicate(timeout=5)
        assert second.returncode == 0, error
        acquired, encoded = output.splitlines()
        assert acquired == "acquired"
        second_result = json.loads(encoded)
        assert second_result["ok"], second_result
        if first_action == second_action and not fail_commit:
            assert second_result["changed"] == []
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
    if second_action == "install":
        assert installer.doctor(home=home, repo_root=repo)["installed"]
    else:
        assert _manifest(home)["artifacts"] == {}
        assert all(not os.path.lexists(path) for path in installer._targets(home).values())


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_installation_lock_has_bounded_wait_without_host_changes(desk, monkeypatch, action):
    home, repo = desk
    _install(desk)
    paths = list(installer._targets(home).values())
    before = {path: installer._read(home, path) for path in paths}
    manifest = _manifest(home)
    lock = home / ".local/state/quota-lantern" / installer.INSTALL_LOCK_NAME
    identity = lock.stat().st_ino
    monkeypatch.setattr(installer, "INSTALL_LOCK_SECONDS", 0.05)
    with lock.open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        start = time.monotonic()
        kwargs = {"dry_run": False} if action == "install" else {}
        result = getattr(installer, action)(home=home, repo_root=repo, **kwargs)
        assert time.monotonic() - start < 0.5
    assert not result["ok"]
    assert result["diagnostics"] == ["install_lock_timeout"]
    assert {path: installer._read(home, path) for path in paths} == before
    assert _manifest(home) == manifest
    assert lock.stat().st_ino == identity
    assert lock.stat().st_mode & 0o777 == 0o600
    assert lock.parent.stat().st_mode & 0o777 == 0o700
    assert installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert installer.uninstall(home=home, repo_root=repo)["ok"]
    assert lock.stat().st_ino == identity


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo", "public", "nonempty"])
@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_unsafe_installation_lock_is_never_repaired_or_followed(desk, tmp_path, kind, action):
    home, repo = desk
    _install(desk)
    targets = {path: installer._read(home, path) for path in installer._targets(home).values()}
    manifest = _manifest(home)
    lock = home / ".local/state/quota-lantern" / installer.INSTALL_LOCK_NAME
    lock.unlink()
    outside = tmp_path / "lock-target"
    outside.write_bytes(b"PRIVATE_DO_NOT_CHANGE")
    outside.chmod(0o600)
    if kind == "symlink":
        lock.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, lock)
    elif kind == "directory":
        lock.mkdir(mode=0o700)
    elif kind == "fifo":
        os.mkfifo(lock, 0o600)
    else:
        lock.write_bytes(b"" if kind == "public" else b"private content")
        lock.chmod(0o644 if kind == "public" else 0o600)
    before = lock.lstat()
    kwargs = {"dry_run": False} if action == "install" else {}
    start = time.monotonic()
    report = getattr(installer, action)(home=home, repo_root=repo, **kwargs)
    assert time.monotonic() - start < 0.5
    assert not report["ok"]
    assert report["changed"] == []
    assert "PRIVATE_DO_NOT_CHANGE" not in json.dumps(report)
    assert lock.lstat() == before
    assert outside.read_bytes() == b"PRIVATE_DO_NOT_CHANGE"
    assert {path: installer._read(home, path) for path in targets} == targets
    assert _manifest(home) == manifest


def test_skill_is_shared_auto_invocable_and_advisory():
    path = Path(__file__).resolve().parents[1] / "tools/quota_lantern/skill/SKILL.md"
    text = path.read_text()
    assert "name: quota-lantern" in text
    assert "description:" in text
    assert "disable-model-invocation: true" not in text
    assert "allowed-tools:" not in text
    assert "soft / advisory" in text
    assert "data untrusted" in text
    assert "唔使每個 message" in text
    assert "唔自動派工" in text
