"""只核對 suite owned 入口；唔讀產品 cache、執行命令或建立 state。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from lantern_common.install import host_parent
from lantern_common.json import decode_json, validate_plain
from lantern_common.private import private_read

GUIDE = (
    "# Lantern suite（advisory）\n"
    "suite 入口可用；唔代表四燈已啟用／健康。\n"
    "按需要用現有 bash 呼叫 `~/.local/bin/kingdom-lanterns status` 或 "
    "`~/.local/bin/kingdom-lanterns doctor`；唔強制檢查。\n"
    "只有已授權嘅本地檢查先用 `~/.local/bin/kingdom-reality check -- 原命令`，"
    "原命令只跑一次；普通 Bash exit0 唔係 pass。\n"
    "唔自動重驗、讀 credentials、改 model/routing/permissions/login；unknown 唔當 green。"
)
MAX_MANIFEST_BYTES = 16 * 1024
MAX_WRAPPER_BYTES = 16 * 1024
_SCHEMA = "kingdom.lanterns.installation/2"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def _valid_manifest(value):
    if (type(value) is not dict
            or set(value) != {"schema", "repo_root", "installation_nonce", "artifacts"}
            or value["schema"] != _SCHEMA):
        return False
    repo, nonce, artifacts = value["repo_root"], value["installation_nonce"], value["artifacts"]
    if (type(repo) is not str or not 1 <= len(repo.encode("utf-8")) <= 4096
            or not repo.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in repo)
            or (repo != "/" and any(p in ("", ".", "..") for p in repo.split("/")[1:]))
            or type(nonce) is not str or not _NONCE.fullmatch(nonce)
            or type(artifacts) is not dict or "wrapper" not in artifacts
            or not set(artifacts) <= {"wrapper", "claude_skill", "codex_skill", "agents"}):
        return False
    for key, item in artifacts.items():
        fields = {"owned", "sha256"}
        if key == "wrapper":
            fields.add("mode")
        elif key == "agents":
            fields.update({"existed", "separator"})
        if (type(item) is not dict or set(item) != fields or type(item["owned"]) is not bool
                or type(item["sha256"]) is not str or not _HASH.fullmatch(item["sha256"])):
            return False
        if key == "wrapper" and (item["owned"] is not True or type(item["mode"]) is not int or item["mode"] != 0o700):
            return False
        if key == "agents" and (item["owned"] is not True or type(item["existed"]) is not bool
                                or item["separator"] not in ("", "\n", "\n\n")):
            return False
    return True


def _read_owned(home, relative, mode, maximum, *, private_parent=False):
    path = home / relative
    with host_parent(home, path) as fd:
        if fd is None:
            return None
        if private_parent and stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            return None

        def inspect(directory, leaf):
            info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1):
                raise ValueError("unsafe_entry")
            return info

        return private_read(fd, path.name, maximum, file_info=inspect)


def installation_guide(*, home: Path | str | None = None) -> str:
    """回固定短指引或空字串；home 只係目標 HOME／離線測試 seam。"""
    try:
        root = Path.home() if home is None else Path(home)
        if not root.is_absolute() or root == Path("/") or ".." in root.parts:
            return ""
        data = _read_owned(root, ".local/state/lanterns/installation.json", 0o600,
                           MAX_MANIFEST_BYTES, private_parent=True)
        if data is None:
            return ""
        manifest = decode_json(
            data.decode("utf-8"), maximum=MAX_MANIFEST_BYTES,
            validator=lambda value: validate_plain(value, max_string_bytes=MAX_MANIFEST_BYTES,
                                                   max_dict_items=32, max_list_items=32, max_nodes=512),
        )
        if not _valid_manifest(manifest):
            return ""
        wrapper = _read_owned(root, ".local/bin/kingdom-lanterns", 0o700, MAX_WRAPPER_BYTES)
        if wrapper is None or hashlib.sha256(wrapper).hexdigest() != manifest["artifacts"]["wrapper"]["sha256"]:
            return ""
        # 收窄安裝／撤回交錯；唔宣稱之後執行時都冇 race。
        if _read_owned(root, ".local/state/lanterns/installation.json", 0o600,
                       MAX_MANIFEST_BYTES, private_parent=True) != data:
            return ""
        return GUIDE
    except Exception:
        # Discovery 失敗唔阻 boot，亦唔將未核實入口當可用。
        return ""
