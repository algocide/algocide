# ETH 15-minute up/down: what survived the hidden third

Session 2026-09-29/30, two-hour budget. Rules fixed before any data was split: `docs/eth15/plan.md`. Every step,
with times: `docs/eth15/ledger.md`. Control panel: <https://claude.ai/artifact/YAS7tESA11fJnXGVzmovYJ> (source
`results/eth15/control_panel.html`).

## Setup

* A bet goes on at a 15-minute candle's open and settles at its close. Up wins if close >= open.
* Payout 1.8x (a win nets +0.8, a loss costs the stake), fee 0.06% of the stake. Break-even win rate **55.59%**;
  the bar was **57%**.
* Data: Binance ETHUSDT perpetual, 1-minute candles with taker-buy volume, 2021-01-01 to 2026-09-28, grouped into
  201,312 fifteen-minute candles. The **last third (67,104 candles, 2024-10-30 to 2026-09-28) was hidden**: written
  to its own file with a SHA-256, read once by `validate.py`, which refused to run until the survivor list was
  committed (ed42a84, 23:23 UTC).
* 728 candidates were tested on the first two thirds: 704 rules in 14 families (previous-candle momentum and
  reversal, streaks, time of day and weekday, funding times, volume and range, wicks, RSI, Bollinger, taker flow,
  BTC-ETH divergence, last-minute moves) and 24 model thresholds (logistic regression and LightGBM on 24 features,
  fitted on the first 70% of the research set and scored on the last 30%). Every one is in
  `results/eth15/research_log.jsonl`.
* Survivor rule (fixed in advance): at least 200 bets, win rate above 57%, and above break-even in both halves of
  the sample. 21 survived.
* Half Kelly: stake = 0.5 x (0.7994 p - 1.0006 (1 - p)) / (0.7994 x 1.0006) of the bankroll, with p = the
  strategy's research win rate. Starting bankroll 1,000.

## Result on the hidden candles

17 of the 21 survivors stayed above 57%; 20 of 21 stayed above break-even. Sorted by the lower end of the 95%
interval:

| Strategy | Research win rate (bets) | Holdout win rate (bets) | 95% interval | p vs break-even | 57% bar | Half-Kelly stake | $1,000 becomes | Worst drawdown |
|---|---|---|---|---|---|---|---|---|
| Logistic model, threshold 0.59 | 59.6% (3,471) | 58.5% (5,704) | 57.2-59.8% | <0.0001 | held | 4.55% | $8.2M | -92% |
| Streak fade, 5 in a row | 57.3% (5,521) | 59.0% (2,693) | 57.1-60.8% | 0.0002 | held | 1.98% | $17.4K | -36% |
| Logistic model, threshold 0.60 | 60.4% (2,114) | 58.7% (3,486) | 57.1-60.3% | 0.0001 | held | 5.42% | $726.1K | -91% |
| Streak fade, 6 in a row | 58.3% (2,355) | 60.0% (1,104) | 57.0-62.8% | 0.0018 | held | 3.05% | $9,480 | -36% |
| Logistic model, threshold 0.58 | 59.0% (5,335) | 58.0% (8,841) | 57.0-59.0% | <0.0001 | held | 3.82% | $13.1M | -85% |
| LightGBM, threshold 0.60 | 60.6% (3,126) | 58.3% (4,735) | 56.9-59.7% | <0.0001 | held | 5.59% | $1.3M | -78% |
| RSI(3) fade, RSI < 10 | 57.4% (11,803) | 57.9% (5,836) | 56.6-59.2% | 0.0002 | held | 2.06% | $57.4K | -56% |
| LightGBM, threshold 0.59 | 59.5% (4,623) | 57.7% (7,163) | 56.6-58.9% | 0.0001 | held | 4.43% | $808.7K | -85% |
| RSI(3) fade, RSI < 15 | 57.5% (21,660) | 57.4% (10,969) | 56.5-58.4% | <0.0001 | held | 2.19% | $375.1K | -76% |
| RSI(3) fade, RSI < 20 | 57.1% (33,688) | 57.1% (16,962) | 56.4-57.9% | <0.0001 | held | 1.72% | $471.5K | -74% |
| Logistic model, threshold 0.57 | 58.3% (7,850) | 57.2% (12,920) | 56.3-58.0% | 0.0002 | held | 3.00% | $527.4K | -88% |
| Logistic model, threshold 0.61 | 62.0% (1,234) | 58.4% (2,009) | 56.2-60.5% | 0.0061 | held | 7.21% | $23.0K | -93% |
| Logistic model, threshold 0.62 | 62.1% (660) | 58.9% (1,089) | 55.9-61.7% | 0.0159 | held | 7.35% | $10.8K | -87% |
| LightGBM, threshold 0.61 | 62.3% (2,010) | 57.4% (3,002) | 55.6-59.1% | 0.0263 | held | 7.54% | $1,462 | -97% |
| LightGBM, threshold 0.62 | 62.4% (1,247) | 57.6% (1,821) | 55.3-59.9% | 0.0436 | held | 7.65% | $2,197 | -97% |
| Streak fade, 7 in a row | 59.1% (982) | 57.2% (442) | 52.6-61.8% | 0.2580 | held | 3.91% | $1,276 | -51% |
| Funding, > 3 bps | 58.7% (482) | 73.3% (15) | 48.0-89.1% | 0.1298 | held | 3.52% | $1,175 | -7% |
| LightGBM, threshold 0.58 | 58.4% (6,683) | 56.9% (10,411) | 55.9-57.8% | 0.0040 | above break-even only | 3.13% | $33.6K | -86% |
| Logistic model, threshold 0.56 | 57.3% (10,947) | 56.4% (18,042) | 55.6-57.1% | 0.0185 | above break-even only | 1.98% | $8,591 | -92% |
| LightGBM, threshold 0.57 | 57.6% (9,442) | 56.4% (14,774) | 55.6-57.2% | 0.0308 | above break-even only | 2.32% | $4,737 | -86% |
| Streak fade, 8 in a row | 57.5% (402) | 54.5% (189) | 47.4-61.4% | 0.6471 | failed | 2.11% | $894 | -28% |

