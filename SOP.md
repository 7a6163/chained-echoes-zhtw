# Chained Echoes 繁體中文（台灣）在地化 SOP

遊戲官方只出簡體中文。本流程把遊戲自身的資料檔做「簡轉繁 → 名詞/用語校正 →（選用）AI 潤稿」，
產出台灣正體中文，並寫回遊戲檔。二進位 schema 保持位元組一致，所以選單不會壞。

> ⚠️ 法務：**遊戲二進位檔（`*.bytes`、`*.bundle`、`*.bak`）與抽出的文字 CSV 一律不進 git**。
> repo 只放工具程式碼與 `corrections.json`。

---

## 0. 環境

```bash
cd ~/Developer/chained_echoes
source .venv/bin/activate          # 需 UnityPy（見 requirements.txt）
```

兩個 live 遊戲檔（`$GAME` 為 StreamingAssets 根目錄）：

```bash
GAME="$HOME/Library/Application Support/Steam/steamapps/common/Chained Echoes/ChainedEchoes.app/Contents/Resources/Data/StreamingAssets"
DB="$GAME/bansheegz_database.bytes"                              # 選單/道具/敵人描述
BUNDLE="$GAME/aa/StandaloneOSX/packedassets_assets_all.bundle"   # 劇情對話
```

---

## 工具一覽

| 檔案 | 作用 |
|---|---|
| `bgdb.py` | BGDatabase V7 二進位解析（被其他腳本 import） |
| `convert_zh.py` | DB 的 `zh` 欄 簡→繁（繁化姬 API）→ `<name>.zhtw.bytes` |
| `convert_bundle.py` | bundle 對話 `zh` 欄 簡→繁 → `<name>.zhtw.bundle` |
| `corrections.json` | 大陸用語→台灣用語、專有名詞統一的規則（**git 版控的權威層**） |
| `apply_corrections.py` | 把 `corrections.json` 套到 DB 或 bundle（冪等） |
| `export_subtitles.py` | 從 live 檔匯出 `key,source,context,english,chinese_zhtw` CSV |
| `import_subtitles.py` | 把編修過的 CSV 依 `key` 寫回 live 檔 |
| `polish_csv.py` | 用 OpenAI 相容 API 對 CSV 做台灣中文潤稿（英文為基準、含詞彙表、保護標記、可續跑） |
| `diff_csv.py` | 比對兩份 CSV，只列有改動的句子 |

---

## SOP A — 作者改版後重做中文（核心流程，不含 AI）

> 改版會把 `$DB` / `$BUNDLE` 換成新的**簡體**檔（Steam 更新覆蓋）。
> 舊的已轉檔不能沿用（schema 可能變動），必須**從新檔重轉**。

1. **備份新版原始簡體檔**（還原與重跑用）
   ```bash
   cp "$DB" "$DB.simplified.bak"
   cp "$BUNDLE" "$BUNDLE.simplified.bak"
   ```

2. **簡→繁轉換**（繁化姬，需網路）
   ```bash
   python convert_zh.py     "$DB"     -o /tmp/db.zhtw.bytes
   python convert_bundle.py "$BUNDLE" -o /tmp/bundle.zhtw.bundle
   ```

3. **套用名詞/用語校正**（並驗冪等：第二次應為 0）
   ```bash
   python apply_corrections.py /tmp/db.zhtw.bytes      -o /tmp/db.final.bytes
   python apply_corrections.py /tmp/db.final.bytes     -o /tmp/idem.bytes        # 應印 applied 0
   python apply_corrections.py /tmp/bundle.zhtw.bundle -o /tmp/bundle.final.bundle
   python apply_corrections.py /tmp/bundle.final.bundle -o /tmp/idem.bundle       # 應印 applied 0
   ```

4. **安裝到 live**
   ```bash
   cp /tmp/db.final.bytes "$DB"
   cp /tmp/bundle.final.bundle "$BUNDLE"
   ```

5. **進遊戲驗證**（先關掉再重開；bundle 在啟動時載入）。

到這裡已是「簡轉繁 + 名詞統一」的可玩版本。要更道地再做 SOP B。

---

## SOP B — （選用）AI 潤稿一輪

