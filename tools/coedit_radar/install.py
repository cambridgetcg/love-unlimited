"""共筆雷達薄 installer；ownership／nonce 只經 common public API。"""
from lantern_common.product import ProductSpec, product_doctor, product_install, product_uninstall

SPEC = ProductSpec(
    product_id="coedit-radar", package="coedit_radar", command="kingdom-coedit", skill="coedit-radar",
    events={"PreToolUse": "Read|Edit|Write", "PostToolUse": "Read|Edit|Write",
            "PostToolUseFailure": "Read|Edit|Write", "SubagentStart": None},
    guide="""## 共筆雷達（advisory）
按需要用 `~/.local/bin/kingdom-coedit status --session <本次明示 session>` 或 `brief`。
其他 harness 可明示 `observe --file <安全絕對檔名> --session <本次 session>`，
再用同一組參數 `compare`。只係 explicit 有界觀察，唔冒認 Read 收據。
冇 session／缺觀察／範圍未知唔係綠燈；提示只建議重新核對，唔 block／改工具輸入。
入口存在唔等於 hook 已載入；唔因此自行安裝、執行檢查或更改權限。
""",
)


def install(*, home, repo_root, dry_run=True, installation_nonce=None) -> dict:
    return product_install(SPEC, home=home, repo_root=repo_root, dry_run=dry_run,
                           installation_nonce=installation_nonce)


def uninstall(*, home, repo_root, expected_nonce=None) -> dict:
    return product_uninstall(SPEC, home=home, repo_root=repo_root, expected_nonce=expected_nonce)


def doctor(*, home, repo_root) -> dict:
    return product_doctor(SPEC, home=home, repo_root=repo_root)
