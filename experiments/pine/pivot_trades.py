#!/usr/bin/env python3
"""Label every trade of Pivot-Based-Volume-Weighted-Breakout-Reversal-Strategy (vault #1) by signal type and exit
reason, using the independent re-implementation in results/pine_review/indep.py (matched the engine trade for trade).
Descriptive only. Usage: PYTHONPATH=src python3 experiments/pine/pivot_trades.py
"""
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, "results/pine_review")
from indep import bars, minutes, minute_slices, walk_minute, pivot, sma, DAY  # noqa: E402

OOS0 = pd.Timestamp("2025-04-26")
FEE = 0.0007


def run(sym):
    B = bars(sym, DAY)
    o, h, l, c, v = (B[k].to_numpy() for k in "ohlcv")
    T = pd.to_datetime(B["T"].to_numpy(), unit="ms")
    n = len(c)
    ph, pl = pivot(h, 10, 10, True), pivot(l, 10, 10, False)
    res, sup = np.full(n, np.nan), np.full(n, np.nan)
    rz = sz = np.nan
    for i in range(n):
        if ph[i] == ph[i]:
            rz = ph[i]
        if pl[i] == pl[i]:
            sz = pl[i]
        res[i], sup[i] = rz, sz
    hv = v > sma(v, 20) * 1.5
    with np.errstate(invalid="ignore"):
        lb = (c > res * 1.01) & hv
        lr = (c >= sup * 0.99) & (c <= sup * 1.01) & (l <= sup) & (c > sup) & hv
        sb = (c < sup * 0.99) & hv
        sr = (c >= res * 0.99) & (c <= res * 1.01) & (h >= res) & (c < res) & hv
    mt, mo, mh, ml, mc, _ = minutes(sym)
    ma_, mb_ = minute_slices(sym, B)
    trades, pos, pend, lv = [], None, [], None
    for i in range(n):
        for d, kind in pend:
            if pos and pos["dir"] == d:
                continue
            if pos:
                pos.update(exit_t=T[i], exit_px=o[i], reason="reversed by opposite signal"); trades.append(pos); pos = None
            pos = {"dir": d, "kind": kind, "signal_t": T[i - 1], "entry_t": T[i], "entry_px": o[i],
                   "support": sup[i - 1], "resistance": res[i - 1], "vol_x": v[i - 1] / sma(v, 20)[i - 1]}
        pend = []
        if pos and lv is not None:
            tp, sl = lv[0] if pos["dir"] == 1 else lv[1]
            orders = [("take profit", tp, "rise"), ("stop loss", sl, "fall")] if pos["dir"] == 1 else [("take profit", tp, "fall"), ("stop loss", sl, "rise")]
            for m in range(ma_[i], mb_[i]):
                hit = walk_minute(mo[m], mh[m], ml[m], mc[m], orders)
                if hit is not None:
                    pos.update(exit_t=T[i], exit_px=hit[1], reason=hit[0]); trades.append(pos); pos = None
                    break
        for flag, d, kind in ((lb[i], 1, "long breakout"), (lr[i], 1, "long rejection of support"),
                              (sb[i], -1, "short breakdown"), (sr[i], -1, "short rejection of resistance")):
            if flag:
                pend.append((d, kind))
        lv = ((c[i] * 1.03, c[i] * 0.98), (c[i] * 0.97, c[i] * 1.02))
    if pos:
        pos.update(exit_t=T[-1], exit_px=c[-1], reason="still open"); trades.append(pos)
    D = pd.DataFrame(trades)
    D["ret"] = D.dir * (D.exit_px / D.entry_px - 1) - 2 * FEE
    D["days_held"] = (D.exit_t - D.entry_t).dt.days
    return D[D.exit_t >= OOS0].reset_index(drop=True)


def main():
    out = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        D = run(sym)
        out[sym] = D
        print(f"==== {sym}: {len(D)} OOS trades, win rate {(D.ret > 0).mean():.3f}, sum of returns {D.ret.sum():+.3f}")
        print(D.groupby("kind").agg(n=("ret", "size"), win=("ret", lambda x: round((x > 0).mean(), 2)), avg=("ret", lambda x: round(x.mean(), 4))).to_string())
        print(D.reason.value_counts().to_string())
        print("held (days):", D.days_held.value_counts().sort_index().to_dict())
        feb = D[(D.entry_t >= "2026-01-25") & (D.entry_t < "2026-02-20")]
        print("late Jan-Feb 2026:")
        print(feb[["entry_t", "kind", "entry_px", "exit_px", "reason", "ret"]].to_string(index=False))
    pd.concat([d.assign(symbol=s) for s, d in out.items()]).to_csv("results/pine_final/pivot_trades_oos.csv", index=False)


if __name__ == "__main__":
    main()
