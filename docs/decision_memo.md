# Decision memo — Hyperliquid edge research (2026-09-24)

## Decision
**No credible, economically meaningful trading edge was found.** After an independent adversarial review, every
directional or relative-value hypothesis tested here is rejected or inconclusive, and the only strategy that passes its
own pre-registered bar is a well-known carry, not an alpha: shorting the HYPE perpetual against HYPE spot on
Hyperliquid, which netted about 7% a year on committed capital in 2026 at 4x perp leverage (≈ +3 points over the 4.03%
T-bill), and fails the bar at conservative leverage. "No sufficiently supported edge" is therefore the outcome.

**Evidence strength.** Facts about the venue are verified only through search snippets of the official docs and the
official SDK, because the sandbox blocked the docs and API. The measured results rest on two public GitHub datasets
(hourly funding/premium for 161 Hyperliquid markets since 2023; a 5-minute cross-venue tape for two HIP-3 commodity
perps since May 2026), whose provenance was cross-checked against two independent snapshot repositories. Execution
costs on HIP-3 markets (spreads, depth, growth-mode fee status) were never observed; every "net" number assumes them.

**Next useful action (cheap, specific).** (1) Allow `api.hyperliquid.xyz` in the environment (or run
`forward/collector.py` on any small VM) and record `metaAndAssetCtxs` plus top-of-book every 60 s for four weeks; that
data settles the two unknowns that decide H7 (executable spreads in liquid US-equity perps; growth-mode fee status).
(2) If growth mode is confirmed for liquid US equities, paper-trade H7's executable version under the pre-declared
criteria in `docs/forward_plan.md`; otherwise drop it. (3) Treat the HYPE carry as an operations exercise, not a
return target. (4) Re-run `experiments/e8_e10_pipeline_when_api_available.py` for the untested candle-based hypotheses.

## Evidence ladder
* **Facts** (official docs via search snippets; SDK source): fee schedule basics, funding formula and 4%/h cap, HIP-3
  2x fee share and growth mode (gold-tracking perps excluded), trade.xyz sessions and oracle, API limits
  (`docs/venue_verification.md`). Data-inferred by the reviewer: the xyz funding formula is
  F = 0.5·[p + clamp(0.01%/8h − p, ±3 bps)]/8 per hour since Dec 2025 (97.6% of rows tie exactly), i.e. xyz shorts earn a
  **5.5% APR floor** whenever the premium sits inside [−2, +4] bps — the structural source of most "funding harvest" income.
* **Measured** (real observations; code in `experiments/`, outputs in `results/`): the table below.
* **Simulations / assumptions**: fees per regime; 1 bp half-spread + 0.5 bp impact on HIP-3 perps (unobserved);
  stock hedge at the oracle price; 4.03% financing; 25% perp margin.
* **Unresolved**: the reviewer's objections preserved below.

## Results (net of modelled costs) after review
| Hypothesis | Measured result | Classification |
|---|---|---|
| H1 cross-venue dislocation, xyz:GOLD / xyz:CL vs Binance/OKX | Gold reverts toward Binance (HAC t −4.1 at 5 min) but ≈ 1 bp per event; WTI none; gross ≈ 0; net −18 bps at standard fees | **Rejected** (too small vs fees) |
| H2 funding differential, HL gold perp vs Binance | +4.3% APR, HAC t 4.9, 85% of days positive; always-on nets 3.4% on notional across two venues (below cash) | **Rejected** (real, not meaningful) |
| H3 weekend drift reversal, WTI | corr −0.77, n = 9 | **Inconclusive** (monitor) |
| H4 equity-perp funding harvest, K = 5, stock-hedged | 4.7% APR as simulated; hedge cannot be placed at the simulated time (Sun 20:00 ET); 2.5% (t 1.2) when both legs trade; 52% of gross funding from names < $1M/day; fails the pre-registered 7.03% bar | **Rejected** |
| H5 native funding carry, short perp / long spot | 2026 net on capital: BTC 3.6%, ETH 4.2% (below 4.03% cash); HYPE 7.0% (2025: 17.4%), 7% negative hours, worst 30-day window +28 bps; at 50% margin HYPE ≈ 5.8% (fails rf + 2%) | BTC/ETH **not meaningful**; HYPE **historically supported modest carry** |
| H6 funding persistence | weekly autocorr 0.39 (xyz), 0.75 (main); x-sec rank corr 0.43 | Supported (statistical property) |
| H7 premium-extreme reversion, xyz perps | Reversion is the premium's AR(1) decay (ρ ≈ 0.65, half-life ≈ 1.6 h); honestly timed capture over 6 h: 17 bps pooled, 14 bps US equities, 9 bps liquid US equities (external session); negative at standard fees for any spread; +4 to +15 bps at growth fees with ≤ 5 bps half-spreads | **Rejected at standard fees; inconclusive at growth fees** |
| H11 weekend premium reversal | corr −0.74 but consistent with the oracle catching up | **Inconclusive** |
| H14 same-stock differential across HIP-3 dexes | para:AVGO 23% APR differential but $40k/day volume; 4 of 7 para pairs negative; hyna pairs negative | **Rejected as tradeable** |
| H8, H9, H10, H12, H13 | not testable here (API blocked) | Pipeline written, not run |

