"""Generic engine 嘅三種 thin adapter；唔觸碰真 HOME／真 installer activation。"""
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from lantern_common import product

SPECS = [
    product.ProductSpec("friction-lantern", "friction_lantern", "kingdom-friction", "friction-lantern",
                        {"SessionStart": None, "PostToolUse": "Bash"}, "## 阻力燈\n只讀提示，唔當 pass。"),
    product.ProductSpec("coedit-radar", "coedit_radar", "kingdom-coedit", "coedit-radar",
                        {"PreToolUse": "Read|Edit|Write", "PostToolUse": "Read|Edit|Write"}, "## 共筆\n唔借他人 baseline。"),
    product.ProductSpec("lanterns", "lantern_suite", "kingdom-lanterns", "lanterns", {}, "## 四燈\n入口唔等於已啟用。"),
]


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home, repo = tmp_path / "fake home", tmp_path / "repo 'quoted' $(touch NO_EXEC)"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    (repo / "tools/hooks").mkdir(parents=True)
    for spec in SPECS:
        (repo / "tools" / spec.package / "skill").mkdir(parents=True)
        (repo / "tools" / spec.package / "skill/SKILL.md").write_text("fixture\n")
        script = ("import json,sys\nprint(json.dumps({'args':sys.argv[1:], 'isolated':sys.flags.isolated,"
                  "'no_site':sys.flags.no_site, 'no_bytecode':sys.dont_write_bytecode}))\n")
        (repo / "tools" / (spec.command + ".py")).write_text(script)
        if spec.events:
            (repo / "tools/hooks" / (spec.product_id + ".py")).write_text(script)
    return home, repo


def tree(home):
    return {str(path.relative_to(home)): (path.lstat().st_mode, path.lstat().st_ino,
            path.lstat().st_mtime_ns, os.readlink(path) if path.is_symlink()
            else path.read_bytes() if path.is_file() else None) for path in home.rglob("*")}


def install(desk, spec=SPECS[0], **kwargs):
    home, repo = desk
    return product.product_install(spec, home=home, repo_root=repo, dry_run=False, **kwargs)


def engine(desk, spec=SPECS[0]):
    return product._Product(spec, *desk)


def seed(home, relative, data):
    path = home / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("spec", SPECS)
def test_adapter_exact_manifest_and_result_keys(desk, spec):
    home, repo = desk
    result = install(desk, spec)
    assert set(result) == {"ok", "action", "dry_run", "changed", "diagnostics", "activation", "installation_nonce", "created"}
    assert result["ok"] and result["created"] and result["activation"] == "unverified"
    value = engine(desk, spec).read()
    assert value["schema"] == f"kingdom.{spec.product_id}.installation/2"
    assert value["installation_nonce"] == result["installation_nonce"]
    assert value["repo_root"] == str(repo)
    assert set(value["artifacts"]) == set(engine(desk, spec).targets)
    assert value["artifacts"]["wrapper"]["mode"] == 0o700
    state = home / ".local/state" / spec.product_id
    assert state.stat().st_mode & 0o777 == 0o700
    assert (state / "installation.json").stat().st_mode & 0o777 == 0o600
    assert {p.name for p in state.iterdir()} == {"installation.json", "installation-nonces.json"}
    history = state / "installation-nonces.json"
    assert history.stat().st_mode & 0o777 == 0o600 and history.stat().st_nlink == 1
    assert json.loads(history.read_bytes()) == {
        "schema": "kingdom.lantern.installation-nonces/1",
        "nonces": [result["installation_nonce"]],
    }
    report = product.product_doctor(spec, home=home, repo_root=repo)
    assert report["installed"] and report["installation_nonce"] == result["installation_nonce"]
    assert report["checks"]["harness_loaded"] == "unverified"


@pytest.mark.parametrize("kind", ["invalid", "symlink", "fifo", "unsafe"])
def test_no_events_never_reads_or_changes_settings(desk, monkeypatch, kind):
    home, repo = desk
    path = home / ".claude/settings.json"
    path.parent.mkdir()
    if kind == "symlink":
        path.symlink_to(home / "outside")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.write_bytes(b"PRIVATE_INVALID_SETTINGS")
        if kind == "unsafe":
            path.chmod(0o666)
    before = tree(home)[".claude/settings.json"]
    read, put = product.host_read, product.host_put
    def no_settings_read(home, target, *args, **kwargs):
        assert target != path
        return read(home, target, *args, **kwargs)
    def no_settings_put(home, target, *args, **kwargs):
        assert target != path
        return put(home, target, *args, **kwargs)
    monkeypatch.setattr(product, "host_read", no_settings_read)
    monkeypatch.setattr(product, "host_put", no_settings_put)
    spec = SPECS[2]
    result = product.product_install(spec, home=home, repo_root=repo)
    assert result["ok"] and "settings" not in result["changed"]
    assert install(desk, spec)["ok"]
    assert product.product_doctor(spec, home=home, repo_root=repo)["installed"]
    assert "settings" not in engine(desk, spec).read()["artifacts"]
    assert product.product_uninstall(spec, home=home, repo_root=repo)["ok"]
    assert tree(home)[".claude/settings.json"] == before


