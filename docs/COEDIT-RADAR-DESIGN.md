# COEDIT RADAR — 有界共筆觀察

## 目的同 public contract

Advisory，唔係 Read 收據、寫入許可或作者歸因。共用 API／ownership 以 `LANTERN-SUITE-DESIGN.md` 為準。

`tools/coedit_radar/api.py`：

```python
status(*, home, cwd=None, session=None) -> dict
brief(*, home, cwd=None, session=None, max_bytes=512) -> str
observe(*, home, path, session, deadline=None) -> dict
compare(*, home, path, session, deadline=None) -> dict
```

結果只含固定 enums／計數：`status/reason/source/advisory/read_bytes`；`read_bytes` 永遠 `unknown`。Session status 可附 baseline/source/stale/unknown 計數，唔掃目標檔。冇 session 唔借用其他 baseline。`no_observed_difference` 只係兩個 observation 暫未見差異，唔係綠燈。

CLI `kingdom-coedit observe|compare` 強制 `--file` 同 `--session`。檔名要安全絕對路徑，唔自動 resolve alias。明示 session 只喺 process 記憶體／argv 存在，先 HMAC 後落 cache；唔宣稱 session identifier 係 credential。`status/brief/compare` 純讀，唔 mkdir／建 key／建 lock。`cwd` 保留 contract，唔用嚟借別人 scope。

## 觀察範圍

- 檔案最多 1MiB，UTF-8 text；逐層 directory descriptor ＋ `O_NOFOLLOW`，leaf stat/open/fstat/再 stat 核對。拒 symlink、hardlink、special files、PDF／notebook／已知 binary extensions；內容 control bytes／非 UTF-8／PDF magic 亦 unknown。
- 先調用 common 純 lexical `is_secret_path`，連 directory open 都未做。唔讀 secrets 去猜內容，唔存 raw file bytes。
- 觀察只回 keyed content HMAC、identity HMAC、version HMAC 同 size／LF physical line count／max line bytes／line-ending flags。`trailing_lf` 只係 ephemeral observation bool，唔落 cache，原 `lines` 計法不變。Identity 包含 device/inode，version 包含 size、mtime、ctime、mode、uid、link count；同內容換 inode 仍提示 identity changed。
- 同 pinned parent 兩次 ENOENT 先可描述 leaf 目前 missing；缺 ancestor、read 過程替換／mutation、directory chain 被換全部 unknown。唔承諾 sample 之後冇 race。

## Read hook allowlist

Pre/Post 用 `session_id + tool_use_id` HMAC tuple 配對。Pre 同 Post input 都只接受 `{file_path}`；任何 explicit offset／limit／pages／未知欄位都 unsupported，唔累積片段。只支援非空、LF、physical 同 reported 都最多 2000 行、每行最多 2000 bytes 嘅短 text；超出／CR／空檔保守 unknown。

Post response 只接受呢個 metadata shape：

```text
{type: "text", file: {filePath, content, numLines, startLine, totalLines}}
```

`content` 只驗係 string，唔計長度、比較、hash 或解讀。`numLines/startLine/totalLines` 必須係真正 int（唔收 bool／float），`startLine=1`、`numLines==totalLines`、`1<=totalLines<=2000`。保留原 `totalLines==physical lines` 格式；只喺實際檔案有 trailing LF 時，額外容許 `totalLines==physical lines+1`，對應 split-LF 保留 terminal empty line。無尾 LF 唔加一；physical 2000／reported 2001 一律 unknown，包括 numLines=2000 嘅 implicit truncation，唔因本地 physical count 未超標而接受。

Post 喺完整 request／有效 pending 配對下做原本有界 observer；先核對 freshness、bounds、前後 identity/content/version HMAC 一致，再把 matching Post snapshot 嘅 ephemeral `trailing_lf` 交畀 metadata helper。唔保存新 flag／response payload，cache/2 同 pending 欄位保持不變；缺 metadata／extra keys／partial 一律唔 mint baseline。成功先存 `claude_hook_observation`。呢個只證兩個 hook 時點觀察同 allowlisted metadata，**永遠唔證 Read 實際返回 bytes**。其他 harness／payload versions／截斷機制未識別就 unknown。

2026-09-08 main 回報已移除臨時 metadata-only probe：真 Read request 只有 file_path，兩行各尾 LF 嘅 UTF-8 檔案，allowlisted response 報 numLines=totalLines=3、startLine=1，Read display 有第三行空白。本輪 owner 只以自建 tmp 同 synthetic metadata 重現／回歸；冇讀真 scratch／payload，亦冇重驗修正後嘅真 harness activation。

重複／跨 path／跨 tool tuple 會退役疑似觀察；同 session 重複 Post 唔重建 baseline。新 Read 令同 path 未完成 token 退役，唔讓 interleaved call 借用另一張觀察。Write/Edit 前後會 poison 同 path pending Reads；已知 mutation pending 時唔建立新 observation。碰撞／缺 token 嘅 mutation 留 path-scoped pending 到 TTL，唔猜 completion。

