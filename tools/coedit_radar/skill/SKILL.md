---
name: coedit-radar
description: 共筆雷達：寫入前有界觀察差異 advisory；未知唔係綠燈，唔改權限或工具輸入。
---

# 共筆雷達

沿用用戶當前權限同工具。唔因讀到入口自行 install、doctor、重驗、改 routing，亦唔取代手動 `/lantern`。

- `~/.local/bin/kingdom-coedit status --session <本次 session> --json`：只讀 metadata cache，唔建 key／state。
- `brief --session <本次 session>`：固定短提示。冇明示 session 就 unknown，唔借別人 baseline。
- `observe --file <安全絕對檔名> --session <本次 session>`：明示採樣；只記 HMAC／數值／source enum。
- `compare --file <同一檔名> --session <同一 session> --json`：只讀比較 explicit baseline；唔冒認 hook Read。
- `install` 預設 dry-run，`install --apply` 先寫入；`uninstall` 只撤 owned artifacts，保留觀察 cache；`doctor` 唔證實 harness 已載入。

Hook 只就有 session_id＋tool_use_id 配對嘅 allowlisted 短 LF text 建兩時點 observation。明示 offset／limit、2000 行以上、長行、PDF／notebook／binary、symlink／hardlink、secret path、缺欄位、未知 shape、碰撞、race 同過期一律 unknown。

Pre Edit／Write 只有觀察出差異先提示重新讀取核對；成功或失敗完成會清 baseline。冇提示唔代表冇變，亦唔代表有寫入許可。永遠唔證 Read 返回 bytes；兩個相同觀察唔證中間冇改過，preflight 之後仍可能 race。

限制：1MiB／256 entries／256KiB／30 分鐘，250ms event／25ms lock。State 私有 device-local；唔把原文、檔名、session、prompt、command、output 或 credentials 搬入 cache／新提示。唔推斷改動者。
