# REALITY LANTERN — 實況燈

2026-09-08。狀態：本輪 capacity／archive 擴充實作中，focused tests 已跑，未完成四燈整合／最終驗收／本輪真 host smoke。上一輪 2026-09-07 原兩燈曾驗收 **885 passed／1 skipped** 同本 session 當時嘅真 `Edit` additionalContext；呢啲係歷史 baseline，唔係新增 archive 證據，亦唔推斷其他 sessions／Codex lifecycle。

## 底線

本地、advisory、event-driven。只包住原本獲授權要跑嘅命令；唔自動 tests／build／deploy，唔 polling。普通 Bash success 唔係 process completion 證據。Outcome 同版本有效性分開，local 唔升格成 remote。

此文件記錄已實作嘅共同 contract、證據限制同接入方式；驗證清單見 `REALITY-LANTERN-PLAN.md`。Runtime 只依賴 Python stdlib，唔開額外 LLM polling。

## 跨模組 contract（第一版）

Python stdlib runtime。JSON-compatible dict/list；UTC ISO8601 時間；deadline 係 absolute `time.monotonic()` 秒數。永不將 raw exception、argv、檔案內容、hook input/output、credential values 落 state 或 context。

### core.py

- `SCHEMA_VERSION = "kingdom.reality-lantern/1"`
- `RealityError(code: str)`：固定安全 code。
- `utc_now() -> str`
- `keyed_digest(key: bytes, domain: str, value) -> str`：canonical JSON HMAC-SHA256，64 lowercase hex；唔用 Python hash。
- `build_status(state: dict | None, *, scope_id: str | None = None) -> dict`
- `render_brief(status: dict, *, max_bytes: int = 768) -> str`
- `status_fingerprint(status: dict) -> str`：只含 material facts。

### snapshot.py

- `capture(cwd: Path, *, key: bytes, deadline: float | None = None) -> dict`
- `scope_candidates(path: Path, *, key: bytes) -> list[str]`：有界 locator／ancestor metadata＋path hashes，遇最近 `.git` boundary 即停；唔掃目錄內容、唔 spawn Git。由近至遠供 hooks 揀已知 scope。

Snapshot 固定欄位：

```text
schema, scope_id, worktree_id, cwd_id, fingerprint, complete,
observed_at, diagnostics, coverage={paths, bytes, ignored:"excluded",
                                  environment:"unverified", remote:"unverified"}
```

`scope_id = keyed_digest(key, "path", str(canonical_worktree_root))`；非 Git 就用 canonical cwd，但 worktree_id／fingerprint null、complete false。`cwd_id` 同樣 domain `path`。worktree_id 綁 root／Git-dir identity，防另一 worktree 或原位替換 repo 借證據。只返回 hashes，唔返回 raw paths。

完整 fingerprint 覆蓋 HEAD、index/staged、tracked 同非 ignored untracked path/type/mode/content／刪除。Symlink 唔跟隨；不支援、known credential paths、partial／競態／超限係 complete false。2 秒、10k distinct paths、64MiB IO；所有 Git child 亦有 deadline／output cap／cleanup。HEAD/index 直接 nofollow 讀取；Git 只喺自建 shadow 執行 check-ignore，唔讀原 repo/global config、objects 或 credentials。忽略規則用已擷取 `.gitignore`／`info/exclude`，`core.ignorecase=false/true` 取 ignored intersection，寧願多涵蓋亦唔漏另一語義；filesystem `.GITIGNORE` alias 亦處理。Global excludes/config 未採納，只可以 over-cover；環境仍未核實。非 Git 唔掃 HOME。

scope_candidates 只查有界 locator／ancestor metadata，唔建立 scope、唔掃內容、唔跑 Git；遇到最近 `.git` boundary 即停止，未登記 nested repo/worktree 唔可以借 parent 證據。

### store.py

