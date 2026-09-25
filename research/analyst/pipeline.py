"""Orchestration: scan (signals + funnel stages 1-2), analyze (stages 3-4 on the provider), render (radar), portfolio,
backtest. Everything reads the warehouse; nothing writes outside results/phase4 and data/warehouse."""
from __future__ import annotations
import os, json, datetime as dt
import numpy as np, pandas as pd
from .config import load_config, resolve
from .universe import load_universe
from .warehouse import Warehouse, ingest_offline_prices, PLACEHOLDERS
from .signals import price_features, position_changes, institutional_flow, insider_summary, sequence_lags, catalyst_context
from .funnel import stage_rules, stage_classifier, latest_institutional, cost_estimate
from .report import priority, render_radar, language_check, pct
from .llm.base import make_provider, Ledger
from .llm.stages import build_packet, deep_read, contradiction_engine


def open_warehouse(cfg: dict) -> tuple[Warehouse, pd.DataFrame, pd.DataFrame]:
    wh = Warehouse(resolve(cfg, "warehouse")); u = load_universe(cfg, wh); px = wh.read("prices_daily")
    if not len(px): ingest_offline_prices(wh, cfg, u); px = wh.read("prices_daily")
    return wh, u, px


def load_composite(cfg: dict) -> dict | None:
    p = os.path.join(resolve(cfg, "results"), "backtest.json")
    if not os.path.exists(p): return None
    try:
        c = json.load(open(p))["composite"]; auc = (c.get("eval_val") or {}).get("auc")
        return c["fit_dev"] if auc is not None and auc >= 0.55 else None        # protocol: val AUC < 0.55 = no usable ranking; rank by anomaly_score instead
    except Exception: return None


def _inst_text(ir, asof) -> str:
    if ir is None: return "not available (no 13F data ingested)"
    fd = pd.Timestamp(ir.filing_date); pe = pd.Timestamp(ir.period_of_report)
    tone = "positive" if ir.institutional_flow > 0.1 else ("negative" if ir.institutional_flow < -0.1 else "neutral")
    return f"{tone} (institutional_flow {ir.institutional_flow:+.2f} across {int(ir.n_filers)} filers, period end {pe.date()}, filing dated {(asof - fd).days} days ago)"


def _insider_text(ins, window) -> str:
    if ins is None or not len(ins): return f"no open-market Form 4 transactions in {window} days (or no Form 4 data ingested)"
    r = ins.iloc[0]; lf = pd.Timestamp(r.last_filing_date).date() if pd.notna(r.last_filing_date) else "n/a"
    return f"{int(r.buys)} purchases / {int(r.sells)} sales in {window} days, net {r.net_shares:+,.0f} shares (last filing {lf})"


def _catalyst_text(cat) -> str:
    if cat["last_earnings"] is None: return "no earnings event on file (8-K 2.02 not ingested, or foreign filer)"
    s = f"earnings {cat['days_since_earnings']} days ago ({cat['last_earnings']}, 8-K filed {cat['last_earnings_filing_date']})"
    if cat.get("next_known"): s += f"; next {cat.get('next_kind','event')} {cat['next_known']} ({cat['next_known_source']})"
    elif cat.get("next_estimated"): s += f"; next earnings estimated around {cat['next_estimated']} (last + 91 days, unconfirmed)"
    return s


def _sequence_text(seq) -> str:
    if seq.get("lag_institutional_to_price_days") is None: return "institutional timing not available; retail flow not available"
    lag = seq["lag_institutional_to_price_days"]; vis = "filing was public before the move" if seq["visible_before_move"] else "only visible now because of the filing lag"
    return f"institutional position change dated {lag} days before the move (period end {seq['institutional_period_end']}, filed {seq['institutional_filing_date']}); {vis}; retail flow not available"


