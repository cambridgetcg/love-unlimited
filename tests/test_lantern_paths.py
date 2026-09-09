"""Secret policy 係純 lexical；測試禁止任何 filesystem content／metadata IO。"""
import builtins
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from lantern_common.paths import is_secret_path


@pytest.mark.parametrize("path", [
    ".ssh/id_rsa", ".aws/config", ".azure/a", ".gnupg/a", ".kube/config", ".docker/config.json",
    ".config/any-client/settings.json", "credentials/a", ".credentials/a", "secrets/a", ".secrets/a",
    "Library/Keychains/login.keychain-db", "auth.json", "credentials.json", ".credentials.json",
    "cookies", "Cookies", "cookies.txt", "cookies.sqlite", "cookies.sqlite3", "cookies.json",
    "Login Data", "key4.db", "logins.json", "tokens.json", "token.json", ".netrc", "_netrc",
    ".npmrc", ".pypirc", ".git-credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "settings.local.json", ".claude/settings.json", ".codex/config.toml", ".claude.json",
    ".env", ".env.local", "key.pem", "key.key", "key.p12", "key.pfx", "key.keychain",
    ".gemini/settings.json", ".gemini/oauth_creds.json", ".cursor/mcp.json", ".continue/config.yaml",
    ".vscode/settings.json", ".mcp.json", ".envrc", "production.env", "auth.json.bak",
    "cookies.sqlite-wal", "Login Data-journal", ".claude.json.backup", "settings.local.json~",
    "application_default_credentials.json", "credentials.db", "access_tokens.db", "id_ed25519_sk",
    ".aws/../ordinary.py", r"C:\Users\test\.CLAUDE\SETTINGS.JSON", Path(".codex/auth.json"),
    "invalid\x00path",
])
def test_known_credentials_and_client_settings_are_secret(path):
    assert is_secret_path(path)


@pytest.mark.parametrize("path", [
    "", ".", "/", "main.py", "tools/lantern_common/paths.py", "tests/test_auth.py",
    "docs/auth.md", "src/auth/handler.py", "settings.json", "config.toml", "requirements.txt",
    ".claude/skills/example/SKILL.md", ".codex/AGENTS.md", "cookie_policy.md", "environment.py",
    "credentials-example.json", "public_key.txt", "docs/secrets-policy.md",
])
def test_ordinary_source_and_guides_stay_observable(path):
    assert not is_secret_path(path)


def test_policy_does_not_read_stat_resolve_or_expand_home(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("純 lexical policy 唔可以做 IO")
    with monkeypatch.context() as patch:
        for owner, name in ((builtins, "open"), (os, "open"), (os, "stat"), (os, "lstat"),
                            (Path, "resolve"), (Path, "stat"), (Path, "expanduser"),
                            (Path, "read_bytes"), (Path, "read_text")):
            patch.setattr(owner, name, forbidden)
        assert is_secret_path("~/not-existing/.claude/settings.json")
        assert is_secret_path(Path("/missing/.env"))
        assert not is_secret_path("/missing/source.py")
