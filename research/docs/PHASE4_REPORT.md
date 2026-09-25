# Phase 4 report: the AI market analyst, built and checked (2026-09-25)

Source article: `docs/PHASE4_ARTICLE_DIGEST.md`. Protocol: `docs/PROTOCOL_PHASE4.md` (written before the evaluation ran).
User decisions: article-faithful analyst report; Claude for both reasoning roles; free data only (Hyperliquid read-only
+ SEC EDGAR); the 59 Hyperliquid-listed stocks. Code: `research/analyst/`. Tests: `tests/test_analyst.py`.

## 1. What was built

| article layer | implementation | state in the sandbox |
|---|---|---|
| Data warehouse | `analyst/warehouse.py`: 15 typed parquet tables with `source` and filing dates; 4 are placeholders (estimate revisions, options, retail flow, news) that print "not available" | prices for 59 names loaded from the committed panel; every filings table empty (EDGAR unreachable here) |
| Sources | `sources/hyperliquid.py` (POST /info only: candles, mids, clearinghouseState), `sources/edgar.py` (company tickers, submissions index, Form 4 XML, 13F information table, 8-K item 2.02 earnings events, XBRL companyfacts, MD&A and EX-99.1 text) | parsers unit-tested on synthetic fixtures; live fetch runs only on the user's machine |
| Signal engine | `analyst/signals.py`: `volume_ratio`, `return_5d`, `z_return`, `z_volume`, `anomaly_score`, `position_change_pct`, `institutional_flow` (equal or accuracy weights), insider net buying, `lag_institutional_to_price` with the "visible before the move?" check, catalyst context, forward returns and the 5%-in-20-sessions label | runs on the full panel in 0.6 s |
| Composite | `analyst/composite.py`: the five-term logistic formula, statsmodels fit with p-values, terms without data fixed at 0 and listed | fitted on dev, evaluated once on val (section 3) |
| Kimi-role deep read | `llm/stages.py: deep_read`: eight-quarter packet (MD&A, press releases, XBRL table) with source ids; the model extracts management figures; `consistency` computed in code from XBRL | stub offline; live with `--live-llm` |
| Astra-role contradiction engine | `contradiction_engine`: Agent A bull, Agent B bear, Agent C arbiter with the seven checklist questions; `thesis_gap` from the arbiter's checked strengths | stub offline; live with `--live-llm` |
| Funnel | `analyst/funnel.py`: rules -> composite rank (internal only) -> deep read -> reasoning -> human, budgets 20/8/5/3 by default; cost estimate reproducing the article's arithmetic | ran: 59 -> 14 -> 8 -> 5 -> 3 on 2026-09-23 |
| Portfolio math | `analyst/portfolio.py`: contribution, HHI, pairwise correlation, first principal component share, flags | ran on the article's example basket with real prices |
| Report | `analyst/report.py`: MARKET RADAR layout, categorical priority, language check that rejects recommendation wording and scores | `results/phase4/radar_2026-09-23.md` |
| Permissions | market data, portfolio, research: read. Orders: no code exists in the package. Transfers, settings: no | by construction |

Provider gating: `make_provider` returns the stub unless `live=True`; `AnthropicProvider` refuses to construct without the
live flag and without the key in the env var; the key is held on the instance, never printed (`repr` redacts it). Tests
cover both refusals and the header the key travels in.

## 2. What the sandbox could and could not do

- Could: build and test every layer; run the deterministic layers and the funnel on real prices; run the LLM stages
  through the stub so the whole pipeline executes end to end and writes a radar.
- Could not: reach SEC EDGAR or Hyperliquid (network policy), or call any model (no key here, and paid calls are out of
  scope). So the radar's institutional, insider, catalyst, fundamental and contradiction fields all read "not
  available", honestly. They fill in on the user's machine after `ingest --source edgar` and `run --live-llm`.

## 3. "Does any of this mean anything?" (protocol section: statistics)

