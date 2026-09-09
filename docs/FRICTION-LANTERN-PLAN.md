# 阻力燈 — 實作同測試計劃

唯一 source 係本 repo；只用 fake HOME／自建 tmp workspace。唔 run 真 installer/check/doctor，唔碰真收據，唔 spawn 模型／network／fleet。

1. 純 core：三張 nonzero／完整同版本／match；反例涵蓋 interleave、pending、非 nonzero、不同 exit/kind/check/scope、incomplete/stale/unknown、clock order、容量滿、壞 schema；合法 sequence highwater gaps 係 acceptance case，唔係 suppression。
2. API：缺 state 零 mkdir/key/write；只讀既有 v1 state、scope 不能向 nested repo 借證據；canary 唔入 status/brief。
3. Delivery：自家 HMAC state、material-change＋60s、clock 倒退、256項上限、private links／corrupt／lock timeout／write failure；Reality bytes 不變。
4. Hook/CLI：固定 JSON／64KiB／250ms／25ms；普通 Bash success 零收據；SubagentStart零寫；stdin stall、unknown flags、低信任 canary、錯誤 fail-open。
5. Thin installer：等 common ProductSpec ready 後，用 fake HOME 驗 dry-run、install、doctor、nonce、uninstall，保留 foreign settings／guides，唔建替代 production helper。
6. 只跑 `tests/test_friction_lantern*.py` focused tests，main freeze 後先統一回歸。Actual harness activation 由 main 最後獨立驗，pipe fixture 唔代證。

## 2026-09-08 confirmed 修正驗收

- 真 Store `start_run/finish_run/invalidate(changed=False)/observe` 建立 `[1,4,5], next=6` 同 `[1,2,3], next=5`；先 2 failed／3 passed（合法 gaps 被 sequence_gap 抑制），只修 derive 後 renderer 仲有 1 failed／4 passed。修 renderer 後 core＋e2e 67 passed。保留最新 foreign scope/check/pending barrier；API／hook 後 Reality bytes 一樣。
- 新 delivery fault tests 用真 `atomic_write`，分開注入 rename 前 EIO、rename 後 directory fsync 一次 EIO、一次／持續 readback EIO。先 10 failed／4 passed；deadline／stalled readback 新例先 5 failed。修正後 delivery＋hooks 85 passed，再加 exact-bytes（等價 JSON 唔夠）對照。
- 最後只跑五個 `tests/test_friction_lantern_*.py`：**160 passed**。所有 commands 用新 `/private/tmp` fake HOME／basetemp，`PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`python3 -B` 同 `-p no:cacheprovider`；冇全 suite／真 HOME／remote／harness smoke。
- Public API、delivery schema、state 檔清單、bundle allowlist 不變；只新增 focused test 檔。原 tests 中三個「gap 必抑制」assertions 依明示修正 contract 改成 acceptance，並補 renderer impossible-count 反例，唔放寬 interleave／pending／時序／容量界線。
- Freeze：readback 核實唔當 durability／harness acknowledgement；兩次讀唔到或 deadline 到期仍無法確認，可能有提示遺漏。故障仍 SessionStart unknown／其他事件 silent，唔 rollback 已 publish bytes。
