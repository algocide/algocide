# Phase 4 source digest: "How to Build an AI Market Analyst on Kimi K3 and GPT-6 Astra: The Full Architecture"

**Source:** X article by @leopardracer, https://x.com/leopardracer/status/2101982527871131955 (posted 2026-09-21).
**How it was captured:** X is unreachable from the research sandbox (all mirrors blocked), so the
user supplied the article as 26 screenshots on 2026-09-25. This file transcribes the substance,
with every formula kept verbatim. Model specs and prices are quoted as the article states them;
none were verified independently.

The article's own framing, repeated several times: this is an **analyst, not a predictor**. Every
numeric example is labelled illustrative or constructed, including the regression table, the
correlation matrix and the backtest numbers.

---

## 1. Layer 1: data warehouse

Data the author lists as inputs:

- price and volume (minute and daily)
- market cap
- earnings dates and results
- analyst estimate revisions
- 13F holdings (lagged up to 45 days)
- Form 4 insider transactions
- options activity (put/call ratio, unusual activity)
- news flow
- broker customer-flow data
- technical indicators

Architecture diagram:

```
Prices / Filings / News
        -> Data Warehouse
        -> Signal Engine (plain code, not an LLM)
        -> Kimi K3 (long-context work)  /  GPT-6 Astra (heavy reasoning)
        -> Final Report
        -> Human
```

## 2. Layer 2: signal engine (deterministic)

### Price and volume anomalies

```
volume_ratio = today's_volume / average_volume_20d            # 34 / 11.8 = 2.88
return_5d    = (price_today - price_5d_ago) / price_5d_ago     # 142 -> 158 = 11.3%
z_return     = (return_5d - historical_mean) / historical_stdev
               # mean 0.8%, stdev 3.4%  ->  (11.3 - 0.8) / 3.4 = 3.08
z_volume     = same construction applied to volume
anomaly_score = |z_return| + |z_volume|
```

`anomaly_score` is a trigger for investigation, not a signal. Events use `anomaly_score > 2`.

### Institutional flow

```
position_change_pct = (shares_now - shares_before) / shares_before   # 2.4M -> 3.1M = +29.2%
institutional_flow  = sum_j ( weight_j * position_change_j )         # over funds j
```

Fund weights come from each fund's historical lead/lag accuracy. The filing date is always shown
next to the signal, because 13F data can be up to 45 days stale.

### Composite (logistic regression)

Build the sample from historical events with `anomaly_score > 2`. Label each event with
"price rose 5%+ over the next 20 trading days".

```
P(rise) = 1 / (1 + e^-(b0 + b1*retail + b2*institutional + b3*fundamental + b4*momentum + b5*options))
```

Illustrative coefficient table (the author states it is not real):

| feature               | coefficient | p-value | note            |
|-----------------------|-------------|---------|-----------------|
| institutional         | 0.41        | 0.001   |                 |
| fundamental revision  | 0.33        | 0.004   |                 |
| momentum              | 0.19        | 0.02    |                 |
| options               | 0.11        | 0.08    | not significant |
| retail                | -0.07       | 0.11    | not significant |

### Sequence (who moved first)

```
lag_institutional_to_price = time_of_price_move - time_of_institutional_signal
lag_retail_to_price        = time_of_price_move - time_of_retail_signal
```

Large positive lag: the signal led the move. Negative lag: the signal only became visible after
the move, usually the filing lag.

## 3. Layer 3: Kimi K3 (long context)

Article's spec: Moonshot, released 2026-07-16, 2.8T-parameter MoE, 1,048,576-token context,
$3 per million input tokens, $15 per million output tokens.

Job: put the last eight quarters of filings and calls into one context and find where management
language stayed the same while the numbers moved.

```
consistency = 1 - |economic_reality - management_claim| / max(|economic_reality|, |management_claim|)
# 0.4 vs 0.8 -> 1 - 0.4/0.8 = 0.5   "rhetoric diverging from the trend, dig further"
```

## 4. Layer 4: GPT-6 Astra (heavy reasoning)

