# Chained Echoes — 簡體→繁體 中文化工具 / Simplified→Traditional Localizer

A small toolkit that converts the **Simplified Chinese** shipped with the current
version of [Chained Echoes](https://store.steampowered.com/app/1229240/) into
**Traditional Chinese (Taiwan)**, in place, **without altering the data schema**
— so the in-game menu keeps working.

> ⚠️ **No game assets are included in this repository.** These are tools only.
> You must own the game and run them against your own installed files.

> 🎮 **Tested against game version 1.4 / 適用遊戲版本 1.4.** Other versions may
> ship a different data schema; check before applying.

> 📋 **完整流程(改版重做、AI 潤稿、逐句修正、還原)見 [SOP.md](SOP.md)。**

## Why

The new official build already ships Chinese, but only **Simplified**. Dropping an
old fan Traditional patch on top breaks the menu (different DB schema). This toolkit
instead rewrites only the Chinese text fields of the current game's own data files,
leaving the binary structure byte-for-byte identical everywhere else.

Two pieces of text are handled:

| Tool | Target file | Content |
|------|-------------|---------|
| `convert_zh.py` (+ `bgdb.py`) | `bansheegz_database.bytes` | UI, items, skills, character names |
| `convert_bundle.py` | `packedassets_assets_all.bundle` | story / cutscene dialogue |

## How it works

- **`bgdb.py`** — a faithful structural parser for the **BansheeGz BGDatabase binary
  format V7**. It walks the whole file and locates every field's value-blob
  (`[int num][num × (int entityIndex, int endOffset)][concatenated UTF-8 bytes]`),
  so individual string columns can be rewritten by pure slice-replacement
  (the format is self-delimiting — no absolute offsets). Validation gate: after a
  full parse, the cursor must equal the file length.
- **`convert_zh.py`** — decodes every field named `zh`, converts each value to
  Traditional, rebuilds the value-blob (new offset table + bytes), slice-replaces it,
  and re-parses to assert `cursor == len`.
- **`convert_bundle.py`** — the asset bundle embeds Pixel Crushers *Dialogue System*
  databases as MonoBehaviours. Dialogue lives at
  `conversations[].dialogueEntries[].fields[]` (`{title, value}`); the Chinese field
  has `title == "zh"`. It converts those values via the typetree and repacks the
  bundle with [UnityPy](https://github.com/K0lb3/UnityPy).

Markup such as `[panel=N]`, `\.`, `#va`, `<color=#...>` is ASCII and survives
conversion untouched.

## Requirements

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Locating the game files

In Steam, right-click **Chained Echoes → Manage → Browse local files**, then find
the `StreamingAssets` folder. The two files you need:

**Database** — `bansheegz_database.bytes`

- macOS: `Chained Echoes.app/Contents/Resources/Data/StreamingAssets/bansheegz_database.bytes`
- Windows: `Chained Echoes_Data/StreamingAssets/bansheegz_database.bytes`

**Dialogue bundle** — `packedassets_assets_all.bundle` (inside the platform `aa` subfolder)

- macOS: `…/StreamingAssets/aa/StandaloneOSX/packedassets_assets_all.bundle`
- Windows: `…/StreamingAssets/aa/StandaloneWindows64/packedassets_assets_all.bundle`

## Usage

Always back up your files first.

```bash
# 1) Database (UI / items / names)
python convert_zh.py "/path/to/StreamingAssets/bansheegz_database.bytes"
#    -> writes bansheegz_database.zhtw.bytes next to it

# 2) Dialogue bundle (macOS path shown; use StandaloneWindows64 on Windows)
python convert_bundle.py "/path/to/StreamingAssets/aa/StandaloneOSX/packedassets_assets_all.bundle"
#    -> writes packedassets_assets_all.zhtw.bundle next to it
```

Then back up the originals and replace them with the `.zhtw.*` outputs. On Steam:

- macOS: `…/Chained Echoes/ChainedEchoes.app/Contents/Resources/Data/StreamingAssets/`
- Windows: `…/Chained Echoes/Chained Echoes_Data/StreamingAssets/`

> The dialogue bundle is a Unity **Addressables** asset; if your build enforces a
> bundle CRC check it may reject a repacked file. Keep the backups so you can revert.

## Taiwan term corrections

The Fanhuaji output is already good, but a few Mainland-China terms slip through
(e.g. `設置`→`設定`, `窗口`→`視窗`, `後台運行`→`背景執行`) and some battle-state
names read better as adjectives (`增重`→`沉重`, `受潮`→`潮濕`). `apply_corrections.py`
applies a curated, editable glossary (`corrections.json`) on top of the converted
files:

```bash
python apply_corrections.py /path/to/bansheegz_database.bytes      # -> *.corrected.bytes
python apply_corrections.py /path/to/packedassets_assets_all.bundle # -> *.corrected.bundle
```

`corrections.json` has two rule kinds:

- **`substring`** — ordered `[from, to]` pairs applied to every line (put specific
  phrases first, e.g. `主界面` before `界面`).
- **`exact`** — replaces a value only when it equals `from` wholesale, for UI
  labels whose words mean something else in prose (e.g. `保存` = "Save" button,
  but `保存` = "preserve" in dialogue, which must be left alone).

It is idempotent — re-running adds nothing new — so it doubles as a record of every
term decision. Add a pair, re-run, done.

## Quality note

The Taiwan converter makes context-aware choices (e.g. `内存`→`記憶體`, `土豆`→`馬鈴薯`)
and correctly preserves proper nouns. A naive character-level converter would damage
names (`維克托`→`維克託`) or use archaic variants, so this toolkit does **not** apply a
second char-level pass. A few words may still need manual touch-ups (the classic
`餘`/`余` ambiguity); fix those per-line as you find them.

## Attribution & licensing of the conversion service

本程式使用了 **繁化姬 (Fanhuaji)** 的 API 服務進行簡繁轉換。
**商業使用請參閱繁化姬授權條款。**

This tool calls the **[繁化姬 / Fanhuaji](https://zhconvert.org/)** API
(`https://api.zhconvert.org/convert`, `converter=Taiwan`) for the actual
Simplified→Traditional conversion. For **commercial use, consult the Fanhuaji
terms of service.** Please use the public API responsibly (the scripts batch
requests and pause between calls).

## License

The code in this repository is released under the [MIT License](LICENSE).
This license covers **only** the tool code — it does **not** grant any rights to
Chained Echoes game assets or to the Fanhuaji service. *Chained Echoes* is © Matthias
Linda / Deck13. This is an unofficial, non-commercial interoperability tool.
