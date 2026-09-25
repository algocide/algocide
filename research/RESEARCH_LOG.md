# RESEARCH_LOG.md — decisions, failures, findings (session 2026-09-25, branch claude/sweet-bardeen-fzr7j4)

Times are UTC. The previous session (2026-09-24, `docs/`, `src/hlr/`, `experiments/e*.py`) is preserved untouched;
this run lives in `research/`.

## 07:25 Start. Environment check
* `api.hyperliquid.xyz` → CONNECT 403 from the sandbox proxy; same for all Hyperliquid hosts, both docs sites, all
  exchange APIs, Yahoo, CoinGecko, DefiLlama, Hugging Face. WebFetch is blocked for the same hosts. Only GitHub, PyPI
  and S3 (requester-pays buckets → useless) are reachable. Decision: do not stop; source data from public GitHub
  repositories whose owners ran the official API, and verify venue facts from committed API responses plus search
  snippets. Tell the user the exact host to allow-list.
* User message during the run: "https://app.hyperliquid.xyz/trade/UNI I have account". Noted; no change to scope
  (research only, no keys). The account does not help from inside this sandbox.

## 07:30–07:55 Data discovery (what exists, what does not)
* No public archive of true 15m/1h candles for xyz stock perps exists in reachable places. Candidates examined:
  bond-labs-dev/hyperliquid-data (tool, no data), moondevonyt/Hyperliquid-Data-Layer-API (paid API examples),
  JohnGavin/historical (no HL data), Freedom (its candle archive is a GitHub Actions artifact, unreachable),
  sanketagarwal/hyperliquid-trading-agent (code only), kerb (config only), CozanetHQ (native top-30 by OI only),
  hazwop/funding-scout (funding only), perp-basis (GOLD/CL only).
* Found: **lukasbecker36-dot/hyperdata** (real 1h candles Dec 2025→Jul 2026 and 15m May 26→Jul 17 2026 for 177
  native perps + funding + one L2 snapshot) and **Tohshi-memo/HyperLiquid-Bot-test** (GitHub Actions cron writing
  15-minute mid-price snapshots of every asset incl. xyz since 2026-05-01; rolling file, full history recoverable
  from 14,402 commits). Also **buggatidealership/Freedom** fixtures = real `perpDexs`, `meta`, `metaAndAssetCtxs` (xyz)
  responses (specs, OI caps, impact prices) and sample xyz:NVDA candles.
* Decision: primary stock-linked history = Tohshi-memo sampled mids (exploratory-grade); BTC/ETH = hyperdata real
  candles. Both labelled accordingly everywhere. No synthetic extension, no forward-fill.

## 07:56–08:05 Reconstruction and audit
* Walked the commit history (29 blobs) → 12,144 observations, 129 symbols, 2026-05-01→2026-09-25. Gaps > 60 min: 23
  (four multi-day gaps in May/June). Jitter 0.3–16 min between slot and observation; all alignment on the
  observation time. Daily liquidity snapshots: 130 days (T-LIQ). BTC/ETH candle audit clean (0 gaps/dups).
* Protocol frozen BEFORE any strategy test: `docs/PROTOCOL_FREEZE.md` (splits 50/30/20 by trading day, walk-forward
  windows of 10 days, ≤ 60 configs, gates, cost scenarios, concurrent-signal rule).

## 08:05–08:20 Market selection (before strategy results were seen)
* Ranked xyz US-stock markets on 30-day median 24h volume, p10 volume, 90-day median, OI, impact half-spread
  (single snapshot), in-session data continuity (≥ 60 days) → **SNDK, MU, NVDA, META, GOOGL**. CRCL/INTC/TSLA/AAPL
  rank next (CRCL/INTC lose on spread; TSLA/AAPL on volume). Korean names (SKHX, SMSN, SKHY) excluded: KRX session.
  Selection bias acknowledged: today's most liquid names are backtested retrospectively.

## 08:20–08:35 Engine + tests
* Engine conventions: decision at bar close, fill at next observation / next bar open, taker costs, funding hourly,
  single position, $1 planned risk incl. costs, 2x gross leverage cap, szDecimals rounding, $10 minimum, session
  flat exit (decision ≥ 45 min before close for sampled data, fill ≤ 15 min later), latest entry 90 min before close,
  $10 drawdown pause with shadow account. Six tests pass (future-information perturbation, completed-1h alignment,
  calendar/DST/early close, sizing/rounding, stop-vs-target/gap/costs/funding, single position + pause).
* Smoke run (NVDA/TSLA/META, not the selected universe, not recorded as trials): engine behaves as designed; session
  exits land at 15:37–15:53 ET; stop rejections when the stop rounds onto the wrong side are logged as rejections.

## 08:40 Experiments launched (stocks: 37 configs × 3 regimes; crypto15 37 × 2; crypto1h 12 × 2; crypto247 4 × 2)
