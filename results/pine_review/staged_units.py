#!/usr/bin/env python3
"""Quantify the absolute-qty unit mismatch on Dual-EMA-Trend-Following-Strategy-with-Staged-Position-Exit
(engine semantics vs the script's own units rescaled to the tournament's entry size)."""
import math, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indep

DAY = 86_400_000
F = "Dual-EMA-Trend-Following-Strategy-with-Staged-Position-Exit.md"


def oos_stats(B, eq, trades, lm="2025-02-24 10:23:24", end="2026-09-28"):
    day = (B.TC.to_numpy() - 1) // DAY
    s = pd.Series(eq, index=day).groupby(level=0).last()
    lm_ms = int(pd.Timestamp(lm).value // 10**6)
    d0 = max(int(s.index[0]) + 30, (lm_ms + DAY) // DAY + 1)
    d1 = int(pd.Timestamp(end).value // 10**6 // DAY)
    e = s.loc[d0 - 1:d1]
    r = e.pct_change().dropna()
    nt = sum(1 for t in trades if d0 * DAY <= t[2] < (d1 + 1) * DAY)
    return dict(sharpe=r.mean() / r.std() * math.sqrt(365), cagr=(e.iloc[-1] / e.iloc[0]) ** (365 / len(r)) - 1,
                mdd=float((e / e.cummax() - 1).min()), trades=nt, days=len(r))


if __name__ == "__main__":
    out = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        for mode in ("engine", "fraction"):
            B, A, eq = indep.s_staged(sym, mode)
            out[(sym, mode)] = oos_stats(B, eq, A.trades)
    df = pd.DataFrame(out).T
    print(df.to_string())
    sc = {m: np.mean([out[("BTCUSDT", m)]["sharpe"], out[("ETHUSDT", m)]["sharpe"]]) for m in ("engine", "fraction")}
    print("score (mean OOS Sharpe BTC/ETH):", sc)
