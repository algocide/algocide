#!/usr/bin/env python3
"""Apply the pre-registered evaluation (docs/pine/preregistration.md) to tournament runs.

Outputs (to --results): metrics.parquet (one row per script x symbol), ranking.csv (eligible scripts, score order,
duplicates marked), top10.json, summary.json.
"""
import argparse, json, math, os, sys
import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
from hlr.stats import deflated_sharpe, probabilistic_sharpe, max_drawdown  # noqa: E402

DAY = 86_400_000
WARMUP_DAYS = 30
MIN_OOS_DAYS = 365
MIN_TRADES = 20
DUP_CORR = 0.95


def run_key(file, symbol):
    import hashlib
    return hashlib.sha1(f"{file}|{symbol}".encode()).hexdigest()[:16]


def asset_daily(symbol):
    from pinebt.data import resample
    bo, tc, o, h, l, c, v, f = resample(symbol, DAY)
    days = (np.asarray(tc, np.int64) - 1) // DAY
    return pd.Series(np.asarray(c, float), index=days.astype(np.int64))


def window_metrics(days, eq, trades, d0, d1, bh: pd.Series):
    """Metrics on daily equity for days in [d0, d1] (returns of those days)."""
    sel = (days >= d0 - 1) & (days <= d1)
    if sel.sum() < 3:
        return None
    e = eq[sel]
    dd = days[sel]
    r = e[1:] / e[:-1] - 1.0
    rd = dd[1:]
    n = len(r)
    sd = r.std(ddof=1) if n > 1 else 0.0
    mu = r.mean()
    sharpe = mu / sd * math.sqrt(365) if sd > 0 else 0.0
    tot = e[-1] / e[0] - 1.0 if e[0] > 0 else -1.0
    cagr = (e[-1] / e[0]) ** (365.0 / n) - 1.0 if e[0] > 0 and e[-1] > 0 else -1.0
    peak = np.maximum.accumulate(e)
    mdd = float(((e - peak) / peak).min())
    t0, t1 = (d0) * DAY, (d1 + 1) * DAY
    if len(trades):
        inwin = (trades[:, 1] >= t0) & (trades[:, 1] < t1)
        tw = trades[inwin]
        # time in market inside the window (closed trades only)
        ov = np.clip(np.minimum(trades[:, 1], t1) - np.maximum(trades[:, 0], t0), 0, None).sum()
        exposure = float(ov / (t1 - t0))
    else:
        tw = np.zeros((0, 6))
        exposure = 0.0
    nt = len(tw)
    wins = tw[:, 5][tw[:, 5] > 0].sum() if nt else 0.0
    losses = -tw[:, 5][tw[:, 5] < 0].sum() if nt else 0.0
    b = bh.reindex(np.arange(d0 - 1, d1 + 1)).ffill().to_numpy()
    br = b[1:] / b[:-1] - 1.0
    k = min(len(br), n)
    br = br[-k:] if k else br
    rr = r[-k:] if k else r
    bsd = br.std(ddof=1) if k > 1 else 0.0
    bh_sharpe = br.mean() / bsd * math.sqrt(365) if bsd > 0 else 0.0
    bh_cagr = (b[-1] / b[0]) ** (365.0 / max(1, len(br))) - 1.0 if len(b) > 1 and b[0] > 0 else 0.0
    if k > 2 and bsd > 0 and sd > 0:
        beta = float(np.cov(rr, br, ddof=1)[0, 1] / (bsd * bsd))
        alpha = float((rr.mean() - beta * br.mean()) * 365)
        corr = float(np.corrcoef(rr, br)[0, 1])
    else:
        beta = alpha = corr = 0.0
    return {"days": n, "sharpe": float(sharpe), "cagr": float(cagr), "total": float(tot), "mdd": mdd,
            "vol": float(sd * math.sqrt(365)), "trades": int(nt), "win_rate": float((tw[:, 5] > 0).mean()) if nt else np.nan,
            "profit_factor": float(wins / losses) if losses > 0 else (np.inf if wins > 0 else np.nan),
            "exposure": exposure, "bh_sharpe": float(bh_sharpe), "bh_cagr": float(bh_cagr), "beta": beta,
            "alpha": alpha, "corr_asset": corr, "skew": float(sps.skew(r)) if n > 2 else 0.0,
            "kurt": float(sps.kurtosis(r, fisher=False)) if n > 3 else 3.0, "mean_d": float(mu), "sd_d": float(sd)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="data/pine/t1")
    ap.add_argument("--index", default="data/pine/index.parquet")
    ap.add_argument("--results", default="results/pine")
    ap.add_argument("--end", default="2026-09-28")
    a = ap.parse_args()
    os.makedirs(a.results, exist_ok=True)
    idx = pd.read_parquet(a.index)
    idx = idx[idx.lang == "pinescript"].set_index("file")
    meta = pd.DataFrame([json.loads(l) for l in open(os.path.join(a.runs, "meta.jsonl"))])
    meta = meta.drop_duplicates(["file", "symbol"], keep="last")
    end_day = int(pd.Timestamp(a.end, tz="UTC").timestamp() * 1000) // DAY
    bh = {s: asset_daily(s) for s in meta.symbol.unique()}
    rows = []
    for m in meta.itertuples():
        row = {"file": m.file, "symbol": m.symbol, "status": m.status}
        for c in ("tf_ms", "n_bars", "err_bars", "blown", "n_closed", "us_per_bar", "flags", "first_err", "error"):
            row[c] = getattr(m, c, None)
        lm = idx.loc[m.file, "last_modified"]
        row["last_modified"] = lm
        if m.status == "ok":
            z = np.load(os.path.join(a.runs, "runs", run_key(m.file, m.symbol) + ".npz"))
            days, eq, tr = z["days"].astype(np.int64), z["eq"], z["trades"]
            run_start = int(days[0]) + WARMUP_DAYS
            oos0 = max(run_start, int((lm + pd.Timedelta(days=1)).value // 10**6 // DAY) + 1)
            row["oos_start_day"] = oos0
            o = window_metrics(days, eq, tr, oos0, end_day, bh[m.symbol]) if oos0 < end_day - 2 else None
            i_ = window_metrics(days, eq, tr, run_start, oos0 - 1, bh[m.symbol]) if oos0 - 1 > run_start + 2 else None
            f_ = window_metrics(days, eq, tr, run_start, end_day, bh[m.symbol])
            for pre, d in (("oos_", o), ("is_", i_), ("full_", f_)):
                if d:
                    for k, v in d.items():
                        row[pre + k] = v
        rows.append(row)
    M = pd.DataFrame(rows)
    M.to_parquet(os.path.join(a.results, "metrics.parquet"), index=False)

    # ---- eligibility and ranking
    syms = ["BTCUSDT", "ETHUSDT"]
    piv = {}
    for s in syms:
        piv[s] = M[M.symbol == s].set_index("file")
    files = sorted(set(piv["BTCUSDT"].index) & set(piv["ETHUSDT"].index))
    E = []
    for f in files:
        b, e = piv["BTCUSDT"].loc[f], piv["ETHUSDT"].loc[f]
        flags = b.flags if isinstance(b.flags, list) else []
        reasons = []
        for tag, r in (("BTC", b), ("ETH", e)):
            if r.status != "ok":
                reasons.append(f"{tag}:{r.status}")
                continue
            if bool(r.blown):
                reasons.append(f"{tag}:blown")
            if not (r.get("oos_days", 0) or 0) >= MIN_OOS_DAYS:
                reasons.append(f"{tag}:oos<{MIN_OOS_DAYS}d")
            if not (r.get("oos_trades", 0) or 0) >= MIN_TRADES:
                reasons.append(f"{tag}:trades<{MIN_TRADES}")
        if "lookahead_on" in flags:
            reasons.append("lookahead_on")
        E.append({"file": f, "eligible": not reasons, "reasons": ";".join(reasons),
                  "score": np.nanmean([b.get("oos_sharpe", np.nan), e.get("oos_sharpe", np.nan)]),
                  "oos_sharpe_btc": b.get("oos_sharpe", np.nan), "oos_sharpe_eth": e.get("oos_sharpe", np.nan),
                  "is_sharpe_btc": b.get("is_sharpe", np.nan), "is_sharpe_eth": e.get("is_sharpe", np.nan),
                  "full_sharpe_btc": b.get("full_sharpe", np.nan), "full_sharpe_eth": e.get("full_sharpe", np.nan),
                  "oos_days": min(b.get("oos_days", 0) or 0, e.get("oos_days", 0) or 0),
                  "oos_trades_btc": b.get("oos_trades", 0), "oos_trades_eth": e.get("oos_trades", 0),
                  "oos_cagr_btc": b.get("oos_cagr", np.nan), "oos_cagr_eth": e.get("oos_cagr", np.nan),
                  "oos_mdd_btc": b.get("oos_mdd", np.nan), "oos_mdd_eth": e.get("oos_mdd", np.nan),
                  "bh_sharpe_btc": b.get("oos_bh_sharpe", np.nan), "bh_sharpe_eth": e.get("oos_bh_sharpe", np.nan),
                  "bh_cagr_btc": b.get("oos_bh_cagr", np.nan), "bh_cagr_eth": e.get("oos_bh_cagr", np.nan),
                  "beta_btc": b.get("oos_beta", np.nan), "beta_eth": e.get("oos_beta", np.nan),
                  "exposure_btc": b.get("oos_exposure", np.nan), "tf_ms": b.tf_ms, "flags": ",".join(flags),
                  "last_modified": b.last_modified})
    R = pd.DataFrame(E)
    el = R[R.eligible].sort_values("score", ascending=False).reset_index(drop=True)
    # ---- dedupe on BTC OOS daily returns
    def oos_returns(f, sym="BTCUSDT"):
        r = piv[sym].loc[f]
        z = np.load(os.path.join(a.runs, "runs", run_key(f, sym) + ".npz"))
        d, q = z["days"].astype(np.int64), z["eq"]
        s = pd.Series(q, index=d)
        s = s[s.index >= r.oos_start_day - 1]
        return s.pct_change().dropna()
    kept, dup_of = [], {}
    cache = {}
    for f in el.file:
        rf = cache.setdefault(f, oos_returns(f))
        dup = None
        for g in kept:
            rg = cache[g]
            j = rf.index.intersection(rg.index)
            if len(j) > 30 and rf.loc[j].std() > 0 and rg.loc[j].std() > 0:
                if float(np.corrcoef(rf.loc[j], rg.loc[j])[0, 1]) > DUP_CORR:
                    dup = g
                    break
        if dup is None:
            kept.append(f)
        else:
            dup_of[f] = dup
        if len(kept) >= 60:
            break
    el["duplicate_of"] = el.file.map(dup_of)
    # ---- deflated Sharpe per asset (per-period SR, N = eligible count)
    n_el = len(el)
    out_top = []
    for s, tag in (("BTCUSDT", "btc"), ("ETHUSDT", "eth")):
        srs = el[f"oos_sharpe_{tag}"].to_numpy() / math.sqrt(365)
        var_sr = float(np.var(srs, ddof=1)) if n_el > 1 else 0.0
        el[f"dsr_{tag}"] = np.nan
        el[f"psr_{tag}"] = np.nan
        for k, f in enumerate(el.file):
            if f not in kept[:30]:
                continue
            r = piv[s].loc[f]
            sr = r.oos_mean_d / r.oos_sd_d if r.oos_sd_d > 0 else 0.0
            dsr = deflated_sharpe(sr, int(r.oos_days), r.oos_skew, r.oos_kurt, n_el, var_sr)
            el.loc[k, f"dsr_{tag}"] = dsr["dsr"]
            el.loc[k, f"psr_{tag}"] = probabilistic_sharpe(sr, int(r.oos_days), r.oos_skew, r.oos_kurt, 0.0)
            el.loc[k, f"sr0_ann_{tag}"] = dsr["sr0"] * math.sqrt(365)
    el.to_csv(os.path.join(a.results, "ranking.csv"), index=False)
    R.to_csv(os.path.join(a.results, "eligibility.csv"), index=False)
    top = el[el.file.isin(kept)].head(10)
    top.to_json(os.path.join(a.results, "top10.json"), orient="records", indent=1, default_handler=str)
    # ---- secondary questions
    ok_both = R[(R.reasons.fillna("").str.contains(":ok") == False)]
    sp = sps.spearmanr((el.is_sharpe_btc + el.is_sharpe_eth) / 2, el.score, nan_policy="omit") if n_el > 3 else None
    summ = {
        "n_scripts": int(len(idx)), "n_runs": int(len(M)), "status_counts": M.status.value_counts().to_dict(),
        "n_eligible": int(n_el), "ineligible_reason_counts": pd.Series(";".join(R.reasons.fillna("")).split(";")).value_counts().drop("", errors="ignore").head(20).to_dict(),
        "share_positive_oos_sharpe": float((el.score > 0).mean()) if n_el else None,
        "share_beating_bh_both": float(((el.oos_sharpe_btc > el.bh_sharpe_btc) & (el.oos_sharpe_eth > el.bh_sharpe_eth)).mean()) if n_el else None,
        "spearman_is_vs_oos": None if sp is None else {"rho": float(sp.correlation), "p": float(sp.pvalue)},
        "median_score": float(el.score.median()) if n_el else None,
        "median_bh_sharpe_btc": float(el.bh_sharpe_btc.median()) if n_el else None,
        "median_bh_sharpe_eth": float(el.bh_sharpe_eth.median()) if n_el else None,
        "n_duplicates_collapsed_in_top": int(len(dup_of)),
    }
    json.dump(summ, open(os.path.join(a.results, "summary.json"), "w"), indent=1, default=str)
    print(json.dumps(summ, indent=1, default=str))


if __name__ == "__main__":
    main()
