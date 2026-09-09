"""所有 installer／child 都只用 synthetic HOME，唔連真 harness 或網絡。"""
import fcntl
import importlib
import json
import os
from pathlib import Path
import select
import shlex
import stat
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from lantern_common import install as common
from reality_lantern import install as installer
from reality_lantern.store import Store
from quota_lantern import install as quota


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home = tmp_path / "fake home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    repo = tmp_path / "repo 'quoted' $(touch SHOULD_NOT_EXIST)"
    (repo / "tools/hooks").mkdir(parents=True)
    script = ("import json,sys\nprint(json.dumps({'args':sys.argv[1:],"
              "'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,"
              "'no_bytecode':sys.dont_write_bytecode}))\n")
    for product in ("reality", "quota"):
        (repo / f"tools/{product}_lantern/skill").mkdir(parents=True)
        (repo / f"tools/{product}_lantern/skill/SKILL.md").write_text(f"---\nname: {product}-lantern\ndescription: fixture\n---\n")
        (repo / f"tools/kingdom-{product}.py").write_text(script)
        (repo / f"tools/hooks/{product}-lantern.py").write_text(script)
    return home, repo


def settings(home, value):
    path = home / ".claude/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=4) + "\n")
    return path


def agents(home, content):
    path = home / ".codex/AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def install(desk, product=installer):
    home, repo = desk
    result = product.install(home=home, repo_root=repo, dry_run=False)
    assert result["ok"], result
    return result


def manifest(home):
    return Store(home=home).read_cache("installation")


def tree(home):
    # 唔跟 symlink；metadata 亦用嚟證明 doctor／dry-run 冇 rewrite。
    return {str(path.relative_to(home)): (
        path.lstat().st_mode, path.lstat().st_mtime_ns, path.lstat().st_ino,
        os.readlink(path) if path.is_symlink() else (path.read_bytes() if path.is_file() else None))
        for path in home.rglob("*")}


def test_dry_run_doctor_uninstall_empty_home_are_write_free(desk, monkeypatch):
    home, repo = desk
    def forbidden(*args, **kwargs):
        pytest.fail("純讀命令唔應該 lock／write")
    monkeypatch.setattr(installer, "host_settings_lock", forbidden)
    monkeypatch.setattr(installer, "host_put", forbidden)
    monkeypatch.setattr(Store, "write_cache", forbidden)
    report = installer.install(home=home, repo_root=repo)
    assert report["ok"] and set(report["changed"]) == set(installer._targets(home))
    assert not installer.doctor(home=home, repo_root=repo)["installed"]
    assert installer.uninstall(home=home, repo_root=repo)["ok"]
    assert tree(home) == {}


def test_six_events_preserve_foreign_groups_arrays_and_all_settings(desk):
    home, repo = desk
    original = {
        "model": "leave-model", "routing": {"provider": "leave-route"},
        "permissions": {"allow": ["Read", "Read"], "deny": ["Write"]},
        "statusLine": {"type": "command", "command": "leave-status"},
        "env": {"SECRET_CANARY_KEY": "SECRET_CANARY_VALUE"},
        "future": [None, {"list": [1, 1, [], True]}],
        "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
            {"type": "command", "command": "quota-lantern fixture", "timeout": 2}]}],
            "PreToolUse": [], "Stop": [], "Notification": [{"future": 9, "hooks": [
                {"type": "prompt", "prompt": "foreign prompt"}]}]},
    }
    path = settings(home, original)
    path.chmod(0o640)
    guide = agents(home, "# 原本\n原文唔改".encode())
    before = guide.read_bytes()
    install(desk)
    current = json.loads(path.read_bytes())
    assert {key: value for key, value in current.items() if key != "hooks"} == {key: value for key, value in original.items() if key != "hooks"}
    assert current["hooks"]["Stop"] == []
    assert current["hooks"]["Notification"] == original["hooks"]["Notification"]
    for event, matcher in installer.EVENTS.items():
        assert current["hooks"][event][:-1] == original["hooks"].get(event, [])
        group = current["hooks"][event][-1]
        assert set(group) == ({"hooks"} if matcher is None else {"hooks", "matcher"})
        assert group.get("matcher") == matcher
        assert set(group["hooks"][0]) == {"type", "command", "timeout"}
        assert group["hooks"][0]["timeout"] == 1
        assert group["hooks"][0]["type"] == "command"
        argv = shlex.split(group["hooks"][0]["command"])
        assert Path(argv[0]).is_absolute()
        assert argv[1:6] == ["-I", "-S", "-B", str(repo / "tools/hooks/reality-lantern.py"), event]
    assert path.stat().st_mode & 0o777 == 0o640
    assert guide.read_bytes().startswith(before + b"\n\n" + installer.BEGIN.encode())
    value = manifest(home)
    assert value["schema"] == installer.MANIFEST_SCHEMA
    assert set(value["artifacts"]) == set(installer._targets(home))
    for private in (home / ".local/state").rglob("*"):
        if private.is_file():
            assert b"SECRET_CANARY" not in private.read_bytes()
    assert "SECRET_CANARY" not in json.dumps(value)
    assert not list(home.rglob("*.bak"))
    report = installer.doctor(home=home, repo_root=repo)
    assert report["installed"] and report["activation"] == "unverified"
    assert report["checks"]["harness_loaded"] == "unverified"


