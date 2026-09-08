"""用真共用 ProductSpec helper，但所有接線只落 fake HOME／tmp source。"""
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from .test_friction_lantern_core import REPO
from .test_friction_lantern_hooks import environment, file_tree, write_private
from friction_lantern.install import SPEC, doctor, install, uninstall


@pytest.fixture
def installation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = tmp_path / "source"
    (repo / "tools/hooks").mkdir(parents=True, mode=0o700)
    (repo / ".git").mkdir(mode=0o700)
    for package in ("friction_lantern", "reality_lantern", "lantern_common"):
        shutil.copytree(REPO / "tools" / package, repo / "tools" / package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(REPO / "tools/kingdom-friction.py", repo / "tools/kingdom-friction.py")
    shutil.copyfile(REPO / "tools/hooks/friction-lantern.py", repo / "tools/hooks/friction-lantern.py")
    monkeypatch.setenv("HOME", str(home))
    return home, repo


def test_spec_is_thin_contract_and_only_supported_default_events():
    assert (SPEC.product_id, SPEC.package, SPEC.command, SPEC.skill) == (
        "friction-lantern", "friction_lantern", "kingdom-friction", "friction-lantern")
    assert SPEC.events == {"SessionStart": None, "UserPromptSubmit": None, "PostToolUse": "Bash"}
    assert "soft" in SPEC.guide and "唔為亮燈加跑命令" in SPEC.guide


def test_dryrun_and_uninstalled_doctor_create_nothing(installation):
    home, repo = installation
    before = file_tree(home)
    value = install(home=home, repo_root=repo, dry_run=True)
    assert value["ok"] and not value["created"]
    assert not doctor(home=home, repo_root=repo)["installed"]
    assert file_tree(home) == before and not list(home.iterdir())


def test_install_idempotent_nonce_doctor_wrapper_and_uninstall(installation):
    home, repo = installation
    nonce = str(uuid.uuid4())
    value = install(home=home, repo_root=repo, dry_run=False, installation_nonce=nonce)
    assert value["ok"] and value["created"] and value["installation_nonce"] == nonce
    assert value["activation"] == "unverified"
    after = file_tree(home)
    again = install(home=home, repo_root=repo, dry_run=False)
    assert again["ok"] and not again["created"] and not again["changed"]
    assert again["installation_nonce"] == nonce
    assert file_tree(home) == after
    health = doctor(home=home, repo_root=repo)
    assert health["installed"] and health["activation"] == "unverified"
    wrapper = home / ".local/bin/kingdom-friction"
    result = subprocess.run([str(wrapper), "status", "--cwd", str(repo), "--json"],
                            capture_output=True, timeout=2, env=environment(home), cwd=repo)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)["pattern"] is None
    assert not (home / ".local/state/reality-lantern").exists()
    manifest = json.loads((home / ".local/state/friction-lantern/installation.json").read_bytes())
    assert manifest["schema"] == "kingdom.friction-lantern.installation/2"
    wrong = uninstall(home=home, repo_root=repo, expected_nonce=str(uuid.uuid4()))
    assert not wrong["ok"] and "install_nonce_mismatch" in wrong["diagnostics"]
    assert wrapper.exists()
    assert uninstall(home=home, repo_root=repo, expected_nonce=nonce)["ok"]
    assert not wrapper.exists()
    assert not doctor(home=home, repo_root=repo)["installed"]


def test_install_preserves_foreign_settings_guides_and_delivery(installation):
    home, repo = installation
    settings = {"model": "ROUTING-CANARY", "permissions": {"allow": ["EXISTING-CANARY"]},
                "statusLine": {"type": "command", "command": "STATUS-CANARY"},
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "FOREIGN-CANARY"}]}]}}
    write_private(home / ".claude/settings.json", json.dumps(settings).encode())
    guide = b"# Existing\nFOREIGN-CANARY\n"
    write_private(home / ".codex/AGENTS.md", guide)
    delivery = b'{"fixture":"DELIVERY-CANARY"}'
    write_private(home / ".local/state/friction-lantern/delivery.json", delivery)
    assert install(home=home, repo_root=repo, dry_run=False)["ok"]
    merged = json.loads((home / ".claude/settings.json").read_bytes())
    for field in ("model", "permissions", "statusLine"):
        assert merged[field] == settings[field]
    assert merged["hooks"]["SessionStart"][0] == settings["hooks"]["SessionStart"][0]
    assert merged["hooks"]["PostToolUse"][0]["matcher"] == "Bash"
    assert "PreToolUse" not in merged["hooks"]
    assert uninstall(home=home, repo_root=repo)["ok"]
    assert json.loads((home / ".claude/settings.json").read_bytes()) == settings
    assert (home / ".codex/AGENTS.md").read_bytes() == guide
    assert (home / ".local/state/friction-lantern/delivery.json").read_bytes() == delivery


def test_foreign_wrapper_is_never_replaced(installation):
    home, repo = installation
    wrapper = home / ".local/bin/kingdom-friction"
    write_private(wrapper, b"FOREIGN-CANARY")
    before = file_tree(home)
    value = install(home=home, repo_root=repo, dry_run=False)
    assert not value["ok"] and not value["created"]
    assert "CANARY" not in json.dumps(value)
    assert file_tree(home) == before


def test_uninstall_keeps_modified_owned_wrapper(installation):
    home, repo = installation
    assert install(home=home, repo_root=repo, dry_run=False)["ok"]
    wrapper = home / ".local/bin/kingdom-friction"
    wrapper.write_bytes(b"FOREIGN-CANARY")
    result = uninstall(home=home, repo_root=repo)
    assert not result["ok"]
    assert wrapper.read_bytes() == b"FOREIGN-CANARY"


def test_cli_install_dryrun_and_doctor_use_fake_home(installation):
    home, repo = installation
    import sys
    cli = repo / "tools/kingdom-friction.py"
    for args in (("install", "--dry-run", "--json"), ("doctor", "--json")):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(cli), "--home", str(home), *args],
                                capture_output=True, timeout=2, env=environment(home), cwd=repo)
        assert result.returncode == 0 and not result.stderr
        assert json.loads(result.stdout)["activation"] == "unverified"
    assert not list(home.iterdir())
