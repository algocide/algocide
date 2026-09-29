# The best 10 Pine Scripts in The Quant Trading Vault, tested

Session 2026-09-29. Request: "find the best 10 Pine Scripts" among the strategies in
[brainbrick-trades/The-Quant-Trading-Vault](https://github.com/brainbrick-trades/The-Quant-Trading-Vault) (commit
`c9d6fa4`). Rules fixed before any script ran: `docs/pine/preregistration.md`. Every decision taken after seeing results
is in `docs/research_ledger.md` (items 27-40) and marked POST-HOC below.

## Short answer

"Best" here means the highest Sharpe ratio after the script was published, on Binance BTC and ETH perpetuals,
2021 to 2026-09-28 data, after 7 bps per side and funding, with every order filled against 1-minute candles. Ranked by
the mean of the BTC and ETH out-of-sample (OOS) Sharpe, near-duplicates removed, each finalist read by hand:

| # | Vault file (`strategies/…`) | What it does | TF | Published | OOS Sharpe BTC / ETH | Buy & hold Sharpe BTC / ETH | Max drawdown BTC / ETH | OOS trades BTC / ETH |
|---|---|---|---|---|---|---|---|---|
| 1 | `Pivot-Based-Volume-Weighted-Breakout-Reversal-Strategy.md` | Breaks or rejections of the last swing high/low on high volume; 3% target, 2% stop | 1d | 2025-04-24 | 1.38 / 2.12 | -0.01 / 0.76 | -6% / -6% | 33 / 32 |
| 2 | `Dynamic-ATR-Trailing-Stop-Trading-Strategy-Market-Volatility-Adaptive-System.md` | Always long or short; flips when price crosses a 1-ATR band | 1d | 2025-03-04 | 1.74 / 1.25 | 0.09 / 0.51 | -22% / -40% | 21 / 23 |
| 3 | `RSI2-Based-Dynamic-Breakout-Trading-Strategy-with-Moving-Average-Filter-System.md` | Buys RSI(2) dips above the 50-day average, sells after 5 days | 1d | 2025-02-27 | 0.66 / 1.83 | 0.20 / 0.51 | -17% / -12% | 28 / 28 |
| 4 | `Breakout-Zone-Momentum-Trading-Strategy.md` | 50-day moving-average crossover, long and short | 1d | 2024-07-29 | 0.78 / 1.67 | 0.46 / 0.21 | -28% / -37% | 54 / 29 |
| 5 | `Multi-Trend-Crossover-Strategy.md` | Long while EMA(10) is above EMA(200) | 3h | 2023-09-21 | 1.17 / 1.15 | 1.04 / 0.59 | -29% / -39% | 50 / 36 |
| 6 | `Renko-Boxes-and-TEMA-Indicator-Micro-Profit-Strategy.md` | Long-only TEMA cross that averages down and never sells at a loss | 1h | 2023-09-20 | 0.92 / 1.31 | 1.04 / 0.59 | -38% / -20% | 898 / 1502 |
| 7 | `Bollinger-Percentage-Bands-Trading-Strategy.md` | Trailing stop on a smoothed Bollinger %B, long and short | 1d | 2023-12-11 | 1.04 / 1.17 | 0.76 / 0.44 | -32% / -43% | 73 / 77 |
| 8 | `Turnaround-Tuesday-Strategy-Weekend-Filter.md` | Buys Monday in a downtrend, sells Wednesday | 2h | 2024-04-30 | 1.61 / 0.60 | 0.55 / 0.28 | -9% / -29% | 38 / 67 |
| 9 | `Multi-Timeframe-Heikin-Ashi-Moving-Average-Trend-Following-Trading-System.md` | Heikin-Ashi close crossing its 30-day EMA, long and short | 1d | 2025-01-06 | 0.78 / 1.35 | 0.02 / 0.16 | -32% / -56% | 56 / 42 |
| 10 | `Moving-Average-Ribbon-Trend-Strategy.md` | Noro's Trend Ribbon: breakout of a 20-day moving-average channel | 1d | 2023-11-02 | 1.14 / 0.99 | 0.88 / 0.53 | -34% / -72% | 30 / 32 |

Read this list with three facts in mind.

