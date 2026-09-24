#!/usr/bin/env python3
"""POST-HOC (decided after seeing E4): H7 premium-reversion event study restricted to the most liquid xyz equity
names (top 15 by median OI x price in the 40-day OI snapshot file) and bucketed by |p0|. Written 2026-09-24 after
E4 run 3; recorded in the ledger. It exists because the pooled result may be driven by illiquid names whose
quoted spreads (unobserved here) would swamp the reversion."""
import os, sys, json
import numpy as np, pandas as pd
from zoneinfo import ZoneInfo
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from hlr.stats import circular_block_bootstrap_ci
ET = ZoneInfo("America/New_York")
OUT = os.path.join(ROOT, "results", "e4")

fu = pd.read_parquet(os.path.join(ROOT, "data", "derived", "funding_hourly.parquet"))
oi = pd.read_parquet(os.path.join(ROOT, "data", "derived", "oi_snapshots.parquet"))
oi = oi[oi["coin"].str.startswith("xyz:")]
oi["oi_usd"] = oi["oi"].astype(float) * oi["px"].astype(float)
liq = oi.groupby("coin")["oi_usd"].median().sort_values(ascending=False)
top = list(liq.index[:15]); bottom = list(liq.index[-30:])
p = fu[fu["dex"] == "xyz"].pivot_table(index="ts", columns="coin", values="premium").sort_index()


def events(cols):
    rows = []
    for c in cols:
        if c not in p: continue
        s = p[c].dropna()
        thr = s.abs().rolling(24 * 30, min_periods=24 * 20).quantile(0.95).shift(1)
        ev = s[s.abs() > thr]; last = None
        for t, v in ev.items():
            if last is not None and (t - last) < pd.Timedelta(hours=24): continue
            last = t
            te = t.tz_convert(ET)
            wk = ((te.dayofweek == 4) and te.hour >= 20) or te.dayofweek == 5 or (te.dayofweek == 6 and te.hour < 20)
            sess = "weekend" if wk else ("overnight" if (te.hour >= 20 or te.hour < 4) else "external")
            r = {"coin": c, "t": t, "p0": v, "session": sess}
            for h in [1, 6, 24]:
                r[f"d{h}"] = (s.get(t + pd.Timedelta(hours=h), np.nan) - v) * -np.sign(v)
            rows.append(r)
    return pd.DataFrame(rows)


res = {"top15_by_oi": top, "median_oi_usd_top15": {k: float(liq[k]) for k in top}}
md = ["# E4b (post-hoc) — H7 premium reversion in liquid vs illiquid xyz names\n", f"Top-15 by median OI (USD): {', '.join(f'{k} (${liq[k]/1e6:.1f}M)' for k in top)}\n"]
for label, cols in [("top15_liquid", top), ("bottom30_illiquid", bottom), ("all", list(p.columns))]:
    ev = events(cols); res[label] = {}
    md.append(f"\n## {label}: {len(ev)} events, mean |p0| {ev['p0'].abs().mean()*1e4:.1f} bps\n")
    md.append("| session | n | mean abs p0 bps | reversion 1h [CI] | reversion 6h [CI] | reversion 24h |\n|---|---|---|---|---|---|")
    for sess, g in list(ev.groupby("session")) + [("all", ev)]:
        row = {"n": int(len(g)), "abs_p0": float(g["p0"].abs().mean() * 1e4)}
        cells = []
        for h in [1, 6, 24]:
            x = g[f"d{h}"].dropna().values * 1e4
            if len(x) > 10:
                m, lo, hi = circular_block_bootstrap_ci(x, block=10, n_boot=1000)
                row[f"h{h}"] = [m, lo, hi]; cells.append(f"{m:.1f} [{lo:.1f}, {hi:.1f}]")
            else:
                cells.append("n/a")
        res[label][sess] = row
        md.append(f"| {sess} | {row['n']} | {row['abs_p0']:.1f} | {cells[0]} | {cells[1]} | {cells[2]} |")
    # by |p0| bucket (external only)
    ex = ev[ev["session"] == "external"].copy()
    if len(ex) > 30:
        ex["bucket"] = pd.cut(ex["p0"].abs() * 1e4, [0, 20, 40, 80, 1e9], labels=["<20", "20-40", "40-80", ">80"])
        g = ex.groupby("bucket", observed=True).agg(n=("p0", "size"), abs_p0=("p0", lambda x: x.abs().mean() * 1e4), r1=("d1", lambda x: x.mean() * 1e4), r6=("d6", lambda x: x.mean() * 1e4))
        res[label]["external_by_bucket"] = g.round(2).to_dict(orient="index")
        md.append("\nExternal-session events by |p0| bucket (bps): " + "; ".join(f"{k}: n={int(v['n'])}, |p0|={v['abs_p0']:.0f}, rev1h={v['r1']:.1f}, rev6h={v['r6']:.1f}" for k, v in g.iterrows()))
