"""python -m analyst.cli <command>. Commands: status | ingest | scan | analyze | report | run | backtest | portfolio.
Read-only everywhere. Live LLM calls need --live-llm and your key in the env var named in config (default ANTHROPIC_API_KEY)."""
from __future__ import annotations
import os, sys, json, argparse
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from analyst.config import load_config, resolve
from analyst import pipeline


def cmd_status(cfg, a):
    wh, u, px = pipeline.open_warehouse(cfg); st = wh.status()
    print(f"universe: {len(u)} names; ciks known: {int(u.cik.notna().sum())}")
    for k, v in st.items(): print(f"{k:20s} {v['rows']:7d} rows" + (f"  {v.get('min','')} -> {v.get('max','')}" if v.get("min") else "") + ("  (placeholder: no free source)" if v["placeholder"] else ""))


def cmd_ingest(cfg, a):
    wh, u, px = pipeline.open_warehouse(cfg)
    if a.source == "offline":
        from analyst.warehouse import ingest_offline_prices; print("prices rows:", ingest_offline_prices(wh, cfg, u)); return
    if a.source == "hyperliquid":
        from analyst.sources.hyperliquid import HLReadOnly; from analyst.warehouse import now_utc
        hl = HLReadOnly(cfg["hyperliquid"]["base_url"]); frames = []
        for _, r in u.iterrows():
            try: d = hl.daily_candles(r.coin, cfg["hyperliquid"]["candle_days"]); d["source"] = f"hyperliquid candleSnapshot 1d {r.coin}"; d["fetched_at"] = now_utc(); frames.append(d)
            except Exception as e: print("  skip", r.coin, e)
        if frames: print("prices rows:", wh.write("prices_daily", pd.concat(frames), mode="append"))
        return
    if a.source == "edgar":
        from analyst.sources import edgar as E
        ua = os.environ.get(cfg["edgar"]["user_agent_env"])
        client = E.EdgarClient(ua, max_rps=cfg["edgar"]["max_requests_per_sec"], data_url=cfg["edgar"]["data_url"], www_url=cfg["edgar"]["www_url"])
        what = a.what; errs = []
        if what in ("all", "ciks"): u = E.ingest_ciks(wh, client, u); print("ciks:", int(u.cik.notna().sum()))
        u = pipeline.load_universe(cfg, wh) if what != "ciks" else u
        if what in ("all", "index"): n, e = E.ingest_filings_index(wh, client, u, cfg["edgar"]["forms_index"]); errs += e; print("filings_index rows:", n, "earnings_events:", len(wh.read("earnings_events")))
        if what in ("all", "form4"): n, e = E.ingest_form4(wh, client, u, cfg["edgar"]["form4_lookback_days"]); errs += e; print("insider_form4 rows:", n)
        if what in ("all", "13f"):
            if not cfg["edgar"]["filers"]: print("13f: no filers configured (edgar.filers = [CIK, ...] in your config); skipped")
            else: n, e = E.ingest_13f(wh, client, cfg["edgar"]["filers"], u); errs += e; print("holdings_13f rows:", n)
        if what in ("all", "xbrl"): n, e = E.ingest_xbrl(wh, client, u); errs += e; print("xbrl_quarterly rows:", n)
        if what in ("all", "text"): n, e = E.ingest_filing_text(wh, client, u, cfg["edgar"]["text_lookback_quarters"], cfg["edgar"]["max_text_chars"]); errs += e; print("filing_text rows:", n)
        print(f"requests made: {client.requests_made}; errors: {len(errs)}")
        if errs: json.dump(errs, open(os.path.join(resolve(cfg, "results"), "ingest_errors.json"), "w"), indent=1); print("  see results/phase4/ingest_errors.json")


def _positions(cfg, a): return pipeline.load_positions(getattr(a, "positions", None), getattr(a, "address", None) or os.environ.get(cfg["hyperliquid"]["account_address_env"]) if getattr(a, "use_env_address", False) else getattr(a, "address", None), cfg)


