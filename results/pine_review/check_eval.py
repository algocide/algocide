#!/usr/bin/env python3
"""Task 3: recompute OOS metrics of one tournament run from its npz and the raw 1m data, compare with
results/pine_mag/metrics.parquet (written by experiments/pine/evaluate.py). No pinebt import.

Usage: python3 results/pine_review/check_eval.py [file] [symbol]
"""
import hashlib, math, os, sys
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DAY = 86_400_000


def run_key(file, symbol):
    return hashlib.sha1(f"{file}|{symbol}".encode()).hexdigest()[:16]


def daily_close(sym):
    df = pd.read_parquet(os.path.join(ROOT, "data", "binance", f"{sym}_1m.parquet"), columns=["open_time", "close"])
    d = df.open_time // DAY
    return df.groupby(d).close.last()          # close of the last minute of each UTC day, index = day number


def main(file, sym, runs="data/pine/t2_mag", metrics="results/pine_mag/metrics.parquet"):
    idx = pd.read_parquet(os.path.join(ROOT, "data/pine/index.parquet"))
    lm = pd.Timestamp(idx[idx.file == file].iloc[0].last_modified)
    z = np.load(os.path.join(ROOT, runs, "runs", run_key(file, sym) + ".npz"))
    days, eq, tr = z["days"].astype(np.int64), z["eq"], z["trades"]
    eqs = pd.Series(eq, index=days)
    # OOS window: first full UTC day after Last Modified + 1 day (the pre-registration's "first bar after LM + 1 day"
    # for a daily chart), held at least 30 days after the first bar; ends 2026-09-28.
    lm_ms = int(lm.value // 10**6)
    first_bar_after = (lm_ms + DAY) // DAY + 1
    d0 = max(int(days[0]) + 30, first_bar_after)
    d1 = int(pd.Timestamp("2026-09-28", tz="UTC").value // 10**6 // DAY)
    e = eqs.loc[d0 - 1:d1]
    r = e.pct_change().dropna()
    n = len(r)
    sharpe = r.mean() / r.std(ddof=1) * math.sqrt(365)
    cagr = (e.iloc[-1] / e.iloc[0]) ** (365 / n) - 1
    mdd = (e / e.cummax() - 1).min()
    t0, t1 = d0 * DAY, (d1 + 1) * DAY
    ntr = int(((tr[:, 1] >= t0) & (tr[:, 1] < t1)).sum())
    bh = daily_close(sym).loc[d0 - 1:d1]
    br = bh.pct_change().dropna()
    bh_sh = br.mean() / br.std(ddof=1) * math.sqrt(365)
    bh_cagr = (bh.iloc[-1] / bh.iloc[0]) ** (365 / len(br)) - 1
    M = pd.read_parquet(os.path.join(ROOT, metrics))
    m = M[(M.file == file) & (M.symbol == sym)].iloc[0]
    rows = [("oos_start_day", pd.to_datetime(d0 * DAY, unit="ms").date(), pd.to_datetime(int(m.oos_start_day) * DAY, unit="ms").date()),
            ("oos_days", n, m.oos_days), ("sharpe", sharpe, m.oos_sharpe), ("cagr", cagr, m.oos_cagr), ("mdd", mdd, m.oos_mdd),
            ("trades", ntr, m.oos_trades), ("bh_sharpe", bh_sh, m.oos_bh_sharpe), ("bh_cagr", bh_cagr, m.oos_bh_cagr)]
    print(f"{file} {sym}  last_modified={lm}")
    for k, a, b in rows:
        print(f"  {k:14s} mine={a!s:>24}  evaluate.py={b!s:>24}")


if __name__ == "__main__":
    f = sys.argv[1] if len(sys.argv) > 1 else "Pivot-Based-Volume-Weighted-Breakout-Reversal-Strategy.md"
    s = sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT"
    main(f, s)
