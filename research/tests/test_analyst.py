"""Phase 4 analyst tests: the article's worked examples reproduced exactly, causality of the signal engine, EDGAR and
Hyperliquid parsers on synthetic fixtures, provider gating (no key, no live flag -> no call), language check, funnel
budgets, composite fit, portfolio math, backtest statistics. Fixtures are synthetic; nothing here touches the network."""
import os, sys, json
import numpy as np, pandas as pd, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from analyst.config import load_config
from analyst.signals import price_features, position_changes, institutional_flow, insider_summary, sequence_lags, catalyst_context
from analyst.composite import probability, fit_composite, predict, evaluate, event_table
from analyst.portfolio import hhi, contributions, avg_pairwise, correlation_matrix, pca_first_share
from analyst.funnel import cost_estimate, stage_rules, stage_classifier
from analyst.report import language_check, priority, render_radar
from analyst.backtest import group_stats, clustered_bootstrap_diff, precision_recall, SPLIT_RANGES
from analyst.llm.base import make_provider, AnthropicProvider, StubProvider, Ledger, Completion, parse_json
from analyst.llm.stages import consistency, thesis_gap, economic_reality, deep_read, contradiction_engine, build_packet
from analyst.sources import edgar as E
from analyst.sources.hyperliquid import HLReadOnly, positions_to_weights

CFG = load_config()


def synth_prices(symbols=("AAA", "BBB"), n=420, seed=3):
    rng = np.random.default_rng(seed); out = []
    for i, s in enumerate(symbols):
        r = rng.normal(0.0005, 0.02, n); c = 100 * np.exp(np.cumsum(r)); v = rng.lognormal(15, 0.4, n)
        out.append(pd.DataFrame({"symbol": s, "date": pd.bdate_range("2024-01-01", periods=n), "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": v}))
    return pd.concat(out, ignore_index=True)


# ------------------------------------------------------------------ article worked examples
def test_article_layer2_examples_on_engine():
    sig = dict(CFG["signals"]); p = synth_prices(("AAA",), n=300)
    v = p.v.values.copy(); v[-21:-1] = 11.8; v[-1] = 34.0; p["v"] = v
    c = p.c.values.copy(); c[-6] = 142.0; c[-1] = 158.0; p["c"] = c
    f = price_features(p, sig).iloc[-1]
    assert abs(f.volume_ratio - 34 / 11.8) < 1e-9 and round(f.volume_ratio, 2) == 2.88          # 34 / 11.8 = 2.88
    assert abs(f.return_5d - (158 - 142) / 142) < 1e-12 and round(f.return_5d * 100, 1) == 11.3  # 142 -> 158 = 11.3%
    # z_return against the trailing distribution of return_5d, today excluded (the definition in the protocol)
    ff = price_features(p, sig); r5 = ff.return_5d; hist = r5.iloc[-1 - sig["hist_window"]: -1].dropna()
    assert abs(f.z_return - (f.return_5d - hist.mean()) / hist.std()) < 1e-9
    assert abs(f.anomaly_score - (abs(f.z_return) + abs(f.z_volume))) < 1e-12
    assert abs((0.113 - 0.008) / 0.034 - 3.08) < 0.01                                            # the article's arithmetic


