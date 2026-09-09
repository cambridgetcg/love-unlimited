# LANTERN SUITE — 四燈共用 contract

2026-09-08。狀態：本機＋可攜交付完成；2093 passed／1 skipped，Coedit 真 Read／Edit context 已驗。Friction 真收據模式／CLI 已驗，自然 Bash context 因此 session 嘅 HOME cwd 邊界未驗；其他 runtime／跨平台 activation 分開列明。唔包含 remote／SSH／commit／push／deploy。完整證據及產物見 `LANTERN-SUITE-PLAN.md`。

## 產品同界線

四個產品保持獨立 schema、state、installer。Quota／Reality 既有使用者設定保留；新增 Friction／Coedit 同 `lanterns` 入口。固定 allowlist，唔由資料載入任意程式。所有 CLI 路徑由目標 HOME 生成，canonical 係 `~/.local/bin/kingdom-{quota,reality,friction,coedit,lanterns}`。

Advisory；冇 LLM polling／daemon／自動重驗。原命令只執行一次。資料只記 digests／固定 enums／時間，唔記 raw command/output/path/session/credentials；installation manifest 嘅固定 source locator 係明示 metadata。Installed、capability、activation 分開，pipe test 唔當真 harness 證據。

## 共用 owned installer API

新增 `tools/lantern_common/product.py`：

```python
@dataclass(frozen=True)
class ProductSpec:
    product_id: str
    package: str
    command: str
    skill: str
    events: dict[str, str | None]
    guide: str

product_install(spec, *, home, repo_root, dry_run=True, installation_nonce=None) -> dict
product_uninstall(spec, *, home, repo_root, expected_nonce=None) -> dict
product_doctor(spec, *, home, repo_root) -> dict
```

- Source CLI：`tools/{command}.py`；skill：`tools/{package}/skill`；有 events 時 hook：`tools/hooks/{product_id}.py`。冇 events 唔讀／改 settings。
- Product state：`~/.local/state/{product_id}/installation.json`，同產品其餘 cache 分檔；strict private IO／共用 host lock。Generic helper 必須沿用現有 compare-and-replace／foreign-safe rollback／strict manifest durability reconciliation。
- 新 manifest schema：`kingdom.{product_id}.installation/2`，欄位 `schema/repo_root/installation_nonce/artifacts`；nonce 係 canonical UUID。Artifact shape／owned merge 跟 Reality installer，冇 events 時唔包含 settings。
- 新三個 specs：`friction-lantern/friction_lantern/kingdom-friction/friction-lantern`；`coedit-radar/coedit_radar/kingdom-coedit/coedit-radar`；`lanterns/lantern_suite/kingdom-lanterns/lanterns`（最後一個 events={}）。
- `guide` 係可信 source 固定 body；helper 用 product_id 專屬 begin/end markers 包住，保留外來 AGENTS 文本。
- Install／doctor 結果額外有 `installation_nonce: str | None`；install 額外 `created: bool`。只有今次確定新建且 manifest commit 可核實先 created=true；idempotent／legacy／uncertain 唔宣稱新建。
- 薄產品 `install/uninstall/doctor` 用同名 keyword 參數；Quota／Reality public API 加相同 optional nonce／expected_nonce，原有呼叫繼續用到。
- 兩燈舊 v1 manifest 可讀／可獨立卸載；健康 adoption 唔改寫。Fresh install 用新 installation v2，receipt domain v1 不變。Expected nonce 喺產品鎖內比對，唔以讀後再卸載造成 ABA。
- Product-local `installation-nonces.json` 用 `kingdom.lantern.installation-nonces/1`、exact `schema/nonces` 欄位，最多 4096 個 canonical UUID／256KiB、0600 single-link。Fresh host 改動前先 durable 保留 nonce；已用或已保留 nonce 不可復活，包括 N→M→N。卸載／失敗唔刪歷史；滿額或 reservation 不確定就拒絕，唔自動清空。既有 v2 撤回前補保護其 nonce，健康 adoption 零改寫。Reservation 成功後其他步驟失敗可以 burn nonce，結果另報 `nonce_reservation="reserved"`；呢個 metadata 唔係設定 backup，亦唔入 bundle。