- `Store(*, home: Path | None = None, state_dir: Path | None = None)`：constructor 純讀。
- `get_key(*, create: bool = False, deadline=None) -> bytes | None`：32-byte device-local key，private IO；create false 唔建檔。
- `read_state() -> dict`：bounded 純讀，包含 scopes/runs/projections；缺檔回固定空 state。
- `start_run(*, kind: str, check_id: str, before: dict, deadline=None) -> dict`：回 run，含 `id`、單調 `sequence`、scope、開始 generation、before snapshot；原子 pending。
- `finish_run(run_id: str, *, outcome: str, exit_code: int | None, signal_number: int | None, after: dict, deadline=None) -> dict`：獨立 completion，唔覆寫已完成事實；回 receipt。
- `invalidate(scope_id: str, *, changed: bool, deadline=None) -> bool`：只改已存在 scope；changed true 對舊 receipts sticky stale，false 只 unknown／需要比較；每次更新 generation。
- `observe(snapshot: dict, *, expected_generation: int, deadline=None) -> dict`：CAS 更新 projection；唔將已 stale 收據復活。
- `capacity() -> dict`：純讀 state／archive metadata，唔建 key、state、目錄或鎖；`writable="unknown"`、`archive_integrity="unverified"`，數量唔冒充 durability／可寫保證。
- `rotate(*, archive=True, deadline=None) -> dict`：只接受 archive=True，pending 喺同一 Store lock 內拒絕；durable archive 先 reset，保留 key 同 generation highwater。
- `deliver(session_id: str, scope_id: str, fingerprint: str, *, event: str, interval_seconds: int = 60, deadline=None) -> bool`：private keyed session、bounded 節流；唔存 raw session。

State schema 由 Store/core 統一驗證，其他模組用 API，唔各自修改 state。CLI／hooks 唯一直接 lookup contract：`state["scopes"]` 係以 scope_id 作 key 嘅 dict，每項有 integer `generation`。冇匹配 scope 時傳 `build_status(None)`，唔從其他 worktree 借結果。

Store 同 core 共同 contract：core 根據嚴格 state shape 計 projection，其他模組唔猜。Outcome `pending/exit0/nonzero/signal/spawn_failed`；validity `match/stale/unknown`，match 永遠附 as-of。command kind 係聲明，唔自動稱 tests passed。

State 固定為 `schema/next_sequence/scopes/runs/completions/projections/deliveries`；最多 256 runs、128 scopes、2MiB，滿額唔刪 evidence，原命令仍可照跑。Delivery cache 最多256項，可淘汰最舊節流項。一般 Store lock 最多500ms；hooks 明示25ms deadline，不延長。`build_status(...)["checks"]` 按 check identity 選最新起跑 run，唔用 completion 到達次序掩蓋新 pending／失敗；`recording=capacity_available` 只證明 cached 容量未滿，唔承諾當下 IO 可寫。

較舊 snapshot 唔可倒退 latest observation／升 match，但完整嘅歷史差異仍會將該時點已存在嘅舊 run sticky stale。`observe` CAS mismatch／較舊 observation 同 `finish_run` generation mismatch 都保留呢種只降級反證；唔污染較新重驗或其他 scope。CLI 仍如實回報 CAS／時間排序失敗。

Snapshot 差異要同時滿足 `observed_at >= run.started_at` 同 `observed_at >= run.before.observed_at`，等於邊界亦適用。Current-CAS `_project` 同遲到／舊-CAS `_record_changes` 共用同一時間 gate，唔因 pending／completion 到達順序而偷加 sticky stale。例如 before=A@10、start@30，B@20 喺 pending 送到只保持 unknown，after=A@39 可成 match；B@30／35 就係真反證，之後 A 還原亦唔復活。唔適用嘅 current 差異冇資格 stale；若目前冇可用 matching observation，仍係 unknown，而唔借 CAS 升 match。舊 CAS 亦唔可覆寫最新 observation。

既有 sticky stale、`changed=True` 同 before／after intrinsic 差異全部保留；呢次唔追溯修復舊帳嘅 stale，因 receipt v1 冇足夠 provenance 分辨係真反證定舊 bug。2026-09-08 呢項 focused 驗證：新增 projection-history 29 cases＋原 Store 67＋main history-boundaries 4，共 **100 passed**；先驗到3個目標 regression 紅燈再修。原 Store synthetic timestamp fixture 改用固定 clock，所有既有 assertions 保留。全部新 tmp／fake HOME；唔係全 suite／真 host 驗收。Public API、receipt schema、容量／鎖 bounds、portable allowlist 均不變；bundle content digest 會隨 source bytes 改變。

