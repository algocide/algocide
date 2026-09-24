#!/usr/bin/env python3
"""E4: funding-based hypotheses — H6 persistence, H4 equity-perp funding harvest, H5 native carry,
H14 cross-dex same-underlying differential, H7/H11 premium extremes and sessions.
Pre-registered in docs/hypotheses_and_preregistration.md. Outputs results/e4/*
"""
import os, sys, json
import numpy as np, pandas as pd
from zoneinfo import ZoneInfo
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from hlr.costs import FEE_REGIMES, RISK_FREE
from hlr.stats import hac_regression, circular_block_bootstrap_ci, describe_pnl

OUT = os.path.join(ROOT, "results", "e4"); os.makedirs(OUT, exist_ok=True)
DEV_END = pd.Timestamp("2026-06-01", tz="UTC")
H_PER_YEAR = 24 * 365
ET = ZoneInfo("America/New_York")


def load():
    fu = pd.read_parquet(os.path.join(ROOT, "data", "derived", "funding_hourly.parquet"))
    st = pd.read_parquet(os.path.join(ROOT, "data", "derived", "stock_daily.parquet"))
    meta = json.load(open(os.path.join(ROOT, "data", "derived", "rwa_meta.json")))
    return fu, st, meta


def hourly_matrix(fu, dex, field="funding"):
    x = fu[fu["dex"] == dex].pivot_table(index="ts", columns="coin", values=field)
    return x.sort_index()


def per_hour_rate(fu):
    """Funding rate per hour: before 2023-06-08 BTC/ETH settled every 8h (rate per 8h)."""
    fu = fu.copy()
    dtv = fu.groupby(["dex", "coin"])["t"].diff().fillna(3600)
    interval_h = (dtv / 3600).clip(lower=1).round()
    fu["rate_ph"] = fu["funding"] / interval_h.where(interval_h.isin([1, 8]), 1)
    fu["interval_h"] = interval_h
    return fu


def h6_persistence(fu):
    out = {}
    for dex in ["main", "xyz", "hyna", "para", "mkts"]:
        m = hourly_matrix(fu, dex)
        if m.shape[1] == 0: continue
        wk = m.resample("W-MON").mean(); cnt = m.resample("W-MON").count()
        wk = wk.where(cnt >= 120)
        a = wk.shift(1).stack(); b = wk.stack()
        j = pd.concat([a.rename("prev"), b.rename("cur")], axis=1).dropna()
        j = j[(j["prev"].abs() < 0.01) & (j["cur"].abs() < 0.01)]
        # cross-sectional rank persistence (only meaningful with many coins)
        rk = []
        for i in range(1, len(wk)):
            r0 = wk.iloc[i - 1].dropna(); r1 = wk.iloc[i].dropna(); c = r0.index.intersection(r1.index)
            if len(c) >= 8:
                rk.append(float(pd.Series(r0[c]).rank().corr(pd.Series(r1[c]).rank(), method="spearman")))
        out[dex] = {"coins": int(m.shape[1]), "weekly_pairs": int(len(j)), "corr_week_to_next": float(j["prev"].corr(j["cur"])) if len(j) > 10 else np.nan,
                    "sign_persistence": float((np.sign(j["prev"]) == np.sign(j["cur"])).mean()) if len(j) else np.nan,
                    "xsec_rank_corr_mean": float(np.mean(rk)) if rk else np.nan, "xsec_rank_corr_n_weeks": len(rk),
                    "mean_apr_all": float(m.stack().mean() * H_PER_YEAR), "median_apr_all": float(m.stack().median() * H_PER_YEAR)}
    return out


