# CHECKPOINT.md (updated 2026-09-25 ~08:55 UTC)

## Completed
1. Environment verified: api.hyperliquid.xyz and docs blocked by network policy; public GitHub archives located.
2. Datasets reconstructed and audited (`data/raw/`, `DATA_AUDIT.md`): Tohshi 15-min mids (May 1–Sep 25 2026, 125 xyz
   markets + BTC/ETH), daily liquidity snapshots (130 days), hyperdata BTC/ETH candles (1h Dec 2025–Jul 2026; 15m
   May 26–Jul 17 2026), funding, one L2 snapshot, Freedom API fixtures (specs, OI caps, impact prices).
3. Protocol frozen (`docs/PROTOCOL_FREEZE.md`); markets selected (`MARKET_SELECTION.csv`: SNDK, MU, NVDA, META, GOOGL).
4. Package `src/hlr2` + 6 tests passing; experiment runner with sealed holdout; robustness and one-shot holdout scripts;
   paper-trader scaffold (`forward/paper_trader.py`, replay mode).
5. Runs: crypto15 (37 cfg × 2 regimes), crypto1h (12 × 2), crypto247 (4 × 2) done; stocks (37 × 3) in progress;
   MA-filter redefinition (F1b) reruns launched for crypto.

## Exact next step
* When `results/stocks/experiments.csv` exists: run `--families F1b --append` for stocks, then
  `PYTHONPATH=src python3 experiments/summarize.py`, apply the screening gates, pick ≤ 1 primary candidate on
  dev+validation, run `experiments/robustness.py` for it, then `experiments/evaluate_holdout.py` ONCE, write
  STRATEGY_SPEC.md/json, FINAL_REPORT.md, update RESEARCH_LOG.md, commit and push.
