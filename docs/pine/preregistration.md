# Pre-registration: which of the vault's Pine Scripts are "best"? (written 2026-09-29, before any result)

The request: find the best 10 Pine Scripts among the 5,283 in `brainbrick-trades/The-Quant-Trading-Vault`
(a repackaging of FMZ's public strategy library; commit `c9d6fa4`, 2026-09-27). "Best" has no meaning without a test,
so this file fixes the test before any script is run. The git history of this file is the audit trail; anything decided
after results were seen will be marked POST-HOC in `docs/research_ledger.md`.

## Facts that shape the design (from `src/pinebt/vault_index.py` over all files)

* 5,283 files carry Pine source: v5 2,563, v4 1,488, v2 444, v3 424, v6 336, v1 8, unversioned 20. 5,279 place orders
  (`strategy.entry`/`strategy.order`).
* 4,665 were posted by FMZ staff account "ChaoZhang" (mostly ports of public TradingView scripts), 536 by "ianzeng123".
* Each file has an FMZ backtest header. Its median length is **30 days**; 4,727 target Binance BTC_USDT (4,899 futures).
  Median gap between that backtest's end and the file's "Last Modified" date: 1 day.
* "Last Modified" years: 2022 243, 2023 2,064, 2024 2,198, 2025 778.

So nearly every script was shown on about one month of data and posted. **Everything after "Last Modified" is
out-of-sample** for that script: nobody could have tuned it on data that did not exist yet. That is the core of the test.

## Data

* Binance USDT-M perpetual futures, 1-minute klines, BTCUSDT and ETHUSDT (the ranking panel), plus SOLUSDT (robustness
  only), 2021-01-01 00:00 UTC to 2026-09-28 23:59 UTC, from the public archive (data.binance.vision, S3 endpoint).
  Resampled to each script's timeframe (bars labelled by open time, UTC).
* Binance historical funding rates for the same symbols, charged on open positions at each settlement timestamp
  (longs pay positive funding).

## Engine

A Pine Script (v1-v6, lenient) to Python compiler with a TradingView-style broker emulator, written for this test
(`src/pinebt/`). Semantics follow TradingView defaults: scripts execute once per bar on close; market orders fill at
the next bar's open (or at the same bar's close when the script sets `process_orders_on_close=true`); stop and limit
orders fill intrabar using the standard OHLC path (open, then the nearer extreme, then the other, then close), gaps fill
at the open; `strategy.entry` reverses an opposite position; pyramiding as declared. Drawing, plotting and alert calls
are no-ops. Unit tests cover indicators, series semantics and fills. Scripts are run with their default inputs; no
parameter is changed.

## Uniform overrides (the same for every script)

1. **Costs**: 5 bps fee + 2 bps slippage per side on every fill (14 bps round trip), plus funding. The script's own
   commission and slippage settings are ignored.
2. **Sizing**: every entry uses `100 / max(1, pyramiding)` percent of current equity; explicit `qty` arguments are
   replaced by that size; relative exits (`qty_percent`) are kept. Gross exposure therefore never exceeds 1x. This
   ranks the signals, not the author's leverage choice.
3. **Date-window inputs** (inputs whose names or titles mark a backtest start/end, year/month/day or `input.time`
   ranges) are neutralised to allow every date; otherwise many scripts would stop trading in 2019-2022.
4. **`request.security`** is evaluated without future data: the higher-timeframe value visible at a chart bar is the
   last higher-timeframe bar that had closed by then, whatever `lookahead` says. Scripts that ask for
   `lookahead_on` are flagged (their author backtests leak the future) and excluded from the top 10.
5. **Timeframe**: the header's `period` (1h if missing). **Symbol**: BTCUSDT and ETHUSDT for every script, whatever the
   header says (4,727 of 5,283 headers name BTC_USDT anyway).
6. **Bar cap**: runs use the most recent N bars of the data window, N fixed from measured engine throughput before
   any performance is examined and recorded in the ledger. Sub-hour scripts may therefore not reach the OOS minimum.

## Windows

* **OOS** (primary): from the first bar after `Last Modified` + 1 day to 2026-09-28. The script runs continuously from
  the start of its data; positions carry across the boundary; OOS metrics use the equity changes inside the window.
* **IS** (secondary): the part of the run before `Last Modified`.

## Metrics (daily UTC mark-to-market equity)

Annualised Sharpe (365 days, rf = 0), CAGR, maximum drawdown, closed trades, win rate, profit factor, exposure,
turnover; buy-and-hold Sharpe and CAGR of the same asset and window; beta and annualised alpha against it.

## Eligibility for the ranking

A script is eligible if, on **both** BTCUSDT and ETHUSDT: it compiled and ran without error, equity never reached zero,
the OOS window is at least **365 days**, and it closed at least **20 trades** in the OOS window; and it does not use
`lookahead_on`.

## Ranking rule (primary)

`score = mean(OOS Sharpe on BTCUSDT, OOS Sharpe on ETHUSDT)`, highest first. Then collapse near-duplicates walking down
the list: a script whose daily OOS returns on BTCUSDT correlate above 0.95 with a higher-ranked script is dropped. The
first 10 survivors are "the best 10" by this test. Each is then read by hand for defects the static flags miss
(repainting, future references, broker-emulation artefacts); a defect found by reading removes it and is recorded.

## Reported with the top 10 (pre-declared)

* Probabilistic Sharpe vs 0 and the **Deflated Sharpe Ratio** with N = number of eligible scripts and the
  cross-sectional variance of their OOS Sharpes. If none reaches DSR 0.95 the report says so first.
* Whether each beats buy-and-hold Sharpe on both assets over its own OOS window.
* Robustness (not used for ranking): SOLUSDT, costs x2, full-window (2021-2026) Sharpe.

## Secondary questions (pre-declared)

1. Does pre-publication performance predict post-publication performance? Spearman rank correlation of IS vs OOS
   Sharpe across eligible scripts.
2. What share of scripts has a positive OOS Sharpe, and what share beats buy-and-hold?
3. How many scripts could not be evaluated, and why (parse, unsupported feature, runtime error)?

## What will not be done

No parameter search, no re-running with changed inputs, no alternative ranking rule chosen after seeing results
(any extra view is labelled POST-HOC), no synthetic data presented as evidence.