def h4_equity_harvest(fu, meta, K, fee_regime, basis=True, margin=0.25):
    mapped = {t["coin"] for t in meta["tickers"]}
    m = hourly_matrix(fu, "xyz"); p = hourly_matrix(fu, "xyz", "premium")
    m = m[[c for c in m.columns if c in mapped]]; p = p[m.columns]
    fee = FEE_REGIMES[fee_regime].taker * 1e4
    side_cost_perp = fee + 1.0 + 0.5      # taker fee + assumed 1 bp half-spread + 0.5 impact (bps)
    side_cost_stock = 1.0 + 0.5           # 1 bp half-spread + 0.5 impact
    # margin: perp margin per unit notional (10x max leverage; 25% default, 50% conservative for earnings gaps)
    capital_per_pos = 1.0 + margin
    mondays = pd.date_range(pd.Timestamp("2025-12-01", tz="UTC"), m.index.max().normalize(), freq="W-MON")
    weeks = []; held = set()
    for t in mondays:
        hist = m[(m.index < t) & (m.index >= t - pd.Timedelta(days=28))]
        elig = [c for c in m.columns if hist[c].count() >= 27 * 24]
        if len(elig) < max(K or 5, 5): continue
        score = m[(m.index < t) & (m.index >= t - pd.Timedelta(days=7))][elig].mean()
        score = score[score > 0].sort_values(ascending=False)
        sel = list(score.index[:K]) if K else list(score.index)  # K=None -> naive all positive
        if not sel: continue
        win = m[(m.index >= t) & (m.index < t + pd.Timedelta(days=7))]; pw = p[(m.index >= t) & (m.index < t + pd.Timedelta(days=7))]
        if len(win) < 100: continue
        pnl = 0.0; gross_f = 0.0; basis_pnl = 0.0; cost = 0.0; n = len(sel)
        for c in sel:
            f = win[c].dropna(); pc = pw[c].dropna()
            fsum = float(f.sum()); gross_f += fsum
            b = float(pc.iloc[0] - pc.iloc[-1]) if (basis and len(pc) > 1) else 0.0   # short perp gains when premium falls
            basis_pnl += b
            entry = 0.0 if c in held else (side_cost_perp + side_cost_stock) / 1e4
            cost += entry
        # exit costs for positions dropped this week
        exits = len(held - set(sel)) * (side_cost_perp + side_cost_stock) / 1e4
        fin = RISK_FREE * 7 / 365 * n
        pnl = gross_f + basis_pnl - cost - exits - fin
        weeks.append({"week": t, "n": n, "ret": pnl / (n * capital_per_pos), "gross_funding_ret": gross_f / (n * capital_per_pos),
                      "basis_ret": basis_pnl / (n * capital_per_pos), "cost_ret": -(cost + exits) / (n * capital_per_pos), "fin_ret": -fin / (n * capital_per_pos),
                      "turnover": len(set(sel) - held), "sel": ",".join(sel)})
        held = set(sel)
    return pd.DataFrame(weeks)


def summarise_weekly(df, label):
    if len(df) == 0: return {"label": label, "n_weeks": 0}
    r = df["ret"].values
    d = describe_pnl(r, per_year=52)
    _, lo, hi = circular_block_bootstrap_ci(r, block=4, n_boot=2000)
    dev = df[df["week"] < DEV_END]["ret"]; val = df[df["week"] >= DEV_END]["ret"]
    top5 = np.sort(r)[-5:].sum() if len(r) >= 5 else 0.0
    return {"label": label, "n_weeks": int(len(r)), "net_apr": float(d["mean"] * 52), "net_apr_ci95": [float(lo * 52), float(hi * 52)],
            "sharpe_ann": d.get("sharpe_ann"), "gross_funding_apr": float(df["gross_funding_ret"].mean() * 52), "basis_apr": float(df["basis_ret"].mean() * 52),
            "cost_apr": float(df["cost_ret"].mean() * 52), "financing_apr": float(df["fin_ret"].mean() * 52), "max_dd": d["max_dd"], "win_rate": d["win_rate"],
            "dev_apr": float(dev.mean() * 52) if len(dev) else np.nan, "dev_weeks": int(len(dev)), "val_apr": float(val.mean() * 52) if len(val) else np.nan, "val_weeks": int(len(val)),
            "apr_ex_top5_weeks": float((r.sum() - top5) / len(r) * 52), "mean_turnover": float(df["turnover"].mean())}


