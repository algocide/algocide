#!/usr/bin/env python3
"""How the final ten beat buy-and-hold (descriptive, POST-HOC view): for each script and coin, OOS annual return,
Sharpe and worst drawdown vs holding the coin over the same days, the excess compounded growth per year (daily log-return
differences) and a one-sided test of it (circular block bootstrap, 20-day blocks), plus the same comparison over the full 2021-2026 run.
Usage: PYTHONPATH=src python3 experiments/pine/beat_bh.py
"""
import json, math, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluate import run_key, asset_daily, WARMUP_DAYS, DAY  # noqa: E402

END_DAY = int(pd.Timestamp("2026-09-28", tz="UTC").timestamp() * 1000) // DAY
rng = np.random.default_rng(0)


def stats(r):
    n = len(r)
    eq = np.cumprod(1 + r)
    cagr = eq[-1] ** (365 / n) - 1
    sh = r.mean() / r.std(ddof=1) * math.sqrt(365) if r.std(ddof=1) > 0 else 0.0
    peak = np.maximum.accumulate(np.r_[1.0, eq])
    mdd = float((np.r_[1.0, eq] / peak - 1).min())
    return cagr, sh, mdd


def boot_p(d, block=20, n_boot=4000):
    """One-sided p-value that the mean daily excess return is <= 0 (circular block bootstrap of the centred series)."""
    n = len(d)
    obs = d.mean()
    c = d - obs
    nb = int(math.ceil(n / block))
    starts = rng.integers(0, n, size=(n_boot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n] % n
    means = c[idx].mean(axis=1)
    return float((means >= obs).mean())


def window(file, sym, d0, d1):
    z = np.load(f"data/pine/t3_mag/runs/{run_key(file, sym)}.npz")
    s = pd.Series(z["eq"], index=z["days"].astype(np.int64))
    b = asset_daily(sym).reindex(range(d0 - 1, d1 + 1)).ffill()
    s = s.reindex(range(d0 - 1, d1 + 1)).ffill()
    rs, rb = s.pct_change().dropna().to_numpy(), b.pct_change().dropna().to_numpy()
    return rs, rb


def main():
    top = json.load(open("results/pine_final/final_top10.json"))["top"]
    M = pd.read_parquet("results/pine_final/metrics.parquet").set_index(["file", "symbol"])
    rows = []
    for k, t in enumerate(top, 1):
        for sym in ("BTCUSDT", "ETHUSDT"):
            m = M.loc[(t["file"], sym)]
            d0 = int(m.oos_start_day)
            rs, rb = window(t["file"], sym, d0, END_DAY)
            cs, ss, ms = stats(rs)
            cb, sb, mb = stats(rb)
            ex = np.log1p(rs) - np.log1p(rb)        # difference in compounded growth per day
            z = np.load(f"data/pine/t3_mag/runs/{run_key(t['file'], sym)}.npz")
            f0 = int(z["days"][0]) + WARMUP_DAYS
            fs, fb = window(t["file"], sym, f0, END_DAY)
            fcs, _, fms = stats(fs)
            fcb, _, fmb = stats(fb)
            rows.append({"rank": k, "file": t["file"], "coin": sym[:3], "oos_from": str(pd.Timestamp(d0 * DAY, unit="ms").date()),
                         "days": len(rs), "cagr": cs, "bh_cagr": cb, "sharpe": ss, "bh_sharpe": sb, "mdd": ms, "bh_mdd": mb,
                         "excess_log_growth_per_year": ex.mean() * 365, "p_excess": boot_p(ex),
                         "full_cagr": fcs, "full_bh_cagr": fcb, "full_mdd": fms, "full_bh_mdd": fmb})
    D = pd.DataFrame(rows)
    D.to_csv("results/pine_final/beat_bh.csv", index=False)
    pd.set_option("display.width", 250)
    show = D.copy()
    for c in ("cagr", "bh_cagr", "mdd", "bh_mdd", "excess_log_growth_per_year", "full_cagr", "full_bh_cagr", "full_mdd", "full_bh_mdd"):
        show[c] = (100 * show[c]).round(0)
    for c in ("sharpe", "bh_sharpe"):
        show[c] = show[c].round(2)
    show["p_excess"] = show.p_excess.round(3)
    show["file"] = show.file.str.slice(0, 32)
    print(show.to_string(index=False))


if __name__ == "__main__":
    main()
