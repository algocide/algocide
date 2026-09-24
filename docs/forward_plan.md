# Forward paper-trading plan

Purpose: (1) verify operations (data, fills, funding accrual, kill switches) and (2) collect the execution-level
evidence this session could not obtain (executable spreads, impact prices, growth-mode fee status), before any real
capital. Paper trading here means simulated fills against observed quotes; nothing is sent to the exchange.

## Step 0 — prerequisites (one afternoon)
1. Run `forward/collector.py --every 60 --l2 <finalist markets>` on a small VM (≈220 weight/min, under the 1200/min IP limit).
   Keep it running permanently: open-interest and premium history exist only if recorded.
2. Live-verify with `perpDexs`/`meta` (a) the dex list and identifiers, (b) `maxLeverage` per market, (c) whether growth mode
   is active for the xyz equity markets (fee shown by `userFees` for a test address, or the exchange UI), (d) the identity,
   fees and liquidity of the `para` dex. Record answers in the ledger.
3. Smoke-test `forward/paper_trader.py` on testnet (`--base https://api.hyperliquid-testnet.xyz`) for one day.

## Step 1 — paper strategies and pre-declared criteria

| Strategy | Frequency (independent opportunities) | Minimum sample before judging | PASS (continue to small real capital) | FAIL (stop) |
|---|---|---|---|---|
| H7 hedged premium reversion (`PremiumReversion`, external sessions, top-15 liquid xyz names, thr 30 bps, exit at |p|<7.5 bps or 6 h) | ≈3–6 events per business day across the universe (≈105 persisting external events / 11 months in the liquid subset) | 60 completed events (≈4–8 weeks) | mean net ≥ +8 bps/event with block-bootstrap 95% lower bound > 0, using OBSERVED touch prices and impact prices; ≥55% wins | mean net ≤ 0 after 60 events, or observed half-spread in the traded names > 15 bps on median |
| H4 equity funding harvest (`EquityFundingHarvest`, K=5, weekly, liquid names only) — only if growth-mode fees are confirmed | 1 rebalance/week; funding accrues hourly | 12 rebalances (≈3 months) | net weekly mean > 0 with t > 2 AND gross funding APR of selected names ≥ 8% | net ≤ 0 after 12 weeks, or fees confirmed at 9 bps taker (standard) → do not run |
| H14 para/xyz same-stock differential — monitor only | daily | 12 weeks of collector data | mean differential ≥ 15% APR with ≥ 70% of days same sign, para top-of-book depth ≥ $20k | otherwise drop |
| H3 WTI weekend reversal — monitor only | 1/week | 40 weekends | corr(weekend move, 2 h reopen move) < −0.4 with p < 0.01 | otherwise drop |

## Step 2 — risk limits and kill switches (`forward/paper_trader.py`, `RiskLimits`)
* Gross paper notional ≤ $20k; per-market ≤ $5k (scale with the capital scenario: $5k / $25k / $100k → per-market 5% / 5% / 5%).
* Daily loss ≤ 1.5% of scenario capital, drawdown ≤ 3% → flatten and halt (file `KILLED`; manual reset).
* Data staleness > 180 s, 10 consecutive API errors, or hard stop time → halt.
* Operational log review daily: `forward/evaluate.py data/paper` prints the criteria above from the JSONL logs.

## Step 3 — sizing scenarios (feasibility, not recommendations)
| Capital | Positions | Per-position | Notes |
|---|---|---|---|
| $5,000 | 2–3 | $1,000 | Costs dominate: at 9 bps taker a $1,000 round trip is $1.80 + hedge; only worthwhile if growth-mode fees; hedge leg needs a broker with fractional shares |
| $25,000 | 5 | $2,500 | H7 at ~+8 bps net/event × 4 events/day ≈ $8/day ≈ 12%/yr on capital before hedge frictions — only if forward evidence confirms |
| $100,000 | 5–10 | $5–10k | Capacity limited by OI caps ($25–100M per market, fine) and by top-of-book depth in thin names (unknown; collector records it) |

## What would make me stop immediately
Executable spreads in the liquid xyz names above 15 bps; growth mode confirmed off AND net per event < 5 bps;
any evidence that the oracle, not the perp, does the reverting during external sessions (see review objection);
a rule change to HIP-3 fees or funding.
