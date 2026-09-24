#!/usr/bin/env python3
"""Adversarial review: data-semantics and cost-realism checks.
(1) does the stored xyz `premium` tie to `fundingRate` via the documented formula (i.e. is it the hourly average premium)?
(2) 5-minute tape density by month and duplicate timestamps; (3) OI/volume-based liquidity of xyz names incl. H4's
profit centres; (4) H2 gold differential: flips in the sign-following rule vs an always-on position; (5) H14 para:AVGO
outliers and hyna end-of-data artefact; (6) main-dex 8h->1h cadence handling."""
import os, json
import numpy as np, pandas as pd
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "results", "review"); os.makedirs(OUT, exist_ok=True)
res = {}
fu = pd.read_parquet(os.path.join(ROOT, "data", "derived", "funding_hourly.parquet"))

# ---------- (1) premium/funding tie for xyz ----------
x = fu[fu["dex"] == "xyz"].copy()
x["month"] = x["ts"].dt.to_period("M").astype(str)
def tie(df, mult, ir, lo=-0.0005, hi=0.0005):
    pred = mult * (df["premium"] + np.clip(ir - df["premium"], lo, hi)) / 8
    return ((df["funding"] - pred).abs() < 1e-8)
print("== xyz: share of rows where funding == mult*(p + clamp(ir - p, -5e-4, 5e-4))/8 exactly, by month ==")
tab = {}
for mult, ir in [(1.0, 0.0001), (0.5, 0.0001), (1.0, 0.00005), (0.5, 0.00005), (0.5, 0.0)]:
    x[f"tie_{mult}_{ir}"] = tie(x, mult, ir)
    tab[f"mult={mult},ir={ir}"] = x.groupby("month")[f"tie_{mult}_{ir}"].mean().round(3).to_dict()
print(pd.DataFrame(tab).to_string())
res["xyz_formula_tie_by_month"] = tab
# try to infer the effective formula: for rows with small |p| (unclamped region) funding*8/mult should equal ir
small = x[x["premium"].abs() < 0.00005]
print("\nrows with |p|<5e-5: implied 8*F (should equal ir if mult=1, or 0.5*ir if mult=0.5):")
print((small["funding"] * 8).describe(percentiles=[.1, .5, .9]).round(9).to_dict())
res["implied_8F_small_p"] = (small["funding"] * 8).describe(percentiles=[.1, .5, .9]).round(9).to_dict()
# residual structure: F*8/0.5 - p vs p (should be clamp(ir-p)); show the mean residual by p bucket for recent months
x["c"] = x["funding"] * 8 / 0.5 - x["premium"]
rec = x[x["ts"] >= "2026-06-01"]
b = pd.cut(rec["premium"], [-1, -0.002, -0.001, -0.0005, -0.0002, 0, 0.0002, 0.0005, 0.001, 0.002, 1])
print("\nrecent (>=2026-06) xyz: mean of 8F/0.5 - p by premium bucket (clamp(ir-p) would be +5e-4 for p<-4e-4 and -5e-4 for p>6e-4):")
print(rec.groupby(b, observed=True)["c"].agg(["mean", "std", "size"]).round(7).to_string())
# how many xyz rows hit the 4%/h cap or are exactly zero
print("\nxyz funding exactly 0:", int((x["funding"] == 0).sum()), " premium exactly 0:", int((x["premium"] == 0).sum()), " premium NaN:", int(x["premium"].isna().sum()))

# ---------- (2) tape density ----------
pb = pd.read_parquet(os.path.join(ROOT, "data", "derived", "perp_basis_5m.parquet"))
hl = pb[(pb["venue"] == "hyperliquid") & (pb["asset"] == "gold")]
dens = hl.groupby(hl["ts"].dt.to_period("M")).agg(rows=("ts", "size"), days=("ts", lambda s: s.dt.date.nunique()))
dens["rows_per_day"] = (dens["rows"] / dens["days"]).round(0)
print("\n== 5-min tape: HL gold rows per month (288/day = full) ==")
print(dens.to_string())
res["tape_density"] = {str(k): v for k, v in dens.astype(float).to_dict(orient="index").items()}
dup = pb.duplicated(subset=["ts", "venue", "asset"]).sum()
print("duplicate (ts, venue, asset) rows:", int(dup))
gap = hl["ts"].sort_values().diff().dt.total_seconds().div(60)
print("HL gold snapshot gap minutes: median %.1f, q90 %.1f, q99 %.1f, share>7min %.3f" % (gap.median(), gap.quantile(.9), gap.quantile(.99), (gap > 7).mean()))
res["tape_gap_min"] = {"median": float(gap.median()), "q90": float(gap.quantile(.9)), "q99": float(gap.quantile(.99)), "share_gt7": float((gap > 7).mean())}
# shared ts across venues within a snapshot?
same = pb[pb["venue"].isin(["hyperliquid", "binance", "okx"])].groupby(["ts", "asset"])["venue"].nunique()
print("share of snapshot ts with all 3 live venues:", round(float((same == 3).mean()), 3), " with >=2:", round(float((same >= 2).mean()), 3))

