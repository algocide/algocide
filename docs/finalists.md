# Finalists — specifications, results, and what must happen before real capital

After the adversarial review, one candidate passes its own pre-registered bar (F1, a carry), one is conditional (F2),
and two are rejected but kept here as specified for the record (F3, F4). None is validated by forward trading.

---
## F1 — H5 (HYPE): short HYPE perp / long HYPE spot funding carry, fully on Hyperliquid

**What exactly is the edge?** Not an alpha: perp longs pay shorts the funding rate, whose interest component alone is
0.01% per 8 h (≈ 10.95% APR) whenever the premium is near zero, plus a premium component when speculative long demand
is high. A delta-neutral short-perp/long-spot position collects it. Who pays: leveraged longs. Why it persists: it is
compensation for supplying leverage; it compresses when arbitrage capital is abundant (BTC/ETH: 19–24% gross in 2024 →
5% in 2026) and reverses in bear regimes.

**Rule**: hold short HYPE perp against an equal notional of HYPE spot; rehedge monthly; 25% perp margin (4x on that leg).

**Evidence (measured, hourly settled funding, Dec 2024 → Sep 2026)**: gross funding APR 2024 (Dec only) 118.8%, 2025
22.1%, 2026 YTD 9.2%; 7% of hours negative; worst rolling 30-day funding +28 bps; corr of consecutive 30-day sums 0.83.
Net on capital (spot notional + 25% margin, one round trip per year at 4.5 bps perp taker + 4.5 bps spot taker + 5 bps
spot half-spread + impact): 2025 17.4%, 2026 7.0%. Pre-registered bar (risk-free + 2% = 6.03%): **passes in 2026 at 25%
margin; fails at 50% margin (≈ 5.8%)**. BTC (3.6%) and ETH (4.2%) fail.

**How the evidence could mislead**: the 2026 figure is nine months; HYPE's funding depends on speculative demand for one
token; the spot fee schedule was assumed (perps schedule); no basis P&L was modelled (perp and spot converge, but the
short perp can be liquidated during a rally before the spot gain is usable as margin because spot HYPE is not perp
collateral).

**What kills it**: funding turning negative for months (bear regime), a fee change, or having to run the perp leg at 2x.

**Before real capital**: paper-run for 8 weeks to verify funding accrual, margin top-up logic and the spread on the spot
leg; predeclared pass: realised net funding ≥ 6% annualised with no margin call at a simulated 30% adverse move.

---
## F2 — H7 (conditional): hedged premium-reversion on trade.xyz US-equity perps, external session only

**Edge (as revised)**: the hourly-average premium of an xyz perp is an AR(1) with ρ ≈ 0.65 (half-life ≈ 1.6 h). Selling
a rich perp (buying a cheap one) hedged with the stock captures part of the decay. With honest timing (direction from
the last completed hour, entry as the next hour's TWAP): 6-hour capture 14 bps for US equities, 9 bps for the 24
liquid US equities (≥ $5M/day), external sessions.

**Economics**: standard HIP-3 round trip 18 bps + spread + stock 3 bps → negative for any spread. Growth-mode fees
(1.8 bps round trip) → +9 bps (US equities) / +4 bps (liquid names) at 1 bp half-spread; ≈ 0 at 5 bps. **Only viable
if growth mode is confirmed for the liquid names and their half-spreads are ≤ 3 bps.** Both are unknown here.

**Rule for the paper test** (`forward/paper_trader.py::PremiumReversion`): point-in-time premium > +30 bps (< −30) in
external sessions (04:00–20:00 ET, Mon–Fri), liquid US equities only, hedge 1:1 with stock at the oracle proxy, exit at
|p| < 7.5 bps or 6 h, ≤ 5 concurrent, ≤ 5% of capital per market. Pass/fail in `docs/forward_plan.md`.

**Update 2026-09-26 (reported, not verified live):** web-search snippets of a Coin Metrics note say trade.xyz is running
growth mode (fees cut by more than 90%, taker 0.009%), which was the unverified precondition above, and that Hyperliquid has
proposed letting HIP-3 deployers raise fees by up to 3x, asset by asset, with no timeline. Confirm per market with
`{"type":"userFees"}` before starting the paper test; if the 3x increase lands on the liquid US equities, drop F2.
Sources and the rest of that day's research: `docs/agent/research_2026-09-26.md`.

**How the evidence could mislead**: the backtest statistic is an hourly average (a TWAP), not the touch; 71% of
historical events were in internal sessions where the oracle chases the perp; a third were non-equities; event
clustering across names; possible survivorship in the coin list.

---
## F3 — H4 (rejected): weekly short of the top-5 trailing-funding xyz equity perps, stock-hedged
As simulated: 4.7% APR [0.6, 8.7] on capital, validation 1.5%, Q3-2026 −2.0%. Rejected because (a) the simulated Monday
00:00 UTC execution is Sunday evening ET when the stock hedge cannot be placed, and the +3.0 pt basis term is the weekend
premium reverting into Monday; with both legs at 14:00 UTC the net is 2.5% (t 1.2); (b) 52% of gross funding came from
names trading < $1M/day (BIRD $0.04M, DKNG $0.09M, BX $0.15M …), realistic book ≈ $80k; (c) fails the pre-registered
7.03% bar; (d) fails costs ×2 and funding ×0.5 stresses. Growth-mode fees at 14:00 UTC: 6.6% (t 3.2), still below the bar.

---
## F4 — H14 (rejected as tradeable): same-stock funding differential across HIP-3 dexes
para:AVGO vs xyz:AVGO differential 23% APR over 15 weeks (Aug 55%, Sep 26%), sign-following net +6 bps/week (t 0.28), but
para:AVGO trades ≈ $40k/day notional (median) and 4 of 7 para/xyz pairs are negative; hyna vs native pairs negative after
costs. Untradeable at any meaningful size; the `para` deployer's identity, fees and oracle could not be verified.