def test_products_preserve_each_other_and_foreign_configuration(desk):
    home, repo = desk
    original = {"model": "keep", "routing": "keep", "permissions": {"deny": ["Bash"]},
                "env": {"PRIVATE_CANARY": "NEVER_BACKUP"}, "statusLine": {"command": "keep"},
                "hooks": {event: [] for spec in SPECS for event in spec.events}}
    path = seed(home, ".claude/settings.json", json.dumps(original).encode())
    guide = seed(home, ".codex/AGENTS.md", "原有指南\n".encode())
    before_guide = guide.read_bytes()
    for spec in SPECS:
        assert install(desk, spec)["ok"]
        assert product.product_doctor(spec, home=home, repo_root=repo)["installed"]
    for item in (home / ".local/state").rglob("*"):
        if item.is_file():
            assert b"PRIVATE_CANARY" not in item.read_bytes()
            assert b"NEVER_BACKUP" not in item.read_bytes()
    for index, spec in enumerate(SPECS):
        assert product.product_uninstall(spec, home=home, repo_root=repo)["ok"]
        for remaining in SPECS[index + 1:]:
            assert product.product_doctor(remaining, home=home, repo_root=repo)["installed"]
    assert json.loads(path.read_bytes()) == original
    assert guide.read_bytes() == before_guide