def test_article_institutional_and_consistency_examples():
    h = pd.DataFrame([{"filer_cik": 1, "symbol": "X", "period_of_report": "2026-03-31", "filing_date": "2026-05-15", "shares": 2.4e6},
                      {"filer_cik": 1, "symbol": "X", "period_of_report": "2026-06-30", "filing_date": "2026-08-14", "shares": 3.1e6}])
    h["period_of_report"] = pd.to_datetime(h.period_of_report); h["filing_date"] = pd.to_datetime(h.filing_date)
    ch = position_changes(h); assert len(ch) == 1 and round(ch.position_change_pct.iloc[0] * 100, 1) == 29.2      # 2.4M -> 3.1M = +29.2%
    fl = institutional_flow(ch); assert len(fl) == 1 and abs(fl.institutional_flow.iloc[0] - ch.position_change_pct.iloc[0]) < 1e-12
    w = pd.DataFrame([{"filer_cik": 1, "weight": 0.5}]); fl2 = institutional_flow(ch, w); assert abs(fl2.institutional_flow.iloc[0] - ch.position_change_pct.iloc[0]) < 1e-12  # single filer normalises to 1
    assert consistency(0.4, 0.8) == 0.5 and consistency(0.0, 0.0) == 1.0 and consistency(None, 0.3) is None   # 1 - |0.4-0.8|/0.8 = 0.5
    assert abs(thesis_gap(0.7, 0.4) - 0.3) < 1e-12 and thesis_gap(None, 0.4) is None
    seq = sequence_lags("X", pd.Timestamp("2026-09-10"), fl)
    assert seq["lag_institutional_to_price_days"] == (pd.Timestamp("2026-09-10") - pd.Timestamp("2026-06-30")).days and seq["visible_before_move"] is True
    seq2 = sequence_lags("X", pd.Timestamp("2026-07-15"), fl); assert seq2["visible_before_move"] is False   # filed 2026-08-14, after the move


def test_article_portfolio_and_cost_examples():
    assert abs(hhi([0.05] * 20) - 0.05) < 1e-12                                                    # 20 x 0.05^2
    assert round(hhi([0.5] + [0.5 / 19] * 19), 3) == 0.263                                          # 0.25 + 0.013
    con = contributions({"NVDA": 0.40, "MSFT": 0.25, "AAPL": 0.20, "ETH": 0.15}, {"NVDA": 0.10, "MSFT": 0.0, "AAPL": 0.0, "ETH": 0.0})
    assert abs(con[con.symbol == "NVDA"].contribution.iloc[0] - 0.04) < 1e-12                       # 0.40 x 0.10 = +4pp
    m = pd.DataFrame([[1, .71, .68, .75], [.71, 1, .64, .69], [.68, .64, 1, .61], [.75, .69, .61, 1]], index=list("ABCD"), columns=list("ABCD"))
    assert round(avg_pairwise(m), 2) == 0.68                                                        # ~0.68, one factor bet
    ce = cost_estimate(150, 40, CFG["llm"]); ref = ce["article_reference"]
    assert abs(ref["astra_per_name_usd"] - 6.0) < 1e-9 and abs(ref["astra_40_names_usd"] - 240) < 1e-9 and abs(ref["astra_10000_names_per_day_usd"] - 60_000) < 1e-6
    assert abs(ref["kimi_150_names_usd"] - 153) < 1e-6                                              # 150 x (0.9 + 0.12)
    assert ce["deep_read"]["total_usd"] is None                                                     # Claude models unpriced until the user fills them in
    b = {"b0": 0.0, "institutional": 0.41, "fundamental": 0.33, "momentum": 0.19, "options": 0.11, "retail": -0.07}
    assert probability(b) == 0.5 and probability(b, institutional=1) > probability(b) > probability(b, retail=1)
    assert abs(74 / (74 + 126) - 0.37) < 1e-12                                                       # precision 37% example


