"""
Export every translated line (English + current Traditional-Chinese) from the
*live, already-corrected* game files to a CSV, for a second-pass review/edit in
another tool.

Two sources, same row shape:
  - DB  (bansheegz_database.bytes): tr_* tables, `en` paired with `zh` by entity index.
  - Dialogue (packedassets_assets_all.bundle): each dialogueEntry's `en`
    (falling back to "Dialogue Text") paired with its `zh`.

The first column is a stable `key` that round-trips back to the exact value, so a
later importer can map edited Chinese straight onto the live files:
  db:<table>:<entityIndex>              e.g. db:tr_MENU:42
  dlg:<objPathId>:<convID>:<entryID>    e.g. dlg:-8123..:17:3

The dialogue bundle holds 9 separate dialogue MonoBehaviours that reuse the same
(convID, entryID) numbering for different content, so the object path_id is part
of the key — (convID, entryID) alone is NOT unique across the bundle.

Run:  python export_subtitles.py            # uses live install defaults
      python export_subtitles.py -o out.csv
"""
import argparse
import csv
import os

import bgdb

HOME = os.path.expanduser("~")
GAME = os.path.join(
    HOME, "Library/Application Support/Steam/steamapps/common/Chained Echoes",
    "ChainedEchoes.app/Contents/Resources/Data/StreamingAssets",
)
DEFAULT_DB = os.path.join(GAME, "bansheegz_database.bytes")
DEFAULT_BUNDLE = os.path.join(GAME, "aa/StandaloneOSX/packedassets_assets_all.bundle")
DEFAULT_OUT = os.path.join(HOME, "Desktop", "chained_echoes_subtitles.csv")

COLUMNS = ["key", "source", "context", "english", "chinese_zhtw"]


def db_rows(db_path):
    data = open(db_path, "rb").read()
    _, _, fields, cur = bgdb.parse(data)
    assert cur == len(data), "DB parse mismatch"
    # group field-value dicts {entityIndex: text} per table, per field name
    by_table = {}  # meta -> {name: {idx: text}}
    for f in fields:
        if f.name not in ("en", "zh") or not f.val_len:
            continue
        vs = bgdb.decode_field_values(data, f.val_off, f.val_len)
        if not vs:
            continue
        d = {idx: vb.decode("utf-8") for idx, vb in vs if vb}
        by_table.setdefault(f.meta, {})[f.name] = d
    for meta, names in by_table.items():
        zh = names.get("zh")
        if not zh:
            continue
        en = names.get("en", {})
        for idx in sorted(zh):
            yield [f"db:{meta}:{idx}", "DB", meta, en.get(idx, ""), zh[idx]]


def _conv_title(conv):
    for fld in conv.get("fields", []):
        if fld.get("title") == "Title":
            return fld.get("value", "") or ""
    return ""


def bundle_rows(bundle_path):
    import UnityPy
    env = UnityPy.load(bundle_path)
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            tree = o.read_typetree()
        except Exception:
            continue
        if not isinstance(tree, dict) or "conversations" not in tree:
            continue
        for conv in tree["conversations"]:
            ctx = _conv_title(conv)
            cid = conv.get("id", "")
            for de in conv.get("dialogueEntries", []):
                f = {x.get("title"): (x.get("value") or "") for x in de.get("fields", [])}
                zh = f.get("zh", "")
                if not zh:
                    continue
                english = f.get("en") or f.get("Dialogue Text", "")
                yield [f"dlg:{o.path_id}:{cid}:{de.get('id', '')}", "Dialogue", ctx, english, zh]


def main(db_path, bundle_path, out_path):
    rows = list(db_rows(db_path)) + list(bundle_rows(bundle_path))
    assert rows, "no translated rows found — wrong files?"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)
    db_n = sum(1 for r in rows if r[1] == "DB")
    print(f"wrote {len(rows)} rows ({db_n} DB, {len(rows) - db_n} dialogue) -> {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Export English + current zh-TW to CSV for second-pass editing")
    ap.add_argument("--db", default=DEFAULT_DB, help="bansheegz_database.bytes (live, corrected)")
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE, help="packedassets_assets_all.bundle (live, corrected)")
    ap.add_argument("-o", "--output", default=DEFAULT_OUT, help="output CSV path")
    a = ap.parse_args()
    main(a.db, a.bundle, a.output)
