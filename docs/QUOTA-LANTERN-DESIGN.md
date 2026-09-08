# QUOTA LANTERN — 知情、自在、可研究

2026-09-07。狀態：已實作、本機安裝及 smoke 通過；原有360個 offline tests 通過。本 session 其後已真正收到 UserPromptSubmit／SessionStart hook brief；唔由此推斷所有已開 Claude／Codex sessions 都已載入。

## 目的及界線

呢部 Mac 跨工程嘅 advisory 用量燈。正常工作 → 白名單用量觀察 → deterministic feedback → 下一次 brief 帶 feedforward。唔阻擋 agent、唔改 model／routing／permissions／登入，唔自動派工或消耗 credits。沒有額外 LLM loop；brief 本身仍有少量 context 成本。

只喺本 checkout 新增程式，沿用 `tools/kingdom` 嘅 PATH plugin fallback，由新 `~/.local/bin/kingdom-quota` 指向呢度；舊 `kingdom`、另一個 checkout、手動 `/lantern` 保持原樣。

## 證據模型

Schema `kingdom.quota-lantern/1`。Included quota、credits、token activity分帳；API quota唔由Codex推算。Account未核實就source-isolated。時間、coverage、identity、pressure、availability分開。重讀舊event唔刷新evidence時間；重讀同一筆唔增加樣本。`primary`唔固定5h，reset過時唔代表恢復，缺值唔補零，Δpercent=0唔代表免費。

資料只存在device-local private store，唔入Git：`~/.local/state/quota-lantern/`。JSONL係機器觀察 evidence，Markdown係人／agent明示提交嘅研究文字；JSON caches全部可重建。唔保存raw transcripts／RPC／hook payload／secrets。

## 模組協作 contract

以下係第一版共同 API；跨模組改動先對齊，唔由各檔自行猜 schema。所有 public data 都係 JSON-compatible dict/list。時間字串用 UTC ISO8601；`now` 可省略或傳 aware datetime。`deadline` 係 absolute `time.monotonic()` 秒數。

### `core.py`

- `SCHEMA_VERSION = "kingdom.quota-lantern/1"`
- `LanternError(code: str)`：固定、安全 error code，唔把原始內容塞入message。
- `utc_now() -> str`
- `normalize_rate_limits(raw: dict, *, source_kind: str, source_id: str, source_version: str | None = None, observed_at: str | None = None, collected_at: str | None = None, usage: dict | None = None, coverage: dict | None = None, account_ref: str | None = None) -> list[dict]`。接受log snake_case或RPC camelCase／response envelope，多bucket去除legacy mirror，唔相加。
- `build_status(observations: list[dict], *, now=None, freshness_seconds: int = 300, diagnostics: list | None = None, research: dict | None = None) -> dict`。
- `render_brief(status: dict, *, max_bytes: int = 1024) -> str`：只用固定模板＋白名單derived facts，唔插入研究note原文。
- `status_fingerprint(status: dict) -> str`：只含有意義狀態，排除當下時間／不斷增加嘅age，供節流。

Normalized observation欄位固定：

```text
schema, id, provider="openai", product_pool="work_codex",
source={kind,id,version,experimental}, observed_at, collected_at,
account_ref, identity_status, bucket_id, plan_type,
windows={primary: window|null, secondary: window|null},
credits, ordinary_usage_allowed, rate_limit_reached_type, spend_control_reached,
usage, coverage, validity={valid:bool,issues:[safe_code]}
```

Window：`{used_percent, window_minutes, resets_at}`；nullable原值保留。Credits：`{has_credits, unlimited, balance}`或null。Usage只接受input/cached_input/output/reasoning_output/total數字，統一key為`input_tokens/cached_input_tokens/output_tokens/reasoning_output_tokens/total_tokens`。Observation ID由已驗證內容＋source/event time穩定hash，唔包括collection time／coverage。

Status：`{schema, generated_at, mode:"advisory", billing_route:"unverified", sources:[...], research:{...}, diagnostics:[safe_code]}`。每個sources item係normalized observation再加`evidence_kind, freshness, age_seconds, pressure, availability`。各source/bucket分開保留最新rate snapshot同純activity snapshot，原ID／時間保持，唔跨event拼quota／credits／allowstate，唔整全機假總額。Brief優先fresh高壓，全部stale時先顯示較新證據，明示略過快照數。Pressure `roomy/mindful/conserve/unknown`，80%／95%係本地advisory門檻；availability只由explicit證據判，缺值unknown。

### `store.py`

`Store(*, home: Path | None = None, state_dir: Path | None = None)`。Constructor唔建檔。

