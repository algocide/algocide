"""Deep read (the Kimi role) and the contradiction engine (the Astra role), both on the configured provider.
consistency = 1 - |economic_reality - management_claim| / max(|economic_reality|, |management_claim|)
thesis_gap  = bull_strength - bear_strength   (from the arbiter's checked strengths, not the agents' own)"""
from __future__ import annotations
import json, re
import numpy as np, pandas as pd
from .base import Ledger, parse_json
from . import prompts


def consistency(economic_reality: float | None, management_claim: float | None) -> float | None:
    if economic_reality is None or management_claim is None: return None
    er, mc = float(economic_reality), float(management_claim); den = max(abs(er), abs(mc))
    if den == 0: return 1.0
    return float(1.0 - abs(er - mc) / den)


def thesis_gap(bull_strength, bear_strength) -> float | None:
    if bull_strength is None or bear_strength is None: return None
    return float(bull_strength) - float(bear_strength)


def _frame_shift(frame: str, quarters_back: int) -> str:
    y, q = int(frame[2:6]), int(frame[7]); idx = y * 4 + (q - 1) - quarters_back; return f"CY{idx // 4}Q{idx % 4 + 1}"


def economic_reality(xbrl: pd.DataFrame, metric: str, period: str) -> float | None:
    """Computed from the XBRL quarterly table, never from model text."""
    if xbrl is None or not len(xbrl): return None
    def val(m, fr):
        r = xbrl[(xbrl.metric == m) & (xbrl.frame == fr)]
        return float(r.val.iloc[0]) if len(r) and pd.notna(r.val.iloc[0]) else None
    rev = val("revenue", period)
    if metric == "revenue_yoy_growth":
        prev = val("revenue", _frame_shift(period, 4)); return (rev / prev - 1) if rev is not None and prev else None
    if metric == "revenue_qoq_growth":
        prev = val("revenue", _frame_shift(period, 1)); return (rev / prev - 1) if rev is not None and prev else None
    num = {"gross_margin": "gross_profit", "operating_margin": "operating_income", "net_margin": "net_income"}.get(metric)
    if num:
        n = val(num, period); return (n / rev) if n is not None and rev else None
    return None


