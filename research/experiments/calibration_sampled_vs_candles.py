#!/usr/bin/env python3
"""Methodological check (not a strategy trial): how much does the sampled-mid approximation distort results?
Run identical configurations on BTC/ETH (a) real 15m candles and (b) Tohshi 15-min sampled mids over the same window
(2026-05-26 .. 2026-07-17, US session), base costs. Differences arise from: two-point bars (no intrabar stops/targets),
observation jitter, and mid vs open fills. Output: results/calibration_sampled_vs_candles.csv"""
import os, sys, json
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.data import load_sampled, load_candles
from hlr2.strategies import MATrend, BollingerMR, VolCompressionBO, ChannelBO, TrendPullback, OpeningRange
from hlr2.backtest import run
from hlr2.metrics import summarize
from hlr2.funding import load_funding
syms = ["BTC", "ETH"]; fund = load_funding(syms)
pc = load_candles(syms, "15m", "us_regular")
ps = load_sampled(syms)
start, end = pc.grid[0], pc.grid[-1]
cfgs = [MATrend(tf="15m", fast=10, slow=40), MATrend(tf="1h", fast=10, slow=40), BollingerMR(tf="15m", k=2.0), VolCompressionBO(tf="1h", pct=0.2), ChannelBO(tf="15m", n=24), TrendPullback(defn="A", target_r=2.0), OpeningRange(or_min=30, target_r=2.0), OpeningRange(or_min=60, target_r=None)]
rows = []
for strat in cfgs:
    rc = run(pc, strat, funding=fund, start=start, end=end); rs = run(ps, strat, funding=fund, start=start, end=end)
    sc, ss = summarize(rc["trades"]), summarize(rs["trades"])
    rows.append({"config": strat.describe(), "candles_n": sc["n_trades"], "candles_net": sc["net_pnl"], "candles_pf": sc["profit_factor"], "candles_stop_exits": sc.get("stop_exits"), "candles_avg_stop_loss": float(rc["trades"][rc["trades"].reason == "stop"].net_pnl.mean()) if sc["n_trades"] and (rc["trades"].reason == "stop").any() else np.nan,
                 "sampled_n": ss["n_trades"], "sampled_net": ss["net_pnl"], "sampled_pf": ss["profit_factor"], "sampled_stop_exits": ss.get("stop_exits"), "sampled_avg_stop_loss": float(rs["trades"][rs["trades"].reason == "stop"].net_pnl.mean()) if ss["n_trades"] and (rs["trades"].reason == "stop").any() else np.nan})
    print(rows[-1], flush=True)
df = pd.DataFrame(rows); df.to_csv(os.path.join(R, "results", "calibration_sampled_vs_candles.csv"), index=False)
print(df.round(2).to_string(index=False))
