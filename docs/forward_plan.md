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
| F1 HYPE carry (short HYPE perp / long HYPE spot, 25% margin) — operations test | continuous; funding hourly | 8 weeks | realised net funding ≥ 6% annualised; margin logic handled a simulated 30% adverse move without liquidation; spot-leg spread ≤ 5 bps | negative net funding over 8 weeks, or margin top-ups needed more than weekly |
| F2 H7 hedged premium reversion (`PremiumReversion`, point-in-time premium, external sessions, liquid US equities, thr 30 bps, exit at |p| < 7.5 bps or 6 h) — **only if growth mode is confirmed for those names** | ≈ 2–4 events per business day | 60 completed events (≈ 4–8 weeks) | mean net ≥ +8 bps/event with block-bootstrap 95% lower bound > 0 using OBSERVED touch and impact prices; ≥ 55% wins; observed median half-spread ≤ 3 bps | growth mode not confirmed (do not start); mean net ≤ 0 after 60 events; median half-spread > 5 bps |
| H3 WTI weekend reversal — monitor only | 1/week | 40 weekends | corr(weekend move, 2 h reopen move) < −0.4 with p < 0.01 | otherwise drop |
| H4 / H14 — dropped after review; the collector still records the funding and volume needed to revisit them | — | — | — | — |

Note (review O17): the paper trader trades the point-in-time premium a trader actually sees; the backtest statistic was
an hourly average. The collector samples `premium` every 60 s so both can be computed and compared.

## Step 2 — risk limits and kill switches (`forward/paper_trader.py`, `RiskLimits`)
* Gross paper notional ≤ $20k; per-market ≤ $5k (scale with the capital scenario: $5k / $25k / $100k → per-market 5% / 5% / 5%).
* Daily loss ≤ 1.5% of scenario capital, drawdown ≤ 3% → flatten and halt (file `KILLED`; manual reset).
* Data staleness > 180 s, 10 consecutive API errors, or hard stop time → halt.
* Operational log review daily: `forward/evaluate.py data/paper` prints the criteria above from the JSONL logs.

## Step 3 — sizing scenarios (feasibility, not recommendations)
| Capital | Positions | Per-position | Notes |
|---|---|---|---|
| $5,000 | 1 (carry) or 2 (H7) | $1,000–4,000 | Costs dominate H7 ($1.80 per $1,000 round trip at standard fees); carry ≈ $350/yr expected |
| $25,000 | 1 carry + up to 5 H7 | $2,500 | Carry ≈ $1,750/yr; H7 at +5–10 bps net/event × 3 events/day ≈ $4–8/day — only if forward evidence confirms |
| $100,000 | same | $5–10k | Carry has no capacity issue; H7 limited by top-of-book depth in liquid names (unobserved; the collector records it) |

## What would make me stop immediately
Executable spreads in the liquid xyz names above 15 bps; growth mode confirmed off AND net per event < 5 bps;
any evidence that the oracle, not the perp, does the reverting during external sessions (see review objection);
a rule change to HIP-3 fees or funding.
