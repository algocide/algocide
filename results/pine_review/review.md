# Adversarial review of the Pine tournament (engine, broker, evaluation, data)

Review of 2026-09-29 against the code on disk at commit `251366d` (exit-scope fix, 1-minute magnifier). Nothing under
`src/`, `experiments/`, `docs/`, `tests/`, `data/` or `results/pine*/` was modified (the broker was only monkeypatched
inside my own processes for sensitivity runs). All scripts and outputs are in `results/pine_review/`; commands assume
`cd /home/user/algocide`.

## Bottom line

* **No error was found that inflates the scripts at the top of the magnified ranking.** The whole top 14 of
  `results/pine_mag/ranking.csv` except #6 (the coin-flip script, excluded as `random_signal` once E1 is fixed), 13
  scripts, was re-implemented from the Pine
  source with pandas/numpy and a hand-written account (no pinebt compiler, runtime or broker). Twelve reproduce the
  engine **trade for trade (entry and exit time, price, quantity) with 0.0 difference in bar-by-bar equity** (two also
  checked on ETH); the thirteenth (MA-Ribbon) differs only by one warm-up trade in January 2021 and has identical
  returns afterwards. Stored tournament equity (`data/pine/t2_mag`,
  `data/pine/t3_mag`) equals the independent equity, and `evaluate.py`'s metrics recompute exactly.
* **The finished re-run with the fixed engine** (`data/pine/t3_mag`, evaluated here with `evaluate.py` as now on disk
  into `results/pine_review/t3_mag_eval/`: 3,566 eligible) has as its top 10 exactly ten of those verified scripts
  (Golden-Cross drops out with 6 trades, the coin-flip script is excluded); #11 was verified too, and #12 is the one
  script of that list hit by F1 below.
* **Evaluation bug (E1, fixed on disk by the main run as ledger item 38 while this review ran; my first pass missed
  it):** `evaluate.py` read `b.flags`, which on a pandas Series is the `Flags` object, so the `lookahead_on` and
  `random_signal` exclusions were never applied. Checked here: 66 flagged scripts were counted as eligible in the t3_mag
  evaluation (61 `lookahead_on` in `results/pine_mag`), the best at rank 33 (36); with the fix the eligible count drops
  3,632 -> 3,566 and the top 10 does not change.
* **Two engine errors change results for whole classes of scripts, in both directions, and can flip eligibility**:
  absolute exit quantities applied in engine units after entries were resized (F1: 94 scripts re-run, 38 change,
  |dScore| up to 1.7, the #14 script loses eligibility), and stop-limit entry orders filled as "stop OR limit" (F2: 59
  re-run, 16 change, the best-ranked affected eligible script goes from +0.66 to an account blown on ETH). Neither
  touches the verified top 10. Smaller issues: v1/v2 `security()` look-ahead not flagged (F3), HMA length rounding (F4),
  two intrabar edge cases (F5, F6).
* Method caveat: the re-implementations follow the same stated rules (next-open market fills, 100% sizing, 7 bps,
  funding at bar close, 1-minute path), so they prove the engine implements those rules; where the rules or their
  implementation depart from TradingView is covered by the findings, the minimal Pine cases and the patched re-runs.

## Findings, by severity

### E1 (high for the pre-registered rules, none for the top 10; already fixed on disk): exclusion flags were ignored

`evaluate.py` (commit version) built `flags = b.flags if isinstance(b.flags, list) else []` where `b` is a pandas Series:
`b.flags` is pandas' `Flags(allows_duplicate_labels=True)` object, never a list, so every script got `flags = []` and the
pre-registered `lookahead_on` exclusion (and the post-hoc `random_signal` one) did nothing in `results/pine`,
`results/pine_mag` and any evaluation made with that version. The main run fixed it (`b["flags"]`, ledger item 38) while
this review was running; my evaluation check had recomputed the metrics but not the eligibility flags, so I missed it.
Impact measured here: 66 flagged scripts were eligible in the t3_mag evaluation (best rank 33; 61 `lookahead_on` scripts
in `results/pine_mag`, best rank 36); with the fix, 3,632 -> 3,566 eligible, top 10 unchanged, `n_el` for the DSR and
the summary shares change slightly. Worth re-running every summary/table that was produced before the fix.

