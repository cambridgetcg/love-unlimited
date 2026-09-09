# LANTERN SUITE — 實作及驗收清單

2026-09-08。本機＋可攜分發已完成；以下記錄發布前驗收，當時未 commit／push／deploy。其後用戶明示要求 push and deploy：採 private GitHub Release 分發，唔涉及 SSH／remote host install／fleet boot。Contract 見 `LANTERN-SUITE-DESIGN.md`；發布套件嘅 README／VERIFICATION 記錄對應 commit、digests 同重驗結果。

- [x] 用戶選定本機＋可攜，正式批准計劃；保留 dirty baseline 同單一 writer boundaries。
- [x] Common owned installer、共用 secret-path policy、legacy-compatible installation nonce。
- [x] Reality capacity、durable archive、generation highwater 同歷史反證時間界線。
- [x] Friction 可比收據模式、CLI／advisory hooks、bounded delivery reconciliation。
- [x] Coedit cache/2、boot-scoped TTL、overflow uncertainty、explicit observe／compare。
- [x] Suite entry／membership、2→4 adoption、nonce 保護撤回。
- [x] Portable 60-file allowlist、trusted bootstrap、永久 root preflight／安裝。
- [x] YOUI／Sovereign／kingdom-agent discovery seams；170 focused adapter tests。
- [x] 獨立 review：八項確認問題已修，逐部分獨立驗修通過。
- [x] 真 Read 顯示 trailing LF 多計末空行；窄相容修正，partial／截斷仍 unknown，獨立驗修通過。
- [x] 最終 freeze、完整四燈回歸、Python／Node syntax 同 source hashes。
- [x] Main 真 HOME Read→dry-run→owned merge；原兩燈同非 hooks 設定保留。
- [x] 真 Coedit Read→模擬外部改動→Edit additionalContext；Edit 保留外部改動，baseline 退役。
- [x] 真三次 exit 7 scratch 收據→Friction CLI pattern；自然 Bash context 因 HOME cwd 邊界未驗，如實標示。
- [x] 最終實體 tar 另做 fresh fake-HOME install、五入口 doctor、Reality check、archive、uninstall。
- [x] 本地產物、完整 digests、接入／撤回指引及能力限制交付。

## 最終證據

**2093 passed／1 skipped in 93.38s**，40 個四燈 test files。唯一 skip：測試 filesystem 唔支援 non-UTF8 filename。105 個 runtime／測試／設定檔 hashes 前後一致；94 Python files AST、3 Node files syntax 通過。

測試 stdout、freeze 同 artifact smoke 詳細記錄只存本機 temporary 檔案，唔入 Git 或分發包，亦唔保證永久保存；對外只交付驗證摘要。

之前 baseline／checkpoint：885／1617／2002 passed，各有1 skip；唔相加，唔代替最後版本。Shared-secret integration 曾獨立跑184 passed／1 skipped，最終亦已包含。

八項確認修正：nonce 多 cycle ABA、current-CAS 早期 snapshot 錯 stale、Friction highwater gaps、delivery publish 後 fsync failure、Coedit 滿額 pending 遺忘、TTL wall-clock 回調、suite 結果 ledger 失敗漏報進度、portable HOME-root／Unicode 私人路徑漏攔。另補真 harness terminal-LF 行數相容；唔修改其他既有 safety assertions 去換綠。

小型 synthetic scratch、10輪、matching hooks 並行、含 Python startup：事件 median 約44–69ms，最大83ms。唔係最壞情況或 SLO 證書；原 bounded deadlines 冇放大。

## 本機接入同 activation

五入口 doctor installed=true。Quota／Reality membership 係 preexisting；Friction／Coedit／entry 係 created。原兩個 wrapper／manifest、原 hook groups、AGENTS prefix、global CLAUDE 同非 hooks 設定均保留。

三個新 Claude skills 已實際載入。Coedit 真 `PreToolUse:Edit` additionalContext 已收到；唔 block，未宣稱 Read returned bytes。臨時 metadata 診斷 hook 已撤回，冇保存 raw payload。

Friction 真三張可比 nonzero 收據及 CLI／suite brief 已驗。此 harness 將 Bash cwd 固定 HOME，`cd` 會 reset；唔解析 command 猜 workspace，所以自然 Bash context 未驗。跨工程用 `brief --cwd`；唔用 pipe test 代替 activation。

Python／Node helpers 已讀真 entry；YOUI／Sovereign／kingdom-agent 只驗 source adapter／離線 caller，冇 boot 模型。Codex skills／AGENTS 已安裝，Codex harness 未驗；Linux 未實跑，Windows 不支援。冇 remote 操作。

## 可攜交付

發布前本地包（以下舊 digests 唔可當 GitHub Release 包嘅 expected SHA）：

- `kingdom-lanterns.tar`：522240 bytes；60個 allowlisted source 檔＋manifest。
- `lantern-bootstrap.py`：可信 source 副本；先獨立核對，唔執行未驗 tar 內 code。
- `SHA256SUMS`、`README.md`、`VERIFICATION.json`：完整 digests、接收方操作同證據界線。

Archive SHA-256：`e5c95d5adfe17c6122de93c4fe07d769dbbb283e2b5143fb230d60bc7c796ee5`

Content digest：`9f816d33f946e0dac5edecea63e1261a2745ee735b88f96fcf20ade7971061f2`

Bootstrap SHA-256：`254f902dbc2ee62c7ece93652b9f427e5aa979dee550da358b6962e4f5d4ee5f`

上述係發布前 working-tree 包，base revision 唔係 release commit；checksum 唔係 signature。之後發布須由 release commit 重建，所以 archive SHA 會不同；以指定 Release 嘅 README／VERIFICATION 為準。Bundle 冇 HOME／state／auth／logs／bootchain。

預設 `~/.local/bin/kingdom-lanterns uninstall --json` 只撤 suite confirmed-created 且 nonce 吻合嘅 additions；呢部 Mac 原兩燈保留。Evidence／archive／nonce history／永久 root 唔刪；uncertain／foreign drift 先人工核實。
