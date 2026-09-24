# Hypothesis register and pre-registration

Written 2026-09-24 (UTC), BEFORE any strategy backtest in this repository was run. The git history of this file is the
audit trail. Anything added after the experiments ran is marked "POST-HOC" explicitly.

## Environment constraints that shape what can be tested

* Facts: the container's network policy blocks `api.hyperliquid.xyz`, `hyperliquid.gitbook.io`, `docs.trade.xyz`, all CEX
  APIs, archive.org and every third-party data host tried. Only GitHub, PyPI and npm are reachable. `WebSearch`
  returns doc-page snippets (used for venue verification, cited in `docs/venue_verification.md`).
* Consequence: no candles, order books, liquidation feeds or wallet data for Hyperliquid can be downloaded here.
  The empirical work uses two public GitHub datasets that commit real observations (see `data/derived/provenance.json`):
  1. `perp-basis` — 5-minute snapshots (May–Sep 2026) of `xyz:GOLD` / `xyz:CL` on Hyperliquid vs Binance and OKX
     perps and CME (Yahoo, delayed). Fields: mark, last, bid, ask, 24h volume, funding, OI, staleness.
  2. `hyperliquid-rwa-dashboard` — hourly `fundingHistory` (rate + premium) for 161 markets across dexes
     `main` (BTC, ETH, HYPE since 2023-05), `xyz` (109 equity/commodity/FX/index perps since 2025-10), `para`,
     `hyna`, `mkts`; daily stock OHLC (Yahoo); ~40 days of daily OI snapshots.
* Credit usage in dollars is NOT observable from inside the session (only a rate-limit status is exposed), so the
  budget is managed as a bounded work plan, not an enforced cap.

## Hypotheses (H1–H14)

Legend for "Executable": HL-only = both legs on Hyperliquid; +CEX = needs a Binance/OKX account; +broker = needs a
stock broker; N/A = not testable in this environment (data blocked); pipeline code only.

