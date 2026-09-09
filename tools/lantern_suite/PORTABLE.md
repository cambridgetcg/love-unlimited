# 四燈 portable guide

四個獨立 advisory 產品加 `lanterns` 入口。Python 3.11+，macOS／Linux 目標；Linux 未實跑唔當已驗。
冇 network、auth、auto-fetch、model turn、daemon 或 silent upgrade。Checksum 唔係 signature；bootstrap 同 expected SHA 要由可信渠道另行取得，唔好先執行未驗 archive 入面嘅 bootstrap。

## CLI

Source 入口係 `python3 -I -S -B tools/kingdom-lanterns.py`；安裝後係 `~/.local/bin/kingdom-lanterns`。
以下 `ABS` 代表你明示揀嘅 absolute path；唔係叫你複製 placeholder。

```text
kingdom-lanterns [--home ABS] list [--json]
kingdom-lanterns [--home ABS] brief [--cwd ABS] [--session TOKEN] [--max-bytes 768..4096]
kingdom-lanterns [--home ABS] status [--cwd ABS] [--session TOKEN] [--json]
kingdom-lanterns [--home ABS] doctor [--json]
kingdom-lanterns [--home ABS] install [--dry-run] [--json]
kingdom-lanterns [--home ABS] uninstall [--json]
kingdom-lanterns bundle allowlist
kingdom-lanterns bundle build --output ABS [--base-revision HEX]
kingdom-lanterns bundle verify --bundle ABS --expected-sha256 HEX
kingdom-lanterns [--home ABS] bundle install --bundle ABS --expected-sha256 HEX
python3 -I -S -B TRUSTED_BOOTSTRAP verify --bundle ABS --expected-sha256 HEX
python3 -I -S -B TRUSTED_BOOTSTRAP stage --bundle ABS --expected-sha256 HEX [--home ABS]
python3 -I -S -B TRUSTED_BOOTSTRAP install --bundle ABS --expected-sha256 HEX [--home ABS]
```

`list/brief/status/doctor` 只讀，唔重新掃 snapshot／live quota／mint 收據。
Quota／Reality details 沿用各自 CLI；Friction／Coedit 缺證據保持 unknown。
`--session` 只供本次 cached lookup，suite 唔保存 token。

## Source install 同 ownership

先 `install --dry-run --json` 睇計劃，再喺明示授權下 `install --json`。
健康兩燈 legacy v1 只 adopt，唔重裝／改 nonce；新增產品同 entry 分別由自己 installer 持有 artifacts。
Entry 只 wrapper／native skills／AGENTS block，冇 settings hook。
Suite 獨立 private `~/.local/state/lantern-suite/membership.json`，先 durable requested nonce intent，再記 created／preexisting／failed／uncertain。

預設 uninstall 只撤 suite confirmed-created 並由產品鎖內核對 expected nonce；preexisting 留低。
Foreign drift／reinstall／crash gap／durability uncertain 一律保留，停止該輪 partial operation；唔跨產品全域 rollback。
遇 uncertain 先睇 doctor／各產品結果，人工核實再決定；唔刪 membership 來猜 ownership。
Evidence／archive／cache 保留。撤回唔刪永久 source root；先確認冇 wrappers／hooks／skills 指向佢，先另行處理。

## Bundle 同永久 root

`bundle build` 只收逐檔 allowlist（`bundle allowlist` 可列全），唔 glob repo／未跟踪檔／tests／HOME／state／credentials／boot docs。
明示包含兩個純 runtime discovery helpers `tools/lantern_suite/advisory.py` 同 `advisory.mjs`；唔包含 YOUI／Sovereign 本體。
已 allowlist 嘅新 runtime 即使未 commit 都按 working-tree bytes 打包；`base_revision` 只 metadata，唔冒稱 bytes 已 commit。
版本係 canonical manifest file entries 嘅 SHA256；archive SHA256 另計。固定 USTAR metadata／排序／mode，source 前後 bytes 改就拒絕。
Build 喺 publish 前對有界 UTF-8 source 做 lexical guard：拒絕 NUL，同 `/Users`／`/home` 下 literal username token（包括 Unicode／combining marks）、HOME 根或子路徑；空白／引號／EOF 都可結束根路徑。
Regex／template metacharacters 唔當 username token。呢個唔係通用 secret scanner，唔解碼轉義／拼接路徑，亦唔保證攔晒其他位置或形式嘅敏感內容；仍須審閱可信 source。

Trusted bootstrap 先驗 expected archive SHA、完整 manifest、每檔 digest／size／mode，再 copy。
拒絕 duplicate、extra、缺檔、absolute／traversal、symlink／hardlink／special、非 canonical headers、PAX／GNU extensions、trailing archive，同超額資源。
上限：archive 16MiB、payload 12MiB、每檔 2MiB、manifest 64KiB、128 entries；只接受未壓縮 `.tar`。

`stage` 只安置 private `~/.local/share/kingdom-lanterns/<content_digest>`，唔執行包內 code／唔裝 hooks。
Suite `bundle install` 只薄委派當前可信 source 嘅同一 standalone bootstrap；用 suite 全域 `--home`（放 `bundle` 前），冇另一套安裝路徑或自動下載。
`install` 全驗及 permanent copy 後先由永久 root 跑 suite owned `install --dry-run --json` preflight，成功先喺同一 root 跑正式 install；preflight 唔另造 installer／路徑，亦唔保證之後無 race。中斷 staging 留低，不當成功；錯 digest／manifest 喺 staging／exec 前拒絕，唔寫目標 HOME。
`bundle install` 係明示寫入操作，唔提供 `--dry-run`。只想核對包而唔寫 HOME，用 `bundle verify`；`stage` 已明示會寫 private 永久 root。
同 digest 已有 root 要全檔／mode／extra entries 一致，包括 `MANIFEST.json`；唔覆寫。只改 `base_revision` 雖然 `content_digest` 相同，但 manifest／archive SHA 不同，重用既有 root 會 `bundle_root_conflict`；唔合併 metadata 或改版本語義。
舊 source wrappers 或其他 digest install 衝突會拒絕，唔 silent relocate；可能保留已驗 private root／失敗 ledger，唔自動撤回原安裝。需要搬 source 就先另行明示處理既有 ownership。
Source／bundle staging 之後移走都唔影響永久 root；root 仍要留住。

## 證據界線

fake-HOME fixture 唔係真 harness activation。CLI／pipe 成功唔證明 additionalContext 已載入。
Installed、capability、activation 分開。Unknown 唔係 green；普通 Bash success 唔 mint pass。
Shared lock 係協作防撞，唔係同 UID 惡意程序嘅 sandbox；checksum 信任亦唔取代供應來源信任。
