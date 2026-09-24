#!/usr/bin/env python3
"""Adversarial review of H4 (short top-K trailing-funding xyz equity perps, hedged with stock).
Replicates the baseline via the project's own simulator, then: (a) applies the literal pre-registered rejection rule
(net APR >= risk-free + 3%), (b) executes at a time when the STOCK leg can actually be traded (Monday ~09:30 ET),
(c) decomposes the basis term, (d) day-of-week/session location of the premium used for the basis, (e) checks the
telescoping gap in the basis accounting and the never-charged final exit."""
import os, sys, json
import numpy as np, pandas as pd
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e4_funding_carry as e4
from hlr.costs import FEE_REGIMES, RISK_FREE
from hlr.stats import circular_block_bootstrap_ci
OUT = os.path.join(ROOT, "results", "review"); os.makedirs(OUT, exist_ok=True)
fu, st, meta = e4.load()
res = {}

base = e4.h4_equity_harvest(fu, meta, 5, "hip3_standard")
r = base["ret"].values
print(f"baseline replication: weeks={len(r)} net APR={r.mean()*52*100:.2f}% t_iid={r.mean()/r.std(ddof=1)*np.sqrt(len(r)):.2f} mean turnover={base['turnover'].mean():.2f} of 5")
_, lo, hi = circular_block_bootstrap_ci(r, block=4, n_boot=2000)
print(f"  CI [{lo*52*100:.1f}, {hi*52*100:.1f}]; components APR: gross funding {base['gross_funding_ret'].mean()*52*100:.1f}%, basis {base['basis_ret'].mean()*52*100:.1f}%, cost {base['cost_ret'].mean()*52*100:.1f}%, financing {base['fin_ret'].mean()*52*100:.1f}%")
res["baseline"] = {"net_apr": float(r.mean() * 52), "ci": [float(lo * 52), float(hi * 52)], "weeks": int(len(r))}
print(f"  literal pre-registered bar: net APR >= risk-free + 3% = {RISK_FREE*100+3:.2f}%  -> {'PASS' if r.mean()*52 >= RISK_FREE+0.03 else 'FAIL'} (net already deducts financing at risk-free on the stock leg)")
print(f"  alternative reading (excess over risk-free >= 3%): {'PASS' if r.mean()*52 >= 0.03 else 'FAIL'} point estimate, CI lower bound {lo*52*100:.1f}% -> {'PASS' if lo*52 >= 0.03 else 'FAIL'}")
dev = base[base["week"] < e4.DEV_END]["ret"]; val = base[base["week"] >= e4.DEV_END]["ret"]
print(f"  dev APR {dev.mean()*52*100:.1f}% ({len(dev)} wks) val APR {val.mean()*52*100:.1f}% ({len(val)} wks); val t_iid={val.mean()/val.std(ddof=1)*np.sqrt(len(val)):.2f}")


def run(K=5, fee="hip3_standard", start="2025-12-01", trailing_days=7, delay_hours=0, margin=0.25, basis=True, basis_delay_hours=None, hs_perp=1.0):
    """E5-style simulator with an extra knob: basis_delay_hours (premium reference taken at t+delay for entry and t+7d+delay for exit,
    i.e. the perp is executed when the stock can be hedged) and a configurable perp half-spread."""
    mapped = {t["coin"] for t in meta["tickers"]}
    m = e4.hourly_matrix(fu, "xyz"); p = e4.hourly_matrix(fu, "xyz", "premium")
    m = m[[c for c in m.columns if c in mapped]]; p = p[m.columns]
    feeb = FEE_REGIMES[fee].taker * 1e4
    side_perp = feeb + hs_perp + 0.5; side_stock = 1.0 + 0.5
    cap = 1.0 + margin
    mondays = pd.date_range(pd.Timestamp(start, tz="UTC"), m.index.max().normalize(), freq="W-MON")
    weeks = []; held = set()
    bd = delay_hours if basis_delay_hours is None else basis_delay_hours
    for t in mondays:
        hist = m[(m.index < t) & (m.index >= t - pd.Timedelta(days=28))]
        elig = [c for c in m.columns if hist[c].count() >= 27 * 24]
        if len(elig) < max(K, 5): continue
        score = m[(m.index < t) & (m.index >= t - pd.Timedelta(days=trailing_days))][elig].mean()
        score = score[score > 0].sort_values(ascending=False); sel = list(score.index[:K])
        if not sel: continue
        t_exec = t + pd.Timedelta(hours=delay_hours)
        win = m[(m.index >= t_exec) & (m.index < t_exec + pd.Timedelta(days=7))]
        pw = p[(p.index >= t + pd.Timedelta(hours=bd)) & (p.index < t + pd.Timedelta(hours=bd) + pd.Timedelta(days=7))]
        if len(win) < 100: continue
        gross = bas = cost = 0.0
        for c in sel:
            f = win[c].dropna(); pc = pw[c].dropna(); gross += float(f.sum())
            bas += float(pc.iloc[0] - pc.iloc[-1]) if (basis and len(pc) > 1) else 0.0
            cost += 0.0 if c in held else (side_perp + side_stock) / 1e4
        cost += len(held - set(sel)) * (side_perp + side_stock) / 1e4
        fin = RISK_FREE * 7 / 365 * len(sel)
        weeks.append({"week": t, "ret": (gross + bas - cost - fin) / (len(sel) * cap), "gross": gross / (len(sel) * cap), "bas": bas / (len(sel) * cap), "sel": sel})
        held = set(sel)
    return pd.DataFrame(weeks)