def test_idempotent_install_and_doctor_are_pure_reads_of_owned_objects(desk, monkeypatch):
    home, repo = desk
    install(desk)
    before = tree(home)
    def forbidden(*args, **kwargs):
        pytest.fail("已安裝唔應該重新寫 artifact／manifest")
    monkeypatch.setattr(installer, "host_put", forbidden)
    monkeypatch.setattr(Store, "write_cache", forbidden)
    assert install(desk)["changed"] == []
    monkeypatch.setattr(installer, "host_settings_lock", forbidden)
    assert installer.install(home=home, repo_root=repo)["changed"] == []
    assert installer.doctor(home=home, repo_root=repo)["installed"]
    assert tree(home) == before


@pytest.mark.parametrize("content", [b"", b"mine", b"mine\n", b"mine\n\n", b"mine\r\n", b"```text\nhello\n```\n"])
def test_uninstall_preserves_exact_agents_original(desk, content):
    home, repo = desk
    guide = agents(home, content)
    install(desk)
    assert installer.uninstall(home=home, repo_root=repo)["ok"]
    assert guide.read_bytes() == content
    assert manifest(home)["artifacts"] == {}


def test_uninstall_preserves_later_foreign_changes_and_evidence(desk):
    home, repo = desk
    path = settings(home, {"hooks": {"PostToolUse": []}, "model": "original"})
    guide = agents(home, b"mine\n")
    install(desk)
    evidence = home / ".local/state/reality-lantern/receipt-canary"
    evidence.write_bytes(b"EVIDENCE_MUST_STAY")
    evidence.chmod(0o600)
    value = json.loads(path.read_bytes())
    value["model"] = "new-model"
    value["permissions"] = {"deny": ["Bash"]}
    foreign = {"hooks": [{"type": "command", "command": "foreign-new"}]}
    value["hooks"]["SessionStart"].append(foreign)
    path.write_text(json.dumps(value))
    guide.write_bytes(b"new header\n" + guide.read_bytes() + b"new footer\n")
    report = installer.uninstall(home=home, repo_root=repo)
    assert report["ok"], report
    assert json.loads(path.read_bytes()) == {"model": "new-model", "permissions": {"deny": ["Bash"]},
                                           "hooks": {"PostToolUse": [], "SessionStart": [foreign]}}
    assert guide.read_bytes() == b"new header\nmine\nnew footer\n"
    assert evidence.read_bytes() == b"EVIDENCE_MUST_STAY"
    before = tree(home)
    assert installer.uninstall(home=home, repo_root=repo)["changed"] == []
    assert tree(home) == before
    install(desk)


