"""Daily panel builder for phase 3: Yahoo daily OHLC of the underlyings (previous session's data/derived/stock_daily.parquet)
aligned to the union trading calendar; missing days stay NaN (never forward-filled). Also BTC/ETH/HYPE daily from the
same source. Audit: per-symbol coverage, gaps, |daily return| > 40% flags (possible splits), duplicates."""
from __future__ import annotations
import os, json
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
SRC = os.path.join(R, "..", "data", "derived", "stock_daily.parquet")
CRYPTO = {"BTC", "ETH", "HYPE"}


def build(min_rows: int = 200):
    df = pd.read_parquet(SRC); df["date"] = pd.to_datetime(df["date"]); df = df.drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"])
    stocks = {s: d for s, d in df.groupby("symbol") if s not in CRYPTO}; crypto = {s: d for s, d in df.groupby("symbol") if s in CRYPTO}
    cal = pd.DatetimeIndex(sorted(set().union(*[set(d.date) for d in stocks.values()])))
    ccal = pd.DatetimeIndex(sorted(set().union(*[set(d.date) for d in crypto.values()])))
    audit = {}
    def align(d, calendar):
        d = d.set_index("date").reindex(calendar); return pd.DataFrame({"date": calendar, "o": d.o.values, "h": d.h.values, "l": d.l.values, "c": d.c.values, "v": d.v.values})
    panel, cpanel = {}, {}
    for s, d in stocks.items():
        if len(d) < min_rows: audit[s] = {"skipped": f"{len(d)} rows < {min_rows}"}; continue
        a = align(d, cal); r = a.c.pct_change().abs()
        audit[s] = {"rows": int(len(d)), "first": str(d.date.min().date()), "last": str(d.date.max().date()), "missing_days_in_span": int(a.c[(a.date >= d.date.min()) & (a.date <= d.date.max())].isna().sum()),
                    "abs_ret_gt40pct": int((r > 0.4).sum()), "ohlc_bad": int(((a.h < a.l) | (a.h < a.c) | (a.l > a.c)).sum())}
        panel[s] = a
    for s, d in crypto.items():
        cpanel[s] = align(d, ccal); audit[s] = {"rows": int(len(d)), "first": str(d.date.min().date()), "last": str(d.date.max().date()), "crypto": True}
    return panel, cpanel, cal, audit


SPLITS = {"dev_start": "2024-09-23", "val_start": "2025-09-23", "hold_start": "2026-03-23", "end": "2026-09-24"}


def val_windows(cal, n_windows=6):
    v = cal[(cal >= pd.Timestamp(SPLITS["val_start"])) & (cal < pd.Timestamp(SPLITS["hold_start"]))]; step = max(1, len(v) // n_windows)
    return [(v[i], v[min(i + step, len(v) - 1)] if i + step < len(v) else pd.Timestamp(SPLITS["hold_start"])) for i in range(0, len(v), step)][:n_windows]


if __name__ == "__main__":
    panel, cpanel, cal, audit = build()
    os.makedirs(os.path.join(R, "data", "raw"), exist_ok=True); json.dump(audit, open(os.path.join(R, "data", "raw", "daily_panel_audit.json"), "w"), indent=1)
    print("stocks:", len(panel), "calendar:", cal.min().date(), "->", cal.max().date(), len(cal), "days; crypto:", list(cpanel))
    bad = {s: a for s, a in audit.items() if a.get("abs_ret_gt40pct", 0) or a.get("ohlc_bad", 0) or a.get("missing_days_in_span", 0) > 5}
    print("flags:", json.dumps(bad, indent=0)[:1500]); print("val windows:", [(str(a.date()), str(b.date())) for a, b in val_windows(cal)])