- `read_latest(*, cache_only: bool = False) -> list[dict]`，`read_observations(limit: int = 2000) -> list[dict]`：有界純讀，唔建檔；缺檔回空，損壞報安全錯誤。`cache_only=True` 時 latest 缺失／journal stamp 不匹配就回空，唔讀 journal，專供 SubagentStart 等快速接入。
- `append(observations: list[dict], *, deadline=None) -> dict`：回`{added, duplicates, recording_paused, diagnostics}`。Journal成功後先更新derivedcache；並行lock、idempotency、20MiB上限。未完整追加時唔推進source cursor。
- `read_cache(name: str, default=None)`、`write_cache(name: str, value, *, deadline=None)`、`mutate_cache(name: str, callback, *, default=None, deadline=None)`。只接受固定cache names：`cursors, throttle, reflection, installation, receipts, diagnostics`。Mutate喺單一lock內read-modify-write；callback唔做nested store calls。
- `write_note(kind: str, payload: dict) -> dict`：只准`feedback/hypotheses`，建立有UUID `id`嘅Markdown卡；拒絕任意path。以固定第一個JSON code block保存machine fields，文字仍係低信任資料。
- `read_notes(kind: str, *, limit: int = 100) -> list[dict]`，`read_note(kind: str, note_id: str) -> dict`：有界、canonical UUID、唔跟外來symlink。
- `record_diagnostic(code: str) -> None`：有界白名單code，唔存rawexception／輸入。

State目錄0700、files0600，existing foreign/symlink objects唔自動修復／overwrite。Atomic cache＋fcntl process lock；lock timeout符合caller deadline。Hypothesis文字唔自動變指令。

### `sources.py`

- `collect_local(codex_home: Path, *, files: list[Path] | None = None, cursors: dict | None = None, deadline=None) -> dict`：回`{observations, coverage, diagnostics, cursors}`，唔自行寫state。
- `collect_live(*, codex_command: list[str] | None = None, timeout_seconds: float = 30) -> dict`：回同一report shape（cursors可空）。Command override只係internal test seam，唔由hook／untrustedpayload提供。

Local只解析`event_msg`＋`payload.type=token_count` metrics，唔向agent呈現／保存其他內容。Hook bounds：256 discovery entries、8files、每檔128KiB、總1MiB、單行64KiB，任何丟棄都報coverage partial。安全處理partial line／rotation／cursor／out-of-order；檔案locator輸出用hash，唔存對話標題。Log來源version未知就null，唔冒認係目前installedCLI寫出。

Live只支援已核實0.153.3；`codex --version`亦包含總deadline。`initialize` response→`initialized`無params→`account/rateLimits/read`無params；sparse最多一次boundedrefetch，其他唔retry。最多1MiB／256lines，drainstderr但唔保存。總30秒含cleanup，EOF→terminate→kill→reap；唔留daemon。Official child處理existinglogin，adapter唔讀auth/cookies/envvalues，唔發起login。0.153.3冇explicitallowflag就null；read-onlyRPC唔保證child零bookkeeping。

### `research.py`

- `record_feedback(store: Store, *, observation_id: str, outcome: str, note: str = "") -> dict`。
- `record_hypothesis(store: Store, *, claim: str, evidence: list[str] | None = None, counterevidence: list[str] | None = None, next_test: str = "", metric: str | None = None, lower: float | None = None, upper: float | None = None, supersedes: str | None = None) -> dict`。
- `reflect(store: Store, *, hypothesis_id: str | None = None, deadline=None) -> dict`：回且cache固定schema嘅derived report。Evidence IDs指normalizedobservation；唔自然語言判真。可用metrics `used_percent/window_minutes/delta_used_percent`，window用primary作明示v1語义；未知／confounded不足就說不足。

Note長度／數量有界，IDs嚴格validation；model/generated prose唔自動讀回hookcontext。Reflect只按source/bucket/window/reset可比組，唔硬歸因job／account。Re-read唔灌大n。Report包含`sample_count, comparable_count, confounded, findings, hypotheses, next_steps`；findings/next_steps用固定code/數字/ID，rawnote不入。

### Hooks／installation

Hook script係main-only薄wrapper，可importpackage，用CLI event argument；stdin最多64KiB，只取hook_event_name/session_id/必要cwd hash。SessionStart提供brief、SubagentStartcache-only、UserPromptSubmit全機60秒採樣且每session狀態改變先提示、Stop本地observe/reflect唔rewake。內部1.5s／harness2s，永不block、永不livecall。全局採樣claim用atomicmutate，唔持lock做IO。

`install.py` public API：

