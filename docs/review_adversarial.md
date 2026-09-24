# Adversarial review of the strongest findings (independent reviewer, 2026-09-24)

Scope: attempt to invalidate H7, H4, H14, H2 and (briefly) H1, H5 as classified by the lead. Every number below was
either read from a file in this repository or produced by one of the review scripts:
`experiments/review_h7.py`, `experiments/review_data.py`, `experiments/review_h4.py`,
`experiments/review_followup.py` (outputs in `results/review/`). Upstream collectors were read at
`scratchpad/ext/hyperliquid-rwa-dashboard/scripts/refresh_funding.py` and `scratchpad/ext/perp-basis/src/perp_basis/`.
No existing project file was modified.

## 1. Summary table

| Finding | Strongest objection (severity) | Reviewer verdict | One-line reason |
|---|---|---|---|
| H7 premium extremes mean-revert | **Blocking**: the reported 16/33 bps is the unconditional AR(1) decay of an hourly *average*; with honest timing (decide on p_t, execute TWAP in hour t+1) the capturable 6 h reversion is 17 bps pooled, 14 bps for US equities, **9 bps for liquid US equities in the external session** — below the 18 bps standard round trip at any spread; 71% of events sit in internal sessions where the oracle EWMA chases the perp by construction; 34% of events are commodities/FX/indices, not equities | **Rejected at standard fees; inconclusive under (unverified) growth fees** — forward paper test only if growth mode is confirmed and only on liquid US equities in the external session | Statistically real (day-clustered t ≈ 21–36) but economically not capturable |
| H4 short top-K funding, stock-hedged | **Blocking**: fails the literal pre-registered bar (net ≥ rf + 3% = 7.03%); execution at Mon 00:00 UTC is Sunday evening ET (stock closed) and supplies 3.0 of the 4.7 pts via an uncapturable weekend-basis term; executing when the stock trades gives 2.5% (t 1.2); 52% of gross funding comes from names with < $1M/day volume; realistic book capacity ≈ $80k | **Rejected** | Not executable as simulated; below the pre-registered bar even as simulated |
| H14 cross-dex funding differential | **Material**: para:AVGO trades $40k/day notional (median), para:AAOI $90k/day; 4 of 7 para pairs negative; weekly t 0.28 | **Rejected as tradeable; statistically inconclusive** | Untradeable at any size; sign not stable across pairs |
| H2 gold HL vs Binance funding | Minor: the differential is real (HAC t 4.9, 4.30% APR, 85% days > 0); "realised ≈ 0" is an artefact of 4 sign flips × 30 bps in 15 weeks; always-on nets 3.4% APR on notional | **Rejected** (agree) | Below cash on notional, needs two-venue capital; pre-registered 1/7/30-day-hold rule fails |
| H1 cross-venue dislocation | Minor: validation window is effectively July only (tape 64 rows/day in Aug, 7/day in Sep vs 288 full) | **Rejected** (agree) | Nothing found that could flip the sign; gross ≈ 0 |
| H5 native carry | Minor but a mis-summary: HYPE 2026 nets 7.0% on capital, above the pre-registered bar (rf + 2% = 6.03%); "decayed below cash" holds for BTC (3.6%) and ETH (4.2%) only | **Rejected for BTC/ETH; HYPE passes the project's own rule** | Lead's summary contradicts the project's own table |

## 2. Numbered objections with evidence

### H7 — premium extremes on xyz perps

**O1 (blocking). The measured reversion is not capturable at decision time; honestly timed, it is 9–17 bps, below standard fees.**
`fundingHistory` premium `p` is the hourly *average* premium (verified below, O5), stamped at the end of the averaging
hour. The lead's statistic `d_h = -sign(p_t)·(p_{t+h} - p_t)` uses `p_t` as the entry level, but `p_t` is an average
over an hour that has already ended. Re-implementation (`review_h7.py`, same event definition, replication exact:
d1 = 15.9, d6 = 32.6, d24 = 35.6 bps, n = 4185/4180/4162):
* at the first tradeable moment (end of hour t) the extreme has largely decayed: mean |p_t| 37.7 bps → mean
  |p_{t+1}| 24.4 bps; **65% of events are already below their own threshold at t+1**; 12.1% have flipped sign;
