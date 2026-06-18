"""
Convert the NEW-version Chained Echoes story dialogue (inside the asset bundle)
from Simplified Chinese -> Traditional.

The bundle packedassets_assets_all.bundle embeds Pixel Crushers Dialogue System
databases as MonoBehaviours. Each line lives at
    conversations[].dialogueEntries[].fields[]  -> {title, value}
with a parallel field per language. The Chinese field has title == "zh"
(siblings "jp"/"en"/... are left untouched). We convert every zh value via the
繁化姬 (zhconvert.org) Taiwan converter, write it back into the typetree, and
repack the bundle.

Output is written next to the source as <name>.zhtw.bundle (or --output); the
source bundle is NOT touched here. Reloads the result and re-scans to confirm
the zh values are now Traditional.

Run:  python convert_bundle.py /path/to/packedassets_assets_all.bundle [-o OUTPUT]
"""
import argparse
import re
import sys

import UnityPy

from convert_zh import build_mapping

CJK = re.compile("[一-鿿]")


def iter_zh_fields(tree):
    """Yield each {title,value} field dict whose title == 'zh'."""
    for conv in tree.get("conversations", []):
        for de in conv.get("dialogueEntries", []):
            for fld in de.get("fields", []):
                if fld.get("title") == "zh":
                    yield fld


def main(src, out):
    print("loading bundle...", flush=True)
    env = UnityPy.load(src)

    # pass 1: collect unique Simplified zh strings
    unique = set()
    dlg_objs = 0
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            tree = o.read_typetree()
        except Exception:
            continue
        if not isinstance(tree, dict) or "conversations" not in tree:
            continue
        dlg_objs += 1
        for fld in iter_zh_fields(tree):
            v = fld.get("value", "")
            if isinstance(v, str) and v and CJK.search(v):
                unique.add(v)
    print(f"dialogue databases: {dlg_objs}", flush=True)
    print(f"unique zh dialogue strings: {len(unique)}", flush=True)
    if not unique:
        raise RuntimeError("no zh dialogue found - aborting")

    mapping = build_mapping(sorted(unique))
    assert len(mapping) == len(unique), "mapping incomplete"

    # pass 2: write converted values back into the typetree and save
    changed = 0
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
        for fld in iter_zh_fields(tree):
            v = fld.get("value", "")
            if isinstance(v, str) and v in mapping and mapping[v] != v:
                fld["value"] = mapping[v]
                dirty = True
                changed += 1
        if dirty:
            o.save_typetree(tree)
    print(f"dialogue values changed: {changed}", flush=True)

    with open(out, "wb") as f:
        f.write(env.file.save(packer="lz4"))
    print(f"wrote {out}", flush=True)

    # validate: reload and confirm zh is now Traditional (no simplified markers)
    env2 = UnityPy.load(out)
    simp_markers = set("这们么说过还时国见东车马问开关写边观觉买卖")
    leftover = 0
    checked = 0
    samples = []
    for o in env2.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            tree = o.read_typetree()
        except Exception:
            continue
        if not isinstance(tree, dict) or "conversations" not in tree:
            continue
        for fld in iter_zh_fields(tree):
            v = fld.get("value", "")
            if isinstance(v, str) and v and CJK.search(v):
                checked += 1
                if any(c in simp_markers for c in v):
                    leftover += 1
                elif len(samples) < 5:
                    samples.append(v[:40].replace("\n", " "))
    print(f"reload ok: checked={checked} still-simplified-markers={leftover}")
    for s in samples:
        print("  sample:", s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Convert Dialogue System bundle zh fields Simplified -> Traditional (Taiwan)")
    ap.add_argument("input", help="path to packedassets_assets_all.bundle")
    ap.add_argument("-o", "--output", help="output path (default: <input>.zhtw.bundle)")
    a = ap.parse_args()
    out_path = a.output or (a.input.rsplit(".", 1)[0] + ".zhtw.bundle")
    sys.exit(main(a.input, out_path))
