# Hyperliquid Venue Verification (as of 2026-09-24)

Scope: Hyperliquid native perps + HIP-3 builder-deployed dexes (trade.xyz in particular).
Compiled 2026-09-24 in a sandbox whose network policy blocks `api.hyperliquid.xyz`,
`hyperliquid.gitbook.io` and `docs.trade.xyz` for direct fetches. Evidence therefore comes from
two methods only:

- **via WebSearch snippet** — page-content summaries returned by the search tool for official-doc
  domains (`hyperliquid.gitbook.io`, `docs.trade.xyz`) and, for one third-party bucket, the open web.
  Snippets aggregate over the result set of one query; the URL cited is the page whose title matches
  the fact. Snippets are summaries, not page text, so every number below must be re-checked live
  before it is used in production code.
- **via SDK source** — the local clone of the official Python SDK
  (`github.com/hyperliquid-dex/hyperliquid-python-sdk`, commit `2fdb18f`, 2026-06-04), read at
  `/tmp/claude-0/-home-user-algocide/5429b401-030f-59f0-af50-07bf4bddde5d/scratchpad/ext/hyperliquid-python-sdk`.

Anything not backed by one of those two methods is marked **NOT VERIFIED** and, where I have prior
knowledge, labelled **unverified prior knowledge**. No number below was invented.

## Summary table (decision-relevant parameters)

| Parameter | Value | Status / source |
|---|---|---|
| Base perps taker fee (validator-operated perps) | 0.045% | Verified (fees page, growth-mode text) |
| Base perps maker fee | 0.015% | unverified prior knowledge (snippet did not return tier 0) |
| Fee tier basis | rolling 14-day volume, assessed daily at 00:00 UTC; weighted vol = perps + 2 x spot | Verified (fees page) |
| Tier thresholds | >$5M, >$25M, >$100M, >$500M, >$2B, >$7B | Verified (fees page) |
| Maker rebate qualification | >0.5% of 14-day weighted maker volume for Tier 1+ | Verified; rebate sizes NOT VERIFIED |
| Staking discount | Wood 5% ... Diamond 40% | Verified (fees page) |
| Referral discount | 4% of fees on first $25M volume | Verified (referrals page) |
| Builder-code fee cap | 0.1% perps, 1% spot | Verified (builder-codes page) |
| HIP-3 growth mode | >=90% fee cut; all-in taker 0.0045%-0.009% | Verified (fees page) |
| Funding formula | F = avgPremium + clamp(interest - premium, -0.0005, +0.0005) (8h rate, paid hourly at 1/8) | Verified (funding page) |
| Interest rate | 0.01% per 8h = 0.00125%/h (~11.6% APR, paid to shorts) | Verified (funding page) |
| Funding cap | 4% per hour | Verified (funding page) |
| Funding payment basis | position_size x **oracle** price x rate (not mark) | Verified (funding page) |
| trade.xyz funding | F_xyz = 0.5 x [avgPremium + clamp(...)] (~5.5% ann. baseline) | Verified (docs.trade.xyz updates page) |
| Max leverage range (native) | 3x-40x per margining page; BTC/ETH shown as 50x on margin-tiers page | Verified (both statements; see 4) |
| Maintenance margin | half of initial margin at max leverage (1.25% at 40x ... 16.7% at 3x) | Verified (margining page) |
| Backstop liquidation trigger | below 2/3 of maintenance margin | Verified (liquidations page) |
| IP rate limit | 1200 weight/min; info weights 2 / 20 / 60 | Verified (rate-limits page) |
| candleSnapshot | only most recent 5000 candles per interval | Verified (info-endpoint page) |
| Time-range endpoints | max 500 rows per call (paginate on last timestamp) | Verified (info-endpoint page) |
| Equities external pricing | Sun 20:00 ET - Fri 20:00 ET (24/5); internal pricing Fri 20:00 - Sun 20:00 ET | Verified (docs.trade.xyz US stocks page) |
| Commodities external pricing | Sun 18:00 ET - Fri 17:00 ET (23/5), daily gap 17:00-18:00 ET | Verified (docs.trade.xyz commodities page) |
| Discovery bounds | mark within +/-(1/maxLeverage) of reference price, re-anchoring | Verified (docs.trade.xyz discovery-bounds page) |

## 1. Market universe

- **Native perps count / spot count: NOT VERIFIED (API blocked; docs snippets carry no counts).**
  Must be read live from `{"type":"meta"}` (length of `universe`) and `{"type":"spotMeta"}`.