def summ(df):
    r = df["ret"].values
    return {"weeks": int(len(r)), "net_apr": float(r.mean() * 52), "t": float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))), "basis_apr": float(df["bas"].mean() * 52), "gross_apr": float(df["gross"].mean() * 52)}


print("\n== execution-time realism (stock leg tradeable only 04:00-20:00 ET; Monday 00:00 UTC = Sunday evening ET) ==")
tests = {"as reported (Mon 00:00 UTC)": dict(), "perp+stock at Mon 14:00 UTC (~09:30-10:00 ET open)": dict(delay_hours=14),
         "funding from 00:00 UTC but basis marked at 14:00 UTC (perp entered Sunday night, hedge on Monday open)": dict(basis_delay_hours=14),
         "Mon 14:00 UTC, no basis term": dict(delay_hours=14, basis=False),
         "Mon 14:00 UTC, perp half-spread 5 bps": dict(delay_hours=14, hs_perp=5.0), "Mon 14:00 UTC, perp half-spread 10 bps": dict(delay_hours=14, hs_perp=10.0),
         "Mon 00:00 UTC, perp half-spread 5 bps": dict(hs_perp=5.0), "Mon 14:00 UTC, growth fees": dict(delay_hours=14, fee="hip3_growth")}
res["exec"] = {}
for name, kw in tests.items():
    s = summ(run(**kw)); res["exec"][name] = s
    print(f"  {name:100s} weeks={s['weeks']} net={s['net_apr']*100:5.1f}% t={s['t']:5.2f} gross={s['gross_apr']*100:4.1f}% basis={s['basis_apr']*100:4.1f}%")

# session of the premium samples used for the basis term at Monday 00:00 UTC
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
ts0 = pd.Timestamp("2026-06-01", tz="UTC"); print("\nMonday 00:00 UTC in ET:", ts0.tz_convert(ET), "(winter:", pd.Timestamp("2026-01-05", tz="UTC").tz_convert(ET), ") -> weekend internal pricing session, stock market closed")

# telescoping gap: premium change from Sunday 23:00 UTC to Monday 00:00 UTC for held-over positions is never booked
p = e4.hourly_matrix(fu, "xyz", "premium")
gapc = []
for i in range(1, len(base)):
    prev, cur = base.iloc[i - 1], base.iloc[i]
    keep = set(prev["sel"].split(",")) & set(cur["sel"].split(","))
    t1 = cur["week"] - pd.Timedelta(hours=1); t2 = cur["week"]
    for c in keep:
        if t1 in p.index and t2 in p.index and not (np.isnan(p.loc[t1, c]) or np.isnan(p.loc[t2, c])):
            gapc.append(p.loc[t1, c] - p.loc[t2, c])
print(f"unbooked 1-hour premium change across week boundaries for held-over positions: n={len(gapc)} mean={np.mean(gapc)*1e4:.2f} bps (positive = would have helped)")
# unpriced final exit
print(f"final week positions never charged an exit: {base.iloc[-1]['n']} positions x {(9+1.5+1.5):.1f} bps ≈ {base.iloc[-1]['n']*12/1e4/ (base.iloc[-1]['n']*1.25)*1e4:.1f} bps of capital once (negligible)")

# concentration: per-coin gross funding share and the top coins' liquidity
cw = pd.DataFrame([{"week": r_["week"], "coin": c, "f": v} for _, r_ in run().iterrows() for c, v in zip(r_["sel"], [None]*len(r_["sel"]))]) if False else None
json.dump(res, open(os.path.join(OUT, "h4_review.json"), "w"), indent=1, default=str)
