# Protocol, phase 4 (written before any phase-4 evaluation ran) — "build the AI market analyst from the article"

Source: `docs/PHASE4_ARTICLE_DIGEST.md` (leopardracer, 2026-09-21). User decisions (2026-09-25): article-faithful
analyst report; Claude for both reasoning roles; free data only (Hyperliquid read-only + SEC EDGAR); the 59
Hyperliquid-listed stocks already in the daily panel.

## What phase 4 is and is not

Phase 4 builds an **analyst**: a pipeline that says what changed, where the fact came from, and what contradicts
it. It is not a strategy and produces no trades, no scores, no buy/sell language. The only quantitative
question it answers offline is the article's own: "does any of this mean anything?", i.e. do the deterministic
signals carry information about forward returns on this universe. A positive answer would not be a trading
edge (no costs, no execution, no sizing are involved); a negative answer would not invalidate the analyst's
job of speeding up research.

## Data

- Prices: `data/derived/stock_daily.parquet` (Yahoo daily OHLCV via the MiggoyGHP dashboard repo, provenance in
  `data/derived/provenance.json`), 59 names with >= 200 sessions, 2024-09-23 to 2026-09-23. Same panel as phase 3.
- Filings (13F, Form 4, 8-K, 10-Q/10-K text, XBRL facts): SEC EDGAR is unreachable from the sandbox. The
  adapters are written and unit-tested on synthetic fixtures; the tables stay empty until the user runs the
  ingest on their machine. Estimate revisions, options flow, retail flow and news have no free source: they are
  typed placeholder tables and appear in reports as "not available".
- Hyperliquid: read-only `info` endpoints only (candles, mids, clearinghouseState for the user's positions).
  No signing key, no order endpoints, no account settings.

## Splits (same calendar as phase 3)

- dev: 2024-09-23 to 2025-09-22; val: 2025-09-23 to 2026-03-22; holdout: 2026-03-23 to 2026-09-23.
- The holdout is **not evaluated** in phase 4. The radar produced for 2026-09-23 is a descriptive report of the
  latest session, not an evaluation metric, and its priorities come from the rule below, not from any fit.

## Definitions fixed before running

| quantity | definition |
|---|---|
| volume_ratio | volume today / mean volume of the previous 20 sessions (today excluded) |
| return_5d | close today / close 5 sessions ago − 1 |
| z_return | (return_5d − trailing mean) / trailing stdev, trailing window = previous 250 sessions (today excluded), min 60 |
| z_volume | same construction on volume_ratio |
| anomaly_score | \|z_return\| + \|z_volume\| |
| event (Group A) | session with anomaly_score > 2; "positive" if z_return > 0, "negative" otherwise |
| first-trigger event | event with no event in the previous 5 sessions (removes overlap of the 5-day window) |
| quiet (Group B) | session with anomaly_score < 1 (the article's "score < 0" cannot occur with an absolute-value score) |
| forward_return_k | close k sessions ahead / close today − 1, k in {5, 20, 60} |
| label | forward_return_20 >= +5% |
| strong flag | positive first-trigger event |
| base rate | share of all sessions in the split with label true |
| z_momentum | 60-session return z-scored against its trailing 250-session distribution |

## Statistics (one pass, no tuning on val)

1. Event study, per split and horizon: n, mean, median, stdev, share of losers, share >= +5%, p10, p90 for
   Group A positive, Group A negative, Group A all, Group B. Difference of means A-positive minus B with a
   bootstrap CI clustered by session date (1000 resamples, seed 0).
2. Precision and recall of the strong flag against the label, and lift = precision / base rate.
3. Composite: logistic regression on first-trigger events, features z_return, z_volume, z_momentum
   (standardised on dev), fitted on dev only with statsmodels Logit; coefficients, standard errors and
   p-values reported. Evaluated once on val: AUC (rank statistic), precision at P >= 0.5 and in the top decile,
   calibration by decile. Placeholder terms (retail, institutional, fundamental, options) are excluded from the
   fit and listed as unavailable; the article's five-term formula is kept in code with those terms fixed at 0.

Headline (predeclared): the 20-session difference of means, A-positive minus B, on val, and the val lift of the
strong flag. Everything else is secondary and reported anyway.

## Interpretation rules (predeclared)

- Val CI for the headline difference excludes 0 and val lift >= 1.5: "anomalies carried directional
  information on this universe in this period". Not a strategy claim.
- Otherwise: "no evidence that the price/volume anomaly score alone is informative here"; the analyst still
  runs, because its output is sourced facts, not the score.
- Composite coefficients are reported whatever their sign; a val AUC below 0.55 is reported as "no usable
  ranking from price/volume alone".

## Report and LLM constraints

- Report language: "what changed, source, what contradicts it". A language check rejects recommendation
  wording (recommend, BUY/SELL as advice, price targets, "N/100" scores). Priority labels are categorical from a
  fixed rule: high = anomaly_score > 3 with at least one corroborating source (institutional, insider or
  catalyst); medium = anomaly_score > 2; low otherwise.
- LLM stages (deep read, bull/bear/arbiter) run through one provider interface. In the sandbox the provider is
  a stub that makes no network call and labels its output as such. Live calls need the user's key in an
  environment variable on their machine and an explicit `--live-llm` flag. Keys are never written to the repo
  or to logs.
- The model extracts numbers and claims with citations; `consistency` and `thesis_gap` are computed in code
  from those numbers. Model output is treated as claims to check, never as data.
- Funnel budgets cap the number of names per stage; the cost estimate uses the article's token assumptions and
  a price table the user fills in for the Claude models they use.

## Permissions (article's table, kept as hard rules)

Market data READ; portfolio READ; research READ; orders: not implemented in this package at all; transfers NO;
account settings NO.
