# Protocol freeze (written BEFORE any strategy test ran) — 2026-09-25

Session: claude/sweet-bardeen-fzr7j4, started 2026-09-25 07:25 UTC. Research budget: <= 4 h active execution,
<= 60 distinct strategy configurations in total (every change of indicator, filter, exit, session rule or
parameter value counts as one; the same configuration evaluated on several instruments counts once).

## Environment facts that shape this run
* The sandbox network policy denies `api.hyperliquid.xyz` (CONNECT 403), both docs sites, every exchange API and
  every data vendor. Only github.com / raw.githubusercontent.com, PyPI and (requester-pays, hence unusable) S3
  are reachable. All market data therefore comes from public GitHub repositories whose authors ran the official
  API themselves (provenance recorded in DATA_AUDIT.md).
* No true 15m/1h OHLC candles for HIP-3 stock perps exist in any reachable archive. The stock-linked tests use
  15-minute **sampled mid prices** (Tohshi-memo/HyperLiquid-Bot-test, GitHub Actions cron at :07/:22/:37/:52).
  BTC/ETH tests use real Hyperliquid candles (lukasbecker36-dot/hyperdata, pulled 2026-07-17).
* Consequence declared up front: stock-linked results are **exploratory** regardless of outcome. They cannot be
  "validated" here because (a) intrabar highs/lows are unobserved, (b) the sample is <= 5 months, (c) spreads and
  depth are known from two snapshots only.

## Data splits (chronological, fixed now)
* Development: first 50% of usable trading days of each dataset.
* Validation (walk-forward): next 30%, evaluated in rolling windows of ~10 trading days, parameters frozen.
* Holdout: last 20% of trading days, evaluated ONCE for at most ONE primary candidate chosen on dev+validation.
* Purge: trades that open before a boundary and would close after it are dropped at the boundary; indicators
  are warmed up on prior data only (never on future data).

## Screening gates (from the task; provisional, not guarantees)
PF >= 1.3 on validation; holdout PF > 1.0 with uncertainty; positive expectancy under adverse costs (2x spread and
slippage); majority of walk-forward windows positive; stable under nearby parameters; not driven by one trade or
one day; support on >= 3 stock instruments for a shared rule; >= 100 OOS trades over >= 40 distinct days.

## Strategy families and coarse predeclared grids (budget: 60 configs)
Shared rules for all: completed bars only; signal at bar close t executes at the first observable price after
t + 1 bar-interval sampling latency (sampled-mid data: the next 15-min sample; candle data: next bar open),
taker fees, half-spread + slippage per side, funding accrued hourly while open, one position at a time across
the universe, $1 planned risk incl. costs, 2x max gross leverage, size rounded to szDecimals, min order $10,
session-end flat exit, $10 drawdown pause with shadow analysis. Simultaneous signals: lowest estimated execution
cost (spread proxy) wins; ties by alphabetical symbol. Long and short reported separately.

1. MA trend following (15m and 1h): EMA fast/slow in {(10,40), (20,80)}; optional ADX-like strength filter
   (|fast-slow|/ATR > 0.5). Stop = 2 ATR(14); exit on opposite cross or session end. Configs: 2 tf x 2 grids x 2 filter = 8.
2. Bollinger mean reversion (15m, 1h): 20-bar, k in {2.0, 2.5}; entry on close back inside the band after an
   excursion; range regime = 1h EMA slope small (|EMA20 slope| < 0.25 ATR). Stop = beyond the excursion extreme
   + 0.5 ATR; target = middle band; time exit 8 bars. Configs: 2 tf x 2 k x 2 regime = 8.
3. Volatility-compression breakout (15m, 1h): bandwidth(20) at 60-bar low (percentile <= 20%), then close beyond
   band. Stop = opposite band; trail = 2 ATR; configs: 2 tf x 2 percentile {10, 20} = 4.
4. Channel breakout (15m, 1h): N-bar high/low with N in {12, 24}; stop = 1.5 ATR; trail = N-bar opposite
   extreme; configs: 2 tf x 2 N = 4.
5. Trend pullback (1h filter + 15m entry): 1h EMA(20) vs EMA(50) direction (completed 1h bars only); 15m
   pullback = close below EMA(10) then close back above (long); stop = pullback low - 0.5 ATR; target = 2R;
   configs: 2 pullback definitions x 2 targets {1.5R, 2R} = 4.
6. Opening-range breakout (stock-linked, 15m): first 30 or 60 minutes of the US session define the range;
   breakout on close beyond; stop = range midpoint; exit at session end or 2R; configs: 2 ranges x 2 targets = 4.
Baselines (not counted as configs): cash; session-matched long exposure; basic 1h EMA(20/50) trend.
Reserve: up to 28 configs for cost-stress variants, nearby-parameter robustness and BTC/ETH 24/7 experiment.

## Execution cost scenarios
Base: fees per verified schedule (native taker 0.045%; xyz growth mode: 0.0045%-0.009% protocol + deployer share
-> modelled as 0.009% + deployer 0.009% = 0.018% taker unless verified otherwise; sensitivity at 0.09%).
Half-spread from snapshot impact prices (per market), slippage 1 bp. Adverse: 2x spread and 2x slippage.