def xbrl_table(xbrl: pd.DataFrame, quarters: int = 8) -> list[dict]:
    if xbrl is None or not len(xbrl): return []
    frames = sorted(xbrl.frame.unique())[-quarters:]; rows = []
    for fr in frames:
        row = {"period": fr}
        for m in ("revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "operating_cash_flow"):
            v = xbrl[(xbrl.metric == m) & (xbrl.frame == fr)]; row[m] = float(v.val.iloc[0]) if len(v) else None
        row["revenue_yoy_growth"] = economic_reality(xbrl, "revenue_yoy_growth", fr); row["net_margin"] = economic_reality(xbrl, "net_margin", fr); rows.append(row)
    return rows


def build_packet(symbol: str, asof: pd.Timestamp, signal: dict, texts: pd.DataFrame, xbrl: pd.DataFrame, extras: dict | None = None, quarters: int = 8, max_chars_per_doc: int = 12_000) -> dict:
    """Every source gets an id the model must cite. S1 is always the price/volume fact block from the signal engine."""
    sources = [{"id": "S1", "type": "price_volume_signals", "asof": str(pd.Timestamp(asof).date()), "text": json.dumps(signal, default=str)}]
    if extras:
        for k, v in extras.items(): sources.append({"id": f"S{len(sources)+1}", "type": k, "text": json.dumps(v, default=str)})
    if texts is not None and len(texts):
        t = texts[texts.symbol == symbol].sort_values("filing_date", ascending=False).head(quarters * 2)
        for _, r in t.iterrows():
            sources.append({"id": f"S{len(sources)+1}", "type": f"{r.form} {r.section}", "filing_date": str(pd.Timestamp(r.filing_date).date()), "report_date": str(pd.Timestamp(r.report_date).date()) if pd.notna(r.report_date) else None,
                            "accession": r.accession, "text": str(r.text)[:max_chars_per_doc]})
    tab = xbrl_table(xbrl[xbrl.symbol == symbol] if xbrl is not None and len(xbrl) else None, quarters)
    if tab: sources.append({"id": f"S{len(sources)+1}", "type": "xbrl_quarterly_table (edgar companyfacts)", "text": json.dumps(tab)})
    return {"symbol": symbol, "asof": str(pd.Timestamp(asof).date()), "sources": sources, "n_filing_docs": int(len(sources) - 1 - len(extras or {}) - (1 if tab else 0))}


def packet_text(packet: dict) -> str:
    return "\n\n".join(f"[{s['id']}] type={s['type']}" + (f" filing_date={s.get('filing_date')}" if s.get("filing_date") else "") + (f" report_date={s.get('report_date')}" if s.get("report_date") else "") + f"\n{s['text']}" for s in packet["sources"])


def deep_read(provider, cfg_llm: dict, packet: dict, xbrl: pd.DataFrame, ledger: Ledger) -> dict:
    sym = packet["symbol"]
    c = provider.complete(prompts.SYSTEM, prompts.DEEP_READ.format(symbol=sym, packet=packet_text(packet)), cfg_llm["deep_read_model"], cfg_llm["max_tokens_deep_read"], cfg_llm.get("temperature", 0.0))
    ledger.add(c, "deep_read", sym); out = parse_json(c.text); rows = []
    xs = xbrl[xbrl.symbol == sym] if xbrl is not None and len(xbrl) else None
    for q in out.get("quarters", []) or []:
        for cl in q.get("claims", []) or []:
            er = economic_reality(xs, cl.get("metric", ""), q.get("period", "")) if xs is not None else None
            rows.append({"period": q.get("period"), "metric": cl.get("metric"), "management_claim": cl.get("management_claim_value"), "economic_reality": er,
                         "consistency": consistency(er, cl.get("management_claim_value")), "source": cl.get("source"), "quote": (cl.get("quote") or "")[:200]})
    cons = [r["consistency"] for r in rows if r["consistency"] is not None]
    return {"symbol": sym, "stub": bool(c.stub or out.get("stub")), "model": c.model, "raw": out, "consistency_rows": rows, "min_consistency": (min(cons) if cons else None),
            "flag": ("rhetoric diverging from the numbers, dig further" if cons and min(cons) < 0.6 else None), "n_filing_docs": packet.get("n_filing_docs", 0)}


def contradiction_engine(provider, cfg_llm: dict, packet: dict, deep: dict | None, ledger: Ledger) -> dict:
    sym = packet["symbol"]; pk = packet_text(packet)
    if deep and not deep.get("stub"): pk += "\n\n[DEEP_READ] " + json.dumps({"consistency_rows": deep["consistency_rows"], "key_facts": deep["raw"].get("key_facts", [])}, default=str)
    model = cfg_llm["reasoning_model"]; mt = cfg_llm["max_tokens_reasoning"]; temp = cfg_llm.get("temperature", 0.0)
    a = provider.complete(prompts.SYSTEM, prompts.BULL.format(symbol=sym, packet=pk), model, mt, temp); ledger.add(a, "bull", sym); A = parse_json(a.text)
    b = provider.complete(prompts.SYSTEM, prompts.BEAR.format(symbol=sym, packet=pk), model, mt, temp); ledger.add(b, "bear", sym); B = parse_json(b.text)
    cl = "\n".join(f"{i+1}. {q}" for i, q in enumerate(prompts.CHECKLIST))
    c = provider.complete(prompts.SYSTEM, prompts.ARBITER.format(checklist=cl, bull=json.dumps(A), bear=json.dumps(B), packet=pk), model, mt, temp); ledger.add(c, "arbiter", sym); C = parse_json(c.text)
    stub = bool(a.stub or b.stub or c.stub or C.get("stub"))
    return {"symbol": sym, "stub": stub, "model": c.model, "bull": A, "bear": B, "arbiter": C, "bull_strength": C.get("bull_strength"), "bear_strength": C.get("bear_strength"),
            "thesis_gap": thesis_gap(C.get("bull_strength"), C.get("bear_strength")), "top_contradiction": C.get("top_contradiction"),
            "what_would_change_conclusion": C.get("what_would_change_conclusion", []), "checklist": C.get("checklist", [])}