def scan(cfg: dict, asof: str | None = None, positions: pd.DataFrame | None = None) -> dict:
    wh, u, px = open_warehouse(cfg); sig = cfg["signals"]
    feats = price_features(px, sig); last = feats.date.max()
    asof_ts = pd.Timestamp(asof) if asof else last
    avail = feats.date[feats.date <= asof_ts]
    if not len(avail): raise ValueError(f"no sessions on or before {asof_ts.date()}")
    used = avail.max(); today = feats[feats.date == used].copy()
    inst = institutional_flow(position_changes(wh.read("holdings_13f")), wh.read("filer_weights"))
    form4 = wh.read("insider_form4"); insider = insider_summary(form4, used, sig["insider_window_days"])
    earnings = wh.read("earnings_events"); calendar = wh.read("catalyst_calendar")
    model = load_composite(cfg)
    rules = stage_rules(today, inst, insider, earnings, calendar, cfg); ranked = stage_classifier(rules, today, model, cfg)
    names = dict(zip(u.symbol, u.name)); companies = []
    for _, r in ranked.iterrows():
        t = today[today.symbol == r.symbol].iloc[0]; hist = feats[(feats.symbol == r.symbol) & (feats.date <= used)].tail(2)
        p1 = float(hist.close.iloc[-1] / hist.close.iloc[-2] - 1) if len(hist) == 2 else None
        ir = latest_institutional(inst, r.symbol, used); ins = insider[insider.symbol == r.symbol] if len(insider) else None
        cat = catalyst_context(r.symbol, used, earnings, calendar, sig["catalyst_window_days"]); seq = sequence_lags(r.symbol, used, inst, None)
        companies.append({"symbol": r.symbol, "name": names.get(r.symbol, r.symbol), "priority": priority(t.anomaly_score, int(r.corroborating), cfg["priority"]),
                          "price_1d": p1, "price_5d": float(t.return_5d) if pd.notna(t.return_5d) else None, "volume_ratio": float(t.volume_ratio) if pd.notna(t.volume_ratio) else None,
                          "z_return": float(t.z_return) if pd.notna(t.z_return) else 0.0, "z_volume": float(t.z_volume) if pd.notna(t.z_volume) else 0.0, "anomaly_score": float(t.anomaly_score) if pd.notna(t.anomaly_score) else 0.0,
                          "institutional": _inst_text(ir, used), "insider": _insider_text(ins, sig["insider_window_days"]), "fundamental": "not available (deep read not run)",
                          "catalyst": _catalyst_text(cat), "sequence": _sequence_text(seq), "contradiction": "not run", "what_would_change": [], "rules": list(r.reasons), "corroborating": int(r.corroborating),
                          "signal_facts": {"asof": str(used.date()), "close": float(t.close), "return_1d": p1, "return_5d": float(t.return_5d) if pd.notna(t.return_5d) else None, "volume_ratio": float(t.volume_ratio) if pd.notna(t.volume_ratio) else None,
                                           "z_return": float(t.z_return) if pd.notna(t.z_return) else None, "z_volume": float(t.z_volume) if pd.notna(t.z_volume) else None, "anomaly_score": float(t.anomaly_score) if pd.notna(t.anomaly_score) else None,
                                           "return_60d": float(t[f"return_{sig['momentum_days']}d"]) if pd.notna(t[f"return_{sig['momentum_days']}d"]) else None, "institutional": _inst_text(ir, used), "insider": _insider_text(ins, sig["insider_window_days"]), "catalyst": _catalyst_text(cat), "sequence": seq}})
    n_anom = int((today.anomaly_score > sig["anomaly_threshold"]).sum())
    high_info = sum(1 for c in companies if c["anomaly_score"] > sig["anomaly_threshold"] and c["corroborating"] >= 1)
    cats = []
    if len(calendar):
        c = calendar[(pd.to_datetime(calendar.event_date) > used) & (pd.to_datetime(calendar.event_date) <= used + pd.Timedelta(days=14))]
        cats += [{"symbol": r.symbol, "kind": r.kind, "date": str(pd.Timestamp(r.event_date).date()), "source": r.source} for _, r in c.iterrows()]
    if len(earnings):
        for s, g in earnings.groupby("symbol"):
            est = pd.Timestamp(g.event_date.max()) + pd.Timedelta(days=91)
            if used < est <= used + pd.Timedelta(days=14) and not any(x["symbol"] == s for x in cats): cats.append({"symbol": s, "kind": "earnings (estimated: last + 91 days)", "date": str(est.date()), "source": "estimate from 8-K history"})
    port = None
    if positions is not None:
        from .portfolio import portfolio_review
        port = portfolio_review(positions, px, cfg["portfolio"], used)
    st = wh.status(); prov = str(px.source.iloc[0]) if len(px) else "n/a"
    tables = {k: (f"{v['rows']} rows" if v["rows"] else "empty") for k, v in st.items() if k in ("filings_index", "earnings_events", "insider_form4", "holdings_13f", "filing_text", "xbrl_quarterly", "catalyst_calendar")}
    return {"date": str(used.date()), "requested_date": str(asof_ts.date()), "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "assets_monitored": int(len(today)),
            "anomalies": n_anom, "high_information": high_info, "portfolio_risks": len(port["flags"]) if port else 0, "upcoming_catalysts": len(cats), "catalysts": cats, "portfolio": port,
            "corr_window": cfg["portfolio"]["corr_window"], "data_status": {"prices_through": str(last.date()), "prices_source": prov, "tables": tables},
            "not_available": sorted(PLACEHOLDERS), "llm_status": "not run", "companies": companies, "funnel": {"universe": int(len(today)), "rules": int(len(rules)), "classifier": int(len(ranked)), "deep_read": 0, "reasoning": 0,
            "rank_basis": (ranked.rank_basis.iloc[0] if len(ranked) else None), "rules_candidates": rules[["symbol", "anomaly_score", "n_reasons", "corroborating"]].to_dict("records")}}


