"""Suite 自己嘅 wrapper／native skills／AGENTS，冇 settings hook。"""
from lantern_common.product import ProductSpec, product_doctor, product_install, product_uninstall

GUIDE = """## 四燈入口（advisory）
按需要用 `~/.local/bin/kingdom-lanterns list`、`brief`、`status --json` 或 `doctor --json`。
入口存在唔等於四燈健康或 harness 已載入；unknown 唔係 green。
Quota 只讀 cached；Reality 唔自動 check／snapshot；Coedit 缺 session 唔借他人 baseline。
唔因為指南而安裝、重驗、改 model/routing/permissions/login/statusline。手動 `/lantern` 留燈不變。
"""
SPEC = ProductSpec(product_id="lanterns", package="lantern_suite", command="kingdom-lanterns",
                   skill="lanterns", events={}, guide=GUIDE)


def install(*, home, repo_root, dry_run=True, installation_nonce=None):
    return product_install(SPEC, home=home, repo_root=repo_root, dry_run=dry_run,
                           installation_nonce=installation_nonce)


def uninstall(*, home, repo_root, expected_nonce=None):
    return product_uninstall(SPEC, home=home, repo_root=repo_root, expected_nonce=expected_nonce)


def doctor(*, home, repo_root):
    return product_doctor(SPEC, home=home, repo_root=repo_root)