@pytest.mark.parametrize("key", ["wrapper", "claude_skill", "codex_skill", "agents", "settings"])
def test_later_owned_edits_are_not_overwritten_or_removed(desk, key):
    home, repo = desk
    install(desk)
    path = installer._targets(home)[key]
    if key.endswith("skill"):
        path.unlink()
        path.symlink_to(home / "foreign-missing")
        before = os.readlink(path)
    elif key == "settings":
        value = json.loads(path.read_bytes())
        value["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 9
        path.write_text(json.dumps(value))
        before = path.read_bytes()
    else:
        path.write_bytes(path.read_bytes().replace(b"soft / advisory", b"edited") if key == "agents" else b"foreign wrapper\n")
        before = path.read_bytes()
    saved = tree(home)
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert tree(home) == saved
    assert not installer.doctor(home=home, repo_root=repo)["installed"]
    report = installer.uninstall(home=home, repo_root=repo)
    assert not report["ok"]
    assert set(manifest(home)["artifacts"]) == {key}
    if key == "settings":
        value = json.loads(path.read_bytes())
        assert set(value["hooks"]) == {"PreToolUse"}
        assert value["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == 9
    else:
        assert (os.readlink(path) if key.endswith("skill") else path.read_bytes()) == before


def test_modified_mode_and_duplicate_groups_are_not_claimed(desk):
    home, repo = desk
    install(desk)
    wrapper = installer._targets(home)["wrapper"]
    wrapper.chmod(0o600)
    path = home / ".claude/settings.json"
    value = json.loads(path.read_bytes())
    for event in installer.EVENTS:
        value["hooks"][event] *= 2
    path.write_text(json.dumps(value, indent=5))
    before = path.read_bytes()
    assert not installer.doctor(home=home, repo_root=repo)["installed"]
    assert not installer.uninstall(home=home, repo_root=repo)["ok"]
    assert path.read_bytes() == before
    assert wrapper.stat().st_mode & 0o777 == 0o600
    assert set(manifest(home)["artifacts"]) == {"settings", "wrapper"}


def test_preexisting_exact_skill_links_are_shared_not_owned(desk):
    home, repo = desk
    for key in ("claude_skill", "codex_skill"):
        path = installer._targets(home)[key]
        path.parent.mkdir(parents=True)
        path.symlink_to(repo / "tools/reality_lantern/skill")
    install(desk)
    assert not manifest(home)["artifacts"]["claude_skill"]["owned"]
    assert installer.uninstall(home=home, repo_root=repo)["ok"]
    assert all(installer._targets(home)[key].is_symlink() for key in ("claude_skill", "codex_skill"))


@pytest.mark.parametrize("key", ["wrapper", "claude_skill", "codex_skill"])
@pytest.mark.parametrize("kind", ["file", "directory", "link", "hardlink", "fifo"])
def test_collisions_refuse_before_any_write(desk, key, kind):
    home, repo = desk
    path = installer._targets(home)[key]
    path.parent.mkdir(parents=True)
    if kind == "file":
        path.write_text("FOREIGN_OBJECT")
    elif kind == "directory":
        path.mkdir()
    elif kind == "link":
        path.symlink_to(home / "foreign-missing")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        target = home / "foreign-hardlink"
        target.write_text("FOREIGN_OBJECT")
        os.link(target, path)
    # FIFO 唔用 tree 嘅 regular file branch，避免任何無界 read。
    before = tree(home)
    report = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not report["ok"], report
    assert tree(home) == before


@pytest.mark.parametrize("target", [".claude/settings.json", ".codex/AGENTS.md", ".claude/skills", ".local/bin", ".local/state", ".codex"])
def test_symlinks_never_followed(desk, tmp_path, target):
    home, repo = desk
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"DO_NOT_TOUCH")
    path = home / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(sentinel if target.endswith((".json", ".md")) else outside)
    report = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not report["ok"]
    assert sentinel.read_bytes() == b"DO_NOT_TOUCH"
    assert list(outside.iterdir()) == [sentinel]
    assert path.is_symlink()


@pytest.mark.parametrize("target", [".claude", ".codex", ".local/bin", ".claude/settings.json", ".codex/AGENTS.md"])
def test_unsafe_writable_objects_refused_without_chmod(desk, target):
    home, repo = desk
    path = home / target
    path.parent.mkdir(parents=True, exist_ok=True)
    if target.endswith((".md", ".json")):
        path.write_bytes(b"{}" if target.endswith(".json") else b"mine")
        path.chmod(0o666)
    else:
        path.mkdir(exist_ok=True)
        path.chmod(0o777)
    before = tree(home)
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert tree(home) == before


@pytest.mark.parametrize("invalid", [b"{", b"[]", b"null", b'{"hooks":null}', b'{"hooks":{"Stop":{}}}',
    b'{"hooks":{"Stop":[{}]}}', b'{"hooks":{"Stop":[{"hooks":[1]}]}}', b'{"model":1,"model":2}',
    b'{"env":{"a":1,"a":2}}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}', b'\xff'])
def test_malformed_settings_are_never_repaired(desk, invalid):
    home, repo = desk
    path = settings(home, {})
    path.write_bytes(invalid)
    before = tree(home)
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert tree(home) == before


@pytest.mark.parametrize("invalid", [b"```python\nunclosed", b"~~~\nunclosed", b"<!-- unclosed", b"---\nunclosed: true", b"\xff",
    installer.BEGIN.encode() + b"\nforeign", installer.END.encode()])
def test_unsafe_agents_append_context_refused(desk, invalid):
    home, repo = desk
    path = agents(home, invalid)
    before = tree(home)
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert tree(home) == before
    assert path.read_bytes() == invalid


@pytest.mark.parametrize("marker", ["reality-lantern", "kingdom-reality"])
def test_foreign_similar_hook_collision_refused(desk, marker):
    home, repo = desk
    path = settings(home, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": marker}]}]}})
    before = tree(home)
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert tree(home) == before


@pytest.mark.parametrize("action", ["missing", "link"])
def test_untrusted_source_is_refused(desk, action):
    home, repo = desk
    source = repo / "tools/hooks/reality-lantern.py"
    source.unlink()
    if action == "link":
        source.symlink_to(repo / "tools/kingdom-reality.py")
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert tree(home) == {}


def test_bounded_file_size_refuses_before_writes(desk):
    home, repo = desk
    path = agents(home, b"x" * installer.MAX_BYTES)
    before = tree(home)
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert tree(home) == before


@pytest.mark.parametrize("invalid", [{}, {"schema": "foreign"}, {"schema": installer.MANIFEST_SCHEMA, "repo_root": "foreign", "artifacts": {}}])
def test_unknown_manifest_shapes_refused(desk, invalid):
    home, repo = desk
    Store(home=home).write_cache("installation", invalid)
    before = tree(home)
    for action in ("install", "doctor", "uninstall"):
        assert not getattr(installer, action)(home=home, repo_root=repo)["ok"]
    assert tree(home) == before


def test_hooks_and_wrapper_quote_paths_and_isolate_python(desk, tmp_path):
    home, repo = desk
    install(desk)
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    for name in ("sitecustomize.py", "reality_lantern.py"):
        (attacker / name).write_text("raise RuntimeError('must not import')")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(attacker)}
    wrapper = installer._targets(home)["wrapper"]
    result = subprocess.run([str(wrapper), "status", "a 'quoted' value"], cwd=attacker, env=env,
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"args": ["status", "a 'quoted' value"], "isolated": 1, "no_site": 1, "no_bytecode": True}
    value = json.loads((home / ".claude/settings.json").read_bytes())
    for event in installer.EVENTS:
        command = value["hooks"][event][0]["hooks"][0]["command"]
        for raw in (command, command.removesuffix(" 2>/dev/null || true")):
            result = subprocess.run(["/bin/sh", "-c", raw], input="{}", cwd=attacker, env=env,
                                    text=True, capture_output=True, timeout=5)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {"args": [event], "isolated": 1, "no_site": 1, "no_bytecode": True}
    assert not (attacker / "SHOULD_NOT_EXIST").exists()
    assert not list(repo.rglob("__pycache__"))


@pytest.mark.parametrize("failure", ["host", "manifest"])
def test_precommit_failure_ram_rollback_exact_original_no_secret_backup(desk, monkeypatch, failure):
    home, repo = desk
    path = settings(home, {"env": {"SECRET_CANARY": "DO_NOT_BACKUP"}})
    guide = agents(home, b"mine without newline")
    originals = path.read_bytes(), guide.read_bytes()
    put = installer.host_put
    def broken_put(home, path, before, after, directories):
        if path.name == "settings.json":
            raise OSError("PRIVATE_EXCEPTION")
        return put(home, path, before, after, directories)
    def broken_write(*args, **kwargs):
        raise OSError("PRIVATE_EXCEPTION")
    if failure == "host":
        monkeypatch.setattr(installer, "host_put", broken_put)
    else:
        monkeypatch.setattr(Store, "write_cache", broken_write)
    result = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not result["ok"] and result["changed"] == []
    assert "PRIVATE_EXCEPTION" not in json.dumps(result)
    assert (path.read_bytes(), guide.read_bytes()) == originals
    assert manifest(home) is None
    for key in ("wrapper", "claude_skill", "codex_skill"):
        assert not os.path.lexists(installer._targets(home)[key])
    for file in (home / ".local/state").rglob("*"):
        if file.is_file():
            assert b"DO_NOT_BACKUP" not in file.read_bytes()
    assert not list(home.rglob("*.bak"))


def test_failed_uninstall_commit_restores_artifacts(desk, monkeypatch):
    home, repo = desk
    install(desk)
    before = {key: common.host_read(home, path) for key, path in installer._targets(home).items()}
    old = manifest(home)
    def fail(*args, **kwargs):
        raise OSError("PRIVATE_EXCEPTION")
    monkeypatch.setattr(Store, "write_cache", fail)
    result = installer.uninstall(home=home, repo_root=repo)
    assert not result["ok"] and result["changed"] == []
    assert manifest(home) == old
    for key, path in installer._targets(home).items():
        now = common.host_read(home, path)
        assert (now.kind, now.data, now.mode) == (before[key].kind, before[key].data, before[key].mode)


@pytest.mark.parametrize("replacement", ["content", "inode", "mode"])
def test_rollback_does_not_overwrite_new_foreign_object(desk, monkeypatch, replacement):
    home, repo = desk
    wrapper = installer._targets(home)["wrapper"]
    put = installer.host_put
    saved = None
    def fail(home, path, before, after, directories):
        nonlocal saved
        if path.name == "settings.json":
            if replacement == "content":
                wrapper.write_bytes(b"foreign concurrent edit")
            elif replacement == "mode":
                wrapper.chmod(0o500)
            else:
                content = wrapper.read_bytes()
                wrapper.unlink()
                wrapper.write_bytes(content)
                wrapper.chmod(0o700)
            saved = common.host_read(home, wrapper)
            raise OSError("PRIVATE_EXCEPTION")
        return put(home, path, before, after, directories)
    monkeypatch.setattr(installer, "host_put", fail)
    result = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not result["ok"]
    assert "install_rollback_incomplete" in result["diagnostics"]
    assert common.host_read(home, wrapper) == saved


@pytest.mark.parametrize("action", ["install", "uninstall"])
@pytest.mark.parametrize("readback_fails", [False, True])
def test_published_manifest_exception_never_rolls_back_host(desk, monkeypatch, action, readback_fails):
    home, repo = desk
    if action == "uninstall":
        install(desk)
    read, write = Store.read_cache, Store.write_cache
    published = False
    def publish(self, name, value, **kwargs):
        nonlocal published
        write(self, name, value, **kwargs)
        published = True
        raise OSError("PRIVATE_POST_REPLACE_FAILURE")
    def readback(self, name, *args, **kwargs):
        if published and readback_fails:
            raise OSError("PRIVATE_READBACK_FAILURE")
        return read(self, name, *args, **kwargs)
    monkeypatch.setattr(Store, "write_cache", publish)
    monkeypatch.setattr(Store, "read_cache", readback)
    kwargs = {"dry_run": False} if action == "install" else {}
    result = getattr(installer, action)(home=home, repo_root=repo, **kwargs)
    assert published and not result["ok"]
    assert set(result["changed"]) == set(installer._targets(home))
    assert result["diagnostics"] == ["install_manifest_unverified" if readback_fails else "install_manifest_durability_unverified"]
    current = read(Store(home=home), "installation")
    for key, path in installer._targets(home).items():
        obj = common.host_read(home, path)
        if action == "install":
            assert installer._matches(key, obj, current["artifacts"][key])
        else:
            assert obj is None
    assert "PRIVATE_" not in json.dumps(result)


def test_silent_manifest_write_failure_is_not_success(desk, monkeypatch):
    home, repo = desk
    monkeypatch.setattr(Store, "write_cache", lambda *args, **kwargs: None)
    result = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not result["ok"]
    assert manifest(home) is None
    assert result["changed"] == []
    assert all(not os.path.lexists(path) for path in installer._targets(home).values())


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_successful_publish_with_unreadable_readback_keeps_host(desk, monkeypatch, action):
    home, repo = desk
    if action == "uninstall":
        install(desk)
    read, write = Store.read_cache, Store.write_cache
    published = False
    def publish(self, name, value, **kwargs):
        nonlocal published
        write(self, name, value, **kwargs)
        published = True
    def readback(self, name, *args, **kwargs):
        if published:
            raise OSError("PRIVATE_READBACK_FAILURE")
        return read(self, name, *args, **kwargs)
    monkeypatch.setattr(Store, "write_cache", publish)
    monkeypatch.setattr(Store, "read_cache", readback)
    kwargs = {"dry_run": False} if action == "install" else {}
    result = getattr(installer, action)(home=home, repo_root=repo, **kwargs)
    assert not result["ok"] and result["diagnostics"] == ["install_manifest_unverified"]
    assert set(result["changed"]) == set(installer._targets(home))
    assert all(os.path.lexists(path) == (action == "install") for path in installer._targets(home).values())


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_common_host_lock_is_bounded_and_retained(desk, monkeypatch, action):
    home, repo = desk
    install(desk)
    lock = home / ".local/state/lantern-common/.host-settings.lock"
    before = tree(home)
    monkeypatch.setattr(installer, "INSTALL_LOCK_SECONDS", 0.03)
    with lock.open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        start = time.monotonic()
        kwargs = {"dry_run": False} if action == "install" else {}
        result = getattr(installer, action)(home=home, repo_root=repo, **kwargs)
        assert time.monotonic() - start < 0.5
    assert not result["ok"] and result["diagnostics"] == ["install_lock_timeout"]
    assert tree(home) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo", "public", "nonempty"])
def test_unsafe_shared_lock_refused_and_not_repaired(desk, kind):
    home, repo = desk
    install(desk)
    lock = home / ".local/state/lantern-common/.host-settings.lock"
    lock.unlink()
    outside = home / "foreign-lock-target"
    outside.write_bytes(b"DO_NOT_TOUCH")
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
        lock.write_bytes(b"" if kind == "public" else b"content")
        lock.chmod(0o644 if kind == "public" else 0o600)
    before = tree(home)
    assert not installer.install(home=home, repo_root=repo, dry_run=False)["ok"]
    assert not installer.uninstall(home=home, repo_root=repo)["ok"]
    assert tree(home) == before


@pytest.mark.parametrize("install_order", [(installer, quota), (quota, installer)])
@pytest.mark.parametrize("remove_order", [(installer, quota), (quota, installer)])
def test_two_lights_install_and_uninstall_preserve_each_other(desk, install_order, remove_order):
    home, repo = desk
    original = {"model": "keep", "routing": ["keep"], "permissions": {"ask": ["Bash"]},
                "hooks": {event: [] for event in {*installer.EVENTS, *quota.EVENTS}}, "statusLine": {"command": "keep"}}
    path = settings(home, original)
    guide = agents(home, b"original\n")
    for product in install_order:
        install(desk, product)
    assert installer.doctor(home=home, repo_root=repo)["installed"]
    assert quota.doctor(home=home, repo_root=repo)["installed"]
    first, second = remove_order
    assert first.uninstall(home=home, repo_root=repo)["ok"]
    assert second.doctor(home=home, repo_root=repo)["installed"]
    assert second.uninstall(home=home, repo_root=repo)["ok"]
    assert json.loads(path.read_bytes()) == original
    assert guide.read_bytes() == b"original\n"
    assert manifest(home)["artifacts"] == {}
    assert quota.Store(home=home).read_cache("installation")["artifacts"] == {}


@pytest.mark.parametrize("first,second", [(installer, quota), (quota, installer)])
def test_no_cross_product_guessing_of_preexisting_empty_arrays(desk, first, second):
    home, repo = desk
    path = settings(home, {"model": "keep"})
    install(desk, first)
    install(desk, second)
    assert first.uninstall(home=home, repo_root=repo)["ok"]
    assert second.uninstall(home=home, repo_root=repo)["ok"]
    # 第二盞燈見過既有 containers；冇跨產品 ownership 證據就保留空殼，唔猜可以刪。
    assert json.loads(path.read_bytes()) == {"model": "keep", "hooks": {
        event: [] for event in set(first.EVENTS) & set(second.EVENTS)}}


# Child 只 import 本 checkout；所有 writes 喺 fake HOME，唔經 shell 執行 user 內容。
_PROCESS = r'''
import importlib,json,sys
from pathlib import Path
from contextlib import contextmanager
sys.path.insert(0, sys.argv[1])
from lantern_common import install as common
home, repo = Path(sys.argv[2]), Path(sys.argv[3])
product, action, role, fail_commit = sys.argv[4:8]
installer = importlib.import_module(product + "_lantern.install")
store_type = importlib.import_module(product + "_lantern.store").Store
real_put, real_lock = common.host_put, common.host_settings_lock
real_write = store_type.write_cache
failed = paused = False

def put(*args, **kwargs):
    global paused
    value = real_put(*args, **kwargs)
    if role == "first" and not paused and (fail_commit == "False" or failed):
        paused = True
        print("paused", flush=True)
        if not sys.stdin.readline():
            raise OSError("test gate closed")
    return value

def write(self, name, value, **kwargs):
    global failed
    if fail_commit == "True" and name == "installation":
        failed = True
        raise OSError("private failure")
    return real_write(self, name, value, **kwargs)

@contextmanager
def lock(*args, **kwargs):
    if role == "second":
        print("waiting", flush=True)
    with real_lock(*args, **kwargs) as held:
        if role == "second":
            print("acquired", flush=True)
        yield held

common.host_put, common.host_settings_lock = put, lock
if product == "reality":
    installer.host_put, installer.host_settings_lock = put, lock
store_type.write_cache = write
kwargs = {"dry_run": False} if action == "install" else {}
print(json.dumps(getattr(installer, action)(home=home, repo_root=repo, **kwargs)), flush=True)
'''


def process(desk, product, action, role, fail=False):
    home, repo = desk
    return subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", _PROCESS,
        str(ROOT / "tools"), str(home), str(repo), product, action, role, str(fail)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=home, env={"HOME": str(home), "CODEX_HOME": str(home / ".codex"), "PATH": "/usr/bin:/bin"})


