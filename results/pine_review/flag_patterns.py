#!/usr/bin/env python3
"""Screen the top of a ranking for the source patterns behind this review's findings.

Usage: python3 results/pine_review/flag_patterns.py [ranking.csv] [top_n]
  F1 abs_qty      strategy.close/exit/order with qty= not derived from strategy.position_size (engine-unit mix-up)
  F2 stop_limit   strategy.entry/order with both stop= and limit= (filled as stop OR limit)
  F3 v2_security  security() in a v1/v2/unversioned script (TradingView defaulted to lookahead_on)
  F4 hma          ta.hma/hma call whose literal length has frac(sqrt(n)) >= 0.5, or a non-literal length (check by hand)
  F5 limit+trail  limit entries together with trailing exits
  info cof        calc_on_order_fills=true (ignored by the engine)
  info risk       strategy.risk.max_* limits (ignored by the engine)
"""
import math, re, sys
import pandas as pd

ROOT = __file__.rsplit("/results/", 1)[0]


def uncomment(s):
    return "\n".join(l.split("//")[0] for l in s.split("\n"))


def flags(src, version):
    s = uncomment(src)
    out = []
    for m in re.finditer(r"strategy\.(close|exit|order)\s*\(([^\n]*)", s):
        q = re.search(r"\bqty\s*=\s*([^,\)]+)", m.group(2))
        if q and "position_size" not in q.group(1) and "strategy." not in q.group(1):
            out.append("F1:abs_qty"); break
        if m.group(1) == "order" and re.match(r"[^,]+,[^,]+,\s*[0-9.]+", m.group(2)):
            out.append("F1:abs_qty"); break
    if any(re.search(r"\bstop\s*=", m.group(1)) and re.search(r"\blimit\s*=", m.group(1))
           for m in re.finditer(r"strategy\.(?:entry|order)\s*\(([^\n]*)", s)):
        out.append("F2:stop_limit")
    if (version != version or version <= 2) and re.search(r"\bsecurity\s*\(", s):
        out.append("F3:v2_security")
    for m in re.finditer(r"\b(?:ta\.)?hma\s*\(\s*[^,\)]+,\s*([^\)\n]+)\)", s):
        a = m.group(1).strip()
        if re.fullmatch(r"\d+", a):
            if (math.sqrt(int(a)) % 1) >= 0.5:
                out.append(f"F4:hma({a})"); break
        else:
            out.append(f"F4?:hma({a[:20]})"); break
    if re.search(r"strategy\.(entry|order)\s*\([^\n]*\blimit\s*=", s) and re.search(r"trail_(points|price)", s):
        out.append("F5:limit+trail")
    if re.search(r"calc_on_order_fills\s*=\s*true", s):
        out.append("info:calc_on_order_fills")
    if re.search(r"strategy\.risk\.max_\w+\s*\(", s):
        out.append("info:risk_limits")
    return out


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else f"{ROOT}/results/pine_mag/ranking.csv"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    r = pd.read_csv(path)
    if "duplicate_of" in r:
        r = r[r.duplicate_of.isna()]
    idx = pd.read_parquet(f"{ROOT}/data/pine/index.parquet").set_index("file")
    for k, row in enumerate(r.head(n).itertuples(), 1):
        fl = flags(idx.loc[row.file, "source"], idx.loc[row.file, "version"])
        print(f"{k:3d} {row.score:6.3f} {row.file[:78]:78s} {' '.join(fl)}")