### F1 (medium): absolute `qty` in `strategy.close` / `strategy.exit` / `strategy.order` is applied in engine units after entries were resized

*What.* The pre-registered override replaces entry sizes with 100/pyramiding % of equity, but absolute quantities of
reducing orders stay in the script's own units: `Broker.close()` -> `_close_id(q)`, `ExitOrder.qty` -> `_fill_exit`
(`q = x.qty`), `_exec_order` (`want = q`). A script that enters `qty=0.02` and takes profit with `qty=0.01` closes
0.01 BTC of a ~0.15 BTC engine position (7%, not half); `strategy.order(sell, qty=1)` used to flatten a 1-unit long
reverses to a full short on BTC (1 > position) but trims 1 of ~5 ETH on ETH. An absolute-qty exit is also created per
open trade, so a pyramided position loses N x qty (TradingView's allocation across trades could not be checked).

*Evidence.* `PYTHONPATH=src python3 results/pine_review/broker_cases.py` (output `broker_cases.out`): entry resized to 100
units, then `close(qty=1)` each bar removes 1 unit per bar (97 left after three); `order(short, qty=2)` leaves 98 long;
`exit(qty=10)` on two pyramided trades closes 20.

Real script, `Dual-EMA-Trend-Following-Strategy-with-Staged-Position-Exit.md` (#14 in `results/pine_mag`, #12 in the
finished t3_mag ranking; score 1.059): entries `qty=0.02`,
`strategy.close(qty=0.01)` on every bar while close >= entry + 200 ticks. `indep.s_staged` reproduces the engine exactly
(636 trades, equity diff 0.0). With the script's units rescaled to the resized entry (`staged_units.py`, and the
in-process patch `qtyunits_patch.py` gives the same):

| | BTC OOS Sharpe | BTC OOS trades | ETH OOS Sharpe | ETH OOS trades | ETH CAGR / MDD |
|---|---|---|---|---|---|
| engine (units mixed) | 0.83 | 158 | 1.29 | 271 | 63% / -44% |
| script semantics | 1.33 | 25 | 0.82 | **18** | 7% / -7% |

The engine turns "take profit within two days" into a slow scale-out, and the 0.01-unit partial closes inflate the trade
count; with the script's semantics it fails the 20-trade rule on ETH and is not eligible.