Article's spec: OpenAI, released 2026-09-03, 1.05M-token input, 128K output, five reasoning-effort
levels, $10 per million input, $50 per million output, $1 per million cached input.

Job: answer "what am I still missing?" through a three-agent contradiction engine.

- **Agent A**: strongest bullish thesis from the provided data only, a source for every claim.
- **Agent B**: strongest bearish thesis, same rules.
- **Agent C (arbiter)**: for every major claim, check the source, find contradicting evidence,
  flag unsupported assumptions, and state what new information would change the conclusion.

```
thesis_gap = bull_strength - bear_strength
```

A large gap marks where the market cannot agree with itself. Agreement between A and B is often
the more reliable signal.

### Worked hypothetical (NVDA, constructed numbers)

`return_5d +11.2%`, `volume_ratio 2.7`, `z_return 3.1`, `institutional_flow +1.4`,
`fundamental_score +1.8`. A good system then runs this checklist:

1. Was there a catalyst (earnings, guidance)?
2. Did analysts revise estimates before or after the price moved?
3. Did institutional buying precede the move, or is it only visible now because of the 45-day lag?
4. Is retail chasing or leading?
5. Does options activity confirm direction, or is it speculative noise?
6. Did valuation expand faster than earnings expectations changed?
7. Are competitors showing the same demand pattern (industry shift or one-company story)?

Good output: "revenue grew 31%, but forward P/E expanded from 28x to 41x over the same window;
the market is pricing in future growth faster than the company has delivered it." Bad output:
"bullish momentum confirmed."

## 5. Portfolio math

### Contribution to return

```
portfolio_return = sum_i ( weight_i * return_i )
# 40% NVDA / 25% MSFT / 20% AAPL / 15% ETH, NVDA +10% this week:
NVDA_contribution = 0.40 * 0.10 = 0.04 = +4pp
```

If the whole portfolio was up 4.3%, almost all of it came from one position.

### Concentration (Herfindahl-Hirschman Index)

```
concentration = sum_i ( weight_i^2 )
# 20 equal-weight assets:             20 * 0.05^2 = 0.05
# one at 50%, rest across 19 assets:  0.5^2 + 19 * (0.5/19)^2 = 0.25 + 0.013 = 0.263
```

Fivefold difference at the same ticker count. Flag it automatically; nobody checks by hand daily.

### Hidden correlation

```
correlation(X, Y) = covariance(X, Y) / (stdev_X * stdev_Y)
```

Illustrative matrix (not live data) for a semiconductor basket:

|      | NVDA | AMD  | TSM  | AVGO |
|------|------|------|------|------|
| NVDA | 1.00 | 0.71 | 0.68 | 0.75 |
| AMD  | 0.71 | 1.00 | 0.64 | 0.69 |
| TSM  | 0.68 | 0.64 | 1.00 | 0.61 |
| AVGO | 0.75 | 0.69 | 0.61 | 1.00 |

Average pairwise correlation about 0.68: four "different" positions are mostly one factor bet
(AI-infrastructure demand). Principal component analysis is the rigorous version; the pairwise
average is enough to flag "you don't have the diversification you think you have".

## 6. Pipeline economics: the filtering funnel

```
one deep Astra pass (500K in, 20K out): 0.5M * $10 + 0.02M * $50 = $5 + $1 = $6 per company
10,000 companies daily:                  10,000 * $6 = $60,000/day  ~  $1.3M/month
```

That is a way to go bankrupt in a quarter, hence the funnel:

```
10,000 companies
  -> deterministic rules      -> 800
  -> lightweight classifier   -> 150
  -> Kimi K3 deep read        -> 40
  -> GPT-6 Astra reasoning    -> 8
  -> human review             -> 2-3
```

```
Kimi K3 on 150 candidates (300K in / 8K out each): 150 * (0.3M * $3 + 0.008M * $15) = 150 * ($0.9 + $0.12) ~ $153
Astra on the final 40:                              40 * $6 = $240
total daily AI spend:                               ~$400-450, not $60,000
```

"Optimize the funnel before you optimize the model."

## 7. Regulation and permissions

