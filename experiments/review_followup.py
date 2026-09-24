#!/usr/bin/env python3
"""Follow-up checks for the adversarial review: H7 event composition by asset class and by stamp hour, honest
'extreme persists' (2h delay), xyz funding-formula clamp bound, zero-funding rows, para:AVGO liquidity, H4 gross
funding share from illiquid names."""
import os, sys, json
import numpy as np, pandas as pd
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "review")
res = {}
fu = pd.read_parquet(os.path.join(ROOT, "data", "derived", "funding_hourly.parquet"))
meta = json.load(open(os.path.join(ROOT, "data", "derived", "rwa_meta.json")))
mapped = {t["coin"] for t in meta["tickers"]}
ev = pd.read_csv(os.path.join(OUT, "h7_events.csv"), parse_dates=["t", "day"])
ev["us_equity"] = ev["coin"].isin(mapped)
oi = pd.read_parquet(os.path.join(ROOT, "data", "derived", "oi_snapshots.parquet"))
oi["oi_usd"] = oi["oi"].astype(float) * oi["px"].astype(float)
liq = oi.groupby("coin").agg(oi_usd=("oi_usd", "median"), vlm=("vlm", "median"))

# ---- H7 composition ----
print("== H7 events by asset class (mapped US-listed tickers vs commodities/FX/indices/non-US) ==")
g = ev.groupby("us_equity").agg(n=("p0", "size"), coins=("coin", "nunique"), abs_p0=("abs_p0", "mean"), d6=("d6", "mean"), e6=("e6", "mean"), e3=("e3", "mean"))
print(g.round(1).to_string()); res["by_class"] = g.round(2).to_dict(orient="index")
non = ev[~ev["us_equity"]]
print("non-equity coins with most events:", non.groupby("coin").size().sort_values(ascending=False).head(12).to_dict())
ex = ev[ev["sess"] == "external"]
s18 = ex[ex["et_hour"] == 18]
print(f"\nexternal events stamped 18:00 ET (17:00-18:00 ET): n={len(s18)}; share non-US-equity={1-s18['us_equity'].mean():.2f}; top coins:", s18.groupby("coin").size().sort_values(ascending=False).head(8).to_dict())
print(f"  their honest e6={np.nanmean(s18['e6']):.1f} vs d6={s18['d6'].mean():.1f}")
res["stamp18"] = {"n": int(len(s18)), "share_non_equity": float(1 - s18["us_equity"].mean()), "e6": float(np.nanmean(s18["e6"]))}
# US equities only, honest, by session, day-clustered
def cl(x, g):
    m = ~np.isnan(x); x = x[m]; g = np.asarray(g)[m]; mu = x.mean(); n = len(x)
    d = pd.DataFrame({"x": x - mu, "g": g}).groupby("g")["x"].sum(); G = len(d)
    return mu, mu / (np.sqrt((d.values ** 2).sum()) / n * np.sqrt(G / (G - 1))), n, G
print("\n== US-listed equities only (62 mapped tickers): honest reversion by session ==")
res["us_equity_honest"] = {}
for sess in ["external", "overnight", "weekend", "all"]:
    gg = ev[ev["us_equity"] & ((ev["sess"] == sess) if sess != "all" else True)]
    row = {}
    for h in [1, 3, 6]:
        mu, t, n, G = cl(gg[f"e{h}"].values, gg["day"].values); row[f"e{h}"] = [mu, t, n, G]
    print(f"  {sess:9s} n={row['e6'][2]} days={row['e6'][3]} e1={row['e1'][0]:.1f} (t_cl {row['e1'][1]:.1f}) e3={row['e3'][0]:.1f} e6={row['e6'][0]:.1f} (t_cl {row['e6'][1]:.1f}) | d6 (lead's timing)={gg['d6'].mean():.1f} |p0|={gg['abs_p0'].mean():.1f}")
    res["us_equity_honest"][sess] = row
