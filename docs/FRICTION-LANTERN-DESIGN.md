# 阻力燈 — 設計

## 意圖同界線

本地、soft、cached advisory。只描述已記錄嘅可比失敗模式，唔自動執行檢查、唔判斷 agent 空轉或失敗原因。唔保存 raw command/output/prompt/path/session/credentials；唔改 Reality evidence。

## Public APIs

- `friction_lantern.core.derive_pattern(state, scope_id, threshold=3) -> dict`：純函式，讀既有 Reality v1 schema，唔依賴 archive 新 method。
- `render_brief(status, *, max_bytes=512) -> str`：固定廣東話模板、有 UTF-8 byte cap、renderer 再驗 schema。
- `friction_lantern.api.status(*, home, cwd) -> dict`／`brief(*, home, cwd, max_bytes=512) -> str`：純讀 Store state/key；缺資料零 mkdir/key/write。只用有界 scope locator，唔掃 workspace／capture／執行 Git。
- `friction_lantern.hooks.run_hook(event, payload, *, home=None, deadline=None) -> dict`。
- `friction_lantern.install.install/uninstall/doctor`：沿用 suite contract keyword API，交共用 ProductSpec helper 執行 owned merge／nonce 保護。

Status schema `kingdom.friction-lantern/1` 固定欄位 `schema/status/cached/advisory/scope_id/pattern/as_of/recording/diagnostics`。`status` 係 `pattern/no_pattern/suppressed/unknown`；任何一項都唔代表 pass。Pattern 只含 HMAC identities、kind、nonzero exit code、count 同 recorded sequence metadata。

## 純推導

驗證完整 bounded v1 state，再取全域 recorded sequence 最尾 threshold 個 runs（最少三）。唔先過濾 scope/check 再湊數。全部同 scope/check/kind/nonzero exit code；before/after 同跨 run scope/worktree/cwd/fingerprint/coverage 相同而完整；目前 projection match。比較 observed_at 唔當版本內容。

Run 時序要求 before observation ≤ start ≤ after observation ≤ finish，上一個 finish ≤ 下一個 start；projection 比較唔早過 after。Sequence 容許 highwater gaps：相鄰指全域最近 **recorded runs** 嘅順序，唔要求數字逐一加一，亦唔要求尾位等於 next_sequence−1；invalidation／generation highwater 唔係新收據。Renderer 接受 gaps，但 count 唔可大過 first/last sequence 所能容納嘅 run 數。Interleave、pending、exit0、signal、spawn_failed、stale、unknown、容量或 generation 耗盡仍均抑制；唔跳過 barrier 去湊較舊模式。Cached as_of 係 scope observation 時點，唔冒認實時狀態，唔推論中途閱讀、未落帳工作、環境、原因或執行者。舊 `sequence_gap` diagnostic 保留可讀兼容，本輪唔再產生。

## Delivery 同 hooks

自家 `~/.local/state/friction-lantern/{key,delivery.json}`，private nofollow IO，256 entries／256KiB。Key 32 bytes；session＋scope 經自家 HMAC先落 cache；只存 fingerprint 同 delivered_at。呢個 cache 同 installation.json 分開；cache eviction 唔係刪收據。

Delivery publish 使用原有 `kingdom.friction-lantern.delivery/1`，冇新 state 檔。`atomic_write` rename 後 directory fsync 失敗時，用同一 lock／deadline 做 exact private bytes readback；讀取 IO error 最多補一次（總共兩次），唔重寫／rollback。可核實等於今次預期 bytes 先 return due；呢個只係 process 可見 publication，唔聲稱 durability 或 harness 已收到 context。每次 readback 前後同 retry 前都核對原 deadline，唔延長 budget。Prepublish failure 保留原 cache，下次健康 hook 可再試。兩次 readback 都失敗、bytes mismatch 或 deadline 到期就無法確認，沿用 SessionStart unknown／其餘 silent；無跨 hook delivery acknowledgement，因此 multi-fault 或 process/output 中斷後，即使 cache 已 publish，仍可能有提示遺漏，唔聲稱 exactly-once／必達。

SessionStart/UserPromptSubmit/PostToolUse:Bash 只讀 Reality cached state，無模式靜默。每個 session/scope 要 material change **而且**至少 60 秒先提示；只刷新 as_of 唔算變。唔讀 tool_input/tool_response。PostToolUseFailure 可手動接同樣只讀 evidence；預設唔加。SubagentStart 可手動接純讀短 context，零 delivery 寫；預設唔加。

薄 hook stdin 64KiB、總 budget 250ms、lock wait 25ms，輸出固定安全 JSON，錯誤唔 block 原工具。Unknown 冇模式默認靜默；bookkeeping exception 只喺 SessionStart 用固定 unknown 提示，其餘事件靜默以免故障逐回合洗版；唔洩原 exception。普通 Bash success 永遠唔 mint pass。

## CLI 同接線

`kingdom-friction [--home ABS] brief/status --cwd ABS`；status 支援 `--json`，brief 支援 `--max-bytes`。`install [--dry-run]`、`uninstall`、`doctor` 只由明示操作呼叫。新 flag 錯誤唔 echo raw argv。CLI 嘅 `--home` 係 absolute test seam，唔係 silent relocation。

Native skill 用廣東話，叫使用者只包住已明示要跑嘅原檢查留收據，唔 run extra。Installed、hook capability、actual harness activation 分開；fake HOME tests 唔叫 live。
