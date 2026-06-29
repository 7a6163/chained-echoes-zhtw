"""
Import edited Traditional-Chinese back into the live game files, the inverse of
export_subtitles.py. Routes each CSV row to its exact value via the `key` column:

  db:<table>:<entityIndex>              -> DB zh field
  dlg:<objPathId>:<convID>:<entryID>    -> dialogue bundle zh field

Only rows whose chinese_zhtw differs from the current live value are written, so
re-running is a no-op. The `english`/`context` columns are ignored — only `key`
and `chinese_zhtw` matter.

By default outputs go beside the live files as <name>.imported.<ext> (install by
copying over the originals). Use --in-place to overwrite the live files directly
(the pristine *.simplified.bak backups are untouched either way).

Run:  python import_subtitles.py edited.csv               # writes .imported files
      python import_subtitles.py edited.csv --in-place     # overwrites live
"""
import argparse
import csv
import os
import struct

import bgdb
from convert_zh import build_blob
from export_subtitles import DEFAULT_DB, DEFAULT_BUNDLE


def load_edits(csv_path):
    """key -> new chinese, for db:* and dlg:* keys."""
    db, dlg = {}, {}
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            key, zh = row.get("key", ""), row.get("chinese_zhtw", "")
            if key.startswith("db:"):
                _, meta, idx = key.split(":", 2)
                db[(meta, int(idx))] = zh
            elif key.startswith("dlg:"):
                _, pid, cid, eid = key.split(":", 3)
                dlg[(pid, cid, eid)] = zh
    return db, dlg


def import_db(src, out, edits):
    data = open(src, "rb").read()
    _, _, fields, cur = bgdb.parse(data)
    assert cur == len(data), "DB parse mismatch"
    patches, n = [], 0
    for f in fields:
        if f.name != "zh" or not f.val_len:
            continue
        vs = bgdb.decode_field_values(data, f.val_off, f.val_len)
        if vs is None:
            continue
        changed, pairs = False, []
        for idx, vb in vs:
            nz = edits.get((f.meta, idx))
            if nz is not None:
                cur_txt = vb.decode("utf-8") if vb else ""
                if nz != cur_txt:
                    vb = nz.encode("utf-8")
                    changed, n = True, n + 1
            pairs.append((idx, vb))
        if changed:
            blob = build_blob(pairs)
            patches.append((f.val_off - 4, f.val_off + f.val_len,
                            struct.pack("<i", len(blob)) + blob))
    patches.sort()
    buf, pos = bytearray(), 0
    for s, e, r in patches:
        assert s >= pos, "overlapping edits"
        buf += data[pos:s] + r
        pos = e
    buf += data[pos:]
    buf = bytes(buf)
    _, _, _, c2 = bgdb.parse(buf)
    assert c2 == len(buf), "output parse mismatch"
    open(out, "wb").write(buf)
    return n


def import_bundle(src, out, edits):
    import UnityPy
    env = UnityPy.load(src)
    n = 0
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            tree = o.read_typetree()
        except Exception:
            continue
        if not isinstance(tree, dict) or "conversations" not in tree:
            continue
        dirty = False
        pid = str(o.path_id)
        for conv in tree["conversations"]:
            cid = str(conv.get("id", ""))
            for de in conv.get("dialogueEntries", []):
                nz = edits.get((pid, cid, str(de.get("id", ""))))
                if nz is None:
                    continue
                for fld in de.get("fields", []):
                    if fld.get("title") == "zh" and fld.get("value", "") != nz:
                        fld["value"] = nz
                        dirty, n = True, n + 1
        if dirty:
            o.save_typetree(tree)
    with open(out, "wb") as fh:
        fh.write(env.file.save(packer="lz4"))
    UnityPy.load(out)  # reload sanity check
    return n


def _out(path, in_place):
    if in_place:
        return path
    root, ext = os.path.splitext(path)
    return root + ".imported" + ext


def main(csv_path, db_path, bundle_path, in_place):
    db_edits, dlg_edits = load_edits(csv_path)
    db_out, bundle_out = _out(db_path, in_place), _out(bundle_path, in_place)
    dn = import_db(db_path, db_out, db_edits)
    bn = import_bundle(bundle_path, bundle_out, dlg_edits)
    print(f"[DB] wrote {dn} changed values -> {db_out}")
    print(f"[bundle] wrote {bn} changed values -> {bundle_out}")
    if not in_place:
        print("install with:\n  cp", repr(db_out), repr(db_path),
              "\n  cp", repr(bundle_out), repr(bundle_path))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Write edited zh-TW from a CSV back into the live game files")
    ap.add_argument("csv", help="edited CSV (from export_subtitles.py)")
    ap.add_argument("--db", default=DEFAULT_DB, help="live bansheegz_database.bytes")
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE, help="live packedassets_assets_all.bundle")
    ap.add_argument("--in-place", action="store_true", help="overwrite the live files directly")
    a = ap.parse_args()
    main(a.csv, a.db, a.bundle, a.in_place)