- `install(*, home: Path, repo_root: Path, dry_run: bool = True) -> dict`
- `uninstall(*, home: Path, repo_root: Path) -> dict`
- `doctor(*, home: Path, repo_root: Path) -> dict`

Hosttargets：新`~/.local/bin/kingdom-quota`、兩個`~/.claude/skills/quota-lantern`與`~/.codex/skills/quota-lantern`link、Codex`~/.codex/AGENTS.md`managedpointer、Claude`~/.claude/settings.json`四個commandhooks。永不改原statusLine/model/permissions/login/config.toml，唔備份secret-bearing全份settings；manifest只記ownedhash。Collision／user後續修改唔覆蓋，重裝冪等；部分失敗rollback只rollback自己仍然吻合嘅新增項。真實host安裝只由主agent執行。

Installer 用獨立、5秒 bounded `.installation.lock`，再攞 `lantern_common` 嘅 `~/.local/state/lantern-common/.host-settings.lock`，同 Reality 序列化 host mutation／manifest commit／rollback；鎖內重新讀取 settings／AGENTS，唔持 Store 採樣鎖做 host IO。兩燈各有 manifest，唔互刪 owned additions。Manifest commit 後出錯會先核對實際 published 狀態，唔盲目 rollback 成無 ownership 嘅 hooks。部分卸載嘅 settings hashes 唔當完整 EVENTS 安裝；doctor／reinstall 會如實拒絕。Rollback journal 只存在 RAM，唔聲稱有 SIGKILL／斷電 transaction recovery。

## Codex 指引 discovery 證據

2026-09-07 已核實官方 [AGENTS.md 文件](https://learn.chatgpt.com/docs/agent-configuration/agents-md)：Codex home 預設 `~/.codex`，可由 `CODEX_HOME` 改寫；全域優先 `AGENTS.override.md`，否則 `AGENTS.md`，每次 run 建立一次指令鏈，之後由 project root 到cwd疊加。呢部機本次檢查冇 `CODEX_HOME` override，亦未有兩個全域AGENTS檔。呢啲只確認安裝落點，唔係證明已開啟嘅TUI sessions會熱載入。

## 使用：零填表有燈，有疑問先研究

```sh
kingdom quota brief
kingdom quota observe
kingdom quota status --json
kingdom quota reflect --json
kingdom quota doctor
```

`brief`／`status` 純讀；`observe` 先將本地 metrics 正規化落 evidence journal。手動收數失敗會回 nonzero 同安全 diagnostic，普通輸出會明講顯示緊 cached evidence；正常冇新樣本、有限 discovery 嘅 partial coverage 唔會一律當失敗。預設 discovery 只涵蓋最近7個 UTC calendar days，最多8檔，會明示 `source_recent_scope`／partial；唔聲稱掃齊所有 clients。明示舊檔用 `observe --file /absolute/path/to/.codex/sessions/YYYY/MM/DD/rollout-....jsonl`，仍受安全同 IO 界線限制。

可由 `status --json` 嘅 `sources[].id` 取得 observation ID，按需要記低一次 feedback：

```sh
kingdom quota feedback --observation OBSERVATION_ID --outcome completed --note '今次係正常工作嘅觀察；唔做因果歸因'
kingdom quota hypothesis add --claim '今次 primary 係7日視窗' --evidence OBSERVATION_ID --metric window_minutes --lower 10080 --upper 10080 --next-test '等下一次自然讀數再比較'
kingdom quota reflect
kingdom quota research watch --samples 3 --interval 60 --duration 180
```

以上 `OBSERVATION_ID` 係 placeholder，唔係可用 ID。有結構比較只針對明示 metric／primary window，唔將自然語言 claim 判成真理。Watch 喺前景跑；Ctrl-C 即停，唔開模型或背景 loop。唔需要為咗有樣本而特登花 quota。

Claude lifecycle hooks 讀本地 metrics 同 derived report；Codex 用 global pointer＋skill，屬 soft awareness。`/hooks` 可以檢視 Claude 接線；重新開正常 session 再確認收到 brief，已開 Codex run 唔假設會熱載入。

撤回用 `kingdom quota uninstall`：只移除仍吻合嘅 owned additions，保留研究 evidence。Wrapper 亦會一併移除；之後若要讀返研究，可直接用 `python3 -I -S -B /absolute/repo/tools/kingdom-quota.py status --json`。

## 驗證

Offline pytest、fake HOME、fake Codex transport、secret canaries、並行writers、throttle/deadline、兩project lifecycleE2E。真實log smoke唔發起LLM；live smoke僅顯式讀quota。Hook pipe tests唔等於真agent已載入；reload／下一次正常session再核實receipt。
