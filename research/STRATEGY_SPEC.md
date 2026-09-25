# STRATEGY_SPEC.md — frozen rules of the leading candidate (frozen 2026-09-25 09:35 UTC, before the holdout was opened)

**Status: exploratory, not validated.** See FINAL_REPORT.md for why. This document freezes the rules so that a
prospective paper test measures exactly this version.

## Identity
`F4:channel_bo(atr_mult=1.5, n=12, tf=15m)` — 15-minute channel breakout with ATR stop and N-bar trailing exit, US
regular session only, on the five selected trade.xyz US-stock perpetuals: `xyz:SNDK`, `xyz:MU`, `xyz:NVDA`, `xyz:META`,
`xyz:GOOGL` (contract type: USDC-margined linear perpetual on the xyz HIP-3 dex; not shares, no ownership; growth-mode
fees enabled as of 2026-09-25; max leverage 10x SNDK/MU, 20x NVDA/META/GOOGL; szDecimals 3; $10 minimum order).

## Hypothesis and regime
Intraday continuation: a close beyond the prior 3-hour (12 × 15-minute) high/low during the US cash session tends to
extend within the session, enough to cover 1.5 ATR of risk and the exit at the N-bar opposite extreme. Expected to
work in trending, high-volatility sessions (the 2026 memory-stock regime) and to fail in range-bound tape.

## Data and timeframe
* Signal timeframe: 15-minute bars, decision at bar close (candles in production; the research used 15-minute sampled
  mids, see DATA_AUDIT.md). Only completed bars are used.
* Indicators (causal): `hh[t] = max(high[t-12..t-1])`, `ll[t] = min(low[t-12..t-1])`, `ATR14[t]` = EMA(14) of true range
  (production: candle true range; research: 2 × |Δclose| on sampled data).

## Entry
* Long when `close[t] > hh[t]`; short when `close[t] < ll[t]`; evaluated only at bars where `can_enter` is true:
  in session and at least 90 minutes before the close (i.e. decisions up to 14:30 ET on regular days, 11:30 ET on
  13:00 early closes).
* Execution: market (taker) order submitted immediately after the bar close; modelled fill at the next bar's open
  (production) / next 15-minute observation (research) plus half-spread plus 1 bp slippage.
* Concurrent signals across the five markets: take the one with the lowest snapshot half-spread (ties alphabetical);
  only one position at a time.

## Initial stop
`stop = entry_reference ± 1.5 × ATR14[t]` (reference = signal bar close), rounded to the price tick. Stop-market order
(research: filled at the observed price beyond the stop with a 2 bp adverse penalty; 5 bp in the adverse scenario).

## Trailing exit
After each completed 15-minute bar, `stop = max(stop, ll[t])` for longs (`min(stop, hh[t])` for shorts): the stop
ratchets to the 12-bar opposite extreme. No profit target.

## Time / session exit
* Flat decision at the first completed bar at or after 45 minutes before the close (15:15 ET regular / 12:15 ET early);
  fill within 15 minutes. No overnight or weekend positions.
* No other time exit.

## Position sizing
`qty = floor_szDecimals( $1 / (stop_distance + estimated round-trip cost per unit) )`, then `qty ≤ 2 × equity / price`
(gross leverage cap), then reject if `qty × price < $10`. Estimated round-trip cost per unit = 2 × (fee + half-spread +
slippage) × price + adverse-stop allowance. Equity = current simulated account equity ($100 start).

## Drawdown pause
When equity < high-water mark − $10, no new entries; the open position is managed to its exit. A shadow book keeps
trading so the pause's effect is measurable. Resumption requires a manual review (not automatic).

## Exclusions and required data
* Requires: 15-minute candles for the five markets (≥ 26 bars of warm-up), NYSE calendar, current spread snapshot,
  szDecimals/maxLeverage from `meta(dex="xyz")`, hourly funding (accrued while open).
* Excludes: sessions outside the US regular session; bars spanning a data gap; markets not in growth mode (fees) —
  re-check `growthMode` before each session.

## Research evidence (2026-09-25; dev+validation on sampled mids; holdout reported once in FINAL_REPORT.md)
Development (46 sessions): 65 trades, net +$20.0, PF 1.56. Validation (27 sessions): 37 trades, net +$16.3,
PF 2.06, win 59%, 2 of 3 windows positive, adverse costs +$14.7, standard fees +$10.8, 3 of 5 markets positive.
Sampled-stop stress: f = 0.5 → dev −$22.3 / val +$4.6; f = 0.75 → dev −$32.1 / val −$13.7. This stress is the reason
the candidate is exploratory only.
