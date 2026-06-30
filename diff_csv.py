"""
Show only the lines a polish pass actually changed, side by side, so you can
review polished.csv against the current export instead of eyeballing 18k rows.

Matches the two CSVs by `key` and emits rows where chinese_zhtw differs:
  key, context, english, old_zh, new_zh

Run:  python diff_csv.py current.csv polished.csv          # -> polished.diff.csv
      python diff_csv.py current.csv polished.csv -o d.csv
"""
import argparse
import csv
import os
import sys


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["key"]: r for r in csv.DictReader(f)}


def diffs(base, polished):
    for key, p in polished.items():
        b = base.get(key)
        if b is None or b["chinese_zhtw"] == p["chinese_zhtw"]:
            continue
        yield [key, p.get("context", ""), p.get("english", ""),
               b["chinese_zhtw"], p["chinese_zhtw"]]


def main(base_csv, polished_csv, out_csv):
    rows = list(diffs(load(base_csv), load(polished_csv)))
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "context", "english", "old_zh", "new_zh"])
        w.writerows(rows)
    print(f"{len(rows)} changed lines -> {out_csv}")


def self_check():
    base = {"k1": {"chinese_zhtw": "甲"}, "k2": {"chinese_zhtw": "乙"}}
    pol = {"k1": {"chinese_zhtw": "甲"}, "k2": {"chinese_zhtw": "丙"},
           "k3": {"chinese_zhtw": "丁"}}  # k3 absent in base -> skipped
    d = list(diffs(base, pol))
    assert [r[0] for r in d] == ["k2"], d
    print("self-check OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="List lines changed between a current and a polished subtitles CSV")
    ap.add_argument("base", nargs="?", help="current/export CSV")
    ap.add_argument("polished", nargs="?", help="polished CSV")
    ap.add_argument("-o", "--output", help="diff CSV (default: <polished>.diff.csv)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        self_check()
        sys.exit(0)
    if not a.base or not a.polished:
        ap.error("need base and polished CSV paths")
    out = a.output or os.path.splitext(a.polished)[0] + ".diff.csv"
    main(a.base, a.polished, out)