*Class size.* `PYTHONPATH=src python3 results/pine_review/qtyunits_patch.py` re-runs every script with a constant-looking
absolute qty in close/exit/order (timeframe >= 1h: 94 scripts, BTC and ETH, magnified) with quantities converted to the
script's units (`strategy.position_size` also shown in script units, entries without explicit qty left as is):
38 of 94 change score, median |d| 0.26, max 1.73; eligibility 44 -> 47 (Dual-EMA staged exit loses it; four scripts
gain it, all with negative scores under the script's semantics). Largest eligible moves (ranks from `results/pine_mag`): Multi-Timeframe-Donchian-Channel-and-ATR (#75) 0.75 -> -0.38,
ATR-based-Dynamic-Stop-Loss 0.14 -> 0.82, Simple-Momentum-SMA-EMA-Volume (#129) 0.66 -> 0.27,
Dual-Moving-Average-Crossover-Arrow (#121) 0.67 -> 0.85 (`qtyunits_class.csv`). No eligible script of this class
reaches the top-10 cutoff (1.067 in t3_mag) under either semantics; the closest is the staged-exit script (1.059 under
the engine's, ineligible under the script's).

*Fix.* Let the script see its own units: keep per trade the quantity the script asked for (explicit `qty`, else its
declared default qty) and convert every absolute quantity it passes (close/exit/order, and `strategy.position_size`,
`opentrades.size` it reads) by engine qty / script qty; allocate an exit's absolute qty over the position FIFO.
Cheaper alternative: flag scripts whose reducing quantities are not derived from `strategy.position_size`
(`flag_patterns.py` does this) and report them separately.

### F2 (medium): `strategy.entry/order` with both `stop=` and `limit=` is filled as "stop OR limit", not as a stop-limit

*What.* `Broker._candidates` checks `(po.limit, "limit")` and `(po.stop, "stop")` independently, so a buy with
`stop=105, limit=106` while price is 100 fills at the next open (the buy limit at 106 is marketable). TradingView treats
an order with both prices as a stop-limit: the stop must trigger, then a limit order works at the limit price.

*Evidence.* `broker_cases.py`, `stop_limit_entry`: engine buys at 100 on bar 1; with TradingView semantics
(`stoplimit_patch.py`, same case) the buy fills at 105 on bar 4. `stop_limit_entry_short`: engine sells at the next open
instead of on the breakdown. Real scripts (`PYTHONPATH=src python3 results/pine_review/stoplimit_patch.py <file> 86400000`,
output `stoplimit_patch.out`):

| script (rank) | engine BTC / ETH OOS Sharpe (trades) | stop-limit semantics |
|---|---|---|
| EMA-Trend-Following-Automated (#125, eligible) | 0.68 (50) / 0.64 (53) | -0.32 (24) / ETH account blown in 2024 (short from 1,661 held to 3,492): ineligible |
| Confirmed-SMA-Crossover-Momentum (#370, eligible) | 0.14 (32) / 0.66 (28) | 0.90 (14) / 0.14 (14): ineligible |

In EMA-Trend-Following the buy's "stop" is the 10-bar high (+5 ticks) and its limit is the signal close + 20 ticks:
TradingView waits for a breakout and then a pull-back to within 20 ticks of the signal close; the engine buys at the next
open every time.

*Class size.* `stoplimit_class.py` over the 59 such scripts with timeframe >= 1h (of 67): 16 change score (median |d|
0.27), eligibility 27 -> 24 (the two above and Multi-Indicator-Integration-and-Intelligent-Risk-Control 0.17 -> 0.09).
Where the author used stop/limit as a misplaced SL/TP (long: stop below, limit above) the two semantics agree (both
fill at the next open), which is why most do not change.

*Fix.* Give `PriceOrder` a triggered state: before triggering only the stop level is live (fill at the trigger price if
it satisfies the limit, else become a resting limit); `_next_hot` must watch the stop before triggering and the limit
after; a re-issued order starts untriggered. `stoplimit_patch.py` is a working monkeypatch sketch.

### F3 (low; rule consistency, no inflation of engine numbers): v1/v2 `security()` look-ahead is not flagged

Before Pine v3, `security()` defaulted to `barmerge.lookahead_on` (TradingView's v3 migration guide: "lookahead_on (the
default for Pine Script version 2)"). The engine evaluates every request without look-ahead (checked, no leak), but
only an explicit `lookahead_on` argument sets the flag, so v1/v2 and unversioned (= v1) scripts that call `security()`
remain eligible although their authors' backtests used future data, the pre-registered reason for excluding
`lookahead_on`. 82 scripts, 61 eligible, best rank 158 (`security_check.out`, last line: v2 script, `flags=[]`).
Fix: flag `security()` calls without a `lookahead` argument when `version <= 2` or unversioned.

### F4 (low): `ta.hma` smooths with `round(sqrt(n))`; TradingView uses `floor(sqrt(n))`

`runtime.HMA.u`: `m = max(1, int(round(math.sqrt(n))))`; TradingView's Hull MA source is
`wma(2*wma(src, length/2) - wma(src, length), floor(sqrt(length)))`. `PYTHONPATH=src python3 results/pine_review/hma_check.py`
on BTC daily: hma(9) and hma(55) match to 1e-16, hma(14) and hma(21) differ by up to 1.7% and 1.4% of price.
Affected lengths: 3, 7-8, 13-15, 21-24, 31-35, 43-48, 57-63, 73-80, 91-99, ... 110 scripts call hma (85 eligible); the top
users are unaffected (Multi-Trend computes HMA 10/200 but selects EMA; Bollinger-%B uses length 10). Fix:
`int(math.sqrt(n))`.

### F5 (low): a trailing exit can be activated (and its extreme set) by prices from before the entry fill

`_walk` calls `_trail_update(path[s], path[s+1])` over the whole segment, so for a trade opened by a limit order in the
middle of a falling leg (long) or rising leg (short) the leg's starting price, which precedes the fill, counts as a
post-entry extreme. `broker_cases.py` `trailing_activation_from_pre_entry_price`: buy limit fills at 95 on a 100 -> 94
leg, the +3 activation is "reached" by the 100 seen before the fill, the trail sits at 99 and the trade closes at the next
open (96). With the magnifier the window is one minute; only 3 scripts combine limit entries with trailing exits
(ranks 3294, 3311). Fix: for a trade opened inside a segment, update its trail with `[fill_px, p1]` only.

### F6 (low): exits that are already through the market when they become active intrabar do not fill immediately

After an intrabar fill `first` is False, so a new trade's exit on the wrong side of the market (a long's stop above the
fill) fills only on a later downward cross or at the next minute's/bar's open, where TradingView fills it at once.
`magnifier_marketable_exit_intrabar`: entry 101, stop 101.1, engine exits at 101.10 after the price comes back down
(TradingView: about 101.0). At most one minute of delay with the magnifier, up to a bar in the plain run. Rare.

### F7 (info): conventions that only change the warm-up

* `ta.ema` is seeded with the first value (the reference manual's `pine_ema`); TradingView's built-in is commonly
  reported to be na for `length-1` bars and SMA-seeded (tradingview.com is blocked, not confirmed). Measured on
  Multi-Trend-Crossover (EMA 10/200, 3h): an SMA seed changes trades only in Q1 2021; OOS Sharpe identical to 1e-14
  (`indep.s_multitrend(seed="sma")`). Bar-capped runs (under 15m) keep at least 30 days before the OOS start (4,320 bars
  at 10m, 8,640 at 5m), so the seed of an EMA of up to a few hundred bars has vanished (< 1e-7; an EMA of 1,000 bars on
  10m keeps ~2e-4 of the seed gap); uncapped runs start in January 2021 and their OOS windows in 2022 or later. For daily
  charts with a long EMA and a 2022 Last Modified date a residue remains at the OOS start (EMA200: ~20% of the seed gap
  170 bars after the seed), fading within months; for 2023+ dates it is below 1%.
* `ta.highest/lowest` skip na inside the window while `ta.sma` returns na if any value is na: Moving-Average-Ribbon (#12 / #10)
  gets one extra trade on 2021-01-22 because the highest/lowest of its SMA exist 19 bars earlier than with a strict
  window; from 2021-04-20 on trades are identical and the equity ratio is constant (0.966397395737908 to ...915), so its
  OOS returns are identical.
* RSI (Wilder, SMA-seeded RMA of changes from bar 1), ATR (RMA of `ta.tr(true)`) and RMA match the reference `pine_*`
  functions; DMI and KC use `tr(true)` where TradingView's `ta.tr` is na on bar 0 (warm-up only).

### F8 (info, rule not verifiable here): `from_entry` exits issued before their entry order exists

The ledger-37 scope rule ignores an exit with `from_entry` for entries created after the exit's latest call
(`exit_with_from_entry_before_entry`: exit called once while flat on bar 0, entry on bar 2, the 111 high is not taken).
TradingView's reference says an exit generated before its entry is filled waits for the entry; whether that extends to
entry orders created on later bars could not be checked. It matters only for scripts that call `strategy.exit` once and
never again; every script checked here re-issues it each bar or on the entry bar.

### F9 (info): TradingView settings the engine ignores

`calc_on_order_fills=true` (intrabar recalculation after a fill) is ignored: 105 scripts, 82 eligible, including
Bollinger-Percentage-Bands (#9 in `results/pine_mag`, #7 in t3_mag). The engine's result is the bar-close-only version, which is conservative (TradingView's
intrabar recalculation on historical bars is a known source of optimistic fills), not inflated, but it is not the
author's backtest. Also ignored: `strategy.risk.max_*` limits (4 scripts, best rank 235) and OCA groups (2 scripts
uncommented, best rank 1094; the ledger's known limitation).

### F10 (info): robustness of the current #1 (not an engine error)

Pivot-Based-Volume-Weighted-Breakout-Reversal makes 58% (BTC) / 94% (ETH) of its OOS trades as same-day round trips: entry
at the open, then the +3% limit or -2% stop (re-issued each bar from the prior close) fills inside the day. With
1-minute candles this is legitimate (the two levels are 5% apart and never share a minute). `pivot_sensitivity.py`:
requiring the take-profit limit to trade through by 1, 5 or 20 bps changes nothing (score 1.747); stop fills slipping
10 / 30 bps beyond the 2 bps already in the fee give 1.65 / 1.46. The OOS gain is concentrated in the February 2026
sell-off (six consecutive short take-profits in seven days); DSR null 0.02 (BTC) / 0.11 (ETH). Trades opened and closed
inside one daily bar pay no funding (charged at bar close only): about 0.01-0.02% per trade, negligible here.

## Things checked that were fine

* **Independent re-implementations (task 1)**, BTCUSDT, most recent 200,000 bars to 2026-09-29 00:00 UTC, magnified
  (`python3 results/pine_review/indep.py`; engine runs via `run_engine.py` into `engine_runs/`; outputs `compare_*.txt`):

| script (rank in results/pine_mag / in t3_mag) | TF | exercises | result |
|---|---|---|---|
| a Dynamic-ATR-Trailing-Stop (#2 / #2) | 1d | ATR (RMA), `close` + `entry` on the same bar, stop-and-reverse | 99/99 trades identical, equity diff 0.0; ETH 99/99, 0.0 |
| b RSI2-Dynamic-Breakout (#3 / #3) | 1d | Wilder RSI(2), SMA50, `var` counter, `strategy.close` | 87/87, 0.0 |
| c Pivot-Volume-Breakout-Reversal (#1 / #1) | 1d | pivots, TP/SL re-issued each bar with `from_entry`, reversals, 1-minute fills walked minute by minute | 121/121, 0.0; ETH 122/122, 0.0 |
| d Multi-Trend-Crossover (#7 / #5) | 3h | `process_orders_on_close`, EMA 10/200 | 95/95, 0.0 |
| Breakout-Zone-Momentum (#4 / #4) | 1d | SMA cross, highest/lowest, reversal | 119/119, 0.0 |
| Dual-MA-Golden-Cross (#5 / not eligible) | 1d | exit without `from_entry` under the new scope rule, minute limit fills | 6/6, 0.0 (current engine; t2_mag had 96 OOS trades from stale exits, so it drops out) |
| Renko-TEMA (#8 / #6) | 1h | pyramiding 100 (1% legs), `position_avg_price`, `close_all` | 1,289/1,289, 0.0 |
| Bollinger-Percentage-Bands (#9 / #7) | 1d | BB(100, 10 sd) %B of OHLC, RMA(22) with na warm-up, HMA(10), chandelier direction, `initial_capital=1000` | 150/150, 0.0 |
| Turnaround-Tuesday (#10 / #8) | 2h | `dayofweek`, month filter, ATR, RSI3 | 120/120, 0.0 |
| MTF-Heikin-Ashi-MA (#11 / #9) | 1d | lower-timeframe `request.security("180")` of a script-computed HA | 169/169, 0.0 |
| MA-Ribbon (#12 / #10) | 1d | SMA of ohlc4, highest/lowest | one warm-up trade (F7), identical afterwards |
| VWAP-and-RSI-Crossover (#13 / #11) | 2d | rolling VWAP, RSI20, price-less `strategy.exit` (a no-op, as on TradingView) | 101/101, 0.0 |
| Dual-EMA staged exit (#14 / #12) | 1d | `strategy.close(qty=)` partials | 636/636, 0.0 in engine semantics (see F1) |

  Funding is material in these runs (Multi-Trend paid 72% of its initial capital), so the equity match also checks
  funding sign, timing (settlement assigned to the bar containing it, charged on the position at that bar's close) and
  the September-2026 carry rule. EMA seeding: see F7 (no OOS effect measured).
* **Stored results**: the daily equity stored in `data/pine/t3_mag` for BTC equals the independent equity for all twelve
  scripts of the finished top 12 (difference 0.0; MA-Ribbon a constant ratio after its warm-up trade;
  `t3_mag_equity_check.out`), and `data/pine/t2_mag` for a-d.
* **Evaluation (task 3)**: `python3 results/pine_review/check_eval.py [file] [symbol]` recomputes from the npz and raw 1m
  closes (no pinebt import) OOS start, days, Sharpe, CAGR, max drawdown, trade count, buy-and-hold Sharpe and CAGR for
  Pivot BTC, Pivot ETH and Multi-Trend BTC: all equal `results/pine_mag/metrics.parquet` (to 1e-16). OOS window = first
  full UTC day after Last Modified + 1 day (LM 2025-04-24 17:08 -> 2025-04-26), at least 30 days after the first bar;
  on intraday charts that is up to one day later than "first bar after LM + 1 day" (conservative). Trades are counted by
  exit time; partial closes and pyramid legs count separately, as in TradingView's list of trades (this is what lets F1
  inflate counts). `hlr.stats` PSR/DSR formulas are standard (Pearson kurtosis, (k-1)/4 term); the literal DSR is
  degenerate as ledger 36 says; the "null" DSR treats the N eligible scripts as independent trials, which over-deflates
  (conservative); the ranking does not use DSR.
* **Resampling (task 4)**: an independent pandas `groupby(t // tf)` resample gives identical open times and OHLC for 1d,
  3h, 2h and 1h (all comparisons above start from it); close = last minute's close; bars are labelled by open time; a bar
  is used only if it closes by `end_ms`. The 1m files have no gaps or duplicates and consistent OHLC; the only one-minute
  wicks beyond 3% are the 2021-04-18 and 2021-05-19 crashes (in-sample for every script).
* **Higher-timeframe look-ahead**: `PYTHONPATH=src python3 results/pine_review/security_check.py` records every value the
  chart's `SecurityHub` returns and compares it with the last higher-timeframe bar closed by the chart bar's close:
  D close on 1h, D high with `lookahead_on` requested, D `ta.sma(close,3)` on 4h, W close on 1d (Monday anchor), 240
  `close[1]` on 1h, and a v2 script: 0 mismatches in 17,095 values, never a value of the still-forming bar. The
  lower-timeframe direction is covered by the MTF-Heikin-Ashi re-implementation.
* **Magnifier skip logic**: `mag_brute_check.py` compares `_next_hot`/`_advance_trails` with walking every minute on
  Bollinger-Bands-Breakout (trailing stop, #29), Pivot, MACD-MA-Crossover-with-Trailing-Stop and a dense synthetic script
  (stop entries, two trailing brackets, 4h): identical fills and equity in all four.
* **Broker basics** (`broker_cases.out`): with `process_orders_on_close` the fill is at the close and a stop placed on that
  bar only acts from the next bar; a reversal pays two fees; positive funding is paid by longs and received by shorts;
  `close` + `entry` on the same bar closes and re-opens; a `strategy.exit` whose levels become na keeps its old object but
  no longer reaches a later position; a stop or limit already crossed at a bar's (or minute's) open fills at that open;
  limit orders fill on a touch (TradingView's rule; irrelevant for #1, F10).
* `timenow`, `last_bar_index` and `barstate.islast` refer to the run's last bar: they can gate trading by date but carry
  no price information; negative history offsets raise.

## The finished magnified ranking (t3_mag)

`PYTHONPATH=src python3 experiments/pine/evaluate.py --runs data/pine/t3_mag --results results/pine_review/t3_mag_eval`
(the version with the E1 fix, written only to this folder): 3,566 eligible, 28.5% with a positive score, 3.1% beating
buy-and-hold on both assets. Top 10 by score: Pivot-Volume-Breakout-Reversal 1.747, Dynamic-ATR-Trailing-Stop 1.493, RSI2-Dynamic-Breakout
1.243, Breakout-Zone-Momentum 1.224, Multi-Trend-Crossover 1.159, Renko-TEMA 1.114, Bollinger-Percentage-Bands 1.105,
Turnaround-Tuesday 1.104, MTF-Heikin-Ashi-MA 1.068, MA-Ribbon 1.067 - the same scores as in `results/pine_mag` (the
ledger-37 fixes do not affect them). All ten are verified above, and the list is the same as in
`results/pine_final/final_top10.json` (written by the main run while this review was finishing; read, not modified). Null DSR of the ten:
0.002-0.115, none near 0.95.

`python3 results/pine_review/flag_patterns.py results/pine_review/t3_mag_eval/ranking.csv 20` screens the top of a
ranking for the source patterns behind F1-F5 and F9: in the t3_mag top 20 it flags only #12 (F1, the staged-exit script)
and #7 (`calc_on_order_fills`, F9); the two `F4?` marks (#5, #7) are HMA lengths 10/200 and 10, unaffected. If
hand-reading removes scripts from the top 10, the next ones in line are #11 VWAP-and-RSI-Crossover (verified, 1.062) and
#12 Dual-EMA staged exit (1.059, not the script's behaviour, F1), then #13 Dynamic-Envelope-MA (1.034, not re-implemented).

## Suggested actions

1. The engine-level answer for the top 10 stands: every top-10 script of the finished ranking reproduces independently.
   What remains open for them is judgement, not engine error: #1's edge is a few crash days and fragile to stop
   slippage (F10), and no null DSR is above 0.12.
2. Fix F2 (stop-limit) and F1 (script units for absolute quantities), or flag and exclude the two classes
   (`flag_patterns.py` finds them), then re-run those ~160 scripts; F1 already changes the eligibility of #12.
3. Cheap consistency fixes: flag v1/v2 `security()` as `lookahead_on` (F3); `floor(sqrt(n))` in `ta.hma` (F4); trail
   updates from the fill price (F5); immediate fill for exits placed through the market intrabar (F6).

## Files (all in `results/pine_review/`)

`indep.py` (independent implementations and trade-by-trade comparison; `compare_*.txt`), `run_engine.py` + `engine_runs/`
(pickled engine runs), `check_eval.py` (evaluation recomputation), `broker_cases.py` / `.out` (minimal Pine cases),
`security_check.py` / `.out`, `mag_brute_check.py` / `.out`, `hma_check.py` / `.out`, `staged_units.py` / `.out`,
`qtyunits_patch.py` + `qtyunits_class.csv` / `.out` (F1 class), `stoplimit_patch.py` + `stoplimit_class.py` +
`stoplimit_class.csv` / `.out` (F2 class), `pivot_sensitivity.py` / `.out`, `flag_patterns.py`, `t3_mag_eval/`
(evaluation of the finished magnified run), `t3_mag_equity_check.out`.
