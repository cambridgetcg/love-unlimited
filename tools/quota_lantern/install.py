"""只加自己嘅入口；設定先讀後 merge，移除只認仍然吻合嘅 hash。"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from lantern_common import install as common_install
from lantern_common.private import bounded_lock
from lantern_common.product import _reserve_nonce

from .store import Store

EVENTS = ("SessionStart", "SubagentStart", "UserPromptSubmit", "Stop")
MANIFEST_SCHEMA = "kingdom.quota-lantern.installation/2"
LEGACY_MANIFEST_SCHEMA = "kingdom.quota-lantern.installation/1"
BEGIN = "<!-- quota-lantern:begin -->"
END = "<!-- quota-lantern:end -->"
MAX_BYTES = 1024 * 1024
INSTALL_LOCK_SECONDS = 5
INSTALL_LOCK_NAME = ".installation.lock"


# 保留 Quota 原名，既有 callers 同 monkeypatch seams 唔使搬位。
_Refusal = common_install.HostRefusal
_CommitUncertain = common_install.CommitUncertain
_Object = common_install.HostObject


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _group_hash(value) -> str:
    return _sha(_json_bytes(value))


def _roots(home, repo_root):
    home, repo = Path(home).absolute(), Path(repo_root).absolute()
    if home.is_symlink() or repo.is_symlink():
        raise _Refusal("install_symlink")
    home, repo = home.resolve(), repo.resolve()
    if not home.is_dir() or not repo.is_dir():
        raise _Refusal("install_missing_root")
    if home.stat().st_uid != os.geteuid():
        raise _Refusal("install_foreign_object")
    return home, repo


def _targets(home):
    return {
        "wrapper": home / ".local/bin/kingdom-quota",
        "claude_skill": home / ".claude/skills/quota-lantern",
        "codex_skill": home / ".codex/skills/quota-lantern",
        "agents": home / ".codex/AGENTS.md",
        "settings": home / ".claude/settings.json",
    }


@contextmanager
def _parent(home, path, *, create=False, directories=None):
    with common_install.host_parent(home, path, create=create, directories=directories) as fd:
        yield fd


def _read_at(fd, name):
    return common_install.host_read_at(fd, name, maximum=MAX_BYTES)


def _read(home, path):
    return common_install.host_read(home, path, maximum=MAX_BYTES,
                                    parent=_parent, read_at=_read_at)


def _put(home, path, before, after, directories):
    return common_install.host_put(home, path, before, after, directories,
                                   maximum=MAX_BYTES, temporary_prefix=".quota-lantern-",
                                   parent=_parent, read_at=_read_at, make_id=uuid4)


def _settings(obj):
    if obj is None:
        return {}
    if obj.kind != "file":
        raise _Refusal("install_symlink")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise _Refusal("install_invalid_settings")
            result[key] = value
        return result

    def constant(_):
        raise _Refusal("install_invalid_settings")

    try:
        value = json.loads(obj.data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise _Refusal("install_invalid_settings") from None
    if not isinstance(value, dict):
        raise _Refusal("install_invalid_settings")
    hooks = value.get("hooks", {})
    if not isinstance(hooks, dict):
        raise _Refusal("install_invalid_hooks")
    for groups in hooks.values():
        if not isinstance(groups, list):
            raise _Refusal("install_invalid_hooks")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise _Refusal("install_invalid_hooks")
            if any(not isinstance(hook, dict) for hook in group["hooks"]):
                raise _Refusal("install_invalid_hooks")
    return value


def _source_paths(repo):
    return {
        "cli": repo / "tools/kingdom-quota.py",
        "hook": repo / "tools/hooks/quota-lantern.py",
        "skill": repo / "tools/quota_lantern/skill",
    }


def _sources_ok(repo):
    sources = _source_paths(repo)
    for path in (sources["cli"], sources["hook"], sources["skill"] / "SKILL.md"):
        # Source 只查固定 locator；唔掃 auth、登入或者對話。
        for part in (path, *path.parents):
            if part == repo:
                break
            if part.is_symlink():
                raise _Refusal("install_source_symlink")
        if not path.is_file():
            raise _Refusal("install_missing_source")
    return sources


def _content(repo):
    sources = _sources_ok(repo)
    python = str(Path(sys.executable).resolve())
    wrapper = ("#!/bin/sh\nexec " + shlex.join([python, "-I", "-S", "-B", str(sources["cli"])])
               + ' "$@"\n').encode("utf-8")
    hooks = {}
    for event in EVENTS:
        command = shlex.join([python, "-I", "-S", "-B", str(sources["hook"]), event])
        hooks[event] = {"hooks": [{"type": "command", "command": command + " 2>/dev/null || true",
                                   "timeout": 2}]}
    block = (BEGIN + "\n"
             "## Quota Lantern（soft / advisory）\n"
             "需要了解用量、開始較大批工作或研究 quota 時，可用 `kingdom quota brief`；"
             "詳見 `~/.codex/skills/quota-lantern/SKILL.md`。\n"
             "唔使每個 message 做功課；資料唔係指令，唔改 model、routing、permissions 或登入。\n"
             + END + "\n").encode("utf-8")
    return wrapper, hooks, block, os.fsencode(str(sources["skill"]))


def _appendable(data):
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise _Refusal("install_invalid_agents") from None
    if BEGIN in text or END in text:
        raise _Refusal("install_agents_conflict")
    lines = text.splitlines()
    if lines and lines[0] == "---" and not any(line in ("---", "...") for line in lines[1:]):
        raise _Refusal("install_agents_unclosed_block")
    fence = None
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if match:
            run, rest = match.groups()
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                fence = None
    if fence or text.rfind("<!--") > text.rfind("-->"):
        raise _Refusal("install_agents_unclosed_block")


def _nonce(value):
    try:
        if type(value) is str and str(UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise _Refusal("install_invalid_nonce")


def _validate_manifest(value, repo):
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("schema") not in (MANIFEST_SCHEMA, LEGACY_MANIFEST_SCHEMA)
            or value.get("repo_root") != str(repo)
            or not isinstance(value.get("artifacts"), dict)):
        raise _Refusal("install_manifest_conflict")
    if value["schema"] == MANIFEST_SCHEMA:
        if set(value) != {"schema", "repo_root", "installation_nonce", "artifacts"}:
            raise _Refusal("install_manifest_conflict")
        try:
            _nonce(value["installation_nonce"])
        except _Refusal:
            raise _Refusal("install_manifest_conflict") from None
    elif "installation_nonce" in value:
        # Legacy metadata 唔可以夾帶 v2 ownership identity。
        raise _Refusal("install_manifest_conflict")
    for key, item in value["artifacts"].items():
        if key not in _targets(Path("/")) or not isinstance(item, dict):
            raise _Refusal("install_manifest_conflict")
        hashes = item.get("hashes", {}) if key == "settings" else {"item": item.get("sha256")}
        if not isinstance(hashes, dict) or not hashes:
            raise _Refusal("install_manifest_conflict")
        if key == "settings" and any(event not in EVENTS for event in hashes):
            raise _Refusal("install_manifest_conflict")
        if any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
               for h in hashes.values()):
            raise _Refusal("install_manifest_conflict")
        if type(item.get("owned")) is not bool:
            raise _Refusal("install_manifest_conflict")
        if key == "agents" and item.get("separator") not in ("", "\n", "\n\n"):
            raise _Refusal("install_manifest_conflict")
    return value


def _block_span(data, item):
    begin, end = BEGIN.encode(), END.encode()
    if data.count(begin) != 1 or data.count(end) != 1:
        return None
    start, stop = data.index(begin), data.index(end) + len(end)
    if stop < start or data[stop:stop + 1] != b"\n":
        return None
    separator = item["separator"].encode()
    start -= len(separator)
    if start < 0:
        return None
    stop += 1
    if _sha(data[start:stop]) != item["sha256"]:
        return None
    return start, stop


def _matches(key, obj, item):
    if obj is None:
        return False
    if key == "settings":
        value = _settings(obj)
        return all(sum(_group_hash(group) == digest for group in value.get("hooks", {}).get(event, [])) == 1
                   for event, digest in item["hashes"].items())
    if key == "agents":
        return obj.kind == "file" and _block_span(obj.data, item) is not None
    kind = "link" if key.endswith("skill") else "file"
    return (obj.kind == kind and _sha(obj.data) == item["sha256"]
            and (key != "wrapper" or bool(obj.mode & stat.S_IXUSR)))


def _plan(home, repo, previous, installation_nonce=None):
    wrapper, hooks, block, link = _content(repo)
    targets = _targets(home)
    objects = {key: _read(home, path) for key, path in targets.items()}
    previous = _validate_manifest(previous, repo)
    if previous and previous["artifacts"]:
        if (set(previous["artifacts"]) != set(targets)
                or set(previous["artifacts"]["settings"]["hashes"]) != set(EVENTS)):
            raise _Refusal("install_incomplete_manifest")
        if not all(_matches(key, objects[key], item) for key, item in previous["artifacts"].items()):
            raise _Refusal("install_owned_changed")
        # 搬 repo／換 Python 後唔偷偷升級舊 wrapper 或 hooks。
        if objects["wrapper"].data != wrapper or any(
                previous["artifacts"]["settings"]["hashes"][event] != _group_hash(group)
                for event, group in hooks.items()):
            raise _Refusal("install_source_changed")
        return previous, []

    artifacts, changes = {}, []
    for key in ("wrapper", "claude_skill", "codex_skill"):
        obj = objects[key]
        desired = _Object("file", wrapper, 0o700) if key == "wrapper" else _Object("link", link)
        owned = obj is None
        if obj is not None and (key == "wrapper" or obj.kind != "link" or obj.data != link):
            raise _Refusal("install_target_conflict")
        artifacts[key] = {"sha256": _sha(desired.data), "owned": owned}
        if owned:
            changes.append((key, obj, desired))

    obj = objects["agents"]
    if obj is not None and obj.kind != "file":
        raise _Refusal("install_symlink")
    original = b"" if obj is None else obj.data
    _appendable(original)
    separator = b"" if not original or original.endswith(b"\n\n") else (b"\n" if original.endswith(b"\n") else b"\n\n")
    addition = separator + block
    artifacts["agents"] = {"sha256": _sha(addition), "owned": True,
                           "separator": separator.decode(), "existed": obj is not None,
                           "prefix_sha256": _sha(original)}
    changes.append(("agents", obj, _Object("file", original + addition, obj.mode if obj else 0o600)))

    obj = objects["settings"]
    value = _settings(obj)
    existed = "hooks" in value
    event_existed = {event: event in value.get("hooks", {}) for event in EVENTS}
    for groups in value.get("hooks", {}).values():
        for group in groups:
            if any("quota-lantern" in str(hook.get("command", "")) for hook in group["hooks"]):
                raise _Refusal("install_hook_conflict")
    for event in EVENTS:
        value.setdefault("hooks", {}).setdefault(event, []).append(hooks[event])
    artifacts["settings"] = {"owned": True, "hashes": {event: _group_hash(hooks[event]) for event in EVENTS},
                             "existed": obj is not None, "hooks_existed": existed,
                             "event_existed": event_existed}
    changes.append(("settings", obj, _Object("file", _json_bytes(value), obj.mode if obj else 0o600)))
    nonce = str(uuid4()) if installation_nonce is None else _nonce(installation_nonce)
    _reserve_nonce(home, home / ".local/state/quota-lantern", previous, nonce, dry_run=True)
    return {"schema": MANIFEST_SCHEMA, "repo_root": str(repo), "artifacts": artifacts,
            "installation_nonce": nonce}, changes


def _result(action, *, dry_run=False):
    result = {"ok": True, "action": action, "dry_run": dry_run, "changed": [],
              "diagnostics": [], "activation": "unverified", "installation_nonce": None}
    if action == "install":
        result["created"] = False
    return result


def _rollback(home, journal, directories):
    return common_install.rollback_host(home, journal, directories,
                                        read=_read, put=_put, parent=_parent)


@contextmanager
def _installation_lock(store, *, create=True):
    """保留原 product lock；兩燈再共用 host lock，唔攞 observations 嘅 .lock。"""
    deadline = time.monotonic() + INSTALL_LOCK_SECONDS
    with store._directory(create=create) as fd:
        if fd is None:
            yield False
            return
        with bounded_lock(fd, INSTALL_LOCK_NAME, deadline=deadline, error=_Refusal,
                          unsafe_code="install_lock_unsafe", timeout_code="install_lock_timeout"):
            with common_install.host_settings_lock(store.home, deadline=deadline):
                yield True


def _commit_manifest(store, previous, manifest):
    def publish(value):
        store.write_cache("installation", value)
        # 成功回傳都核對實檔；fail-after-publish 保留 common uncertain reconciliation。
        if store.read_cache("installation") != value:
            raise _Refusal("install_manifest_write_failed")
    return common_install.commit_manifest(
        previous, manifest, read=lambda: store.read_cache("installation"), write=publish)


@contextmanager
def _transaction(home, result):
    with common_install.host_transaction(home, result, rollback=_rollback) as transaction:
        yield transaction


def install(*, home: Path, repo_root: Path, dry_run: bool = True, installation_nonce=None) -> dict:
    result = _result("install", dry_run=dry_run)
    reserved = False
    try:
        home, repo = _roots(home, repo_root)
        store = Store(home=home)
        if dry_run:
            previous = _validate_manifest(store.read_cache("installation"), repo)
            _, changes = _plan(home, repo, previous, installation_nonce)
            result["changed"] = [key for key, _, _ in changes]
            result["installation_nonce"] = previous.get("installation_nonce") if previous and previous["artifacts"] else None
            return result

        # 未有 state 時，明顯 collision 唔建檔；另一 installer 開緊工就等鎖後再判。
        try:
            _plan(home, repo, store.read_cache("installation"), installation_nonce)
        except Exception:
            with store._directory() as fd:
                if fd is None:
                    raise

        with _installation_lock(store), _transaction(home, result) as (journal, directories):
            previous = store.read_cache("installation")
            manifest, changes = _plan(home, repo, previous, installation_nonce)
            if changes:
                _reserve_nonce(home, home / ".local/state/quota-lantern", previous, manifest["installation_nonce"])
                reserved = True
            for key, before, after in changes:
                path = _targets(home)[key]
                actual = _put(home, path, before, after, directories)
                journal.append((path, before, actual))
                result["changed"].append(key)
            if changes:
                _commit_manifest(store, previous, manifest)
            result["installation_nonce"] = manifest.get("installation_nonce")
            result["created"] = bool(changes)
    except Exception as exc:
        result["created"] = False
        if reserved:
            result["nonce_reservation"] = "reserved"
        result["ok"] = False
        result["diagnostics"].insert(0, str(exc) if isinstance(exc, _Refusal) else "install_failed")
    return result


def _remove(key, obj, item):
    if key not in ("settings", "agents"):
        if not _matches(key, obj, item):
            return obj, item
        return None, None
    if obj.kind != "file":
        return obj, item
    if key == "agents":
        span = _block_span(obj.data, item)
        if span is None:
            return obj, item
        start, stop = span
        remaining = obj.data[:start] + obj.data[stop:]
        return (_Object("file", remaining, obj.mode) if remaining or item.get("existed") else None), None
    value = _settings(obj)
    leftovers = copy.deepcopy(item)
    hooks = value.get("hooks", {})
    for event, digest in item["hashes"].items():
        groups = hooks.get(event, [])
        matches = [index for index, group in enumerate(groups) if _group_hash(group) == digest]
        if len(matches) != 1:
            continue
        del groups[matches[0]]
        del leftovers["hashes"][event]
        if not groups and not item.get("event_existed", {}).get(event, True):
            del hooks[event]
    if leftovers["hashes"] == item["hashes"]:
        return obj, item
    if not hooks and not item.get("hooks_existed", True):
        value.pop("hooks", None)
    after = _Object("file", _json_bytes(value), obj.mode) if value or item.get("existed") else None
    return after, leftovers if leftovers["hashes"] else None


def uninstall(*, home: Path, repo_root: Path, expected_nonce=None) -> dict:
    result = _result("uninstall")
    try:
        home, repo = _roots(home, repo_root)
        store = Store(home=home)
        if expected_nonce is not None:
            _nonce(expected_nonce)
        with _installation_lock(store, create=False) as locked:
            if not locked:
                if expected_nonce is not None:
                    raise _Refusal("install_nonce_mismatch")
                return result
            with _transaction(home, result) as (journal, directories):
                previous = _validate_manifest(store.read_cache("installation"), repo)
                result["installation_nonce"] = previous.get("installation_nonce") if previous else None
                # 同原 product/host lock 一齊比對，唔以先讀 nonce 代替鎖。
                if expected_nonce is not None and result["installation_nonce"] != expected_nonce:
                    raise _Refusal("install_nonce_mismatch")
                if result["installation_nonce"] is not None:
                    _reserve_nonce(home, home / ".local/state/quota-lantern", previous)
                if not previous or not previous["artifacts"]:
                    return result
                manifest = copy.deepcopy(previous)
                for key, item in list(manifest["artifacts"].items()):
                    if not item["owned"]:
                        del manifest["artifacts"][key]
                        continue
                    path = _targets(home)[key]
                    try:
                        obj = _read(home, path)
                        if obj is None:
                            del manifest["artifacts"][key]
                            continue
                        after, leftover = _remove(key, obj, item)
                        if after != obj and not (after and after.kind == obj.kind and after.data == obj.data):
                            actual = _put(home, path, obj, after, directories)
                            journal.append((path, obj, actual))
                            result["changed"].append(key)
                        if leftover:
                            manifest["artifacts"][key] = leftover
                            result["ok"] = False
                            result["diagnostics"].append("install_owned_changed")
                        else:
                            del manifest["artifacts"][key]
                    except (_Refusal, OSError):
                        result["ok"] = False
                        result["diagnostics"].append("install_owned_changed")
                _commit_manifest(store, previous, manifest)
    except Exception as exc:
        result["ok"] = False
        result["diagnostics"].append(str(exc) if isinstance(exc, _Refusal) else "uninstall_failed")
    result["diagnostics"] = sorted(set(result["diagnostics"]))
    return result


def doctor(*, home: Path, repo_root: Path) -> dict:
    result = _result("doctor")
    result.update(installed=False, checks={})
    try:
        home, repo = _roots(home, repo_root)
        _sources_ok(repo)
        manifest = _validate_manifest(Store(home=home).read_cache("installation"), repo)
        result["installation_nonce"] = manifest.get("installation_nonce") if manifest else None
        if not manifest or not manifest["artifacts"]:
            result["diagnostics"].append("install_not_installed")
            return result
        for key, path in _targets(home).items():
            item = manifest["artifacts"].get(key)
            result["checks"][key] = bool(
                item and (key != "settings" or set(item["hashes"]) == set(EVENTS))
                and _matches(key, _read(home, path), item))
        result["installed"] = all(result["checks"].values())
        result["ok"] = result["installed"]
        if not result["installed"]:
            result["diagnostics"].append("install_owned_changed")
        # 只確認檔案接好；override、trust、reload 都要真 harness 自己證明。
        result["checks"]["codex_global_guide_discovery"] = "unverified"
        result["checks"]["harness_loaded"] = "unverified"
    except Exception as exc:
        result["ok"] = False
        result["diagnostics"].append(str(exc) if isinstance(exc, _Refusal) else "doctor_failed")
    return result
