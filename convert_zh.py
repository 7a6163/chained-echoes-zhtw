"""
Convert the NEW-version Chained Echoes BGDatabase Simplified Chinese -> Traditional.

For every field named `zh` (typecode 34) inside the tr_* metas, decode its
value-blob, convert each Simplified string to Traditional via the 繁化姬
(zhconvert.org) Taiwan converter, then rebuild the value-blob (offset table +
concatenated UTF-8 bytes) and slice-replace it in the file. The binary schema
is left byte-for-byte identical everywhere else, so the game menu still loads.

Output is written next to the source as <name>.zhtw.bytes (or --output); the
source file is NOT touched here. Re-parses the result and asserts cursor==len.

Run:  python convert_zh.py /path/to/bansheegz_database.bytes [-o OUTPUT]
"""
import argparse
import json
import struct
import sys
import time
import urllib.request

import bgdb

API = "https://api.zhconvert.org/convert"
UA = "chained-echoes-localizer/1.0"
SEP_TOKEN = "@@@FHJ_SEP@@@"
SEP = "\n" + SEP_TOKEN + "\n"
PAD = "X"                 # dummy items flanking the batch, immune to global trim
BATCH_MAX_ITEMS = 400
BATCH_MAX_BYTES = 40_000
REQUEST_PAUSE_S = 0.4     # be polite to the public API


def convert_batch(items):
    """Convert a list of strings via the Taiwan converter, preserving boundaries.

    Pads both ends with a dummy item so every real item is flanked by the
    newlines we add, making each immune to any whole-text trimming. Returns a
    same-length list of converted strings, or None on a boundary mismatch."""
    payload = SEP.join([PAD] + items + [PAD])
    body = json.dumps({"text": payload, "converter": "Taiwan"}).encode("utf-8")
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA})
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if resp.get("code") != 0:
        raise RuntimeError(f"API error: {resp.get('code')} {resp.get('msg')}")
    parts = resp["data"]["text"].split(SEP_TOKEN)
    real = parts[1:-1]  # drop the padding pieces
    if len(real) != len(items):
        return None  # caller falls back to per-item conversion
    out = []
    for p in real:
        if p.startswith("\n"):
            p = p[1:]
        if p.endswith("\n"):
            p = p[:-1]
        out.append(p)
    return out


def build_mapping(unique_texts):
    """Build {simplified -> traditional} for all unique strings, batched."""
    mapping = {}
    batch, batch_bytes = [], 0

    def flush(b):
        if not b:
            return
        conv = convert_batch(b)
        if conv is None:
            # boundary mismatch: convert one at a time (still padded)
            print(f"  ! batch mismatch ({len(b)}), per-item fallback", flush=True)
            conv = []
            for s in b:
                c = convert_batch([s])
                conv.append(c[0] if c else s)
                time.sleep(REQUEST_PAUSE_S)
        for src, dst in zip(b, conv):
            mapping[src] = dst
        time.sleep(REQUEST_PAUSE_S)

    for t in unique_texts:
        assert SEP_TOKEN not in t, "sentinel collision in source text"
        tb = len(t.encode("utf-8"))
        if batch and (len(batch) >= BATCH_MAX_ITEMS or batch_bytes + tb > BATCH_MAX_BYTES):
            flush(batch)
            print(f"  converted {len(mapping)}/{len(unique_texts)}", flush=True)
            batch, batch_bytes = [], 0
        batch.append(t)
        batch_bytes += tb
    flush(batch)
    print(f"  converted {len(mapping)}/{len(unique_texts)}", flush=True)
    return mapping


def build_blob(pairs):
    """pairs = list of (entityIndex:int, value_bytes:bytes) -> blob bytes
    layout = [int num][num x (int idx, int cumulativeEndOffset)][concat bytes]"""
    num = len(pairs)
    table = bytearray()
    body = bytearray()
    cum = 0
    for idx, vb in pairs:
        cum += len(vb)
        table += struct.pack("<ii", idx, cum)
        body += vb
    return struct.pack("<i", num) + bytes(table) + bytes(body)


def main(src, out_path):
    data = open(src, "rb").read()
    version, addons, fields, cursor = bgdb.parse(data)
    assert cursor == len(data), "source parse mismatch"
    print(f"source ok: len={len(data)} version={version}")

    zh_fields = [f for f in fields if f.name == "zh"]
    print(f"zh fields: {len(zh_fields)}")

    # collect unique source strings
    decoded = {}  # id(field) -> list of (idx, text_or_None)
    unique = set()
    for f in zh_fields:
        vs = bgdb.decode_field_values(data, f.val_off, f.val_len)
        if vs is None:
            raise RuntimeError(f"zh field in {f.meta} did not decode as strings")
        rows = []
        for idx, vb in vs:
            if vb:
                t = vb.decode("utf-8")
                unique.add(t)
                rows.append((idx, t))
            else:
                rows.append((idx, None))  # empty stays empty
        decoded[id(f)] = rows
    print(f"unique strings to convert: {len(unique)}")

    mapping = build_mapping(sorted(unique))
    assert len(mapping) == len(unique), "mapping incomplete"

    # build edits: replace each field's [len][blob] region
    edits = []  # (start, end, replacement)
    changed_fields = 0
    for f in zh_fields:
        rows = decoded[id(f)]
        if f.val_len == 0:
            continue  # genuinely empty blob (no num int) -> leave untouched
        new_pairs = []
        field_changed = False
        for idx, t in rows:
            if t is None:
                new_pairs.append((idx, b""))
            else:
                nb = mapping[t].encode("utf-8")
                ob = t.encode("utf-8")
                if nb != ob:
                    field_changed = True
                new_pairs.append((idx, nb))
        if not field_changed:
            continue
        blob = build_blob(new_pairs)
        start = f.val_off - 4
        end = f.val_off + f.val_len
        replacement = struct.pack("<i", len(blob)) + blob
        edits.append((start, end, replacement))
        changed_fields += 1
    print(f"fields changed: {changed_fields}")

    # reassemble file from sorted, non-overlapping edits
    edits.sort()
    out = bytearray()
    pos = 0
    for start, end, repl in edits:
        assert start >= pos, "overlapping edits"
        out += data[pos:start]
        out += repl
        pos = end
    out += data[pos:]
    out = bytes(out)

    # validate
    v2, a2, f2, c2 = bgdb.parse(out)
    assert c2 == len(out), f"OUTPUT PARSE MISMATCH cursor={c2} len={len(out)}"
    # spot-check: zh fields decode and are now Traditional
    z2 = [f for f in f2 if f.name == "zh"]
    sample = bgdb.decode_field_values(out, z2[0].val_off, z2[0].val_len)
    print(f"output ok: len={len(out)} cursor==len (delta {len(out)-len(data):+d} bytes)")
    for idx, vb in sample[:3]:
        if vb:
            print("  sample:", vb.decode("utf-8")[:40])

    open(out_path, "wb").write(out)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Convert BGDatabase zh fields Simplified -> Traditional (Taiwan)")
    ap.add_argument("input", help="path to bansheegz_database.bytes")
    ap.add_argument("-o", "--output", help="output path (default: <input>.zhtw.bytes)")
    a = ap.parse_args()
    out_path = a.output or (a.input.rsplit(".", 1)[0] + ".zhtw.bytes")
    sys.exit(main(a.input, out_path))
