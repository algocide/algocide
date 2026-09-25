"""Trade-level and account-level metrics with day-clustered uncertainty."""
from __future__ import annotations
import numpy as np, pandas as pd


def summarize(trades: pd.DataFrame, equity: pd.DataFrame | None = None, equity0: float = 100.0, n_boot: int = 1000, seed: int = 7) -> dict:
    t = trades.copy() if trades is not None else pd.DataFrame()
    if t is None or len(t) == 0:
        return {"n_trades": 0, "net_pnl": 0.0, "expectancy_usd": np.nan, "expectancy_r": np.nan, "profit_factor": np.nan, "win_rate": np.nan}
    t["day"] = pd.to_datetime(t.entry_ts, utc=True).dt.tz_convert("America/New_York").dt.date
    net = t.net_pnl.values; wins = net[net > 0]; losses = net[net <= 0]
    gp, gl = wins.sum(), -losses.sum()
    out = {"n_trades": int(len(t)), "n_days": int(t.day.nunique()), "net_pnl": float(net.sum()), "expectancy_usd": float(net.mean()),
           "expectancy_r": float(t.r_multiple.mean()), "profit_factor": float(gp / gl) if gl > 0 else (np.inf if gp > 0 else np.nan),
           "win_rate": float((net > 0).mean()), "avg_win": float(wins.mean()) if len(wins) else np.nan, "avg_loss": float(losses.mean()) if len(losses) else np.nan,
           "gross_pnl": float(t.gross_pnl.sum()), "total_costs": float(t.cost_total.sum()), "funding": float(t.funding.sum()),
           "cost_share_of_gross_profits": float(t.cost_total.sum() / gp) if gp > 0 else np.nan,
           "avg_hold_min": float(t.hold_min.mean()), "median_hold_min": float(t.hold_min.median()),
           "turnover_notional": float(t.notional.sum()), "avg_notional": float(t.notional.mean()), "avg_leverage": float(t.leverage.mean()),
           "n_long": int((t.dir == 1).sum()), "n_short": int((t.dir == -1).sum()),
           "net_long": float(net[t.dir.values == 1].sum()), "net_short": float(net[t.dir.values == -1].sum()),
           "stop_exits": int((t.reason == "stop").sum()), "target_exits": int((t.reason == "target").sum()), "session_exits": int((t.reason == "session_end").sum())}
    # excluding best trade / best day
    daily = t.groupby("day").net_pnl.sum()
    out["net_ex_best_trade"] = float(net.sum() - net.max()); out["net_ex_best_day"] = float(net.sum() - daily.max())
    out["best_trade"] = float(net.max()); out["best_day"] = float(daily.max()); out["worst_trade"] = float(net.min()); out["worst_day"] = float(daily.min())
    # max drawdown on the cumulative trade P&L (closed trades)
    cum = np.cumsum(net); peak = np.maximum.accumulate(np.concatenate([[0.0], cum])); dd = (np.concatenate([[0.0], cum]) - peak)
    out["max_dd_usd"] = float(-dd.min()); out["max_dd_pct"] = float(-dd.min() / equity0 * 100)
    # day-clustered block bootstrap for the mean net P&L per trade and total P&L
    rng = np.random.default_rng(seed); days = daily.index.values; groups = [t.net_pnl.values[t.day.values == d] for d in days]
    if len(days) >= 5:
        means, totals = [], []
        for _ in range(n_boot):
            pick = rng.integers(0, len(days), len(days)); vals = np.concatenate([groups[i] for i in pick])
            means.append(vals.mean()); totals.append(vals.sum())
        out["exp_ci95"] = [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]
        out["total_ci95"] = [float(np.percentile(totals, 2.5)), float(np.percentile(totals, 97.5))]
        out["p_mean_le_0"] = float(np.mean(np.array(means) <= 0))
    if equity is not None and len(equity):
        out["exposure"] = float(equity.in_position.mean()); out["final_equity"] = float(equity.equity.iloc[-1]); out["final_shadow_equity"] = float(equity.shadow_equity.iloc[-1])
        out["paused"] = bool(equity.paused.any()); out["paused_at"] = str(equity.ts[equity.paused].iloc[0]) if equity.paused.any() else None
        e = equity.equity.values; pk = np.maximum.accumulate(e); out["account_max_dd_usd"] = float((pk - e).max())
    return out


def by_group(trades: pd.DataFrame, key: str) -> pd.DataFrame:
    if trades is None or len(trades) == 0: return pd.DataFrame()
    t = trades.copy()
    if key == "month": t["month"] = pd.to_datetime(t.entry_ts, utc=True).dt.strftime("%Y-%m")
    if key == "direction": t["direction"] = np.where(t.dir == 1, "long", "short")
    g = t.groupby(key)
    return pd.DataFrame({"n": g.size(), "net": g.net_pnl.sum(), "exp": g.net_pnl.mean(), "win_rate": g.net_pnl.apply(lambda x: (x > 0).mean()),
                         "pf": g.net_pnl.apply(lambda x: x[x > 0].sum() / -x[x <= 0].sum() if (x <= 0).any() and x[x <= 0].sum() != 0 else np.inf)})


def windows(trades: pd.DataFrame, bounds: list[tuple]) -> pd.DataFrame:
    """Evaluate fixed rules in successive windows [(start, end), ...]. Trades are attributed by entry time; trades that
    cross a window boundary (exit after the window end) are purged."""
    rows = []
    for (a, b) in bounds:
        if trades is None or len(trades) == 0:
            rows.append({"start": a, "end": b, "n": 0, "net": 0.0}); continue
        et = pd.to_datetime(trades.entry_ts, utc=True); xt = pd.to_datetime(trades.exit_ts, utc=True)
        m = (et >= pd.Timestamp(a)) & (et < pd.Timestamp(b)) & (xt <= pd.Timestamp(b))
        w = trades[m]
        rows.append({"start": a, "end": b, "n": int(len(w)), "net": float(w.net_pnl.sum()), "pf": (w.net_pnl[w.net_pnl > 0].sum() / -w.net_pnl[w.net_pnl <= 0].sum()) if (w.net_pnl <= 0).any() and w.net_pnl[w.net_pnl <= 0].sum() != 0 else np.nan,
                     "exp": float(w.net_pnl.mean()) if len(w) else np.nan})
    return pd.DataFrame(rows)