json.dump(res, open(os.path.join(OUT, "e4b_h7_liquid.json"), "w"), indent=1, default=str)
open(os.path.join(OUT, "e4b_h7_liquid.md"), "w").write("\n".join(md))
print("\n".join(md))
print("\npara:AVGO|xyz:AVGO details:", json.dumps(json.load(open(os.path.join(OUT, "e4_results.json")))["h14_cross_dex"]["para:AVGO|xyz:AVGO"], indent=0, default=str)[:900])

# ---- POST-HOC addition (same day): realistic timing. The hourly-average premium p_t is only known at the END of hour t,
# so the earliest entry is at hour t+1. Measure reversion from t+1 to t+7, and only when the extreme persists at t+1
# (|p_{t+1}| > thr), for external-session events in the liquid subset and in all names.
def tradable(cols):
    rows = []
    for c in cols:
        if c not in p: continue
        s = p[c].dropna()
        thr = s.abs().rolling(24 * 30, min_periods=24 * 20).quantile(0.95).shift(1)
        ev = s[s.abs() > thr]; last = None
        for t, v in ev.items():
            if last is not None and (t - last) < pd.Timedelta(hours=24): continue
            last = t
            t1 = t + pd.Timedelta(hours=1)
            if t1 not in s.index or t1 not in thr.index: continue
            p1 = s[t1]
            te = t1.tz_convert(ET)
            wk = ((te.dayofweek == 4) and te.hour >= 20) or te.dayofweek == 5 or (te.dayofweek == 6 and te.hour < 20)
            sess = "weekend" if wk else ("overnight" if (te.hour >= 20 or te.hour < 4) else "external")
            persists = abs(p1) > thr[t1] if not np.isnan(thr[t1]) else False
            r = {"coin": c, "t1": t1, "p1": p1, "session": sess, "persists": bool(persists)}
            for h in [1, 3, 6]:
                r[f"d{h}"] = (s.get(t1 + pd.Timedelta(hours=h), np.nan) - p1) * -np.sign(p1)
            rows.append(r)
    return pd.DataFrame(rows)

md2 = ["\n## Realistic timing: enter at t+1 (after the extreme hour is known), reversion measured from t+1\n",
       "| subset | condition | session | n | mean |p(t+1)| bps | rev 1h [CI] | rev 3h [CI] | rev 6h [CI] |\n|---|---|---|---|---|---|---|---|"]
res["tradable"] = {}
for label, cols in [("top15_liquid", top), ("all", list(p.columns))]:
    tv = tradable(cols)
    for cond_name, cond in [("all events", tv["session"].notna()), ("extreme persists at t+1", tv["persists"])]:
        for sess in ["external", "overnight", "weekend"]:
            g = tv[cond & (tv["session"] == sess)]
            if len(g) < 15: continue
            cells = []; rec = {"n": int(len(g)), "abs_p1": float(g["p1"].abs().mean() * 1e4)}
            for h in [1, 3, 6]:
                x = g[f"d{h}"].dropna().values * 1e4
                m, lo, hi = circular_block_bootstrap_ci(x, block=10, n_boot=1000); rec[f"h{h}"] = [m, lo, hi]; cells.append(f"{m:.1f} [{lo:.1f}, {hi:.1f}]")
            res["tradable"][f"{label}|{cond_name}|{sess}"] = rec
            md2.append(f"| {label} | {cond_name} | {sess} | {rec['n']} | {rec['abs_p1']:.1f} | {cells[0]} | {cells[1]} | {cells[2]} |")
json.dump(res, open(os.path.join(OUT, "e4b_h7_liquid.json"), "w"), indent=1, default=str)
open(os.path.join(OUT, "e4b_h7_liquid.md"), "a").write("\n".join(md2))
print("\n".join(md2))
