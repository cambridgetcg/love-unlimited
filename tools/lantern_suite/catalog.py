"""固定 catalog；唔由 membership／環境／PATH 載入程式。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    name: str
    product_id: str
    command: str
    skill: str
    purpose: str


PRODUCTS = (
    Product("quota", "quota-lantern", "kingdom-quota", "quota-lantern", "cached 用量；唔代表 billing route"),
    Product("reality", "reality-lantern", "kingdom-reality", "reality-lantern", "command outcome 同版本分開；線上未驗"),
    Product("friction", "friction-lantern", "kingdom-friction", "friction-lantern", "最近可比 nonzero 模式；唔推論空轉"),
    Product("coedit", "coedit-radar", "kingdom-coedit", "coedit-radar", "有界檔案觀察；唔保證冇 race"),
)
NAMES = tuple(item.name for item in PRODUCTS) + ("entry",)


def catalog():
    return [{"name": item.name, "product_id": item.product_id, "command": item.command,
             "skill": item.skill, "purpose": item.purpose, "activation": "unverified"}
            for item in PRODUCTS]


def installer(name):
    # 固定分支先 import；使用者資料唔可以變成 module path。
    if name == "quota":
        from quota_lantern import install
    elif name == "reality":
        from reality_lantern import install
    elif name == "friction":
        from friction_lantern import install
    elif name == "coedit":
        from coedit_radar import install
    elif name == "entry":
        from . import entry as install
    else:
        raise ValueError("suite_unknown_product")
    return install
