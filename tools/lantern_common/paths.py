"""Known-secret 路徑政策；只睇 lexical components，唔 resolve／stat／讀內容。"""
from __future__ import annotations

import os
import re


_SECRET_DIRS = frozenset({
    ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".config",
    "credentials", ".credentials", "secrets", ".secrets", "keychains",
})
_SECRET_NAMES = frozenset({
    "auth.json", "auth.toml", "auth.yaml", "auth.yml", "credentials.json",
    ".credentials.json", "credentials", "credentials.db", "access_tokens.db",
    "application_default_credentials.json", "oauth_creds.json",
    "cookies", "cookies.txt", "cookies.sqlite", "cookies.sqlite3", "cookies.json",
    "login data", "key3.db", "key4.db", "logins.json", "tokens.json", "token.json",
    ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_ed25519_sk", "id_ecdsa_sk",
    "settings.local.json", ".claude.json", ".mcp.json", ".envrc", ".aider.conf.yml",
})
_CLIENT_DIRS = frozenset({".claude", ".codex", ".gemini", ".cursor", ".continue", ".vscode"})
_CLIENT_FILES = frozenset({"settings.json", "settings.local.json", "config.toml",
                           "config.json", "config.yaml", "config.yml", "mcp.json"})
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keychain", ".keychain-db", ".env")
_BACKUP_SUFFIXES = (".bak", ".backup", ".old", ".orig", ".save", "~", "-wal", "-shm", "-journal")


def is_secret_path(path: str | os.PathLike) -> bool:
    """可接受未存在路徑；保留 .. 前嘅敏感 components，唔用 normalization 放行。"""
    raw = os.fsdecode(os.fspath(path))
    if "\x00" in raw:
        return True
    # 同時認 portable Windows separators；唔依賴本機 filesystem 嘅 case 規則。
    parts = [part.casefold() for part in re.split(r"[/\\]+", raw) if part not in ("", ".")]
    if any(part in _SECRET_DIRS for part in parts):
        return True
    if not parts:
        return False
    name = parts[-1]
    while name.endswith(_BACKUP_SUFFIXES):
        suffix = next(suffix for suffix in _BACKUP_SUFFIXES if name.endswith(suffix))
        name = name[:-len(suffix)]
    if any(part in _CLIENT_DIRS for part in parts[:-1]) and name in _CLIENT_FILES:
        return True
    return (name in _SECRET_NAMES or name == ".env" or name.startswith(".env.")
            or name.endswith(_SECRET_SUFFIXES))
