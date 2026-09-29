#!/usr/bin/env python3
"""POST-HOC (docs/eth15/ledger.md item 7): what if the bet goes in k minutes after the candle opens? The reference
price becomes the open of minute k+1 of the candle (the price when the bet is placed) and the bet still settles at
the candle close. Holdout window, frozen rule survivors, perpetual 1-minute data. Also per-year win rates.

Usage: python3 experiments/eth15/posthoc_latency.py
"""
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

M15 = 15 * 60_000


def main():
    split = json.load(open("results/eth15/split.json"))
    h0 = int(pd.Timestamp(split["holdout_from"]).value // 10**6)
    c = pd.concat([pd.read_parquet("data/eth15/research.parquet"), pd.read_parquet("data/eth15/holdout.parquet")],
                  ignore_index=True)
    f = lib.features(c)
    m1 = pd.read_parquet("data/binance/ETHUSDT_1m.parquet", columns=["open_time", "open"])
    m1 = m1[m1.open_time >= h0 - M15]
    op = pd.Series(m1.open.values, index=m1.open_time.values)
    hold = c.t.values >= h0
    surv = [r for r in json.load(open("results/eth15/survivors.json"))["survivors"] if r["kind"] == "rule"]
    out = {"note": "POST-HOC; holdout window; reference price = open of the minute the bet is placed", "latency": {},
           "by_year": {}}
    for k in (0, 1, 2, 5):
        ref = op.reindex(c.t.values + k * 60_000).values if k else c.open.values
        up_k = (c.close.values >= ref).astype(np.int8)
        valid = hold & ~np.isnan(ref)
        rows = []
        for r in surv:
            n, w = lib.score(lib.signal(f, r["family"], r["params"]), up_k, valid)
            rows.append({"id": r["id"], "family": r["family"], "params": r["params"], "n": n, "win_rate": w / n if n else None})
        out["latency"][f"{k}min"] = rows
    years = pd.to_datetime(c.t, unit="ms").dt.year.values
    up = c.up.values.astype(np.int8)
    for r in surv:
        sig = lib.signal(f, r["family"], r["params"])
        yr = {}
        for y in np.unique(years):
            n, w = lib.score(sig, up, years == y)
            yr[int(y)] = {"n": n, "win_rate": w / n if n else None}
        out["by_year"][r["id"]] = yr
    json.dump(out, open("results/eth15/posthoc_latency.json", "w"), indent=1)
    for k, rows in out["latency"].items():
        print(k, "  ".join(f"{x['id']}:{x['win_rate']:.3f}" for x in rows if x["win_rate"] is not None))
    for rid in ("R0050", "R0102", "R0103"):
        print(rid, {y: round(v["win_rate"], 3) for y, v in out["by_year"][rid].items() if v["win_rate"]})


if __name__ == "__main__":
    main()