潤稿在「export → 編輯 → import」之間進行；做完務必**重套 corrections** 讓名詞統一蓋回最上層。

```bash
export OPENAI_API_KEY=你的金鑰          # polish_csv.py 預設 Poe 端點，見檔頭

# 1. 從 live 匯出現況
python export_subtitles.py              # -> ~/Desktop/chained_echoes_subtitles.csv

# 2. 先小量試跑，確認模型/端點正常
python polish_csv.py ~/Desktop/chained_echoes_subtitles.csv --limit 5 -o /tmp/test.csv

# 3. 全量潤稿（可中斷續跑；快取在 <out>.cache.jsonl）
python polish_csv.py ~/Desktop/chained_echoes_subtitles.csv -o ~/Desktop/polished.csv

# 4. 審稿：只看改了哪些
python diff_csv.py ~/Desktop/chained_echoes_subtitles.csv ~/Desktop/polished.csv
#    -> ~/Desktop/polished.diff.csv（old_zh vs new_zh，用 Numbers/Excel 開）

# 5. 匯入潤稿為底層（先產 .imported 檔，不動 live）
python import_subtitles.py ~/Desktop/polished.csv
#    產生 $DB→bansheegz_database.imported.bytes、$BUNDLE→...imported.bundle

# 6. 把 corrections 疊回最上層（潤稿可能洗掉名詞統一）
python apply_corrections.py "$GAME/bansheegz_database.imported.bytes"                        -o /tmp/db.final.bytes
python apply_corrections.py "$GAME/aa/StandaloneOSX/packedassets_assets_all.imported.bundle" -o /tmp/bundle.final.bundle

# 7. 安裝 + 清暫存 + 重新匯出（CSV 反映最終結果）
cp /tmp/db.final.bytes "$DB" && cp /tmp/bundle.final.bundle "$BUNDLE"
rm -f "$GAME/bansheegz_database.imported.bytes" "$GAME/aa/StandaloneOSX/packedassets_assets_all.imported.bundle"
python export_subtitles.py
```

**潤稿要點（已寫進 `polish_csv.py` 的 prompt）：** 以英文為語意基準、台灣用語、降低成語、保護
`[panel=N]`/`#token`/換行（標記若被改動自動退回原文）、固定譯名與 `corrections.json` 詞彙表。
調整 prompt 後請更新 `PROMPT_VERSION`，快取會自動失效重跑。

---

## 逐句修正（日常 QA）

玩到怪句子時，最小修法是加進 `corrections.json` 再套用：

- `substring`：詞/名詞層級，套到每個 zh 值（要**冪等**：目標不可包含來源）。
- `exact`：整句替換（標點、寬度需逐字相符）。

```bash
# 編輯 corrections.json 後
python apply_corrections.py "$DB"     -o /tmp/x.bytes  && cp /tmp/x.bytes "$DB"
python apply_corrections.py "$BUNDLE" -o /tmp/x.bundle && cp /tmp/x.bundle "$BUNDLE"
git add corrections.json && git commit -m "fix: ..."
```

---

## 還原 / 救援

```bash
cp "$DB.simplified.bak" "$DB"            # 還原成官方簡體
cp "$BUNDLE.simplified.bak" "$BUNDLE"
```

Steam「驗證遊戲檔案完整性」也會還原成官方簡體 → 重跑 SOP A（＋B）即可。

---

## Source of Truth / 備份

| 資產 | 內容 | 版控 | 備註 |
|---|---|---|---|
| `corrections.json` | 名詞/用語權威層，冪等可重套 | ✅ git | 改版照樣適用 |
| 工具 `*.py` | 流程程式碼 | ✅ git | |
| `~/Desktop/chained_echoes_polished_source.csv` | AI 潤稿底稿（12k 句） | ❌ | **務必另外備份**：不在 git，丟了得重潤 |
| live `*.bytes` / `*.bundle` | 最終成品 | ❌ | 可由上述重建 |
| `*.simplified.bak` | 官方簡體原檔 | ❌ | 還原基準 |

> 名詞統一（如 Lisvan→利斯凡、Victor→維克托、airship→飛空艇、His Holiness→教宗聖下）
> 透過 `corrections.json` 的 substring 規則維持，跨版本自動沿用。