# ------------------------------------------------------------------ signal engine properties
def test_features_are_causal_and_flags_consistent():
    sig = dict(CFG["signals"]); p = synth_prices(); full = price_features(p, sig)
    t = full.date.iloc[300]; trunc = price_features(p[p.date <= t], sig)
    cols = [c for c in full.columns if not c.startswith("fwd_") and c != "label"]
    a = full[full.date == t].sort_values("symbol")[cols].reset_index(drop=True); b = trunc[trunc.date == t].sort_values("symbol")[cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    ev = full[full.event]; assert (ev.anomaly_score > sig["anomaly_threshold"]).all() and full[full.quiet].anomaly_score.lt(sig["quiet_threshold"]).all()
    assert full.first_trigger.sum() <= full.event.sum() and (~(full.event & full.quiet)).all()
    lh = sig["label_horizon"]; ok = full.dropna(subset=["label"]); assert ((ok[f"fwd_{lh}"] >= sig["label_pct"]).astype(float) == ok.label).all()
    for s, d in full.groupby("symbol"):
        d = d.reset_index(drop=True); tr = d.index[d.first_trigger]
        for i in tr: assert not d.event.iloc[max(0, i - sig["first_trigger_gap"]): i].any()


def test_insider_summary_and_catalyst_context():
    f4 = pd.DataFrame([{"symbol": "X", "transaction_date": "2026-09-01", "filing_date": "2026-09-03", "code": "P", "shares": 1000, "price": 10.0, "insider": "DOE JANE"},
                       {"symbol": "X", "transaction_date": "2026-08-20", "filing_date": "2026-08-22", "code": "S", "shares": 300, "price": 11.0, "insider": "ROE RAY"},
                       {"symbol": "X", "transaction_date": "2026-09-05", "filing_date": "2026-09-30", "code": "P", "shares": 5000, "price": 9.0, "insider": "LATE FILER"}])
    s = insider_summary(f4, pd.Timestamp("2026-09-10"), 90)
    assert len(s) == 1 and s.buys.iloc[0] == 1 and s.sells.iloc[0] == 1 and s.net_shares.iloc[0] == 700     # the late filing is not visible yet
    e = pd.DataFrame([{"symbol": "X", "event_date": "2026-09-08", "filing_date": "2026-09-08"}]); e["event_date"] = pd.to_datetime(e.event_date); e["filing_date"] = pd.to_datetime(e.filing_date)
    c = catalyst_context("X", pd.Timestamp("2026-09-10"), e, None, 3); assert c["earnings_within_window"] and c["days_since_earnings"] == 2 and c["next_estimated"] == "2026-12-08"


# ------------------------------------------------------------------ EDGAR parsers on synthetic fixtures
FORM4 = b"""<?xml version="1.0"?><ownershipDocument><issuer><issuerTradingSymbol>SYN</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle><transactionDate><value>2026-09-10</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>150.5</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts><postTransactionAmounts><sharesOwnedFollowingTransaction><value>51000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
INFOTABLE = b"""<?xml version="1.0"?><informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
<infoTable><nameOfIssuer>SYNTHETIC SEMI CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>000000AA1</cusip><value>465000</value><shrsOrPrnAmt><sshPrnamt>3100000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
<infoTable><nameOfIssuer>OTHER CO INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>000000BB2</cusip><value>1000</value><shrsOrPrnAmt><sshPrnamt>2400000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
</informationTable>"""


def test_edgar_parsers():
    tx = E.parse_form4_xml(FORM4); assert len(tx) == 1 and tx[0]["code"] == "P" and tx[0]["shares"] == 1000 and tx[0]["price"] == 150.5 and tx[0]["shares_after"] == 51000
    assert tx[0]["insider"] == "DOE JANE" and "CFO" in tx[0]["relationship"] and "Director" in tx[0]["relationship"]
    it = E.parse_13f_infotable(INFOTABLE); assert len(it) == 2 and it[0]["shares"] == 3100000 and it[0]["cusip"] == "000000AA1"
    uni = pd.DataFrame([{"symbol": "SYN", "name": "Synthetic Semi"}, {"symbol": "OTH", "name": "Other"}])
    assert E.match_issuer("SYNTHETIC SEMI CORP", uni) == "SYN" and E.match_issuer("OTHER CO INC", uni) == "OTH" and E.match_issuer("UNRELATED PLC", uni) is None
    sub = {"cik": "1234", "filings": {"recent": {"accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3"], "filingDate": ["2026-08-01", "2026-08-20", "2026-09-01"], "reportDate": ["2026-07-30", "2026-06-30", "2026-08-30"],
                                                "form": ["8-K", "10-Q", "8-K"], "primaryDocument": ["a.htm", "q.htm", "b.htm"], "items": ["2.02,9.01", "", "7.01"]}}}
    idx = E.parse_submissions(sub); idx["symbol"] = "SYN"; ev = E.earnings_events_from_index(idx)
    assert len(ev) == 1 and str(ev.event_date.iloc[0].date()) == "2026-07-30" and str(ev.filing_date.iloc[0].date()) == "2026-08-01"
    assert E.pick_form4_xml(["xslF345X05/wk-form4_1.xml", "wk-form4_1.xml", "R1.xml"], "xslF345X05/wk-form4_1.xml") == "wk-form4_1.xml"
    assert E.pick_13f_table(["primary_doc.xml", "infotable.xml"], "primary_doc.xml") == "infotable.xml" and E.pick_exhibit_99(["x.htm", "ex99-1.htm", "ex99-2.htm"]) == "ex99-1.htm"
    html = "<html><body><p>Table of Contents</p><p>Item 2. Management's Discussion and Analysis ... 12</p><p>Item 3. Quantitative and Qualitative Disclosures About Market Risk ... 30</p>" \
           "<h2>Item 2. Management's Discussion and Analysis of Financial Condition</h2><p>Revenue grew 31% year over year; demand remained strong.</p><script>x=1</script>" \
           "<h2>Item 3. Quantitative and Qualitative Disclosures About Market Risk</h2><p>irrelevant</p></body></html>"
    t = E.html_to_text(html); m = E.extract_mdna(t); assert "Revenue grew 31%" in m and "irrelevant" not in m and "x=1" not in m and "... 12" not in m
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [{"frame": f"CY2025Q{q}", "end": f"2025-0{3*q}-30", "val": 100 + q, "form": "10-Q", "filed": "2025-01-01"} for q in (1, 2, 3, 4)] + [{"frame": "CY2025", "val": 999, "form": "10-K"}]}},
                                   "NetIncomeLoss": {"units": {"USD": [{"frame": "CY2025Q1", "val": 10, "form": "10-Q"}]}}}}}
    x = E.parse_companyfacts(facts); assert set(x.metric) == {"revenue"} and len(x) == 4 and "CY2025" not in set(x.frame)   # net income has < 4 quarterly frames


class FakeResp:
    def __init__(self, obj=None, content=b"", status=200): self._obj = obj; self.content = content; self.status_code = status; self.text = content.decode() if content else json.dumps(obj)
    def json(self): return self._obj
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f"http {self.status_code}")


def test_edgar_client_gating_and_injected_network():
    with pytest.raises(RuntimeError): E.EdgarClient("")
    with pytest.raises(RuntimeError): E.EdgarClient("no-contact-here")
    calls = []
    def get(url, headers=None, timeout=None):
        calls.append((url, headers["User-Agent"]))
        if url.endswith("company_tickers.json"): return FakeResp({"0": {"cik_str": 1, "ticker": "SYN", "title": "Synthetic"}})
        if url.endswith("/index.json"): return FakeResp({"directory": {"item": [{"name": "wk-form4_1.xml"}]}})
        if url.endswith(".xml"): return FakeResp(content=FORM4)
        return FakeResp(status=404)
    sleeps = []; c = E.EdgarClient("Test User test@example.com", get=get, sleep=sleeps.append, max_rps=8)
    assert c.company_tickers() == {"SYN": 1} and calls[0][1] == "Test User test@example.com"
    tx = c.form4(1, "0001-26-1", "xslF345X05/wk-form4_1.xml"); assert len(tx) == 1 and c.requests_made == 3
    assert "https://www.sec.gov/Archives/edgar/data/1/000126" in calls[1][0]


def test_hyperliquid_readonly_and_weights():
    now = 1_800_000_000_000
    def post(url, json=None, timeout=None):
        assert url.endswith("/info") and "/exchange" not in url
        if json["type"] == "candleSnapshot":
            return FakeResp([{"t": now - 3 * 86_400_000, "T": now - 2 * 86_400_000 - 1, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10"},
                             {"t": now - 86_400_000, "T": now + 86_400_000, "o": "1", "h": "2", "l": "0.5", "c": "1.6", "v": "11"}])      # second candle not complete
        if json["type"] == "clearinghouseState":
            if json.get("dex") == "xyz": return FakeResp({"marginSummary": {"accountValue": "100"}, "assetPositions": [{"position": {"coin": "xyz:NVDA", "szi": "0.5", "entryPx": "180", "positionValue": "90", "unrealizedPnl": "1", "leverage": {"value": 2}}}]})
            return FakeResp({"marginSummary": {"accountValue": "100"}, "assetPositions": [{"position": {"coin": "ETH", "szi": "-0.01", "entryPx": "3000", "positionValue": "30", "unrealizedPnl": "0", "leverage": {"value": 3}}}]})
        raise AssertionError(json["type"])
    hl = HLReadOnly("https://example.invalid", post=post)
    d = hl.daily_candles("xyz:NVDA", 5, now_ms=now); assert len(d) == 1 and d.symbol.iloc[0] == "NVDA" and d.c.iloc[0] == 1.5
    w = positions_to_weights(hl.positions("0xabc")); assert set(w.symbol) == {"NVDA", "ETH"} and abs(w.weight.sum() - 1) < 1e-12 and w[w.symbol == "ETH"].side.iloc[0] == "short"
    src = open(os.path.join(os.path.dirname(__file__), "..", "analyst", "sources", "hyperliquid.py")).read(); assert '"/exchange"' not in src and "signing" not in src.replace("no signing", "")


# ------------------------------------------------------------------ LLM layer gating and stages
def test_provider_gating_and_key_handling(monkeypatch):
    assert isinstance(make_provider({"provider": "anthropic", "api_key_env": "X_KEY"}, live=False), StubProvider)      # no live flag -> stub, always
    with pytest.raises(RuntimeError): AnthropicProvider("X_KEY", live=False)
    monkeypatch.delenv("X_KEY", raising=False)
    with pytest.raises(RuntimeError): AnthropicProvider("X_KEY", live=True)
    monkeypatch.setenv("X_KEY", "fake-test-key-value"); seen = {}
    def post(url, headers=None, json=None, timeout=None):
        seen.update({"url": url, "headers": headers, "body": json}); return FakeResp({"model": "m", "content": [{"type": "text", "text": "{\"thesis\": \"t\"}"}], "usage": {"input_tokens": 10, "output_tokens": 5}})
    p = AnthropicProvider("X_KEY", live=True, post=post); c = p.complete("sys", "user", "claude-x", 100)
    assert c.input_tokens == 10 and c.output_tokens == 5 and seen["headers"]["x-api-key"] == "fake-test-key-value" and "fake-test-key" not in repr(p) and seen["body"]["model"] == "claude-x"
    assert parse_json("```json\n{\"a\": 1}\n```") == {"a": 1} and parse_json("noise {\"a\": 2} tail") == {"a": 2} and parse_json("garbage")["parse_error"]


class FakeAnalyst:
    """Deterministic non-stub provider returning crafted JSON per schema."""
    name = "fake"
    def complete(self, system, user, model, max_tokens, temperature=0.0):
        schema = user.split("OUTPUT_SCHEMA:")[1].split()[0]
        out = {"deep_read": {"quarters": [{"period": "CY2026Q2", "management_summary": "s", "sources": ["S2"], "claims": [{"metric": "revenue_yoy_growth", "management_claim_value": 0.31, "quote": "grew 31%", "source": "S2"}]}], "language_unchanged_numbers_moved": [], "key_facts": [{"fact": "f", "source": "S2"}], "not_answerable": []},
               "bull": {"thesis": "b", "claims": [], "strength": 0.9, "assumptions": []}, "bear": {"thesis": "r", "claims": [], "strength": 0.9, "assumptions": []},
               "arbiter": {"claims_checked": [], "checklist": [], "bull_strength": 0.6, "bear_strength": 0.4, "top_contradiction": "valuation expanded faster than revisions [S3]", "what_would_change_conclusion": ["a 10-Q showing margin compression"]}}[schema]
        return Completion(text=json.dumps(out), model=model, provider="fake", input_tokens=len(user) // 4, output_tokens=50)


def test_stages_compute_consistency_and_gap_in_code():
    xbrl = pd.DataFrame([{"symbol": "SYN", "metric": "revenue", "frame": "CY2025Q2", "val": 142.0}, {"symbol": "SYN", "metric": "revenue", "frame": "CY2026Q2", "val": 158.0}, {"symbol": "SYN", "metric": "net_income", "frame": "CY2026Q2", "val": 15.8}])
    assert abs(economic_reality(xbrl, "revenue_yoy_growth", "CY2026Q2") - (158 / 142 - 1)) < 1e-12 and abs(economic_reality(xbrl, "net_margin", "CY2026Q2") - 0.1) < 1e-12
    texts = pd.DataFrame([{"symbol": "SYN", "form": "10-Q", "section": "MD&A", "filing_date": pd.Timestamp("2026-08-01"), "report_date": pd.Timestamp("2026-06-30"), "accession": "a", "text": "Revenue grew 31%"}])
    pk = build_packet("SYN", pd.Timestamp("2026-09-23"), {"return_5d": 0.1}, texts, xbrl); assert pk["sources"][0]["id"] == "S1" and pk["n_filing_docs"] == 1 and any("xbrl" in s["type"] for s in pk["sources"])
    led = Ledger(prices={}); d = deep_read(FakeAnalyst(), CFG["llm"], pk, xbrl, led)
    assert not d["stub"] and len(d["consistency_rows"]) == 1
    er = 158 / 142 - 1; assert abs(d["consistency_rows"][0]["consistency"] - (1 - abs(er - 0.31) / max(abs(er), 0.31))) < 1e-12 and d["flag"] is not None   # 0.36 < 0.6 -> "dig further"
    e = contradiction_engine(FakeAnalyst(), CFG["llm"], pk, d, led); assert abs(e["thesis_gap"] - 0.2) < 1e-12 and "valuation" in e["top_contradiction"] and len(led.rows) == 4
    s = deep_read(StubProvider(), CFG["llm"], pk, xbrl, Ledger()); assert s["stub"] and s["consistency_rows"] == [] and s["min_consistency"] is None
    se = contradiction_engine(StubProvider(), CFG["llm"], pk, None, Ledger()); assert se["stub"] and se["thesis_gap"] is None


# ------------------------------------------------------------------ report language, priority, funnel budgets
def test_language_check_and_priority():
    assert language_check("We recommend buying NVDA. Score: 87/100. Strong buy. BUY.") and language_check("upside of 20%")
    assert language_check("insider buying continued; institutional buying preceded the move; 2 purchases, 1 sale") == []
    assert priority(3.5, 1, CFG["priority"]) == "high" and priority(3.5, 0, CFG["priority"]) == "medium" and priority(1.0, 3, CFG["priority"]) == "low"


def test_funnel_budgets_and_rank_basis():
    cfg = json.loads(json.dumps(CFG)); cfg["funnel"].update({"max_rules": 5, "max_classifier": 3})
    today = pd.DataFrame({"symbol": [f"S{i}" for i in range(10)], "date": pd.Timestamp("2026-09-23"), "anomaly_score": np.linspace(1.5, 4.0, 10), "z_return": np.linspace(-1, 3, 10), "z_volume": 0.5, "z_momentum": 0.0})
    r = stage_rules(today, None, None, None, None, cfg); assert len(r) == 5 and r.anomaly_score.is_monotonic_decreasing and r.anomaly_score.min() > 2
    c = stage_classifier(r, today, None, cfg); assert len(c) == 3 and c.rank_basis.iloc[0] == "anomaly_score"
    model = {"features": ["price_anomaly"], "coef": {"b0": 0.0, "price_anomaly": 1.0}, "standardization": {"mean": {"price_anomaly": 0.0}, "std": {"price_anomaly": 1.0}}}
    c2 = stage_classifier(r, today, model, cfg); assert len(c2) == 3 and "composite" in c2.rank_basis.iloc[0] and c2.rank_value.between(0, 1).all()


def test_radar_end_to_end_on_repo_data_passes_language_check():
    from analyst import pipeline
    R = pipeline.scan(CFG, "2026-09-23", pd.DataFrame([{"symbol": "NVDA", "side": "long", "weight": 0.6}, {"symbol": "AMD", "side": "long", "weight": 0.4}]))
    assert R["assets_monitored"] == 59 and R["funnel"]["classifier"] <= CFG["funnel"]["max_classifier"] and R["portfolio"]["n_positions"] == 2
    R = pipeline.analyze(CFG, R, live=False); assert R["llm"]["provider"] == "stub" and all(r["stub"] for r in R["llm"]["ledger"])
    text = render_radar(R); assert language_check(text) == [] and "MARKET RADAR" in text and "not available" in text


# ------------------------------------------------------------------ composite, portfolio, backtest statistics
def test_composite_fit_recovers_planted_signal():
    rng = np.random.default_rng(0); n = 800; x = rng.normal(size=(n, 3)); p = 1 / (1 + np.exp(-(-0.3 + 1.2 * x[:, 0]))); y = (rng.random(n) < p).astype(float)
    ev = pd.DataFrame({"price_anomaly": x[:, 0], "volume_anomaly": x[:, 1], "momentum": x[:, 2], "institutional": np.nan, "fundamental": np.nan, "options": np.nan, "retail": np.nan, "label": y})
    m = fit_composite(ev); assert m["coef"]["price_anomaly"] > 0.6 and m["p"]["price_anomaly"] < 0.001 and set(m["unavailable"]) == {"institutional", "fundamental", "options", "retail"}
    e = evaluate(predict(m, ev), y); assert e["auc"] > 0.7 and 0 < e["precision"] <= 1 and len(e["calibration"]) == 10


def test_portfolio_and_backtest_stats():
    rng = np.random.default_rng(1); f = rng.normal(size=200); r = pd.DataFrame({"A": f + rng.normal(scale=0.3, size=200), "B": f + rng.normal(scale=0.3, size=200), "C": rng.normal(size=200)})
    cm = correlation_matrix(r); assert np.allclose(np.diag(cm.values), 1) and cm.loc["A", "B"] > 0.8 and abs(cm.loc["A", "C"]) < 0.3 and 0.5 < pca_first_share(r) < 0.8
    g = group_stats(pd.Series([-0.1, 0.0, 0.05, 0.2]), 0.05); assert g["n"] == 4 and g["share_losers"] == 0.25 and g["share_ge_5pct"] == 0.5
    a = pd.DataFrame({"date": pd.Timestamp("2026-01-01") + pd.to_timedelta(np.repeat(np.arange(50), 2), "D"), "x": 0.03 + rng.normal(scale=0.01, size=100)})
    b = pd.DataFrame({"date": pd.Timestamp("2026-01-01") + pd.to_timedelta(np.repeat(np.arange(50), 4), "D"), "x": rng.normal(scale=0.01, size=200)})
    d = clustered_bootstrap_diff(a, b, "x", n_boot=300); assert d["ci_lo"] <= d["diff"] <= d["ci_hi"] and d["ci_lo"] > 0 and d["clusters_a"] == 50
    dates = pd.bdate_range("2025-10-01", periods=100)                                             # inside the val range
    ft = pd.DataFrame({"symbol": np.repeat(["S0", "S1", "S2", "S3"], 100), "date": np.tile(dates, 4), "first_trigger": [True] * 200 + [False] * 200, "event": [True] * 200 + [False] * 200, "direction": 1,
                       "label": [1.0] * 74 + [0.0] * 126 + [1.0] * 50 + [0.0] * 150})
    pr = precision_recall(ft, "val"); assert abs(pr["precision"] - 0.37) < 1e-12 and pr["n_strong"] == 200 and abs(pr["base_rate"] - 124 / 400) < 1e-12
    assert set(SPLIT_RANGES) == {"dev", "val"}                                                      # the holdout is never evaluated in phase 4