`next_sequence` 同時做 generation allocation highwater；新帳首個 scope 保留 generation 0，之後新 scope 用 floor，caller 必須讀實際 generation。Invalidate 提升 floor；舊 v1 可以 generation 遠大於 sequence，write／rotate 取 `max(next_sequence, generations+1)`，唔改 receipt schema、唔純讀遷移。Rotate 保留 floor（非空帳最少2），唔將同 scope 重建成舊 generation；Highwater 只防舊 CAS 升 match／覆寫 observation；`_record_changes` 保留原 timestamp-only 反證規則。早於新 run 嘅 snapshot 唔污染新收據，但真正喺新 run 期間或之後捕到嘅完整差異，即使 observer token 跨 rotate 都必須 sticky stale；唔用 generation mismatch 丟棄有效反證。Generation／sequence integer 耗盡就停止記錄，runner 原命令仍只跑一次。

### archive.py／明示容量管理

Archive 只含 `schema="kingdom.reality-lantern.archive/1" / archive_id / state`；內部 state 仍係完整 typed receipt v1，唔包含 key／installation manifest／raw command/output/path/session。`archive_id = keyed_digest(key, "archive", state)`，同內容同 key 有固定 filename，重試唔另造時間戳版本。最多32個 archives／64MiB，唔自動 evict，唔自動 restore／merge。

Private `archives/` 0700、檔案0600/nofollow/single-link。新 target 用 atomic no-clobber link publication；同名已存在要先 private read 相同 bytes 同核對 HMAC，唔覆蓋外來檔。必須 file fsync、archive directory fsync、state parent fsync、readback/HMAC 都成功，先准 reset active state；**唔用 Store `_publish` 嘅 fsync-recovery 當 durable 證據**。Kernel IO 仍冇硬時限保證。

`capacity` 返回 `counts={runs,scopes,pending,state_bytes,archives,archive_bytes}`、`limits`、`next_sequence/generation_floor` 同固定 `diagnostics`；讀取錯誤時未知值係 null，唔假設0。Archive inventory 係有界 metadata，唔表示每個舊 archive 已重新驗 HMAC。

`rotate` 返回 `ok/operation/archive_id/archive_durable/reset/diagnostics`。`reset` 可為 `complete/not_reset/uncertain/not_needed`。Archive 先成功、reset 發生後 fsync／readback 出問題就 `ok=false, archive_durable=true, reset=uncertain`；只知道已發佈唔宣稱 reset durable。失敗唔盲目 restore 或刪 archive；重試相同舊 state 可安全重用已核實 archive，active state 已空就 no-op，唔重複 archive。未有 durable 證據時 `archive_durable=false`，唔代表中途一定冇留下 target。Crash 遺留 private temp／外來檔會拒絕再寫，唔自行清理。缺 key 唔換身份，包括只剩 archives 嘅情況。

`read_cache(name="installation", default=None)`／`write_cache(name,value,deadline=None)` 只准 installation manifest。Manifest atomic-write durability exception 唔由 receipt recovery 吞掉，交 installer 做 commit reconciliation。收據只存工作 path／argv／session digests；安裝 manifest 額外保存固定 source repo locator，唔保存整份 settings 或其中秘密。

### runner.py

- `run_check(argv: list[str], *, cwd: Path, kind: str = "check", store: Store) -> int`
- 只真正 spawn 原命令一次，`shell=False`，繼承 stdin/stdout/stderr，唔 capture 原 command output。
- 先 capture→pending→Popen/wait→capture→completion；store／snapshot 失敗唔阻 command，唔 retry。
- check_id keyed digest 只含原 argv 同 cwd identity；唔存原文、唔 hash env values。
- 正常退出回原 code；signal 回 `128+N`，收據另記實際 signal；spawn failure 126／127。SIGKILL wrapper 冇 completion 就保持 pending。
- Python 3.11+ `process_group=0`：owned child group 避免 terminal signal 再被 wrapper 重複轉發。Wrapper 已擁有 foreground 時先移交 child，wait 後還原；direct-PID／group SIGINT、真 PTY 單 Ctrl-C、stdin、spawn failure restoration 均已測。
- Wrapper-targeted INT／TERM／HUP 轉發 owned group；兩秒仍未退出先 KILL＋reap，唔掃／殺外來 process。只證明 direct process completion，唔追 detached descendants。
- Foreground preexec 只保證單-thread CLI；**唔代理 Ctrl-Z／完整 job control**。唔用 runner 包需要 suspend／resume 嘅互動 shell。
- `started_at`／`finished_at` 係 pending／post-capture receipt 時點，涵蓋 bookkeeping，唔係精確 child CPU/runtime。Snapshot 2s／Store每步0.5s 係 cooperative budget，kernel IO／reap 無硬時限保證。

