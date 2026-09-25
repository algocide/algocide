"""Portfolio math from the article: contribution to return, Herfindahl-Hirschman concentration, pairwise correlation
(with the principal-component share as the 'rigorous version'). Inputs are read-only positions; nothing here trades."""
from __future__ import annotations
import numpy as np, pandas as pd


def contributions(weights: dict, returns: dict) -> pd.DataFrame:
    """portfolio_return = sum_i (weight_i * return_i); each row's contribution in percentage points."""
    rows = [{"symbol": s, "weight": float(w), "return": float(returns.get(s, np.nan)), "contribution": float(w) * float(returns.get(s, np.nan))} for s, w in weights.items()]
    df = pd.DataFrame(rows, columns=["symbol", "weight", "return", "contribution"])
    if len(df):
        tot = df.contribution.sum(skipna=True); df["share_of_total"] = df.contribution / tot if tot else np.nan
    return df.sort_values("contribution", ascending=False).reset_index(drop=True)


def hhi(weights) -> float:
    """concentration = sum_i (weight_i^2) on weights that sum to 1."""
    w = np.asarray(list(weights.values()) if isinstance(weights, dict) else weights, dtype=float); w = w / w.sum()
    return float((w ** 2).sum())


def returns_panel(prices: pd.DataFrame, symbols: list[str], window: int, asof: pd.Timestamp) -> pd.DataFrame:
    p = prices[prices.symbol.isin(symbols) & (prices.date <= asof)].pivot(index="date", columns="symbol", values="c").sort_index().tail(window + 1)
    return p.pct_change().dropna(how="all")


def correlation_matrix(rets: pd.DataFrame) -> pd.DataFrame:
    """correlation(X,Y) = covariance(X,Y) / (stdev_X * stdev_Y), pairwise over the common sessions."""
    return rets.corr(min_periods=max(10, len(rets) // 2))


def avg_pairwise(corr: pd.DataFrame) -> float | None:
    n = len(corr)
    if n < 2: return None
    iu = np.triu_indices(n, 1); vals = corr.values[iu]; vals = vals[~np.isnan(vals)]
    return float(vals.mean()) if len(vals) else None


def pca_first_share(rets: pd.DataFrame) -> float | None:
    r = rets.dropna()
    if r.shape[0] < 10 or r.shape[1] < 2: return None
    x = (r - r.mean()) / r.std(ddof=0).replace(0, 1.0); s = np.linalg.svd(x.values, compute_uv=False)
    return float(s[0] ** 2 / (s ** 2).sum())


def portfolio_review(weights: pd.DataFrame, prices: pd.DataFrame, cfg_port: dict, asof: pd.Timestamp, period_sessions: int = 5) -> dict:
    """weights: DataFrame(symbol, side, weight). Returns the article's three checks with flags, all as facts."""
    out = {"asof": str(pd.Timestamp(asof).date()), "n_positions": int(len(weights)), "flags": []}
    if not len(weights): return out
    wmap = dict(zip(weights.symbol, weights.weight)); syms = list(wmap)
    px = prices[prices.symbol.isin(syms) & (prices.date <= asof)].pivot(index="date", columns="symbol", values="c").sort_index()
    rets = {}
    for s in syms:
        col = px[s].dropna() if s in px.columns else pd.Series(dtype=float)
        if len(col) > period_sessions:
            r = float(col.iloc[-1] / col.iloc[-1 - period_sessions] - 1); side = weights[weights.symbol == s].side.iloc[0]
            rets[s] = r if side != "short" else -r
    con = contributions(wmap, rets); out["period_sessions"] = period_sessions; out["portfolio_return"] = float(con.contribution.sum(skipna=True))
    out["contributions"] = con.to_dict("records"); out["hhi"] = hhi(wmap); out["hhi_equal_weight_reference"] = 1.0 / len(wmap)
    rp = returns_panel(prices, syms, cfg_port["corr_window"], asof); corr = correlation_matrix(rp) if rp.shape[1] >= 2 else pd.DataFrame()
    out["avg_pairwise_correlation"] = avg_pairwise(corr) if len(corr) else None; out["pca_first_component_share"] = pca_first_share(rp) if rp.shape[1] >= 2 else None
    out["correlation_matrix"] = corr.round(2).to_dict() if len(corr) else {}
    if out["hhi"] >= cfg_port["hhi_flag"]: out["flags"].append(f"concentration: HHI {out['hhi']:.3f} vs {out['hhi_equal_weight_reference']:.3f} if equal-weighted across {len(wmap)} positions")
    if out["avg_pairwise_correlation"] is not None and out["avg_pairwise_correlation"] >= cfg_port["avg_corr_flag"]:
        out["flags"].append(f"hidden correlation: average pairwise correlation {out['avg_pairwise_correlation']:.2f} over the last {cfg_port['corr_window']} sessions; positions move mostly as one factor")
    if len(con) and con.share_of_total.notna().any():
        top = con.iloc[0]
        if abs(out["portfolio_return"]) > 0 and top.share_of_total >= cfg_port["single_contribution_share_flag"]:
            out["flags"].append(f"single-position return: {top.symbol} contributed {top.contribution*100:+.2f}pp of the {out['portfolio_return']*100:+.2f}% {period_sessions}-session portfolio move ({top.share_of_total*100:.0f}% of it)")
    return out