- **HIP-3 dex list: NOT VERIFIED.** The names supplied with this task (`xyz`, `hyna`, `para`, `mkts`,
  `flx`, `km`, `vntl`) are unverified prior knowledge and must be read live from `{"type":"perpDexs"}`.
  Only `xyz` is corroborated indirectly: docs.trade.xyz says trade[XYZ] "offers perpetual markets
  deployed through HIP-3, including stocks, equity indices, commodities, currencies, and pre-IPO
  markets" (https://docs.trade.xyz/, 2026-09-24, via WebSearch snippet). The docs also name markets
  `XYZ100` (equity index) and `WTIOIL` (https://docs.trade.xyz/perp-mechanics/discovery-bounds,
  2026-09-24, via WebSearch snippet), so an index called `CL` and one called `WTIOIL` may both exist; check live.
- **Identifier format `dex:COIN` is verified via SDK source**: the SDK builds coin names as
  `f"{DUMMY_DEX}:ABC"` / `f"{DUMMY_DEX}:TEST0"` (`examples/basic_order_with_builder_deployed_dex.py`,
  `examples/perp_deploy.py`, via SDK source, 2026-09-24). Hence `xyz:GOLD`, `xyz:TSLA` is the expected
  form once the dex name `xyz` is confirmed live.
- **Asset-index layout (via SDK source, `hyperliquid/info.py` L45-71)**: spot assets = spot index +
  10000; builder-deployed perp dexs start at asset index 110000 + 10000 x (position in `perpDexs()[1:]`);
  `""` is the original (validator-operated) dex. `meta`, `clearinghouseState`, `openOrders`,
  `frontendOpenOrders`, `allMids` accept a `dex` parameter.
- HIP-3 deployer registration fields (via SDK source, `exchange.py::perp_deploy_register_asset`):
  `coin`, `szDecimals`, `oraclePx`, `marginTableId`, `onlyIsolated`, plus dex schema
  `{fullName, collateralToken (token index), oracleUpdater}`; oracle updates via `perpDeploy.setOracle`
  with `oraclePxs`, `markPxs`, `externalPerpPxs`.

## 2. Collateral and contract specifications

**Collateral.** Native perps are USDC-margined: **NOT VERIFIED from a snippet** (unverified prior
knowledge). The SDK shows each HIP-3 dex has a single `collateralToken` (token index; the example uses
`0`) and that `sendAsset` "Token must match the collateral token if transferring to or from a perp dex"
(`hyperliquid/exchange.py` L493-505, via SDK source). HIP-3 OI caps are expressed in "size unit of
collateral asset" (HIP-3 page, below). **USDH / aligned quote assets: NOT VERIFIED (search returned
the page URL https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/aligned-quote-assets but no content).**

**Oracle price** (validator-computed): weighted median of Binance, OKX, Bybit, Kraken, Kucoin, Gate IO,
MEXC and Hyperliquid spot mid prices with weights 3, 2, 2, 1, 1, 1, 1, 1
(https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/oracle, 2026-09-24, via WebSearch snippet).

**Mark price** = median of three inputs: (1) oracle price + 150-second EMA of (Hyperliquid mid - oracle);
(2) median of best bid, best ask and last trade on Hyperliquid; (3) weighted median of Binance, OKX,
Bybit, Gate IO, MEXC perp mid prices with weights 3, 2, 2, 1, 1. If exactly two of the three inputs
exist, the 30-second EMA of the median of best bid/best ask/last trade on Hyperliquid is added to the
median inputs. Mark and oracle update roughly every 3 seconds; mark is used for margining,
liquidations, TP/SL triggers and unrealized PnL
(https://hyperliquid.gitbook.io/hyperliquid-docs/trading/robust-price-indices and
https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/oracle, 2026-09-24, via WebSearch snippet).

**Funding (native perps)** (https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding,
2026-09-24, via WebSearch snippet):
- `F = AveragePremiumIndex(P) + clamp(interest_rate - P, -0.0005, +0.0005)`.
- Premium sampled every 5 seconds and averaged over the hour.
- Interest rate fixed at 0.01% per 8 hours (= 0.00125%/hour, ~11.6% APR, paid to shorts).
- The formula yields an 8-hour rate; funding is paid every hour at one eighth of that rate.
- Cap: 4% per hour.
- Payment = `position_size x oracle_price x funding_rate`; the oracle (not mark) price converts size to notional.
- API: `fundingHistory` rows carry `coin, fundingRate, premium, time` (`hyperliquid/info.py` L402-428, via SDK source).

