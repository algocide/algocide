#!/usr/bin/env python3
"""Audit tournament finalists: robustness reruns (SOLUSDT, costs x2), per-year returns vs buy-and-hold, trade
concentration, long/short split, static repaint/lookahead checks, and equity charts.

Usage: PYTHONPATH=src python3 experiments/pine/audit.py --files a.md,b.md --out results/pine/audit
"""
import argparse, json, math, os, re, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tournament import tf_ms_of, daily            # noqa: E402
from evaluate import window_metrics, asset_daily, DAY, WARMUP_DAYS   # noqa: E402

REPAINT_PATTERNS = {
    "lookahead_on": r"lookahead\s*=\s*barmerge\.lookahead_on|lookahead_on",
    "security_no_offset": r"(request\.)?security\s*\([^)]*,\s*(close|high|low|open|hl2|hlc3|ohlc4)\s*\)",
    "realtime_branch": r"barstate\.(isrealtime|isconfirmed|islast|isnew)",
    "varip": r"\bvarip\b",
    "timenow": r"\btimenow\b",
    "calc_on_every_tick": r"calc_on_every_tick\s*=\s*true",
    "process_orders_on_close": r"process_orders_on_close\s*=\s*true",
    "security_lower_tf": r"request\.security_lower_tf",
    "negative_offset": r"\[\s*-\s*\d",
}


def run(src, sym, tf_ms, fee, end_ms, max_bars):
    from pinebt.engine import CompiledScript, Runner
    cs = CompiledScript(src)
    r = Runner(cs, sym, tf_ms, end_ms=end_ms, max_bars=max_bars, fee=fee, time_limit=1800).run()
    return cs, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", required=True)
    ap.add_argument("--index", default="data/pine/index.parquet")
    ap.add_argument("--out", default="results/pine/audit")
    ap.add_argument("--end", default="2026-09-29")
    ap.add_argument("--max-bars", type=int, default=200_000)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    idx = pd.read_parquet(a.index).set_index("file")
    end_ms = int(pd.Timestamp(a.end, tz="UTC").timestamp() * 1000)
    end_day = end_ms // DAY - 1
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    report = []
    for f in a.files.split(","):
        row = idx.loc[f]
        src = row.source
        tf = tf_ms_of(row.bt_period)
        lm = row.last_modified
        oos_day = int((lm + pd.Timedelta(days=1)).value // 10**6 // DAY) + 1
        rec = {"file": f, "name": row["name"], "author": row.author, "last_modified": str(lm), "tf_ms": tf,
               "detail": row.detail, "static": {k: bool(re.search(p, src)) for k, p in REPAINT_PATTERNS.items()}}
        fig, axes = plt.subplots(3, 1, figsize=(9, 8), sharex=False)
        for k, (sym, fee) in enumerate([("BTCUSDT", 0.0007), ("ETHUSDT", 0.0007), ("SOLUSDT", 0.0007),
                                        ("BTCUSDT", 0.0014), ("ETHUSDT", 0.0014)]):
            cs, r = run(src, sym, tf, fee, end_ms, a.max_bars)
            days, eq = daily(r["TC"], r["equity"])
            days = days.astype(np.int64)
            tr = np.array([[c[3], c[4], c[5], c[6], c[7], c[8]] for c in r["closed"]], float) if r["closed"] else np.zeros((0, 6))
            d0 = max(int(days[0]) + WARMUP_DAYS, oos_day)
            bh = asset_daily(sym)
            m = window_metrics(days, eq, tr, d0, end_day, bh)
            full = window_metrics(days, eq, tr, int(days[0]) + WARMUP_DAYS, end_day, bh)
            key = f"{sym}_fee{int(fee * 1e4)}bps"
            out = {"oos": m, "full": full, "err_bars": r["err_bars"], "blown": r["blown"], "n_closed": len(r["closed"])}
            if fee == 0.0007:
                # per-year returns vs buy and hold, trade concentration, long/short split
                s = pd.Series(eq, index=pd.to_datetime(days * DAY, unit="ms"))
                b = bh.reindex(days).ffill()
                b.index = s.index
                yr = {}
                for y, g in s.groupby(s.index.year):
                    gb = b[b.index.year == y]
                    yr[int(y)] = {"strategy": float(g.iloc[-1] / g.iloc[0] - 1), "buy_hold": float(gb.iloc[-1] / gb.iloc[0] - 1)}
                out["years"] = yr
                oos_t = tr[tr[:, 1] >= d0 * DAY] if len(tr) else tr
                if len(oos_t):
                    net = oos_t[:, 5]
                    order = np.sort(net)[::-1]
                    tot = net.sum()
                    out["oos_trades_detail"] = {
                        "n": int(len(oos_t)), "long_share": float((oos_t[:, 2] > 0).mean()),
                        "net_long": float(net[oos_t[:, 2] > 0].sum()), "net_short": float(net[oos_t[:, 2] < 0].sum()),
                        "top5_share_of_net": float(order[:5].sum() / tot) if tot > 0 else None,
                        "median_hold_h": float(np.median(oos_t[:, 1] - oos_t[:, 0]) / 3_600_000),
                        "win_rate": float((net > 0).mean())}
                if k < 3:
                    ax = axes[k]
                    ax.plot(s.index, s.values / s.values[0], lw=1.0, label="strategy")
                    ax.plot(s.index, b.values / b.values[0], lw=0.8, alpha=0.7, label="buy & hold")
                    ax.axvline(pd.to_datetime(d0 * DAY, unit="ms"), color="k", ls="--", lw=0.8)
                    ax.set_yscale("log")
                    ax.set_title(f"{sym} (dashed line = start of out-of-sample)", fontsize=9)
                    ax.legend(fontsize=7, loc="upper left")
            rec[key] = out
        fig.suptitle(row["name"][:90], fontsize=10)
        fig.tight_layout()
        png = os.path.join(a.out, re.sub(r"[^A-Za-z0-9]+", "_", f[:-3])[:80] + ".png")
        fig.savefig(png, dpi=110)
        plt.close(fig)
        rec["chart"] = png
        report.append(rec)
        print(f"audited {f}", flush=True)
    with open(os.path.join(a.out, "audit.json"), "w") as fo:
        json.dump(report, fo, indent=1, default=str)
    print(f"written {os.path.join(a.out, 'audit.json')}")


if __name__ == "__main__":
    main()