Rules in plain words:

* **Streak fade:** after 5 (or 6) 15-minute candles in the same direction, bet the next one goes the other way.
* **RSI(3) fade:** when the 3-period RSI of 15-minute closes is below 15, bet Up; above 85, bet Down.
* **Models:** the same idea learned from 24 features; they bet when their probability of Up is beyond the threshold.

## What it means

**One effect, seen through several windows.** Nearly every survivor is a bet that a stretched 15-minute move
reverses on the next candle. The models learned the same thing from the data. Direction reverses, size does not: fading
the previous candle wins 52% of the time although consecutive 15-minute returns are uncorrelated (+0.007). Only a bet
that pays on direction can use that.

**It held where it could have failed.** Out of sample (2024-10 to 2026-09), in every calendar year from 2021 to 2026
(56-61% for the streak and RSI fades), on Binance spot and on the index price, a multi-exchange spot composite close
to what oracles settle on (57-60%; ledger 6-7). Those checks were run after the holdout was opened, so they are
labelled post-hoc.

**It is thin and it lives in the first minute.** At 57-60% the edge is 2 to 8 cents per dollar staked. Betting one
minute after the candle opens drops the RSI fades to 56.2% (break-even 55.6%). The streak fades keep more: 57.5% for
5 in a row and 58.7% for 6 in a row at one minute late.

**Half Kelly was too aggressive here.** Half Kelly uses the research win rate. The models won 1-5 points less on
the hidden candles than in research, so their half-Kelly stake was close to full Kelly at the win rate they actually
had. That produced 78-97% drawdowns even where the bankroll ended far higher. The streak fades, whose holdout win rate
matched research, drew down 36%. For real money a quarter Kelly or less is the sensible setting; the control panel's
slider shows the effect.

## Before risking money

* **Check the settlement price and time.** Everything here uses the price at the exact candle open. A platform that
  fixes the reference price a few seconds later, or settles on a different feed, changes the result.
* **A fixed 1.8x payout is the key assumption.** On markets where the odds move (prediction markets, order books), a
  known reversal effect gets priced in and the payout on the favoured side drops below 1.8x.
* **Pick one strategy.** The survivors bet on many of the same candles; stacking them multiplies the stake on one
  effect.
* **Compounding to millions is not realistic.** Stake limits and liquidity cap bet sizes long before that.
* **Paper trade first.** Run the 6-in-a-row streak fade and the RSI(3) < 15 fade live for a few weeks on the actual
  platform, logging the reference price, your bet time and the payout, at quarter Kelly or less.

## Files

| Path | Content |
|---|---|
| `docs/eth15/plan.md` | The pre-registration (committed 306b694 before any result) |
| `docs/eth15/ledger.md` | Every step with times, POST-HOC marked |
| `experiments/eth15/` | `prepare.py` (candles, split, hash), `lib.py` (features, 14 rule families, Kelly), `research.py`, `validate.py` (holdout guard), `download_sources.py`, `posthoc_sources.py`, `posthoc_latency.py`, `build_panel.py` + `panel_template.html` |
| `results/eth15/research_log.jsonl` | All 728 candidates with bets, wins and win rates on each half |
| `results/eth15/survivors.json` | The 21 frozen survivors (committed ed42a84 before the holdout was opened) |
| `results/eth15/holdout_results.json`, `bets.json` | Holdout validation and every bet's outcome |
| `results/eth15/posthoc_*.json` | Price-source and latency checks |
| `results/eth15/control_panel.html` | The control panel |

```bash
python3 experiments/eth15/prepare.py        # needs the Binance 1-minute archives in data/binance/raw
python3 experiments/eth15/research.py       # research set only
git add results/eth15/survivors.json && git commit -m "freeze"   # validate.py refuses to run before this
python3 experiments/eth15/validate.py
python3 experiments/eth15/build_panel.py
```
