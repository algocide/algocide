# Decision memo — Hyperliquid edge research (2026-09-24)

## Decision
**No edge was found that is both economically meaningful and sufficiently supported to justify real capital.**
One hypothesis — hedged mean reversion of extreme hourly premiums on trade.xyz (HIP-3) equity perps (H7) — has
strong statistical support in 11 months of hourly data and a plausible mechanism, but its economics depend on
execution costs that could not be observed from this environment. The next useful action is cheap and specific:
run the forward collector and the dry-run paper trader for 4–8 weeks and judge H7 against the pre-declared criteria
in `docs/forward_plan.md`. Everything else tested was rejected, inconclusive, or has decayed to below cash.

## Evidence ladder (what kind of claim each is)
* **Facts (verified from official docs via search snippets / SDK source)**: fee schedule basics, funding formula and
  cap, HIP-3 2x fee share and growth mode, trade.xyz sessions and oracle mechanics, API limits (`docs/venue_verification.md`).
* **Measured (real observations, code in `experiments/`, outputs in `results/`)**: everything in the table below.
* **Simulations / assumptions**: fees per regime, 1 bp half-spread + 0.5 bp impact on HIP-3 perps (unobserved),
  stock hedge at the oracle price, 4.03% financing, 25% perp margin.
* **Unresolved**: the reviewer's objections (section below), growth-mode status per market, `para` deployer identity.

## Results at a glance (net of modelled costs)
| Hypothesis | Net result | Classification |
|---|---|---|
| H1 cross-venue dislocation, xyz:GOLD / xyz:CL vs Binance | gross ≈ 0 bps/trade; net −18 bps | Rejected |
| H2 funding differential HL vs CEX (gold) | +4.3% APR gross, ≈ 0 after two-leg costs | Rejected |
| H3 weekend drift reversal (WTI) | corr −0.77, n = 9 | Inconclusive (monitor) |
| H4 equity-perp funding harvest, K=5 | +4.7% APR in-sample, +1.5% out-of-sample, negative under cost ×2 | Inconclusive, effectively rejected at standard fees |
| H5 native funding carry BTC/ETH | 2026: 3.6–4.2% net vs 4.03% cash (2024: 17–19%) | Historically real, currently not meaningful |
| H7 premium-extreme reversion (liquid names, external session, realistic timing) | +31 bps/6 h gross [17, 50], n = 105; ≈ +7 bps net at standard fees, ≈ +25 at growth fees | Supported for forward paper test |
| H11 weekend premium reversal | −0.74 corr but likely oracle catch-up | Inconclusive |
| H14 para/xyz same-stock funding differential | 23% APR differential, +6 bps/week net, t = 0.3, 15 weeks | Inconclusive (monitor) |
| H8, H9, H10, H12, H13 | not testable here (API blocked) | Pipeline written, not run |

## Why the strongest result is not yet a strategy
The H7 premium is the hourly average of impact prices relative to the oracle; the executable quote at the moment of an
extreme is worse than that average, and it is worst in the thin names where extremes are largest. At the standard
HIP-3 taker fee (9 bps) the liquid-name version nets only ≈ 7 bps per event with a lower confidence bound below zero;
it becomes interesting only if growth-mode fees apply (unverified per name) or if maker fills are realistic. The
forward test is designed to measure exactly these unknowns.

## Capital scenarios (feasibility examples, not recommendations)
* **$5,000**: fees and minimum sizes dominate; only H7 under growth-mode fees could clear costs; the stock hedge needs a
  broker with fractional shares; expected value after frictions is a few dollars a day at best.
* **$25,000**: H7 with 5 concurrent $2.5k positions; if the forward test confirms +8 bps net per event at ~4 events/day
  this is ≈ $8/day (≈ 12%/yr on capital) before hedge frictions — an if, not a forecast.
* **$100,000**: same, limited by top-of-book depth in the traded names (unobserved); OI caps ($25–100M) are not binding.

## What this session could not do, and how to unblock it
The sandbox's network policy blocked `api.hyperliquid.xyz`, both docs sites and every exchange/data host; only public
GitHub repositories were readable. Allowing `api.hyperliquid.xyz` (and, for cross-venue work, `fapi.binance.com`,
`api.bybit.com`, `data.binance.vision`) in the environment settings and re-running `experiments/e8_e10_pipeline_when_api_available.py`
would test H8–H10 on 208 days of hourly candles. A funded follow-up could pull the Hydromancer Reservoir archive
(requester-pays S3) for fills, 1-second candles and 1-minute L2 books across all dexes, which would settle the
execution questions on H7 directly.

## Budget
Dollar credit usage is not observable from inside the session (only a rate-limit status is exposed). The work was
bounded instead: capability audit and verification, two data sources, five experiment scripts, one robustness battery,
one independent review, and the deliverables — roughly the 15/50/20/15 split requested, judged by wall-clock.

## Unresolved objections from the independent review
(See `docs/review_adversarial.md` for the full text; this section is updated after the review.)
