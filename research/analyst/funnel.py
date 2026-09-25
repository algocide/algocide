"""The filtering funnel: universe -> deterministic rules -> composite rank -> deep read -> reasoning -> human.
Budgets cap every stage. The composite probability is used for ranking only and is never printed (no scores in reports).
The cost estimate reproduces the article's arithmetic and prices the configured models when a price is set."""
from __future__ import annotations
import numpy as np, pandas as pd
from .signals import catalyst_context


def latest_institutional(inst: pd.DataFrame | None, symbol: str, asof: pd.Timestamp):
    if inst is None or not len(inst): return None
    h = inst[(inst.symbol == symbol) & (pd.to_datetime(inst.filing_date) <= asof)]
    return h.sort_values("period_of_report").iloc[-1] if len(h) else None


def stage_rules(today: pd.DataFrame, inst, insider, earnings, calendar, cfg: dict) -> pd.DataFrame:
    sig = cfg["signals"]; rows = []
    for _, r in today.iterrows():
        reasons = []; corroborating = 0
        if pd.notna(r.anomaly_score) and r.anomaly_score > sig["anomaly_threshold"]: reasons.append(f"anomaly_score {r.anomaly_score:.2f} > {sig['anomaly_threshold']:.0f}")
        ins = insider[insider.symbol == r.symbol] if insider is not None and len(insider) else None
        if ins is not None and len(ins) and ins.iloc[0].net_shares > 0:
            reasons.append(f"insider net open-market buying ({int(ins.iloc[0].buys)} purchases, {int(ins.iloc[0].sells)} sales in {sig['insider_window_days']} days)"); corroborating += 1
        ir = latest_institutional(inst, r.symbol, r.date)
        if ir is not None and ir.institutional_flow > sig["institutional_min_change"]:
            reasons.append(f"institutional_flow {ir.institutional_flow:+.2f} (period end {pd.Timestamp(ir.period_of_report).date()}, filed {pd.Timestamp(ir.filing_date).date()})"); corroborating += 1
        cat = catalyst_context(r.symbol, r.date, earnings, calendar, sig["catalyst_window_days"])
        if cat["earnings_within_window"]: reasons.append(f"earnings {cat['days_since_earnings']} days ago"); corroborating += 1
        if reasons: rows.append({"symbol": r.symbol, "date": r.date, "anomaly_score": float(r.anomaly_score) if pd.notna(r.anomaly_score) else 0.0, "reasons": reasons, "n_reasons": len(reasons), "corroborating": corroborating})
    if not rows: return pd.DataFrame(columns=["symbol", "date", "anomaly_score", "reasons", "n_reasons", "corroborating"])
    return pd.DataFrame(rows).sort_values(["n_reasons", "anomaly_score"], ascending=[False, False]).head(cfg["funnel"]["max_rules"]).reset_index(drop=True)


def stage_classifier(candidates: pd.DataFrame, today: pd.DataFrame, model: dict | None, cfg: dict) -> pd.DataFrame:
    """Rank by the dev-fitted composite when one exists, else by anomaly_score. Rank value is internal."""
    if not len(candidates): return candidates.assign(rank_value=pd.Series(dtype=float), rank_basis=pd.Series(dtype=str))
    c = candidates.merge(today[["symbol", "z_return", "z_volume", "z_momentum"]], on="symbol", how="left")
    if model and model.get("features"):
        from .composite import predict
        c = c.assign(price_anomaly=c.z_return, volume_anomaly=c.z_volume, momentum=c.z_momentum, institutional=np.nan, fundamental=np.nan, options=np.nan, retail=np.nan)
        c["rank_value"] = predict(model, c); c["rank_basis"] = "composite (dev fit, internal ranking only)"
    else:
        c["rank_value"] = c.anomaly_score; c["rank_basis"] = "anomaly_score"
    return c.sort_values(["rank_value", "anomaly_score"], ascending=False).head(cfg["funnel"]["max_classifier"]).reset_index(drop=True)


def cost_estimate(n_deep_read: int, n_reasoning: int, cfg_llm: dict) -> dict:
    """cost = input_tokens_M * price_in + output_tokens_M * price_out, per name, times names per stage."""
    ft = cfg_llm["funnel_tokens"]; prices = cfg_llm["prices_per_million"]
    def per_name(model, tok):
        p = prices.get(model)
        return None if not p else tok["input"] / 1e6 * p["input"] + tok["output"] / 1e6 * p["output"]
    dr, rs = per_name(cfg_llm["deep_read_model"], ft["deep_read"]), per_name(cfg_llm["reasoning_model"], ft["reasoning"])
    ref_dr, ref_rs = per_name("kimi-k3", ft["deep_read"]), per_name("gpt-6-astra", ft["reasoning"])
    out = {"deep_read": {"model": cfg_llm["deep_read_model"], "names": n_deep_read, "per_name_usd": dr, "total_usd": (dr * n_deep_read) if dr is not None else None},
           "reasoning": {"model": cfg_llm["reasoning_model"], "names": n_reasoning, "per_name_usd": rs, "total_usd": (rs * n_reasoning) if rs is not None else None},
           "article_reference": {"kimi_k3_per_name_usd": ref_dr, "astra_per_name_usd": ref_rs, "kimi_150_names_usd": ref_dr * 150 if ref_dr else None, "astra_40_names_usd": ref_rs * 40 if ref_rs else None,
                                 "astra_10000_names_per_day_usd": ref_rs * 10_000 if ref_rs else None}}
    tot = [v["total_usd"] for v in (out["deep_read"], out["reasoning"])]
    out["total_usd"] = None if any(t is None for t in tot) else sum(tot)
    out["note"] = "None = model has no price in config llm.prices_per_million; fill it from your provider's price page"
    return out