1. **None of them beats luck.** 3,507 scripts qualified for the ranking. If all of them had zero skill, the best
   would still be expected to show an OOS Sharpe of about 2.1 to 3.0 over these window lengths. The winners score 1.1
   to 1.75. Their deflated Sharpe ratios are 0.00 to 0.12; 0.95 would be the bar for "probably real".
2. **The typical script loses money after costs.** Median OOS score -0.49. 28.5% of the 3,507 have a positive score,
   and 3.1% beat buy-and-hold's Sharpe on both BTC and ETH.
3. **The test as pre-registered crowned fake winners.** With TradingView's own fill model the top 10 showed OOS
   Sharpe ratios of 2.5 to 17.5, and one multiplied its money 1.3 billion times after publication. They were
   artefacts of how TradingView fills orders inside a bar (next section). Re-run with 1-minute fills, every one of
   those ten loses money.

## What went wrong first, and what was changed (POST-HOC)

**The intrabar fill artefact (ledger 35).** TradingView's strategy tester does not know the path price took inside a
bar. It assumes a straight line from the open to the nearer extreme, then to the other extreme, then to the close.
Under that assumption a trailing stop with a one-tick offset rides the whole move to the bar's high and exits one tick
below it. That is what the pre-registered top 10 did: 51-100% of their OOS trades opened and closed inside a single
bar (34 of the top 50 made most of their BTC trades that way), and in the first run Piercing-Pin-Bar-Reversal-Strategy
exited one tick under the bar high on almost every trade. The engine copied TradingView faithfully, so it copied the
artefact. The pre-registration lists broker-emulation artefacts as a
reason to remove a script; with the whole top 50 affected, removing them one by one was not practical. The fix was a
1-minute bar magnifier (TradingView offers the same idea as "Bar Magnifier"): stop, limit and trailing orders are checked
against the 1-minute candles inside each chart bar. Every script was re-run with it and the unchanged ranking rule was
applied to the new runs.

| Pre-registered top 10 (TradingView fills) | Score, TradingView fills | Score, 1-minute fills | Rank with 1-minute fills |
|---|---|---|---|
| `Multi-Indicator-Dynamic-Trailing-Stop-Trading-Strategy.md` | 13.43 | -6.81 | 3,173 of 3,507 |
| `Open-Close-Cross-Moving-Average-Trend-Following-Strategy.md` | 10.32 | -4.78 | 3,066 of 3,507 |
| `Multi-Timeframe-Trend-Breakout-Strategy-with-RSI-Filter-and-ATR-Based-Risk-Management.md` | 8.37 | -10.38 | 3,292 of 3,507 |
| `Dynamic-EMA-Trend-Following-with-Volatility-Adaptive-Nasdaq-Futures-Trading-Strategy.md` | 7.50 | -4.56 | 3,053 of 3,507 |
| `Multi-Moving-Average-Trend-Following-with-ATR-Risk-Management-Quantitative-Trading-Strategy.md` | 7.24 | -13.10 | 3,339 of 3,507 |
| `RSI-Reversal-Trading-Strategy-432118.md` | 6.29 | -4.82 | 3,069 of 3,507 |
| `RePaNoCHa-Quantitative-Trading-Strategy.md` | 6.10 | -3.34 | 2,934 of 3,507 |
| `DYNAMIC-MOMENTUM-OSCILLATOR-TRAILING-STOP-STRATEGY.md` | 5.99 | -4.54 | 3,051 of 3,507 |
| `Piercing-Pin-Bar-Reversal-Strategy.md` | 5.82 | -4.14 | 3,014 of 3,507 |
| `RSI-Relative-Strength-Index-Strategy.md` | 5.79 | -21.39 | 3,435 of 3,507 |

Scripts that do not use intrabar orders are unaffected: across all eligible scripts the rank correlation between
the two versions is 0.95 and the median change is 0.00.

