# AGENT_SYSTEM.md — the "agentic" automated trading system (built 2026-09-25, phase 2)

## 0. What was asked, what could be reviewed, what was built
Asked: review two YouTube videos and build a profitable automated trading system based on them.

* **Video 2** (`ZN0gkZw-2ks`) is "Agentic AI Trading For Beginners: A New Money Making Era Is Here" (All About AI /
  Kristian Fagerlie, 2026-06-04). YouTube and every transcript mirror are blocked from this sandbox, so the review rests
  on indexed descriptions, the creator's companion guide as indexed by search, and the open-source agents the videos'
  approach matches. The approach: an LLM agent (Codex 5.5 or Claude Code running a long-lived `/goal` loop) trading
  Hyperliquid perps (and Polymarket); a lightweight sub-agent polls positions and market data into a compact JSON
  digest; a main agent evaluates P&L against a goal and issues buy/sell/hold decisions with take-profit and stop-loss
  each heartbeat; indicators (RSI, EMA, MACD, Bollinger, ATR, volume, open-interest change) are computed locally;
  small test capital (about $200 USDC on Arbitrum), hard loss limits, and a review of every trade log before sizing up.
  The evidence offered in that ecosystem is live experiments with real wallets (including a Codex-vs-Claude head to
  head), not a validated edge.
* **Video 1** (`aI34O-ZA0VY`) could not be identified: its ID is not indexed anywhere reachable. Not reviewed.
* **Built**: the same architecture, engineered so that its decision core can be backtested under the protocol of
  phase 1, with paper trading by default and a key-gated live venue. **Profitability is not demonstrated**; see §4.

## 1. Architecture (`research/agent/`)
```
feed.py      LiveFeed (POST /info: candleSnapshot, allMids, l2Book, metaAndAssetCtxs) | ReplayFeed (committed datasets, no look-ahead)
digest.py    the "sub-agent": completed-bar indicators per asset + account state -> compact JSON digest
decide.py    RuleConsensusDecider (deterministic, backtestable, DEFAULT) | LLMDecider (Anthropic Messages API, optional, never run here)
             VerifierGate: deterministic skeptic (stop side, stop distance <= 5%, reward:risk >= 1.5)
risk.py      RiskGate: $1 planned risk incl. costs, gross notional <= 2x equity, one position, $3/day loss halt,
             $10 drawdown pause (shadow), $20 drawdown KILL, cooldown, max 6 trades/day, US-session rules for stock perps,
             szDecimals rounding, $10 minimum; the decider sees these limits and cannot change them
venues.py    PaperVenue (taker fees, half-spread + 1 bp, adverse stop fill, hourly funding, stop-before-target on a bar)
             HyperliquidVenue (official SDK; IOC entry, reduce-only trigger stop and take-profit; closes if the stop is rejected)
loop.py      heartbeat: new completed bar -> manage position -> digest -> decide -> verify -> risk -> execute -> journal
             modes: replay (offline), paper (live data, simulated fills), live (--live --acknowledge-risk + env keys)
backtest_adapter.py  the same rules as an hlr2 Strategy, so evidence is produced by the tested engine
```
Journal: `results/agent/journal.jsonl` (every decision, rejection with stage and reason, entry, exit, execution
failure, kill). State: `results/agent/state.json`. Kill switch: create `results/agent/KILL` (the agent flattens and
refuses to run until it is removed).

## 2. Decision rule (the deterministic core that was tested)
Score = higher-timeframe EMA20 vs EMA50 direction (±1) + decision-timeframe EMA20 vs EMA50 (±1) + MACD histogram sign
(±1) + RSI regime (+1 if 40 ≤ RSI ≤ 70, −1 if 30 ≤ RSI ≤ 60, else 0). Buy when score ≥ threshold, RSI < 70 and price
above EMA20; short mirror. Stop = entry ∓ 2 ATR(14); target = entry ± 4 ATR (reward:risk 2). Exit on the opposite
consensus, after 32 bars, at the stop/target, at the session end (stock perps) or at 96 bars of age. One position at
a time; lowest-spread market first on ties. Default: BTC and ETH, 1-hour bars, 24/7, threshold 3.