@pytest.mark.parametrize("key", ["wrapper", "claude_skill", "codex_skill", "agents", "settings"])
@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "directory", "writable"])
def test_unsafe_target_collision_is_write_free(desk, key, kind):
    home, _ = desk
    path = engine(desk).targets[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "symlink":
        path.symlink_to(home / "foreign")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.mkdir()
    elif kind == "hardlink":
        outside = seed(home, "outside", b"PRIVATE_CANARY")
        os.link(outside, path)
    else:
        path.write_bytes(b"{}" if key == "settings" else b"FOREIGN")
        path.chmod(0o666)
    before = tree(home)
    result = install(desk)
    assert not result["ok"] and not result["created"]
    assert tree(home) == before


@pytest.mark.parametrize("key", ["cli", "hook", "skill"])
def test_untrusted_source_cannot_create_state(desk, key):
    home, repo = desk
    paths = engine(desk).sources()
    path = paths[key] / "SKILL.md" if key == "skill" else paths[key]
    path.unlink()
    path.symlink_to(repo / "outside")
    before = tree(home)
    result = install(desk)
    assert not result["ok"] and result["diagnostics"] == ["install_source_symlink"]
    assert tree(home) == before


@pytest.mark.parametrize("change", ["root", "wrapper", "hook", "guide", "skill"])
def test_existing_install_rejects_silent_source_change(desk, monkeypatch, tmp_path, change):
    home, repo = desk
    assert install(desk)["ok"]
    before = tree(home)
    source = product._Product.content
    def changed(self):
        wrapper, groups, block, link = source(self)
        if change == "wrapper":
            wrapper += b"# changed\n"
        elif change == "hook":
            groups["SessionStart"]["hooks"][0]["timeout"] = 2
        elif change == "guide":
            block = block.replace("阻力燈".encode(), b"changed")
        elif change == "skill":
            link += b"-changed"
        return wrapper, groups, block, link
    if change == "root":
        other = tmp_path / "other-root"
        other.mkdir()
        result = product.product_install(SPECS[0], home=home, repo_root=other, dry_run=False)
    else:
        monkeypatch.setattr(product._Product, "content", changed)
        result = install(desk)
    assert not result["ok"] and not result["created"]
    assert result["diagnostics"] == ["install_manifest_conflict" if change == "root" else "install_source_changed"]
    assert tree(home) == before


def test_incomplete_event_manifest_never_reports_healthy_or_adopts(desk):
    home, repo = desk
    assert install(desk)["ok"]
    state = engine(desk)
    value = state.read()
    value["artifacts"]["settings"]["hashes"].pop("PostToolUse")
    state.write(value)
    before = tree(home)
    assert not product.product_doctor(SPECS[0], home=home, repo_root=repo)["installed"]
    report = install(desk)
    assert not report["ok"] and report["diagnostics"] == ["install_incomplete_manifest"]
    assert tree(home) == before


@pytest.mark.parametrize("value", [b'{"a":1,"a":2}', b'{"hooks":null}', b'{"a":NaN}', b'[]', b'\xff'])
def test_invalid_settings_are_not_repaired(desk, value):
    home, _ = desk
    seed(home, ".claude/settings.json", value)
    before = tree(home)
    assert not install(desk)["ok"]
    assert tree(home) == before


@pytest.mark.parametrize("value", [b"```\nunclosed", b"<!-- unclosed", b"---\nunclosed", b"\xff",
    b"<!-- friction-lantern:begin -->\nforeign", b"x" * product.MAX_BYTES])
def test_unappendable_agents_are_not_changed(desk, value):
    home, _ = desk
    seed(home, ".codex/AGENTS.md", value)
    before = tree(home)
    assert not install(desk)["ok"]
    assert tree(home) == before


@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_real_atomic_manifest_fsync_uncertainty(desk, monkeypatch, phase, action):
    home, repo = desk
    if action == "uninstall":
        assert install(desk)["ok"]
    atomic, fsync = product.atomic_write, os.fsync
    writing = False
    failures = []
    def atomic_guard(*args, **kwargs):
        nonlocal writing
        writing = True
        try:
            return atomic(*args, **kwargs)
        finally:
            writing = False
    def fsync_fault(fd):
        if writing and stat.S_ISDIR(os.fstat(fd).st_mode) == (phase == "after_replace"):
            failures.append(phase)
            raise OSError("PRIVATE_FSYNC")
        return fsync(fd)
    monkeypatch.setattr(product, "atomic_write", atomic_guard)
    monkeypatch.setattr(os, "fsync", fsync_fault)
    kwargs = {"dry_run": False} if action == "install" else {}
    report = getattr(product, "product_" + action)(SPECS[0], home=home, repo_root=repo, **kwargs)
    assert failures == [phase]
    assert not report["ok"] and not report.get("created", False)
    installed = (action == "install") == (phase == "after_replace")
    assert all(os.path.lexists(path) == installed for path in engine(desk).targets.values())
    if phase == "after_replace":
        assert report["diagnostics"] == ["install_manifest_durability_unverified"]
    else:
        assert report["changed"] == []
    assert "PRIVATE_FSYNC" not in json.dumps(report)


@pytest.mark.parametrize("foreign", [False, True])
def test_host_rollback_preserves_foreign_concurrent_changes(desk, monkeypatch, foreign):
    home, _ = desk
    put = product.host_put
    wrapper = engine(desk).targets["wrapper"]
    def failing(home, path, before, after, directories):
        if path.name == "settings.json":
            if foreign:
                wrapper.write_bytes(b"FOREIGN_CONCURRENT")
            raise OSError("PRIVATE_FAILURE")
        return put(home, path, before, after, directories)
    monkeypatch.setattr(product, "host_put", failing)
    report = install(desk)
    assert not report["ok"] and not report["created"]
    assert engine(desk).read() is None
    if foreign:
        assert "install_rollback_incomplete" in report["diagnostics"]
        assert wrapper.read_bytes() == b"FOREIGN_CONCURRENT"
    else:
        assert report["changed"] == [] and not wrapper.exists()


def test_host_compare_and_replace_rejects_concurrent_settings(desk, monkeypatch):
    home, _ = desk
    path = seed(home, ".claude/settings.json", b'{"model":"original"}')
    put = product.host_put
    foreign = b'{"model":"foreign"}'
    def conflicting(home, target, before, after, directories):
        if target == path:
            target.write_bytes(foreign)
        return put(home, target, before, after, directories)
    monkeypatch.setattr(product, "host_put", conflicting)
    report = install(desk)
    assert not report["ok"] and report["changed"] == []
    assert report["diagnostics"] == ["install_concurrent_change"]
    assert path.read_bytes() == foreign


def test_common_host_lock_timeout_keeps_everything(desk, monkeypatch):
    home, _ = desk
    assert install(desk)["ok"]
    lock = home / ".local/state/lantern-common/.host-settings.lock"
    before = tree(home)
    monkeypatch.setattr(product, "INSTALL_LOCK_SECONDS", 0.02)
    with lock.open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        report = install(desk)
        assert time.monotonic() - started < 0.5
    assert not report["ok"] and report["diagnostics"] == ["install_lock_timeout"]
    assert tree(home) == before


@pytest.mark.parametrize("target", ["directory", "manifest", "link", "hardlink"])
def test_private_state_refuses_insecure_objects_without_repair(desk, target):
    home, _ = desk
    assert install(desk)["ok"]
    state = engine(desk).state
    manifest = state / "installation.json"
    if target == "directory":
        state.chmod(0o755)
    elif target == "manifest":
        manifest.chmod(0o644)
    else:
        original = manifest.read_bytes()
        manifest.unlink()
        outside = seed(home, "outside", original)
        outside.chmod(0o600)
        manifest.symlink_to(outside) if target == "link" else os.link(outside, manifest)
    before = tree(home)
    assert not install(desk)["ok"]
    assert tree(home) == before


def test_wrapper_and_hook_quotes_are_isolated_not_shell_data(desk):
    home, repo = desk
    assert install(desk)["ok"]
    state = engine(desk)
    wrapper = state.targets["wrapper"]
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(home / "attacker")}
    result = subprocess.run([str(wrapper), "argument 'with' spaces"], cwd=home, env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"args": ["argument 'with' spaces"], "isolated": 1, "no_site": 1, "no_bytecode": True}
    settings = json.loads(state.targets["settings"].read_bytes())
    for event, groups in settings["hooks"].items():
        command = groups[0]["hooks"][0]["command"]
        assert shlex.split(command)[1:4] == ["-I", "-S", "-B"]
        result = subprocess.run(["/bin/sh", "-c", command], cwd=home, env=env,
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0
        assert json.loads(result.stdout)["args"] == [event]
    assert not (home / "NO_EXEC").exists()
    assert not list(repo.rglob("__pycache__"))