# ---------- (3) liquidity from OI snapshots ----------
oi = pd.read_parquet(os.path.join(ROOT, "data", "derived", "oi_snapshots.parquet"))
oi = oi[oi["coin"].str.startswith("xyz:")].copy()
oi["oi_usd"] = oi["oi"].astype(float) * oi["px"].astype(float)
liq = oi.groupby("coin").agg(n=("t", "size"), oi_usd_med=("oi_usd", "median"), vlm_med=("vlm", "median"), first=("ts", "min"), last=("ts", "max"))
liq = liq.sort_values("oi_usd_med", ascending=False)
print("\n== OI snapshot window:", oi["ts"].min(), "->", oi["ts"].max(), "snapshots per coin (median):", int(liq["n"].median()))
print("median 24h notional volume across xyz coins: $%.1fM; median OI $%.1fM" % (liq["vlm_med"].median() / 1e6, liq["oi_usd_med"].median() / 1e6))
for c in ["xyz:HIMS", "xyz:GME", "xyz:USAR", "xyz:BIRD", "xyz:MU", "xyz:SNDK", "xyz:RIVN", "xyz:INTC", "xyz:BX", "xyz:GOOGL", "xyz:NVDA", "xyz:GOLD", "xyz:TSLA"]:
    if c in liq.index:
        r = liq.loc[c]; print(f"  {c:10s} rank={list(liq.index).index(c)+1:3d}/{len(liq)} OI ${r['oi_usd_med']/1e6:7.1f}M  vol24h ${r['vlm_med']/1e6:7.1f}M")
res["liquidity"] = liq[["oi_usd_med", "vlm_med"]].round(0).to_dict(orient="index")
print("bottom-30 by OI: median vol24h $%.2fM, median OI $%.2fM" % (liq.tail(30)["vlm_med"].median() / 1e6, liq.tail(30)["oi_usd_med"].median() / 1e6))
print("top-15 by OI: median vol24h $%.1fM" % (liq.head(15)["vlm_med"].median() / 1e6))
print("coins with median vol24h < $1M:", int((liq["vlm_med"] < 1e6).sum()), "of", len(liq), "; < $5M:", int((liq["vlm_med"] < 5e6).sum()))

# ---------- (4) H2 gold: flips and always-on ----------
g = pb[pb["asset"] == "gold"]
w = {}
for v, tag in [("hyperliquid", "hl"), ("binance", "bn")]:
    s = g[g["venue"] == v].set_index("ts")["funding_rate"]; s = s[~s.index.duplicated()]; w[tag] = s
w = pd.DataFrame(w).sort_index()
f = pd.DataFrame({"hl": w["hl"] * 24 * 365, "bn": w["bn"] / 8 * 24 * 365})
daily = f.resample("1D").mean().dropna()
d = daily["hl"] - daily["bn"]
print("\n== H2 gold HL-Binance differential ==")
print(f"days={len(d)} mean={d.mean()*100:.2f}% sd={d.std()*100:.2f}% autocorr1={d.autocorr(1):.2f} share>0={(d>0).mean():.2f}")
print("monthly mean differential APR:", (d.resample("MS").mean() * 100).round(1).to_dict())
res["h2_gold_monthly"] = {str(k.date()): float(v) for k, v in (d.resample("MS").mean() * 100).items()}
# HAC t of the mean
import statsmodels.api as sm
m = sm.OLS(d.values, np.ones(len(d))).fit(cov_type="HAC", cov_kwds={"maxlags": 7})
print(f"HAC(7) t-stat of mean differential: {m.tvalues[0]:.2f}")
rt = 2 * (9.0 + 0.5 + 0.12) + 2 * (5.0 + 0.5 + 0.02)
sig = d.rolling(7).mean().shift(1); pos = 0; flips = 0; wk = []
for k in range(7, len(d) - 7, 7):
    sgn = np.sign(sig.iloc[k])
    if sgn == 0 or np.isnan(sgn): continue
    cost = rt if sgn != pos else 0.0; flips += int(cost > 0); pos = sgn
    wk.append(sgn * d.iloc[k:k + 7].mean() * 1e4 * 7 / 365 - cost)
