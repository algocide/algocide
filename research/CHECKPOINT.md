# CHECKPOINT.md (updated after phase 2, 2026-09-25)

## Completed
1. Environment verified: `api.hyperliquid.xyz`, docs and every exchange/data host are denied by the sandbox network
   policy; only GitHub/PyPI reachable. Public GitHub archives of official API responses were used instead.
2. Datasets reconstructed and audited (`data/raw/`, DATA_AUDIT.md): 15-min sampled mids for all xyz markets + BTC/ETH
   (2026-05-01 → 09-25), daily liquidity snapshots (130 days), BTC/ETH real candles (1h Dec 2025–Jul 2026, 15m May
   26–Jul 17 2026), funding, one L2 snapshot, API fixtures (specs, OI caps, impact prices, sample xyz:NVDA candles).
3. Protocol frozen before testing (docs/PROTOCOL_FREEZE.md); markets selected on liquidity evidence
   (MARKET_SELECTION.csv → SNDK, MU, NVDA, META, GOOGL).
4. Package `src/hlr2` (calendar, panels, completed-1h views, indicators, costs, specs, engine, metrics, funding) with
   6 passing tests; experiment runner with sealed holdout; robustness; one-shot holdout; summary/figures; paper-trader
   scaffold with a working replay mode.
5. 45 distinct configurations run (245 rows in EXPERIMENTS.csv across universes and cost regimes); calibration of the
   sampled-mid approximation against real candles; one candidate screened, frozen (STRATEGY_SPEC.md) and evaluated
   once on the holdout → rejected. FINAL_REPORT.md written.

## State of the holdout
`results/stocks/HOLDOUT_OPENED.json` records that the stock holdout (2026-08-31 → 09-25) was opened for
`F4:channel_bo(atr_mult=1.5, n=12, tf=15m)`. It must not be reused for any other configuration. The crypto holdouts
were never opened (no candidate qualified).

## Exact next step (for a resumed session)
1. Ask the environment owner to allow `api.hyperliquid.xyz` (Network access → allowed domains). Then run
   `python3 ../forward/collector.py --out data/forward --every 60 --l2 xyz:SNDK,xyz:MU,xyz:NVDA,xyz:META,xyz:GOOGL,BTC,ETH`
   (previous session's collector) to record spreads/depth/OI, and archive 15-minute candles daily
   (`candleSnapshot`, 5,000-bar server cap) so a real-candle dataset accumulates.
2. Do NOT re-optimise on the existing sample. When ≥ 40 new sessions of real candles exist, re-run
   `experiments/run_experiments.py --universe stocks` on candles (requires a `load_candles` path for xyz markets:
   same parquet schema as `hyperdata_candles_15m.parquet`), treat the new data as a fresh holdout for the frozen spec.
3. Optionally run `forward/paper_trader.py --mode live --once` every 15 minutes from a machine that can reach the API
   (research observation only; the strategy is unproven).

## Phase 2 (agentic system) — completed
* `research/agent/` built and tested (5 tests); replay loop validated against the engine; 8 predeclared configs
  backtested (family A, EXPERIMENTS.csv; budget 53/60); no holdout opened; AGENT_SYSTEM.md written.
* Both videos identified and reviewed from InnerTube metadata, chapters, comments and the creator's repository (docs/VIDEO_REVIEW.md); transcripts unobtainable from this address. Added agent/cli.py (gate/preflight/flatten) and the testnet-first mainnet gate.

## Exact next step for phase 2
1. From a machine with API access: `PYTHONPATH=src python3 agent/loop.py --mode paper --once` every minute via cron
   (or a systemd unit with Restart=always), universe BTC,ETH, interval 1h. Let the journal accumulate ≥ 30 sessions
   and ≥ 50 trades. Optionally run a second instance with `"decider": "llm"` and your own ANTHROPIC_API_KEY to test
   the videos' actual claim (LLM > rules) side by side, same limits.
2. Evaluate the journal with the phase-1 gates; never raise limits or fund the live venue before that review.

## Phase 3 (three strategy videos) — completed
* docs/PHASE3_REPORT.md: momentum rank MOM-RS(120,top5,hold20) = most profitable (dev/val/holdout positive, fragile);
  long mean reversion rejected out of sample; the bot competition is a lottery. Holdout for daily_stocks opened once
  (results/phase3/HOLDOUT_OPENED_stocks.json) — do not reuse. Trials: 16 (10 + 6 robustness).
## Exact next step for phase 3
1. With API access: run MOM-RS(120,top5) prospectively in paper mode, weekly, five slots of ≥ $100 each (account ≥ $500)
   across the xyz stock perps; log every rebalance; judge after two quarters with the phase-1 gates.
2. Keep MR-SHORT-RSI2(90) as the $100 single-slot paper observation (never holdout-tested).
3. Do not re-tune on 2024-09 → 2026-09; treat 2026-09-24 onward as fresh data.

## Phase 4 (AI market analyst from the leopardracer article) — completed
* `analyst/` built and tested (15 tests): warehouse with provenance and typed placeholders, read-only Hyperliquid and
  SEC EDGAR adapters (parsers tested on synthetic fixtures; live fetch not possible from this address), the article's
  signal engine and composite, funnel with budgets and cost estimate, Claude-backed deep read and contradiction engine
  behind a stub (no key, no paid calls here), portfolio math, radar report with language check.
* Predeclared evaluation (docs/PROTOCOL_PHASE4.md) ran on dev/val only, holdout untouched: no evidence the price/volume
  anomaly score is informative (val +2.0pp CI [−1.2, +5.3]; lift 1.16; composite val AUC 0.48). Report:
  docs/PHASE4_REPORT.md. Radar for 2026-09-23 written with every unavailable field labelled.
## Exact next step for phase 4
1. On your machine: `export SEC_USER_AGENT="Name you@example.com"`; `python3 -m analyst.cli ingest --source edgar --what all`
   (add 13F filer CIKs to a config first if you want institutional flow); then
   `export ANTHROPIC_API_KEY=...; python3 -m analyst.cli run --date <last session> --live-llm --address <your HL address>`.
2. Read the radar; fix whatever the first live EDGAR run breaks (file naming varies by filer agent; errors are logged).
3. Only after 13F/Form 4/XBRL tables have a year of history: refit the composite (`backtest`) so the institutional and
   fundamental terms stop being placeholders; keep the holdout closed.