def cmd_scan(cfg, a):
    R = pipeline.scan(cfg, a.date, _positions(cfg, a)); out = resolve(cfg, "results"); os.makedirs(out, exist_ok=True)
    json.dump(R, open(os.path.join(out, f"scan_{R['date']}.json"), "w"), indent=1, default=str)
    print(f"scan {R['date']}: {R['assets_monitored']} monitored, {R['anomalies']} anomalies, funnel rules->{R['funnel']['rules']} classifier->{R['funnel']['classifier']} ({R['funnel']['rank_basis']})")
    for c in R["companies"]: print(f"  {c['symbol']:6s} {c['priority']:6s} anomaly {c['anomaly_score']:.2f} 5d {c['price_5d']*100:+.1f}%  {'; '.join(c['rules'])}")
    return R


def cmd_run(cfg, a):
    R = pipeline.scan(cfg, a.date, _positions(cfg, a)); R = pipeline.analyze(cfg, R, live=a.live_llm); text, issues = pipeline.render(cfg, R)
    print(text)
    if issues: print("\nLANGUAGE CHECK FAILED:", *issues, sep="\n  "); sys.exit(2)
    print(f"\nwritten: results/phase4/radar_{R['date']}.md / .json; LLM totals: {json.dumps(R['llm']['totals'])}")


def cmd_backtest(cfg, a):
    from analyst.signals import price_features; from analyst.backtest import run_backtest
    wh, u, px = pipeline.open_warehouse(cfg); f = price_features(px, cfg["signals"]); res = run_backtest(f, cfg, resolve(cfg, "results"))
    for s in ("dev", "val"):
        d = res["event_study"][f"{s}_first_trigger"]["A_positive_minus_B_20"]; pr = res["precision_recall"][s]
        print(f"{s}: A_pos - B (20 sessions) = {d['diff']*100:+.2f}pp CI [{d['ci_lo']*100:+.2f}, {d['ci_hi']*100:+.2f}]; strong-flag precision {pr['precision']:.3f} vs base {pr['base_rate']:.3f} (lift {pr['lift']:.2f}, n={pr['n_strong']})")
    c = res["composite"]; print("composite dev fit:", {k: round(v, 3) for k, v in c["fit_dev"]["coef"].items()}, "p:", {k: round(v, 3) for k, v in c["fit_dev"]["p"].items()}, "unavailable:", c["fit_dev"]["unavailable"])
    print("composite val:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in c["eval_val"].items() if k != "calibration"})


def cmd_portfolio(cfg, a):
    from analyst.portfolio import portfolio_review
    wh, u, px = pipeline.open_warehouse(cfg); pos = _positions(cfg, a)
    if pos is None or not len(pos): print("no positions: pass --positions file.csv (symbol,side,notional) or --address 0x... (read-only)"); return
    R = portfolio_review(pos, px, cfg["portfolio"], pd.Timestamp(a.date) if a.date else px.date.max()); print(json.dumps(R, indent=1, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(prog="analyst", description=__doc__); p.add_argument("--config", default=None); sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    i = sub.add_parser("ingest"); i.add_argument("--source", choices=["offline", "hyperliquid", "edgar"], default="offline"); i.add_argument("--what", choices=["all", "ciks", "index", "form4", "13f", "xbrl", "text"], default="all")
    for name in ("scan", "run", "analyze", "report", "portfolio"):
        s = sub.add_parser(name); s.add_argument("--date", default=None); s.add_argument("--positions", default=None); s.add_argument("--address", default=None); s.add_argument("--live-llm", action="store_true")
    sub.add_parser("backtest")
    a = p.parse_args(argv); cfg = load_config(a.config)
    {"status": cmd_status, "ingest": cmd_ingest, "scan": cmd_scan, "run": cmd_run, "analyze": cmd_run, "report": cmd_run, "backtest": cmd_backtest, "portfolio": cmd_portfolio}[a.cmd](cfg, a)


if __name__ == "__main__": main()