**Engine and evaluation bugs found by reading the finalists and by an independent review (ledger 37-39).**
Exit orders outlived the position they were placed for and closed later positions at stale prices; TradingView scopes
an exit without `from_entry` to the position open at the call, and one with `from_entry` to entries created on or
before the call ([TradingView strategies documentation](https://www.tradingview.com/pine-script-docs/concepts/strategies/)).
Orders resting at exactly a just-filled price were skipped. `math.random()` depended on which scripts had run before
in the same process. One finalist (EMA-RVI "Gold Bot") gates its entries with `math.random() > 0.5`, so its result is
a coin flip; the 8 scripts that call `math.random` are now excluded. And the evaluation never applied the
pre-registered `lookahead_on` exclusion because of a pandas attribute clash; it now covers the 96 scripts that ask
for look-ahead explicitly and 80 written before Pine v3, where `security()` looked ahead by default. All of it was
fixed, tested and everything re-run. The final list above comes from that last run.

**Deflated Sharpe (ledger 36).** The pre-registered formula uses the cross-sectional variance of OOS Sharpe ratios.
Some scripts lose almost the same amount every day (fees on trades at unchanged prices), their volatility is near
zero and their Sharpe near -10^14, so the variance explodes and the formula returns a benchmark Sharpe of 7x10^13.
The report uses the null version instead: each script is compared with the expected best Sharpe of 3,507 skill-less
strategies with the same number of OOS days (a daily Sharpe estimated from n returns has variance 1/(n-1) when the
true Sharpe is zero).

## The ten, with the caveats that matter

An independent review (a separate agent that rebuilt 13 candidates, including all ten below, from their Pine source
with pandas and its own account code, without the engine) matched twelve of them trade for trade with zero equity
difference. The Trend Ribbon differs by one warm-up trade in January 2021 and has identical returns afterwards. The
review found nothing that inflates these ten (`results/pine_review/review.md`).

1. **Pivot-Based Volume-Weighted Breakout/Reversal.** Tiny drawdown (6%) because it is in the market about 5% of the
   time, and most trades hit the 3% target or 2% stop the same day. Only 33 trades on BTC; 98% of the BTC profit came
   from shorts during a falling 2025-2026. Its pre-publication Sharpe was 0.14 / 0.39.
2. **Dynamic ATR Trailing Stop** (a simplified UT Bot). 81-83% a year OOS from 21-23 trades. Over 2021-2026 as a whole
   its Sharpe is 0.48 / 0.70; it lost 37% on BTC in 2021 and 26% in 2022.
3. **RSI(2) dip-buyer.** Strong on ETH (1.83), weak on BTC (0.66), and its pre-publication record was negative
   (-0.16 / -0.29). Full-window Sharpe 0.00 on BTC.
4. **"Breaker Blocks"**, in practice a 50-day moving-average crossover. 19% of trades win; the five best trades make
   more than three times the net profit. Flat on SOL.
5. **Multi Trend Crossover** (EMA 10/200, long only). Much of its return is plain market exposure (beta about 0.5). Best of
   the ten on SOL (1.48) and in the full window it is one of the steadier ones (0.68 / 0.85).
6. **"Renko Boxes and TEMA"** has no Renko in it. It buys dips up to 100 times and only sells at a 2% gain on the
   average price, so it never books a loss. That looks smooth until a long bear market; it lost 65% in 2022 in the
   full-window run. It does not beat buy-and-hold's Sharpe on BTC.
7. **Bollinger %B trailing stop.** The best full-window record of the ten (0.80 / 1.08) and positive on SOL.
8. **Turnaround Tuesday.** A weekday effect. Positive on BTC every year from 2022 to 2026, weak on ETH (0.60) and SOL
   (0.16).
9. **Multi-timeframe Heikin-Ashi crossover.** Heikin-Ashi values drive the signal, fills are at real prices. ETH
   drawdown 56%.
10. **Noro's Trend Ribbon.** 72% drawdown on ETH inside its OOS window.

A pattern runs through the list: all ten lagged buy-and-hold on BTC in 2023 and 2024 (BTC gained 155% and 112%) and
made their relative gains in 2025 and 2026, when BTC fell about 6-7% a year. Strategies that go short or stand aside
win in that stretch. That is partly regime, not skill.

## Checks outside the ranking

| # | Script | Beats B&H Sharpe on both | PSR BTC / ETH | Alpha/yr BTC / ETH | Beta BTC / ETH | SOL OOS Sharpe | OOS Sharpe at 2x costs BTC / ETH | Full 2021-26 Sharpe BTC / ETH |
|---|---|---|---|---|---|---|---|---|
| 1 | Pivot-Based-Volume-Weighted-Breakout-Reversal-Strategy | yes | 0.96 / 1.00 | 17% / 26% | 0.01 / -0.00 | 0.97 | 1.14 / 1.90 | 0.47 / 0.84 |
| 2 | Dynamic-ATR-Trailing-Stop-Trading-Strategy-Market-Volatility | yes | 0.99 / 0.95 | 67% / 69% | -0.04 / 0.32 | 0.67 | 1.69 / 1.22 | 0.48 / 0.70 |
| 3 | RSI2-Based-Dynamic-Breakout-Trading-Strategy-with-Moving-Ave | yes | 0.81 / 1.00 | 9% / 53% | 0.11 / 0.23 | 0.58 | 0.49 / 1.75 | 0.00 / 0.37 |
| 4 | Breakout-Zone-Momentum-Trading-Strategy | yes | 0.88 / 0.99 | 32% / 95% | -0.03 / 0.05 | -0.00 | 0.70 / 1.63 | 0.65 / 0.68 |
| 5 | Multi-Trend-Crossover-Strategy | yes | 0.98 / 0.98 | 14% / 34% | 0.52 / 0.48 | 1.48 | 1.10 / 1.11 | 0.68 / 0.85 |
| 6 | Renko-Boxes-and-TEMA-Indicator-Micro-Profit-Strategy | no | 0.95 / 0.99 | 4% / 29% | 0.53 / 0.31 | 1.01 | 0.90 / 1.29 | 0.27 / 0.50 |
| 7 | Bollinger-Percentage-Bands-Trading-Strategy | yes | 0.96 / 0.98 | 50% / 72% | -0.12 / -0.04 | 0.34 | 0.95 / 1.11 | 0.80 / 1.08 |
| 8 | Turnaround-Tuesday-Strategy-Weekend-Filter | yes | 1.00 / 0.83 | 20% / 14% | 0.09 / 0.14 | 0.16 | 1.46 / 0.46 | 0.64 / 0.21 |
| 9 | Multi-Timeframe-Heikin-Ashi-Moving-Average-Trend-Following-T | yes | 0.85 / 0.97 | 31% / 79% | -0.17 / 0.08 | 0.63 | 0.67 / 1.30 | 0.44 / 0.63 |
| 10 | Moving-Average-Ribbon-Trend-Strategy | yes | 0.97 / 0.96 | 45% / 56% | 0.13 / 0.09 | 1.23 | 1.11 / 0.97 | 0.90 / 0.34 |

SOL was not used for ranking. "2x costs" doubles fees and slippage to 14 bps per side. "Full" is the whole
2021-2026 run, including the years before publication.

| # | Script | Long share of OOS trades | Short side's share of net | Top-5 trades' share of net | Median hold (h) | Win rate | Static flags |
|---|---|---|---|---|---|---|---|
| 1 | Pivot-Based-Volume-Weighted-Breakout-Reversal-Strategy | 55% | 98% | 76% | 0.0 | 48% | none |
| 2 | Dynamic-ATR-Trailing-Stop-Trading-Strategy-Market-Volatility | 48% | 47% | 121% | 408.0 | 52% | none |
| 3 | RSI2-Based-Dynamic-Breakout-Trading-Strategy-with-Moving-Ave | 100% | none | 203% | 96.0 | 50% | none |
| 4 | Breakout-Zone-Momentum-Trading-Strategy | 50% | 46% | 321% | 96.0 | 19% | none |
| 5 | Multi-Trend-Crossover-Strategy | 100% | none | 148% | 130.5 | 28% | process_orders_on_close |
| 6 | Renko-Boxes-and-TEMA-Indicator-Micro-Profit-Strategy | 100% | none | 3% | 223.5 | 77% | none |
| 7 | Bollinger-Percentage-Bands-Trading-Strategy | 49% | 24% | 177% | 72.0 | 29% | none |
| 8 | Turnaround-Tuesday-Strategy-Weekend-Filter | 100% | none | 55% | 39.0 | 68% | none |
| 9 | Multi-Timeframe-Heikin-Ashi-Moving-Average-Trend-Following-T | 50% | 61% | 281% | 120.0 | 36% | none |
| 10 | Moving-Average-Ribbon-Trend-Strategy | 50% | 23% | 127% | 708.0 | 37% | none |

## What the whole library says

| Outcome (worst of BTC and ETH) | Scripts |
|---|---|
| not run (bar cap) | 255 |
| parse error | 23 |
| unsupported feature | 18 |
| compile error | 26 |
| engine crash | 4 |
| runtime error on some bars | 7 |
| ran cleanly | 4950 |
| total | 5283 |

* 5,283 Pine files. 255 one- and two-minute scripts were not run: under the 200,000-bar cap their data covers 139 or
  278 days, short of the 365-day OOS minimum. 4,950 ran cleanly on both assets.
* 3,507 qualified for the ranking (clean runs, equity never hit zero, at least 365 OOS days and 20 OOS trades on both
  assets, no look-ahead request, no random signal). The rest mostly traded too rarely (1,313) or blew up (218).
* **Pre-publication performance does predict post-publication performance, mostly through costs.** Spearman
  correlation of pre- vs post-publication Sharpe: 0.78. Scripts that trade a lot lose to fees in both periods (OOS
  alpha vs trade count: -0.79). Holding trading frequency roughly fixed (terciles), the correlation of alpha drops to
  0.37 and 0.36 for the two lower-turnover groups (POST-HOC).
* The median eligible script has a beta to BTC of 0.03 over its OOS window: most are not simply long the market.

## About the tweet

The tweet says 5,800+ strategies: the vault has 5,806 files. "5,200+ Pine Script indicators": 5,283 Pine files, and
5,279 of them place orders, so they are strategies, not indicators. "360+ for Java": the 362 files are JavaScript
(FMZ's JavaScript API), not Java. "130+ for Python": 131. Almost all of it is a repackaging of FMZ's public strategy
library: 4,665 of the Pine files were posted by one FMZ staff account ("ChaoZhang"), mostly ports of public
TradingView scripts, each shown on a backtest with a median length of 30 days. Nothing in this test needed the
"agent kit".

## Caveats

* Two coins, one venue, 2021-2026. OOS windows differ by script (1 to 4.4 years) because they start at each script's
  publication date, so scripts published in 2025 are judged mostly on a falling BTC market.
* Default inputs only, uniform sizing (100% of equity per position, divided by pyramiding) and uniform costs. A script's
  own margin, fee and position-size settings are ignored.
* The engine is a Pine-to-Python reimplementation, not TradingView. Known gaps, none of which can move a script into
  the top 10 (measured by the review): absolute exit sizes (`qty=` in close/exit/order) are not rescaled to the
  resized entries (94 scripts, 38 change, best corrected qualifying score 0.85); entries with both `stop=` and
  `limit=` fill as either rather than as a stop-limit (59 scripts, best score 0.96 either way); `ta.hma` rounds its
  smoothing length where TradingView floors it; one-cancels-other groups and `calc_on_order_fills` are ignored; within
  each 1-minute candle fills still follow the four-point path.
* Pivot-Based (#1) made most of its BTC gain in the February 2026 sell-off, and 10 or 30 bps of extra slippage on its
  stops lowers its score from 1.75 to 1.65 or 1.46.
* Picking the top 10 of 3,507 guarantees a winner's curse. Expect all ten to do worse from here.

## Reproduce

```bash
PYTHONPATH=src python3 -m pinebt.vault_index --vault <vault clone> --out data/pine/index.parquet
python3 experiments/pine/download_binance.py                       # BTC/ETH/SOL 1m klines and funding
PYTHONPATH=src python3 experiments/pine/tournament.py --out data/pine/t3_mag --min-tf-ms 180000 --magnify
PYTHONPATH=src python3 experiments/pine/evaluate.py --runs data/pine/t3_mag --results results/pine_final
python3 experiments/pine/final_top10.py --results results/pine_final   # applies hand_review.json
PYTHONPATH=src python3 experiments/pine/audit.py --files <top 10, comma separated> --out results/pine_final/audit
python3 experiments/pine/report_tables.py results/pine_final
PYTHONPATH=src python3 -m pytest tests/test_pinebt.py
```

Outputs: `results/pine_final/` (ranking, eligibility, summary, final top 10, hand review, audit JSON and equity
charts), `results/pine_plain/` (the same rules with TradingView's fill model), `results/pine_review/` (independent
review).
