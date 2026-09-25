# Protocol, phase 3 (written before any phase-3 test ran) — "test the three videos' strategies"

## What the videos actually claim (docs/VIDEO_REVIEW.md, section 3)
* V1 "Claude Tested Over 9,000 Trading Strategies" (AI Pathways): mean reversion was the only family with a durable
  edge on US stocks; trend/momentum fragile; bootstrapping used for robustness. Exact rules not disclosed.
* V2 "I made AI trading bots compete" (Across The Rubicon): 15 LLM-persona bots, $1,000 each, Hyperliquid; 11 of 15 lost;
  the winner made $175 on a 40x BTC long. No rules; the claim is that a population of leveraged LLM bets contained a winner.
* V3 "Letting Claude Trade For A Month, $102k" (AI Pathways): $66k account, one strong month, leveraged LEAPS on screened
  small caps plus momentum names; +155% in 30 days, no drawdown or multi-month record. Rules not disclosed.

## Families and predeclared configurations (budget 16; all rules standard, since the videos disclose none)
Daily bars, signal at close t, execution at the NEXT open (t+1), stops evaluated on daily high/low (stop first, gap
through the stop fills at the open), exits at the close of the exit signal day's next open.
* MR-RSI2 (V1): long when RSI(2) < {10, 5} and close > SMA200; exit when close > SMA5 or after 10 bars; stop 2×ATR14.
* MR-BB (V1): long when close < lower Bollinger(20, k) with k ∈ {2.0, 2.5} and close > SMA200; exit close ≥ SMA20 or 10 bars; stop 2×ATR14.
* MR-3DOWN (V1): long after 3 consecutive lower closes and close > SMA200; exit close > prior day's high or after 5 bars; stop 2×ATR14.
* MR-SHORT (V1 mirror): short when RSI(2) > 90 and close < SMA200; exit close < SMA5 or 10 bars; stop 2×ATR14.
* MOM-RS (V3 analog): every 5 bars rank the universe by trailing {60, 120}-day return; long the top 5 above SMA50; hold 20 bars; stop 2×ATR14.
* BO (V3 analog): long on close > prior {20, 55}-day high and close > SMA200; trail 3×ATR14; no target.
* LEV (V2): not a strategy; a leverage sensitivity of the best BTC/ETH config at {1, 2, 5, 10, 40}x and a Monte-Carlo
  "15 random 40x bets" simulation to show what a population winner looks like without edge.
Baselines: equal-weight buy-and-hold of the universe; BTC buy-and-hold; cash.
Total predeclared strategy configurations: 2 + 2 + 1 + 1 + 2 + 2 = 10.

## Data
* Underlyings: Yahoo daily OHLC for the 62 stocks/ETFs that have xyz perps (previous session's `stock_daily.parquet`,
  2024-09-23 → 2026-09-23; names with < 400 rows used only from their listing). BTC/ETH daily from the same source.
  These are the UNDERLYING prices (signal input and comparison), not perp executions.
* Perp execution cross-check: 2026-05-01 → 09-25 with the 15-minute sampled perp mids (session open/close samples).
* Splits (by date, all names): development 2024-09-23 → 2025-09-22; validation 2025-09-23 → 2026-03-22 in 6 windows of
  ~30 trading days; holdout 2026-03-23 → 2026-09-23, sealed, ONE look for ONE candidate.
* Survivorship: the universe is today's listed perps (names that failed before listing are absent) — same bias V1's
  own commenters raise. Reported, not fixable here.

## Costs (perp execution assumed; stock commissions not modelled)
Per side: 0.9 bp taker (growth mode) + 1 bp half-spread + 1 bp slippage; adverse: 2× spread and slippage; standard
fees 9 bp. Funding for overnight holds: measured hourly xyz funding where it exists (from 2025-11), else the measured
mean +1.5 bp/day paid by longs (received by shorts). Overnight/weekend perp holding is allowed in this phase (the
videos' strategies require it); this departs from the phase-1 session-only rule and is flagged in every table.

## Two evaluation modes
1. Portfolio (edge measurement, like V1): equal $ per position, up to 5 concurrent, no leverage, all signals taken.
2. $100 account (the user's constraints): one position at a time, $1 planned risk incl. costs, gross ≤ 2× equity,
   szDecimals rounding, $10 minimum, $10 drawdown pause with shadow. Concurrent signals: most extreme signal value
   (lowest RSI / deepest band excursion / highest momentum rank), ties alphabetical.

## Gates (same as phase 1) and reporting
PF ≥ 1.3 on validation, holdout PF > 1 with CI, adverse costs positive, majority of windows positive, not one trade or
day, ≥ 3 instruments, ≥ 100 OOS trades / 40 days where available; day-clustered bootstrap; results by family, direction,
period, instrument; excluding the best trade and day.
