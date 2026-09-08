# Lantern suite：本地 agent 按需入口

呢頁講 discovery adapter，唔係安裝／activation 證書。共用 contract 見 [LANTERN-SUITE-DESIGN.md](LANTERN-SUITE-DESIGN.md)；按需用法見 [suite skill](../tools/lantern_suite/skill/SKILL.md)。Source root 嘅 [CLAUDE.md](../CLAUDE.md) 只加短入口，唔取代手動 `/lantern` 留燈。

## 入口做咩

- [Python helper](../tools/lantern_suite/advisory.py)：`installation_guide(*, home=None) -> str`。
- [Node helper](../tools/lantern_suite/advisory.mjs)：`installationGuide({ home } = {})`。
- `home` 係目標 HOME locator／離線測試 seam；省略先用 runtime 嘅 home resolver。唔掃環境變數、產品 cache、credentials、settings、收據或其他 session。
- 有效入口只回 source 固定 `GUIDE`；Python／Node 同字，UTF-8 最多 768 bytes。缺 entry、unsafe、corrupt、hash drift 或 IO 出錯，一律空字串，唔印 exception，唔阻 boot。
- 唔起 CLI／subprocess／network／LLM，唔建 state、鎖檔、收據或送出 prompt。呢個 helper 只回字串，由原 caller 照既有流程使用。

固定指引只話 suite 入口可用，**唔話四燈已啟用／健康**。按當前需要同原權限，先決定用唔用現有 `bash` 呼叫 `~/.local/bin/kingdom-lanterns status` 或 `~/.local/bin/kingdom-lanterns doctor`；冇新工具 schema、強制檢查或自動安裝。

只有本身已獲授權嘅本地檢查，先用 `~/.local/bin/kingdom-reality check -- 原命令` 包住原命令一次。唔重跑嚟湊證據；普通 Bash exit 0 唔會鑄成 pass。Unknown 唔當 green；命令結果同 bookkeeping 問題要分開講。Model、routing、permissions、login、statusline 同原手動留燈都冇改。

## 核對邊界

只讀兩個固定 leaf，同由 HOME 到佢哋嘅必要目錄 metadata：

1. `~/.local/state/lanterns/installation.json`：regular file、owner 係目前 effective UID、mode `0600`、單一 hard link；直接 parent `lanterns` 要 `0700`。
2. `~/.local/bin/kingdom-lanterns`：owned regular file、相同 private owner、mode `0700`、單一 hard link；SHA-256 要吻合 entry artifact。

由 HOME 開始嘅父目錄唔可以 group／world writable，唔跟 symlink。兩個檔各最多 16 KiB；JSON 要 UTF-8、有界深度／項目數、冇 duplicate keys／nonfinite／非法 Unicode。頂層只接受 `schema/repo_root/installation_nonce/artifacts`，schema 必須係 `kingdom.lanterns.installation/2`，nonce 要 canonical UUID。

`repo_root` 只係 bounded absolute source locator，**唔攞佢再開檔**。`artifacts` 只接受約定 shape；必須有 owned wrapper。其餘 skill／AGENTS artifact 只驗 metadata shape，唔讀嗰啲檔，亦唔保證佢哋仍可用。Manifest 任意 guide／command／path 欄位會被拒絕；就算 source locator 或 wrapper bytes 帶文字，都唔會載入 prompt。Wrapper 驗完會再讀 entry，比對安裝 metadata 有冇變。

### Race 同信任限制

- Python 重用 common 嘅 descriptor-relative `dir_fd`／`O_NOFOLLOW` 逐層讀取；leaf 讀前後核對 inode／mode／owner／size／timestamps。
- Node 用 stdlib 逐層 `lstat`／`open(O_NOFOLLOW)`，保留 parent descriptors，讀前後再核對 parent／leaf identity。**Node 冇 `openat`，呢個唔係 descriptor-relative 強保證**；parent ABA（換走再放返）、同 UID 攻擊仍有限制，唔聲稱 race-free。
- 兩者由目標 HOME 開始，以 host 對 HOME 上層嘅信任為前提。唔係對抗已控制同一 UID／root 嘅攻擊者；entry 同 wrapper hash 吻合亦唔係 source signature。
- 入口只係當次 bounded observation；之後撤回／改檔／執行仍可有 race。唔核對 source runtime 能否啟動，唔證明全部產品 installed，更唔證明 harness 已載入。

