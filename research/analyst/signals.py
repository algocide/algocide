"""Layer 2: the signal engine. Plain code, the article's formulas verbatim, causal: every baseline (20-day volume,
trailing mean/stdev) ends the session before the one being scored. Forward returns and the label exist only for
evaluation and are never inputs to anything the report prints."""
from __future__ import annotations
import numpy as np, pandas as pd


def price_features(prices: pd.DataFrame, sig: dict) -> pd.DataFrame:
    w, rd, hw, mh, md = sig["vol_window"], sig["ret_days"], sig["hist_window"], sig["min_history"], sig["momentum_days"]
    out = []
    for s, d in prices.groupby("symbol", sort=True):
        d = d.sort_values("date").reset_index(drop=True); c = d.c.astype(float); v = d.v.astype(float)
        average_volume_20d = v.shift(1).rolling(w, min_periods=w).mean()          # previous 20 sessions, today excluded
        volume_ratio = v / average_volume_20d                                       # today's_volume / average_volume_20d
        return_5d = c / c.shift(rd) - 1                                             # (price_today - price_5d_ago) / price_5d_ago
        mu = return_5d.shift(1).rolling(hw, min_periods=mh).mean(); sd = return_5d.shift(1).rolling(hw, min_periods=mh).std()
        z_return = (return_5d - mu) / sd                                            # (return_5d - historical_mean) / historical_stdev
        vmu = volume_ratio.shift(1).rolling(hw, min_periods=mh).mean(); vsd = volume_ratio.shift(1).rolling(hw, min_periods=mh).std()
        z_volume = (volume_ratio - vmu) / vsd
        anomaly_score = z_return.abs() + z_volume.abs()                             # |z_return| + |z_volume|
        mom = c / c.shift(md) - 1
        mmu = mom.shift(1).rolling(hw, min_periods=mh).mean(); msd = mom.shift(1).rolling(hw, min_periods=mh).std()
        f = pd.DataFrame({"symbol": s, "date": d.date, "close": c, "volume": v, "average_volume_20d": average_volume_20d,
                          "volume_ratio": volume_ratio, "return_5d": return_5d, "z_return": z_return, "z_volume": z_volume,
                          "anomaly_score": anomaly_score, f"return_{md}d": mom, "z_momentum": (mom - mmu) / msd})
        for k in sig["horizons"]: f[f"fwd_{k}"] = c.shift(-k) / c - 1                # forward_return_k, evaluation only
        lh = sig["label_horizon"]; fwd = f[f"fwd_{lh}"]
        f["label"] = np.where(fwd.isna(), np.nan, (fwd >= sig["label_pct"]).astype(float))
        ev = (anomaly_score > sig["anomaly_threshold"]).fillna(False)
        prior = ev.astype(int).shift(1).rolling(sig["first_trigger_gap"], min_periods=1).sum().fillna(0)
        f["event"] = ev.astype(bool); f["first_trigger"] = f["event"] & (prior == 0)
        f["direction"] = np.sign(z_return.fillna(0)).astype(int); f["quiet"] = (anomaly_score < sig["quiet_threshold"]).fillna(False).astype(bool)
        out.append(f)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------- institutional (13F)
def position_changes(holdings: pd.DataFrame) -> pd.DataFrame:
    """position_change_pct = (shares_now - shares_before) / shares_before per filer and symbol, consecutive report periods."""
    if not len(holdings): return pd.DataFrame(columns=list(holdings.columns) + ["shares_before", "period_before", "position_change_pct"])
    h = holdings.dropna(subset=["shares"]).sort_values(["filer_cik", "symbol", "period_of_report"]).copy()
    g = h.groupby(["filer_cik", "symbol"])
    h["shares_before"] = g.shares.shift(1); h["period_before"] = g.period_of_report.shift(1)
    h["position_change_pct"] = (h.shares - h.shares_before) / h.shares_before
    return h[h.shares_before.notna() & (h.shares_before > 0)].reset_index(drop=True)


