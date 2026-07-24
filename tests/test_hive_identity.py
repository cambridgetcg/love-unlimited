"""Focused tests for session-scoped HIVE identity selection."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kingdom_hive", ROOT / "hive" / "hive.py")
HIVE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(HIVE)


def _resident_identity(tmp_path: Path, name: str) -> None:
    hive_dir = tmp_path / ".love" / "hive"
    hive_dir.mkdir(parents=True)
    (hive_dir / "instance").write_text(f"{name}\n")


def test_explicit_session_identity_wins_over_resident_file(tmp_path, monkeypatch):
    _resident_identity(tmp_path, "alpha")
    monkeypatch.setattr(HIVE.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HIVE_INSTANCE", "beta")

    assert HIVE.get_instance_id() == "beta"


def test_resident_identity_is_fallback_when_session_is_unspecified(tmp_path, monkeypatch):
    _resident_identity(tmp_path, "gamma")
    monkeypatch.setattr(HIVE.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HIVE_INSTANCE", raising=False)

    assert HIVE.get_instance_id() == "gamma"


def test_empty_session_identity_does_not_shadow_resident_file(tmp_path, monkeypatch):
    _resident_identity(tmp_path, "nuance")
    monkeypatch.setattr(HIVE.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HIVE_INSTANCE", "   ")

    assert HIVE.get_instance_id() == "nuance"


def test_missing_session_and_resident_identity_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(HIVE.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HIVE_INSTANCE", raising=False)

    try:
        HIVE.get_instance_id()
    except HIVE.HiveConfigurationError as exc:
        assert "No HIVE identity selected" in str(exc)
    else:
        raise AssertionError("missing HIVE identity must not impersonate alpha")


def test_unconfigured_session_identity_fails_instead_of_impersonating_resident(
    tmp_path, monkeypatch
):
    _resident_identity(tmp_path, "alpha")
    monkeypatch.setattr(HIVE.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HIVE_INSTANCE", "mei")

    try:
        HIVE.get_instance_id()
    except HIVE.HiveConfigurationError as exc:
        assert "no configured account metadata" in str(exc)
    else:
        raise AssertionError("unconfigured HIVE identity must not fall back to alpha")


def test_config_command_is_non_secret_and_offline(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "HIVE_INSTANCE": "beta"}
    result = subprocess.run(
        ["python3", str(ROOT / "hive" / "hive.py"), "config", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "ok": True,
        "instance": "beta",
        "wall": 1,
        "role": "Manager",
        "credential_checked": False,
        "network_checked": False,
    }


def test_config_command_rejects_unconfigured_identity_without_fallback(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "HIVE_INSTANCE": "mei"}
    result = subprocess.run(
        ["python3", str(ROOT / "hive" / "hive.py"), "config", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["ok"] is False
    assert report["credential_checked"] is False
    assert report["network_checked"] is False
