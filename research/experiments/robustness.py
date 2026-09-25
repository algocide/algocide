#!/usr/bin/env python3
"""Nearby-parameter robustness for one candidate on DEV+VALIDATION only (holdout untouched). Each variant counts
against the 60-configuration budget and is appended to EXPERIMENTS.csv with family tag 'R'.
Usage: PYTHONPATH=src python3 experiments/robustness.py --universe stocks --family F6 --strategy orb --base '{"or_min":30,"target_r":2.0}' --grid '{"or_min":[15,45],"target_r":[1.5,3.0]}'
"""
import argparse, os, sys, json, time
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.data import load_sampled, load_candles
from hlr2.strategies import REGISTRY
from hlr2.backtest import run
from hlr2.metrics import summarize, windows
from hlr2.funding import load_funding
from run_experiments import STOCKS, CRYPTO, split_days

ap = argparse.ArgumentParser(); ap.add_argument("--universe", required=True); ap.add_argument("--strategy", required=True); ap.add_argument("--base", required=True); ap.add_argument("--grid", required=True); ap.add_argument("--regime", default="base")
a = ap.parse_args(); u = a.universe
if u == "stocks": panel = load_sampled(STOCKS); syms = STOCKS
elif u == "crypto15": panel = load_candles(CRYPTO, "15m", "us_regular"); syms = CRYPTO
elif u == "crypto1h": panel = load_candles(CRYPTO, "1h", "us_regular"); syms = CRYPTO
else: raise SystemExit(u)
fund = load_funding(syms); sp = split_days(panel); out = os.path.join(R, "results", u)
base = json.loads(a.base); grid = json.loads(a.grid); rows = []
variants = [dict(base)]
for k, vals in grid.items():
    for v in vals: variants.append({**base, k: v})
for prm in variants:
    strat = REGISTRY[a.strategy](**prm); t0 = time.time()
    res = run(panel, strat, cost_regime=a.regime, funding=fund); tr = res["trades"]
    ent = pd.to_datetime(tr.entry_ts, utc=True) if len(tr) else pd.Series([], dtype="datetime64[ns, UTC]"); xt = pd.to_datetime(tr.exit_ts, utc=True) if len(tr) else ent
    dev = tr[(ent < sp["val_start"]) & (xt <= sp["val_start"])] if len(tr) else tr
    val = tr[(ent >= sp["val_start"]) & (ent < sp["hold_start"]) & (xt <= sp["hold_start"])] if len(tr) else tr
    sd, sv = summarize(dev), summarize(val); w = windows(val, sp["val_windows"])
    rows.append({"universe": u, "family": "R", "config": f"R:{strat.describe()}", "regime": a.regime, "is_base": prm == base, "dev_n": sd["n_trades"], "dev_net": sd["net_pnl"], "dev_pf": sd["profit_factor"],
                 "val_n": sv["n_trades"], "val_net": sv["net_pnl"], "val_exp": sv["expectancy_usd"], "val_pf": sv["profit_factor"], "val_win": sv["win_rate"], "val_windows_pos": int((w.net > 0).sum()), "val_windows_n": len(w), "seconds": round(time.time() - t0, 1)})
    print(rows[-1], flush=True)
df = pd.DataFrame(rows); df.to_csv(os.path.join(out, f"robustness_{a.strategy}.csv"), index=False)
ex = os.path.join(out, "experiments.csv")
if os.path.exists(ex):
    pd.concat([pd.read_csv(ex), df[~df.is_base]], ignore_index=True).to_csv(ex, index=False)
print("done")
