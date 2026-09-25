#!/usr/bin/env python3
"""Calibrate the sampled-data stop-proximity factor on BTC/ETH: for f in a grid, run the channel/ORB/MA configs on
Tohshi sampled mids and compare stop-exit counts and net P&L with the same configs on real candles (same window).
Choose the smallest f whose total stop-exit count across configs is >= the candle count."""
import os, sys, numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.data import load_sampled, load_candles
from hlr2.strategies import ChannelBO, OpeningRange, MATrend, TrendPullback
from hlr2.backtest import run
from hlr2.metrics import summarize
from hlr2.funding import load_funding
syms = ["BTC", "ETH"]; fund = load_funding(syms); pc = load_candles(syms, "15m", "us_regular"); ps = load_sampled(syms); a, b = pc.grid[0], pc.grid[-1]
cfgs = [ChannelBO(tf="15m", n=12), ChannelBO(tf="15m", n=24), OpeningRange(or_min=30, target_r=2.0), MATrend(tf="15m", fast=20, slow=80), TrendPullback(defn="A", target_r=2.0)]
rows = []
for strat in cfgs:
    rc = run(pc, strat, funding=fund, start=a, end=b); sc = summarize(rc["trades"])
    for f in [0.0, 0.25, 0.5, 0.75, 1.0]:
        rs = run(ps, strat, funding=fund, start=a, end=b, stop_proximity_atr=f); ss = summarize(rs["trades"])
        rows.append({"config": strat.describe(), "f": f, "candles_n": sc["n_trades"], "candles_stops": sc.get("stop_exits"), "candles_net": sc["net_pnl"], "sampled_n": ss["n_trades"], "sampled_stops": ss.get("stop_exits"), "sampled_net": ss["net_pnl"]})
df = pd.DataFrame(rows); df.to_csv(os.path.join(R, "results", "calibration_stop_proximity.csv"), index=False)
pd.set_option("display.width", 200); print(df.round(2).to_string(index=False))
g = df.groupby("f").agg(candles_stops=("candles_stops", "sum"), sampled_stops=("sampled_stops", "sum"), candles_net=("candles_net", "sum"), sampled_net=("sampled_net", "sum"), abs_net_gap=("sampled_net", lambda x: 0))
g["abs_net_gap"] = df.assign(gap=(df.sampled_net - df.candles_net).abs()).groupby("f").gap.sum()
print(g.round(2).to_string())
