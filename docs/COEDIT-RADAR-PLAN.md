# COEDIT RADAR — 實作驗收清單

共用 contract 同完整 suite 驗收由 main 維護；呢度只涵蓋共筆雷達 owner 範圍。

- [x] 按已鎖 `api.py` signatures 接 explicit observe/compare 同 session-only cached status/brief。
- [x] 有界 HMAC observer：1MiB、safe absolute path、逐層 nofollow、hardlink／secret／binary／PDF／notebook 排除、replacement／deletion／race。
- [x] Private cache：HMAC identity/source separation、256 entries／256KiB／TTL30m、25ms lock、corrupt 不修。
- [x] Allowlisted Pre/PostRead metadata 配對；offset／limit、default line cap、long-line、缺欄位／未知 shape／tuple collision 均 unknown。
- [x] Pre Edit/Write 只 additionalContext，有差異先提示；success/failure 清 baseline；普通 Bash 唔 mint pass。
- [x] CLI、hook entry（64KiB stdin／250ms）、common ProductSpec thin installer、skill。
- [x] Fake-HOME／自建 tmpGit focused tests，包括 secret open-never-called、canaries、readonly、session/source、dedup/full/corrupt、race 同 installer foreign preservation。
- [x] 255／256 explicit baselines overflow regression 先紅再綠；固定持久 global guard，multi-pending／completion 缺 token／Read poisoning／TTL 延長／CAS guard race／publication fault，全程唔 evict pending／consumed 證據。
- [x] 雙 clock API＋source CLI stale→wall -60s regression 先紅再綠；cache/2 boot-HMAC＋suspend-aware elapsed TTL，legacy shape unknown；native macOS 跨 process、Linux fixed-source mock、boot change／missing／anomaly／sleep／新 baseline 回調穩定／readonly zero-write。
- [x] Observer 中跨 TTL 嘅 Read pairing／preflight regression 先紅再綠，explicit compare 對照保守 unknown。
- [x] Terminal-empty-line 相容先紅再綠：原 physical count 保留，只容許實際尾 LF 嘅 reported +1；numLines==totalLines／startLine1／reported 上限2000不放寬。Post matching snapshot 提供 ephemeral flag，cache/2／core／store／API 未改。
- [x] 尾 LF／無尾 LF／空檔／terminal blank、1999／2000／2001、physical2000但display2001、offset／limit／extra／缺 metadata／partial／race 對照；Edit／Write × success／failure 完整 synthetic lifecycle 有 baseline、異動 advisory、完成清理。
- [ ] Main freeze 後統一 suite 回歸／portable bundle E2E。
- [ ] Main 核對今次 terminal LF 修正後嘅真 session baseline／additionalContext；main 已回報安裝及修正前 metadata，owner synthetic fixture 唔當修正後 live 驗收。

測試命令只選 `tests/test_coedit_radar_*.py`，用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider`。唔個別跑全 repo，唔寫真 HOME／既有 state／settings，唔更改其他 owner 文件。

2026-09-08 owner freeze：最終上述四個 focused files **182 passed，4.43s**；全部新 tmp／fake HOME、plugin autoload 關閉、bytecode/cacheprovider 關閉。新增紅燈證據：overflow 5 failed／1 passed；雙 clock API/source CLI 2 failed；observer 跨 TTL 2 failed／1 passed，再修到全綠。原 wall-only TTL fixtures 改為同步驅動權威 elapsed clock，過期／倒退 unknown 斷言保留（elapsed 倒退 reason 明示 clock_anomaly）。

只改既有 coedit runtime 檔，portable 逐檔 allowlist 無新增；cache/2 不兼容舊 cache shape，舊資料只 unknown、唔自動 migration。未跑 native Linux／真休眠或改系統 clock／真 harness activation／全 suite／portable build／本機安裝。Publication 完全失敗時只能當次 unknown，無法宣稱跨 process 記住未落盤 mutation；global guard 可能令無關 path 暫時 unknown。Main 整體／live 驗收未完成，唔勾選。

2026-09-08 terminal LF 窄修 freeze（獨立於上一段四檔收據）：先跑 `test_coedit_radar_hooks.py -k terminal_empty_line_metadata_lifecycle`，**4 failed／72 deselected，0.16s**，四個都喺 expected baseline_count1／actual0 證紅；再加對照同修 observer/hooks，三檔 `test_coedit_radar_{observer,hooks,core}.py` **258 passed，1.49s**。呢輪用自建 private tmp、fake HOME／TMPDIR、`PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`python3 -B -m pytest -q -p no:cacheprovider --noconftest -c <自建pytest.ini> --basetemp=<自建tmp>`。無全 suite／installer／CLI／portable 重驗。

Runtime 只改 observer.py／hooks.py；tests 只改 observer／hooks 兩檔，core tests 原樣納入回歸；文件只改本 PLAN 同 COEDIT-RADAR-DESIGN。無新增 persisted metadata，boot TTL／overflow／identity／safety 同原 budget 不變。Main 回報嘅真 Read metadata 只作 synthetic fixture 依據，唔收集或保存真 response payload；`read_bytes` 永久 unknown，兩個有界 snapshots 唔當 returned bytes 證書。修正後真 harness smoke 留畀 main，owner 完成即 freeze。