# liquid US equities (median vol24h >= $5M)
liq_eq = [c for c in mapped if c in liq.index and liq.loc[c, "vlm"] >= 5e6]
gg = ev[ev["coin"].isin(liq_eq) & (ev["sess"] == "external")]
mu, t, n, G = cl(gg["e6"].values, gg["day"].values)
print(f"  US equities with median 24h volume >= $5M ({len(liq_eq)} coins), external: n={n} e6={mu:.1f} (t_cl {t:.1f}); e3={np.nanmean(gg['e3']):.1f}; e1={np.nanmean(gg['e1']):.1f}")
res["liquid_us_equity_external"] = {"coins": len(liq_eq), "n": int(n), "e6": float(mu), "t_cl": float(t), "e3": float(np.nanmean(gg["e3"])), "e1": float(np.nanmean(gg["e1"]))}

# ---- honest 'persists': condition on |p_{t+1}| > thr (known at end of hour t+1), enter TWAP hour t+2, exit hour t+2+h ----
p = fu[fu["dex"] == "xyz"].pivot_table(index="ts", columns="coin", values="premium").sort_index(); p = p.loc[:, p.count() > 24 * 60]
H1 = pd.Timedelta(hours=1)
rows = []
for c in p.columns:
    s = p[c].dropna(); thr = s.abs().rolling(720, min_periods=480).quantile(0.95).shift(1)
    e = s[s.abs() > thr]; last = None
    for t, v in e.items():
        if last is not None and (t - last) < pd.Timedelta(hours=24): continue
        last = t
        p1 = s.get(t + H1, np.nan); th1 = thr.get(t + H1, np.nan)
        if np.isnan(p1) or np.isnan(th1) or abs(p1) <= th1 or np.sign(p1) != np.sign(v): continue
        p2 = s.get(t + 2 * H1, np.nan)
        r = {"coin": c, "t": t, "day": t.floor("D"), "p1": p1 * 1e4, "p2": p2 * 1e4}
        for h in [1, 3, 6]:
            r[f"f{h}"] = -np.sign(v) * (s.get(t + (2 + h) * H1, np.nan) - p2) * 1e4
        rows.append(r)
pe = pd.DataFrame(rows)
print(f"\n== honest 'extreme persists at t+1' (decide at end of hour t+1, enter TWAP hour t+2): n={len(pe)} mean|p1|={pe['p1'].abs().mean():.1f} mean|p2|={pe['p2'].abs().mean():.1f}")
res["honest_persists"] = {}
for h in [1, 3, 6]:
    mu, t, n, G = cl(pe[f"f{h}"].values, pe["day"].values); res["honest_persists"][f"f{h}"] = [mu, t, n]
    print(f"  f{h}: n={n} mean={mu:.1f} bps t_cl={t:.1f}  -> net at 9 bps fee, 1/5 bps half-spread: {mu-2*(9+1+0.5):.1f} / {mu-2*(9+5+0.5):.1f}; growth 0.9 fee: {mu-2*(0.9+1+0.5):.1f} / {mu-2*(0.9+5+0.5):.1f}")

# ---- xyz formula clamp bound ----
x = fu[fu["dex"] == "xyz"].copy(); x["month"] = x["ts"].dt.strftime("%Y-%m")
print("\n== xyz tie share by month for F = 0.5*(p + clamp(1e-4 - p, -L, L))/8 ==")
tab = {}
for L in [0.0005, 0.0003, 0.00025]:
    pred = 0.5 * (x["premium"] + np.clip(0.0001 - x["premium"], -L, L)) / 8
    x[f"tie_{L}"] = (x["funding"] - pred).abs() < 1e-8
    tab[f"L={L}"] = x.groupby("month")[f"tie_{L}"].mean().round(3)
# mult 1 with L=5e-4 and 3e-4 for the early months
for L in [0.0005, 0.0003]:
    pred = 1.0 * (x["premium"] + np.clip(0.0001 - x["premium"], -L, L)) / 8
    x[f"tie1_{L}"] = (x["funding"] - pred).abs() < 1e-8
    tab[f"mult1,L={L}"] = x.groupby("month")[f"tie1_{L}"].mean().round(3)
