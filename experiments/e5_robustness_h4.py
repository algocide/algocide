#!/usr/bin/env python3
"""E5: pre-registered robustness battery for H4 (equity-perp funding harvest): start dates, parameter neighbourhood,
cost stress, funding-reversal stress, execution delay, per-quarter, top coin-week concentration. Uses the same
simulator as E4 (imported), standard HIP-3 fees unless stated."""
import os, sys, json
import numpy as np, pandas as pd
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e4_funding_carry as e4
from hlr.costs import FEE_REGIMES
OUT = os.path.join(ROOT, "results", "e5"); os.makedirs(OUT, exist_ok=True)

fu, st, meta = e4.load()


def run(K=5, fee="hip3_standard", start="2025-12-01", trailing_days=7, cost_mult=1.0, funding_mult=1.0, delay_hours=0, margin=0.25, basis=True):
    """Re-implementation of the H4 loop with knobs (kept in sync with e4.h4_equity_harvest)."""
    mapped = {t["coin"] for t in meta["tickers"]}
    m = e4.hourly_matrix(fu, "xyz"); p = e4.hourly_matrix(fu, "xyz", "premium")
    m = m[[c for c in m.columns if c in mapped]] * funding_mult; p = p[m.columns]
    feeb = FEE_REGIMES[fee].taker * 1e4
    side_perp = (feeb + 1.0 + 0.5) * cost_mult; side_stock = (1.0 + 0.5) * cost_mult
    cap = 1.0 + margin
    mondays = pd.date_range(pd.Timestamp(start, tz="UTC"), m.index.max().normalize(), freq="W-MON")
    weeks = []; held = set()
    for t in mondays:
        hist = m[(m.index < t) & (m.index >= t - pd.Timedelta(days=28))]
        elig = [c for c in m.columns if hist[c].count() >= 27 * 24]
        if len(elig) < max(K, 5): continue
        score = m[(m.index < t) & (m.index >= t - pd.Timedelta(days=trailing_days))][elig].mean()
        score = score[score > 0].sort_values(ascending=False); sel = list(score.index[:K])
        if not sel: continue
        t_exec = t + pd.Timedelta(hours=delay_hours)
        win = m[(m.index >= t_exec) & (m.index < t + pd.Timedelta(days=7))]; pw = p[(p.index >= t_exec) & (p.index < t + pd.Timedelta(days=7))]
        if len(win) < 100: continue
        gross = bas = cost = 0.0
        for c in sel:
            f = win[c].dropna(); pc = pw[c].dropna(); gross += float(f.sum())
            bas += float(pc.iloc[0] - pc.iloc[-1]) if (basis and len(pc) > 1) else 0.0
            cost += 0.0 if c in held else (side_perp + side_stock) / 1e4
        cost += len(held - set(sel)) * (side_perp + side_stock) / 1e4
        fin = e4.RISK_FREE * 7 / 365 * len(sel)
        weeks.append({"week": t, "ret": (gross + bas - cost - fin) / (len(sel) * cap), "sel": sel, "per_coin": {c: float(win[c].sum()) for c in sel}})
        held = set(sel)
    return pd.DataFrame(weeks)


def summ(df):
    if len(df) == 0: return {"n": 0}
    r = df["ret"].values
    return {"n_weeks": int(len(r)), "net_apr": float(r.mean() * 52), "t": float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))) if r.std(ddof=1) > 0 else np.nan,
            "win": float((r > 0).mean()), "worst_week": float(r.min())}


res = {}; md = ["# E5 — robustness battery for H4 (K=5 unless stated, standard HIP-3 fees, 25% margin)\n", "| test | weeks | net APR | t | win | worst week |\n|---|---|---|---|---|---|"]
tests = {
    "baseline": dict(),
    "start 2026-01-05": dict(start="2026-01-05"), "start 2026-03-02": dict(start="2026-03-02"), "start 2026-05-04": dict(start="2026-05-04"),
    "K=3": dict(K=3), "K=7": dict(K=7), "trailing 3d": dict(trailing_days=3), "trailing 14d": dict(trailing_days=14),
    "costs x2": dict(cost_mult=2.0), "costs x3": dict(cost_mult=3.0), "funding x0.5 (reversal stress)": dict(funding_mult=0.5),
    "execute Monday 12:00 UTC": dict(delay_hours=12), "execute Tuesday 00:00": dict(delay_hours=24),
    "no basis term": dict(basis=False), "margin 50%": dict(margin=0.5), "growth fees": dict(fee="hip3_growth"),
}
for name, kw in tests.items():
    df = run(**kw); s = summ(df); res[name] = s
    md.append(f"| {name} | {s.get('n_weeks',0)} | {100*s.get('net_apr',np.nan):.1f}% | {s.get('t',np.nan):.2f} | {100*s.get('win',np.nan):.0f}% | {100*s.get('worst_week',np.nan):.2f}% |")
# per quarter and concentration
base = run()
base["q"] = base["week"].dt.to_period("Q").astype(str)
q = base.groupby("q")["ret"].agg(["mean", "count"]); q["apr"] = q["mean"] * 52
res["per_quarter"] = q.round(5).to_dict(orient="index")
md.append("\nPer quarter (net APR): " + "; ".join(f"{k}: {100*v['apr']:.1f}% ({int(v['count'])} wks)" for k, v in q.iterrows()))
# coin-week concentration: gross funding by coin-week
cw = pd.DataFrame([{"week": r["week"], "coin": c, "f": v} for _, r in base.iterrows() for c, v in r["per_coin"].items()])
tot = cw["f"].sum(); top10 = cw.sort_values("f", ascending=False).head(10)
res["top10_coin_weeks_share_of_gross_funding"] = float(top10["f"].sum() / tot)
res["coin_share_of_gross_funding"] = cw.groupby("coin")["f"].sum().sort_values(ascending=False).head(10).div(tot).round(3).to_dict()
md.append(f"\nTop-10 coin-weeks share of gross funding: {100*res['top10_coin_weeks_share_of_gross_funding']:.0f}%. Top coins by share: " + ", ".join(f"{k} {100*v:.0f}%" for k, v in res["coin_share_of_gross_funding"].items()))
# removing the top-5 coin-weeks' funding
top5 = cw.sort_values("f", ascending=False).head(5)
adj = base.copy()
for _, r in top5.iterrows():
    i = adj.index[adj["week"] == r["week"]][0]; adj.loc[i, "ret"] -= r["f"] / (len(adj.loc[i, "sel"]) * 1.25)
res["net_apr_ex_top5_coin_weeks"] = float(adj["ret"].mean() * 52)
md.append(f"Net APR after removing the 5 most profitable coin-weeks: {100*res['net_apr_ex_top5_coin_weeks']:.1f}%")
json.dump(res, open(os.path.join(OUT, "e5_h4_robustness.json"), "w"), indent=1, default=str)
open(os.path.join(OUT, "e5_h4_robustness.md"), "w").write("\n".join(md))
print("\n".join(md))
