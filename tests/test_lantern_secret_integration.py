"""Reality／Coedit 共用 secret path policy 嘅跨模組界線。"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from lantern_common.paths import is_secret_path
from reality_lantern import snapshot as snapshot_module
from reality_lantern.core import validate_snapshot


@pytest.mark.parametrize("relative", [
    ".claude/settings.json.bak",
    "nested/.codex/config.toml.old",
    "nested/.gemini/settings.json",
    "application_default_credentials.json",
    "oauth_creds.json",
    "cookies.sqlite-wal",
    ".netrc~",
    ".envrc",
    "service.env",
    "id_ed25519_sk",
])
def test_reality_never_reads_shared_policy_secret(tmp_path, monkeypatch, relative):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = tmp_path / "repo"
    repo.mkdir(mode=0o700)
    subprocess.run(
        ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "init", "-q", str(repo)],
        check=True, capture_output=True,
        env={
            "PATH": "/usr/bin:/bin", "HOME": str(home), "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    (repo / "plain.txt").write_text("普通測試輸入\n", encoding="utf-8")
    secret = repo / relative
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("synthetic-secret-canary-not-a-credential\n", encoding="utf-8")
    assert is_secret_path(secret)
    original = snapshot_module._read_regular
    opened = []

    def guarded(path, *args, **kwargs):
        path = Path(path)
        if path == secret:
            opened.append(path)
            raise AssertionError("唔應讀取 secret-policy fixture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(snapshot_module, "_read_regular", guarded)
    value = snapshot_module.capture(repo, key=b"L" * 32)
    validate_snapshot(value)
    assert value["complete"] is False
    assert value["fingerprint"] is None
    assert "credential_path" in value["diagnostics"]
    assert opened == []


def test_reality_secret_shim_uses_common_policy(monkeypatch):
    observed = []
    marker = Path("example")

    def policy(path):
        observed.append(path)
        return True

    monkeypatch.setattr(snapshot_module, "is_secret_path", policy)
    assert snapshot_module._secret(marker) is True
    assert observed == [marker]
