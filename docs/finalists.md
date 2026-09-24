# Finalists — specifications, results, and what must happen before real capital

Three candidates survived the screen well enough to specify in detail. None is validated by forward trading. Only H7
is "supported for forward paper test"; H4 and H14 are included because they are the next-best mechanisms and their
forward tests are cheap once the collector runs.

---
## F1 — H7: hedged premium-reversion on trade.xyz (HIP-3) equity perps

**What exactly is the edge?** When the hourly premium of an xyz perp (impact mid vs oracle) is extreme, it reverts
toward zero over the following 1–6 hours. A trader sells the perp when it is rich (premium > +thr) and buys it when
cheap, hedged with the underlying stock during US external pricing sessions (04:00–20:00 ET), capturing the
convergence. Who pays: retail directional flow on 24/7 stock perps that pushes the perp away from the oracle, and slow
market makers on a venue where taker fees (9 bps standard) deter arbitrageurs from correcting small deviations.

**Signal / entry / exit / sizing / holding (as tested and as scaffolded)**
* Signal: |p_t| > trailing-30-day 95th percentile of |p| (pre-registered), or a fixed 30 bps threshold (scaffold).
* Entry: at the start of the next hour (the hourly premium is known only at hour end), only if the extreme persists,
  only in external sessions, only in the 15 most liquid xyz names (by OI). Short perp if p > 0 (long if p < 0), hedge 1:1 with stock.
* Exit: |p| < thr/4 or 6 hours. Sizing: equal notional per event, ≤ 5 concurrent, ≤ 5% of capital per market.

**Evidence (measured, hourly data, Oct 2025–Sep 2026)**
| subset | n | mean |p| at entry | reversion 1h | 3h | 6h |
|---|---|---|---|---|---|
| top-15 liquid, external, persists at t+1 | 105 | 43 bps | 15.7 [7.3, 26.3] | 26.6 [9.7, 51.4] | 31.2 [17.0, 50.5] |
| all 109 names, external, persists at t+1 | 399 | 51 bps | 20.6 [14.4, 27.3] | 32.0 [22.6, 41.6] | 39.3 [29.4, 50.3] |
Reversion scales with the size of the extreme (external events > 80 bps revert ≈ 120 bps in 6 h). Bootstrap CIs use
10-event blocks; the reviewer's day-clustered check is in `docs/review_adversarial.md`.

**Cost break-even (per event, round trip)**: standard HIP-3 taker 18 bps + assumed 2 bps spread crossing + 1 bp impact
+ stock 3 bps ≈ 24 bps → net ≈ +7 bps at 6 h in the liquid subset (lower CI negative); growth-mode fees 1.8 bps →
≈ +25 bps. Half-spreads above ~10 bps in the traded names eliminate the standard-fee version entirely.

**How the evidence could mislead**: (1) the premium is an hourly average of impact prices, not an executable quote —
real entry is at the touch, which is worse in exactly the moments the signal fires; (2) part of the "reversion" can be
the oracle moving to the perp (at session transitions) rather than the perp moving — hedged P&L is still positive when
the stock hedge is live, but not in overnight/weekend sessions; (3) event clustering across names on market-wide moves
inflates the effective sample; (4) survivorship in the xyz coin list (as of 2026-09-23).

**Capacity**: dozens of events per week; each limited by top-of-book depth in the name (unknown; the collector records
it). Realistic for $5k–$100k; not for $1M+.

**Before real capital**: run the collector ≥ 4 weeks; confirm growth-mode/fee status per name; paper-trade ≥ 60 events
with observed touch and impact prices; pass criteria in `docs/forward_plan.md`; confirm the broker can hedge
fractional shares during 04:00–20:00 ET; size for an earnings-gap of 30% on the short perp leg (isolated margin).

---
## F2 — H4: equity-perp funding harvest (short high-funding xyz perps, long stock)

**Edge**: 24/7 levered stock exposure is in demand; longs pay funding (xyz formula halves the standard rate; mean xyz
funding 11% APR, median 5.5%). Persistence (weekly autocorr 0.39; rank corr 0.43) allows selecting next week's payers.

**Rule tested**: Mondays 00:00 UTC, rank mapped xyz names by trailing 7-day mean funding, short the top 5, hedge with
stock, hold a week. Costs: 9 bps taker + 1.5 bps spread/impact per perp side, 1.5 bps per stock side, financing at
4.03% on the stock notional, 25% perp margin.

**Result**: net 4.7% APR on capital [0.6, 8.7] over 39 weeks; development 6.9%, validation 1.5%; Q3-2026 −2.0%; the
basis term (premium reverting after entry) contributes +3.0% and without it the net is 1.7%; costs ×2 → −1.4%;
funding ×0.5 → −0.8%; removing the 5 best coin-weeks → 2.2%. Top payers were thin names (HIMS, GME, USAR, BIRD).
Growth-mode fees would give 8.8% [4.7, 12.9], validation 6.4%.

**Verdict**: inconclusive; effectively rejected at standard fees (fails the ≥ 3% excess bar out of sample and the
cost/funding stresses). Only a growth-mode fee confirmation plus a liquid-name restriction would justify a paper test.

**Kill conditions**: fees confirmed standard; selected names' spreads > 10 bps; funding APR of the top-5 < 8%.

---
## F3 — H14: same-stock funding differential across HIP-3 dexes (para vs xyz)

**Edge**: two perps on the same stock, on two dexes with segmented clienteles, pay different funding; long the low-funding
leg / short the high-funding leg, both on Hyperliquid, no external account. para:AVGO paid 34.6% APR vs xyz:AVGO 11.4%
over Jun–Sep 2026 (differential 23% APR; monthly 4%, 6%, 55%, 26%).

**Result**: weekly sign-following with costs only on flips: +6.3 bps/week, t=0.28, n=15; basis (premium divergence, sd
12 bps) costs 4 bps/week; 7 flips. hyna:BTC/ETH/HYPE vs native: negative after costs. Other para/xyz pairs have ≤ 6 weeks.

**Verdict**: inconclusive. The deployer behind `para` could not be identified from this sandbox (fees, oracle, depth
unknown). Monitor with the collector; act only if the differential stays ≥ 15% APR with ≥ 70% sign consistency for 12
weeks and para depth is adequate.