Universe: 59 names, sessions 2024-09-24 to 2026-09-23. dev = 14,063 sessions, val = 7,316. Holdout not evaluated.
Base rate of a 5%+ gain within 20 sessions: **42.5% in dev, 35.1% in val** (the article assumes 15 to 20% for the
broad market; this universe is high-beta and the period was a strong tape, so every precision number must be read
against these rates, not the article's).

### Event study (first-trigger events; A = anomaly_score > 2, B = quiet < 1)

| split | horizon | A positive n | A pos mean | A pos median | share losers | B quiet n | B mean | B median | A pos − B | 95% CI (date-clustered) |
|---|---|---|---|---|---|---|---|---|---|---|
| dev | 5 | 283 | +0.9% | +0.5% | 46% | 3,833 | +1.6% | +1.1% | −0.7pp | [−2.3, +1.1] |
| dev | 20 | 283 | +5.4% | +3.9% | 39% | 3,833 | +6.0% | +3.5% | **−0.7pp** | **[−3.9, +2.9]** |
| dev | 60 | 283 | +16.0% | +7.6% | 35% | 3,833 | +22.0% | +11.7% | −6.0pp | [−14.8, +2.6] |
| val | 5 | 157 | +1.8% | +1.0% | 45% | 3,069 | +0.6% | −0.1% | +1.2pp | [−0.5, +2.7] |
| val | 20 | 157 | +4.2% | +1.3% | 42% | 3,069 | +2.2% | −0.2% | **+2.0pp** | **[−1.2, +5.3]** |
| val | 60 | 157 | +21.8% | +10.7% | 35% | 3,069 | +19.1% | +6.0% | +2.8pp | [−5.8, +11.6] |

Negative anomalies (price fell): dev 20-session A neg − B = −3.9pp [−7.6, +0.2]; val +2.8pp [−1.4, +7.5]. The sign
flips between splits. Full table: `results/phase4/event_study.csv` (also the all-days variant).

The distribution matters more than the mean, as the article says: in val the median 20-session return after a
positive anomaly is +1.3% against a +4.2% mean, 42% of events lose, p10 is −15.5% and p90 +24.3%.

### Strong flag (positive first-trigger event) against the label

| split | flagged | precision | base rate | lift | recall |
|---|---|---|---|---|---|
| dev | 283 | 0.449 | 0.425 | 1.06 | 0.021 |
| val | 157 | 0.408 | 0.351 | **1.16** | 0.025 |
| val, all event days | 620 | 0.434 | 0.351 | 1.24 | 0.105 |

### Composite (logistic, fitted on dev only)

Primary (protocol features; momentum coverage drops dev n to 326): b0 +0.06, price_anomaly +0.19 (p 0.11),
volume_anomaly +0.02 (p 0.89), momentum −0.11 (p 0.34). In-sample AUC 0.557. **Val AUC 0.476**, precision at
P >= 0.5 0.400 vs base 0.410 (lift 0.98), top decile 0.412. Calibration by decile is flat (hit rates 0.31 to 0.53 with
no trend).
Secondary (price and volume only, n 471): price_anomaly +0.19 (p 0.048), volume_anomaly +0.17 (p 0.073), val AUC 0.501;
at P >= 0.5 it flags 15 events with precision 0.60 (too few to mean anything), top decile 0.529 (lift 1.29).
Unavailable terms in both: institutional, fundamental, options, retail (no data).

### Verdict under the predeclared rules

- Headline: val 20-session difference +2.0pp with CI [−1.2, +5.3] (includes 0); val lift 1.16 < 1.5.
  **No evidence that the price/volume anomaly score alone is informative on this universe.** The dev period even
  points the other way (anomalies lag quiet sessions).
- Composite: val AUC 0.476 < 0.55: **no usable ranking from price/volume alone**. The funnel therefore ranks by
  anomaly_score (the code checks the val AUC and only uses the composite above 0.55).
- The article's own position survives intact: the anomaly score is a trigger for investigation, not a signal, and the
  analyst's value is sourced facts, which need the filings layer this sandbox could not fetch.

Not a strategy claim in either direction: no costs, no execution, no sizing were involved.

## 4. The radar for 2026-09-23 (stub run)

`results/phase4/radar_2026-09-23.md`. 59 monitored, 14 anomalies, 0 high-information events (nothing corroborated
because no filings are loaded), funnel 14 -> 8 -> 5 -> 3, portfolio flag on the article's example basket
(40% NVDA / 25% AMD / 20% TSM / 15% AVGO with real prices: HHI 0.285, average pairwise correlation 0.59 over 60
sessions, first principal component 70% of variance, AMD +5.0pp of the +9.2% five-session move). Top names by
anomaly: ARM (+36% in 5 sessions), MSTR, COIN, BX (−6%), META, BB, DKNG (−13%), AMAT. Every fundamental and
contradiction field says why it is empty. Language check: passed.

Cost of the same run live, from the config's funnel token assumptions: 5 deep reads and 3 contradiction runs per day
on the user's Claude models, price table to be filled in (`llm.prices_per_million`); the article's reference numbers
($153 for 150 Kimi reads, $240 for 40 Astra runs, $60,000/day without the funnel) are reproduced by the tests.

## 5. Tests

`tests/test_analyst.py`, 15 tests: the article's worked examples reproduced to the printed digit (2.88, 11.3%, 3.08,
+29.2%, 0.5, 0.05, 0.263, +4pp, 0.68, $6, $153, $240, $60,000, 37%); causality of every feature (truncating the data at
a session leaves that session's features unchanged); first-trigger and quiet flags; insider window and late-filing
visibility; EDGAR parsers (Form 4, 13F table, submissions, 8-K 2.02, index picks, MD&A extraction, XBRL frames); EDGAR
client refuses without a contact User-Agent and uses only the injected `get`; Hyperliquid client uses only `/info`;
provider gating and key handling; consistency and thesis_gap computed in code from a fake model's JSON; language
check; funnel budgets; end-to-end radar on repo data passes the language check; composite recovers a planted signal;
portfolio and bootstrap statistics; the holdout is not in the evaluated splits.

## 6. Limitations

- The filings layer is untested against live EDGAR responses; the parsers follow the documented formats and were
  tested on synthetic fixtures only. Expect small breakages on first live run (file naming varies by filer agent).
- ETFs (EWJ, EWT, EWY, EWZ, SMH, SOXL, URNM, XLE, KORU) and foreign filers (ASML, TSM, BABA, NOK, ARM) have no 10-Q,
  Form 4 or 8-K 2.02 stream; their catalyst and insider fields stay empty unless the user fills `catalyst_calendar`.
- 13F values changed units in 2023 (thousands to dollars); the code stores the raw value and uses shares for changes.
- Issuer-to-symbol matching for 13F is by normalised name, recorded in `cusip_map` for correction.
- The composite's institutional, fundamental, options and retail terms cannot be fitted until those tables have data;
  the article's coefficient table remains illustrative.
- Estimate revisions, options flow, retail flow and news have no free source; they are placeholders by design.
