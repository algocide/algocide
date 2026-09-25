# Hyperliquid stock-perp strategy research (session 2026-09-25)

Bounded, pre-registered search for a simple 15-minute / 1-hour strategy on the five most liquid trade.xyz (HIP-3)
US-stock perpetuals on Hyperliquid, with BTC/ETH perps as a separate comparison universe. Research and paper
trading only: no keys, no order submission, no purchases. **Read `FINAL_REPORT.md` first.**

Everything for this run lives under `research/`; the previous session's work (funding-carry / premium-reversion
research, `docs/`, `src/hlr/`, `experiments/e*.py`) is untouched.

## Results at a glance (2026-09-25)
* **Conclusion: no demonstrated edge.** 45 distinct configurations across six strategy families were tested under a
  pre-registered protocol. On the five selected stock perps (15-minute sampled mids, May–Sep 2026) only the 15-minute
  channel breakout passed the development + validation gates; it then lost on the untouched holdout (−$5.7 on $100
  over 19 sessions, PF 0.69) under every cost regime, and its earlier gains were mostly stops that sampled data cannot
  see. On BTC/ETH (real candles) nothing passed the gates in the US session; 24/7 trend and channel rules lost.
* Nothing is handed to paper trading as a candidate. `forward/paper_trader.py` can observe the frozen rules for
  research only (label: unproven).
* The one change that would make this research decisive is API access from the environment (allow
  `api.hyperliquid.xyz`) so that real 15-minute candles and a spread history can be recorded from now on.

## Phase 2 (same day): agentic trading system from the "Agentic AI Trading" videos
`AGENT_SYSTEM.md` documents the automated system built from the All About AI approach (digest → decision → verifier →
risk gate → venue → journal). Paper mode by default; live is key-gated and untested here. Its deterministic core was
backtested under the same protocol (EXPERIMENTS.csv family A): best profit factor ≈ 1.1 on BTC/ETH 24/7, inconsistent
elsewhere; no holdout opened; **profitability not demonstrated**. The LLM decision layer is wired but unevaluated.

## Layout
| Path | Content |
|---|---|
| `FINAL_REPORT.md` | Conclusion, selected markets, best candidate, validation/holdout/cost-stress, feasibility, BTC/ETH comparison, failure modes, next action |
| `DATA_AUDIT.md` | Sources, coverage, integrity checks, execution-data limitations |
| `MARKET_SELECTION.csv` | Liquidity evidence per xyz stock market and the chosen five |
| `EXPERIMENTS.csv` | Every trial (configuration × universe × cost regime) with development and validation metrics |
| `RESEARCH_LOG.md` | Decisions, failures, findings in time order |
| `STRATEGY_SPEC.md` / `STRATEGY_SPEC.json` | Exact frozen rules of the leading candidate (machine-readable copy drives the paper trader) |
| `CHECKPOINT.md` | Completed work and the exact next step |
| `docs/PROTOCOL_FREEZE.md` | Evaluation protocol written before any test ran |
| `src/hlr2/` | Package: sessions (NYSE calendar), data (sampled-mid and candle panels, completed-1h views), indicators, costs, specs, backtest engine, metrics, funding |
| `experiments/` | `market_selection.py`, `run_experiments.py`, `robustness.py`, `evaluate_holdout.py` (one-shot), `summarize.py` |
| `results/<universe>/` | `experiments.csv`, `splits.json`, `trades/*.parquet` (full trade logs), `HOLDOUT_OPENED.json` |
| `results/figures/` | Charts |
| `forward/paper_trader.py` | Prospective paper trader for the phase-1 candidate (dry-run; replay mode works offline) |
| `agent/` | Phase-2 agentic system: feeds, digest, deciders, verifier, risk gate, paper/live venues, heartbeat loop, backtest adapter |
| `AGENT_SYSTEM.md` | Phase-2 write-up: what the videos describe, what was built, evidence, how to run |
| `tests/test_core.py` | Focused tests (no future information, completed-1h alignment, calendar, sizing/rounding, costs+funding, stop/target sequencing, single position, drawdown pause) |
| `data/raw/` | Reconstructed datasets with manifests (see DATA_AUDIT.md) |

## Reproduce
```bash
cd research
pip install -r ../requirements.txt
PYTHONPATH=src python3 tests/test_core.py                       # 6 tests
PYTHONPATH=src python3 tests/test_agent.py                      # 5 agent tests
PYTHONPATH=src python3 experiments/run_agent_backtests.py --universe crypto247 --regimes base,adverse   # family A
# datasets are committed (data/raw). To rebuild them from the upstream public repos:
#   git clone --filter=blob:none --no-checkout https://github.com/Tohshi-memo/HyperLiquid-Bot-test  <dir>
#   (cd <dir> && git fetch --unshallow --filter=blob:none)   # then:
#   python3 src/hlr2/reconstruct_tohshi.py <dir> data/raw ; python3 src/hlr2/reconstruct_liquidity.py <dir> data/raw
#   git clone --depth 1 https://github.com/lukasbecker36-dot/hyperdata  (CSV -> data/raw via the snippet in DATA_AUDIT.md)
python3 experiments/market_selection.py                         # MARKET_SELECTION.csv
PYTHONPATH=src python3 experiments/run_experiments.py --universe stocks   --regimes base,adverse,standard_fee
PYTHONPATH=src python3 experiments/run_experiments.py --universe crypto15 --regimes base,adverse
PYTHONPATH=src python3 experiments/run_experiments.py --universe crypto1h --regimes base,adverse
PYTHONPATH=src python3 experiments/run_experiments.py --universe crypto247 --regimes base,adverse
PYTHONPATH=src python3 experiments/summarize.py                 # EXPERIMENTS.csv + figures (dev+validation only)
PYTHONPATH=src python3 experiments/evaluate_holdout.py --universe stocks --config "<config>"   # ONCE
PYTHONPATH=src python3 forward/paper_trader.py --mode replay --start 2026-09-10 --state results/forward/replay_state.json
```

## Conventions (engine)
* Decision at the close of a completed bar; fill at the next observation (sampled data) or next bar open (candles),
  taker fee + half-spread + 1 bp slippage per side; stops filled beyond the stop with an extra adverse penalty.
* 1-hour bars are formed from four 15-minute slots aligned to 09:30 ET (session data) and are usable only once all
  four slots are complete; a 15-minute decision never sees an unfinished 1-hour bar (tested).
* US regular session only for stock perps (NYSE calendar with holidays, early closes, DST). Latest entry 90 minutes
  before the close; flat decision 45 minutes before the close (fill within 15 minutes). Session times: 09:30–16:00 ET
  = 17:30–00:00 Asia/Tbilisi (EDT) or 18:30–01:00 (EST).
* $100 account, $1 planned risk including estimated round-trip costs, gross notional ≤ 2 × equity, one position
  across the universe, sizes rounded to szDecimals, $10 minimum order, $10 drawdown pause with a shadow book.

## Limitations (short version; details in DATA_AUDIT.md and FINAL_REPORT.md)
* The sandbox could not reach `api.hyperliquid.xyz` or the docs; all data are third-party GitHub archives of official
  API responses. Stock-perp prices are 15-minute **sampled mids** (no intrabar high/low, no volume), May–Sep 2026.
* Spreads and depth are two snapshots, not histories. Fees for xyz assume growth mode (verified enabled as of
  2026-09-25) and are stress-tested at the standard schedule.
* Sample sizes are small (92 US sessions for stocks; 37 sessions of 15m and 142 of 1h data for BTC/ETH).