* honest measure `e_h = -sign(p_t)·(p_{t+1+h} - p_{t+1})` (direction from p_t, entry = TWAP over hour t+1, exit =
  TWAP over hour t+1+h): pooled e1/e3/e6/e24 = 9.1/14.3/17.3/19.2 bps; external 11.4/16.2/20.1/23.4; overnight
  7.9/14.7/18.0/20.3; weekend 8.3/13.1/15.3/16.3 (day-clustered t between 6.8 and 20.9 — significance is not the issue);
* net of a round trip = reversion − 2×(fee + half-spread + 0.5 impact), honest e6:

| subset | e6 (bps) | std fee 9 bps, hs 1/5/10/20 | growth fee 0.9 bps, hs 1/5/10/20 |
|---|---|---|---|
| all events (n 4180) | 17.3 | −3.7 / −11.7 / −21.7 / −41.7 | +12.5 / +4.5 / −5.5 / −25.5 |
| external (n 1217) | 20.1 | −0.9 / −8.9 / −18.9 / −38.9 | +15.3 / +7.3 / −2.7 / −22.7 |
| US-listed equities only, external (n 532) | 14.0 | −7.0 / −15.0 / −25.0 / −45.0 | +9.2 / +1.2 / −8.8 / −28.8 |
| US equities with ≥ $5M/day volume (24 names), external (n 197) | 9.1 | −11.9 / −19.9 / −29.9 / −49.9 | +4.3 / −3.7 / −13.7 / −33.7 |

The e4b "extreme persists at t+1" rows (31–39 bps at 6 h) condition on `|p_{t+1}| > thr`, which is not known when the
hour-t+1 TWAP is executed (look-ahead). The honest version (decide at end of hour t+1, enter TWAP hour t+2;
`review_followup.py`): n = 1400, f1/f3/f6 = 7.0/13.7/18.3 bps → at standard fees −14.0/−7.3/−2.7 (1 bp half-spread);
at growth fees +2.2/+8.9/+13.5 (1 bp) or −5.8/+0.9/+5.5 (5 bps). Conclusion: H7 is economically dead at the documented
standard HIP-3 fee and survives only if (i) xyz equities are in growth mode (unverified) and (ii) half-spreads are
≤ 5 bps in the thin names that generate the large events.

**O2 (blocking). The reversion is what any stationary hourly average must show; nothing beyond the premium's AR(1).**
Median AR(1) of the hourly premium across the 88 coins is 0.65 (IQR 0.60–0.74). An AR(1) with ρ = 0.65 predicts a
reversion of (1−0.65)×37.7 = 13.2 bps at 1 h and (1−0.65⁶)×37.7 = 34.9 bps at 6 h from a 37.7 bps extreme — the
observed 15.9/32.6. The event study therefore measures the premium's unconditional half-life (~1.6 h), which the funding
mechanism and the oracle construction guarantee; it is not evidence of a tradeable dislocation.

**O3 (material). 71% of events are in internal pricing sessions where the ORACLE moves toward the perp by design.**
2117 weekend + 846 overnight events = 2963 of 4185 (70.8%). Per `docs/venue_verification.md` §7 the internal-session
oracle is `S_t = β S_{t−} + (1−β)(S_{t−} + IPD_t)`, i.e. it chases the impact-price difference; the premium
(impact mid / oracle − 1) decays even if the perp price does not move. No oracle or perp price history exists in the
data, so the perp-vs-oracle decomposition cannot be made (see §3). Supporting evidence that oracle snap-back is at
work: overnight events stamped Sunday ≥ 20:00 ET (the first hours after the weekend reopen) are 25% of overnight
events (213/846) and revert 27.4 bps in 1 h vs 14.9 bps for other overnight events (6 h: 39.0 vs 34.3).