新增 `tools/lantern_common/paths.py`：`is_secret_path(path: str | os.PathLike) -> bool`，純 lexical、零 IO。重用／擴充 Reality 已有 known-secret policy；涵蓋 auth、cookies、keychain、credential stores、env 及已知含 secrets 嘅 client settings，唔讀檔案去猜。Coedit 可直接用；Reality 呢輪唔由其他 owner 改 snapshot.py，政策接線由整合 owner 核對。

## Friction public contract

`tools/friction_lantern/core.py`：`derive_pattern(state, scope_id, threshold=3) -> dict`；`render_brief(status, *, max_bytes=512) -> str`。

`tools/friction_lantern/api.py`：`status(*, home, cwd) -> dict`、`brief(*, home, cwd, max_bytes=512) -> str`，cached 純讀，唔建 state。CLI／suite 共同使用；無 pattern嘅 hook 靜默，CLI status 解釋 unknown／不足。

嚴格最近 recorded run 模式；唔跳過 interleave 湊數。相同 check/kind/exit code/完整 snapshot，全部目前 projection match，按 sequence 且完成先於下一 run開始；三次先提示。Sequence 容許 highwater gaps，連續係已記錄 run 嘅相鄰次序，唔要求數字逐一加一。不存在證據證明中途冇閱讀、冇外部工作、同一原因／執行者，唔推論空轉。自家 delivery cache，絕不寫 Reality store。

Delivery 喺 publish 後遇 fsync failure，可喺原 deadline 內用 bounded private exact-readback 核實今次 bytes，確認後回傳提示。唔承諾 exactly-once 或 harness 已收取；publication 同 readback 連續故障時保持 unknown／靜默，亦唔保證同一 fingerprint 跨 hook 重試。唔為 advisory 加無限重送或擴大時限。

