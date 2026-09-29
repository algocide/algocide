# ETH 15-minute up/down: pre-registration

Written 2026-09-29 23:20 UTC, before any strategy was evaluated and before the data was split. Every later
decision goes into `docs/eth15/ledger.md` with a time; anything decided after seeing results is marked POST-HOC.

## The bet

* Data: Binance ETHUSDT perpetual 1-minute klines, 2021-01-01 to 2026-09-28 (taker-buy volume and trade counts from
  the raw monthly archives), BTCUSDT 1-minute klines and ETH funding rates for context features.
* Candles: 15 minutes, aligned to :00, :15, :30, :45 UTC. A bet is placed at a candle's open and settles at its close.
  **Up** wins if close >= open, **Down** wins if close < open.
* Payout 1.8x: a win returns 1.8 times the stake (net +0.8), a loss loses the stake. Fee: 0.06% of the stake on every
  bet, win or lose.
* Break-even win rate: (1 + 0.0006) / 1.8 = **55.59%**. The bar set by the request: **57%**.
* A signal may only use data that exists before the candle opens (everything up to the previous minute's close).

## Hidden data

* Chronological split by candle count: the first two thirds are the **research set**, the last third is the
  **holdout**.
* `experiments/eth15/prepare.py` writes the two sets to separate files and records the holdout's SHA-256 in
  `results/eth15/split.json`. Research code reads only the research file and prints nothing about the holdout.
* The holdout is read once, by `experiments/eth15/validate.py`, which refuses to run unless the frozen survivor list
  (`results/eth15/survivors.json`) is committed in git and unchanged.

## Research (research set only)

* Families: previous-candle momentum and reversal (conditioned on size), streaks, time of day and day of week,
  funding-time effects, volatility and volume conditions, wicks, RSI and Bollinger mean reversion, BTC-ETH divergence,
  order-flow imbalance (taker-buy share), and a logistic model on the combined features.
* Every evaluated candidate goes to `results/eth15/research_log.jsonl`: its rule, parameters, bets, wins and win rate
  on the whole research set and on each half of it.
* Models are fitted on the first 70% of the research set; their confidence threshold is chosen on the last 30%, and
  their research win rate is the win rate on that last 30% only.

## Survivor rule (fixed now)

A candidate survives the research stage if, on the research set (for models: its last 30%):

1. it bets at least **200** times,
2. its win rate is **above 57%**, and
3. its win rate is above break-even (55.59%) in **each half** of that sample (so one lucky year cannot carry it).

Survivors are frozen in `results/eth15/survivors.json` and committed before the holdout is opened.

## Validation (holdout, once)

* Each survivor runs unchanged on the holdout candles (lookbacks may use research-period data, which is the past).
* It **passes** if its holdout win rate is above 57%. Also reported: the Wilson 95% interval, a one-sided binomial
  p-value against break-even, and the bankroll path under half Kelly.
* **Half Kelly**: stake fraction f = 0.5 x (p a - q c) / (a c), with a = 0.8 - 0.0006 (net win), c = 1.0006 (net
  loss), q = 1 - p, and **p = the survivor's research win rate** (never the holdout's). Each bet stakes f of the
  current bankroll; one bet per candle per strategy; bankroll starts at 1,000.

## What will not be done

No tuning after the holdout is opened, no second look with changed rules, no reporting of a holdout-selected
strategy as if it had been chosen in advance.