def h5_native_carry(fu):
    out = {}
    fu = per_hour_rate(fu)
    for coin in ["BTC", "ETH", "HYPE"]:
        x = fu[(fu["dex"] == "main") & (fu["coin"] == coin)].set_index("ts").sort_index()
        f = x["funding"]  # per-settlement rate; sum over a year = fraction of notional paid to shorts
        yearly = f.groupby(f.index.year).agg(["sum", "count"])
        yearly["hours"] = x["interval_h"].groupby(x.index.year).sum()
        yearly["apr"] = yearly["sum"] / yearly["hours"] * H_PER_YEAR
        rt_cost = 2 * (4.5 + 0.5 + 0.5) + 2 * (4.5 + 5.0 + 0.5)  # perp leg + spot leg (spot 5 bps half-spread assumed), bps
        roll30 = x["rate_ph"].rolling(24 * 30).sum()
        res = {"start": str(x.index.min().date()), "end": str(x.index.max().date()), "n_settlements": int(len(x)),
               "share_negative": float((f < 0).mean()), "share_at_floor": float((f.round(9) == 1.25e-5).mean()),
               "worst_30d_funding_bps": float(roll30.min() * 1e4), "best_30d_funding_bps": float(roll30.max() * 1e4),
               "round_trip_cost_bps": rt_cost, "by_year": {}}
        for y, r in yearly.iterrows():
            net = (r["apr"] - rt_cost / 1e4 * (H_PER_YEAR / max(r["hours"], 1))) / 1.25  # annualised, on spot notional + 25% margin, one round trip per year
            res["by_year"][int(y)] = {"gross_funding_apr": float(r["apr"]), "hours": float(r["hours"]), "net_apr_on_capital": float(net)}
        # 30-day rolling persistence: corr of consecutive non-overlapping 30-day sums
        s30 = x["rate_ph"].resample("30D").sum()
        res["corr_30d_to_next"] = float(s30.shift(1).corr(s30)) if len(s30) > 6 else np.nan
        out[coin] = res
    return out