def line(child):
    assert select.select([child.stdout], [], [], 5)[0], "child 冇喺 deadline 回應"
    return child.stdout.readline().strip()


@pytest.mark.parametrize("first_product,second_product", [("reality", "quota"), ("quota", "reality")])
@pytest.mark.parametrize("first_action,second_action,fail", [
    ("install", "install", False), ("install", "uninstall", False),
    ("uninstall", "install", False), ("uninstall", "uninstall", False),
    ("install", "install", True), ("uninstall", "uninstall", True)])
def test_different_product_processes_hold_common_lock_through_commit_and_rollback(
        desk, first_product, second_product, first_action, second_action, fail):
    home, repo = desk
    modules = {"reality": installer, "quota": quota}
    path = settings(home, {"model": "keep", "hooks": {event: [] for event in {*installer.EVENTS, *quota.EVENTS}}})
    initially_installed = set()
    for product, action in ((first_product, first_action), (second_product, second_action)):
        if action == "uninstall":
            install(desk, modules[product])
            initially_installed.add(product)
    children = []
    try:
        first = process(desk, first_product, first_action, "first", fail)
        children.append(first)
        assert line(first) == "paused"
        second = process(desk, second_product, second_action, "second")
        children.append(second)
        assert line(second) == "waiting"
        assert not select.select([second.stdout], [], [], 0.12)[0]
        # 真攞兩個 data locks；host transaction／rollback 唔可以鎖住 receipt writers。
        Store(home=home).get_key(create=True, deadline=time.monotonic() + 0.2)
        quota.Store(home=home).write_cache("receipts", {"canary": True}, deadline=time.monotonic() + 0.2)
        output, error = first.communicate("\n", timeout=5)
        assert first.returncode == 0, error
        first_result = json.loads(output)
        assert first_result["ok"] is not fail, first_result
        if fail:
            assert first_result["changed"] == []
        output, error = second.communicate(timeout=5)
        assert second.returncode == 0, error
        acquired, encoded = output.splitlines()
        assert acquired == "acquired"
        assert json.loads(encoded)["ok"], output
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
    expected = set(initially_installed)
    for product, action in ((first_product, first_action), (second_product, second_action)):
        if product == first_product and fail:
            continue
        expected.add(product) if action == "install" else expected.discard(product)
    for product, module in modules.items():
        assert module.doctor(home=home, repo_root=repo)["installed"] == (product in expected)
    assert json.loads(path.read_bytes())["model"] == "keep"


