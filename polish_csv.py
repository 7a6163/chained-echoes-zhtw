"""
Polish the Traditional-Chinese column of an exported subtitles CSV with an
OpenAI-compatible chat API, then feed the result to import_subtitles.py.

Pipeline:  export_subtitles.py  ->  polish_csv.py  ->  import_subtitles.py

Design notes (the failure modes this guards against):
  - English-grounded: the model is told to keep meaning faithful to `english`
    and never add/drop content -- that is where the bad translations came from.
  - Glossary: the Mainland->Taiwan term pairs from corrections.json are fed in
    so polishing stays consistent with the curated fixes.
  - Markup-safe: [panel=N], #tokens, line breaks must survive untouched. Every
    polished line is validated; if markup changed, the ORIGINAL is kept.
  - Batched JSON with explicit ids; missing ids are retried one-by-one.
  - Resumable: results cached in <out>.cache.jsonl keyed by a hash of the input
    + model + prompt version, so re-runs only do new/changed rows.

Auth:  reads the API key from $OPENAI_API_KEY (override with --api-key-env).

Run:   export OPENAI_API_KEY=sk-...
       python polish_csv.py ~/Desktop/chained_echoes_subtitles.csv -o polished.csv
       python polish_csv.py --self-check        # validate the markup checker
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROMPT_VERSION = "v3"

# Setting + confirmed proper-noun renderings, so polishing stays in-world and the
# names stay consistent with the curated fixes. Only facts/names verified from the
# game data are listed here — do not add speculative lore.
GAME_CONTEXT = (
    "【遊戲背景】《Chained Echoes》是一款向 16-bit 致敬的奇幻 JRPG。故事發生在瓦蘭蒂斯"
    "（Valandis）大陸，數個國家（如塔恩、伊斯卡尼亞）長年交戰；世界有飛空艇、名為"
    "「天穹戰鎧（Sky Armor）」的人形機甲，魔法源自水晶與魔典。語氣是奇幻冒險，"
    "對白會因角色與情境而有正式、粗俗或詼諧之別，請依 context 與英文語氣拿捏。\n"
    "【固定譯名】（English → 台灣譯名，務必一致）：\n"
    "  人物 Glenn 格倫、Lenne 蕾妮、Robb 羅布、Sienna 琪安娜、Kylian 凱廉、Nalkilber 納基伯\n"
    "  地名 Valandis 瓦蘭蒂斯、Taryn 塔恩、Escanya 伊斯卡尼亞、Tormund 托蒙德、"
    "He'Kandria 何·坎德利亞、Golgota 果各塔、Kindreld 親族（修道院）、Conothan 科諾森\n"
    "  名詞 Grand Grimoire 至高魔典、Sky Armor 天穹戰鎧（亦作天鎧）、airship 飛空艇、"
    "ether 以太、His Holiness 教宗聖下\n"
)
DEFAULT_URL = "https://1min.2ac.io/v1/chat/completions"
DEFAULT_MODEL = "gpt-5.4-mini"

# tokens that must be preserved exactly (multiset) between input and output
_BRACKET = re.compile(r"\[[^\]]*\]")   # [panel=1], [...]
_HASHTAG = re.compile(r"#\w+")          # #select, button glyphs
_CJK = re.compile(r"[㐀-鿿]")  # any Han char -> there is Chinese to polish


def has_chinese(s):
    """Rows with no Han char (PLACEHOLDER, numbers, None(), [] ...) need no polish."""
    return bool(_CJK.search(s))


def markup_tokens(s):
    return sorted(_BRACKET.findall(s)) + sorted(_HASHTAG.findall(s))


def same_markup(a, b):
    """True if a and b carry identical control markup and line structure."""
    return markup_tokens(a) == markup_tokens(b) and a.count("\n") == b.count("\n")


def load_glossary(corrections_path):
    with open(corrections_path, encoding="utf-8") as f:
        d = json.load(f)
    # only the term-level substring pairs make a useful glossary
    return [(a, b) for a, b in d.get("substring", []) if len(a) <= 8]


def system_prompt(glossary):
    terms = "\n".join(f"  避免「{a}」，用「{b}」" for a, b in glossary)
    return (
        "你是專業的繁體中文（台灣）電玩在地化譯者，負責 JRPG《Chained Echoes》。\n"
        + GAME_CONTEXT +
        "這是『翻譯校潤』工作：en 是英文原文（語意的唯一基準），zh 是由簡體機器轉換而來、"
        "尚待校潤的譯文，context 是出處。\n"
        "目標語言是『台灣所使用的正體中文（zh-TW）』，務必讀起來像台灣母語玩家所寫，"
        "不是大陸用語、也不是簡轉繁的腔調。\n"
        "請輸出校潤後的台灣正體中文，使其忠於英文語意、道地、通順、符合 RPG 對白語氣。\n"
        "規則：\n"
        "1. 以 en 為語意基準，不可增刪劇情內容、不可自行加戲或省略。\n"
        "2. 一律使用台灣慣用詞彙與語法，改掉大陸用語、機翻腔與生硬直譯；成語適度，切勿濫用。\n"
        "3. 用字採台灣正體（例：軟體/網路/品質/介面/影片/滑鼠，而非 軟件/網絡/質量/界面/視頻/鼠標）。\n"
        "4. 結構標記必須原封不動、位置不變：[panel=數字]、#開頭的符號、換行數量。其餘標點可改成台灣自然用法（對白引號用「」『』）。\n"
        "5. 譯名一致，遵守下列詞彙表。\n"
        "6. 若原譯已是道地台灣中文，原樣輸出即可。\n"
        "7. 只輸出校潤後的譯文本身，不要任何說明。\n"
        "詞彙表（避免左邊、使用右邊）：\n" + terms
    )


def extract_json(text):
    """Tolerantly pull the first JSON object out of a model reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("no JSON object in reply")
    return json.loads(text[i:j + 1])


