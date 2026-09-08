"""四燈 cached 純讀；唔 observe、唔 capture、唔建 key/state。"""
from pathlib import Path

from .catalog import PRODUCTS


def _quota(home):
    from quota_lantern.core import build_status
    from quota_lantern.store import Store

    store = Store(home=home)
    research = store.read_cache("reflection", default={})
    diagnostics = store.read_cache("diagnostics", default=[])
    return build_status(store.read_latest(),
                        research=research if type(research) is dict else {},
                        diagnostics=diagnostics if type(diagnostics) is list else [])


def _reality(home, cwd):
    from reality_lantern.core import build_status
    from reality_lantern.snapshot import scope_candidates
    from reality_lantern.store import Store

    store = Store(home=home)
    key = store.get_key(create=False)
    if key is None:
        return build_status(None)
    state = store.read_state()
    scope = next((item for item in scope_candidates(cwd, key=key) if item in state["scopes"]), None)
    return build_status(state if scope else None, scope_id=scope)


def status(*, home, cwd=None, session=None):
    home, cwd = Path(home), Path.cwd() if cwd is None else Path(cwd)
    result = {"schema": "kingdom.lantern-suite.status/1", "advisory": True, "cached": True,
              "activation": "unverified", "products": {}}
    for item in PRODUCTS:
        try:
            if item.name == "quota":
                value = _quota(home)
            elif item.name == "reality":
                value = _reality(home, cwd)
            elif item.name == "friction":
                from friction_lantern.api import status as read
                value = read(home=home, cwd=cwd)
            else:
                from coedit_radar.api import status as read
                value = read(home=home, cwd=cwd, session=session)
        except Exception:
            value = {"status": "unknown", "diagnostics": ["suite_product_unavailable"]}
        result["products"][item.name] = value
    return result


def render_brief(value, *, max_bytes=1536):
    """固定短模板，四個產品都保留；唔拼接低信任文字或四篇長 brief。"""
    if type(max_bytes) is not int or not 0 <= max_bytes <= 65536:
        raise ValueError("suite_invalid_limit")
    products = value.get("products", {}) if type(value) is dict else {}
    try:
        quota = products.get("quota", {})
        sources = quota.get("sources", [])
        blocked = sum(item.get("availability") == "blocked" for item in sources)
        pressure = sum(item.get("pressure") in ("mindful", "conserve") for item in sources)
        unknown = sum(item.get("availability") not in ("allowed", "blocked") for item in sources)
        old = sum(item.get("freshness") != "fresh" for item in sources)
        q = (f"Quota｜cached {len(sources)}來源（獨立計）；blocked {blocked}／高壓 {pressure}／"
             f"availability unknown {unknown}／非 fresh {old}；billing 未驗。"
             if sources else "Quota｜unknown；冇可用 cached 來源；billing 未驗。")
        reality = products.get("reality", {})
        checks = reality.get("checks", [])
        counts = {kind: sum(item.get("validity") == kind for item in checks)
                  for kind in ("match", "stale", "unknown")}
        failures = sum(item.get("outcome") in ("nonzero", "signal", "spawn_failed") for item in checks)
        pending = sum(item.get("outcome") == "pending" for item in checks)
        r = (f"Reality｜cached {len(checks)}檢查；失敗 {failures}／pending {pending}；"
             f"match {counts['match']}（舊時點）／stale {counts['stale']}／unknown {counts['unknown']}。"
             if checks else "Reality｜unknown；冇可核實檢查，唔代表 command 通過。")
        if reality.get("recording") == "full":
            r += "收據已滿，記錄暫停。"
        friction = products.get("friction", {})
        f = ("Friction｜最近可比 nonzero 模式；可考慮另一診斷角度，唔證明空轉。"
             if friction.get("status") == "pattern" else "Friction｜未有可提示模式／unknown；唔代表冇阻力。")
        coedit = products.get("coedit", {})
        c = {"changed": "Coedit｜觀察到改動，編輯前重新讀；唔證明修改者。",
             "observed": "Coedit｜有 session 觀察，未代表而家相同；唔係 Read bytes 證書。",
             "no_observed_difference": "Coedit｜當時未觀察到差異；唔保證冇 race。"}.get(
                 coedit.get("status"), "Coedit｜unknown；缺 session／baseline 唔借其他人證據。")
        text = "四燈｜cached advisory，唔自動重驗。\n" + "\n".join((q, r, f, c))
        text += "\n環境／線上／activation 未驗；unknown 唔係 green。詳情用 status。"
        if any(item.get("diagnostics") for item in products.values()):
            text += "\n有診斷或證據不足；完整診斷見 status。"
    except (TypeError, ValueError, AttributeError):
        text = ""
    if text and len(text.encode()) <= max_bytes:
        return text
    fallback = ("四燈｜完整警示未列，唔可當 green；Quota／Reality／Friction／Coedit 詳情用 status。"
                "cached／unknown；環境、線上同 activation 未驗，唔自動重驗。")
    return fallback if len(fallback.encode()) <= max_bytes else ""


def brief(*, home, cwd=None, session=None, max_bytes=1536):
    return render_brief(status(home=home, cwd=cwd, session=session), max_bytes=max_bytes)
