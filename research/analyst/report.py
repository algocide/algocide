"""The report: what changed, the source, what contradicts it. No scores, no buy/sell language; priority is a categorical
label from a fixed rule. language_check() rejects recommendation wording before anything is written."""
from __future__ import annotations
import re
import pandas as pd

BANNED = [r"\bwe recommend\b", r"\brecommend(s|ed|ation|ations)?\b", r"\bstrong buy\b", r"\bBUY\b", r"\bSELL\b", r"\bbuy now\b", r"\bshould (buy|sell|add|trim|short)\b",
          r"\bprice target\b", r"\btarget price\b", r"\b\d{1,3}\s?/\s?100\b", r"\bscore\s*[:=]\s*\d", r"\bbullish momentum confirmed\b", r"\b(out|under)perform\b", r"\bhold rating\b", r"\bupside\b", r"\bdownside\b"]


def language_check(text: str) -> list[str]:
    hits = []
    for pat in BANNED:
        for m in re.finditer(pat, text):
            s = max(0, m.start() - 40); hits.append(f"{pat!r}: ...{text[s:m.end()+40].replace(chr(10), ' ')}...")
    return hits


def priority(anomaly_score: float, corroborating: int, cfg_pri: dict) -> str:
    a = float(anomaly_score or 0)
    if a > cfg_pri["high_anomaly"] and corroborating >= 1: return "high"
    if a > cfg_pri["medium_anomaly"]: return "medium"
    return "low"


def pct(x, d=1) -> str: return "n/a" if x is None or pd.isna(x) else f"{x*100:+.{d}f}%"


def render_radar(R: dict) -> str:
    L = [f"MARKET RADAR — {R['date']} (generated {R['generated_at']})", f"Assets monitored: {R['assets_monitored']}", f"Anomalies detected: {R['anomalies']}",
         f"High-information events: {R['high_information']}", f"Portfolio risks: {R['portfolio_risks']}", f"Upcoming catalysts: {R['upcoming_catalysts']}", ""]
    d = R["data_status"]
    L.append(f"Data: prices through {d['prices_through']} ({d['prices_source']}); " + "; ".join(f"{k}: {v}" for k, v in d["tables"].items()) + f". LLM stages: {R['llm_status']}.")
    L.append("Not available (no free source configured): " + ", ".join(R["not_available"]) + ".")
    L.append("")
    if not R["companies"]: L.append("No names passed the deterministic rules today.")
    for c in R["companies"]:
        L.append(f"**{c['symbol']} ({c['name']}) — priority: {c['priority']}**")
        parts = [f"Price {pct(c['price_5d'])} over 5 sessions ({pct(c['price_1d'])} last session)", f"Volume {c['volume_ratio']:.1f}x its 20-session average" if c.get("volume_ratio") is not None and not pd.isna(c["volume_ratio"]) else "Volume n/a",
                 f"z_return {c['z_return']:+.1f}, z_volume {c['z_volume']:+.1f}, anomaly_score {c['anomaly_score']:.1f}",
                 f"Institutional signal: {c['institutional']}", f"Insider: {c['insider']}", f"Fundamental: {c['fundamental']}", f"Catalyst: {c['catalyst']}", f"Sequence: {c['sequence']}", f"Contradiction: {c['contradiction']}"]
        L.append(" / ".join(parts))
        if c.get("what_would_change"): L.append("Would change the read: " + "; ".join(str(x) for x in c["what_would_change"][:3]))
        if c.get("rules"): L.append("Why it is here: " + "; ".join(c["rules"]))
        L.append("")
    P = R.get("portfolio")
    if P and P.get("n_positions"):
        L.append(f"**Portfolio (read-only, {P['n_positions']} positions, as of {P['asof']})**")
        L.append(f"{P['period_sessions']}-session portfolio move {pct(P['portfolio_return'],2)}; contributions: " + ", ".join(f"{r['symbol']} {r['contribution']*100:+.2f}pp (w {r['weight']*100:.0f}%)" for r in P["contributions"]))
        L.append(f"Concentration (HHI): {P['hhi']:.3f} vs {P['hhi_equal_weight_reference']:.3f} equal-weight; average pairwise correlation ({R['corr_window']} sessions): "
                 + (f"{P['avg_pairwise_correlation']:.2f}" if P.get("avg_pairwise_correlation") is not None else "n/a") + "; first principal component: " + (f"{P['pca_first_component_share']*100:.0f}% of variance" if P.get("pca_first_component_share") is not None else "n/a"))
        for f in P["flags"]: L.append(f"- flag: {f}")
        L.append("")
    elif P is not None: L.append("**Portfolio**: no positions supplied (pass --positions CSV or --address for a read-only Hyperliquid lookup).\n")
    if R["catalysts"]:
        L.append("**Upcoming catalysts**"); [L.append(f"- {c['symbol']}: {c['kind']} {c['date']} ({c['source']})") for c in R["catalysts"]]; L.append("")
    L.append("No score. No buy or sell language. What changed, sourced, and what contradicts it. Sources: Hyperliquid/Yahoo prices, SEC EDGAR filings where ingested; LLM outputs are claims to check, not data.")
    return "\n".join(L)