| # | Hypothesis | Mechanism / who pays | Why not arbitraged away | Data here | Executable | Strongest alternative explanation | Falsifier |
|---|---|---|---|---|---|---|---|
| H1 | HIP-3 commodity perps (`xyz:GOLD`, `xyz:CL`) temporarily dislocate from Binance/OKX perps and revert; the HL price moves toward the CEX price | Thin HL book; retail flow on HL; MMs slow to requote; funding anchors only hourly | HIP-3 fees are 2x native (9 bps taker) so small dislocations are not worth crossing; internal-session oracle | perp-basis 5-min | HL-only (CEX used as signal) | HL leads (dislocation = information), or the snapshot quote was not executable (size) | Forward HL return not predicted by spread, or net of fees ≤ 0 |
| H2 | Funding differential between HL commodity perps and CEX perps on the same underlying is persistent and harvestable (long low-funding venue / short high-funding venue) | Different clienteles and funding formulas (HL hourly, 0.5 multiplier on xyz) | Two-venue capital, basis risk during internal sessions, OI caps | perp-basis funding columns | +CEX | Differential is noise around 0; costs of two legs exceed carry | Annualized differential net of fees < 3% or sign flips faster than fees amortize |
| H3 | During CME-closed sessions (Fri 21:00–Sun 22:00 UTC) HL commodity perps drift on internal pricing and the drift reverts at reopen | No external anchor; discovery bounds; retail flow | Weekend risk, few events per year | perp-basis | HL-only | Weekend drift is information (macro news) | Reopen move uncorrelated (or positively correlated) with weekend drift; n too small → inconclusive |
| H4 | Equity-perp funding harvest: short the highest trailing-funding `xyz` equity perps, hedge with the real stock | Retail long demand for 24/7 levered stock exposure pays funding; hedgers need a broker | Broker + on-chain capital in two places; funding falls when arbitrageurs enter | RWA funding hourly + stock daily | +broker | Funding APR ≈ risk-free + costs (no excess); selection on noise | Net APR on committed capital < risk-free + 3% or unstable sign |
| H5 | Native-perp funding carry (short BTC/ETH/HYPE perp, long HL spot UBTC/UETH/HYPE) is positive on average and fully executable on HL | Longs pay for leverage | Crowded; funding compresses; negative-funding regimes | RWA `main` funding since 2023 | HL-only (spot leg assumed liquid) | Carry ≈ risk-free after costs; 2024 bull-market artefact | Net APR after fees and spot spread < risk-free + 2% over 2023-2026 |
| H6 | Hourly funding is persistent enough that trailing means predict next-week funding (building block for H4/H5/H14) | Slow-moving positioning | — | RWA funding | — | Persistence is only the interest-rate floor (0.01%/8h) | Weekly non-overlapping correlation < 0.3 |
| H7 | Extreme hourly premium on equity perps mean-reverts within 24h (contrarian short-horizon signal) | Retail bursts; slow MMs | Premium reversion is small vs stock volatility; unhedged | RWA premium | HL-only (unhedged) or +broker | Premium reverts via the oracle moving, not the perp | Premium change after extremes not significantly negative or too small vs 9 bps fees |
| H8 | Volatility-conditioned trend/momentum on native crypto perps | Slow information diffusion; positioning | Well known; crowded | none (needs candles) | HL-only | Beta and leverage | N/A here — pipeline only |
| H9 | Mean reversion after liquidation cascades on native perps | Forced flow, liquidity vacuum | Requires fast execution | none (needs liquidation feed) | HL-only | Continuation | N/A here — pipeline only |
| H10 | Funding × OI interaction: crowded positioning (high OI growth + extreme funding) reverses | Positioning unwinds | Needs OI history (not in HL API) | ~40 days OI daily | HL-only | Noise | N/A here — forward collector |
| H11 | Equity-perp premium behaves differently in internal (weekend/overnight) vs external sessions and weekend premium drift reverts at Monday open | No external anchor on weekends | Weekend risk | RWA premium hourly | HL-only (unhedged) | Drift is information | Weekend premium change uncorrelated with post-open change |
| H12 | Public wallet flow / leaderboard copy-trading | Informed traders visible on-chain | Observation delay, hidden hedges, survivorship | none | HL-only | Survivorship / luck | N/A here |
| H13 | Passive market making / order-flow strategies | Spread capture | Adverse selection; needs L2 + colocation | none | HL-only | Adverse selection | N/A here |
| H14 | Cross-dex funding differential on the SAME underlying inside Hyperliquid (e.g. `hyna:BTC` vs `BTC`, `para:AVGO` vs `xyz:AVGO`): long one dex, short the other, collect the differential | Segmented clienteles across dexes; HIP-3 fees keep small differentials unarbitraged | Two positions on HL, fully on-chain, basis risk only between marks | RWA funding (both legs) | HL-only | Differential ≈ 0 after fees; marks diverge | Net annualized differential < 3% or unstable |

Not pursued (no mechanism or no data): tax/airdrop farming, HYPE token events, vault copying, HIP-4 outcome markets.

## Pre-registered tests

### Common rules
* Chronological split. perp-basis: development 2026-05-01 → 2026-06-30, validation 2026-07-01 → 2026-09-24
  (Aug–Sep are sparse; reported but weighted accordingly). RWA funding: development = up to 2026-05-31,
  validation = 2026-06-01 → 2026-09-23. Parameters are chosen on development only; validation is inspected once per
  hypothesis, after the development grid is frozen (the ledger records every look).
* Benchmarks: no trade (0), risk-free 4.03% (13-week T-bill, upstream ^IRX 2026-09-23) for hedged carries, and a
  "naive" version of each rule (e.g. always-on carry without selection).
* Costs: `hlr.costs.FEE_REGIMES`; spread crossing from observed bid/ask; impact allowance 0.5 bps/side; signal
  computed at snapshot t, executed at snapshot t+1 (≈5 min delay) unless stated.
* Statistics: HAC t-stats for regressions; stationary block bootstrap CIs (blocks by day for trades); deflated
  Sharpe for the best variant of any grid; trade-count and independent-observation counts reported.
* Rejection: (a) net mean ≤ 0 or 95% CI covering 0 on development → rejected; (b) development pass but validation
  net ≤ 0 → inconclusive (not supported); (c) economic size: hedged carries need net APR ≥ risk-free + 3% on
  committed capital; directional/RV trades need net mean per trade > 0 with lower CI > 0 AND ≥ 100 trades; (d)
  results explained by ≤ 5 trades (removing the top 5 trades flips the sign) → downgraded.