[Claude Code hook 文件](https://code.claude.com/docs/en/hooks) 明示 matching hooks 並行，唔可依賴多個 handler 執行次序。文件未提供可當閱讀證書嘅完整 Read response contract。Main 真 smoke 已核實 exact text/file metadata shape 同 terminal-LF 末空行格式，亦已收到 Coedit additionalContext；呢啲仍唔證明 Read 返回 bytes。

## Coedit public contract

`tools/coedit_radar/api.py`：

```python
status(*, home, cwd=None, session=None) -> dict
brief(*, home, cwd=None, session=None, max_bytes=512) -> str
observe(*, home, path, session, deadline=None) -> dict
compare(*, home, path, session, deadline=None) -> dict
```

`status/brief` 純讀；無 session唔借他人baseline，回 unavailable/unknown。CLI observe/compare 要明示 `--session`（HMAC後先落cache）同 `--file`；觀察係 explicit_observation，唔當 Read receipt。

Claude PreRead/PostRead 用已核實 tool-call token 配對，再喺 PreEdit/Write 比較；成功或失敗完成清baseline。1MiB／256項／256KiB／TTL30分鐘；partial、implicit truncation、binary/PDF/notebook、links、secret path、race、缺配對資訊保持unknown。只知 bounded hook observation時點，唔證明 Read返回bytes一致，亦唔保證preflight後無race。不出綠燈、不block。

Cache 用 `kingdom.coedit-radar.cache/2`；entries 有 HMAC boot identity／elapsed clock，top-level 固定 overflow stamp。滿額 mutation 無位就保存 global uncertainty，唔讓 refresh 遺忘已知 pending；completion 唔提早清 overflow。TTL 用包含休眠嘅 boot-scoped elapsed time，wall clock 回調唔復活 baseline；舊 shape／缺可靠時鐘保持 unknown，純讀唔寫。Global guard 會保守影響無關 paths；完全無法落盤／取得 lock 時，唔保證其他 process 記得未落盤事件。TTL 到期唔證明工具已完成。

## Suite ownership 同入口

`tools/lantern_suite/install.py` 提供 `install(*, home, repo_root, dry_run=True)`、`uninstall(*, home, repo_root)`、`doctor(*, home, repo_root)`。

固定 products：quota、reality、friction、coedit；suite 自己用 product_id=lanterns 嘅 entry spec 接 wrapper／skills／AGENTS，冇 hook。Membership 用獨立 `~/.local/state/lantern-suite/membership.json`，唔混入 entry installation manifest。

持自己 operation lock，但唔外持 host lock 再 call產品。Durable intent 記產品 requested nonce／preexisting，逐件結果 created/preexisting/failed/uncertain。唔吞併 artifact ownership、唔跨產品自動rollback；預設卸載只撤 confirmed-created且nonce匹配。Crash gap／uncertain唔猜，保留並報告。Legacy健康兩燈只adopt。

Adapters 只核對 `~/.local/state/lanterns/installation.json` 嘅有效 v2 entry 同 `~/.local/bin/kingdom-lanterns` owned wrapper（hash、private owner／mode／nofollow），先用固定短模板提供入口；唔把入口存在當四產品全部健康。入口指引叫按需要看 suite status／doctor，唔自行執行。唔從 manifest載入任意 guide文字或程式。

## Portable bundle

Bundle 只收明示逐檔 allowlist：上述 runtime packages、CLI／hook／skill／固定指南／pure adapter helper／bootstrap。唔帶整repo、HOME、state、auth、boot files、foreign工作、個人絕對路徑。

Content digest係版本；HEAD只base_revision，唔冒稱working-tree bytes已commit。固定排序／mtime／uid/gid／mode；build前後來源hash變就拒絕。Trusted bootstrap先核對expected digest及bounded manifest／所有 entries，拒duplicate/extra/traversal/absolute/links/special/oversize，再安置private永久digest root，唔先exec包內code／extractall。Fresh host全modules自足；舊source install拒silent relocation。

## Reality archive contract

Reality owner提供 `Store.capacity()`、`Store.rotate(*, archive=True, deadline=None)`；CLI `capacity [--json]`、`rotate --archive [--json]`。同一鎖先durable archive/readback再reset，pending拒絕；保留key、next_sequence/generation highwater防CAS ABA，receipt v1/sticky stale不變。32 archives／64MiB上限，唔自動evict；fsync／reset不確定就明講，唔盲目restore。

**歷史反證不受舊 CAS token 一概排除。** Highwater 只防遲到 caller 升級 match／倒退最新 observation；完整 snapshot 如果確實喺新 run 開始之後觀察到差異，仍要 stale 該 run，包括 token 係上一個 generation／rotation取得。早於新 run 嘅差異則由 observed_at／started_at 邊界排除。唔可用 `run.generation <= expected_generation` 丟棄適用反證。Main `tests/test_lantern_history_boundaries.py` 四個case鎖定呢條語義。

## 實作 ownership

- Common／nonce owner：`lantern_common/product.py`、`paths.py`、Quota／Reality `install.py`、對應新增tests；唔改Reality core/store/CLI。
- Archive owner：Reality core/store/archive/CLI/skill／原Reality docs及新archive tests；唔改installer/common。
- Friction／Coedit owner：各自package/CLI/hook/tests/docs；只用common public API。
- Suite/bundle owner：`lantern_suite/` 除 `advisory.py/advisory.mjs`、suite CLI、bootstrap、suite/bundle tests、portable指南。呢份DESIGN／PLAN由main維護。
- Runtime owner：`lantern_suite/advisory.py/advisory.mjs`、youi/sovereign/kingdom-agent prompt seams、source CLAUDE.md、adapter tests／integration指南；唔改suite installer/bundle。

所有測試 fake HOME／自建tmp repos；寫真HOME／安裝／最終bundle生成只由main，喺全部驗收後執行。原 885 passed／1 skipped 只係上輪，今輪結果另記。
