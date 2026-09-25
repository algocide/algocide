# FINAL_REPORT.md — Hyperliquid stock-perp strategy research (2026-09-25)

## 1. Conclusion: no demonstrated edge (and, for stock perps, insufficient evidence to ever certify one here)
Forty-five distinct configurations from six simple families (MA trend, Bollinger mean reversion, volatility-compression
breakout, channel breakout, 1h-trend + 15m pullback, opening-range breakout) were tested under a protocol frozen before
any test ran (chronological 50/30/20 split by trading day, walk-forward windows, ≤ 60 trials, one holdout look).
* **Stock-linked universe** (xyz:SNDK, MU, NVDA, META, GOOGL; 90 US sessions of 15-minute sampled mid prices,
  2026-05-01 → 09-25): one configuration passed every development + validation gate, the 15-minute channel breakout
  (12-bar high/low, 1.5-ATR stop, 12-bar trailing exit). Evaluated once on the untouched holdout (2026-08-31 → 09-25,
  18 sessions) it lost under every cost regime: −$5.7 base, −$6.6 adverse, −$8.1 standard fees, PF 0.54–0.74,
  0 of 2 windows positive, four of five markets negative. Its development/validation gains were also fragile to the
  main flaw of the data: when a stop is deemed hit whenever a 15-minute sample comes within 0.5–0.75 ATR of it (the
  factor calibrated on BTC/ETH against real candles), the development period turns from +$20 to −$22/−$32.
  **Rejected.** No other configuration reached the holdout.
* **BTC/ETH** (real Hyperliquid candles): in the US session nothing passed the gates (37 sessions of 15m data are too
  few; 142 sessions of 1h data gave a best validation PF of 1.39 on 14 trades with a losing development period).
  The separate 24/7 experiment (1h trend and channel rules, 209 days) lost on validation (−$3 to −$26 on $100) and
  triggered the $10 pause in every case.
* The baselines (cash; session-matched long; 1h EMA-crossover trend rules 10/40 and 20/80) are within ±$3 on validation in every universe:
  nothing here beats "do nothing" after costs with any confidence.

## 2. Selected markets and why (MARKET_SELECTION.csv)
What the instruments are (verified from committed `perpDexs`/`meta`/`metaAndAssetCtxs` responses and the docs, via
search snippets): USDC-margined linear perpetual contracts deployed by trade.xyz on Hyperliquid's HIP-3 `xyz` dex.
They reference a stock's price through the deployer's oracle (institutional LP quotes 24/5, internal pricing
Fri 20:00 → Sun 20:00 ET); they confer no ownership, no dividends, no votes. Funding is hourly with a 0.5 multiplier
(`assetToFundingMultiplier`), fees are in growth mode (`growthMode: enabled`, ~0.009% taker all-in per the fee page
snippet; standard HIP-3 would be 0.09%), price tick = 5 significant figures / ≤ 6−szDecimals decimals, size step
10^−szDecimals, $10 minimum order, per-asset streaming OI caps (e.g. NVDA $500M), max leverage 10x–20x, several names
isolated-only. Not verifiable from this sandbox: the exact all-in xyz fee rate (a parameter, stress-tested), the
impact notional behind `impactPxs`, and historical spreads.

Ranking used 130 daily snapshots (2026-05-01 → 09-24) of 24h notional volume and OI, one impact-price snapshot
(~2026-09-02) for spreads, and 15-minute data continuity (≥ 60 in-session days). Eligible names had a 30-day median
24h volume above ≈ $10M and no day under $1M; score = volume rank + ½ spread rank.

| Market | 30d median 24h volume | 30d p10 | 90d median | impact half-spread (bps, 1 snapshot) | max lev | note |
|---|---|---|---|---|---|---|
| xyz:SNDK | $124M | $27M | $176M | 0.33 | 10x | very volatile (27 days with |daily move| > 8% since May) |
| xyz:MU | $80M | $20M | $142M | 0.35 | 10x | volatile memory name |
| xyz:NVDA | $46M | $11M | $43M | 0.67 | 20x | |
| xyz:META | $23M | $3M | $21M | 0.83 | 20x | |
| xyz:GOOGL | $21M | $5M | $20M | 0.68 | 20x | |
Next in line: CRCL, INTC (wider spreads), TSLA, AAPL (lower volume). Korean names (SKHX, SMSN, SKHY) were excluded
because their cash session is KRX, not US. Limitations: spreads/depth are a single snapshot; volume is the exchange's
rolling 24h figure, not the US-session share. Selection bias: the five were chosen on today's liquidity and then
backtested retrospectively; this is not a simulation of a dynamic top-five rule.