## 呢部 Mac 嘅整合驗收（2026-09-08）

- 五入口 installed；三個新 Claude skills 已實際載入。原兩燈、原設定／hook groups 保留。
- Coedit 真 Read→模擬外部改動→Edit additionalContext 已收到；Edit 唔被擋、外部改動保留、baseline 退役。Terminal-LF metadata 相容只接受已觀察嘅末空行格式，唔放寬 partial／截斷。
- Friction 用真三次 exit 7 scratch 收據驗到 CLI pattern。此 harness 嘅 Bash cwd 固定 HOME，唔由 command 猜 repo；自然 Bash context 未驗，跨工程可明示 `brief --cwd`。
- Python／Node helpers 已各自讀真安裝 entry，回同一固定指引；下面 runtime callers 仍只係離線測試，冇 boot 或送出模型 prompt。
- Codex skills／AGENTS 已安裝，harness 未跑；remote／Linux 未驗。完整2093 passed／1 skipped、產物同限制見 [驗收清單](LANTERN-SUITE-PLAN.md)。

## Native／manual 能力矩陣

| Runtime | Discovery 接點 | Native 自動提示 | Manual 能力同限制 |
|---|---|---|---|
| Claude Code | source CLAUDE 短入口；安裝後 skills／原產品 hooks | 由各產品 hook contract 決定，仍須真 harness smoke 核實 | 沿用既有 Bash／skills 同 permissions；讀到入口唔算 activation |
| YOUI | [youi.mjs](../youi.mjs) 原 `buildSystemPrompt` 加固定 guide | 只有入口提示；冇新增 tool lifecycle hooks | 按需用原 `bash`；冇自動 Reality check／Coedit Read receipt |
| Sovereign | [sovereign.mjs](../sovereign.mjs) 原 `buildSystemPrompt` 加固定 guide | 同上 | Model／provider／tools 保留，guide 唔做 routing |
| kingdom-agent direct backends | [tools/kingdom-agent.py](../tools/kingdom-agent.py) `load_boot_chain`，full／medium／lite 都適用 | 只有入口提示 | 原 tool schemas 不變；唔自動建立產品觀察 |
| kingdom-agent Claude CLI passthrough | 唔經新增 boot-chain adapter | 由 Claude Code 自己處理 | 唔重複 append suite guide；原 `system_append` 照傳 |
| Codex | 安裝後 skill／AGENTS 指引 | 呢個改動冇新增 Codex native hook integration | 只按需 CLI；guide discovery／activation 要按 host 核實 |
| 其他／remote／Linux host | 未替佢哋接 runtime | 未驗 | 本地 helper 或 portable bundle 存在唔代表遠端已部署／啟用 |

## 離線驗證方法

Focused tests 喺 [tests/test_lantern_adapters.py](../tests/test_lantern_adapters.py)，唔跑 agent 主迴圈。

- Python／Node 各自驗有效／缺失 entry、固定短 guide、canonical schema、JSON bounds、unsafe owner／mode／links／FIFO、drift、讀取期間變動同無寫入。
- 用 fake HOME 同自建 temporary Git repo，實際 common entry installer 產生 manifest／wrapper，再驗兩個 helper 接受；撤回後靜默。冇跑 wrapper／四產品／真 installer。
- Python 只 import 有 main guard 嘅 kingdom-agent，跑 fake boot chain；另驗 `--boot-chain-only`。Backend constructor／auth 唔啟動，Claude passthrough 只 mock 原 subprocess boundary。
- Node 用 VM 載入原 runtime declarations 同原 `buildSystemPrompt`，只截去最後 terminal `main()` invocation；kernel、git subprocess、terminal／network／寫檔用 mocks，boot files 全係 fake HOME fixture。呢個係 actual caller body 嘅離線測試，**唔係真 terminal／模型 activation smoke**。

由 source root 跑自己範圍：

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/test_lantern_adapters.py -p no:cacheprovider -q
```

要有 Node，VM 測試由 fixture 傳 `--experimental-vm-modules`；唔裝 npm package。全套回歸、真 HOME 安裝、真 Read／Edit／Bash additionalContext 同跨平台 activation，由整合驗收另行核實；唔用 fake fixtures 代替。