Charging US subscribers for output that analyses specific tickers risks "investment adviser"
territory under the Investment Advisers Act of 1940, especially if outputs read as
recommendations. The line: general information service (no registration) versus personalised
investment advice (RIA registration, disclosure obligations, fiduciary duty).

Practical consequence: the system's language stays at "here's what changed, here's the source"
and never "we recommend buying". No numeric scores like "87/100"; a score presented as guidance
is a regulatory exposure as well as a methodology weakness.

If the system connects to a brokerage with trading capability, permissions need hard separation:

```
Market data       READ
Portfolio         READ
Research          READ
Orders            requires explicit human confirmation
Transfers         NO
Account settings  NO
```

Brokerages that allow agent-connected trading state the user remains responsible for the agent's
actions. That does not remove the need for risk checks and explicit confirmation before anything
executes.

## 8. Backtesting the method

The author flags the whole section as a worked example, not a real study.

```
forward_return_k = (price_at_t+k - price_at_t) / price_at_t     # for the chosen horizons k
```

Take historical events with `anomaly_score > 2` (Group A) and events with score < 0 (Group B),
compare mean forward returns. Illustrative: Group A +3.8% over 20 days, Group B +0.9%. A gap
guarantees nothing for a single trade because a wide spread hides behind any average. Always
report the distribution (median, stdev, share of losers), not just the mean; one outlier can wreck
a naive comparison.

```
precision = true_positives / (true_positives + false_positives)
recall    = true_positives / (true_positives + false_negatives)
```

If 200 signals were flagged "strong" and 74 led to a sustained move, precision is 37%. The base
rate for a random 20-day window showing a 5%+ gain across the broad market usually runs 15 to 20%,
so 37% would be a real edge over chance while still being far from a reliable forecast.

## 9. What the output looks like

```
MARKET RADAR - September 18, 08:40
Assets monitored: 1,284
Anomalies detected: 47
High-information events: 11
Portfolio risks: 4
Upcoming catalysts: 7
```

**Company A, priority: high.** Price +8.4% / Volume 3.4x / Institutional signal: positive (filing
dated 12 days ago) / Fundamental: positive / Catalyst: earnings 3 days ago / Contradiction:
forward P/E expanded from 24x to 33x, outpacing the earnings revision.

**Company B, priority: medium.** Price +4.1% / Volume 2.1x / Institutional signal: neutral /
Retail flow strong, began *after* the price move / Read: retail chasing an existing move, not
new information.

"No score. No BUY. Just what changed, sourced, and what contradicts it."

## 10. Closing thesis

A professional analyst spends 6 to 8 hours on first-pass research per company. A well-built
system cuts that to 20 to 30 minutes, because most of that time is extracting and
cross-referencing facts scattered across sources, which is what a million-token context does
cheaply. "You're selling research velocity, not prediction. Predictions, on average, don't beat
the market, and any honest product says so on page one. The fraud starts exactly where 'explains
the past' gets sold as 'predicts the future.' Don't let that confusion into your own product."

---

## 11. What this means for this repo (pre-build notes)

Already in hand, usable offline:

- Daily bars for 59 Hyperliquid-listed stocks plus BTC, ETH, HYPE (2024-09-24 to 2026-09-23),
  enough for `volume_ratio`, `return_5d`, `z_return`, `z_volume`, `anomaly_score`, forward
  returns, the Group A/B comparison, precision/recall against a 5%-in-20-days label, portfolio
  contribution, HHI and the correlation matrix.
- Hyperliquid market fixtures (specs, fees, leverage caps) and the read-only feed code from the
  phase 2 agent.

Not in hand, and not reachable from the sandbox: 13F and Form 4 filings, 8-K/10-Q/10-K text,
earnings dates, estimate revisions, options flow, retail flow, news. These become typed
placeholder columns in the warehouse schema with adapters that run on the user's machine.

The article's hard constraints line up with the constraints this project already runs under:
read-only market data and portfolio, orders only with explicit human confirmation, no transfers,
no account settings, no scores or buy/sell language in reports.

Open decisions before building are listed in the conversation; this file is the source of truth
for the formulas.
