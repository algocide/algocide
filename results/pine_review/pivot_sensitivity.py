#!/usr/bin/env python3
"""Sensitivity of the rank-1 script (Pivot-Based-Volume-Weighted-Breakout-Reversal, t2_mag) to fill assumptions that
TradingView's emulator makes and the tournament inherits: take-profit limits fill on a touch, stops fill exactly at
the stop level. Variants: TP needs the price to trade through the limit by k bps; stop fills slip by k bps.
Not an engine bug check - a robustness view of the top script. Usage: python3 results/pine_review/pivot_sensitivity.py"""
import math, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indep
from staged_units import oos_stats

LM = "2025-04-24 17:08:39"


def run(sym, tp_through_bps=0.0, stop_slip_bps=0.0):
    orig = indep.walk_minute

    def walk(o, h, l, c, orders):
        adj = []
        for name, lvl, kind in orders:
            if name == "tp":      # a limit that must be traded through
                lvl = lvl * (1 + tp_through_bps / 1e4) if kind == "rise" else lvl * (1 - tp_through_bps / 1e4)
            adj.append((name, lvl, kind))
        hit = orig(o, h, l, c, adj)
        if hit is None:
            return None
        name, px = hit
        if name == "tp":           # filled at the original limit price, not the penetration level
            k = [x for x in orders if x[0] == "tp"][0]
            px = k[1] if px == [x for x in adj if x[0] == "tp"][0][1] else px
        else:                      # stop: adverse slippage
            kind = [x for x in orders if x[0] == "sl"][0][2]
            px = px * (1 - stop_slip_bps / 1e4) if kind == "fall" else px * (1 + stop_slip_bps / 1e4)
        return name, px

    indep.walk_minute = walk
    try:
        B, A, eq = indep.s_pivot(sym)
    finally:
        indep.walk_minute = orig
    return oos_stats(B, eq, A.trades, lm=LM)


if __name__ == "__main__":
    rows = {}
    for tp, sl in [(0, 0), (1, 0), (5, 0), (0, 10), (0, 30), (5, 30)]:
        s = [run(sym, tp, sl)["sharpe"] for sym in ("BTCUSDT", "ETHUSDT")]
        rows[f"TP through {tp} bps, stop slip {sl} bps"] = {"BTC": s[0], "ETH": s[1], "score": np.mean(s)}
    print(pd.DataFrame(rows).T.round(3).to_string())
