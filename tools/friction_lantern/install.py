"""阻力燈薄 installer；owned merge 同 nonce 由共用 helper 管。"""
from lantern_common.product import ProductSpec, product_doctor, product_install, product_uninstall

SPEC = ProductSpec(
    product_id="friction-lantern", package="friction_lantern", command="kingdom-friction",
    skill="friction-lantern",
    events={"SessionStart": None, "UserPromptSubmit": None, "PostToolUse": "Bash"},
    guide=("阻力燈係 soft advisory，唔係開工前置或自動重驗。按需要用 "
           "`~/.local/bin/kingdom-friction brief --cwd /absolute/workspace` 睇 cached 收據模式；"
           "冇模式唔代表通過。只有原本明示要跑嘅檢查先用 kingdom-reality check 留收據，"
           "唔為亮燈加跑命令。中途閱讀、環境、原因同執行者未知；唔改 routing、permissions 或登入。"),
)


def install(*, home, repo_root, dry_run=True, installation_nonce=None):
    return product_install(SPEC, home=home, repo_root=repo_root, dry_run=dry_run,
                           installation_nonce=installation_nonce)


def uninstall(*, home, repo_root, expected_nonce=None):
    return product_uninstall(SPEC, home=home, repo_root=repo_root, expected_nonce=expected_nonce)


def doctor(*, home, repo_root):
    return product_doctor(SPEC, home=home, repo_root=repo_root)