def call_api(url, key, model, temperature, sys_msg, items):
    user = (
        '請潤稿以下各句，回傳 JSON 物件，格式為 '
        '{"results":[{"id":整數,"polished":"潤稿後譯文"}]}，每個 id 都要對應一筆。\n'
        + json.dumps(items, ensure_ascii=False)
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": sys_msg},
                     {"role": "user", "content": user}],
        "temperature": temperature,
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 # default urllib UA is often WAF-blocked (403); send a normal one
                 "User-Agent": "Mozilla/5.0 (polish_csv)"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.load(r)
    content = resp["choices"][0]["message"]["content"]
    out = extract_json(content)
    return {int(x["id"]): x["polished"] for x in out.get("results", []) if "id" in x}


def polish_batch(url, key, model, temperature, sys_msg, batch, retries=3):
    """batch: list of (id, context, en, zh) -> {id: polished}. Retries on error."""
    items = [{"id": i, "context": c, "en": e, "zh": z} for i, c, e, z in batch]
    for attempt in range(retries):
        try:
            got = call_api(url, key, model, temperature, sys_msg, items)
            if all(i in got for i, *_ in batch):
                return got
            # some ids missing: keep what we have, retry the rest below
            if attempt == retries - 1:
                return got
            items = [it for it in items if it["id"] not in got]
        except urllib.error.HTTPError as ex:
            detail = ex.read().decode("utf-8", "replace")[:500]
            if attempt == retries - 1:
                sys.stderr.write(f"  batch failed: HTTP {ex.code} {detail}\n")
                return {}
        except Exception as ex:  # noqa: BLE001 - network/parse, retry then give up
            if attempt == retries - 1:
                sys.stderr.write(f"  batch failed: {ex}\n")
                return {}
        time.sleep(1.5 * (attempt + 1))
    return {}


def row_hash(model, context, en, zh):
    h = hashlib.sha1()
    h.update("\0".join([PROMPT_VERSION, model, context, en, zh]).encode("utf-8"))
    return h.hexdigest()


def load_cache(path):
    cache = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    cache[r["key"]] = r
                except Exception:
                    pass
    return cache


