# CHECKPOINT.md (final for this session, 2026-09-25 ~09:50 UTC)

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