**HIP-3 funding** (https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding and
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/hip-3-deployer-actions, 2026-09-24,
via WebSearch snippet):
- Responsive premium: `premium = 0.5 x (impact_bid_px + impact_ask_px) / oracle_px - 1`, where impact
  prices are the average execution prices to trade the bid/ask side. (Impact notional size: NOT VERIFIED.)
- Deployer funding multiplier must be in [0, 10] (scales the funding rate); deployer interest rate must
  be in [-0.01, 0.01] and replaces the interest-rate term.

**trade.xyz funding** (https://docs.trade.xyz/asset-directory/stocks/updates, 2026-09-24, via WebSearch snippet):
- `F_XYZ = 0.5 x [AveragePremiumIndex(P) + clamp(interest_rate - P, -0.0005, +0.0005)]`; scaling factor
  0.5 applied across all XYZ markets, lowering baseline funding to ~5.5% annualised and softening
  weekend price-discovery funding. Rationale stated: borrow for equities/commodities is closer to SOFR + 1-2%.
- Pre-IPO perps (IPOPs): premium samples are 1% of the standard XYZ calculation
  (https://docs.trade.xyz/asset-directory/pre-ipo-perpetuals-ipops, 2026-09-24, via WebSearch snippet).

## 3. Fees

Source unless noted: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees (2026-09-24, via WebSearch snippet).

- Tier basis: rolling 14-day volume, assessed at the end of each day (UTC);
  `14d weighted volume = 14d perps volume + 2 x 14d spot volume`. One fee tier per user across perps,
  HIP-3 perps and spot. Maker rebates are paid continuously per trade to the trading wallet.
- Baseline taker fee for validator-operated perps: **0.045%** (stated in the growth-mode paragraph).
- **Tier table returned by the snippet (tiers 1-6; tier 0 = <=$5M was not returned):**

| 14d weighted volume | "Perpetuals" per snippet (taker / maker) | "Spot" per snippet (taker / maker) |
|---|---|---|
| > $5M | 0.060% / 0.030% | 0.040% / 0.012% |
| > $25M | 0.050% / 0.020% | 0.035% / 0.008% |
| > $100M | 0.040% / 0.010% | 0.030% / 0.004% |
| > $500M | 0.035% / 0.000% | 0.028% / 0.000% |
| > $2B | 0.030% / 0.000% | 0.026% / 0.000% |
| > $7B | 0.025% / 0.000% | 0.024% / 0.000% |

  **Consistency warning:** the "Perpetuals" column contradicts the same page's statement that the perps
  baseline taker is 0.045% (a >$5M tier cannot be 0.060%). My unverified prior knowledge is that the
  column labelled "Spot" above is the historical *perps and spot* schedule (tier 0: 0.045%/0.015%,
  tier 1: 0.040%/0.012%, ..., tier 6: 0.024%/0.000%). Treat the table as **unconfirmed** and re-read
  the live fees page (or `{"type":"userFees"}`, which returns `userAddRate`/`userCrossRate`,
  `hyperliquid/info.py` L543-547, via SDK source).
- Maker rebate tiers: Tier 1+ requires >0.5% of 14-day weighted *maker* volume. Rebate sizes:
  **NOT VERIFIED** (unverified prior knowledge: -0.001% / -0.002% / -0.003% at >0.5% / >1.5% / >3%).
- Staking discounts (HYPE staked -> discount): Wood >10: 5%; Bronze >100: 10%; Silver >1,000: 15%;
  Gold >10,000: 20%; Platinum >100,000: 30%; Diamond >500,000: 40%.
- Referral: 4% discount on fees for the first $25M of volume
  (https://hyperliquid.gitbook.io/hyperliquid-docs/referrals, 2026-09-24, via WebSearch snippet).
- Builder codes (https://hyperliquid.gitbook.io/hyperliquid-docs/trading/builder-codes, 2026-09-24,
  via WebSearch snippet): builder fee at most 0.1% on perps and 1% on spot; applies to both sides of
  perp trades, not to the buy side of spot; user approves a max fee per builder via `approveBuilderFee`
  signed by the main wallet (not an agent wallet); max 10 active builder approvals per user; builders
  claim via the referral-reward claim flow. SDK: order `builder={"b": <addr>, "f": <int>}` with the
  example approving `"0.001%"` and passing `f=1` (`examples/basic_builder_fee.py`, via SDK source) —
  consistent with `f` in tenths of a basis point, but the unit is not documented in the SDK.
- HIP-3 deployer fee share: configurable 0-300% (0-100% under growth mode); above 100% the protocol
  fee is raised to equal the deployer fee
  (https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals,
  2026-09-24, via WebSearch snippet).
- Growth mode: >=90% reduction of all-in fees; baseline all-in taker 0.0045%-0.009% (5-10x below the
  0.045% baseline) (fees page, as above).
- trade.xyz fee page exists (https://docs.trade.xyz/perp-mechanics/fees) but **its content was not
  returned: NOT VERIFIED** whether xyz runs growth mode or what its deployer share is.

## 4. Leverage and margin

Sources: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining and
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margin-tiers (2026-09-24, via WebSearch snippet).

- Max leverage varies by asset from 3x to 40x (margining page). The margin-tiers page snippet separately
  states "Both BTC and ETH have a maxLeverage of 50" (likely from the API example); **the 40x vs 50x
  discrepancy must be resolved live via `meta` -> `universe[i].maxLeverage`.**
- Maintenance margin = half of the initial margin at max leverage: 1.25% (40x assets) to 16.7% (3x assets).
- Tiered formula: `maintenance_margin = notional x maintenance_margin_rate - maintenance_deduction`,
  where rate and deduction depend only on the margin tier; `maintenance_margin_rate(tier) =
  initial_margin_rate_at_max_leverage(tier) / 2` (e.g. 2.5% at 20x).
- Margin tables in `meta` (`marginTables`): each tier has `lowerBound` (notional above which leverage is
  constrained to that tier's `maxLeverage`); tiers sorted by increasing lower bound / decreasing
  maxLeverage; `marginTiers` max length 3 (HIP-3 deployer actions page).
- **Per-asset position limits by leverage class: NOT VERIFIED (search returned no table).** Read
  `marginTables` live.
- Cross margin is the default (shared collateral); isolated constrains collateral to one asset; "strict
  isolated" additionally disallows removing margin (margin is removed proportionally as the position
  closes). For cross positions the liquidation price is independent of the leverage setting.
- Portfolio margin (https://hyperliquid.gitbook.io/hyperliquid-docs/trading/portfolio-margin,
  2026-09-24, via WebSearch snippet): unified spot+perp balance; eligible collateral HYPE, BTC, USDC,
  USDT with LTV in [0,1] (HYPE 0.65, BTC 0.5); auto-borrow up to `token_balance x borrow_oracle_price x
  ltv`; borrow interest accrues continuously, indexed hourly; PMR > 95% means cross positions and
  collateral are at liquidation risk; liquidation triggers when the whole PM account falls below its
  portfolio maintenance requirement.
- HIP-3 margin modes: `strictIsolated` (no withdrawal of isolated margin from open positions),
  `noCross` (isolated with margin removal, no cross), `normal` (standard cross)
  (HIP-3 page, 2026-09-24, via WebSearch snippet).
- HIP-3 open-interest caps: notional caps (sum |size| x mark) enforced per dex total and per asset,
  plus size caps; caps must be at least max(1,000,000 collateral size units, half of current OI)
  (HIP-3 page, as above).
- trade.xyz caps/leverage (https://docs.trade.xyz/xyz-perps-specification/open-interest-caps and
  https://docs.trade.xyz/xyz-perps-specification/equity-perpetuals/single-name-equities, 2026-09-24,
  via WebSearch snippet): NVDA OI cap $50mm, TSLA $25mm; NVDA up to 10x. WTIOIL used as a 20x example on
  the discovery-bounds page. **GOLD, CL, TSLA max leverage: NOT VERIFIED.** Read live via `meta(dex="xyz")`.

## 5. Liquidation and ADL

Sources: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations and
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/auto-deleveraging (2026-09-24, via WebSearch snippet).

- Liquidation is evaluated on mark price (mark is "used for margining, liquidations" — robust-price-indices page).
- Most liquidations are sent to the order book so all users can compete for the flow and the liquidated
  user keeps remaining margin.
- Backstop liquidation: positions below 2/3 of maintenance margin can be taken over by the liquidator
  vault; maintenance margin is not returned to the user in that case (buffer so backstop is profitable on
  average); liquidation PnL flows to HLP.
- ADL: when an account value or isolated position value goes negative, users on the opposite side are
  ranked by unrealized PnL and leverage used; backstop-liquidated positions get no special treatment in
  the ADL queue. **The exact ranking score formula was not returned: NOT VERIFIED.**
- trade.xyz has its own page (https://docs.trade.xyz/trading/liquidations-and-auto-deleveraging);
  content not returned — NOT VERIFIED whether it differs.

## 6. API and data limits

**Info request types present in the SDK** (`hyperliquid/info.py`, via SDK source, 2026-09-24):
`clearinghouseState`, `spotClearinghouseState`, `openOrders`, `frontendOpenOrders`, `allMids`,
`userFills`, `userFillsByTime`, `meta`, `metaAndAssetCtxs`, `perpDexs`, `spotMeta`,
`spotMetaAndAssetCtxs`, `fundingHistory`, `userFunding`, `l2Book`, `candleSnapshot`, `userFees`,
`delegatorSummary`, `delegations`, `delegatorRewards`, `delegatorHistory`, `orderStatus`, `referral`,
`subAccounts`, `userToMultiSigSigners`, `perpDeployAuctionStatus`, `userDexAbstraction`,
`userAbstraction`, `historicalOrders`, `userNonFundingLedgerUpdates`, `portfolio`,
`userTwapSliceFills`, `userVaultEquities`, `userRole`, `userRateLimit`, `spotDeployState`, `extraAgents`.
Base URLs: `https://api.hyperliquid.xyz`, `https://api.hyperliquid-testnet.xyz` (`api/info/*.yaml`).

**candleSnapshot** (https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint,
2026-09-24, via WebSearch snippet): only the most recent 5000 candles are available per interval;
extra rate-limit weight accrues per 60 items returned. Request `{"type":"candleSnapshot","req":
{coin, interval, startTime, endTime}}`; response fields `t,T,s,i,o,c,h,l,v,n` (`api/info/candle.yaml`, via SDK source).

Lookback implied by the 5000-candle cap (arithmetic, 5000 x interval):

| Interval | Max lookback |
|---|---|
| 1m | 5,000 min = 83.3 h = 3.47 days |
| 5m | 25,000 min = 416.7 h = 17.4 days |
| 15m | 75,000 min = 1,250 h = 52.1 days |
| 1h | 5,000 h = 208.3 days |
| 4h | 20,000 h = 833.3 days = 2.28 years |
| 1d | 5,000 days = 13.7 years (effectively full history) |

**fundingHistory / time-range endpoints**: responses that take a time range return at most 500 elements
(or distinct blocks); paginate by using the last returned timestamp as the next `startTime`;
fundingHistory carries extra weight per 20 items returned (info-endpoint page, as above). With hourly
funding, 500 rows = 20.8 days per call; there is no months-based cap (pagination walks back as far as
data exists — extent NOT VERIFIED).

**Rate limits** (https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits,
2026-09-24, via WebSearch snippet):
- REST: aggregated 1200 weight per minute per IP.
- Exchange actions: weight `1 + floor(batch_length / 40)`.
- Info: `l2Book`, `allMids`, `clearinghouseState`, `orderStatus`, `spotClearinghouseState`,
  `exchangeStatus` weight 2; `userRole` weight 60; all other documented info requests weight 20;
  explorer requests weight 40; EVM JSON-RPC 100 requests/min.
- Address-based: 1 request per 1 USDC traded cumulatively since address inception; when rate-limited,
  1 request per 10 seconds. **Initial buffer (prior knowledge: 10,000 requests) and open-order caps:
  NOT VERIFIED (search returned no content).** `{"type":"userRateLimit"}` returns current usage (SDK).

**Order book**: `l2Book` is a snapshot only (`{coin, levels:[[bids],[asks]], time}`); the SDK yaml
describes it as "the top 10 bids and asks" (`api/info/l2book.yaml`, via SDK source; live depth/`nSigFigs`
options NOT VERIFIED). There is no historical order-book endpoint over REST; history must come from S3.

**WebSocket** subscription types in the SDK (`hyperliquid/websocket_manager.py`, via SDK source):
`allMids`, `l2Book`, `trades`, `userEvents`, `userFills`, `candle`, `orderUpdates`, `userFundings`,
`userNonFundingLedgerUpdates`, `webData2`, `bbo`, `activeAssetCtx`, `activeAssetData`. Coin-keyed
subscriptions accept `dex:COIN` names after remapping.

**Official S3 archives** (https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data, 2026-09-24,
via WebSearch snippet; all requester-pays, `aws s3 ... --request-payer requester`):
- `s3://hyperliquid-archive/market_data` — market data (L2 snapshots). Exact layout and the
  `asset_ctxs` prefix: **NOT VERIFIED** (unverified prior knowledge: `market_data/<date>/<hour>/l2Book/<coin>.lz4`
  and `asset_ctxs/<date>.csv.lz4`).
- `s3://hl-mainnet-node-data/node_fills_by_block` (current format, from `--write-fills --batch-by-block`);
  older `node_fills` (API-format fills) and `node_trades`; also `explorer_blocks`, `replica_cmds`
  (L1 transactions) and `misc_events_by_block` (transfers, staking, funding).
- Docs recommend S3 over the API for large batch pulls.

**Third-party: Hydromancer Reservoir** (https://docs.hydromancer.xyz/reservoir and
https://hydromancer.xyz/resources/hyperliquid-historical-s3-archive, 2026-09-24, via WebSearch snippet):
single public requester-pays bucket `s3://hydromancer-reservoir`, region `ap-northeast-1`; Parquet;
contents: fills (trades, liquidations, ADLs, builder and TWAP fills, with a `user` column), 1-second
OHLCV candles, daily position and balance snapshots, 20-level L2 order-book depth; updated daily;
readable with DuckDB/Polars/pyarrow/Spark. A GitHub thread reports fills coverage "perps from Aug 2025"
(https://github.com/tribulnation/sdk/issues/1, via WebSearch snippet). **L2 sampling frequency
(1-minute) and HIP-3 coverage start: NOT VERIFIED.** This is not an official Hyperliquid source.

## 7. HIP-3 equity / commodity / index markets via trade.xyz

**What the perp is.** "Perps rely on a funding rate mechanism to keep the contract price aligned with the
underlying spot price" (https://docs.trade.xyz/trading/perpetual-assets, 2026-09-24, via WebSearch snippet).
For pre-IPO perps the docs state explicitly that a position confers no ownership rights: no voting,
dividend, information, appraisal, subscription or allocation rights and no claim on corporate assets
(https://docs.trade.xyz/legal-and-disclaimers/pre-ipo-perpetual-markets-risks-and-disclaimers,
2026-09-24, via WebSearch snippet). **An equivalent explicit statement for single-name stock perps was
not returned (NOT VERIFIED), but follows by construction: cash-settled synthetic exposure, no delivery,
anchoring only through funding and the oracle.** Dividends enter pricing only through the cost-of-carry
model, which "takes into account the forward dividend yield of the underlying"
(https://docs.trade.xyz/perp-mechanics/oracle-price, 2026-09-24, via WebSearch snippet).

**Oracle** (https://docs.trade.xyz/perp-mechanics/oracle-price and
https://docs.trade.xyz/perp-mechanics/external-price, 2026-09-24, via WebSearch snippet):
- External pricing session: the externally derived fair price is transmitted as the oracle price in
  relayer updates (external price == oracle price).
- Internal pricing session: external price stays fixed at the external close; the oracle advances by a
  continuous-time EWMA that moves the previous oracle price by a fraction of the impact-price
  difference: `S_t = beta_t x S_{t-} + (1 - beta_t) x x_t`, `beta_t = exp(-dt/tau)`, `x_t = S_{t-} + IPD_t`
  (IPD = impact price difference). Values of `tau` per asset: NOT VERIFIED.
- Relayer updates are clamped to +/-50 bps of the current value to mitigate jumps; the oracle is
  described as robust to irregular updates and market halts.
- Equity indices (e.g. XYZ100): relayer consumes executable quotes for the underlying index futures
  from institutional LPs and converts them to an implied spot value
  (https://docs.trade.xyz/xyz-perps-specification/equity-perpetuals/equity-indices, 2026-09-24, via WebSearch snippet).

**Sessions.**
- US equities (https://docs.trade.xyz/asset-directory/stocks/us, 2026-09-24, via WebSearch snippet):
  external prices 24/5 from Sunday 20:00 ET to Friday 20:00 ET; internal pricing Friday 20:00 to Sunday
  20:00 ET, or whenever external datapoints gap by more than 30 seconds. US exchanges cover Mon-Fri
  09:30-20:00 ET; the overnight session is supplied by Blue Ocean ATS (BOATS), giving 24h x 5d coverage.
- Commodities (https://docs.trade.xyz/asset-directory/commodities, 2026-09-24, via WebSearch snippet):
  external prices 23/5 from Sunday 18:00 ET to Friday 17:00 ET with a daily 17:00-18:00 ET gap; daily
  maintenance 17:00-18:00 ET Monday-Thursday; futures holiday closures apply. The snippet also states
  internal pricing runs "Friday 5:00 PM to Sunday 5:00 PM ET" — a one-hour mismatch with the Sunday
  18:00 external start that must be checked on the page. Liquid spot markets exist for GOLD, SILVER,
  PLATINUM, PALLADIUM.
- GOLD references the USD spot price of 1 troy ounce of gold (XAU/USD) (commodities page, as above).
- CL references 1 barrel of WTI Light Sweet Crude Oil; the underlying futures contract rolls from the
  5th to the 10th business day of the month, weights moving from 100% front contract on the 5th to
  100% next contract on the 10th (https://docs.trade.xyz/consolidated-resources/roll-schedules,
  2026-09-24, via WebSearch snippet; the same snippet once phrases the window as "5th-9th business day" —
  confirm the exact end day live).

**Discovery bounds** (https://docs.trade.xyz/perp-mechanics/discovery-bounds and
https://docs.trade.xyz/changelog/discovery-bounds-v2, 2026-09-24, via WebSearch snippet): mark price
is restricted to +/-(1/maxLeverage) of a reference price in both external and internal sessions. The
reference is the last externally derived fair price (external session) or initially the last external
oracle price, e.g. Friday close (internal session). When the oracle reaches a trigger threshold (e.g.
90% of the distance to the bound for WTIOIL) the reference re-anchors to that bound and a new bound
forms; with the configured number of resets the total discoverable range widens (WTIOIL at 20x: +/-5%
instantaneous, ~+/-15.8% total).

**Corporate actions, splits, halts, maintenance.**
- Splits / specific corporate-action procedures: **NOT VERIFIED (search returned only the disclaimer
  that oracle, mark, conversion or settlement methodology "may be affected by ... corporate action
  changes, market disruption, or extreme volatility").**
- Halts: only the general statement that the oracle is robust to market halts and that relayer updates
  are clamped to +/-50 bps; no halt-specific procedure returned — **NOT VERIFIED.**
- Maintenance windows: commodities 17:00-18:00 ET Mon-Thu (verified, above); equities maintenance
  windows **NOT VERIFIED.**

**Leverage / caps found.** NVDA up to 10x, OI cap $50mm; TSLA OI cap $25mm; WTIOIL 20x (example).
GOLD, CL, TSLA max leverage: **NOT VERIFIED.** A "Market Parameters" changelog and a "Margin Modes"
page exist (https://docs.trade.xyz/changelog/market-parameters, https://docs.trade.xyz/risk-and-margining/margin-modes)
but their content was not returned.

## 8. What could not be verified

1. Current counts of native perps, spot pairs and the HIP-3 dex list (needs live `meta`, `spotMeta`, `perpDexs`).
2. The dex name `xyz` for trade.xyz and the other dex names (`hyna`, `para`, `mkts`, `flx`, `km`, `vntl`).
3. Full fee-tier table: tier 0 values, and the snippet's "Perpetuals" column conflicts with the 0.045% baseline.
4. Maker rebate sizes per rebate tier.
5. USDC as native collateral and USDH / aligned quote assets (page found, no content).
6. Impact notional used for the premium, and HIP-3 per-dex funding multipliers actually set by xyz.
7. Native max leverage 40x vs the 50x shown for BTC/ETH; per-asset margin-tier tables and position limits.
8. Exact ADL ranking score; trade.xyz-specific liquidation/ADL rules.
9. Address rate-limit initial buffer (10,000) and open-order caps; l2Book live depth options.
10. `hyperliquid-archive` layout (`asset_ctxs`, L2 snapshot cadence); Hydromancer L2 sampling interval and coverage dates.
11. trade.xyz fee schedule / growth-mode status, corporate-action and split procedures, halt handling,
    equities maintenance windows, EWMA time constants, and max leverage for GOLD, CL, TSLA.
12. Single-name equity perps' explicit "no dividends / no voting / no delivery" statement (only the
    pre-IPO disclaimer was returned).

## Source register

All retrieved 2026-09-24. Method: WS = via WebSearch snippet (page summaries; not full page text);
SDK = via SDK source (local clone, commit 2fdb18f, 2026-06-04).

| # | URL / file | Method | Used for |
|---|---|---|---|
| 1 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding | WS | funding formula, interest, cap, payment basis, HIP-3 premium |
| 2 | https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/oracle | WS | oracle composition, update cadence |
| 3 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/robust-price-indices | WS | mark price formula |
| 4 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees | WS | tiers, staking discounts, growth mode, fee-share |
| 5 | https://hyperliquid.gitbook.io/hyperliquid-docs/referrals | WS | referral discount |
| 6 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/builder-codes | WS | builder fee caps, approvals |
| 7 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining | WS | leverage range, maintenance margin, margin modes |
| 8 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margin-tiers | WS | tier formula, BTC/ETH 50x statement |
| 9 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/portfolio-margin | WS | portfolio margin |
| 10 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations | WS | order-book vs backstop liquidations |
| 11 | https://hyperliquid.gitbook.io/hyperliquid-docs/trading/auto-deleveraging | WS | ADL ranking |
| 12 | https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals | WS | HIP-3 margin modes, OI caps, fee share |
| 13 | https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/hip-3-deployer-actions | WS | funding multiplier / interest ranges, marginTiers max 3 |
| 14 | https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits | WS | IP / address rate limits, weights |
| 15 | https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint | WS | 5000-candle cap, 500-row cap, extra weights |
| 16 | https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data | WS | official S3 buckets |
| 17 | https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/aligned-quote-assets | WS (URL only) | USDH — no content returned |
| 18 | https://docs.trade.xyz/ | WS | trade[XYZ] market classes |
| 19 | https://docs.trade.xyz/trading/perpetual-assets | WS | perp anchoring via funding |
| 20 | https://docs.trade.xyz/asset-directory/stocks/updates | WS | F_XYZ = 0.5 x [...] |
| 21 | https://docs.trade.xyz/asset-directory/pre-ipo-perpetuals-ipops | WS | IPOP premium 1% |
| 22 | https://docs.trade.xyz/legal-and-disclaimers/pre-ipo-perpetual-markets-risks-and-disclaimers | WS | no ownership rights statement |
| 23 | https://docs.trade.xyz/perp-mechanics/oracle-price | WS | sessions, EWMA, +/-50 bps clamp, dividend yield in carry |
| 24 | https://docs.trade.xyz/perp-mechanics/external-price | WS | external price behaviour |
| 25 | https://docs.trade.xyz/perp-mechanics/discovery-bounds | WS | +/-1/maxLeverage bounds, re-anchoring |
| 26 | https://docs.trade.xyz/changelog/discovery-bounds-v2 | WS | reset mechanics |
| 27 | https://docs.trade.xyz/asset-directory/stocks/us | WS | equity sessions, BOATS |
| 28 | https://docs.trade.xyz/asset-directory/commodities | WS | commodity sessions, maintenance, GOLD spec |
| 29 | https://docs.trade.xyz/consolidated-resources/roll-schedules | WS | CL roll window |
| 30 | https://docs.trade.xyz/xyz-perps-specification/open-interest-caps | WS | NVDA / TSLA OI caps |
| 31 | https://docs.trade.xyz/xyz-perps-specification/equity-perpetuals/single-name-equities | WS | NVDA 10x |
| 32 | https://docs.trade.xyz/xyz-perps-specification/equity-perpetuals/equity-indices | WS | index oracle method |
| 33 | https://docs.trade.xyz/perp-mechanics/fees | WS (URL only) | no content returned |
| 34 | https://docs.trade.xyz/trading/liquidations-and-auto-deleveraging | WS (URL only) | no content returned |
| 35 | https://docs.trade.xyz/changelog/market-parameters | WS (URL only) | no content returned |
| 36 | https://docs.trade.xyz/risk-and-margining/margin-modes | WS (URL only) | no content returned |
| 37 | https://docs.hydromancer.xyz/reservoir | WS | Reservoir bucket spec |
| 38 | https://hydromancer.xyz/resources/hyperliquid-historical-s3-archive | WS | Reservoir contents |
| 39 | https://github.com/tribulnation/sdk/issues/1 | WS | Reservoir coverage note |
| 40 | hyperliquid-python-sdk `hyperliquid/info.py` | SDK | info request types, dex offsets, docstrings |
| 41 | hyperliquid-python-sdk `hyperliquid/exchange.py` | SDK | perpDeploy schema, sendAsset, approveBuilderFee |
| 42 | hyperliquid-python-sdk `hyperliquid/websocket_manager.py` | SDK | WS subscription types |
| 43 | hyperliquid-python-sdk `api/info/*.yaml` | SDK | base URLs, candle/l2Book schemas |
| 44 | hyperliquid-python-sdk `examples/*.py` | SDK | `dex:COIN` naming, builder fee example |
