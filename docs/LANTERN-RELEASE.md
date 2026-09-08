# 四燈發布入口 🏮

四燈係本地 advisory 工具，唔係網站或 fleet installer。分發用此 repo 嘅 **private GitHub Releases**；需要 repo 存取權，唔改 visibility、唔自動 SSH／安裝／重啟 agents。

## 取得指定版本

首個產品版本：`lantern-suite-v0.1.0`。發布是否已完成，以 GitHub Release 實際狀態為準。

```sh
gh release view lantern-suite-v0.1.0 --repo cambridgetcg/love-unlimited

gh release download lantern-suite-v0.1.0 \
  --repo cambridgetcg/love-unlimited \
  --dir ./lantern-suite-v0.1.0
```

每個 Release 有六個 assets：

- `kingdom-lanterns.tar`：60 個固定 allowlisted source 檔＋manifest，唔包含整個 repo。
- `lantern-bootstrap.py`：獨立 trusted bootstrap，唔先執行包內 code。
- `SHA256SUMS`：套件同 bootstrap 嘅 SHA-256。
- `README.md`：該版本完整 expected SHA、安裝／撤回命令同限制。
- `VERIFICATION.json`：source commit、content digest、實際測試／實體包 smoke 摘要。
- `LICENSE`：沿用 repo 嘅 Apache-2.0，隨套件一齊保留同轉交；冇另行改授權。

從可信渠道核對 bootstrap 同 expected archive SHA，先做 README 嘅 `verify`；接收方明示批准先做 `install`。Checksum 唔係 signature，同一 Release 內嘅 checksum 唔能獨立證明來源可信。

Python 3.11+；macOS 已驗，Linux 未實跑，Windows 不支援。Doctor 只驗靜態接線；runtime activation、自然 hook 送達同各 host 嘅狀態分開驗證。已有 source install 唔 silent relocate。

## 發布者流程

1. 基於最新 remote `main` 開獨立 branch，只帶四燈 source、tests、文件同窄 adapter seams。唔 stage foreign settings／hooks／memory、device-local state，亦唔帶無關本地 commits。
2. 解上游衝突時保留 security／runtime 更新。先跑完整四燈 tests、受影響上游 runtime 嘅離線 tests、syntax 同 freeze hash checks；唔 boot 真模型驗 prompt fixture。
3. Commit 後，由乾淨 release checkout 執行 `tools/kingdom-lanterns.py bundle build --output ABS --base-revision COMMIT`。版本依 content digest；`base_revision` 記實際 release commit。Metadata 改變會令 archive SHA 改變，唔沿用舊包 SHA。
4. 用獨立 bootstrap verify；新建 fake HOME 驗永久安裝、五入口 doctor、Reality check／archive、owned uninstall。唔用真 HOME 作發布 smoke。
5. 只作 fast-forward push，唔 force push。建立產品專屬 tag，以 draft Release 先上傳六個 assets；remote 核對 tag／commit／assets 後先發布，再重新下載核對 digests。
6. 保留原本機工作樹同其他拍檔嘅變動。Release 發布唔代表遠端 agents 已下載、安裝或啟用；交付時分開講。

完整功能 contract：[LANTERN-SUITE-DESIGN.md](LANTERN-SUITE-DESIGN.md)。發布前歷史驗收：[LANTERN-SUITE-PLAN.md](LANTERN-SUITE-PLAN.md)。
