# REALITY LANTERN — 實作及驗證清單

2026-09-08。capacity／archive 擴充同 suite 整合已完成，完整四燈驗收見 `LANTERN-SUITE-PLAN.md`；以下 focused／885-test 結果係歷史 checkpoint，唔代替最後驗收。用戶已批准本機跨工程 advisory 實況燈；詳細 contract 見 `REALITY-LANTERN-DESIGN.md`。

## Archive focused 驗證（歷史 checkpoint）

- [x] 純讀 capacity／明示 rotate --archive，pending 喺 Store lock 內拒絕。
- [x] Typed-state HMAC archive、strict file/dir fsync＋readback、no-clobber target 重用、32 archives／64MiB cap／noevict。
- [x] Archive durable 後 reset 失敗如實區分 not_reset／uncertain，唔盲目 restore/delete；空 state 重試唔再 archive。
- [x] Key 保留／缺 key 拒絕再造；next_sequence highwater／legacy 大 generation／late observe／負面證據跨 epoch 保護。
- [x] 新 archive regressions、原 core／Store／CLI focused suites，加 main 四條 history-boundary 反證：**209 passed in 5.39s**；全用 fake HOME，runner integer-full probe 原命令只執行一次。
- [x] Main freeze 後全 runner／四燈回歸、實體 portable bundle 安裝／archive／撤回均完成；各產品真 host activation 同未驗邊界見 suite 驗收。

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -B -W error -m pytest -q \
  tests/test_reality_lantern_archive.py tests/test_reality_lantern_core.py \
  tests/test_reality_lantern_store.py tests/test_reality_lantern_cli.py \
  tests/test_lantern_history_boundaries.py -p no:cacheprovider
```

首次 focused run 係199 passed／1 failed：新增 wide-directory fixture 受 umask 收窄，已喺自建 temp dir 明示 chmod 後重跑。Main 後來補四條 history-boundary 反證：generation filter 會錯誤丟棄真正較遲捕到嘅差異；最終 `_record_changes` 保留 timestamp-only 原語義，高水位只防舊 CAS 升 match。四條原 assert 全部保留並真跑通過；原 baseline tests 冇刪 assertions 或改 generation 假設。885 baseline 唔係 archive 驗證。


## 實作次序

- [x] 只讀探索現有 Quota／snapshot／hook contracts，提交並獲批准計劃。
- [x] 保留既有 dirty baseline；原 Quota 360 tests 通過（16.77s）。
- [x] 窄共用 private IO／transaction primitives，Quota 回歸。
- [x] 有界 Git worktree fingerprint，同不完整／不穩定降級。
- [x] Private receipts／outcome-validity projection／sticky stale。
- [x] Explicit process runner：single spawn、exit／signal、pending／fail-open；真 PTY 單 Ctrl-C 已修並反證。
- [x] CLI、短 command hooks、Claude／Codex 共用 skill。
- [x] 兩燈共存嘅可逆 installer／doctor。
- [x] Fake-HOME 真 source lifecycle E2E、fault／concurrency／secret canaries。
- [x] 獨立 review／反證、修正及最後全回歸。
- [x] Main 真 host Read／dry-run／merge 安裝。
- [x] 真 harness 接線檢查：本 session 實際收到 Edit 後嘅 additionalContext，唔只係 pipe test。

## 驗證原則

只喺自建 temporary repos／fake HOME 執行 synthetic tests，唔改外來工程作實驗。Tests、build 嘅 exit0 唔當全套覆蓋；版本 match 唔當 tests passed。Snapshot 上限／未涵蓋輸入／timeouts 如實報告。

## 最終驗證

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -B -W error -m pytest -q \
  tests/test_quota_lantern*.py \
  tests/test_lantern_common*.py \
  tests/test_reality_lantern*.py \
  -p no:cacheprovider
```

**885 passed、1 skipped in 58.31s**。42個主要 Python source／test 檔通過 AST／trailing-whitespace 檢查，測試前後 SHA256 不變。唯一 skip 係 APFS 不支援 non-UTF8 filename；正常 case-insensitive／`.GITIGNORE` cases 有真測，唔係 skipped。

- Main core／Store／跨模組 regression：140 passed；遲到 observation／completion／generation／CAS 都保留歷史反證，唔污染新 run。
- 真入口 fake-HOME E2E：安裝兩燈 → check → 改檔 stale → nonzero → 修正重驗 → 撤回 Reality、保留 Quota。
- Synthetic 順序執行兩個 SessionStart pipes：0.114311s。只係一次本地 fixture 量度，唔係真 harness latency 保證。
- 獨立 review＋skeptics 已逐項反證修正：nested scope、ignorecase／`.GITIGNORE`、FD leak、歷史差異、manifest fsync、Quota false-installed、單 Ctrl-C 重複轉發。最後 verified-open findings：無。
- Runner 額外21個獨立 probes：真 PTY／group SIGINT direct-vs-wrapped、stdin／foreground 還原、spawn failure；均按實際 child 結果記錄。

## 真實本機驗證

- Dry-run 五個 owned targets 無 collision；main 已 Read／merge 安裝。
- 安裝前後：所有非 hooks 設定相同、原 hook groups 完整保留、原 Codex guide 保留；冇改 model／routing／permissions／status line／登入。
- `kingdom reality doctor --json` 同 `kingdom quota doctor --json` 均 installed=true，五項 owned checks 吻合。
- Claude Code 已 discover 並實際載入 `reality-lantern` skill。
- 已安裝 PATH 入口對自建 smoke repo 記到 exit0／match；真正 `Edit` 改檔之後，**本 session 即時收到 `PostToolUse:Edit additionalContext`**，明示舊收據 stale。
- 隨後 `brief` 證實 stale，原 check 重驗成功，新 brief 回 `match@新時間`。收據／session digests 只留 device-local state，唔搬個人 IDs 入 repo。

## 限制／刻意不做

其他已開 Claude／Codex sessions 嘅 activation 不推斷；doctor 靜態 wiring 唔能證明所有 harness 已載入。Codex 係 soft guide／skill，唔冒認同樣 lifecycle hooks。

Python 3.11+；foreground preexec 只保證單-thread CLI，唔代理 Ctrl-Z／完整 job control；唔追 detached descendants。收據時間包括 bookkeeping，唔係精確 CPU/runtime。Ignored dependencies、環境、外部輸入、remote 未全面驗證。RAM rollback 唔係斷電 recovery；kernel IO／reap 無硬限時保證。

冇 commit／push／deploy／LLM polling／cron／daemon；既有 dirty／deletions 同另一 checkout 保持原樣。

## 撤回

`kingdom reality uninstall` 只移除仍吻合嘅 owned 接線，保留收據、Quota 同 foreign settings；衝突唔自行覆蓋。