## 3. Best candidate's exact rules
See STRATEGY_SPEC.md (frozen before the holdout). In one paragraph: on completed 15-minute bars during the US regular
session, go long when the close exceeds the highest high of the prior 12 bars (short below the lowest low), fill at
the next bar with taker costs, stop 1.5 × ATR(14), trail the stop to the 12-bar opposite extreme after every bar, no
target, flat by 15:15 ET decisions (fill by 15:30), no entries after 14:30 ET, one position across the five markets
(lowest-spread market wins ties), $1 planned risk including costs, gross notional ≤ 2 × equity, sizes rounded to
0.001, $10 minimum, $10 drawdown pause with a shadow book.

## 4. Validation, holdout and cost-stress results (candidate; $100 account; sampled-mid data)
| Period | Trades | Days | Net $ | Exp $/trade (=R) | PF | Win | Avg win / loss | Ex-best trade | Ex-best day | Windows + |
|---|---|---|---|---|---|---|---|---|---|---|
| Development 05-01 → 07-23 | 65 | 45 | +19.99 | +0.31 | 1.56 | — | — | — | — | — |
| Validation 07-23 → 08-31 | 37 | 27 | +16.31 | +0.44 | 2.06 | 59% | — | +8.17 | — | 2/3 |
| **Holdout 08-31 → 09-25 (base)** | 30 | 17 | **−5.74** | −0.19 | 0.69 | 43% | +0.98 / −1.09 | −8.46 | −8.66 | 0/2 |
| Holdout, adverse (2× spread, 2× slippage, 5 bp stop gap) | 30 | 17 | −6.60 | −0.22 | 0.64 | 43% | +0.90 / −1.07 | −8.94 | −9.13 | 0/2 |
| Holdout, standard HIP-3 fees (0.09% taker) | 30 | 17 | −8.14 | −0.27 | 0.54 | 40% | +0.79 / −0.98 | −9.97 | −10.06 | 0/2 |
| Holdout, sampled-stop stress f=0.5 | 34 | 17 | −4.70 | −0.14 | 0.74 | 38% | +1.01 / −0.85 | −6.90 | −7.10 | 0/2 |
| Holdout, sampled-stop stress f=0.75 | 41 | 17 | −9.87 | −0.24 | 0.45 | 27% | +0.74 / −0.60 | −11.63 | −11.63 | 0/2 |

Uncertainty (day-clustered bootstrap, holdout base): expectancy 95% CI [−0.63, +0.31] $/trade; total [−21.3, +8.4];
P(mean ≤ 0) = 0.78. Max drawdown of closed-trade P&L in the holdout: $11.9 (11.9%). Exposure: positions open
≈86% of in-session bars (dev+val); average holding 4.1 h over the full run (3.1 h in the holdout); average notional $107
(0.8× equity); turnover ≈ 30 × $107 over 17 days. Cost burden: 6.6% of gross profits over the full run, 15% in the
holdout base case, 61% at standard fees. Funding: +$0.03 (negligible for intraday holds).
By market (holdout, base): GOOGL +1.70 (1 trade), NVDA −1.17 (7), MU −2.02 (5), SNDK −4.24 (17), META none.
By direction (holdout): long −1.95, short −3.79. By month (all, base): May +18.1, Jun −4.9, Jul +14.8, Aug +7.8,
Sep −5.7. Development + validation gains were concentrated in SNDK (+$24.1 of +$36.3) during the memory-stock
run-up; SNDK is also where the holdout losses sit.
Drawdown pause: a fresh $100 account in the holdout pauses after the drawdown (final $92.3 with the pause);
the shadow book after the pause is +$1.9 (base). Over the full run (dev+val+holdout, one account) the pause
triggered on 2026-09-14; final equity $131.1 with the pause vs $130.0 without, i.e. the pause changed little.
Robustness (dev/val, nearby parameters): n=8 +19.9/+14.7; n=16 +13.7/+3.4; ATR×1.0 −5.2/+22.2; ATR×2.0 +11.0/+1.5:
signs mostly positive, magnitudes unstable.
Sampled-vs-candle calibration (BTC/ETH, same window and rules): net P&L flips sign in 3 of 8 configurations;
modelled stop losses are 10–90% larger on sampled data. The sampled representation is a screening tool, not
a certification tool.