wk = np.array(wk)
print(f"sign-following weekly: n={len(wk)} flips={flips} net mean={wk.mean():.1f} bps/wk; gross mean={np.mean(wk + 0) + flips*rt/len(wk):.1f}")
always = d.mean() * 1e4 * len(d) / 365 - rt
print(f"always-on short-HL/long-Binance over {len(d)} days: gross {d.mean()*1e4*len(d)/365:.0f} bps, net of one round trip {always:.0f} bps = {always/len(d)*365/100:.2f}% APR on notional")
res["h2_gold"] = {"days": int(len(d)), "mean_apr": float(d.mean()), "hac_t": float(m.tvalues[0]), "flips": int(flips), "weekly_net_bps": float(wk.mean()),
                  "always_on_net_apr_on_notional": float(always / len(d) * 365 / 1e4)}

# ---------- (5) H14 para:AVGO outliers; hyna end date ----------
a = fu[(fu["coin"] == "para:AVGO")].set_index("ts")["funding"]; b2 = fu[(fu["coin"] == "xyz:AVGO")].set_index("ts")["funding"]
j = pd.concat([a.rename("para"), b2.rename("xyz")], axis=1).dropna()
dd = (j["para"] - j["xyz"])
print("\n== H14 para:AVGO - xyz:AVGO hourly differential ==")
print(f"hours={len(dd)} mean APR={dd.mean()*8760*100:.1f}% ; APR excluding top 1% |diff| hours: {dd[dd.abs() < dd.abs().quantile(.99)].mean()*8760*100:.1f}% ; excluding top 24 hours: {dd.drop(dd.abs().sort_values().index[-24:]).mean()*8760*100:.1f}%")
print("para:AVGO funding APR by month:", (j["para"].resample("MS").mean() * 8760 * 100).round(1).to_dict())
print("xyz:AVGO funding APR by month:", (j["xyz"].resample("MS").mean() * 8760 * 100).round(1).to_dict())
top = dd.abs().sort_values(ascending=False).head(5)
print("largest |diff| hours (APR-equivalent):", {str(k): round(float(dd[k] * 8760 * 100), 1) for k in top.index})
res["h14_avgo"] = {"mean_apr": float(dd.mean() * 8760), "ex_top1pct_apr": float(dd[dd.abs() < dd.abs().quantile(.99)].mean() * 8760)}
hy = fu[fu["dex"] == "hyna"].groupby("coin")["ts"].max()
print("hyna data ends:", hy.min(), "->", hy.max(), "(main continues to", fu[fu['dex']=='main']['ts'].max(), ")")
hb = fu[fu["coin"] == "hyna:BTC"].set_index("ts")["funding"]; mb = fu[fu["coin"] == "BTC"].set_index("ts")["funding"]
sep = pd.concat([hb.rename("h"), mb.rename("m")], axis=1).dropna(); sep = sep[sep.index >= "2026-09-01"]
print(f"hyna:BTC|BTC 'September' differential is based on {len(sep)} hours")

# ---------- (6) main dex cadence ----------
btc = fu[fu["coin"] == "BTC"].sort_values("t")
dt = btc["t"].diff()
print("\n== main BTC settlement cadence: counts of diff (s) ==", dt.value_counts().head(5).to_dict())
era8 = btc[dt == 28800]; era1 = btc[dt == 3600]
print(f"8h-era rows={len(era8)} mean rate/settlement={era8['funding'].mean():.3e} (x3 per day => APR {era8['funding'].mean()*3*365*100:.1f}%); 1h-era rows={len(era1)} mean rate={era1['funding'].mean():.3e} (APR {era1['funding'].mean()*8760*100:.1f}%)")
print("8h-era rows share at 1e-4 (=0.01% per 8h floor):", float((era8["funding"].round(9) == 1e-4).mean()), "; 1h-era share at 1.25e-5:", float((era1["funding"].round(9) == 1.25e-5).mean()))
json.dump(res, open(os.path.join(OUT, "data_review.json"), "w"), indent=1, default=str)