def analyze(cfg: dict, R: dict, live: bool = False, provider=None) -> dict:
    wh = Warehouse(resolve(cfg, "warehouse")); texts = wh.read("filing_text"); xbrl = wh.read("xbrl_quarterly")
    provider = provider or make_provider(cfg["llm"], live=live); ledger = Ledger(prices=cfg["llm"]["prices_per_million"]); fn = cfg["funnel"]
    deep_n = rs_n = 0
    for i, c in enumerate(R["companies"]):
        if i >= fn["max_deep_read"]: break
        packet = build_packet(c["symbol"], R["date"], c["signal_facts"], texts, xbrl); d = deep_read(provider, cfg["llm"], packet, xbrl, ledger); deep_n += 1
        c["deep_read"] = {k: v for k, v in d.items() if k != "raw"}; c["deep_read"]["raw"] = d["raw"]
        if d["stub"]: c["fundamental"] = f"not available (stub provider; {packet['n_filing_docs']} filing documents in packet)"
        elif not d["consistency_rows"]: c["fundamental"] = f"deep read ran on {packet['n_filing_docs']} documents; no management figure could be paired with XBRL" + ("" if d["raw"].get("key_facts") else "")
        else:
            worst = min(d["consistency_rows"], key=lambda r: (r["consistency"] if r["consistency"] is not None else 9))
            c["fundamental"] = f"{len(d['consistency_rows'])} management figures checked against XBRL; lowest consistency {worst['consistency']:.2f} ({worst['metric']} {worst['period']}: claim {worst['management_claim']}, reality {worst['economic_reality']:.3f} [{worst['source']}])" + (f"; {d['flag']}" if d["flag"] else "")
        if i < fn["max_reasoning"]:
            e = contradiction_engine(provider, cfg["llm"], packet, d, ledger); rs_n += 1; c["contradiction_engine"] = e
            if e["stub"]: c["contradiction"] = "not run (stub provider)"
            else:
                c["contradiction"] = f"{e['top_contradiction']} (bull {e['bull_strength']}, bear {e['bear_strength']}, thesis_gap {e['thesis_gap']:+.2f})" if e["thesis_gap"] is not None else str(e["top_contradiction"])
                c["what_would_change"] = e["what_would_change_conclusion"]
    R["funnel"].update({"deep_read": deep_n, "reasoning": rs_n}); R["llm"] = {"provider": provider.name, "ledger": ledger.rows, "totals": ledger.totals(), "cost_estimate_for_budget": cost_estimate(fn["max_deep_read"], fn["max_reasoning"], cfg["llm"])}
    R["llm_status"] = f"{provider.name} ({deep_n} deep reads, {rs_n} contradiction runs" + ("; outputs are placeholders, no model called" if provider.name == "stub" else "") + ")"
    return R


def render(cfg: dict, R: dict, out_dir: str | None = None) -> tuple[str, list[str]]:
    text = render_radar(R); issues = language_check(text) if cfg["report"]["language_check"] else []
    out_dir = out_dir or resolve(cfg, "results"); os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, f"radar_{R['date']}.md"), "w").write(text + ("\n\nLANGUAGE CHECK FAILED:\n" + "\n".join(issues) if issues else "\n"))
    json.dump(R, open(os.path.join(out_dir, f"radar_{R['date']}.json"), "w"), indent=1, default=str)
    return text, issues


def load_positions(path: str | None = None, address: str | None = None, cfg: dict | None = None) -> pd.DataFrame | None:
    from .sources.hyperliquid import HLReadOnly, positions_to_weights
    if path:
        df = pd.read_csv(path); df.columns = [c.lower() for c in df.columns]
        if "weight" not in df.columns: df["weight"] = df.notional / df.notional.sum()
        if "side" not in df.columns: df["side"] = "long"
        return df[["symbol", "side", "weight"] + (["notional"] if "notional" in df.columns else [])]
    if address:
        hl = HLReadOnly(cfg["hyperliquid"]["base_url"]); return positions_to_weights(hl.positions(address))
    return None
