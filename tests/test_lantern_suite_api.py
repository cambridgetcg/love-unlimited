"""固定 catalog／只讀 API／CLI；冇 host runtime 試驗。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from lantern_suite import api
from lantern_suite.catalog import catalog, installer


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("CODEX_HOME", str(path / ".codex"))
    return path


def test_catalog_fixed_and_imports_idle(home, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("catalog 唔准執行 runtime")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert [item["name"] for item in catalog()] == ["quota", "reality", "friction", "coedit"]
    for name in ("quota", "reality", "friction", "coedit", "entry"):
        assert callable(installer(name).install)
    with pytest.raises(ValueError):
        installer("os")
    assert list(home.iterdir()) == []


def test_status_never_observes_or_snapshots_or_creates(home, monkeypatch):
    from quota_lantern import sources
    from reality_lantern import snapshot
    def forbidden(*args, **kwargs):
        pytest.fail("只讀入口唔准 observe／snapshot／spawn")
    monkeypatch.setattr(sources, "collect_local", forbidden)
    monkeypatch.setattr(sources, "collect_live", forbidden)
    monkeypatch.setattr(snapshot, "capture", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    value = api.status(home=home, cwd=home)
    assert set(value["products"]) == {"quota", "reality", "friction", "coedit"}
    assert value["products"]["coedit"]["status"] == "unknown"
    assert value["products"]["coedit"]["reason"] == "session_required"
    assert value["products"]["reality"]["checks"] == []
    assert value["products"]["quota"]["sources"] == []
    text = api.brief(home=home, cwd=home)
    assert all(name in text for name in ("Quota", "Reality", "Friction", "Coedit", "unknown"))
    assert len(text.encode()) <= 1536
    assert list(home.iterdir()) == []


def test_bookkeeping_failure_isolated_fixed_diagnostic(home, monkeypatch):
    monkeypatch.setattr(api, "_quota", lambda *_: (_ for _ in ()).throw(OSError("PRIVATE_PROMPT")))
    result = api.status(home=home, cwd=home, session="PRIVATE_SESSION")
    assert result["products"]["quota"] == {"status": "unknown", "diagnostics": ["suite_product_unavailable"]}
    assert "PRIVATE" not in json.dumps(result)
    assert result["products"]["reality"]["checks"] == []
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("limit", [0, 100, 512, 768, 1536, 4096])
def test_brief_bounded_and_never_echoes_lowtrust(limit):
    value = {"products": {"quota": {"sources": [{"availability": "blocked", "pressure": "conserve", "prompt": "PRIVATE"}]},
                          "reality": {"recording": "full", "checks": [{"outcome": "nonzero", "validity": "stale"}]},
                          "friction": {"status": "pattern"}, "coedit": {"status": "changed"}}}
    text = api.render_brief(value, max_bytes=limit)
    assert len(text.encode()) <= limit
    assert "PRIVATE" not in text
    if limit >= 1536:
        assert "blocked 1" in text and "stale 1" in text and "收據已滿" in text and "觀察到改動" in text
    elif text:
        assert "完整警示未列" in text or "stale 1" in text


@pytest.mark.parametrize("command", [["list", "--json"], ["brief"], ["status", "--json"], ["doctor", "--json"],
                                     ["bundle", "allowlist"]])
def test_cli_readonly_fake_home(home, command):
    child = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / "tools/kingdom-lanterns.py"),
                            "--home", str(home), *command], cwd=home, capture_output=True, text=True,
                           env={**os.environ, "HOME": str(home)}, timeout=15)
    assert child.returncode == (1 if command[0] == "doctor" else 0), child.stdout + child.stderr
    assert child.stderr == ""
    assert list(home.iterdir()) == []


def test_cli_parse_failure_sanitizes_argument(home):
    child = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / "tools/kingdom-lanterns.py"),
                            "--home", str(home), "PRIVATE_COMMAND"], cwd=home, capture_output=True, text=True,
                           env={**os.environ, "HOME": str(home)}, timeout=15)
    assert child.returncode == 2
    assert "PRIVATE_COMMAND" not in child.stdout + child.stderr


def test_cli_bundle_install_delegates_to_trusted_bootstrap(home, monkeypatch, capsys):
    from lantern_suite import bundle
    from lantern_suite.cli import main
    boot = bundle.bootstrap()
    calls = []
    selected = home.parent / "target-home"
    selected.mkdir(mode=0o700)
    archive = home.parent / "bundle.tar"
    def install(path, **kwargs):
        calls.append((path, kwargs))
        return {"ok": True, "action": "install", "activation": "unverified"}
    monkeypatch.setattr(boot, "install", install)
    monkeypatch.setattr(bundle, "bootstrap", lambda: boot)
    args = ["--home", str(selected), "bundle", "install", "--bundle", str(archive),
            "--expected-sha256", "a" * 64]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert calls == [(archive, {"expected_sha256": "a" * 64, "home": selected})]
    assert list(home.iterdir()) == list(selected.iterdir()) == []


def test_cli_bundle_install_help_describes_verification_and_staging(home):
    child = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / "tools/kingdom-lanterns.py"),
                            "--home", str(home), "bundle", "install", "--help"], cwd=home,
                           capture_output=True, text=True, env={**os.environ, "HOME": str(home)}, timeout=15)
    assert child.returncode == 0, child.stdout + child.stderr
    assert all(token in child.stdout for token in ("--bundle", "--expected-sha256", "驗", "staging"))
    assert "--dry-run" not in child.stdout
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("case", ["missing_bundle", "missing_digest", "relative_bundle", "nonglobal_home", "dry_run"])
def test_cli_bundle_install_requires_explicit_paths_and_digest(home, capsys, case):
    from lantern_suite.cli import main
    args = ["--bundle", str(home.parent / "bundle.tar"), "--expected-sha256", "a" * 64]
    if case == "missing_bundle":
        args = args[2:]
    elif case == "missing_digest":
        args = args[:2]
    elif case == "relative_bundle":
        args[1] = "PRIVATE_RELATIVE.tar"
    elif case == "dry_run":
        args += ["--dry-run"]
    else:
        args += ["--home", str(home)]
    with pytest.raises(SystemExit) as error:
        main(["--home", str(home), "bundle", "install", *args])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "PRIVATE_RELATIVE" not in output.out + output.err
    assert list(home.iterdir()) == []


def test_quota_real_enums_and_unknown_coverage_not_hidden():
    value = {"products": {"quota": {"sources": [
        {"availability": "unknown", "pressure": "conserve", "freshness": "fresh"},
        {"availability": "allowed", "pressure": "mindful", "freshness": "stale"},
        {"availability": "blocked", "pressure": "roomy", "freshness": "unknown"}]}}}
    text = api.render_brief(value)
    assert "blocked 1" in text and "高壓 2" in text
    assert "availability unknown 1" in text and "非 fresh 2" in text