print(pd.DataFrame(tab).to_string()); res["xyz_tie"] = {k: v.to_dict() for k, v in tab.items()}
best = x[["tie_0.0003", "tie1_0.0005", "tie1_0.0003", "tie_0.0005"]].any(axis=1)
print(f"rows matched by any of these variants: {best.mean():.4f}; unmatched rows: {int((~best).sum())}")
um = x[~best]
print("unmatched by month:", um.groupby("month").size().to_dict())
print("unmatched: funding==0 share", float((um["funding"] == 0).mean()), "; |premium| median", float(um["premium"].abs().median()))
z = x[x["funding"] == 0]
print(f"\nfunding exactly 0 rows: {len(z)}; by coin (top):", z.groupby("coin").size().sort_values(ascending=False).head(6).to_dict())
print("  zero-funding rows by month:", z.groupby("month").size().to_dict())
print("  zero-funding rows: share on weekend (UTC Sat/Sun):", float(z["ts"].dt.dayofweek.isin([5, 6]).mean()), "; mean |premium| bps", float(z["premium"].abs().mean() * 1e4))

# ---- para:AVGO liquidity ----
for c in ["para:AVGO", "xyz:AVGO", "para:AAOI", "para:UNITREE"]:
    if c in liq.index: print(f"{c}: median OI ${liq.loc[c,'oi_usd']/1e6:.2f}M, median 24h volume ${liq.loc[c,'vlm']/1e6:.2f}M")
    else: print(f"{c}: not in OI snapshot file")

# ---- H4: gross funding share from illiquid names ----
import e4_funding_carry as e4
base = e4.h4_equity_harvest(fu, meta, 5, "hip3_standard")
m = e4.hourly_matrix(fu, "xyz")
cw = []
for _, r in base.iterrows():
    win = m[(m.index >= r["week"]) & (m.index < r["week"] + pd.Timedelta(days=7))]
    for c in r["sel"].split(","):
        cw.append({"week": r["week"], "coin": c, "f": float(win[c].sum()), "vlm": float(liq.loc[c, "vlm"]) if c in liq.index else np.nan, "oi": float(liq.loc[c, "oi_usd"]) if c in liq.index else np.nan})
cw = pd.DataFrame(cw); tot = cw["f"].sum()
print(f"\n== H4 K=5 standard: {len(cw)} coin-weeks, {cw['coin'].nunique()} distinct coins ==")
for thr_ in [1e6, 5e6, 20e6]:
    sub = cw[cw["vlm"] < thr_]; print(f"  coin-weeks in names with median 24h volume < ${thr_/1e6:.0f}M: {len(sub)} ({len(sub)/len(cw):.0%}), share of gross funding {sub['f'].sum()/tot:.0%}")
print("  coin-weeks with no OI snapshot (delisted/renamed?):", int(cw["vlm"].isna().sum()))
print("  median OI of selected names: $%.1fM; 25th pct $%.1fM" % (cw["oi"].median() / 1e6, cw["oi"].quantile(.25) / 1e6))
sel_counts = cw.groupby("coin").agg(weeks=("f", "size"), fund=("f", "sum"), vlm=("vlm", "first"), oi=("oi", "first")).sort_values("fund", ascending=False)
sel_counts["fund_share"] = (sel_counts["fund"] / tot).round(3); sel_counts["vlm_M"] = (sel_counts["vlm"] / 1e6).round(2); sel_counts["oi_M"] = (sel_counts["oi"] / 1e6).round(1)
print(sel_counts[["weeks", "fund_share", "vlm_M", "oi_M"]].head(15).to_string())
res["h4_liquidity"] = sel_counts[["weeks", "fund_share", "vlm_M", "oi_M"]].head(15).to_dict(orient="index")
# capacity: 5 positions, each <= 10% of daily volume of the name
cap = cw.groupby("week").apply(lambda d: (0.1 * d["vlm"]).min()).describe()
print("capacity per position if each position <= 10%% of the least liquid selected name's daily volume: median $%.0fk, 25th pct $%.0fk" % (cap["50%"] / 1e3, cap["25%"] / 1e3))
res["h4_capacity_10pct_vol"] = {"median": float(cap["50%"]), "q25": float(cap["25%"])}
json.dump(res, open(os.path.join(OUT, "followup_review.json"), "w"), indent=1, default=str)