## Preflight 同 completion

只用本 session `claude_hook_observation` 比較，唔借 `explicit_observation`。Edit／Write input 要已知 shape；未知只清舊觀察，唔回綠燈。有 content／identity／metadata／missing 差異先出固定廣東話建議重新讀取核對；輸出唯一結構係 `hookSpecificOutput.{hookEventName,additionalContext}`，冇 deny/block/updatedInput。

Preflight 已先退役自己 baseline；PostToolUse 同 PostToolUseFailure 再清自己 hook／explicit baseline。呢個 cache 唔係 filesystem lock，唔阻止工具、唔自動讀原文畀 model、唔自動重試。其他 session baseline 唔冒認成本 session 證據。一般 Bash success 完全忽略。

## Private state 同失敗行為

`~/.local/state/coedit-radar/{key.bin,cache.json,.lock}`，directory 0700／files 0600，private common IO；installation manifest 另檔。Cache schema `kingdom.coedit-radar.cache/2`：top-level `schema/key_id/entries/overflow`；每 entry 除原 metadata 加 `boot`（keyed HMAC）同 `elapsed`（boot-scoped seconds），`at` wall time 只作 metadata。`overflow` 係 null 或固定 `{at,elapsed,boot}` stamp。只保存 HMAC keys、source/kind/phase enums、timestamp 同有限數值，冇 raw path/session/tool token/content/prompt/command/output/transcript/credentials／boot UUID。v1／缺 clock／缺 guard 嘅舊 shape 保守 `state_corrupt/unknown`，唔默默 migration／修復。

最多 256 entries／256KiB（包括固定 guard），TTL 30 分鐘。TTL 只用同 boot 嘅 suspend-aware elapsed delta：Linux 固定 `/proc/sys/kernel/random/boot_id`（最多64bytes、nofollow）＋`CLOCK_BOOTTIME`；macOS 固定 libSystem `sysctlbyname(kern.bootsessionuuid)`＋`mach_continuous_time/mach_timebase_info`，都係 stdlib、冇 subprocess／network，唔由 wall time 推算 boot stamp。缺可核實來源就 `clock_unavailable/unknown`，同 boot elapsed 倒退就 `clock_anomaly/unknown` 並保留舊 state；boot 已換則舊證據失效。休眠算 TTL；wall 跳前／回調完全唔改 freshness，新 observation 亦唔會因 wall-derived boot identity 改變而反覆失效。Read-only 以時計算失效，唔建 lock／寫 stale flag；compare 同 Read pairing／preflight 喺 observer 後再核對 TTL。

已用 token tombstones 保留到 TTL，唔為腾位丟棄 pending／consumed 證據。容量滿唔加 entry，但 commit 已有退役。Mutation marker 放唔入時，原子同 cache 寫入一個 **global overflow uncertainty guard**：poison 所有 pending Reads，所有 session/path 嘅 observe、compare、status、Read pairing 同 preflight 都尊重；唔只回一次 cache_full 就忘記。多個 overflow 延長至最後一個 overflow 嘅 TTL，completion（包括無 token）永不提早清 guard；冇 unbounded tokens／paths／counter。Guard 期間唔建立 baseline，所以到期亦唔會復活 guard 前／期間嘅 Read 證據。代價係可能令無關 path 暫時 unknown，唔借此聲稱其他 session 有 baseline。

Corrupt／oversize／key mismatch／links 唔自動修復。Read-only 保留原檔；unknown 唔係健康證明。Cache publication／fsync／lock 失敗只報 unknown、fail-open；publish 後報錯唔 rollback 已落盤 guard。**若 mutation 記帳完全未能寫入，冇 durable evidence，就唔能保證之後另一 process 記住該事件**；唔聲稱磁碟失敗時仍有 filesystem lock 或跨 process 保證。TTL 到期亦只係 advisory evidence 已過期，唔代表工具已完成。

事件內 250ms（POSIX main-thread alarm ＋ cooperative deadline），鎖最多等 25ms；stdin 最多 64KiB，json duplicate keys／NaN／未知輸入拒絕。直接 threaded API 冇 main-thread signal，只能 cooperative deadline，唔宣稱硬 realtime。Bookkeeping 失敗 CLI/hook fail-open，但只回固定 unknown 診斷，唔回印 exception 或低信任資料。冇 daemon／network／model calls。

## 安裝

薄 `install.py` 只用 common `ProductSpec` 同 public product_install/uninstall/doctor；nonce／ownership／rollback 由 common 管。安裝 defaults dry-run，明示 `--apply` 先改設定；只新增 owned hook groups／wrapper／skill／Codex guide block，唔改 model/routing/permissions/login/statusline/manual lantern。Uninstall 唔刪 cache。SubagentStart 唔採樣、唔開 state、唔借 parent session，只講固定 unknown。
