"""薄產品共用嘅 owned installer；固定 spec、獨立 manifest，唔借用 receipt store。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shlex
import sys
import time
from uuid import UUID, uuid4

from .install import (HostObject, HostRefusal, commit_manifest, host_put, host_read,
                      host_settings_lock, host_transaction)
from .json import decode_json, encode_json
from .private import atomic_write, private_directory, private_read
from . import private as _nonce_io

MAX_BYTES = 1024 * 1024
INSTALL_LOCK_SECONDS = 5
NONCE_HISTORY_NAME = "installation-nonces.json"
NONCE_HISTORY_SCHEMA = "kingdom.lantern.installation-nonces/1"
MAX_NONCES = 4096
MAX_NONCE_BYTES = 256 * 1024


@dataclass(frozen=True)
class ProductSpec:
    product_id: str
    package: str
    command: str
    skill: str
    events: dict[str, str | None]
    guide: str


def _nonce(value):
    try:
        if type(value) is str and str(UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise HostRefusal("install_invalid_nonce")


def _read_nonce_history(fd):
    try:
        data = None if fd is None else _nonce_io.private_read(
            fd, NONCE_HISTORY_NAME, MAX_NONCE_BYTES, error=HostRefusal)
        if data is None:
            return set()
        value = decode_json(data, maximum=MAX_NONCE_BYTES, error=HostRefusal)
        if (type(value) is not dict or set(value) != {"schema", "nonces"}
                or value["schema"] != NONCE_HISTORY_SCHEMA or type(value["nonces"]) is not list
                or len(value["nonces"]) > MAX_NONCES):
            raise ValueError
        nonces = {_nonce(nonce) for nonce in value["nonces"]}
        if len(nonces) != len(value["nonces"]):
            raise ValueError
        return nonces
    except Exception:
        raise HostRefusal("install_nonce_history_unavailable") from None


def _sync_nonce_ancestry(fd):
    # state/<product>、state、.local、HOME 嘅新 directory entries 都要可耐 crash。
    # private_directory 已核對成條 ancestry。
    current = os.dup(fd)
    try:
        for index in range(4):
            os.fsync(current)
            if index != 3:
                parent = os.open("..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                os.close(current)
                current = parent
    finally:
        os.close(current)


def _reserve_nonce(home, state, previous, nonce=None, *, dry_run=False):
    """Caller 持有 installation／host lock，直到 host commit 或 rollback 完成。

    Device-local used／reserved UUID 集合只增不減，唔跟 host rollback 或卸載刪除。
    失敗可以 burn reservation；單靠 readback 唔係 durability recovery。
    健康 adoption 唔呼叫此 helper，保持零改寫。缺歷史仍兼容 v1／v2：
    先保護已驗證嘅舊 v2 identity（包括 tombstone），先撤回或建立下一代。
    """
    if Path(state).parent != Path(home) / ".local/state":
        raise HostRefusal("install_nonce_history_unavailable")
    try:
        with _nonce_io.private_directory(state, error=HostRefusal) as fd:
            used = _read_nonce_history(fd)
    except Exception:
        raise HostRefusal("install_nonce_history_unavailable") from None
    reserved = set(used)
    prior_nonce = previous.get("installation_nonce") if previous else None
    if prior_nonce is not None:
        reserved.add(_nonce(prior_nonce))
    if nonce is not None:
        nonce = _nonce(nonce)
        if nonce in reserved:
            raise HostRefusal("install_nonce_reused")
        reserved.add(nonce)
    if len(reserved) > MAX_NONCES:
        raise HostRefusal("install_nonce_history_full")
    if dry_run or not reserved:
        return
    try:
        data = encode_json({"schema": NONCE_HISTORY_SCHEMA, "nonces": sorted(reserved)},
                           maximum=MAX_NONCE_BYTES, error=HostRefusal)
        with _nonce_io.private_directory(state, create=True, error=HostRefusal) as fd:
            if _read_nonce_history(fd) != used:
                raise HostRefusal("install_nonce_history_unavailable")
            if reserved != used:
                _nonce_io.atomic_write(fd, NONCE_HISTORY_NAME, data,
                                       maximum=MAX_NONCE_BYTES, error=HostRefusal)
            # 既存 reservation 都重新 fsync：之前可能已 publish，但 directory fsync 失敗。
            # 可見唔等於 durable。
            info = _nonce_io.private_file_info(fd, NONCE_HISTORY_NAME, error=HostRefusal)
            handle = os.open(NONCE_HISTORY_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                if info is None or _nonce_io._stamp(os.fstat(handle)) != _nonce_io._stamp(info):
                    raise HostRefusal("install_nonce_history_unavailable")
                os.fsync(handle)
            finally:
                os.close(handle)
            _sync_nonce_ancestry(fd)
            if _read_nonce_history(fd) != reserved:
                raise HostRefusal("install_nonce_history_unavailable")
    except Exception:
        # 唔刪可能已 publish 嘅 reservation，亦唔碰 host artifacts。
        raise HostRefusal("install_nonce_reservation_uncertain") from None


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


class _Product:
    def __init__(self, spec, home, repo_root):
        if not isinstance(spec, ProductSpec):
            raise HostRefusal("install_invalid_spec")
        for value, pattern in ((spec.product_id, r"[a-z][a-z0-9-]*"),
                               (spec.package, r"[a-z][a-z0-9_]*"),
                               (spec.command, r"[a-z][a-z0-9-]*"),
                               (spec.skill, r"[a-z][a-z0-9-]*")):
            if type(value) is not str or len(value) > 100 or not re.fullmatch(pattern, value):
                raise HostRefusal("install_invalid_spec")
        if (type(spec.events) is not dict or any(type(event) is not str
                or not re.fullmatch(r"[A-Za-z]{1,64}", event)
                or (matcher is not None and (type(matcher) is not str or len(matcher) > 512))
                for event, matcher in spec.events.items()) or type(spec.guide) is not str):
            raise HostRefusal("install_invalid_spec")
        self.spec = spec
        self.events = dict(spec.events)
        self.home, self.repo = _roots(home, repo_root)
        self.begin, self.end = (f"<!-- {spec.product_id}:{edge} -->" for edge in ("begin", "end"))
        if self.begin in spec.guide or self.end in spec.guide:
            raise HostRefusal("install_invalid_spec")
        self.schema = f"kingdom.{spec.product_id}.installation/2"
        self.state = self.home / ".local/state" / spec.product_id
        self.targets = {"wrapper": self.home / ".local/bin" / spec.command,
                        "claude_skill": self.home / ".claude/skills" / spec.skill,
                        "codex_skill": self.home / ".codex/skills" / spec.skill,
                        "agents": self.home / ".codex/AGENTS.md"}
        if self.events:
            self.targets["settings"] = self.home / ".claude/settings.json"

    def sources(self):
        paths = {"cli": self.repo / "tools" / (self.spec.command + ".py"),
                 "skill": self.repo / "tools" / self.spec.package / "skill"}
        required = [paths["cli"], paths["skill"] / "SKILL.md"]
        if self.events:
            paths["hook"] = self.repo / "tools/hooks" / (self.spec.product_id + ".py")
            required.append(paths["hook"])
        for path in required:
            for part in (path, *path.parents):
                if part == self.repo:
                    break
                if part.is_symlink():
                    raise HostRefusal("install_source_symlink")
            if not path.is_file():
                raise HostRefusal("install_missing_source")
        return paths

    def content(self):
        paths = self.sources()
        prefix = [str(Path(sys.executable).resolve()), "-I", "-S", "-B"]
        wrapper = ('#!/bin/sh\nexec ' + shlex.join(prefix + [str(paths["cli"])]) + ' "$@"\n').encode()
        groups = {}
        for event, matcher in self.events.items():
            command = shlex.join(prefix + [str(paths["hook"]), event]) + " 2>/dev/null || true"
            groups[event] = {"hooks": [{"type": "command", "command": command, "timeout": 1}]}
            if matcher is not None:
                groups[event]["matcher"] = matcher
        block = (self.begin + "\n" + self.spec.guide.rstrip("\n") + "\n" + self.end + "\n").encode()
        return wrapper, groups, block, os.fsencode(paths["skill"])

    def read(self):
        with private_directory(self.state, error=HostRefusal) as fd:
            data = None if fd is None else private_read(fd, "installation.json", MAX_BYTES, error=HostRefusal)
        return None if data is None else decode_json(data, maximum=MAX_BYTES, error=HostRefusal)

    def write(self, value):
        data = _json(value)
        with private_directory(self.state, create=True, error=HostRefusal) as fd:
            atomic_write(fd, "installation.json", data, maximum=MAX_BYTES, error=HostRefusal)

    def manifest(self, value):
        if value is None:
            return None
        def refuse():
            raise HostRefusal("install_manifest_conflict")
        if (type(value) is not dict or set(value) != {"schema", "repo_root", "installation_nonce", "artifacts"}
                or value["schema"] != self.schema or value["repo_root"] != str(self.repo)
                or type(value["artifacts"]) is not dict):
            refuse()
        try:
            _nonce(value["installation_nonce"])
        except HostRefusal:
            refuse()
        for key, item in value["artifacts"].items():
            fields = {"owned", "hashes", "existed", "hooks_existed", "event_existed"} if key == "settings" else {"owned", "sha256"}
            fields |= {"existed", "separator"} if key == "agents" else ({"mode"} if key == "wrapper" else set())
            if key not in self.targets or type(item) is not dict or set(item) != fields or type(item["owned"]) is not bool:
                refuse()
            if key in ("agents", "settings") and (not item["owned"] or type(item["existed"]) is not bool):
                refuse()
            if key == "wrapper" and (item["owned"] is not True or type(item["mode"]) is not int or item["mode"] != 0o700):
                refuse()
            hashes = item["hashes"] if key == "settings" else {"item": item["sha256"]}
            if type(hashes) is not dict or not hashes or any(type(h) is not str or not re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes.values()):
                refuse()
            if key == "settings" and (not set(hashes) <= self.events.keys() or type(item["hooks_existed"]) is not bool
                    or type(item["event_existed"]) is not dict or set(item["event_existed"]) != self.events.keys()
                    or any(type(flag) is not bool for flag in item["event_existed"].values())):
                refuse()
            if key == "agents" and item["separator"] not in ("", "\n", "\n\n"):
                refuse()
        return value

    def appendable(self, data):
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            raise HostRefusal("install_invalid_agents") from None
        if self.begin in text or self.end in text:
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

    def block_span(self, data, item):
        begin, end = self.begin.encode(), self.end.encode()
        if data.count(begin) != 1 or data.count(end) != 1:
            return None
        start = data.index(begin) - len(item["separator"].encode())
        stop = data.index(end) + len(end) + 1
        if start < 0 or stop <= start or data[stop - 1:stop] != b"\n" or _sha(data[start:stop]) != item["sha256"]:
            return None
        return start, stop

    def matches(self, key, obj, item):
        if obj is None:
            return False
        if key == "settings":
            hooks = _settings(obj).get("hooks", {})
            return all(sum(_group_hash(group) == digest for group in hooks.get(event, [])) == 1
                       for event, digest in item["hashes"].items())
        if key == "agents":
            return obj.kind == "file" and self.block_span(obj.data, item) is not None
        return (obj.kind == ("file" if key == "wrapper" else "link") and _sha(obj.data) == item["sha256"]
                and (key != "wrapper" or obj.mode == item["mode"]))

    def source_matches(self, artifacts, content):
        wrapper, groups, block, link = content
        expected = {"wrapper": _sha(wrapper), "claude_skill": _sha(link), "codex_skill": _sha(link),
                    "agents": _sha(artifacts["agents"]["separator"].encode() + block)}
        return (all(artifacts[key]["sha256"] == digest for key, digest in expected.items())
                and (not self.events or artifacts["settings"]["hashes"] == {
                    event: _group_hash(group) for event, group in groups.items()}))

    def plan(self, previous, installation_nonce=None):
        previous = self.manifest(previous)
        content = self.content()
        wrapper, groups, block, link = content
        objects = {key: host_read(self.home, path) for key, path in self.targets.items()}
        if previous and previous["artifacts"]:
            artifacts = previous["artifacts"]
            if (set(artifacts) != self.targets.keys() or (self.events
                    and set(artifacts["settings"]["hashes"]) != self.events.keys())):
                raise HostRefusal("install_incomplete_manifest")
            if not all(self.matches(key, objects[key], item) for key, item in artifacts.items()):
                raise HostRefusal("install_owned_changed")
            if not self.source_matches(artifacts, content):
                raise HostRefusal("install_source_changed")
            return previous, []
        nonce = str(uuid4()) if installation_nonce is None else _nonce(installation_nonce)
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
        self.appendable(original)
        separator = b"" if not original or original.endswith(b"\n\n") else (b"\n" if original.endswith(b"\n") else b"\n\n")
        addition = separator + block
        artifacts["agents"] = {"owned": True, "sha256": _sha(addition), "separator": separator.decode(), "existed": obj is not None}
        changes.append(("agents", obj, HostObject("file", original + addition, obj.mode if obj else 0o600)))
        if self.events:
            obj = objects["settings"]
            value = _settings(obj)
            hooks = value.get("hooks", {})
            for event_groups in hooks.values():
                for group in event_groups:
                    if any(any(marker in str(hook.get("command", "")) for marker in
                               (self.spec.product_id, self.spec.command)) for hook in group["hooks"]):
                        raise HostRefusal("install_hook_conflict")
            artifacts["settings"] = {"owned": True, "hashes": {event: _group_hash(group) for event, group in groups.items()},
                                     "existed": obj is not None, "hooks_existed": "hooks" in value,
                                     "event_existed": {event: event in hooks for event in self.events}}
            for event, group in groups.items():
                value.setdefault("hooks", {}).setdefault(event, []).append(group)
            changes.append(("settings", obj, HostObject("file", _json(value), obj.mode if obj else 0o600)))
        if any(len(after.data) > MAX_BYTES for _, _, after in changes):
            raise HostRefusal("install_file_changed_or_large")
        _reserve_nonce(self.home, self.state, previous, nonce, dry_run=True)
        return {"schema": self.schema, "repo_root": str(self.repo), "installation_nonce": nonce,
                "artifacts": artifacts}, changes

    def remove(self, key, obj, item):
        if key not in ("settings", "agents"):
            return (None, None) if self.matches(key, obj, item) else (obj, item)
        if obj.kind != "file":
            return obj, item
        if key == "agents":
            span = self.block_span(obj.data, item)
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

    def apply(self, previous, manifest, changes, result, journal, directories):
        for key, before, after in changes:
            path = self.targets[key]
            actual = host_put(self.home, path, before, after, directories)
            journal.append((path, before, actual))
            result["changed"].append(key)
        if manifest != previous:
            def publish(value):
                self.write(value)
                # 成功回傳都核對實檔；唔將 silent write failure 當新安裝。
                if self.read() != value:
                    raise HostRefusal("install_manifest_write_failed")
            commit_manifest(previous, manifest, read=self.read, write=publish)


def _result(action, dry_run=False):
    result = {"ok": True, "action": action, "dry_run": dry_run, "changed": [],
              "diagnostics": [], "activation": "unverified", "installation_nonce": None}
    if action == "install":
        result["created"] = False
    return result


def _failure(result, exc):
    result["ok"] = False
    result["diagnostics"].append(str(exc) if isinstance(exc, HostRefusal) else result["action"] + "_failed")
    result["diagnostics"] = sorted(set(result["diagnostics"]))
    return result


def product_install(spec, *, home, repo_root, dry_run=True, installation_nonce=None) -> dict:
    result = _result("install", dry_run)
    reserved = False
    try:
        product = _Product(spec, home, repo_root)
        try:
            previous = product.manifest(product.read())
            manifest, changes = product.plan(previous, installation_nonce)
        except Exception:
            if dry_run:
                raise
            # 另一產品／同產品可能未 commit；已有 host lock directory 先等鎖重判。
            with private_directory(product.home / ".local/state/lantern-common", error=HostRefusal) as fd:
                if fd is None:
                    raise
        if dry_run:
            result["changed"] = [key for key, _, _ in changes]
            result["installation_nonce"] = previous.get("installation_nonce") if previous and previous["artifacts"] else None
            return result
        with host_settings_lock(product.home, deadline=time.monotonic() + INSTALL_LOCK_SECONDS), host_transaction(product.home, result) as (journal, directories):
            previous = product.read()
            manifest, changes = product.plan(previous, installation_nonce)
            if changes:
                _reserve_nonce(product.home, product.state, previous, manifest["installation_nonce"])
                reserved = True
            product.apply(previous, manifest, changes, result, journal, directories)
            result["installation_nonce"] = manifest["installation_nonce"]
            result["created"] = bool(changes)
    except Exception as exc:
        result["created"] = False
        if reserved:
            result["nonce_reservation"] = "reserved"
        return _failure(result, exc)
    return result


def product_uninstall(spec, *, home, repo_root, expected_nonce=None) -> dict:
    result = _result("uninstall")
    try:
        product = _Product(spec, home, repo_root)
        if expected_nonce is not None:
            _nonce(expected_nonce)
        initial = product.manifest(product.read())
        if initial is None:
            with private_directory(product.home / ".local/state/lantern-common", error=HostRefusal) as fd:
                if fd is None:
                    if expected_nonce is not None:
                        raise HostRefusal("install_nonce_mismatch")
                    return result
        with host_settings_lock(product.home, deadline=time.monotonic() + INSTALL_LOCK_SECONDS), host_transaction(product.home, result) as (journal, directories):
            previous = product.manifest(product.read())
            result["installation_nonce"] = previous.get("installation_nonce") if previous else None
            # 同 host transaction 一把鎖內比對，唔畀 read/uninstall ABA 撤錯新安裝。
            if expected_nonce is not None and result["installation_nonce"] != expected_nonce:
                raise HostRefusal("install_nonce_mismatch")
            if result["installation_nonce"] is not None:
                _reserve_nonce(product.home, product.state, previous)
            if not previous or not previous["artifacts"]:
                return result
            manifest, changes = copy.deepcopy(previous), []
            for key, item in previous["artifacts"].items():
                if not item["owned"]:
                    del manifest["artifacts"][key]
                    continue
                try:
                    obj = host_read(product.home, product.targets[key])
                    after, leftover = product.remove(key, obj, item) if obj else (None, None)
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
            product.apply(previous, manifest, changes, result, journal, directories)
    except Exception as exc:
        return _failure(result, exc)
    result["diagnostics"] = sorted(set(result["diagnostics"]))
    return result


def product_doctor(spec, *, home, repo_root) -> dict:
    result = _result("doctor")
    result.update(installed=False, checks={})
    try:
        product = _Product(spec, home, repo_root)
        content = product.content()
        manifest = product.manifest(product.read())
        result["installation_nonce"] = manifest.get("installation_nonce") if manifest else None
        if not manifest or not manifest["artifacts"]:
            result["diagnostics"].append("install_not_installed")
            return result
        for key, path in product.targets.items():
            item = manifest["artifacts"].get(key)
            result["checks"][key] = bool(item and product.matches(key, host_read(product.home, path), item)
                                          and (key != "settings" or set(item["hashes"]) == product.events.keys()))
        result["installed"] = result["ok"] = all(result["checks"].values())
        if not result["installed"]:
            result["diagnostics"].append("install_owned_changed")
        elif not product.source_matches(manifest["artifacts"], content):
            result["installed"] = result["ok"] = False
            result["diagnostics"].append("install_source_changed")
        result["checks"].update(codex_global_guide_discovery="unverified", harness_loaded="unverified")
    except Exception as exc:
        return _failure(result, exc)
    return result
