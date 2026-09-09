"""獨立 membership ledger；先 durable intent，再逐產品接 public installer。"""
from pathlib import Path
import time
from uuid import UUID, uuid4

from lantern_common.json import decode_json, encode_json
from lantern_common.private import atomic_write, private_directory, private_locked, private_read
from . import SuiteError
from .catalog import NAMES, installer

SCHEMA = "kingdom.lantern-suite.membership/1"
MAX_BYTES = 65536
STATES = {"created", "preexisting", "failed", "uncertain"}
REMOVALS = {"not_requested", "intent", "removed", "failed", "uncertain"}


def _nonce(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except ValueError:
        return False


def _validate(value):
    if (type(value) is not dict or set(value) != {"schema", "members"} or value["schema"] != SCHEMA
            or type(value["members"]) is not dict or not set(value["members"]) <= set(NAMES)):
        raise SuiteError("suite_membership_invalid")
    for item in value["members"].values():
        if (type(item) is not dict or set(item) != {"state", "requested_nonce", "installation_nonce", "removal"}
                or type(item["state"]) is not str or item["state"] not in STATES
                or type(item["removal"]) is not str or item["removal"] not in REMOVALS
                or any(item[key] is not None and not _nonce(item[key])
                       for key in ("requested_nonce", "installation_nonce"))):
            raise SuiteError("suite_membership_invalid")
        if (item["state"] == "created" and (not _nonce(item["requested_nonce"])
                or item["requested_nonce"] != item["installation_nonce"])):
            raise SuiteError("suite_membership_invalid")
        if item["state"] != "created" and item["removal"] != "not_requested":
            raise SuiteError("suite_membership_invalid")
    return value


def _read(fd):
    data = None if fd is None else private_read(fd, "membership.json", MAX_BYTES, error=SuiteError)
    return {"schema": SCHEMA, "members": {}} if data is None else _validate(
        decode_json(data, maximum=MAX_BYTES, error=SuiteError))


def _write(fd, value):
    _validate(value)
    data = encode_json(value, maximum=MAX_BYTES, error=SuiteError)
    atomic_write(fd, "membership.json", data, maximum=MAX_BYTES, error=SuiteError)
    if _read(fd) != value:
        raise SuiteError("suite_membership_unverified")


def _path(home):
    path = Path(home)
    if not path.is_absolute() or ".." in path.parts:
        raise SuiteError("suite_invalid_home")
    return path / ".local/state/lantern-suite"


def membership(*, home):
    with private_directory(_path(home), error=SuiteError) as fd:
        return _read(fd)


def _result(action, dry_run=False):
    return {"ok": True, "action": action, "dry_run": dry_run, "activation": "unverified",
            "products": {}, "diagnostics": [], "partial": False}


def _stop(result, code):
    result["ok"] = False
    result["partial"] = bool(result["products"])
    result["diagnostics"].append(code)
    return result


def _item(state, requested=None, nonce=None):
    return {"state": state, "requested_nonce": requested, "installation_nonce": nonce,
            "removal": "not_requested"}


def install(*, home, repo_root, dry_run=True):
    result = _result("install", dry_run)
    try:
        if dry_run:
            previous = membership(home=home)
            for name in NAMES:
                old = previous["members"].get(name)
                if old and (old["state"] in {"uncertain", "failed"} or old["removal"] in {"intent", "uncertain", "failed"}):
                    return _stop(result, "suite_membership_uncertain")
                if old and old["removal"] == "not_requested":
                    check = installer(name).doctor(home=home, repo_root=repo_root)
                    if (not check.get("ok") or not check.get("installed")
                            or check.get("installation_nonce") != old["installation_nonce"]):
                        return _stop(result, "suite_member_changed")
                value = installer(name).install(home=home, repo_root=repo_root, dry_run=True)
                result["products"][name] = value
                if not value.get("ok"):
                    return _stop(result, "suite_product_failed")
            return result
        # 唔外持 host settings lock；各產品自己攞，避免 nested deadlock。
        with private_locked(_path(home), deadline=time.monotonic() + 5, error=SuiteError) as fd:
            value = _read(fd)
            for name in NAMES:
                adapter = installer(name)
                old = value["members"].get(name)
                if old and old["removal"] != "removed":
                    if old["state"] not in {"created", "preexisting"} or old["removal"] != "not_requested":
                        result["products"][name] = dict(old)
                        return _stop(result, "suite_membership_uncertain")
                    check = adapter.doctor(home=home, repo_root=repo_root)
                    if (not check.get("ok") or not check.get("installed")
                            or check.get("installation_nonce") != old["installation_nonce"]):
                        result["products"][name] = dict(old)
                        return _stop(result, "suite_member_changed")
                    result["products"][name] = dict(old)
                    continue
                check = adapter.doctor(home=home, repo_root=repo_root)
                if check.get("ok") and check.get("installed"):
                    # 健康 legacy v1 只 adopt；連 idempotent install 都唔叫。
                    item = _item("preexisting", nonce=check.get("installation_nonce"))
                    value["members"][name] = item
                    _write(fd, value)
                    result["products"][name] = dict(item)
                    continue
                requested = str(uuid4())
                value["members"][name] = _item("uncertain", requested=requested)
                _write(fd, value)
                # Crash gap 保留 uncertain；唔靠見到 nonce 就猜今次 created。
                # 當輪進度唔等於 durable ownership；publication 成功先替換 intent。
                result["products"][name] = {**value["members"][name], "bookkeeping": "uncertain",
                                            "product_result": None}
                outcome = adapter.install(home=home, repo_root=repo_root, dry_run=False,
                                          installation_nonce=requested)
                result["products"][name]["product_result"] = {
                    "ok": outcome.get("ok") is True, "created": outcome.get("created") is True}
                nonce = outcome.get("installation_nonce")
                if outcome.get("ok") and outcome.get("created") is True and nonce == requested:
                    item = _item("created", requested, nonce)
                elif outcome.get("ok") and outcome.get("created") is False:
                    healthy = adapter.doctor(home=home, repo_root=repo_root)
                    item = (_item("preexisting", requested, nonce) if healthy.get("ok") and healthy.get("installed")
                            and healthy.get("installation_nonce") == nonce else _item("uncertain", requested))
                else:
                    # 明確零變更仍唔准自動重試；failed 同 uncertain 都不屬 suite owned。
                    uncertain = bool(outcome.get("changed")) or any(
                        token in str(code) for code in outcome.get("diagnostics", [])
                        for token in ("uncertain", "rollback", "manifest"))
                    item = _item("uncertain" if uncertain else "failed", requested)
                value["members"][name] = item
                _write(fd, value)
                result["products"][name] = dict(item)
                if item["state"] not in {"created", "preexisting"}:
                    return _stop(result, "suite_product_failed")
    except Exception:
        return _stop(result, "suite_bookkeeping_unavailable")
    return result


def uninstall(*, home, repo_root):
    result = _result("uninstall")
    try:
        # 冇 ledger 就冇 ownership；唔建立 state、唔掃 artifacts 去猜。
        if not membership(home=home)["members"]:
            return result
        with private_locked(_path(home), deadline=time.monotonic() + 5, error=SuiteError) as fd:
            value = _read(fd)
            for name in reversed(NAMES):
                item = value["members"].get(name)
                if item is None:
                    continue
                result["products"][name] = dict(item)
                if item["state"] == "preexisting" or item["removal"] == "removed":
                    continue
                if item["state"] != "created" or item["removal"] != "not_requested":
                    return _stop(result, "suite_membership_uncertain")
                item["removal"] = "intent"
                _write(fd, value)
                # 保留已執行進度；未核實結果寫入，唔宣稱 durable removed。
                result["products"][name] = {**item, "bookkeeping": "uncertain", "product_result": None}
                outcome = installer(name).uninstall(home=home, repo_root=repo_root,
                                                    expected_nonce=item["installation_nonce"])
                result["products"][name]["product_result"] = {"ok": outcome.get("ok") is True}
                if outcome.get("ok") and outcome.get("installation_nonce") == item["installation_nonce"]:
                    item["removal"] = "removed"
                else:
                    item["removal"] = "uncertain"
                _write(fd, value)
                result["products"][name] = dict(item)
                if item["removal"] != "removed":
                    return _stop(result, "suite_removal_refused")
    except Exception:
        return _stop(result, "suite_bookkeeping_unavailable")
    return result


def doctor(*, home, repo_root):
    result = _result("doctor")
    try:
        result["membership"] = membership(home=home)
    except Exception:
        result["membership"] = None
        _stop(result, "suite_membership_unavailable")
    for name in NAMES:
        try:
            check = installer(name).doctor(home=home, repo_root=repo_root)
        except Exception:
            check = {"ok": False, "installed": False, "activation": "unverified",
                     "diagnostics": ["suite_product_unavailable"]}
        result["products"][name] = check
        if not check.get("ok") or not check.get("installed"):
            result["ok"] = False
    result["installed"] = all(item.get("installed") for item in result["products"].values())
    if result["membership"] is not None:
        for name, item in result["membership"]["members"].items():
            check = result["products"][name]
            if (item["state"] in {"failed", "uncertain"} or item["removal"] in {"intent", "failed", "uncertain"}
                    or (item["removal"] == "not_requested" and item["installation_nonce"] != check.get("installation_nonce"))):
                _stop(result, "suite_membership_uncertain")
    result["diagnostics"] = sorted(set(result["diagnostics"]))
    return result
