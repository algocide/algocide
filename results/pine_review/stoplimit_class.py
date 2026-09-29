#!/usr/bin/env python3
"""Run every vault script that passes both stop= and limit= to strategy.entry/order (timeframe >= 1h) with the engine's
'stop OR limit' fills and with TradingView stop-limit semantics (stoplimit_patch), BTC and ETH, and report OOS Sharpe,
OOS trades and eligibility under each. Single process. Output: stoplimit_class.csv"""
import json, math, os, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stoplimit_patch as sp
from pinebt.engine import CompiledScript

HERE = os.path.dirname(os.path.abspath(__file__))
UNIT = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def tf_ms(per):
    import re
    m = re.match(r"^(\d+)([mhdw])$", str(per).strip())
    return int(m.group(1)) * UNIT[m.group(2)] if m else 3_600_000


def stats(r, lm):
    tc = np.asarray(r["TC"], np.int64); eq = np.asarray(r["equity"])
    day = (tc - 1) // 86_400_000
    s = pd.Series(eq, index=day).groupby(level=0).last()
    lm_ms = int(pd.Timestamp(lm).value // 10**6)
    d0 = max(int(s.index[0]) + 30, (lm_ms + 86_400_000) // 86_400_000 + 1)
    d1 = int(pd.Timestamp("2026-09-28").value // 10**6 // 86_400_000)
    x = s.loc[d0 - 1:d1].pct_change().dropna()
    sh = x.mean() / x.std() * math.sqrt(365) if x.std() > 0 else 0.0
    nt = sum(1 for c in r["closed"] if d0 * 86_400_000 <= c[4] < (d1 + 1) * 86_400_000)
    return sh, nt, len(x), bool(r["blown"])


if __name__ == "__main__":
    files = json.load(open(os.path.join(HERE, "stoplimit_files.json")))
    idx = pd.read_parquet("data/pine/index.parquet").set_index("file")
    rows = []
    for f in files:
        tf = tf_ms(idx.loc[f, "bt_period"])
        if tf < 3_600_000:
            continue
        src, lm = idx.loc[f, "source"], idx.loc[f, "last_modified"]
        row = {"file": f, "tf_ms": tf}
        try:
            for sym in ("BTCUSDT", "ETHUSDT"):
                for patched in (False, True):
                    r = sp.run(src, sym, tf, patched)
                    tag = ("tv_" if patched else "eng_") + sym[:3].lower()
                    sh, nt, nd, bl = stats(r, lm)
                    row.update({tag + "_sharpe": sh, tag + "_trades": nt, tag + "_days": nd, tag + "_blown": bl,
                                tag + "_err": r["err_bars"]})
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"[:200]
        for m in ("eng", "tv"):
            ok = all(row.get(f"{m}_{s}_trades", 0) >= 20 and row.get(f"{m}_{s}_days", 0) >= 365 and
                     not row.get(f"{m}_{s}_blown", True) and row.get(f"{m}_{s}_err", 1) == 0 for s in ("btc", "eth"))
            row[m + "_eligible"] = ok
            row[m + "_score"] = np.nanmean([row.get(f"{m}_btc_sharpe", np.nan), row.get(f"{m}_eth_sharpe", np.nan)])
        rows.append(row)
        print(f"{f[:60]:60s} eng {row['eng_score']:.2f} {row['eng_eligible']}  tv {row['tv_score']:.2f} {row['tv_eligible']}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(HERE, "stoplimit_class.csv"), index=False)
    ch = df[(df.eng_score - df.tv_score).abs() > 1e-9]
    print(f"{len(df)} scripts run; {len(ch)} change score; eligible engine {df.eng_eligible.sum()} vs TV semantics {df.tv_eligible.sum()}")
