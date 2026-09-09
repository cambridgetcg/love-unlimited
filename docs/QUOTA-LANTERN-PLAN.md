# QUOTA LANTERN — 實作及驗證清單

2026-09-07。用戶已批准 Mac-wide advisory awareness；詳見 `QUOTA-LANTERN-DESIGN.md`。實作、本機安裝及 smoke 已完成；本 session 其後亦已實際收到 hook context，其他 sessions 不推斷。

## 已完成

- [x] Schema／freshness／source isolation fixtures同core。
- [x] Private observations、atomic cache、bounded local source。
- [x] Experimental live reader＋fake protocol deadline／cleanup tests。
- [x] CLI、feedback／hypothesis／reflect、有界localwatch。
- [x] 非阻擋Claude hooks、Codexsoftpointer、共用skill。
- [x] Fake-HOME reversible install／uninstall／doctor。
- [x] 整合offlineE2E、獨立review、逐項反證及修正。
- [x] 真實安裝dry-run、Read／merge既有settings、安裝owned additions。
- [x] 本地snapshot smoke，保留原routing/permissions/login/statusline。

## 實際驗證

```sh
PYTHONPATH="$PWD/tools" python3 -B -W error -m pytest -q tests/test_quota_lantern*.py -p no:cacheprovider
```

最終：**360 passed in 16.03s**。另19個新增Python檔通過AST／trailing-whitespace檢查。所有測試係fake HOME／synthetic fixtures／fake Codex child；冇模型turn／真live網絡請求。

覆蓋：usage-only唔覆蓋rate evidence、metadata-only activity、日期overflow、mtime選檔、fresh／stale讀數priority、512／1024-byte完整brief、研究結果feedforward、同時矛盾endpoints、未引用中間樣本、journal-full診斷、兩工程真source lifecycle、SessionStart重入、SubagentStart cache-only、並行鎖、post-commit fsync failure、rollback序列化、settings／secrets canaries。

真實本機：

- Dry-run只列wrapper、兩個skill links、CodexAGENTS、Claude settings五項；無collision。
- 安裝後逐key比較：全部非hooks設定同安裝前相同；四個hook全部command type、timeout 2秒。
- `kingdom quota doctor --json`：`installed=true`，五個owned targets全部吻合。
- `kingdom quota observe`／`brief`／`status`成功讀取本地白名單metrics，stale、unknown同partial coverage明示。
- 以一筆實際observation建立一份feedback同一份數值hypothesis；`reflect`同下一次`brief`已顯示結果。個人snapshot、IDs同筆記只留device-local state，唔搬入repo。
- Claude Code已discover同成功載入`quota-lantern` skill。
- 冇commit／push，冇改另一棵`~/love-unlimited`，原本dirtywork保持。

## 未驗證／刻意唔做

- 安裝當時未有真 SessionStart delivery 證據；其後**本 session 已實際收到 UserPromptSubmit／SessionStart additionalContext**。呢項係真正 harness 注入證據，唔係從 state 檔案推斷；其他已開 Claude／Codex sessions 仍未驗證。
- Claude可以開`/hooks`檢視／reload，再喺下一次正常session確認brief。Codex global指引每次run建立，已開run唔假設熱載入。
- `observe --live`只完成fake transport驗證，今次冇跑真live；唔聲稱app-server production-stable或included quota換算公式已破解。
- 冇LLM polling、cron、daemon、remote fleet、paid fallback或hard admission gate。
- Rollback journal係RAM；冇做SIGKILL／斷電transaction recovery。

## 撤回

`kingdom quota uninstall`只移除仍吻合嘅owned additions，保留研究資料同用戶後續變動。Collision唔會自行覆蓋；`doctor`只讀診斷。
