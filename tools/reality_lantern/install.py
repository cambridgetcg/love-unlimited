"""只管理實況燈入口；host IO／RAM rollback 交畀 lantern_common。"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import re
import shlex
import sys
import time
from uuid import UUID, uuid4

from lantern_common.install import (HostObject, HostRefusal, commit_manifest,
                                    host_put, host_read, host_settings_lock,
                                    host_transaction)
from lantern_common.json import decode_json, encode_json
from lantern_common.private import private_directory
from lantern_common.product import _reserve_nonce
from .store import Store

EVENTS = {"SessionStart": None, "UserPromptSubmit": None, "SubagentStart": None,
          "PreToolUse": "Bash", "PostToolUse": "Edit|Write|NotebookEdit|Bash",
          "PostToolUseFailure": "Edit|Write|NotebookEdit|Bash"}
MANIFEST_SCHEMA = "kingdom.reality-lantern.installation/2"
LEGACY_MANIFEST_SCHEMA = "kingdom.reality-lantern.installation/1"
BEGIN, END = "<!-- reality-lantern:begin -->", "<!-- reality-lantern:end -->"
MAX_BYTES = 1024 * 1024
INSTALL_LOCK_SECONDS = 5


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return encode_json(value, maximum=MAX_BYTES, error=HostRefusal)


def _group_hash(group):
    return _sha(_json(group))


def _roots(home, repo_root):
    home, repo = Path(home).absolute(), Path(repo_root).absolute()
    if ".." in home.parts or ".." in repo.parts or home.is_symlink() or repo.is_symlink():
        raise HostRefusal("install_symlink")
    home, repo = home.resolve(), repo.resolve()
    if not home.is_dir() or not repo.is_dir():
        raise HostRefusal("install_missing_root")
    if home.stat().st_uid != os.geteuid() or home.stat().st_mode & 0o022:
        raise HostRefusal("install_foreign_object")
    return home, repo


def _targets(home):
    return {"wrapper": home / ".local/bin/kingdom-reality",
            "claude_skill": home / ".claude/skills/reality-lantern",
            "codex_skill": home / ".codex/skills/reality-lantern",
            "agents": home / ".codex/AGENTS.md", "settings": home / ".claude/settings.json"}


def _sources(repo):
    paths = {"cli": repo / "tools/kingdom-reality.py",
             "hook": repo / "tools/hooks/reality-lantern.py",
             "skill": repo / "tools/reality_lantern/skill"}
    for path in (paths["cli"], paths["hook"], paths["skill"] / "SKILL.md"):
        for part in (path, *path.parents):
            if part == repo:
                break
            if part.is_symlink():
                raise HostRefusal("install_source_symlink")
        if not path.is_file():
            raise HostRefusal("install_missing_source")
    return paths


def _content(repo):
    paths = _sources(repo)
    python = str(Path(sys.executable).resolve())
    prefix = [python, "-I", "-S", "-B"]
    wrapper = ('#!/bin/sh\nexec ' + shlex.join(prefix + [str(paths["cli"])]) + ' "$@"\n').encode()
    groups = {}
    for event, matcher in EVENTS.items():
        command = shlex.join(prefix + [str(paths["hook"]), event]) + " 2>/dev/null || true"
        groups[event] = {"hooks": [{"type": "command", "command": command, "timeout": 1}]}
        if matcher is not None:
            groups[event]["matcher"] = matcher
    block = (BEGIN + "\n## Reality Lantern（soft / advisory）\n"
             "只將原本獲授權嘅本地檢查包入 `kingdom reality check -- 原命令`；唔額外跑第二次。\n"
             "`kingdom reality brief` 只讀收據；exit0 唔等於全 suite 通過或線上健康，版本只係 as-of。\n"
             "詳見 `~/.codex/skills/reality-lantern/SKILL.md`；唔自動 tests、deploy、改 model、routing 或 permissions。\n"
             + END + "\n").encode()
    return wrapper, groups, block, os.fsencode(paths["skill"])


def _settings(obj):
    if obj is None:
        return {}
    if obj.kind != "file":
        raise HostRefusal("install_symlink")
    value = decode_json(obj.data, maximum=MAX_BYTES, error=HostRefusal)
    if type(value) is not dict or type(value.get("hooks", {})) is not dict:
        raise HostRefusal("install_invalid_settings")
    for groups in value.get("hooks", {}).values():
        if type(groups) is not list or any(
                type(group) is not dict or type(group.get("hooks")) is not list
                or any(type(hook) is not dict for hook in group["hooks"]) for group in groups):
            raise HostRefusal("install_invalid_hooks")
    return value


def _appendable(data):
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise HostRefusal("install_invalid_agents") from None
    if BEGIN in text or END in text:
        raise HostRefusal("install_agents_conflict")
    lines, fence = text.splitlines(), None
    if lines and lines[0] == "---" and not any(line in ("---", "...") for line in lines[1:]):
        raise HostRefusal("install_agents_unclosed_block")
    for line in lines:
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if match:
            run, rest = match.groups()
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                fence = None
    if fence or text.rfind("<!--") > text.rfind("-->"):
        raise HostRefusal("install_agents_unclosed_block")


def _nonce(value):
    try:
        if type(value) is str and str(UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise HostRefusal("install_invalid_nonce")


def _manifest(value, repo):
    if value is None:
        return None
    def refuse():
        raise HostRefusal("install_manifest_conflict")
    if (type(value) is not dict or value.get("schema") not in (MANIFEST_SCHEMA, LEGACY_MANIFEST_SCHEMA)
            or value.get("repo_root") != str(repo) or type(value.get("artifacts")) is not dict):
        refuse()
    fields = {"schema", "repo_root", "artifacts"}
    if value["schema"] == MANIFEST_SCHEMA:
        fields.add("installation_nonce")
        try:
            _nonce(value.get("installation_nonce"))
        except HostRefusal:
            refuse()
    if set(value) != fields:
        refuse()
    for key, item in value["artifacts"].items():
        fields = {"owned", "hashes", "existed", "hooks_existed", "event_existed"} if key == "settings" else {"owned", "sha256"}
        fields |= {"existed", "separator"} if key == "agents" else ({"mode"} if key == "wrapper" else set())
        if key not in _targets(Path("/")) or type(item) is not dict or set(item) != fields or type(item["owned"]) is not bool:
            refuse()
        if key in ("agents", "settings") and (not item["owned"] or type(item["existed"]) is not bool):
            refuse()
        if key == "wrapper" and (item["owned"] is not True or type(item["mode"]) is not int or item["mode"] != 0o700):
            refuse()
        hashes = item["hashes"] if key == "settings" else {"item": item["sha256"]}
        if type(hashes) is not dict or not hashes or any(type(h) is not str or not re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes.values()):
            refuse()
        if key == "settings" and (not set(hashes) <= EVENTS.keys() or type(item["hooks_existed"]) is not bool
                or type(item["event_existed"]) is not dict or set(item["event_existed"]) != EVENTS.keys()
                or any(type(flag) is not bool for flag in item["event_existed"].values())):
            refuse()
        if key == "agents" and item["separator"] not in ("", "\n", "\n\n"):
            refuse()
    return value


def _block_span(data, item):
    begin, end = BEGIN.encode(), END.encode()
    if data.count(begin) != 1 or data.count(end) != 1:
        return None
    start = data.index(begin) - len(item["separator"].encode())
    stop = data.index(end) + len(end) + 1
    if start < 0 or stop <= start or data[stop - 1:stop] != b"\n" or _sha(data[start:stop]) != item["sha256"]:
        return None
    return start, stop


def _matches(key, obj, item):
    if obj is None:
        return False
    if key == "settings":
        hooks = _settings(obj).get("hooks", {})
        return all(sum(_group_hash(group) == digest for group in hooks.get(event, [])) == 1
                   for event, digest in item["hashes"].items())
    if key == "agents":
        return obj.kind == "file" and _block_span(obj.data, item) is not None
    return (obj.kind == ("file" if key == "wrapper" else "link") and _sha(obj.data) == item["sha256"]
            and (key != "wrapper" or obj.mode == item["mode"]))


def _plan(home, repo, previous, installation_nonce=None):
    wrapper, groups, block, link = _content(repo)
    objects = {key: host_read(home, path) for key, path in _targets(home).items()}
    previous = _manifest(previous, repo)
    if previous and previous["artifacts"]:
        if set(previous["artifacts"]) != objects.keys() or set(previous["artifacts"]["settings"]["hashes"]) != EVENTS.keys():
            raise HostRefusal("install_incomplete_manifest")
        if not all(_matches(key, objects[key], item) for key, item in previous["artifacts"].items()):
            raise HostRefusal("install_owned_changed")
        if objects["wrapper"].data != wrapper or any(previous["artifacts"]["settings"]["hashes"][event] != _group_hash(group) for event, group in groups.items()):
            raise HostRefusal("install_source_changed")
        return previous, []
    artifacts, changes = {}, []
    for key in ("wrapper", "claude_skill", "codex_skill"):
        obj = objects[key]
        desired = HostObject("file", wrapper, 0o700) if key == "wrapper" else HostObject("link", link)
        if obj is not None and (key == "wrapper" or obj.kind != "link" or obj.data != link):
            raise HostRefusal("install_target_conflict")
        artifacts[key] = {"owned": obj is None, "sha256": _sha(desired.data)}
        if key == "wrapper":
            artifacts[key]["mode"] = desired.mode
        if obj is None:
            changes.append((key, obj, desired))
    obj = objects["agents"]
    if obj and obj.kind != "file":
        raise HostRefusal("install_symlink")
    original = obj.data if obj else b""
    _appendable(original)
    separator = b"" if not original or original.endswith(b"\n\n") else (b"\n" if original.endswith(b"\n") else b"\n\n")
    addition = separator + block
    artifacts["agents"] = {"owned": True, "sha256": _sha(addition), "separator": separator.decode(), "existed": obj is not None}
    changes.append(("agents", obj, HostObject("file", original + addition, obj.mode if obj else 0o600)))
    obj = objects["settings"]
    value = _settings(obj)
    hooks = value.get("hooks", {})
    for event_groups in hooks.values():
        for group in event_groups:
            if any(any(marker in str(hook.get("command", "")) for marker in ("reality-lantern", "kingdom-reality")) for hook in group["hooks"]):
                raise HostRefusal("install_hook_conflict")
    artifacts["settings"] = {"owned": True, "hashes": {event: _group_hash(group) for event, group in groups.items()},
                             "existed": obj is not None, "hooks_existed": "hooks" in value,
                             "event_existed": {event: event in hooks for event in EVENTS}}
    for event, group in groups.items():
        value.setdefault("hooks", {}).setdefault(event, []).append(group)
    changes.append(("settings", obj, HostObject("file", _json(value), obj.mode if obj else 0o600)))
    if any(len(after.data) > MAX_BYTES for _, _, after in changes):
        raise HostRefusal("install_file_changed_or_large")
    nonce = str(uuid4()) if installation_nonce is None else _nonce(installation_nonce)
    _reserve_nonce(home, home / ".local/state/reality-lantern", previous, nonce, dry_run=True)
    return {"schema": MANIFEST_SCHEMA, "repo_root": str(repo), "artifacts": artifacts,
            "installation_nonce": nonce}, changes


def _remove(key, obj, item):
    if key not in ("settings", "agents"):
        return (None, None) if _matches(key, obj, item) else (obj, item)
    if obj.kind != "file":
        return obj, item
    if key == "agents":
        span = _block_span(obj.data, item)
        if span is None:
            return obj, item
        remaining = obj.data[:span[0]] + obj.data[span[1]:]
        return (HostObject("file", remaining, obj.mode) if remaining or item["existed"] else None), None
    value, leftover = _settings(obj), copy.deepcopy(item)
    hooks = value.get("hooks", {})
    for event, digest in item["hashes"].items():
        groups = hooks.get(event, [])
        matches = [index for index, group in enumerate(groups) if _group_hash(group) == digest]
        if len(matches) == 1:
            del groups[matches[0]]
            del leftover["hashes"][event]
            if not groups and not item["event_existed"][event]:
                del hooks[event]
    if leftover["hashes"] == item["hashes"]:
        return obj, item
    if not hooks and not item["hooks_existed"]:
        value.pop("hooks", None)
    return (HostObject("file", _json(value), obj.mode) if value or item["existed"] else None), leftover if leftover["hashes"] else None


def _result(action, dry_run=False):
    result = {"ok": True, "action": action, "dry_run": dry_run, "changed": [], "diagnostics": [],
              "activation": "unverified", "installation_nonce": None}
    if action == "install":
        result["created"] = False
    return result


def _failure(result, exc):
    result["ok"] = False
    # Common／Store 固定 codes 以外嘅 exception 原文唔出門。
    result["diagnostics"].append(str(exc) if isinstance(exc, HostRefusal) else result["action"] + "_failed")
    result["diagnostics"] = sorted(set(result["diagnostics"]))
    return result


def _apply(home, store, previous, manifest, changes, result, journal, directories):
    for key, before, after in changes:
        path = _targets(home)[key]
        actual = host_put(home, path, before, after, directories)
        journal.append((path, before, actual))
        result["changed"].append(key)
    if manifest != previous:
        def publish(value):
            store.write_cache("installation", value)
            # Store 成功回傳亦要實檔核對；其後失敗交 common 做 uncertain reconciliation。
            if store.read_cache("installation") != value:
                raise HostRefusal("install_manifest_write_failed")
        commit_manifest(previous, manifest, read=lambda: store.read_cache("installation"), write=publish)


def install(*, home: Path, repo_root: Path, dry_run: bool = True, installation_nonce=None) -> dict:
    result = _result("install", dry_run)
    reserved = False
    try:
        home, repo = _roots(home, repo_root)
        store = Store(home=home)
        try:
            previous = _manifest(store.read_cache("installation"), repo)
            _, changes = _plan(home, repo, previous, installation_nonce)
        except Exception:
            if dry_run:
                raise
            # 同伴可能仲喺 host transaction 中；有共用 state 先等鎖重讀。
            with private_directory(home / ".local/state/lantern-common") as fd:
                if fd is None:
                    raise
        if dry_run:
            result["changed"] = [key for key, _, _ in changes]
            result["installation_nonce"] = previous.get("installation_nonce") if previous and previous["artifacts"] else None
            return result
        with host_settings_lock(home, deadline=time.monotonic() + INSTALL_LOCK_SECONDS), host_transaction(home, result) as (journal, directories):
            previous = store.read_cache("installation")
            manifest, changes = _plan(home, repo, previous, installation_nonce)
            if changes:
                _reserve_nonce(home, home / ".local/state/reality-lantern", previous, manifest["installation_nonce"])
                reserved = True
            _apply(home, store, previous, manifest, changes, result, journal, directories)
            result["installation_nonce"] = manifest.get("installation_nonce")
            result["created"] = bool(changes)
    except Exception as exc:
        result["created"] = False
        if reserved:
            result["nonce_reservation"] = "reserved"
        return _failure(result, exc)
    return result


def uninstall(*, home: Path, repo_root: Path, expected_nonce=None) -> dict:
    result = _result("uninstall")
    try:
        home, repo = _roots(home, repo_root)
        store = Store(home=home)
        if expected_nonce is not None:
            _nonce(expected_nonce)
        initial = _manifest(store.read_cache("installation"), repo)
        if initial is None:
            # Install 未 commit 都可能已持鎖；唔喺 transaction 中途誤報撤回完成。
            with private_directory(home / ".local/state/lantern-common") as fd:
                if fd is None:
                    if expected_nonce is not None:
                        raise HostRefusal("install_nonce_mismatch")
                    return result
        with host_settings_lock(home, deadline=time.monotonic() + INSTALL_LOCK_SECONDS), host_transaction(home, result) as (journal, directories):
            previous = _manifest(store.read_cache("installation"), repo)
            result["installation_nonce"] = previous.get("installation_nonce") if previous else None
            # 鎖內比對；legacy 冇 nonce，唔畀 suite 冒認新安裝。
            if expected_nonce is not None and result["installation_nonce"] != expected_nonce:
                raise HostRefusal("install_nonce_mismatch")
            if result["installation_nonce"] is not None:
                _reserve_nonce(home, home / ".local/state/reality-lantern", previous)
            if not previous or not previous["artifacts"]:
                return result
            manifest, changes = copy.deepcopy(previous), []
            for key, item in previous["artifacts"].items():
                if not item["owned"]:
                    del manifest["artifacts"][key]
                    continue
                try:
                    obj = host_read(home, _targets(home)[key])
                    after, leftover = _remove(key, obj, item) if obj else (None, None)
                except (HostRefusal, OSError):
                    result["ok"] = False
                    result["diagnostics"].append("install_owned_changed")
                    continue
                if after != obj:
                    changes.append((key, obj, after))
                if leftover:
                    manifest["artifacts"][key] = leftover
                    result["ok"] = False
                    result["diagnostics"].append("install_owned_changed")
                else:
                    del manifest["artifacts"][key]
            _apply(home, store, previous, manifest, changes, result, journal, directories)
    except Exception as exc:
        return _failure(result, exc)
    result["diagnostics"] = sorted(set(result["diagnostics"]))
    return result


def doctor(*, home: Path, repo_root: Path) -> dict:
    result = _result("doctor")
    result.update(installed=False, checks={})
    try:
        home, repo = _roots(home, repo_root)
        _sources(repo)
        manifest = _manifest(Store(home=home).read_cache("installation"), repo)
        result["installation_nonce"] = manifest.get("installation_nonce") if manifest else None
        if not manifest or not manifest["artifacts"]:
            result["diagnostics"].append("install_not_installed")
            return result
        for key, path in _targets(home).items():
            item = manifest["artifacts"].get(key)
            result["checks"][key] = bool(item and _matches(key, host_read(home, path), item)
                                          and (key != "settings" or set(item["hashes"]) == EVENTS.keys()))
        result["installed"] = result["ok"] = all(result["checks"].values())
        if not result["installed"]:
            result["diagnostics"].append("install_owned_changed")
        result["checks"].update(codex_global_guide_discovery="unverified", harness_loaded="unverified")
    except Exception as exc:
        return _failure(result, exc)
    return result