### H1 (cross-venue dislocation) — `experiments/e1_xvenue_dislocation.py`
* Signal: s_t = 1e4·(ln HL_mid − ln REF_mid), REF ∈ {Binance, OKX, mean of both}. Primary: Binance.
* Diagnostics: AR(1)/half-life of s; HAC regressions of h-step forward HL mid change (and REF mid change) on s_t,
  h ∈ {1,2,3,6,12} steps of ~5 min (steps require gaps 4–7 min). Decile table of forward returns.
* Rule: enter when |s_t| ≥ θ, θ ∈ {1,2,3,5,8} bps, direction toward REF, execute at t+1 crossing the HL spread;
  exit when |s| ≤ θ/2 or sign flips, or max hold H ∈ {6,12,36} steps, executed one snapshot after the signal.
  Primary pre-specified variant: θ=3, H=12, REF=Binance, taker, delay=1. Grid size: 5×3×3×2(delay) = 90 per asset.
* Fee regimes: hip3_standard (primary), hip3_growth, zero (diagnostic).
* Reject if the primary variant fails common rule (a)/(c); a grid-best that passes only with deflated Sharpe < 0.95
  is "inconclusive".

### H2 (cross-venue funding differential) — inside `e1`
* Annualize each venue's funding using its settlement interval (HL hourly; Binance/OKX per verified interval,
  assumption flagged if unverified). Differential d_t = HL − CEX (annualized). Report mean, sd, sign persistence
  (daily), and net carry after round-trip fees on both legs for holding periods of 1, 7, 30 days.
* Reject if mean |d| net of costs < 3% APR or if daily sign persistence (lag-1 autocorrelation of daily mean d) < 0.3.

### H3 (weekend drift/reopen) — inside `e1`
* Events: each Fri 21:00 → Sun 22:00 UTC window with HL snapshots at both ends and REF snapshots at reopen.
* Stat: correlation and sign agreement between weekend HL move and the move over the first 2 h after reopen, for HL
  and for Binance. Exploratory: with < 25 events any result is "inconclusive" unless |corr| > 0.6 with p < 0.01.

### H4 / H6 (equity-perp funding harvest, persistence) — `experiments/e4_funding_carry.py`
* Universe: `xyz` coins with a Yahoo mapping and ≥ 28 days of history at decision time (point-in-time membership).
* Persistence: non-overlapping weekly means; corr(week_t, week_{t+1}); by dex.
* Rule: each Monday 00:00 UTC rank by trailing 7-day mean funding; short-perp/long-stock the top K ∈ {5,10} with equal
  weights (naive benchmark: all coins equal weight). Hold one week; rebalance. Costs: perp round trip on turnover
  (hip3_standard taker; growth as sensitivity), stock round trip 2 bps, hedge capital = stock notional + 25% perp
  margin; financing = risk-free on the stock notional (opportunity cost). Basis P&L approximated by −Δpremium.
* Report net APR on committed capital, monthly series, drawdown, dependence on the top-5 coin-weeks.
* Reject per common rule (c) (net APR < risk-free + 3%) or sign instability across quarters.

### H5 (native perp carry) — inside `e4`
* Funding leg from `main` BTC/ETH/HYPE since 2023-05; short perp + long spot, rehedged monthly; costs: perp taker +
  spot taker (assumed 0.045%) + assumed 5 bps spot half-spread; report yearly net APR, share of negative-funding hours,
  worst 30-day window. Reject per common rule (c).

### H7 / H11 (premium reversion; session effects) — inside `e4`
* Event: premium |p_t| above its trailing 30-day 95th percentile. Stat: mean change in premium over next 1, 6, 24 h
  (HAC t). Economic size compared with 18 bps round-trip standard fees. Session split: internal (Fri 20:00–Sun 20:00
  ET; nightly 20:00–04:00 ET) vs external.

### H14 (cross-dex same-underlying differential) — inside `e4`
* Pairs available: `hyna:BTC`/`BTC`, `hyna:ETH`/`ETH`, `hyna:HYPE`/`HYPE`, `para:AVGO`/`xyz:AVGO`, any others detected
  by matching tickers. Stat: annualized funding differential (hourly, aligned), sign persistence, net carry after fees on
  both legs (hip3_standard on the HIP-3 leg; native_base on the main leg) for 7/30-day holds, chronological split.
* Reject per common rule (c).

## What will NOT be done
No parameter search on validation; no re-running with new θ after seeing validation; no reporting of a grid-best
without its deflated Sharpe; no synthetic data presented as empirical.