@pytest.mark.parametrize("fail_commit", [False, True])
def test_host_read_put_and_rollback_do_not_hold_reality_data_lock(desk, monkeypatch, fail_commit):
    home, repo = desk
    store = Store(home=home)
    store.get_key(create=True)
    calls = []
    def guard(function):
        def checked(*args, **kwargs):
            calls.append(function.__name__)
            # 真取得 Store data lock，而非檢查 mock flag。
            store.get_key(create=True, deadline=time.monotonic() + 0.15)
            return function(*args, **kwargs)
        return checked
    monkeypatch.setattr(installer, "host_read", guard(installer.host_read))
    monkeypatch.setattr(installer, "host_put", guard(installer.host_put))
    monkeypatch.setattr(common, "host_read", guard(common.host_read))
    monkeypatch.setattr(common, "host_put", guard(common.host_put))
    if fail_commit:
        def fail(*args, **kwargs):
            raise OSError("private commit failure")
        monkeypatch.setattr(Store, "write_cache", fail)
    result = installer.install(home=home, repo_root=repo, dry_run=False)
    assert result["ok"] is not fail_commit, result
    assert "host_read" in calls and "host_put" in calls
    if fail_commit:
        assert calls.count("host_put") == 10
        assert result["changed"] == []


def test_real_source_cli_and_six_hook_pipes_use_only_fake_home(desk, tmp_path):
    home, _ = desk
    install((home, ROOT), quota)
    install((home, ROOT))
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    env = {"HOME": str(home), "CODEX_HOME": str(home / ".codex"), "PATH": "/usr/bin:/bin"}
    wrapper = installer._targets(home)["wrapper"]
    before = tree(home)
    for args in (["status", "--cwd", str(cwd), "--json"], ["brief", "--cwd", str(cwd)], ["doctor"]):
        result = subprocess.run([str(wrapper), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        if args[0] == "doctor":
            assert json.loads(result.stdout)["installed"]
    assert tree(home) == before
    value = json.loads((home / ".claude/settings.json").read_bytes())
    for event in installer.EVENTS:
        group = next(group for group in value["hooks"][event] if any("reality-lantern.py" in hook.get("command", "") for hook in group["hooks"]))
        command = group["hooks"][0]["command"].removesuffix(" 2>/dev/null || true")
        payload = {"hook_event_name": event, "cwd": str(cwd), "session_id": "fake-session",
                   "tool_name": "Bash", "tool_input": {"command": "PRIVATE_COMMAND_MUST_NOT_STORE"},
                   "tool_response": {"stdout": "PRIVATE_OUTPUT_MUST_NOT_STORE"}}
        result = subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload), cwd=cwd,
                                env=env, text=True, capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr
        if result.stdout.strip():
            context = json.loads(result.stdout)
            assert set(context) <= {"hookSpecificOutput"}
            assert "PRIVATE_" not in result.stdout
        assert tree(home) == before
    assert installer.uninstall(home=home, repo_root=ROOT)["ok"]
    assert quota.doctor(home=home, repo_root=ROOT)["installed"]
    assert quota.uninstall(home=home, repo_root=ROOT)["ok"]


@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_real_atomic_manifest_fsync_failure_is_reconciled(desk, monkeypatch, phase, action):
    home, repo = desk
    if action == "uninstall":
        install(desk)
    module = importlib.import_module("reality_lantern.store")
    original_atomic, original_fsync = module.atomic_write, os.fsync
    writing = False
    failures = []
    def atomic(fd, name, *args, **kwargs):
        nonlocal writing
        assert name == "installation.json"
        writing = True
        try:
            return original_atomic(fd, name, *args, **kwargs)
        finally:
            writing = False
    def fsync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if writing and directory == (phase == "after_replace"):
            failures.append(phase)
            raise OSError("PRIVATE_ACTUAL_FSYNC_FAILURE")
        return original_fsync(fd)
    monkeypatch.setattr(module, "atomic_write", atomic)
    monkeypatch.setattr(os, "fsync", fsync)
    kwargs = {"dry_run": False} if action == "install" else {}
    result = getattr(installer, action)(home=home, repo_root=repo, **kwargs)
    assert failures == [phase]
    assert not result["ok"], result
    installed = (action == "install") == (phase == "after_replace")
    for path in installer._targets(home).values():
        assert os.path.lexists(path) == installed
    if phase == "after_replace":
        assert result["diagnostics"] == ["install_manifest_durability_unverified"]
        assert set(result["changed"]) == set(installer._targets(home))
    else:
        assert result["changed"] == []
    assert "PRIVATE_" not in json.dumps(result)


def test_read_before_write_refuses_concurrent_foreign_settings(desk, monkeypatch):
    home, repo = desk
    path = settings(home, {"model": "original"})
    original_put = installer.host_put
    foreign = b'{"model":"concurrent","permissions":{"deny":["Bash"]}}\n'
    def put(home, target, before, after, directories):
        if target == path:
            target.write_bytes(foreign)
        return original_put(home, target, before, after, directories)
    monkeypatch.setattr(installer, "host_put", put)
    result = installer.install(home=home, repo_root=repo, dry_run=False)
    assert not result["ok"] and result["changed"] == []
    assert result["diagnostics"] == ["install_concurrent_change"]
    assert path.read_bytes() == foreign
    assert all(not os.path.lexists(target) for key, target in installer._targets(home).items() if key != "settings")
    assert manifest(home) is None
