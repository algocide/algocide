#!/usr/bin/env python3
"""Adversarial review of H7 (premium extremes mean-revert). Independent re-implementation of the event study with
(a) honest timing (direction decided from p_t, entry TWAP over hour t+1, exit TWAP over hour t+1+h),
(b) day-clustered inference, (c) session-boundary (oracle regime switch) diagnostics, (d) net-of-cost tables.
Writes results/review/h7_review.json and prints a summary."""
import os, sys, json
import numpy as np, pandas as pd
from zoneinfo import ZoneInfo
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "results", "review"); os.makedirs(OUT, exist_ok=True)
ET = ZoneInfo("America/New_York")
rng = np.random.default_rng(0)

fu = pd.read_parquet(os.path.join(ROOT, "data", "derived", "funding_hourly.parquet"))
p = fu[fu["dex"] == "xyz"].pivot_table(index="ts", columns="coin", values="premium").sort_index()
p = p.loc[:, p.count() > 24 * 60]
H1 = pd.Timedelta(hours=1)


def sess_of(t):
    te = t.tz_convert(ET)
    wk = (te.dayofweek == 4 and te.hour >= 20) or te.dayofweek == 5 or (te.dayofweek == 6 and te.hour < 20)
    return "weekend" if wk else ("overnight" if (te.hour >= 20 or te.hour < 4) else "external")


rows = []
for c in p.columns:
    s = p[c].dropna()
    thr = s.abs().rolling(24 * 30, min_periods=24 * 20).quantile(0.95).shift(1)
    ev = s[s.abs() > thr]; last = None
    for t, v in ev.items():
        if last is not None and (t - last) < pd.Timedelta(hours=24): continue
        last = t
        te = t.tz_convert(ET)
        r = {"coin": c, "t": t, "p0": v, "sess": sess_of(t), "et_hour": te.hour, "et_dow": te.dayofweek, "thr": thr[t],
             "day": t.floor("D")}
        for h in [1, 6, 24]:
            r[f"d{h}"] = (s.get(t + h * H1, np.nan) - v) * -np.sign(v)
        p1 = s.get(t + H1, np.nan); r["p1"] = p1
        r["sess_t1"] = sess_of(t + H1)
        r["sign_flip_t1"] = (np.sign(p1) != np.sign(v)) if not np.isnan(p1) else np.nan
        thr1 = thr.get(t + H1, np.nan)
        r["below_thr_t1"] = (abs(p1) <= thr1) if not (np.isnan(p1) or np.isnan(thr1)) else np.nan
        for h in [1, 3, 6, 24]:
            r[f"e{h}"] = -np.sign(v) * (s.get(t + (1 + h) * H1, np.nan) - p1)  # honest: direction from p_t, entry TWAP hour t+1
        rows.append(r)
ev = pd.DataFrame(rows)
for col in [c for c in ev.columns if c[0] in "de" and c[1:].isdigit()]:
    ev[col] = ev[col] * 1e4
ev["abs_p0"] = ev["p0"].abs() * 1e4; ev["abs_p1"] = ev["p1"].abs() * 1e4
ev.to_csv(os.path.join(OUT, "h7_events.csv"), index=False)


