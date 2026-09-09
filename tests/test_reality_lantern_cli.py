"""實況燈 CLI 邊界；只用 fake modules／自建 HOME，唔碰真設定。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import subprocess
import sys
import types

import pytest


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "tools/kingdom-reality.py"
spec = spec_from_file_location("kingdom_reality_cli", CLI)
cli = module_from_spec(spec)
spec.loader.exec_module(cli)


def test_help_does_not_create_home_state(tmp_path):
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(CLI),
                             "--home", str(tmp_path), "--help"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "check" in result.stdout and "observe" in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("arguments", [
    ["TOKEN-CANARY"], ["brief", "--TOKEN-CANARY"],
    ["check", "--kind", "TOKEN-CANARY", "--", "true"],
    ["status", "--cwd", "TOKEN-CANARY"],
    ["brief", "--max-bytes", "TOKEN-CANARY"],
    ["brief", "--max-bytes", "1"], ["brief", "--max-bytes", "99999"],
    ["check"], ["check", "--"], ["check", "TOKEN-CANARY"],
])
def test_argument_errors_do_not_echo_values(tmp_path, arguments):
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(CLI),
                             "--home", str(tmp_path), *arguments],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "TOKEN-CANARY" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert not list(tmp_path.iterdir())


def test_check_parser_keeps_original_argv():
    args = cli.parser().parse_args(["check", "--kind", "tests", "--cwd", "/tmp",
                                   "--", "python3", "-c", "print('a b')", "--home", "child"])
    assert args.argv == ["--", "python3", "-c", "print('a b')", "--home", "child"]
    assert args.cwd == Path("/tmp")
    assert args.kind == "tests"


def install_fake_modules(monkeypatch, store, *, capture=None, run=None, build=None):
    core = types.ModuleType("reality_lantern.core")
    core.build_status = build or (lambda state, **kwargs: {"state": state, **kwargs})
    core.render_brief = lambda status, **kwargs: "固定提示"
    storage = types.ModuleType("reality_lantern.store")
    storage.Store = lambda **kwargs: store
    snapshot = types.ModuleType("reality_lantern.snapshot")
    snapshot.scope_candidates = lambda path, **kwargs: ["near", "far"]
    snapshot.capture = capture or (lambda *args, **kwargs: pytest.fail("純讀唔可以 capture"))
    runner = types.ModuleType("reality_lantern.runner")
    runner.run_check = run or (lambda *args, **kwargs: pytest.fail("唔可以意外執行命令"))
    for module in (core, storage, snapshot, runner):
        monkeypatch.setitem(sys.modules, module.__name__, module)


class FakeStore:
    def __init__(self, scopes=None, key=b"x" * 32):
        self.scopes = {} if scopes is None else scopes
        self.key = key
        self.invalidations = []
        self.observed = []

    def get_key(self, *, create):
        assert create is False
        return self.key

    def read_state(self):
        return {"scopes": self.scopes}

    def invalidate(self, scope_id, *, changed):
        self.invalidations.append((scope_id, changed))

    def observe(self, snapshot, *, expected_generation):
        self.observed.append((snapshot, expected_generation))
        return {"ok": True}


def test_cached_scope_chooses_nearest_known_worktree(monkeypatch):
    store = FakeStore({"far": {"generation": 1}, "near": {"generation": 2}})
    install_fake_modules(monkeypatch, store)
    state, scope_id, key = cli.cached_scope(store, Path("/tmp"))
    assert scope_id == "near"
    assert state["scopes"][scope_id]["generation"] == 2
    assert key == store.key


def test_cached_missing_scope_does_not_borrow_other_evidence(monkeypatch):
    store = FakeStore({"unrelated": {"generation": 12}})
    install_fake_modules(monkeypatch, store)
    assert cli.cached_status(store, Path("/tmp")) == {"state": None, "scope_id": None}


def test_absent_key_is_pure_read(monkeypatch):
    store = FakeStore(key=None)
    store.read_state = lambda: pytest.fail("冇 key 唔讀其他 state")
    install_fake_modules(monkeypatch, store)
    assert cli.cached_scope(store, Path("/tmp")) == ({}, None, None)


def test_observe_without_receipt_does_not_scan_or_write(monkeypatch):
    store = FakeStore()
    install_fake_modules(monkeypatch, store)
    result = cli.observe_once(store, Path("/tmp"))
    assert result["ok"] and result["diagnostics"] == ["no_receipts"]
    assert not store.observed and not store.invalidations


@pytest.mark.parametrize("complete", [False, True])
def test_observe_carries_generation_and_incomplete_status(monkeypatch, complete):
    store = FakeStore({"near": {"generation": 7}})
    snapshot = {"scope_id": "near", "complete": complete, "diagnostics": []}
    install_fake_modules(monkeypatch, store, capture=lambda *a, **kw: snapshot)
    result = cli.observe_once(store, Path("/tmp"))
    assert result["ok"] is complete
    assert store.observed == [(snapshot, 7)]


def test_observe_changed_repo_identity_does_not_apply_to_old_scope(monkeypatch):
    store = FakeStore({"near": {"generation": 7}})
    install_fake_modules(monkeypatch, store,
                         capture=lambda *a, **kw: {"scope_id": "different"})
    result = cli.observe_once(store, Path("/tmp"))
    assert result["ok"] is False
    assert result["diagnostics"] == ["scope_changed"]
    assert not store.observed and store.invalidations == [("near", False)]


def test_check_runs_once_and_returns_child_status(monkeypatch, tmp_path):
    store = FakeStore()
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return 23

    install_fake_modules(monkeypatch, store, run=run)
    result = cli.main(["--home", str(tmp_path), "check", "--cwd", str(tmp_path),
                       "--kind", "build", "--", "program", "argument with spaces"])
    assert result == 23
    assert calls == [(["program", "argument with spaces"],
                      {"cwd": tmp_path, "kind": "build", "store": store})]


def test_untrusted_exception_is_not_printed(monkeypatch, capsys):
    store = FakeStore()
    store.get_key = lambda **kwargs: (_ for _ in ()).throw(OSError("SECRET-CANARY"))
    install_fake_modules(monkeypatch, store)
    assert cli.main(["status", "--cwd", "/tmp", "--json"]) == 1
    output = capsys.readouterr()
    assert "SECRET-CANARY" not in output.out + output.err
    assert json.loads(output.err) == {"ok": False, "error": "reality_operation_failed"}


def test_observe_incomplete_exit_is_nonzero(monkeypatch, capsys):
    store = FakeStore({"near": {"generation": 1}})
    install_fake_modules(monkeypatch, store, capture=lambda *a, **kw: {
        "scope_id": "near", "complete": False, "diagnostics": ["snapshot_limit"],
    })
    assert cli.main(["observe", "--cwd", "/tmp", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