## 3. How to run
```bash
cd research && pip install -r ../requirements.txt hyperliquid-python-sdk
PYTHONPATH=src python3 tests/test_agent.py                                    # 5 tests
PYTHONPATH=src python3 agent/loop.py --mode replay --source candles --interval 1h --universe BTC,ETH \
    --start 2026-05-01 --end 2026-06-01 --state results/agent/replay_state.json   # offline, no network
PYTHONPATH=src python3 agent/loop.py --mode paper --once                      # live data, paper fills (needs api.hyperliquid.xyz)
# live (NOT run here): export HL_AGENT_PRIVATE_KEY=<agent wallet key> HL_ACCOUNT_ADDRESS=<main address>
#   set "live": {"enabled": true} in your config copy, smoke-test on testnet (base_url api.hyperliquid-testnet.xyz), then
#   PYTHONPATH=src python3 agent/loop.py --config my.json --mode live --live --acknowledge-risk
# LLM decider (optional): set "decider": "llm" and ANTHROPIC_API_KEY; every LLM output still passes the verifier and risk gate.
```
Run it under a watchdog (systemd `Restart=always` or a cron `--once` every minute); the loop only acts on new
completed bars, so restarts are safe. The videos' "agent can self-terminate" problem is handled by `--once` + cron.

## 4. Evidence (development + validation only; every holdout stays sealed)
Eight predeclared configurations (threshold {2,3} × stop/target {2/4, 1.5/3} ATR × trend timeframe {1h, same}) through
the tested engine, base and adverse costs (EXPERIMENTS.csv family A; trial budget now 53 of 60):

| Universe | Best by consistency | Dev | Validation | Gates (of 5) |
|---|---|---|---|---|
| BTC/ETH 24/7, real 1h candles (209 days) | threshold 3, 2/4 ATR | +$6.6, PF 1.11, 113 trades | +$4.2, PF 1.12, 67 trades, 4/7 windows, adverse +$2.2 | 4 (fails PF ≥ 1.3) |
| BTC/ETH US session, 1h (142 days) | all four configs | −$11.6 to −$17.5 | −$0.2 to −$3.2 | ≤ 2 |
| BTC/ETH US session, 15m + 1h trend (37 days) | all eight | −$2.5 to +$3.1 | −$3.8 to −$9.1 | 0 |
| Stocks (sampled mids, 90 sessions; holdout already spent) | threshold 3, 2/4 ATR, 1h trend | −$6.1, PF 0.79 | +$4.9, PF 1.59, adverse +$3.4 | 5, but development negative |

Reading: the deterministic core of the "agentic" approach shows, at best, a profit factor around 1.1 on BTC/ETH 24/7
(carried by shorts: +$9.1 short vs −$4.9 long on validation) and inconsistent results elsewhere. No configuration
earned a holdout look. The replay loop and the engine agree on matched trades (60 of 70/74 entries matched, mean
|P&L difference| $0.002), so what was tested is what the loop trades.

What was NOT tested: the LLM decision layer itself (no key; paid calls disallowed; and no historical LLM decisions
exist to backtest). The videos' claim is exactly that an LLM plus this scaffolding beats the deterministic rules;
nothing here supports or refutes that claim. It can only be settled prospectively, in paper mode, with the journal.

## 5. Why it may lose money live (beyond §4)
Model mistakes and stale data (the LLM path), latency between bar close and fill, leverage, funding on multi-day holds
(BTC/ETH funding averaged +0.0005%/h in the sample; small), a deployer switching xyz growth mode off (10x fees), the
$100 account's tick rounding on $1,000+ names, the 2026 regime (memory-stock mania, BTC drifting from $88k to $64k)
not repeating, and the multiple-testing burden already spent in phase 1.

## 6. Recommendation
Run the system in **paper mode** (LLM decider optional) from a machine that can reach the API, for at least 30
sessions / 50 trades, and judge it on the journal with the phase-1 gates. Do not fund it on the strength of the
videos or of this backtest. If you want the deterministic core only, the BTC/ETH 24/7 threshold-3 configuration is the
one to observe. Nothing here is investment advice.