def institutional_flow(changes: pd.DataFrame, weights: pd.DataFrame | None = None) -> pd.DataFrame:
    """institutional_flow = sum_j weight_j * position_change_j per symbol and report period. Equal weights unless a
    filer_weights table (from filer_accuracy) is given. Filing dates are carried so the report can show them."""
    cols = ["symbol", "period_of_report", "institutional_flow", "n_filers", "filing_date", "first_filing_date"]
    if not len(changes): return pd.DataFrame(columns=cols)
    w = dict(zip(weights.filer_cik.astype(int), weights.weight.astype(float))) if weights is not None and len(weights) else {}
    rows = []
    for (sym, per), g in changes.groupby(["symbol", "period_of_report"]):
        ws = np.array([w.get(int(fc), np.nan) for fc in g.filer_cik], dtype=float)
        if np.isnan(ws).all() or np.nansum(ws) <= 0: ws = np.ones(len(g)) / len(g)
        else: ws = np.nan_to_num(ws, nan=float(np.nanmean(ws))); ws = ws / ws.sum()
        rows.append({"symbol": sym, "period_of_report": per, "institutional_flow": float((ws * g.position_change_pct.values).sum()),
                     "n_filers": int(len(g)), "filing_date": g.filing_date.max(), "first_filing_date": g.filing_date.min()})
    return pd.DataFrame(rows, columns=cols)


def forward_return(prices: pd.DataFrame, k: int) -> pd.DataFrame:
    out = []
    for s, d in prices.groupby("symbol"):
        d = d.sort_values("date"); out.append(pd.DataFrame({"symbol": s, "date": d.date.values, f"fwd_{k}": (d.c.shift(-k) / d.c - 1).values}))
    return pd.concat(out, ignore_index=True)


