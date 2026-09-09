---
name: friction-lantern
description: 本地可比失敗收據嘅 soft advisory；唔推論空轉，唔自動重驗。
---

# 阻力燈

呢盞係 **soft advisory**，唔係開工前置、評分、阻擋器或者叫你多跑 tests。

按需要睇 cached 模式，唔做 workspace scan、唔解析 command/output，唔會跑額外檢查。

```sh
~/.local/bin/kingdom-friction brief --cwd /absolute/workspace
~/.local/bin/kingdom-friction status --cwd /absolute/workspace --json
```

只有原本已明示要跑嘅檢查，先用實況燈包住**同一條原命令**，執行一次並留真收據。

```sh
~/.local/bin/kingdom-reality check --kind tests --cwd /absolute/workspace -- python3 -m pytest tests/test_selected.py
```

以上係用法例子，唔係自動執行指令；唔因為讀到 skill、模式或收據不足就 run extra。
普通 Bash success 永遠唔係 pass receipt。

模式只描述全域 recorded sequence 最近三張連續可比收據：同 scope/check/kind/nonzero exit code、完整同版本、projection match，上一張完成先於下一張開始。
Interleave、pending、exit0、signal、spawn_failed、stale、unknown、clock 倒置、容量滿會抑制。
有模式只係截至列出嘅比較時點；唔證明中途冇閱讀、冇新證據、同一原因、同一 agent 或而家仍失敗。
冇模式亦唔係綠燈。環境、線上、未落帳工作同執行者未知。

可以考慮另一個診斷角度，但由你按當前任務決定；唔改 model/routing/permissions/login/statusline/manual lantern。

`install --dry-run`／`install`／`doctor`／`uninstall` 係明示 owned 接線操作。
Doctor 只驗靜態接線，唔證明 harness 已載入；Codex 用明示 CLI，唔冒稱有 native lifecycle hook。
自家 delivery cache 只保留 HMAC／metadata；唔寫實況燈 state。Uninstall 保留 cache 同收據。
