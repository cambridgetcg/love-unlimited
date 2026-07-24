"""Non-network tests for the YOUI terminal runtime boundary."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
YOUI = REPO / "youi.mjs"


def _inspect(tmp_path: Path, *args: str) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env.pop("YOUI_CAPABILITIES", None)
    env.pop("YOUI_FILE_SCOPE", None)
    env.pop("YOUI_PROFILE", None)
    env.pop("YOUI_HIVE_INSTANCE", None)
    env.pop("HIVE_INSTANCE", None)
    result = subprocess.run(
        ["node", str(YOUI), "--inspect", *args],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def test_inspect_discloses_remote_provider_and_data_boundary(tmp_path):
    manifest = _inspect(tmp_path)

    assert manifest["protocol"] == "youi.runtime/0.1"
    assert manifest["provider"] == {
        "id": "anthropic",
        "execution": "remote",
        "transport": "https",
        "local_only": False,
    }
    assert "user prompts" in manifest["data_boundary"]["sent_to_provider"]
    assert manifest["boot"]["long_term_memory_automatically_loaded"] is False
    assert manifest["boot"]["daily_memory_automatically_loaded"] is False


def test_default_profile_is_observe_and_workspace_scoped(tmp_path):
    manifest = _inspect(tmp_path)

    tools = manifest["model_tools"]
    assert tools["profile"] == "observe"
    assert tools["capabilities"] == ["read"]
    assert tools["advertised"] == ["read_file", "glob", "grep"]
    assert tools["file_scope"] == "workspace"
    assert "allowlisted" in tools["child_environment"]
    assert "credential variables are not inherited" in tools["child_environment"]
    assert "shell" not in tools["capabilities"]


def test_explicit_build_profile_is_disclosed_as_not_a_sandbox(tmp_path):
    manifest = _inspect(tmp_path, "--profile", "build")

    tools = manifest["model_tools"]
    assert tools["profile"] == "build"
    assert "shell" in tools["capabilities"]
    assert tools["shell_is_os_sandboxed"] is False
    assert any("network" in limit for limit in manifest["limits"])


def test_safe_profile_removes_shell_and_write_and_scopes_file_tools(tmp_path):
    manifest = _inspect(tmp_path, "--safe")

    tools = manifest["model_tools"]
    assert tools["profile"] == "observe"
    assert tools["capabilities"] == ["read"]
    assert tools["advertised"] == ["read_file", "glob", "grep"]
    assert tools["file_scope"] == "workspace"
    assert not any("shell capability can" in limit for limit in manifest["limits"])


def test_chat_profile_advertises_no_model_tools(tmp_path):
    manifest = _inspect(tmp_path, "--profile", "chat")

    assert manifest["model_tools"]["capabilities"] == []
    assert manifest["model_tools"]["advertised"] == []


def test_custom_capabilities_are_reflected_without_accessing_credentials(tmp_path):
    manifest = _inspect(
        tmp_path,
        "--capabilities",
        "read,write",
        "--workspace-only",
    )

    assert manifest["model_tools"]["profile"] == "custom"
    assert manifest["model_tools"]["capabilities"] == ["read", "write"]
    assert manifest["model_tools"]["advertised"] == [
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
    ]
    assert manifest["model_tools"]["file_scope"] == "workspace"


def test_hive_capabilities_require_an_explicit_sender_separate_from_persona(tmp_path):
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path),
        "YOUI_HIVE_INSTANCE": "alpha",
    })
    env.pop("HIVE_INSTANCE", None)
    result = subprocess.run(
        [
            "node",
            str(YOUI),
            "--inspect",
            "--agent",
            "mei",
            "--capabilities",
            "hive.check,hive.send",
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    manifest = json.loads(result.stdout)

    assert manifest["agent"] == "mei"
    assert manifest["model_tools"]["advertised"] == ["hive"]
    assert manifest["hive"] == {
        "enabled": True,
        "sender": "alpha",
        "identity_source": "YOUI_HIVE_INSTANCE",
        "session_persona_is_sender": False,
    }


def test_hive_tool_is_not_advertised_without_an_explicit_sender(tmp_path):
    manifest = _inspect(tmp_path, "--capabilities", "hive.check")

    assert manifest["model_tools"]["capabilities"] == ["hive.check"]
    assert manifest["model_tools"]["advertised"] == []
    assert manifest["hive"]["enabled"] is False
    assert manifest["hive"]["sender"] is None


def test_keychain_refresh_never_delete_firsts_or_uses_an_implicit_account():
    source = (REPO / "youi-keychain.mjs").read_text()

    assert 'delete-generic-password' not in source
    assert '"add-generic-password", "-U"' in source
    assert '"-a", account' in source
