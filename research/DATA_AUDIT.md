# DATA_AUDIT.md — sources, coverage, integrity, execution-data limitations (2026-09-25)

## 0. Access situation (fact)
The sandbox network policy denied `api.hyperliquid.xyz` (CONNECT 403), `api-ui.hyperliquid.xyz`, the testnet, both
documentation sites (`hyperliquid.gitbook.io`, `docs.trade.xyz`), every centralised-exchange API, every data vendor,
and the WebFetch tool for those hosts. Reachable: `github.com`, `raw.githubusercontent.com`, PyPI, and AWS S3 (whose
Hyperliquid buckets `hyperliquid-archive`, `hl-mainnet-node-data`, `hydromancer-reservoir` are requester-pays and refuse
anonymous requests — re-tested 2026-09-25 07:5x UTC). No data was purchased; no keys were used; no orders were sent.
The user reported having a Hyperliquid account; this does not change the run (read-only research; the fix for API
access is the environment's Network access setting, allow-listing `api.hyperliquid.xyz`).

## 1. Datasets used

| ID | Source (repo @ commit) | What | Coverage | Granularity | Retrieved | Provenance / method |
|---|---|---|---|---|---|---|
| T-MID | github.com/Tohshi-memo/HyperLiquid-Bot-test, 14,402 commits of `data/processed/asset_price_history.json` (2026-05-01 → 2026-09-25 07:45 UTC), 29 blobs read (manifest `research/data/raw/tohshi_mid_15m_manifest.csv`) | 15-minute **sampled mid prices** for 1,395 Hyperliquid assets incl. all `xyz:` HIP-3 markets; kept 125 xyz + BTC/ETH/HYPE/SOL | 2026-05-01 10:28 → 2026-09-25 07:43 UTC; 12,144 distinct observations; 92 US trading days in session | one price per asset per 15-min cron slot (`:07,:22,:37,:52` UTC); actual request time (`collected_at`) lags the slot by median 7.6 min, p95 10.1, max 16.0 | 2026-09-25 | GitHub Actions cron running `collector/asset_universe.py`: `allMids` + `metaAndAssetCtxs(dex)`; price = midPx (fallback markPx, oraclePx); rolling-window file rewritten every run, older windows recovered from git history |
| T-LIQ | same repo, `data/processed/asset_universe_latest.json`, one commit per UTC day at/after 15:30 UTC (130 days) | daily snapshots of 24h notional volume (`dayNtlVlm`), open interest, mid/mark/oracle, maxLeverage, szDecimals, growthMode, onlyIsolated, OI cap | 2026-05-01 → 2026-09-24 | daily | 2026-09-25 | as above |
| H-CANDLES | github.com/lukasbecker36-dot/hyperdata @ a4aa1d9 (data commit 2026-07-17 18:56 +01:00) | real Hyperliquid candles (o,h,l,c,v,n) for 177 native perps; kept BTC/ETH/SOL/HYPE | 1h: 2025-12-21 04:00 → 2026-07-17 15:00 UTC (5,004 bars); 15m: 2026-05-26 10:00 → 2026-07-17 13:45 (5,008 bars) | candle | 2026-09-25 | author's `fetch_*.py` → `candleSnapshot` (5,000-candle server cap) |
| H-FUND | same | hourly funding rates BTC/ETH/SOL/HYPE | 2025-12-19 → 2026-07-17 (5,040 rows each) | hourly | 2026-09-25 | `fundingHistory` |
| H-L2 | same, `spreads_snapshot.csv` (commit 2026-07-17 19:26 +01:00) | ONE L2 snapshot: bid/ask, top depth, slippage at $1k/$10k/$50k, 177 perps | single instant | — | 2026-09-25 | `l2Book` |
| F-FIX | github.com/buggatidealership/Freedom (HEAD 2026-09-25), `tests/fixtures/hyperliquid/*` | real API responses: `perpDexs` (11 dexes), `meta` for xyz/para/km/mkts/hyna/flx/io/cash/vntl/abcd (szDecimals, maxLeverage, marginTableId, growthMode, deployerFeeScale, onlyIsolated, marginMode), `metaAndAssetCtxs` xyz (dayNtlVlm, OI, impactPxs, mark/mid/oracle), sample `candleSnapshot` xyz:NVDA 1h (2026-08-24..29) and 5m (2026-08-26), `fundingHistory` xyz:NVDA | snapshot ~2026-09-02 (per that repo's docs/data-sources.md) | — | 2026-09-25 | committed fixtures of live calls |
| M-FUND | github.com/MiggoyGHP/hyperliquid-rwa-dashboard @ 0a7c1fd (built by the previous session into `data/derived/funding_hourly.parquet`) | hourly settled funding + premium for 161 markets incl. 109 xyz | xyz:NVDA 2025-11-12 → 2026-09-23 | hourly | 2026-09-24 | upstream `fundingHistory` pagination with gap audit |
| K-CAL | github.com/zeeshan8281/kerb `config/calendars/us-equities.json` | NYSE holiday list 2026-2027 (cross-check for our calendar) | — | — | 2026-09-25 | — |

Not used: perp-basis (5-min HL/Binance/OKX tape, GOLD/CL only), CozanetHQ/hyperliquid-research (native top-30 by OI only),
hazwop/funding-scout (4-hourly funding, 10 days), Freedom's own candle archive (lives in GitHub Actions artifacts that
need a logged-in GitHub session; unreachable), Hydromancer Reservoir / hyperliquid-archive (requester-pays S3).

## 2. Integrity checks
**H-CANDLES (BTC/ETH 1h and 15m):** 0 duplicated timestamps, 0 misaligned open times, 0 gaps, 0 zero-volume or
zero-trade bars, 0 OHLC inconsistencies, 0 |log return| > 10% (`research/data/raw/hyperdata_audit.json`).
Limitation: a static pull ending 2026-07-17; it does not reach the current date, so the BTC/ETH results describe
Dec 2025–Jul 2026 only.

**T-MID (sampled mids):** 12,144 observations at a median spacing of 15.0 min; 23 gaps > 60 min, the largest
2026-05-17→05-23 (6.6 days), 05-11→05-17 (5.2 d), 06-19→06-23 (2.9 d), 06-23→06-26 (2.9 d), then a handful of 2–5 hour
gaps in May/Aug. Bars spanning a gap > 45 min are flagged invalid and never used for signals or execution; 1h bars
missing a slot never "complete". Jitter: the slot label (`observed_at`) and the true observation time
(`collected_at`) differ by 0.3–16 minutes; all alignment uses `collected_at`. Stale prices: identical consecutive mids
occur (e.g. weekend internal-pricing hours); in-session repeats are rare for the selected names. Duplicated slots: the
last write wins (later `collected_at`). No forward-filling anywhere. No synthetic history.
**What T-MID cannot tell us:** intrabar highs/lows (stops are only evaluated at 15-min samples), traded volume per bar,
executed prices (mids only), and the true 1h candle open/close (we form 1h bars from four samples). The sample
xyz:NVDA candles in F-FIX show 1h ranges of roughly 0.3–1.0% in late August 2026, i.e. two-point bars understate true
ranges materially; ATR on sampled data is scaled ×2 as an explicit approximation (Brownian E[range]/E|Δ| ≈ 2).

**T-LIQ:** 130 daily snapshots; `dayNtlVlm` is the exchange's rolling 24h notional at the snapshot time (not a session
volume). OI is in units; converted to USD at the mark.