def h14_cross_dex(fu):
    out = {}
    fu = per_hour_rate(fu)
    pairs = []
    coins = fu.groupby(["dex", "coin"]).size().reset_index()[["dex", "coin"]]
    base = {}
    for _, r in coins.iterrows():
        tick = r["coin"].split(":")[-1]
        base.setdefault(tick, []).append((r["dex"], r["coin"]))
    for tick, lst in base.items():
        if len(lst) >= 2:
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    pairs.append((tick, lst[i], lst[j]))
    for tick, (d1, c1), (d2, c2) in pairs:
        a = fu[(fu["dex"] == d1) & (fu["coin"] == c1)].set_index("ts")[["rate_ph", "premium"]]
        b = fu[(fu["dex"] == d2) & (fu["coin"] == c2)].set_index("ts")[["rate_ph", "premium"]]
        j = a.join(b, lsuffix="_1", rsuffix="_2", how="inner").dropna()
        if len(j) < 24 * 30: continue
        d = j["rate_ph_1"] - j["rate_ph_2"]
        daily = d.resample("1D").mean().dropna()
        fee1 = (FEE_REGIMES["native_base"].taker if d1 == "main" else FEE_REGIMES["hip3_standard"].taker) * 1e4
        fee2 = (FEE_REGIMES["native_base"].taker if d2 == "main" else FEE_REGIMES["hip3_standard"].taker) * 1e4
        rt = 2 * (fee1 + 1.0) + 2 * (fee2 + 1.0)  # bps both legs, 1 bp half-spread+impact each side
        # weekly decision: sign from trailing 7 days; position held until the sign flips; round-trip cost charged
        # only on entry/flip (pre-registration said "weekly sign-following"; the first run charged a round trip every
        # week, which is not how the position would be managed -- corrected 2026-09-24, see ledger)
        wk = []; pos = 0
        days = daily.index
        for k in range(7, len(days) - 7, 7):
            sgn = np.sign(daily.iloc[k - 7:k].mean())
            if sgn == 0: continue
            win = j[(j.index >= days[k]) & (j.index < days[k] + pd.Timedelta(days=7))]
            if len(win) < 100: continue
            carry = sgn * (win["rate_ph_1"].sum() - win["rate_ph_2"].sum())
            bas = sgn * ((win["premium_2"].iloc[-1] - win["premium_2"].iloc[0]) - (win["premium_1"].iloc[-1] - win["premium_1"].iloc[0]))
            cost = rt if sgn != pos else 0.0
            pos = sgn
            wk.append({"week": days[k], "carry_bps": carry * 1e4, "basis_bps": bas * 1e4, "net_bps": (carry + bas) * 1e4 - cost, "flip": cost > 0})
        wk = pd.DataFrame(wk)
        res = {"legs": [c1, c2], "n_hours": int(len(j)), "start": str(j.index.min().date()), "end": str(j.index.max().date()),
               "mean_diff_apr": float(d.mean() * H_PER_YEAR), "mean_abs_daily_diff_apr": float(daily.abs().mean() * H_PER_YEAR),
               "daily_autocorr": float(daily.autocorr(1)) if len(daily) > 10 else np.nan, "round_trip_cost_bps": rt,
               "mean_apr_1": float(j["rate_ph_1"].mean() * H_PER_YEAR), "mean_apr_2": float(j["rate_ph_2"].mean() * H_PER_YEAR),
               "premium_diff_sd_bps": float((j["premium_1"] - j["premium_2"]).std() * 1e4)}
        res["monthly_mean_diff_apr"] = {str(k.date()): float(v * H_PER_YEAR) for k, v in d.resample("MS").mean().items()}
        if len(wk):
            res["weekly"] = {"n": int(len(wk)), "net_mean_bps": float(wk["net_bps"].mean()), "net_t": describe_pnl(wk["net_bps"].values)["t_iid"],
                             "carry_mean_bps": float(wk["carry_bps"].mean()), "basis_mean_bps": float(wk["basis_bps"].mean()),
                             "net_apr_on_capital": float(wk["net_bps"].mean() / 1e4 * 52 / 0.5),  # capital = 2 legs x 25% margin
                             "dev_net_mean": float(wk[wk["week"] < DEV_END]["net_bps"].mean()) if (wk["week"] < DEV_END).any() else np.nan,
                             "val_net_mean": float(wk[wk["week"] >= DEV_END]["net_bps"].mean()) if (wk["week"] >= DEV_END).any() else np.nan,
                             "win_rate": float((wk["net_bps"] > 0).mean()), "n_flips": int(wk["flip"].sum())}
        out[f"{c1}|{c2}"] = res
    return out


