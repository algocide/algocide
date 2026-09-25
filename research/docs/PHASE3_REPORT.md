# PHASE3_REPORT.md — the three videos' strategies, tested (2026-09-25)

Videos (details in docs/VIDEO_REVIEW.md, second batch): V3 "Claude Tested Over 9,000 Trading Strategies" (mean reversion
survives), V4 "I made AI trading bots compete" (15 leveraged LLM bots, one winner), V5 "Letting Claude Trade For A Month,
$102k" (levered momentum picks in one strong month). None discloses rules, so each family was tested with standard,
predeclared rules (docs/PROTOCOL_PHASE3.md), on daily bars of the 59 US-listed underlyings that have xyz perps
(2024-09-23 → 2026-09-23) and on BTC/ETH/HYPE, with perp execution costs and funding, in two modes: a 5-slot portfolio
(edge measurement) and the $100 single-position account. Development Sep 2024–Sep 2025; validation Sep 2025–Mar 2026;
holdout Mar–Sep 2026 opened once for one candidate. Overnight holding was allowed (the strategies need it); this
departs from phase 1's session-only rule. 16 trials (10 configurations + 6 robustness neighbours).

## Ranking by evidence (portfolio mode, base costs, $100 split over 5 slots)
| Rank | Strategy (video) | Dev net / PF | Validation net / PF / trades | Adverse val | Holdout (one look) | Verdict |
|---|---|---|---|---|---|---|
| 1 | **MOM-RS(120, top 5, hold 20)** — rank by 120-day return, long the top 5 above SMA50, 2-ATR stop, 20-day hold (V5 analog) | +$47.7 / 2.11 | +$62.6 / 2.50 / 35 | +$61.7 | **+$23.7 / 1.35 / 41 trades**; CI on expectancy [−1.5, +3.1]; adverse +23.3; standard fees +22.3 | Most profitable, **promising, not validated** |
| 2 | MR-SHORT-RSI2(90) — short RSI(2) > 90 below SMA200 (V3 mirror) | +$1.6 / 1.11 | +$7.0 / 1.12 / 107 | +$6.0 | not opened | Marginal, consistent across stocks and crypto (+$5.2 val); unproven |
| 3 | BO(55) — 55-day breakout above SMA200, 3-ATR trail (V5 analog) | −$0.7 / 0.87 | +$12.8 / 1.57 / 29 | +$12.4 | not opened | Development negative |
| 4 | MOM-RS(60, top 5) | −$14.9 / 0.81 | +$71.5 / 2.93 / 33 | +$71.2 | not opened | Development negative |
| 5–10 | MR-RSI2(10), MR-RSI2(5), MR-BB(2.0), MR-BB(2.5), MR-3DOWN (V3's family), BO(20) | +$5.7 to +$15.0 (MR longs) | **−$9.7 to −$20.4** (all MR longs) | negative | not opened | **V3's claim does not hold out of sample here** |
Baseline: equal-weight buy-and-hold of the same 59 names returned +87% (mean) / +29% (median) in development and +23% /
−2.5% in validation; BTC/ETH/HYPE −35% in validation.

## What the numbers mean
* **Momentum (V5's approach) is the only family positive in development, validation and holdout, and all seven
  nearby parameter sets are positive in both in-sample periods.** But: the holdout profit is concentrated (KORU +$20.3,
  SNDK +$18.2, WDC +$10.9; without the best trade the holdout is +$3.4; without the top three it is −$26); its equity
  peaked at +$66 in June 2026 and gave back $42 by August (entries in June–July lost $49); the expectancy CI spans zero;
  and the gains sit in the 2025–26 memory/AI mania names. This is a bet on that regime continuing.
* **Perp execution cross-check** (May–Sep 2026, the same rule on the underlying vs on xyz perp sampled prices):
  −$47.9 vs −$50.0 in portfolio mode, 33 vs 34 trades, funding −$1.7 in both. Executing on the perps changes little;
  the period itself was bad for the rule.
* **The $100 one-position account cannot trade it**: 3 holdout trades, −$0.74. A top-5 momentum rule needs breadth
  (five concurrent slots), i.e. either five $20 positions (below the $10 minimum after rounding for $1,000+ names such
  as SNDK/MU at 0.001 units) or a larger account. At $100 with one slot, MR-SHORT-RSI2 was the only rule with a
  positive validation (+$3.2, PF 1.49, 24 trades, 4/6 windows) — small and unproven.
* **V3's finding (mean reversion) inverted out of sample**: every long mean-reversion variant went from PF 1.2–3.5 in
  development to PF 0.5–0.9 in validation, on 31–157 trades. The short mirror was mildly positive. The video's own
  commenters' objections (undisclosed rules, survivorship of large caps) stand; on this universe and period the claim
  is not reproduced.
* **V4 is a lottery**: with zero edge, 15 random 40x BTC bets over three rounds produce a best bot with a median gain of
  $2,300–$4,000 on $1,000 while the median bot loses and 54–56% of bots lose (results/phase3/lottery_video2.csv). The
  video's $175 "winner" is below what chance alone yields for the best of fifteen. There is nothing to trade there.

## Practical answer to "the most profitable ones"
1. MOM-RS(120, top 5, hold 20) on the xyz stock perps, five concurrent slots, 2-ATR stop, weekly rebalance, long only.
   Evidence: positive in all three periods; fragile (one-trade dependence, a −$49 stretch inside the holdout,
   regime-bound). Needs an account of roughly $500+ so that five slots clear the $10 minimum after size rounding.
2. MR-SHORT-RSI2(90): small, consistent, fits the $100 single-slot account; unproven (no holdout look).
3. Nothing from V4; V3's long mean reversion is rejected on this data.
None of these is validated in the phase-1 sense (100+ out-of-sample trades over 40+ days with a holdout PF > 1 and no
single-trade dependence). The right next step is prospective: run MOM-RS(120) in paper mode across the 59 names from a
machine with API access, weekly, and judge it after two quarters; keep MR-SHORT-RSI2 as the $100-account observation.

## Files
EXPERIMENTS.csv (phase 3 rows, universes daily_stocks / daily_crypto), results/phase3/experiments_*.csv, robustness_momrs.csv,
holdout_stocks.csv, lottery_video2.csv, crosscheck_*.json, trades/*.parquet; figures results/figures/phase3_*.png;
code src/hlr2/daily.py, daily_data.py, experiments/run_phase3.py, phase3_*.py; tests/test_daily.py.