**Funding:** xyz hourly funding available through 2026-09-23 (M-FUND); BTC/ETH through 2026-07-17 (H-FUND). Where a
position spans hours without a funding row, no funding is charged (declared limitation; xyz funding averaged
+0.00125%/h × 0.5 multiplier ≈ +0.0006%/h for NVDA over its history, i.e. ≈ 0.4 bp per 6-hour hold).

**Listing dates / spec changes:** xyz:NVDA listed 2025-11-12, xyz:AAPL 2025-11-21, xyz:SP500 2026-03-18 (Freedom
docs, measured from 4h/1d candles); all five selected names have full T-MID coverage from 2026-05-01. Growth mode
`enabled` on all five as of 2026-09-25 (`lastFeeScaleChangeTime` 2025-11-23 in F-FIX). `xyz:MSTR` and `xyz:GOLD` are
not in growth mode (excluded / not stock).

**Corporate actions:** checked daily Yahoo closes of the five underlyings (previous session's `stock_daily.parquet`,
through 2026-09-23) for split-like discontinuities (|overnight ratio − 1| > 40%): none in 2026-05-01..2026-09-23.
Earnings days (large gaps) remain in the sample and are not excluded.

## 3. Execution-data limitations (why every result is "approximate execution")
* Spreads/depth: two snapshots only (F-FIX impact prices ~2026-09-02 for xyz; H-L2 2026-07-17 for BTC/ETH). No
  historical spread series exists in any reachable source. The adverse scenario doubles spread and slippage.
* Fills: taker at the next observation/bar open plus half-spread plus 1 bp; stops filled at the observed price beyond
  the stop (sampled) or at the stop/open (candles) plus an adverse penalty (2 bp base, 5 bp adverse).
* Sampled data cannot see a stop being hit between samples; realised stop losses would be larger than modelled.

## 4. Calendar
NYSE holidays 2025–2027 and 13:00 ET early closes are hard-coded (`research/src/hlr2/sessions.py`) and unit-tested for
DST (EDT 13:30 UTC open, EST 14:30 UTC open) and early close. Discrepancy noted: kerb lists 2027-04-02 as Good Friday;
Easter 2027 is 28 March, so Good Friday is 26 March; we use 26 March.