def main(in_csv, out_csv, url, model, temperature, workers, batch_size,
         corrections, api_key_env, limit):
    key = os.environ.get(api_key_env)
    if not key:
        raise SystemExit(f"set ${api_key_env} to your API key")
    sys_msg = system_prompt(load_glossary(corrections))

    with open(in_csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    cache_path = out_csv + ".cache.jsonl"
    cache = load_cache(cache_path)
    lock = threading.Lock()

    # which rows still need work (new or input changed); skip rows with no Chinese
    todo = []
    skipped = 0
    for idx, r in enumerate(rows):
        if not has_chinese(r["chinese_zhtw"]):
            skipped += 1
            continue
        h = row_hash(model, r["context"], r["english"], r["chinese_zhtw"])
        c = cache.get(r["key"])
        if not c or c.get("h") != h:
            todo.append((idx, h))
    if limit:
        todo = todo[:limit]
    print(f"{len(rows)} rows, {len(todo)} to polish "
          f"({len(rows) - len(todo) - skipped} cached, {skipped} no-Chinese skipped), "
          f"{workers} workers")

    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    kept = [0]  # markup-mismatch -> kept original
    done = [0]

    def work(batch):
        payload = [(idx, rows[idx]["context"], rows[idx]["english"],
                    rows[idx]["chinese_zhtw"]) for idx, _ in batch]
        got = polish_batch(url, key, model, temperature, sys_msg, payload)
        out = []
        for idx, h in batch:
            orig = rows[idx]["chinese_zhtw"]
            p = got.get(idx, orig)
            if not same_markup(orig, p):  # protect markup -> revert
                p = orig
                with lock:
                    kept[0] += 1
            out.append({"key": rows[idx]["key"], "h": h, "polished": p})
        with lock:
            with open(cache_path, "a", encoding="utf-8") as cf:
                for o in out:
                    cf.write(json.dumps(o, ensure_ascii=False) + "\n")
                    cache[o["key"]] = o
            done[0] += len(out)
            print(f"\r  {done[0]}/{len(todo)} polished", end="", flush=True)

    if batches:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, batches))
        print()
    if kept[0]:
        print(f"  kept {kept[0]} originals (markup would have changed)")

    # assemble output: polished where available & current, else original
    n_changed = 0
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        for r in rows:
            h = row_hash(model, r["context"], r["english"], r["chinese_zhtw"])
            c = cache.get(r["key"])
            if c and c.get("h") == h:
                if c["polished"] != r["chinese_zhtw"]:
                    n_changed += 1
                r = {**r, "chinese_zhtw": c["polished"]}
            w.writerow(r)
    print(f"wrote {out_csv} ({n_changed} lines changed). next: import_subtitles.py")


def self_check():
    assert same_markup("[panel=1]你好#select", "[panel=1]您好#select")
    assert not same_markup("[panel=1]hi", "hi")           # dropped tag
    assert not same_markup("a\nb", "ab")                  # lost newline
    assert not same_markup("#select x", "#other x")       # changed token
    assert same_markup("純文字。", "純文字！")              # no markup -> ok
    print("self-check OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="LLM-polish the zh-TW column of an exported subtitles CSV")
    ap.add_argument("input", nargs="?", help="CSV from export_subtitles.py")
    ap.add_argument("-o", "--output", help="output CSV (default: <input>.polished.csv)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--corrections", default=os.path.join(os.path.dirname(__file__), "corrections.json"))
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    ap.add_argument("--limit", type=int, help="only polish the first N pending rows (testing)")
    ap.add_argument("--self-check", action="store_true", help="run the markup-validator self-test and exit")
    a = ap.parse_args()
    if a.self_check:
        self_check()
        sys.exit(0)
    if not a.input:
        ap.error("input CSV required")
    out = a.output or os.path.splitext(a.input)[0] + ".polished.csv"
    main(a.input, out, a.url, a.model, a.temperature, a.workers, a.batch_size,
         a.corrections, a.api_key_env, a.limit)
