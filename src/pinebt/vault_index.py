"""Index the Pine Script entries of the strategy vault (brainbrick-trades/The-Quant-Trading-Vault, a repackaging of
FMZ's public strategy library) into one row per file with metadata and static code features.

Usage: PYTHONPATH=src python3 -m pinebt.vault_index --vault /path/to/the-quant-trading-vault --out results/pine/index.parquet
"""
from __future__ import annotations
import argparse, hashlib, json, os, re
import pandas as pd

SECTION = re.compile(r"^> ([A-Za-z][A-Za-z ()]*)\s*$", re.M)
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
BT_BLOCK = re.compile(r"/\*backtest(.*?)\*/", re.S)


def split_sections(text: str) -> dict[str, str]:
    out, pos, name = {}, 0, None
    for m in SECTION.finditer(text):
        if name is not None:
            out[name] = text[pos:m.start()].strip()
        name, pos = m.group(1).strip(), m.end()
    if name is not None:
        out[name] = text[pos:].strip()
    return out


def parse_backtest_header(src: str) -> dict:
    m = BT_BLOCK.search(src)
    if not m:
        return {}
    d = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    ex = d.get("exchanges", "")
    try:
        exl = json.loads(ex) if ex else []
    except Exception:
        exl = []
    first = exl[0] if exl else {}
    return {"bt_start": d.get("start"), "bt_end": d.get("end"), "bt_period": d.get("period"),
            "bt_base": d.get("basePeriod"), "bt_eid": first.get("eid"), "bt_currency": first.get("currency"),
            "bt_n_exchanges": len(exl), "bt_raw": m.group(1).strip()[:400]}


def strip_comments(src: str) -> str:
    src = BT_BLOCK.sub("", src)
    out = []
    for line in src.splitlines():
        # remove // comments that are not inside strings (good enough for hashing / feature flags)
        q, buf, i = None, [], 0
        while i < len(line):
            ch = line[i]
            if q:
                buf.append(ch)
                if ch == "\\" and i + 1 < len(line):
                    buf.append(line[i + 1]); i += 2; continue
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch; buf.append(ch)
            elif line.startswith("//", i):
                break
            else:
                buf.append(ch)
            i += 1
        out.append("".join(buf).rstrip())
    return "\n".join(out)


def features(src: str) -> dict:
    code = strip_comments(src)
    vm = re.search(r"//\s*@version\s*=\s*(\d+)", src)
    decl = None
    m = re.search(r"^\s*(strategy|study|indicator|library)\s*\(", code, re.M)
    if m:
        decl = m.group(1)
    commented_strategy = bool(re.search(r"^\s*//\s*strategy\s*\(", src, re.M)) and decl is None
    body = re.sub(r"\s+", " ", code)
    norm = re.sub(r"\b(plot|plotshape|plotchar|plotarrow|bgcolor|barcolor|fill|hline|label\.new|line\.new|box\.new|table\.\w+|alertcondition|alert)\s*\(.*", "", code, flags=re.M)
    norm = re.sub(r"\s+", "", norm)
    f = {
        "version": int(vm.group(1)) if vm else None,
        "decl": decl or ("strategy(commented)" if commented_strategy else None),
        "n_lines": len([l for l in code.splitlines() if l.strip()]),
        "has_entry": bool(re.search(r"\bstrategy\.(entry|order)\s*\(", code)),
        "has_close": bool(re.search(r"\bstrategy\.(close|close_all)\s*\(", code)),
        "has_exit": bool(re.search(r"\bstrategy\.exit\s*\(", code)),
        "uses_security": bool(re.search(r"\b(request\.)?security\s*\(", code)),
        "lookahead_on": bool(re.search(r"lookahead\s*=\s*barmerge\.lookahead_on|lookahead_on", code)),
        "uses_array": bool(re.search(r"\barray\.", code)),
        "uses_matrix": bool(re.search(r"\bmatrix\.", code)),
        "uses_map": bool(re.search(r"\bmap\.", code)),
        "uses_type": bool(re.search(r"^\s*(export\s+)?type\s+\w+", code, re.M)),
        "uses_method": bool(re.search(r"^\s*(export\s+)?method\s+\w+", code, re.M)),
        "uses_import": bool(re.search(r"^\s*import\s+", code, re.M)),
        "uses_varip": "varip" in body,
        "uses_for": bool(re.search(r"^\s*for\s+", code, re.M)),
        "uses_while": bool(re.search(r"^\s*while\s+", code, re.M)),
        "uses_switch": bool(re.search(r"\bswitch\b", code)),
        "uses_request_other": bool(re.search(r"request\.(financial|quandl|economic|dividends|earnings|splits|seed|currency_rate)", code)),
        "calc_on_every_tick": "calc_on_every_tick=true" in body.replace(" ", ""),
        "process_on_close": "process_orders_on_close=true" in body.replace(" ", ""),
        "pyramiding": (lambda mm: int(mm.group(1)) if mm else None)(re.search(r"pyramiding\s*=\s*(\d+)", code)),
        "code_hash": hashlib.sha1(norm.encode()).hexdigest()[:16],
    }
    return f


def parse_file(path: str) -> dict | None:
    text = open(path, encoding="utf-8", errors="replace").read()
    s = split_sections(text)
    src_key = next((k for k in s if k.startswith("Source")), None)
    if not src_key:
        return None
    lang = src_key[len("Source"):].strip(" ()").lower()
    fences = FENCE.findall(s[src_key])
    src = fences[0] if fences else s[src_key]
    row = {"file": os.path.basename(path), "name": s.get("Name", "").strip(), "author": s.get("Author", "").strip(),
           "lang": lang, "detail": s.get("Detail", "").strip(), "last_modified": s.get("Last Modified", "").strip()[:19],
           "desc_len": len(s.get("Strategy Description", "")), "n_args": s.get("Strategy Arguments", "").count("\n|") - 1,
           "source": src}
    if lang == "pinescript":
        row.update(parse_backtest_header(src))
        row.update(features(src))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--out", default="results/pine/index.parquet")
    a = ap.parse_args()
    d = os.path.join(a.vault, "strategies")
    rows = [r for r in (parse_file(os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith(".md") and f != "README.md") if r]
    df = pd.DataFrame(rows)
    df["last_modified"] = pd.to_datetime(df["last_modified"], errors="coerce")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    df.to_parquet(a.out, index=False)
    print(df["lang"].value_counts().to_string())
    print(f"written {a.out}: {len(df)} rows")


if __name__ == "__main__":
    main()