**O4 (material). The universe is not "equity perps", and a large external block is a commodity maintenance artefact.**
The event study runs on all xyz coins with > 60 days of history: 1442 of 4185 events (34%) come from 32 non-US-equity
markets (XYZ100 105 events, SILVER 82, SKHX 81, GOLD 76, PLATINUM 76, JPY 75, SMSN 74, EUR 69, COPPER 62, CL 62,
HYUNDAI 58, PALLADIUM 56, …). These have larger extremes (|p0| 50.8 vs 30.9 bps) and larger reversion (d6 43.2 vs 27.0;
honest e6 21.1 vs 15.2). The ET-based session labels are wrong for them (commodities run 18:00 ET Sun → 17:00 ET Fri with
a daily 17:00–18:00 ET gap; Asian names have their own hours). The single largest external hour bucket is the 18:00 ET
stamp (premium averaged over 17:00–18:00 ET): 231 events = 19% of all external events, 66% of them non-equities
(SILVER 30, GOLD 25, XYZ100 15, JPY 11, EWJ 9, CL 9, PLATINUM 8, PALLADIUM 7) — exactly the commodity maintenance hour in
which external pricing stops; their honest 6 h reversion is 9.1 bps (lead's timing 23.9). US-listed equities only
(`review_followup.py`): honest e6 = 14.0 external / 15.9 overnight / 15.4 weekend / 15.2 all.
Also: stamp t covers (t−1 h, t] (Tue–Fri mean |Δp| peaks at stamps 05:00 = 6.3 bps and 21:00 = 7.8 bps, the hours
containing the 04:00/20:00 ET regime switches), so the code's session labels are shifted by one hour (the 04:00 stamp,
labelled external, is the last internal hour). This shift is small: external events stamped 04:00 (n 72, 5.9%) revert
29.1 bps at 6 h, 05:00 (n 89, 7.3%) 39.1, 09:00/10:00 (n 117, 9.6%) 35.9, all other external hours (n 944) 40.2 — the
04:00 ET oracle switch is **not** what drives the external-session result (this part of the lead's claim holds).

**O5 (supports the lead). `premium` is exactly the hourly-average premium index, and the xyz formula is now known.**
`review_data.py`/`review_followup.py`: for the main dex, `fundingRate = (p + clamp(0.0001 − p, −5e-4, 5e-4))/8` ties
92% of rows to 1e-8. For xyz the tie is exact for 96–100% of rows per month since 2026-01 with
`F = 0.5·(p + clamp(0.0001 − p, −3e-4, +3e-4))/8` (multiplier 1.0 before Dec 2025; Dec 2025 mixed 54/46), 97.6% of all
xyz rows overall; 69% of the 2.4% unmatched rows are funding-exactly-zero hours concentrated in FX perps (EUR 2875,
JPY 2368, GBP 1721 rows since 2026-03). So `p` is the AveragePremiumIndex that enters funding (hourly average per the
docs), the xyz clamp is ±3 bps (not ±5 bps as recorded for native perps in `venue_verification.md`), and xyz shorts earn
a 5.5% APR floor whenever the premium is inside [−2, +4] bps. The threshold uses `shift(1)` (no look-ahead in the
event definition itself); only the 0.95 quantile appears anywhere in the code (grep of `experiments/`, `src/`,
`forward/`, `tests/`); the ledger (entry 14) asserts no other threshold was tried — unrun variants cannot be verified.

**O6 (minor). Dependence: the bootstrap blocks are within-coin, not within-day; significance nevertheless survives.**
Events are appended coin by coin, so `block=10` blocks 10 consecutive events of one coin and ignores same-day
cross-coin clustering (289 event days, mean 14.5 events/day, max 60). Day-clustered t-stats: d1 21.0, d6 35.9, d24 33.4;
one-observation-per-day block bootstrap CI for d6 [30.1, 36.4] (lead: [29.6, 35.7]); honest e6 day-clustered t = 20.9
(all), 14.2 (external), 12.4 (weekend). `circular_block_bootstrap_ci` coverage test (300 sims, iid normal): 0.953 at
n = 300/block 10, 0.943 at n = 4000/block 10, 0.960 on skewed t3+exponential data — fine at H7 sample sizes.

### H4 — short top-K trailing-funding xyz equity perps, hedged with stock

**O7 (blocking). By the project's own pre-registered rule H4 is rejected, not "inconclusive".**
Replicated: 4.68% [0.6, 8.7], t 2.18, 39 weeks; dev 6.9% (23 wks), val 1.5% (16 wks, t 0.41). The pre-registration
says "hedged carries need net APR ≥ risk-free + 3% on committed capital" = 7.03% → FAIL. Even reading it as "excess
over risk-free ≥ 3%" (financing at 4.03% is already deducted), the CI lower bound (0.6%) fails. Further, the
95% CI is really a ~90% CI: `circular_block_bootstrap_ci` with n = 39, block = 4 covers a true mean only 89.7% of the
time (300 sims), so a properly calibrated interval would very likely include 0.

**O8 (blocking). The simulated execution time is infeasible for the stock leg, and it manufactures the basis term.**
Rebalancing at Monday 00:00 UTC = Sunday 20:00 EDT / 19:00 EST: weekend internal pricing session, US stock market
closed (no hedge possible until 04:00 ET pre-market, realistically 09:30 ET). The basis term (+3.0% APR of the 4.7%)
is `p(Mon 00:00 UTC) − p(week end)`: the Sunday-evening premium reverting into Monday — H11's own "weekend drift
reverses 78–81% by Monday noon" — which an investor who can only hedge on Monday cannot capture.
`review_h4.py` (project simulator, extra knobs): both legs at Monday 14:00 UTC (≈ 09:30–10:00 ET) → net 2.5% (t 1.21),
basis 0.6%; funding from 00:00 but basis marked at 14:00 → 2.3%; 14:00 UTC without basis → 1.9%; 14:00 UTC with
5 bps perp half-spread → 0.4%; 10 bps → −2.1%; 14:00 UTC with growth fees → 6.6% (t 3.18), still below 7.03%.

**O9 (blocking). Profits come from markets that cannot absorb a position.**
OI snapshots (2026-08-14 → 09-23, 45 points/coin, `data/derived/oi_snapshots.parquet`, median 24 h notional volume):
xyz:BIRD $0.04M/day (selected 11 weeks, 5.6% of gross funding; OI $0.1M), DKNG $0.09M, BX $0.15M, RIVN $0.16M, GME
$0.48M, USAR $0.55M, HIMS $0.60M, LLY $0.65M. 47% of the 195 coin-weeks (52% of gross funding) are in names with
< $1M/day; 65% of gross funding in names < $5M/day. If each position is capped at 10% of the least liquid selected
name's daily volume, the median position is $16k (25th pct $4k) and the median 5-name book ≈ $80k. 51 of 109 xyz
markets trade < $1M/day; the "1 bp half-spread" assumption is unsupported for any of them.

**O10 (minor, process). E5 is labelled "pre-registered battery" but is not, and validation was inspected repeatedly.**
The pre-registration's H4 section commits only to "net APR on committed capital, monthly series, drawdown, dependence
on the top-5 coin-weeks" and K ∈ {5, 10}, trailing 7 d. Start dates, K = 3/7, trailing 3 d/14 d, cost and funding
stresses and execution delays were run on the full sample (including validation) after validation had been seen in
E4 runs 2 and 3 (ledger entries 11, 13, 15 admit the looks). "Trailing 3 d = 6.9%" is therefore a post-validation
neighbourhood number. Since every variant fails O7–O9 the conclusion is unaffected; the label should be corrected.

**O11 (checked, no bug). Cost/financing/basis accounting in `h4_equity_harvest`.** One-way costs at entry and one-way at
exit (round trip per position lifetime): correct. Financing = 4.03% on stock notional, capital = 1.25× notional: correct.
Basis sign (short perp gains when premium falls): correct. Unbooked 1-hour premium change across week boundaries for
held-over positions: +0.10 bps mean (n 73), negligible. Final-week exits never charged: ≈ 9.6 bps of capital once,
negligible. Funding sign (positive = longs pay shorts): correct. The 5.5% APR floor (O5) is what the naive K=all
variant (7.9% gross) mostly collects; selection adds ≈ 3 pts gross before the reversal risk that "funding ×0.5" exposes.

### H14 — cross-dex same-underlying differential

**O12 (material). The only positive pair is untradeable, and the pair signs are mixed.** para:AVGO median OI $1.31M,
median 24 h notional volume **$0.04M**; para:AAOI $0.09M/day; para:UNITREE $0.59M/day (xyz:AVGO $2.37M/day). The
23.1% mean differential is not outlier-driven (21.9% excluding the top 1% |diff| hours; para:AVGO funding 16/16/68/37%
APR by month vs xyz:AVGO 12/10/13/11) — it is the funding of a market with no volume. Across the seven para/xyz pairs
three are positive and four negative; the three hyna/main pairs are negative. Weekly net t = 0.28 (n 15).
Also: the hyna bundle ends 2026-09-01, so the "2026-09: 178%" monthly entry for hyna:BTC|BTC is 24 hours of data.
Sign conventions in `h14_cross_dex` are correct (carry = sgn·(r₁−r₂); basis = sgn·(Δp₂−Δp₁); costs on flips only).

### H2 — gold funding differential HL vs Binance

**O13 (minor). The differential is real; the "realised ≈ 0" number is a trading-rule artefact; verdict still rejected.**
`review_data.py`: 119 days, mean 4.30% APR, sd 6.37%, autocorr 0.33, 85% of days positive, HAC(7) t = 4.91; monthly
2.2/4.9/8.3/4.1/2.3% (May–Sep; Aug–Sep tape sparse). The sign-following rule flipped 4 times in 15 weeks at 30.3 bps
each (121 bps) against 7.4 bps/week gross, hence −0.7 bps/week. Always-on short-HL/long-Binance: 140 bps gross,
110 bps net over 119 days = 3.37% APR on notional — below cash, on two-venue margin, with basis risk. The
pre-registered 1/7/30-day-hold nets are −29/−22/+5 bps (`results/e1/e1_results.json`) → rejected under the rule as
written. Note the structural source: xyz's 5.5% APR interest floor (O5) plus a small positive premium vs Binance 4.3%.

### H1 and H5 (brief)

**O14 (minor).** H1: gross mean ≈ 0 at every fee regime; the July-only validation (HL gold snapshots per day: May 298,
Jun 302, Jul 294, Aug 64, Sep 7) cannot flip a sign. Tape: 0 duplicate (ts, venue, asset) rows, all three live venues
present in 75.5% of snapshots, snapshot gap median 5.0 min / q99 59.8 min; `ts` is one wall-clock capture time shared by
the parallel venue calls (`snapshot.py`), Yahoo rows carry bar time plus `data_age_sec`.
**O15 (minor, mis-summary).** H5: by the project's own table HYPE 2026 = 9.2% gross → 7.0% net on capital, above the
pre-registered H5 bar (risk-free + 2% = 6.03%), with worst 30-day funding +28 bps and 7% negative hours; BTC (3.6%)
and ETH (4.2%) are below. "Decayed to below cash in 2026" is true for BTC/ETH only. Main-dex 8 h→1 h cadence
(2023-06-08) affects 79 BTC rows and is handled by interval detection; immaterial.

### Cross-cutting

**O16 (minor). Survivorship cannot be excluded.** All 109 xyz coins end at 2026-09-23 23:00; upstream
`fetch_live_coins` skips `isDelisted` assets and the upstream clone is a single squashed commit (`0a7c1fd`), so any
xyz market delisted before the baker's first run is absent and undetectable here. Direction of bias unknown; the
names most likely to be delisted are the thin ones H4 profits from.
**O17 (minor).** Forward `paper_trader.py` (PremiumReversion) uses the point-in-time `premium` from
`metaAndAssetCtxs`, a different quantity from the hourly average the backtest was built on; the forward test will not
measure the backtested statistic.

## 3. Checks that could not be performed (and why)

1. Executable spreads/depth for xyz equity perps: no order-book data (network blocked); only xyz:GOLD/CL top-of-book is
   observed (0.23/0.53 bps median) and both are top-5 by OI, not representative of sub-$1M/day names.
2. Growth-mode status per xyz asset (decides H7's sign): docs.trade.xyz blocked.
3. Perp-price vs oracle decomposition of premium reversion: no oracle or perp price history in either dataset
   (OI snapshots hold one mark price per day for 40 days); could not test "oracle catches up vs perp moves" directly,
   only indirectly (O3, O4).
4. Stock close vs oracle relationship / hedge tracking error: no oracle series; only a magnitude note — mean |premium|
   8–11 bps per hour and weekly |Δpremium| of the same order as the weekly funding of the top-5 (≈ 21 bps/week).
5. Delisted xyz markets (O16): API and upstream history unavailable.
6. Thresholds "tried but not committed" for H7: only the code and the ledger's statement are checkable.
7. The fundingHistory `time` = end-of-averaging-hour convention was inferred from the data (|Δp| peaks at the stamps
   containing the 04:00/20:00 ET switches), not from documentation.