def h7_h11_premium(fu):
    out = {"h7": {}, "h11": {}}
    p = hourly_matrix(fu, "xyz", "premium")
    p = p.loc[:, p.count() > 24 * 60]
    # H7: extremes above trailing-30d 95th pct of |p|, non-overlapping (first event per 24h per coin)
    rows = []
    for c in p.columns:
        s = p[c].dropna()
        thr = s.abs().rolling(24 * 30, min_periods=24 * 20).quantile(0.95).shift(1)
        ev = s[(s.abs() > thr)]
        last = None
        for t, v in ev.items():
            if last is not None and (t - last) < pd.Timedelta(hours=24): continue
            last = t
            te = t.tz_convert(ET); wk_ = ((te.dayofweek == 4) and te.hour >= 20) or te.dayofweek == 5 or (te.dayofweek == 6 and te.hour < 20)
            sess_ = "weekend_internal" if wk_ else ("overnight" if (te.hour >= 20 or te.hour < 4) else "external")
            r = {"coin": c, "t": t, "p0": v, "session": sess_}
            for h in [1, 6, 24]:
                t2 = t + pd.Timedelta(hours=h)
                r[f"d{h}"] = (s.get(t2, np.nan) - v) * -np.sign(v)  # positive = reversion toward zero
            rows.append(r)
    ev = pd.DataFrame(rows)
    if len(ev):
        for h in [1, 6, 24]:
            x = ev[f"d{h}"].dropna().values * 1e4
            d = describe_pnl(x)
            _, lo, hi = circular_block_bootstrap_ci(x, block=10, n_boot=2000)
            out["h7"][f"h{h}"] = {"n_events": d["n"], "mean_reversion_bps": d["mean"], "ci95": [lo, hi], "median_bps": d["median"], "share_reverting": d["win_rate"]}
        out["h7"]["by_session"] = {}
        for sname, g in ev.groupby("session"):
            out["h7"]["by_session"][sname] = {"n": int(len(g)), "mean_abs_p0_bps": float(g["p0"].abs().mean() * 1e4)}
            for h in [1, 6]:
                x = g[f"d{h}"].dropna().values * 1e4
                if len(x) > 10:
                    _, lo, hi = circular_block_bootstrap_ci(x, block=10, n_boot=1000)
                    out["h7"]["by_session"][sname][f"h{h}_mean_bps"] = float(x.mean()); out["h7"]["by_session"][sname][f"h{h}_ci"] = [lo, hi]
        out["h7"]["mean_abs_p0_bps"] = float(ev["p0"].abs().mean() * 1e4)
        out["h7"]["events_per_coin_month"] = float(len(ev) / p.shape[1] / max(1, (p.index.max() - p.index.min()).days / 30))
    # sessions: internal vs external for equities (ET): weekend Fri 20:00 -> Sun 20:00; weekday 20:00 -> 04:00
    idx_et = p.index.tz_convert(ET)
    dow = np.array(idx_et.dayofweek); hr = np.array(idx_et.hour)
    weekend = ((dow == 4) & (hr >= 20)) | (dow == 5) | ((dow == 6) & (hr < 20))
    overnight = (~weekend) & ((hr >= 20) | (hr < 4))
    sess = np.where(weekend, "weekend_internal", np.where(overnight, "overnight", "external"))
    ap = p.abs()
    out["h11"]["mean_abs_premium_bps_by_session"] = {k: float(ap[sess == k].stack().mean() * 1e4) for k in ["external", "overnight", "weekend_internal"]}
    f = hourly_matrix(fu, "xyz", "funding")[p.columns]
    out["h11"]["mean_funding_apr_by_session"] = {k: float(f[sess == k].stack().mean() * H_PER_YEAR) for k in ["external", "overnight", "weekend_internal"]}
    # weekend premium drift vs Monday move (Sun 20:00 ET -> Mon 12:00 ET)
    rows = []
    sundays = pd.date_range(p.index.min(), p.index.max(), freq="W-SUN")
    for su in sundays:
        t_fri = pd.Timestamp(su.date() - pd.Timedelta(days=2), tz=ET).replace(hour=20).tz_convert("UTC")
        t_sun = pd.Timestamp(su.date(), tz=ET).replace(hour=20).tz_convert("UTC")
        t_mon = pd.Timestamp(su.date() + pd.Timedelta(days=1), tz=ET).replace(hour=12).tz_convert("UTC")
        if t_fri in p.index and t_sun in p.index and t_mon in p.index:
            a = p.loc[t_sun] - p.loc[t_fri]; b = p.loc[t_mon] - p.loc[t_sun]
            for c in p.columns:
                if not (np.isnan(a[c]) or np.isnan(b[c])): rows.append({"sunday": su, "coin": c, "wk_drift": a[c], "mon_move": b[c]})
    r = pd.DataFrame(rows)
    if len(r) > 20:
        out["h11"]["weekend_drift_vs_monday"] = {"n_coin_weekends": int(len(r)), "corr": float(r["wk_drift"].corr(r["mon_move"])),
                                                  "share_reversal": float((np.sign(r["wk_drift"]) == -np.sign(r["mon_move"])).mean()),
                                                  "mean_abs_drift_bps": float(r["wk_drift"].abs().mean() * 1e4),
                                                  "hac_beta": hac_regression(r["mon_move"].values * 1e4, r["wk_drift"].values * 1e4, 3)}
    return out