def filer_accuracy(changes: pd.DataFrame, prices: pd.DataFrame, min_change: float = 0.10, horizon: int = 60, label_pct: float = 0.05, min_obs: int = 10) -> pd.DataFrame:
    """Weights from historical lead/lag accuracy: for each filer, the share of its position increases (> min_change at
    period end P) followed by a >= label_pct rise over the `horizon` sessions after P, minus the universe base rate over
    the same dates. weight = max(0, hit_rate - base_rate); filers with < min_obs increases get no weight (equal default)."""
    cols = ["filer_cik", "weight", "n_obs", "hit_rate", "base_rate", "source"]
    if not len(changes): return pd.DataFrame(columns=cols)
    fwd = forward_return(prices, horizon); fwd["date"] = pd.to_datetime(fwd.date)
    inc = changes[changes.position_change_pct > min_change].copy(); rows = []
    for fc, g in inc.groupby("filer_cik"):
        hits, base = [], []
        for _, r in g.iterrows():
            f = fwd[(fwd.symbol == r.symbol) & (fwd.date >= r.period_of_report)].head(1)
            if not len(f) or pd.isna(f[f"fwd_{horizon}"].iloc[0]): continue
            hits.append(float(f[f"fwd_{horizon}"].iloc[0] >= label_pct))
            same_day = fwd[fwd.date == f.date.iloc[0]][f"fwd_{horizon}"].dropna()
            base.append(float((same_day >= label_pct).mean()) if len(same_day) else np.nan)
        n = len(hits)
        if n < min_obs: rows.append({"filer_cik": int(fc), "weight": np.nan, "n_obs": n, "hit_rate": np.mean(hits) if n else np.nan, "base_rate": np.nanmean(base) if n else np.nan, "source": "filer_accuracy (below min_obs)"}); continue
        hr, br = float(np.mean(hits)), float(np.nanmean(base))
        rows.append({"filer_cik": int(fc), "weight": max(0.0, hr - br), "n_obs": n, "hit_rate": hr, "base_rate": br, "source": "filer_accuracy"})
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------- insiders (Form 4)
def insider_summary(form4: pd.DataFrame, asof: pd.Timestamp, window_days: int = 90) -> pd.DataFrame:
    """Open-market purchases (code P) and sales (code S) in the trailing window by transaction date, plus the latest
    filing date so the report can say how old the information is."""
    cols = ["symbol", "buys", "sells", "shares_bought", "shares_sold", "net_shares", "usd_bought", "usd_sold", "last_filing_date", "insiders_buying"]
    if not len(form4): return pd.DataFrame(columns=cols)
    f = form4.copy(); f["transaction_date"] = pd.to_datetime(f.transaction_date, errors="coerce"); f["filing_date"] = pd.to_datetime(f.filing_date, errors="coerce")
    f = f[(f.transaction_date <= asof) & (f.transaction_date > asof - pd.Timedelta(days=window_days)) & (f.filing_date <= asof)]
    rows = []
    for sym, g in f.groupby("symbol"):
        b = g[g.code == "P"]; s = g[g.code == "S"]
        rows.append({"symbol": sym, "buys": int(len(b)), "sells": int(len(s)), "shares_bought": float(b.shares.fillna(0).sum()), "shares_sold": float(s.shares.fillna(0).sum()),
                     "net_shares": float(b.shares.fillna(0).sum() - s.shares.fillna(0).sum()),
                     "usd_bought": float((b.shares.fillna(0) * b.price.fillna(0)).sum()), "usd_sold": float((s.shares.fillna(0) * s.price.fillna(0)).sum()),
                     "last_filing_date": g.filing_date.max(), "insiders_buying": "; ".join(sorted(set(b.insider.dropna().astype(str))))[:200]})
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------- sequence (who moved first) and catalysts
def sequence_lags(symbol: str, event_date: pd.Timestamp, inst: pd.DataFrame, retail: pd.DataFrame | None = None) -> dict:
    """lag_institutional_to_price = time_of_price_move - time_of_institutional_signal (signal time = period end, when the
    buying happened). visible_before_move says whether the filing was public before the move; if not, the signal is
    'only visible now because of the 45-day lag'. Retail is a placeholder until a flow source exists."""
    out = {"lag_institutional_to_price_days": None, "institutional_period_end": None, "institutional_filing_date": None, "visible_before_move": None,
           "lag_retail_to_price_days": None, "retail_available": False}
    if inst is not None and len(inst):
        h = inst[(inst.symbol == symbol) & (pd.to_datetime(inst.period_of_report) <= event_date)].sort_values("period_of_report")
        if len(h):
            r = h.iloc[-1]; per = pd.Timestamp(r.period_of_report); fd = pd.Timestamp(r.filing_date) if pd.notna(r.filing_date) else None
            out.update({"lag_institutional_to_price_days": int((event_date - per).days), "institutional_period_end": str(per.date()),
                        "institutional_filing_date": str(fd.date()) if fd is not None else None, "visible_before_move": bool(fd is not None and fd <= event_date),
                        "institutional_flow": float(r.institutional_flow), "n_filers": int(r.n_filers)})
    if retail is not None and len(retail):
        h = retail[(retail.symbol == symbol) & (pd.to_datetime(retail.date) <= event_date)].sort_values("date")
        if len(h): out.update({"lag_retail_to_price_days": int((event_date - pd.Timestamp(h.iloc[-1].date)).days), "retail_available": True})
    return out


def catalyst_context(symbol: str, date: pd.Timestamp, earnings: pd.DataFrame, calendar: pd.DataFrame | None, window_days: int = 3) -> dict:
    out = {"last_earnings": None, "days_since_earnings": None, "earnings_within_window": False, "last_earnings_filing_date": None,
           "next_known": None, "next_known_source": None, "next_estimated": None}
    if earnings is not None and len(earnings):
        e = earnings[(earnings.symbol == symbol) & (pd.to_datetime(earnings.event_date) <= date)].sort_values("event_date")
        if len(e):
            last = pd.Timestamp(e.iloc[-1].event_date); out.update({"last_earnings": str(last.date()), "days_since_earnings": int((date - last).days),
                                                                       "earnings_within_window": abs((date - last).days) <= window_days,
                                                                       "last_earnings_filing_date": str(pd.Timestamp(e.iloc[-1].filing_date).date()) if pd.notna(e.iloc[-1].filing_date) else None,
                                                                       "next_estimated": str((last + pd.Timedelta(days=91)).date())})
    if calendar is not None and len(calendar):
        c = calendar[(calendar.symbol == symbol) & (pd.to_datetime(calendar.event_date) > date)].sort_values("event_date")
        if len(c): out.update({"next_known": str(pd.Timestamp(c.iloc[0].event_date).date()), "next_known_source": str(c.iloc[0].source), "next_kind": str(c.iloc[0].kind)})
    return out
