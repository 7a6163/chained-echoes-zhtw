"""
Apply curated Mainland-China -> Taiwan term corrections on top of the Fanhuaji
output, for either the database (.bytes) or the dialogue bundle (.bundle).

Rules live in corrections.json:
  - "substring": ordered [from, to] pairs applied to every zh value (put more
    specific phrases first, e.g. 主界面 before 界面).
  - "exact": [from, to] pairs that replace a value only when it equals `from`
    wholesale — used for UI labels whose words mean something else in prose
    (e.g. 保存 = "save" button, but 保存 = "preserve" in dialogue).

Idempotent: running it again on already-corrected files is a no-op. The source
file is never modified in place; output goes to <name>.corrected.<ext> (or -o).

Run:  python apply_corrections.py /path/to/bansheegz_database.bytes
      python apply_corrections.py /path/to/packedassets_assets_all.bundle
"""
import argparse
import json
import os
import struct
import sys

import bgdb
from convert_zh import build_blob


def load_rules(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    substr = [tuple(p) for p in d.get("substring", [])]
    exact = {p[0]: p[1] for p in d.get("exact", [])}
    return substr, exact


def make_fixer(substr, exact):
    def fix(t):
        if t in exact:
            t = exact[t]
        for a, b in substr:
            t = t.replace(a, b)
        return t
    return fix


def correct_db(src, out, fix):
    data = open(src, "rb").read()
    _, _, fields, cur = bgdb.parse(data)
    assert cur == len(data), "source parse mismatch"
    edits = []
    n = 0
    for f in fields:
        if f.name != "zh" or not f.val_len:
            continue
        vs = bgdb.decode_field_values(data, f.val_off, f.val_len)
        if vs is None:
            continue
        changed = False
        pairs = []
        for idx, vb in vs:
            if vb:
                t = vb.decode("utf-8")
                nt = fix(t)
                if nt != t:
                    vb = nt.encode("utf-8")
                    changed = True
                    n += 1
            pairs.append((idx, vb))
        if changed:
            blob = build_blob(pairs)
            edits.append((f.val_off - 4, f.val_off + f.val_len,
                          struct.pack("<i", len(blob)) + blob))
    edits.sort()
    buf = bytearray()
    pos = 0
    for s, e, r in edits:
        assert s >= pos, "overlapping edits"
        buf += data[pos:s] + r
        pos = e
    buf += data[pos:]
    buf = bytes(buf)
    _, _, _, c2 = bgdb.parse(buf)
    assert c2 == len(buf), "output parse mismatch"
    open(out, "wb").write(buf)
    return n


def correct_bundle(src, out, fix):
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
        for conv in tree.get("conversations", []):
            for de in conv.get("dialogueEntries", []):
                for fld in de.get("fields", []):
                    if fld.get("title") == "zh":
                        v = fld.get("value", "")
                        if isinstance(v, str) and v:
                            nv = fix(v)
                            if nv != v:
                                fld["value"] = nv
                                dirty = True
                                n += 1
        if dirty:
            o.save_typetree(tree)
    with open(out, "wb") as f:
        f.write(env.file.save(packer="lz4"))
    UnityPy.load(out)  # reload sanity check
    return n


def default_output(path):
    root, ext = os.path.splitext(path)
    return root + ".corrected" + ext


def main(src, out, rules_path):
    substr, exact = load_rules(rules_path)
    fix = make_fixer(substr, exact)
    if src.endswith(".bytes"):
        n = correct_db(src, out, fix)
        kind = "DB"
    elif src.endswith(".bundle"):
        n = correct_bundle(src, out, fix)
        kind = "bundle"
    else:
        raise SystemExit("input must be a .bytes (database) or .bundle file")
    print(f"[{kind}] applied {n} corrections -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Apply Taiwan term corrections to a Chained Echoes DB or dialogue bundle")
    ap.add_argument("input", help="path to a .bytes database or .bundle file")
    ap.add_argument("-o", "--output", help="output path (default: <input>.corrected.<ext>)")
    ap.add_argument("-c", "--corrections", default=os.path.join(os.path.dirname(__file__), "corrections.json"),
                    help="corrections JSON (default: corrections.json next to this script)")
    a = ap.parse_args()
    sys.exit(main(a.input, a.output or default_output(a.input), a.corrections))
