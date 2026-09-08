"""Suite ownership focused tests；每次只寫 fake HOME 同自建 source。"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from lantern_suite import entry
from lantern_suite import install as suite
from lantern_suite.catalog import NAMES, PRODUCTS, installer


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "source"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    packages = ("quota_lantern", "reality_lantern", "friction_lantern", "coedit_radar", "lantern_suite")
    commands = ("quota", "reality", "friction", "coedit", "lanterns")
    hooks = ("quota-lantern", "reality-lantern", "friction-lantern", "coedit-radar")
    (repo / "tools/hooks").mkdir(parents=True)
    for package, command in zip(packages, commands):
        (repo / "tools" / package / "skill").mkdir(parents=True)
        (repo / "tools" / package / "skill/SKILL.md").write_text("fixture\n")
        (repo / "tools" / ("kingdom-" + command + ".py")).write_text("pass\n")
    for hook in hooks:
        (repo / "tools/hooks" / (hook + ".py")).write_text("pass\n")
    return home, repo


def tree(home):
    return {str(path.relative_to(home)): (path.lstat().st_mode, path.lstat().st_ino,
            path.lstat().st_mtime_ns, os.readlink(path) if path.is_symlink()
            else path.read_bytes() if path.is_file() else None) for path in home.rglob("*")}


def call(desk, operation="install", **kwargs):
    return getattr(suite, operation)(home=desk[0], repo_root=desk[1], **kwargs)


def test_fresh_dryrun_and_readonly_doctor(desk):
    before = tree(desk[0])
    result = call(desk)
    assert result["ok"] and result["dry_run"] and set(result["products"]) == set(NAMES)
    assert call(desk, "doctor")["installed"] is False
    assert call(desk, "uninstall")["ok"]
    assert tree(desk[0]) == before


def test_fresh_four_plus_entry_and_idempotence(desk):
    result = call(desk, dry_run=False)
    assert result["ok"], result
    assert all(item["state"] == "created" for item in result["products"].values())
    assert len({item["installation_nonce"] for item in result["products"].values()}) == 5
    assert call(desk, "doctor")["installed"] is True
    ledger = suite.membership(home=desk[0])
    before = tree(desk[0])
    again = call(desk, dry_run=False)
    assert again["ok"] and suite.membership(home=desk[0]) == ledger
    assert tree(desk[0]) == before
    assert result["activation"] == "unverified"
    assert call(desk, "uninstall")["ok"]
    for name in NAMES:
        assert not installer(name).doctor(home=desk[0], repo_root=desk[1])["installed"]
    assert all(item["removal"] == "removed" for item in suite.membership(home=desk[0])["members"].values())
    assert call(desk, "uninstall")["ok"]
    assert call(desk, dry_run=False)["ok"]


def test_entry_never_reads_settings(desk):
    home, repo = desk
    (home / ".claude").mkdir(mode=0o700)
    settings = home / ".claude/settings.json"
    settings.write_text("PRIVATE_INVALID_SETTINGS")
    before = settings.read_bytes()
    assert entry.install(home=home, repo_root=repo, dry_run=False)["ok"]
    manifest = json.loads((home / ".local/state/lanterns/installation.json").read_bytes())
    assert "settings" not in manifest["artifacts"]
    assert settings.read_bytes() == before
    assert entry.doctor(home=home, repo_root=repo)["installed"]
    assert entry.uninstall(home=home, repo_root=repo)["ok"]
    assert settings.read_bytes() == before


def test_two_legacy_adopt_no_reinstall_and_uninstall_keeps(desk, monkeypatch):
    home, repo = desk
    snapshots = {}
    for name in ("quota", "reality"):
        adapter = installer(name)
        assert adapter.install(home=home, repo_root=repo, dry_run=False)["ok"]
        store = adapter.Store(home=home)
        manifest = store.read_cache("installation")
        manifest["schema"] = adapter.LEGACY_MANIFEST_SCHEMA
        del manifest["installation_nonce"]
        store.write_cache("installation", manifest)
        snapshots[name] = (home / (".local/state/" + name + "-lantern/installation.json")).read_bytes()
        def forbidden(**kwargs):
            pytest.fail("suite 唔准重裝健康 legacy")
        monkeypatch.setattr(adapter, "install", forbidden)
    result = call(desk, dry_run=False)
    assert result["ok"], result
    for name in ("quota", "reality"):
        assert result["products"][name]["state"] == "preexisting"
        assert result["products"][name]["installation_nonce"] is None
        assert (home / (".local/state/" + name + "-lantern/installation.json")).read_bytes() == snapshots[name]
    assert call(desk, "uninstall")["ok"]
    for name in ("quota", "reality"):
        assert installer(name).doctor(home=home, repo_root=repo)["installed"]
        assert (home / (".local/state/" + name + "-lantern/installation.json")).read_bytes() == snapshots[name]


def test_preexisting_entry_preserved(desk):
    assert entry.install(home=desk[0], repo_root=desk[1], dry_run=False)["ok"]
    assert call(desk, dry_run=False)["products"]["entry"]["state"] == "preexisting"
    assert call(desk, "uninstall")["ok"]
    assert entry.doctor(home=desk[0], repo_root=desk[1])["installed"]


def test_foreign_setting_model_routes_and_guide_preserved(desk):
    home, _ = desk
    (home / ".claude").mkdir(mode=0o700)
    settings = {"model": "ORIGINAL", "permissions": {"allow": ["Read"]}, "env": {"ROUTE": "ORIGINAL"},
                "statusLine": {"command": "ORIGINAL"}, "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "foreign"}]}]}}
    target = home / ".claude/settings.json"
    target.write_text(json.dumps(settings))
    target.chmod(0o600)
    (home / ".codex").mkdir(mode=0o700)
    agents = home / ".codex/AGENTS.md"
    agents.write_text("ORIGINAL GUIDE\n")
    agents.chmod(0o600)
    assert call(desk, dry_run=False)["ok"]
    merged = json.loads(target.read_bytes())
    assert {k: v for k, v in merged.items() if k != "hooks"} == {k: v for k, v in settings.items() if k != "hooks"}
    assert call(desk, "uninstall")["ok"]
    assert json.loads(target.read_bytes()) == settings
    assert agents.read_text() == "ORIGINAL GUIDE\n"


@pytest.mark.parametrize("name", NAMES)
def test_failed_product_stops_no_global_rollback(desk, monkeypatch, name):
    adapter = installer(name)
    monkeypatch.setattr(adapter, "install", lambda **_: {"ok": False, "created": False, "changed": [], "diagnostics": []})
    result = call(desk, dry_run=False)
    assert not result["ok"]
    ledger = suite.membership(home=desk[0])["members"]
    assert list(ledger) != [] and set(ledger) == set(NAMES[:NAMES.index(name) + 1])
    assert ledger[name]["state"] == "failed"
    for earlier in NAMES[:NAMES.index(name)]:
        assert installer(earlier).doctor(home=desk[0], repo_root=desk[1])["installed"]
    assert not call(desk, "uninstall")["ok"]


@pytest.mark.parametrize("phase", ["before_intent", "after_intent", "after_product", "silent"])
def test_durable_intent_and_crash_gap_never_infer_created(desk, monkeypatch, phase):
    real_write = suite._write
    writes = 0
    def write(fd, value):
        nonlocal writes
        writes += 1
        if phase == "before_intent" and writes == 1:
            raise OSError("PRIVATE_SECRET")
        if phase == "after_product" and writes == 2:
            raise OSError("PRIVATE_SECRET")
        real_write(fd, value)
        if phase == "after_intent" and writes == 1:
            raise OSError("PRIVATE_SECRET")
    with monkeypatch.context() as patch:
        if phase == "silent":
            patch.setattr(suite, "atomic_write", lambda *_, **__: None)
        else:
            patch.setattr(suite, "_write", write)
        result = call(desk, dry_run=False)
    assert not result["ok"] and "PRIVATE_SECRET" not in json.dumps(result)
    ledger = suite.membership(home=desk[0])["members"]
    if phase in ("after_intent", "after_product"):
        assert ledger["quota"]["state"] == "uncertain"
        assert not call(desk, dry_run=False)["ok"]
        assert not call(desk, "uninstall")["ok"]
    if phase == "after_product":
        assert installer("quota").doctor(home=desk[0], repo_root=desk[1])["installed"]
    else:
        assert not installer("quota").doctor(home=desk[0], repo_root=desk[1])["installed"]


def fail_membership_write(patch, fail_at, *, after_replace=False):
    """Only fault one suite publication; retain real product IO and atomic cleanup."""
    original = suite.atomic_write
    original_fsync = os.fsync
    writes = []
    def no_space(*args):
        raise OSError(errno.ENOSPC, "PRIVATE_BOOKKEEPING_ERROR")
    def write(fd, name, data, **kwargs):
        assert name == "membership.json"
        writes.append(json.loads(data))
        if len(writes) == fail_at:
            with patch.context() as fault:
                if after_replace:
                    fault.setattr(os, "fsync", lambda handle: no_space() if handle == fd else original_fsync(handle))
                else:
                    fault.setattr(os, "write", no_space)
                return original(fd, name, data, **kwargs)
        return original(fd, name, data, **kwargs)
    patch.setattr(suite, "atomic_write", write)
    return writes


def faulted_operation(desk, surface, operation, capsys):
    if surface == "api":
        return call(desk, operation, **({"dry_run": False} if operation == "install" else {}))
    from lantern_suite.cli import main
    code = main(["--home", str(desk[0]), operation, "--json"], repo_root=desk[1])
    output = capsys.readouterr()
    assert code == 1 and not output.err
    return json.loads(output.out)


@pytest.mark.parametrize("surface", ["api", "cli"])
@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_install_bookkeeping_write_failure_reports_progress(desk, monkeypatch, capsys, surface, fail_at):
    home, repo = desk
    adapter = installer("quota")
    original_install = adapter.install
    outcomes = []
    def install(**kwargs):
        outcome = original_install(**kwargs)
        outcomes.append(outcome)
        return outcome
    with monkeypatch.context() as patch:
        patch.setattr(adapter, "install", install)
        writes = fail_membership_write(patch, fail_at)
        report = faulted_operation(desk, surface, "install", capsys)
    assert len(writes) == fail_at
    assert not report["ok"] and report["activation"] == "unverified"
    assert report["diagnostics"] == ["suite_bookkeeping_unavailable"]
    assert "PRIVATE_BOOKKEEPING_ERROR" not in json.dumps(report)
    assert report["partial"] is (fail_at > 1)
    assert set(report["products"]) == ({"quota"} if fail_at > 1 else set())
    assert len(outcomes) == (1 if fail_at > 1 else 0)
    ledger = suite.membership(home=home)["members"]
    assert set(ledger) == ({"quota"} if fail_at > 1 else set())
    assert adapter.doctor(home=home, repo_root=repo)["installed"] is (fail_at > 1)
    for name in NAMES[1:]:
        assert not installer(name).doctor(home=home, repo_root=repo)["installed"]
    if fail_at > 1:
        assert outcomes[0]["ok"] and outcomes[0]["created"] is True
        assert outcomes[0]["changed"]
        assert outcomes[0]["installation_nonce"] == ledger["quota"]["requested_nonce"]
    if fail_at == 2:
        # Product commit is evidence of progress, not durable suite ownership.
        assert ledger["quota"]["state"] == "uncertain"
        assert ledger["quota"]["installation_nonce"] is None
        progress = report["products"]["quota"]
        assert progress == {**ledger["quota"], "bookkeeping": "uncertain",
                            "product_result": {"ok": True, "created": True}}
        before = tree(home)
        for operation, kwargs in (("install", {"dry_run": False}), ("uninstall", {})):
            stopped = call(desk, operation, **kwargs)
            assert not stopped["ok"] and stopped["diagnostics"] == ["suite_membership_uncertain"]
            assert tree(home) == before
    else:
        if fail_at == 3:
            assert ledger["quota"]["state"] == "created"
            assert report["products"]["quota"] == ledger["quota"]
        # No ambiguous publication: retry remains possible.
        assert call(desk, dry_run=False)["ok"]
        assert call(desk, "uninstall")["ok"]


@pytest.mark.parametrize("surface", ["api", "cli"])
@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_uninstall_bookkeeping_write_failure_reports_progress(desk, monkeypatch, capsys, surface, fail_at):
    home, repo = desk
    assert call(desk, dry_run=False)["ok"]
    original_uninstall = entry.uninstall
    outcomes = []
    def uninstall(**kwargs):
        outcome = original_uninstall(**kwargs)
        outcomes.append(outcome)
        return outcome
    with monkeypatch.context() as patch:
        patch.setattr(entry, "uninstall", uninstall)
        writes = fail_membership_write(patch, fail_at)
        report = faulted_operation(desk, surface, "uninstall", capsys)
    assert len(writes) == fail_at
    assert not report["ok"] and report["activation"] == "unverified"
    assert report["diagnostics"] == ["suite_bookkeeping_unavailable"]
    assert "PRIVATE_BOOKKEEPING_ERROR" not in json.dumps(report)
    assert set(report["products"]) == ({"entry", "coedit"} if fail_at == 3 else {"entry"})
    assert len(outcomes) == (1 if fail_at > 1 else 0)
    assert entry.doctor(home=home, repo_root=repo)["installed"] is (fail_at == 1)
    ledger = suite.membership(home=home)["members"]
    for name in NAMES[:-1]:
        assert installer(name).doctor(home=home, repo_root=repo)["installed"]
        assert ledger[name]["state"] == "created"
        assert ledger[name]["removal"] == "not_requested"
    assert ledger["entry"]["removal"] == {1: "not_requested", 2: "intent", 3: "removed"}[fail_at]
    if fail_at > 1:
        assert outcomes[0]["ok"] and report["partial"]
        assert outcomes[0]["installation_nonce"] == ledger["entry"]["installation_nonce"]
    if fail_at == 2:
        assert report["products"]["entry"] == {**ledger["entry"], "bookkeeping": "uncertain",
                                              "product_result": {"ok": True}}
        before = tree(home)
        for operation, kwargs in (("install", {"dry_run": False}), ("uninstall", {})):
            stopped = call(desk, operation, **kwargs)
            assert not stopped["ok"] and stopped["diagnostics"] == ["suite_membership_uncertain"]
            assert tree(home) == before
    else:
        assert report["products"]["entry"] == ledger["entry"]
        assert call(desk, "uninstall")["ok"]
        for name in NAMES:
            assert not installer(name).doctor(home=home, repo_root=repo)["installed"]


@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_bookkeeping_post_replace_failure_keeps_response_uncertain(desk, monkeypatch, operation):
    if operation == "uninstall":
        assert call(desk, dry_run=False)["ok"]
    with monkeypatch.context() as patch:
        writes = fail_membership_write(patch, 2, after_replace=True)
        report = call(desk, operation, **({"dry_run": False} if operation == "install" else {}))
    assert len(writes) == 2
    assert not report["ok"] and report["partial"]
    assert report["diagnostics"] == ["suite_bookkeeping_unavailable"]
    assert "PRIVATE_BOOKKEEPING_ERROR" not in json.dumps(report)
    name = "quota" if operation == "install" else "entry"
    progress = report["products"][name]
    assert set(report["products"]) == {name}
    assert progress["bookkeeping"] == "uncertain"
    assert progress["product_result"] == ({"ok": True, "created": True} if operation == "install" else {"ok": True})
    # Visible bytes after replace are not proof of the failed directory fsync.
    visible = suite.membership(home=desk[0])["members"][name]
    if operation == "install":
        assert visible["state"] == "created"
        assert progress["state"] == "uncertain" and progress["installation_nonce"] is None
        assert installer(name).doctor(home=desk[0], repo_root=desk[1])["installed"]
    else:
        assert visible["removal"] == "removed"
        assert progress["removal"] == "intent"
        assert not entry.doctor(home=desk[0], repo_root=desk[1])["installed"]
    for other in NAMES[1:-1]:
        assert installer(other).doctor(home=desk[0], repo_root=desk[1])["installed"] is (operation == "uninstall")


@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_product_exception_after_commit_reports_attempt_not_success(desk, monkeypatch, operation):
    if operation == "uninstall":
        assert call(desk, dry_run=False)["ok"]
    name = "quota" if operation == "install" else "entry"
    adapter = installer(name)
    original = getattr(adapter, operation)
    def no_result(**kwargs):
        assert original(**kwargs)["ok"]
        raise OSError("PRIVATE_PRODUCT_ERROR")
    with monkeypatch.context() as patch:
        patch.setattr(adapter, operation, no_result)
        report = call(desk, operation, **({"dry_run": False} if operation == "install" else {}))
    assert not report["ok"] and report["partial"]
    assert report["diagnostics"] == ["suite_bookkeeping_unavailable"]
    assert "PRIVATE_PRODUCT_ERROR" not in json.dumps(report)
    ledger = suite.membership(home=desk[0])["members"][name]
    assert set(report["products"]) == {name}
    assert report["products"][name] == {**ledger, "bookkeeping": "uncertain", "product_result": None}
    assert adapter.doctor(home=desk[0], repo_root=desk[1])["installed"] is (operation == "install")
    if operation == "install":
        assert ledger["state"] == "uncertain" and ledger["installation_nonce"] is None
    else:
        assert ledger["removal"] == "intent"
    before = tree(desk[0])
    for retry, kwargs in (("install", {"dry_run": False}), ("uninstall", {})):
        stopped = call(desk, retry, **kwargs)
        assert not stopped["ok"] and stopped["diagnostics"] == ["suite_membership_uncertain"]
        assert tree(desk[0]) == before


def test_concurrent_external_install_between_doctor_and_product_adopted(desk, monkeypatch):
    adapter = installer("quota")
    original = adapter.install
    external_nonce = str(uuid4())
    def racing(**kwargs):
        assert original(home=desk[0], repo_root=desk[1], dry_run=False, installation_nonce=external_nonce)["created"]
        return original(**kwargs)
    monkeypatch.setattr(adapter, "install", racing)
    result = call(desk, dry_run=False)
    assert result["ok"]
    assert result["products"]["quota"]["state"] == "preexisting"
    assert result["products"]["quota"]["installation_nonce"] == external_nonce
    assert call(desk, "uninstall")["ok"]
    assert adapter.doctor(home=desk[0], repo_root=desk[1])["installed"]


def test_reinstallation_nonce_and_foreign_wrapper_refuse_removal(desk):
    assert call(desk, dry_run=False)["ok"]
    before = suite.membership(home=desk[0])["members"]["entry"]["installation_nonce"]
    assert entry.uninstall(home=desk[0], repo_root=desk[1], expected_nonce=before)["ok"]
    new = entry.install(home=desk[0], repo_root=desk[1], dry_run=False)
    assert new["created"] and new["installation_nonce"] != before
    assert not call(desk)["ok"]
    wrapper = desk[0] / ".local/bin/kingdom-lanterns"
    content = wrapper.read_bytes()
    result = call(desk, "uninstall")
    assert not result["ok"] and wrapper.read_bytes() == content
    assert suite.membership(home=desk[0])["members"]["entry"]["removal"] == "uncertain"
    assert installer("quota").doctor(home=desk[0], repo_root=desk[1])["installed"]


def test_product_lock_nonce_recheck_not_suite_toctou(desk, monkeypatch):
    from lantern_common import product
    assert call(desk, dry_run=False)["ok"]
    original = product.host_settings_lock
    @contextmanager
    def race(*args, **kwargs):
        manifest = product._Product(entry.SPEC, desk[0], desk[1])
        value = manifest.read()
        value["installation_nonce"] = str(uuid4())
        manifest.write(value)
        with original(*args, **kwargs) as fd:
            yield fd
    monkeypatch.setattr(product, "host_settings_lock", race)
    assert not call(desk, "uninstall")["ok"]
    assert (desk[0] / ".local/bin/kingdom-lanterns").exists()


def test_unknown_ledger_fields_no_data_execution(desk):
    assert call(desk, dry_run=False)["ok"]
    path = desk[0] / ".local/state/lantern-suite/membership.json"
    value = json.loads(path.read_bytes())
    value["members"]["PRIVATE_MODULE"] = value["members"]["quota"]
    path.write_text(json.dumps(value))
    before = tree(desk[0])
    result = call(desk, "uninstall")
    assert not result["ok"] and "PRIVATE_MODULE" not in json.dumps(result)
    assert tree(desk[0]) == before


@pytest.mark.parametrize("target", ["membership.json", ".lock"])
def test_membership_symlink_rejected(desk, target):
    home = desk[0]
    folder = home / ".local/state/lantern-suite"
    folder.mkdir(mode=0o700, parents=True)
    canary = home / "canary"
    canary.write_text("PRIVATE_CANARY")
    (folder / target).symlink_to(canary)
    result = call(desk, dry_run=False)
    assert not result["ok"] and canary.read_text() == "PRIVATE_CANARY"
    assert not (home / ".local/bin/kingdom-quota").exists()


def test_foreign_entry_wrapper_partial_removal_preserved(desk):
    assert call(desk, dry_run=False)["ok"]
    wrapper = desk[0] / ".local/bin/kingdom-lanterns"
    wrapper.write_text("FOREIGN_WRAPPER")
    report = call(desk, "uninstall")
    assert not report["ok"] and report["partial"]
    assert wrapper.read_text() == "FOREIGN_WRAPPER"
    assert suite.membership(home=desk[0])["members"]["entry"]["removal"] == "uncertain"
    assert installer("quota").doctor(home=desk[0], repo_root=desk[1])["installed"]
    assert not call(desk, "uninstall")["ok"]


def test_uninstall_crash_gap_preserves_remaining_products(desk, monkeypatch):
    assert call(desk, dry_run=False)["ok"]
    original = suite._write
    writes = 0
    def crash(fd, value):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("PRIVATE_ERROR")
        original(fd, value)
    with monkeypatch.context() as patch:
        patch.setattr(suite, "_write", crash)
        report = call(desk, "uninstall")
    assert not report["ok"] and "PRIVATE_ERROR" not in json.dumps(report)
    assert suite.membership(home=desk[0])["members"]["entry"]["removal"] == "intent"
    assert not entry.doctor(home=desk[0], repo_root=desk[1])["installed"]
    assert not call(desk, "uninstall")["ok"]
    assert installer("quota").doctor(home=desk[0], repo_root=desk[1])["installed"]


def test_two_suite_processes_serialize_identity(desk):
    import subprocess
    home, repo = desk
    program = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
               "from lantern_suite.install import install;"
               "print(json.dumps(install(home=sys.argv[2],repo_root=sys.argv[3],dry_run=False)))")
    argv = [sys.executable, "-I", "-S", "-B", "-c", program, str(ROOT / "tools"), str(home), str(repo)]
    children = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={**os.environ, "HOME": str(home)}, cwd=home) for _ in range(2)]
    results = []
    for child in children:
        stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0 and not stderr
        results.append(json.loads(stdout))
    assert all(result["ok"] for result in results), results
    assert results[0]["products"] == results[1]["products"]
    assert call(desk, "uninstall")["ok"]
