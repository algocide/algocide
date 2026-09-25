#!/usr/bin/env python3
"""Perp-execution cross-check for a daily strategy: build daily bars for xyz stock perps from the 15-minute sampled mids
(open = first in-session sample, close = last in-session sample, high/low = extremes of the day's samples incl. overnight,
2026-05-01 → 09-25) and run the same rule in $100-account mode; compare with the underlying-price run over the same
dates. Differences = perp basis, overnight/weekend internal pricing, and the sampled high/low understatement.
Usage: PYTHONPATH=src python3 experiments/phase3_perp_crosscheck.py --config "MR-RSI2(th=10)" [--names SNDK,MU,...]"""
import argparse, os, sys, json
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.daily import run_daily, DailyCosts
from hlr2.daily_data import build
from hlr2.metrics import summarize
from hlr2.sessions import ET
ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True); ap.add_argument("--names", default=None); ap.add_argument("--mode", default="account"); a = ap.parse_args()
raw = pd.read_parquet(os.path.join(R, "data", "raw", "tohshi_mid_15m.parquet")); raw = raw[raw.symbol.str.startswith("xyz:")]
et = raw.collected_at.dt.tz_convert(ET); raw["date"] = pd.to_datetime(et.dt.date); raw["insess"] = (et.dt.weekday < 5) & ((et.dt.hour * 60 + et.dt.minute) >= 570) & ((et.dt.hour * 60 + et.dt.minute) < 960)
panel, _, cal, _ = build()
names = a.names.split(",") if a.names else sorted({s.split(":")[1] for s in raw.symbol.unique()} & set(panel))
perp = {}
for s in names:
    d = raw[raw.symbol == f"xyz:{s}"]; ins = d[d.insess]
    g = ins.groupby("date").agg(o=("price", "first"), c=("price", "last")); hl = d.groupby("date").agg(h=("price", "max"), l=("price", "min"))
    bars = g.join(hl, how="left"); bars["h"] = bars[["h", "o", "c"]].max(axis=1); bars["l"] = bars[["l", "o", "c"]].min(axis=1)
    # prepend the underlying's history for indicator warm-up (SMA200 needs it), scaled to the perp level on the first overlap day
    u = panel[s].set_index("date"); first = bars.index.min(); ratio = bars.c.iloc[0] / u.c.loc[:first].dropna().iloc[-1]
    hist = u[u.index < first][["o", "h", "l", "c"]] * ratio
    full = pd.concat([hist, bars[["o", "h", "l", "c"]]]).reset_index().rename(columns={"index": "date"}); full["v"] = np.nan
    perp[s] = full
under = {s: panel[s] for s in names}
start, end = pd.Timestamp("2026-05-01"), pd.Timestamp("2026-09-24")
res = {}
for label, P in [("underlying", under), ("perp_sampled", perp)]:
    t = run_daily(P, a.config, mode=a.mode, costs=DailyCosts(), start=start, end=end, specs={s: (3, 10.0) for s in names}); s_ = summarize(t)
    res[label] = {k: v for k, v in s_.items() if not isinstance(v, dict)}; print(f"{label:13s} n={s_['n_trades']} net={s_['net_pnl']:.2f} pf={s_['profit_factor']:.2f} win={s_['win_rate']:.2f} stops={s_.get('stop_exits')} funding={s_.get('funding')}")
    if len(t): t.to_parquet(os.path.join(R, "results", "phase3", f"crosscheck_{label}.parquet"), index=False)
json.dump(res, open(os.path.join(R, "results", "phase3", f"crosscheck_{a.config.replace('(', '_').replace(')', '').replace(',', '_').replace('=', '')}.json"), "w"), indent=1, default=str)