## Why the two survivors are not "edges"
* **HYPE carry**: it is the interest-rate floor (0.01%/8h ≈ 10.95% gross) plus HYPE's speculative long demand, paid to
  anyone who shorts the perp against spot. It is crowded, regime-dependent (BTC/ETH carry compressed from 19–24% gross in
  2024 to 5% in 2026), and its net-of-cost excess over cash depends on running the short at 4x. Operational risks: the
  short perp needs USDC margin that the spot leg does not supply; a fast HYPE rally forces margin top-ups.
* **H7**: the premium reverts because the funding and oracle mechanisms make it revert; what a trader can capture after
  the hourly average is known is 9–17 bps, and the standard HIP-3 round trip is 18 bps before spread. Only growth-mode
  fees (unverified per market) would leave a margin, and only in names whose spreads are unknown.

## Capital scenarios (feasibility examples, not recommendations)
| Capital | HYPE carry (25% margin) | H7 if growth fees confirmed |
|---|---|---|
| $5,000 | ≈ $350/yr expected before slippage; margin top-ups needed on rallies | fees and minimum sizes dominate; expected value a few dollars a day at best |
| $25,000 | ≈ $1,750/yr; one position, no capacity issue (HYPE spot is the most liquid HL spot market) | 5 × $2.5k positions; ≈ +5–10 bps/event × 3–4 events/day ≈ $4–10/day, before hedge frictions |
| $100,000 | ≈ $7,000/yr; still no capacity issue | limited by top-of-book depth in liquid names (unobserved) |

## What this session could not do, and how to unblock it
Network policy blocked `api.hyperliquid.xyz`, both docs sites and every exchange/data host; only public GitHub was
readable. Allowing `api.hyperliquid.xyz` (and, for cross-venue work, `fapi.binance.com`, `api.bybit.com`) and re-running
the E8–E10 pipeline would test the candle-based hypotheses on 208 days of hourly candles. A funded follow-up could pull
the Hydromancer Reservoir archive (requester-pays S3: fills, 1-second candles, 1-minute L2 for all dexes), which would
answer the execution questions on H7 directly and allow H9 (liquidation reversal) and H13 (market making) to be tested.

## Budget
Dollar credit usage is not observable from inside the session (only a rate-limit status is exposed). The work was
bounded instead: audit and verification, two data sources, six experiment scripts, a robustness battery, an
independent review, and the deliverables, in roughly the requested 15/50/20/15 proportions by wall-clock.

## Unresolved objections from the independent review (preserved; see `docs/review_adversarial.md`)
1. **H7 timing (O1, blocking)** — accepted. The e4b "extreme persists at t+1" rows conditioned on information not
   available when the hour-t+1 TWAP executes; the honest capture is 9–17 bps. The classification was downgraded.
2. **H7 is the AR(1) half-life of an average (O2)** — accepted as the right description; the residual question is
   whether the point-in-time premium (what a trader sees) reverts as much as the average. Not testable here.
3. **Oracle catch-up in internal sessions (O3) and non-equity/maintenance-hour artefacts (O4)** — accepted; the forward
   test is restricted to US equities in the external session.
4. **H4 hedge timing (O8) and capacity (O9)** — accepted; H4 rejected.
5. **E5 mislabelled as pre-registered (O10)** — accepted; relabelled, ledger entry added. Validation data were
   inspected in E4 runs 2–3 and E5 before the review; no parameter was changed after those looks.
6. **Survivorship of the xyz coin list (O16)** — cannot be resolved here; direction unknown.
7. **Paper trader measures a different premium (O17)** — accepted by design: the forward test measures the executable
   point-in-time version; the collector also records 60-s premium samples so hourly averages can be reconstructed.
8. **Bootstrap blocks within coin, not within day (O6)** — accepted; day-clustered significance survives.