### hooks.py／CLI

- `run_hook(event: str, payload: dict, *, home: Path | None = None, deadline=None) -> dict`。
- SessionStart/UserPromptSubmit/SubagentStart cache-only brief；SubagentStart 零 writes。
- PreToolUse Bash 先 invalidate changed false；Post file tools 依 path 對 known scope invalidate，失敗／未知 shape 保守 unknown；Post Bash 只讀 receipts，唔憑文字 mint success。
- 250ms total／25ms lock wait／harness1s；brief768 bytes，Subagent512；material change 節流60秒。context 只固定模板。
- CLI `check/observe/status/brief/capacity/rotate --archive/install/uninstall/doctor`，`--home` 只係明示 fake-HOME/test seam。cached status／brief／capacity／doctor 唔重掃 repo。

### 共用／安裝

窄 `lantern_common` 由現有 Quota private IO／canonical JSON／atomic／bounded locks／owned host transaction 抽取，保留 Quota API 同 monkeypatch seams。兩 installer 共用 host-settings lock，各自 manifest；唔共用 domain Store/schema。

Reality installer 同 Quota 一致 public API：`install(*, home, repo_root, dry_run=True)`、`uninstall(*, home, repo_root)`、`doctor(*, home, repo_root)`。新 wrapper／兩個 skill links／Codex managed block／Claude hook groups；保留所有 foreign settings／原 Quota、router、model、permissions、statusLine。Main 經 offline tests 同 dry-run 先可裝真 host。

## 使用及撤回

```sh
kingdom reality check --kind tests --cwd /absolute/repo -- python3 -B -m pytest tests -p no:cacheprovider
kingdom reality brief --cwd /absolute/repo
kingdom reality status --cwd /absolute/repo --json
kingdom reality observe --cwd /absolute/repo --json
kingdom reality capacity --json
# 只喺使用者明示要封存時；唔因為容量滿就自動做。
kingdom reality rotate --archive --json
kingdom reality doctor
kingdom reality uninstall
```

第一行只係 pytest 工程嘅例子，唔自動套用其他 repo；原命令照工程指引同授權範圍揀。`status/brief` 純讀；`observe` 比較已有收據，唔重跑 tests，亦唔補回當初缺失嘅資料。喺 HOME 用 `check --cwd repo` 唔會令 hook 從 Bash command 猜 scope；需要時明示 `brief --cwd repo`。

撤回只刪仍吻合嘅 Reality owned additions，保留 receipts、Quota 同 foreign settings；兩燈按不同次序撤回，可能保留第二燈原本見到嘅空 containers，唔猜 ownership。原 router、model、routing、permissions、登入、status line 保持原樣。

2026-09-07 真 host 安裝前後核對：全部非 hooks 設定相同、既有 hook groups 完整保留、原 Codex guide 保留；兩個 doctor 均 installed=true。Skill discovery 同本 session 真 `PostToolUse:Edit` context 已實測；doctor 嘅靜態 `activation=unverified` 唔代表可以推斷其他 sessions 已載入。其他 session 未收到時可用 `/hooks` reload／開新 session。

## 仍然未證明嘅事

exit0 唔證明 tests 真係執行、全 suite 覆蓋、artifact 存在或線上健康。前後 fingerprint 一致唔證明途中冇改過再還原。ignored dependencies、環境、外部輸入同 remote 未覆蓋。Cached match 只係明示時點，唔係連續監測承諾。Device-local hash 帳唔防同 UID 惡意偽造。