def main():
    fu, st, meta = load()
    rep = {}
    rep["h6_persistence"] = h6_persistence(fu)
    h4 = {}; series = {}
    for K in [5, 10, None]:
        for fee in ["hip3_standard", "hip3_growth"]:
            df = h4_equity_harvest(fu, meta, K, fee)
            lab = f"K={K or 'all'}|{fee}"
            h4[lab] = summarise_weekly(df, lab); series[lab] = df
    df = h4_equity_harvest(fu, meta, 10, "hip3_standard", basis=False)
    h4["K=10|hip3_standard|no_basis"] = summarise_weekly(df, "K=10|hip3_standard|no_basis")
    df = h4_equity_harvest(fu, meta, 5, "hip3_standard", margin=0.5)
    h4["K=5|hip3_standard|margin50"] = summarise_weekly(df, "K=5|hip3_standard|margin50")
    df = h4_equity_harvest(fu, meta, 5, "hip3_standard")
    h4["monthly_K5_standard"] = {str(k.date()): float(v) for k, v in df.set_index("week")["ret"].resample("MS").sum().items()}
    rep["h4_equity_harvest"] = h4
    series["K=10|hip3_standard"].to_csv(os.path.join(OUT, "h4_weekly_K10_standard.csv"), index=False)
    rep["h5_native_carry"] = h5_native_carry(fu)
    rep["h14_cross_dex"] = h14_cross_dex(fu)
    rep.update(h7_h11_premium(fu))
    json.dump(rep, open(os.path.join(OUT, "e4_results.json"), "w"), indent=1, default=str)
    # markdown summary
    md = ["# E4 results — funding-based hypotheses (H4, H5, H6, H7, H11, H14)\n", "Generated by experiments/e4_funding_carry.py from data/derived/funding_hourly.parquet.\n"]
    md.append("\n## H6 persistence of weekly mean funding\n\n| dex | coins | weekly pairs | corr(week→next) | sign persistence | x-sec rank corr (mean) | mean APR | median APR |\n|---|---|---|---|---|---|---|---|")
    for dex, v in rep["h6_persistence"].items():
        md.append(f"| {dex} | {v['coins']} | {v['weekly_pairs']} | {v['corr_week_to_next']:.2f} | {v['sign_persistence']:.2f} | {v['xsec_rank_corr_mean']:.2f} ({v['xsec_rank_corr_n_weeks']} wks) | {100*v['mean_apr_all']:.1f}% | {100*v['median_apr_all']:.1f}% |")
    md.append("\n## H4 equity-perp funding harvest (short top-K funding xyz perps, long stock; weekly rebalance)\n\n| variant | weeks | gross funding APR | basis APR | cost APR | financing APR | NET APR on capital [95% CI] | Sharpe (ann.) | dev APR (wks) | val APR (wks) | APR ex top-5 weeks | max DD |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lab, v in h4.items():
        if lab.startswith("monthly"):
            md.append(f"| monthly net returns K=5 standard: {' '.join(f'{k[:7]}:{100*x:.2f}%' for k, x in v.items())} | | | | | | | | | | | |"); continue
        if v["n_weeks"] == 0: md.append(f"| {lab} | 0 | | | | | | | | | | |"); continue
        md.append(f"| {lab} | {v['n_weeks']} | {100*v['gross_funding_apr']:.1f}% | {100*v['basis_apr']:.1f}% | {100*v['cost_apr']:.1f}% | {100*v['financing_apr']:.1f}% | **{100*v['net_apr']:.1f}%** [{100*v['net_apr_ci95'][0]:.1f}, {100*v['net_apr_ci95'][1]:.1f}] | {v['sharpe_ann']:.2f} | {100*v['dev_apr']:.1f}% ({v['dev_weeks']}) | {100*v['val_apr']:.1f}% ({v['val_weeks']}) | {100*v['apr_ex_top5_weeks']:.1f}% | {100*v['max_dd']:.2f}% |")
    md.append("\n## H5 native perp carry (short perp / long spot), funding leg from fundingHistory\n\n| coin | period | share negative | share at floor | worst 30d funding (bps) | corr 30d→next | by year: gross funding APR → net APR on capital |\n|---|---|---|---|---|---|---|")
    for c, v in rep["h5_native_carry"].items():
        yrs = "; ".join(f"{y}: {100*r['gross_funding_apr']:.1f}% → {100*r['net_apr_on_capital']:.1f}%" for y, r in v["by_year"].items())
        md.append(f"| {c} | {v['start']}→{v['end']} | {100*v['share_negative']:.0f}% | {100*v['share_at_floor']:.0f}% | {v['worst_30d_funding_bps']:.0f} | {v['corr_30d_to_next']:.2f} | {yrs} |")
    md.append("\n## H14 cross-dex same-underlying funding differential (weekly sign-following, both legs on Hyperliquid)\n\n| pair | hours | period | mean diff APR | mean |daily diff| APR | daily autocorr | RT cost bps | weekly net mean bps (t) | carry / basis bps | net APR on capital | dev / val net | win |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in rep["h14_cross_dex"].items():
        w = v.get("weekly", {})
        md.append(f"| {k} | {v['n_hours']} | {v['start']}→{v['end']} | {100*v['mean_diff_apr']:.1f}% | {100*v['mean_abs_daily_diff_apr']:.1f}% | {v['daily_autocorr']:.2f} | {v['round_trip_cost_bps']:.0f} | {w.get('net_mean_bps', np.nan):.1f} ({w.get('net_t', np.nan):.2f}, n={w.get('n',0)}, flips={w.get('n_flips',0)}) | {w.get('carry_mean_bps', np.nan):.1f} / {w.get('basis_mean_bps', np.nan):.1f} | {100*w.get('net_apr_on_capital', np.nan):.1f}% | {w.get('dev_net_mean', np.nan):.1f} / {w.get('val_net_mean', np.nan):.1f} | {100*w.get('win_rate', np.nan):.0f}% |")
        md.append(f"|  ↳ monthly mean diff APR: {' '.join(f'{kk[:7]}:{100*vv:.0f}%' for kk, vv in v['monthly_mean_diff_apr'].items())} | | | | | | | | | | | |")
    md.append("\n## H7 premium extremes (|p| > trailing-30d 95th pct), reversion toward zero after h hours\n")
    for h, v in rep["h7"].items():
        if isinstance(v, dict) and "n_events" in v: md.append(f"- {h}: n={v['n_events']}, mean reversion {v['mean_reversion_bps']:.1f} bps [CI {v['ci95'][0]:.1f}, {v['ci95'][1]:.1f}], median {v['median_bps']:.1f}, share reverting {100*v['share_reverting']:.0f}%")
    for sname, v in rep["h7"].get("by_session", {}).items():
        md.append(f"- session {sname}: n={v['n']}, |p0| {v['mean_abs_p0_bps']:.1f} bps, reversion h1 {v.get('h1_mean_bps', np.nan):.1f} [{v.get('h1_ci',[np.nan,np.nan])[0]:.1f},{v.get('h1_ci',[np.nan,np.nan])[1]:.1f}], h6 {v.get('h6_mean_bps', np.nan):.1f} [{v.get('h6_ci',[np.nan,np.nan])[0]:.1f},{v.get('h6_ci',[np.nan,np.nan])[1]:.1f}]")
    md.append(f"- mean |premium| at event {rep['h7'].get('mean_abs_p0_bps', np.nan):.1f} bps; events per coin-month {rep['h7'].get('events_per_coin_month', np.nan):.1f}. Round-trip standard HIP-3 taker cost ≈ 21 bps (growth ≈ 3 bps).")
    md.append("\n## H11 sessions (xyz equities)\n")
    md.append(f"- mean |premium| bps by session: {rep['h11']['mean_abs_premium_bps_by_session']}")
    md.append(f"- mean funding APR by session: { {k: round(100*v,1) for k,v in rep['h11']['mean_funding_apr_by_session'].items()} }")
    if "weekend_drift_vs_monday" in rep["h11"]:
        v = rep["h11"]["weekend_drift_vs_monday"]
        md.append(f"- weekend premium drift (Fri 20:00 → Sun 20:00 ET) vs Monday move (Sun 20:00 → Mon 12:00 ET): n={v['n_coin_weekends']} coin-weekends, corr {v['corr']:.2f}, share reversal {100*v['share_reversal']:.0f}%, HAC beta {v['hac_beta']['beta']:.2f} (t={v['hac_beta']['t_beta_hac']:.2f}), mean |drift| {v['mean_abs_drift_bps']:.1f} bps")
    open(os.path.join(OUT, "e4_summary.md"), "w").write("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