## 5. Practical feasibility for a $100 account
Feasible mechanically: with 0.3–1.5 bp half-spreads (snapshot), 0.9 bp taker fee in growth mode and $10 minimum
orders, a $1-risk trade with a 0.5–1.5% stop needs $70–$200 notional (0.7–2× equity; 20x leverage available, so
margin committed is $4–$20). Sizes round to 0.001 units (0.0x for $1,000+ names like SNDK/MU at 0.001 step ≈ $1.8
granularity), which rejected 18 entry signals as "stop on wrong side" after tick rounding over the full run (logged). What is not
feasible is the economics: expectancy is a few cents per trade at best, so a $100 account cannot generate meaningful
income from these rules even if a small edge existed; the 61% cost burden at standard fees shows how thin the margin is.

## 6. Comparison with BTC/ETH
Under identical US-session rules on real candles, BTC/ETH produced no configuration that passed the validation gates;
the crypto results are not better, they are equally uninformative or negative (24/7 trend rules lost). The
comparison does not favour moving the experiment to BTC/ETH on strategy grounds. It does favour BTC/ETH on data
grounds: real candles, full depth snapshots and 24/7 trading make a rigorous test possible there today, whereas the
stock perps need a candle archive first (see §8). Recommendation: do not shift; fix the data access instead.

## 7. Main reasons the results might fail live (and why the "wins" were not trusted)
1. Sampled mids, not candles: intrabar stops unseen (calibrated stress flips the development result), true ranges
   ~1.8× the sampled ranges, fills modelled at a 15-minute-later mid. 2. Spreads/depth known from one snapshot;
   the impact spread may not be attainable at 09:30–10:00 ET or on news. 3. Regime concentration: gains came from
   SNDK/MU in a mania and from May/July; September lost. 4. Fee regime: growth mode can be switched off by the
   deployer (0.09% taker would have made the holdout −$8.1). 5. Oracle/session mechanics: perp prices can dislocate
   from the stock around the 09:30 open and 16:00 close; the flat-by-15:30 rule was never tested against real
   auction behaviour. 6. Multiple testing: 45 configurations, 5 cost regimes, 4 universes; a PF of 2 on 37 trades
   is within the range of chance. 7. Selection bias in the universe (today's most liquid names).

## 8. Exact next action
1. Allow `api.hyperliquid.xyz` in the environment's Network access (or run on any small VM). Start the previous
   session's `forward/collector.py` (60-second `metaAndAssetCtxs` + `l2Book` for the five names and BTC/ETH) and a
   daily 15-minute `candleSnapshot` archive. This is the only way to obtain intrabar data and a spread history;
   without it no stock-perp strategy can be validated, whatever the backtest says.
2. Do not re-optimise on the May–September sample; the holdout is spent. Treat all data from 2026-09-26 onward as
   fresh. Optionally run `forward/paper_trader.py --mode live` on the frozen (rejected, unproven) rules for
   observation only, and re-screen the six families on real candles after ≥ 40 new sessions.
3. If the user wants a positive-expectancy line of work on Hyperliquid, the previous session's evidence points to
   carry/funding structure (HYPE perp-vs-spot carry) rather than intraday directional rules; that is a different
   project.

## Trial accounting
EXPERIMENTS.csv: 245 rows = 45 distinct configurations (37 predeclared, 4 redefined MA-filter, 4 robustness
neighbours) × universes (stocks, crypto15, crypto1h, crypto247 where applicable) × cost regimes (base, adverse,
standard_fee, two sampled-stop stresses), plus the session-long baseline. Holdout opened once: stocks, one config.
Figures: results/figures/candidate_channel_bo_15m_full.png, stocks_top3_devval.png, crypto1h_top3_devval.png,
family_universe_heatmap.png, audit_nvda_sampled_vs_candles.png.
