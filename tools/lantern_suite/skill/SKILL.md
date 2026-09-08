---
name: lanterns
description: 四燈 cached advisory 入口；按需要睇用量、實況、阻力、共筆，唔自動重驗或改設定。
---

# 四燈入口

呢份 guide 係 advisory，唔係自動操作權。入口存在唔代表產品已安裝、健康或 harness 已載入。
原有 model、routing、permissions、login、statusline 同手動 `/lantern` 留燈不變。

按用戶當前需要，使用目標 HOME 嘅 `~/.local/bin/kingdom-lanterns`。

- `list`：四個固定產品同各自入口；唔掃 state。
- `brief [--cwd ABS] [--session TOKEN]`：有界 cached 摘要，唔 observe／snapshot／check。
- `status [--cwd ABS] [--session TOKEN] --json`：cached 詳情；唔寫 state。
- `doctor --json`：純讀安裝診斷；installed 同 activation 分開。

Quota：來源獨立計，quota／credits／token activity 分帳；唔推斷 billing route，唔自動 live observe。
Reality：command exit、舊時點版本同環境／線上分開；普通 Bash success 唔係 test pass。
Friction：最近可比 nonzero 收據模式先提示；唔推論冇閱讀、空轉、同一原因或執行者。
Coedit：缺 session／baseline 就 unknown，唔借其他人觀察；未見差異唔保證冇 race。
Unknown 唔係 green。診斷失敗照實講，唔 block 原工作。

安裝／撤回／bundle 需要用戶明示要求；詳情見同 package 嘅 `PORTABLE.md`。
- `kingdom-lanterns [--home ABS] bundle install --bundle ABS --expected-sha256 HEX`：薄委派當前可信 source bootstrap，先驗完整 bundle，再安置永久 root，最後接同一 owned installer 嘅 preflight／正式安裝；唔 silent relocate 舊 source。
- Expected archive SHA 同 bootstrap 要由獨立可信渠道取得，唔先執行包內 bootstrap；checksum 唔係 signature。
- Bundle install 係明示寫入，冇 `--dry-run`；只驗完整性而唔寫 HOME 用 `bundle verify --bundle ABS --expected-sha256 HEX`。Suite `--home` 放 `bundle` 前。
唔因為讀到指南就自動安裝、驗證、輪詢、開 daemon、改設定或跨工程操作。