def cluster_stats(x, g, n_boot=4000):
    """mean, iid t, cluster-robust t (clusters g), day-mean block bootstrap CI (blocks of 5 days over the day series)."""
    m = ~np.isnan(x); x = x[m]; g = np.asarray(g)[m]
    n = len(x); mu = x.mean()
    se_iid = x.std(ddof=1) / np.sqrt(n)
    df = pd.DataFrame({"x": x - mu, "g": g}).groupby("g")["x"].sum()
    G = len(df)
    se_cl = np.sqrt((df.values ** 2).sum()) / n * np.sqrt(G / (G - 1))
    # one observation per day (equal weight per day), circular block bootstrap over days, block=5
    dm = pd.DataFrame({"x": x, "g": g}).groupby("g")["x"].mean().sort_index().values
    nd = len(dm); block = 5; nb = int(np.ceil(nd / block))
    starts = rng.integers(0, nd, size=(n_boot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :nd] % nd
    vals = dm[idx].mean(axis=1)
    return {"n": int(n), "n_clusters": int(G), "mean": float(mu), "t_iid": float(mu / se_iid), "t_cluster": float(mu / se_cl),
            "day_mean": float(dm.mean()), "day_ci95": [float(np.quantile(vals, .025)), float(np.quantile(vals, .975))]}


res = {"n_events": int(len(ev)), "n_coins": int(ev["coin"].nunique()), "n_days": int(ev["day"].nunique())}
res["events_per_day"] = {"mean": float(ev.groupby("day").size().mean()), "max": int(ev.groupby("day").size().max()),
                         "q90": float(ev.groupby("day").size().quantile(.9)),
                         "share_of_events_on_top10_days": float(ev.groupby("day").size().sort_values(ascending=False).head(10).sum() / len(ev))}
print(json.dumps(res, indent=1))

# 1. replicate the lead's numbers and add cluster inference
print("\n== Replication of e4 H7 (entry at t, from p_t) with iid vs day-clustered inference ==")
res["replicate"] = {}
for h in [1, 6, 24]:
    st = cluster_stats(ev[f"d{h}"].values, ev["day"].values); res["replicate"][f"d{h}"] = st
    print(f"d{h}: n={st['n']} days={st['n_clusters']} mean={st['mean']:.1f} bps t_iid={st['t_iid']:.1f} t_cluster(day)={st['t_cluster']:.1f} day-mean={st['day_mean']:.1f} CI_days={st['day_ci95'][0]:.1f},{st['day_ci95'][1]:.1f}")

# 2. honest timing
print("\n== Honest timing: direction from sign(p_t); entry = TWAP over hour t+1; exit = TWAP over hour t+1+h ==")
print(f"share of events whose premium sign flips by t+1: {np.nanmean(ev['sign_flip_t1'].astype(float)):.3f}; share already below threshold at t+1: {np.nanmean(ev['below_thr_t1'].astype(float)):.3f}")
print(f"mean |p_t| {ev['abs_p0'].mean():.1f} bps -> mean |p_t+1| {ev['abs_p1'].mean():.1f} bps; mean signed p_{{t+1}} in direction of p_t: {np.nanmean(np.sign(ev['p0'])*ev['p1'])*1e4:.1f} bps")
res["honest"] = {"share_sign_flip_t1": float(np.nanmean(ev['sign_flip_t1'].astype(float))), "share_below_thr_t1": float(np.nanmean(ev['below_thr_t1'].astype(float))),
                 "mean_abs_p0": float(ev['abs_p0'].mean()), "mean_abs_p1": float(ev['abs_p1'].mean())}
for label, g in [("all", ev)] + [(s_, ev[ev["sess"] == s_]) for s_ in ["external", "overnight", "weekend"]]:
    res["honest"][label] = {}
    for h in [1, 3, 6, 24]:
        st = cluster_stats(g[f"e{h}"].values, g["day"].values); res["honest"][label][f"e{h}"] = st
        print(f"{label:9s} e{h:<2d}: n={st['n']:5d} days={st['n_clusters']:4d} mean={st['mean']:6.1f} t_iid={st['t_iid']:5.1f} t_cl={st['t_cluster']:5.1f} dayCI=[{st['day_ci95'][0]:.1f},{st['day_ci95'][1]:.1f}]")

# honest but conditioning only on information at t: |p_t| > 2x threshold (big extremes)
big = ev[ev["abs_p0"] > 2 * ev["thr"] * 1e4]
res["honest"]["big_extremes_gt_2thr"] = {}
print(f"\n-- honest, |p_t| > 2x threshold (known at t): n={len(big)}")
for h in [1, 3, 6]:
    st = cluster_stats(big[f"e{h}"].values, big["day"].values); res["honest"]["big_extremes_gt_2thr"][f"e{h}"] = st
    print(f"  e{h}: n={st['n']} mean={st['mean']:.1f} t_cl={st['t_cluster']:.1f} dayCI=[{st['day_ci95'][0]:.1f},{st['day_ci95'][1]:.1f}]")

# 3. net of costs table (honest e6, by session), half-spread hs, fee f, impact 0.5/side
print("\n== Net of round-trip costs (honest e6 / e3), bps: net = reversion - 2*(fee + half_spread + 0.5) ==")
res["net"] = {}
for label in ["all", "external", "overnight", "weekend"]:
    g = ev if label == "all" else ev[ev["sess"] == label]
    for h in [3, 6]:
        m = float(np.nanmean(g[f"e{h}"]))
        line = []
        for fee in [9.0, 0.9]:
            for hs in [1, 5, 10, 20]:
                net = m - 2 * (fee + hs + 0.5); res["net"][f"{label}|e{h}|fee{fee}|hs{hs}"] = net
                line.append(f"fee{fee}/hs{hs}:{net:6.1f}")
        print(f"{label:9s} e{h} mean={m:5.1f} | " + " ".join(line))

# 4. session-boundary diagnostics: stamp hour in ET. Stamp t covers premium averaged over (t-1h, t].
print("\n== Session boundary diagnostics (stamp hour, ET) ==")
ex = ev[ev["sess"] == "external"]
by_hr = ex.groupby("et_hour").agg(n=("p0", "size"), abs_p0=("abs_p0", "mean"), d1=("d1", "mean"), d6=("d6", "mean"), e1=("e1", "mean"), e6=("e6", "mean")).round(1)
print(by_hr.to_string())
res["external_by_stamp_hour"] = by_hr.to_dict(orient="index")
first = ex[ex["et_hour"] == 4]; rest = ex[ex["et_hour"] != 4]
print(f"external events stamped 04:00 ET (premium averaged over 03:00-04:00 ET = LAST internal hour; reversion d1 spans the 04:00 oracle switch): n={len(first)} share={len(first)/len(ex):.3f} d1={first['d1'].mean():.1f} d6={first['d6'].mean():.1f} vs other external d1={rest['d1'].mean():.1f} d6={rest['d6'].mean():.1f}")
h5 = ex[ex["et_hour"] == 5]
print(f"external events stamped 05:00 ET (first external hour 04:00-05:00): n={len(h5)} share={len(h5)/len(ex):.3f} d1={h5['d1'].mean():.1f} d6={h5['d6'].mean():.1f}")
open_ = ex[ex["et_hour"].isin([9, 10])]
print(f"external events stamped 09:00/10:00 ET (around the 09:30 open): n={len(open_)} share={len(open_)/len(ex):.3f} d1={open_['d1'].mean():.1f} d6={open_['d6'].mean():.1f}")
trans = ex[ex["et_hour"].isin([4, 5])]; away = ex[~ex["et_hour"].isin([4, 5, 9, 10])]
res["external_transition"] = {"n_04_05": int(len(trans)), "share_04_05": float(len(trans) / len(ex)), "d6_transition": float(trans["d6"].mean()), "e6_transition": float(np.nanmean(trans["e6"])),
                              "n_away": int(len(away)), "d6_away": float(away["d6"].mean()), "e6_away": float(np.nanmean(away["e6"]))}
print(f"external away from 04/05/09/10 stamps: n={len(away)} d1={away['d1'].mean():.1f} d6={away['d6'].mean():.1f} e6={np.nanmean(away['e6']):.1f}")
# weekend -> Sunday 20:00 ET transition
ov = ev[ev["sess"] == "overnight"]
sun = ov[(ov["et_dow"] == 6)]
print(f"overnight events stamped Sunday >=20:00 ET (weekend reopen hour block): n={len(sun)} share of overnight={len(sun)/len(ov):.3f} d1={sun['d1'].mean():.1f} d6={sun['d6'].mean():.1f}; other overnight d1={ov[ov['et_dow']!=6]['d1'].mean():.1f} d6={ov[ov['et_dow']!=6]['d6'].mean():.1f}")
res["sunday_reopen"] = {"n": int(len(sun)), "share_of_overnight": float(len(sun) / len(ov)), "d1": float(sun["d1"].mean()), "d6": float(sun["d6"].mean())}
mon = ex[(ex["et_dow"] == 0)]
print(f"Monday external events: n={len(mon)} d6={mon['d6'].mean():.1f}; Tue-Fri external d6={ex[ex['et_dow']!=0]['d6'].mean():.1f}")

# 5. timestamp convention check: mean |p| and mean |dp| by ET stamp hour on Tue-Fri (all coins, all hours)
idx_et = p.index.tz_convert(ET); dow = np.array(idx_et.dayofweek); hr = np.array(idx_et.hour)
ap = p.abs().stack(); dp = p.diff().abs().stack()
lab = pd.Series(hr, index=p.index); dw = pd.Series(dow, index=p.index)
mask_days = dw.isin([1, 2, 3, 4])
tab = pd.DataFrame({"abs_p_bps": ap.groupby(level=0).mean()[mask_days].groupby(lab[mask_days]).mean() * 1e4,
                    "abs_dp_bps": dp.groupby(level=0).mean()[mask_days].groupby(lab[mask_days]).mean() * 1e4}).round(2)
print("\n== Tue-Fri mean |premium| and mean |hourly change| by ET stamp hour (all xyz coins) ==")
print(tab.T.to_string())
res["tue_fri_by_stamp_hour"] = tab.to_dict(orient="index")
# Sunday
mask_sun = dw == 6
tabs = pd.DataFrame({"abs_p_bps": ap.groupby(level=0).mean()[mask_sun].groupby(lab[mask_sun]).mean() * 1e4,
                     "abs_dp_bps": dp.groupby(level=0).mean()[mask_sun].groupby(lab[mask_sun]).mean() * 1e4}).round(2)
print("Sunday:"); print(tabs.T.to_string())

# 6. autocorrelation of premium (is reversion just stationarity?) and unconditional |dp|
ac = p.apply(lambda s: s.dropna().autocorr(1)).describe()
print("\nAR(1) of hourly premium across coins:", ac.round(3).to_dict())
res["premium_ar1_across_coins"] = {k: float(v) for k, v in ac.items()}
json.dump(res, open(os.path.join(OUT, "h7_review.json"), "w"), indent=1, default=str)
