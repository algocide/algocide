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

## 08:55–09:05 Crypto results (dev + validation only; holdout sealed) and a definition bug
* BTC/ETH, US session, real candles: nothing passes the screening gates. crypto15 (37 sessions only): a few configs show
  validation PF > 1.3 on 2–12 trades (noise); crypto1h (142 sessions): the best validation PF is 1.39 on 14 trades
  (vol-compression 1h) with a negative development period; everything else is negative on validation. crypto247 (1h,
  24/7, distinct experiment): all four trend/channel configs lose on validation (−3 to −26 $ on $100) and trigger
  the $10 pause. Baseline session-long ≈ 0.
* Bug found and fixed as a NEW trial family: the MA "strength filter" (|fast−slow| > 0.5 ATR on the crossing bar)
  can never fire because the spread is ≈ 0 at a cross → 0 trades in every universe. Redefined as a "confirmed cross"
  (first bar after a cross within 5 bars where |fast−slow| ≥ 0.5 ATR), tagged F1b and rerun (counts against budget).
* Trials so far: 37 stock configs + 37 crypto15 + 12 crypto1h + 4 crypto247 share the same 37 definitions, i.e.
  **37 distinct configurations + 1 baseline** before F1b (4 redefined configs) = 41 distinct.

## 09:05 Calibration: sampled mids vs real candles (methodological check, not a trial)
* Same 8 configurations, BTC/ETH, same window (2026-05-26..07-17, US session), base costs: trade counts differ by
  10–20%; average modelled stop loss is −1.0 R on candles but −1.1 to −1.9 R on sampled data (stop fills at the next
  sample beyond the stop); net P&L flips sign in 3 of 8 configs (vol-comp 1h +1.1 vs −1.4; channel 15m −8.3 vs +1.1;
  pullback +2.1 vs −9.9). Conclusion: at these sample sizes the sampled-mid results carry idiosyncratic error of
  several dollars per config; they can screen out losers but cannot certify a winner.
* xyz:NVDA, 2026-08-24..29 (120 hours with real 1h candles from the Freedom fixture): the range of the four 15-min
  samples is 55% of the true 1h range (median); the last sample differs from the candle close by 5.9 bps (median),
  23 bps (p90). Figure: results/figures/audit_nvda_sampled_vs_candles.png.

## 09:15–09:30 Stock results (dev + validation only), sampled-stop stress, and two process bugs
* Stock universe (SNDK, MU, NVDA, META, GOOGL; 92 sessions: dev 46 / val 27 / holdout 19 sealed). Mean-reversion (F2),
  trend-pullback (F5) and opening-range (F6) families lose in both periods under base costs; the session-long
  baseline is +2.7 dev / −0.8 val. Positive in both periods: channel breakout 15m (n=12: dev +20.0 PF 1.56, val +16.3
  PF 2.06; n=24: dev +13.0, val +4.4), channel 1h n=24 (small), MA 1h (10/40) (small, +1.2/+1.2), vol-compression 1h
  pct=0.1 (13 validation trades). F1b (confirmed cross 15m 10/40): dev +23.2 (PF 3.6) but val −0.2 → rejected as
  development-only.
* Sampled-stop stress (execution-model variant, not a new configuration): the stop-proximity factor calibrated on
  BTC/ETH (0.75 ATR by the predeclared stop-count rule; 0.5 ATR matches candle P&L best) turns channel 15m n=12 into
  dev −22 / val +4.6 (f=0.5) and dev −32 / val −13.7 (f=0.75). The 1h channel n=24 is nearly unaffected (+3.2/+0.9).
  Interpretation: the 15m breakout's edge on sampled data is mostly "stops that were never seen"; it cannot be
  certified from this data.
* Process bug 1 (fixed): the MA strength filter as first defined could never fire (see 08:55 entry).
* Process bug 2 (fixed): appending re-runs to experiments.csv replaced rows by configuration name only and dropped the
  channel family's base/adverse/standard-fee rows for stocks; the rows were regenerated from identical deterministic
  runs (the trade logs were never lost). Append now keys on (configuration, regime).

## 09:35 Candidate selection, robustness, and the ONE holdout look
* Screening gates on validation (base costs): only `F4:channel_bo(atr_mult=1.5, n=12, tf=15m)` passes all five
  (PF 2.06 ≥ 1.3; adverse expectancy +$0.40; 2/3 windows; 37 trades; positive without the best trade; 3/5 markets
  positive). Selected as the single primary candidate; rules frozen in STRATEGY_SPEC.md before opening the holdout.
* Robustness (4 new trials, dev+val): n=8 → +19.9/+14.7; n=16 → +13.7/+3.4; atr 1.0 → −5.2/+22.2; atr 2.0 → +11.0/+1.5.
  Neighbours mostly positive but with large swings → "unstable in magnitude".
* HOLDOUT (last 19 sessions, 2026-08-31 → 09-25, opened once, marker results/stocks/HOLDOUT_OPENED.json):
  base n=30, net −$5.74, PF 0.69, win 43%, 0/2 windows, 95% CI on expectancy [−0.63, +0.31]; adverse −$6.60;
  standard fees −$8.14; sampled-stop stress −$4.70 (f=0.5) / −$9.87 (f=0.75). Long −$1.95, short −$3.79.
  By market: GOOGL +1.7, NVDA −1.2, MU −2.0, SNDK −4.2 (META: no holdout trade). **Rejected.** No second candidate is
  evaluated on the holdout (protocol). Final classification for this run: no demonstrated edge.
* Budget: 45 distinct strategy configurations used (37 predeclared + 4 F1b redefinitions + 4 robustness neighbours)
  of 60; cost/execution regimes and universes are variants of the same configurations, listed as separate rows in
  EXPERIMENTS.csv for transparency.

## Phase 2 (same day, later): "review these videos and build a profitable automated trading system"
* Inputs: https://www.youtube.com/watch?v=aI34O-ZA0VY and https://www.youtube.com/watch?v=ZN0gkZw-2ks. YouTube, every
  transcript/summary mirror, the Internet Archive and the creator's site (allabtai.com) are egress-blocked in this
  sandbox; the Hyperliquid API is still blocked. Search results identify the second video as "Agentic AI Trading For
  Beginners: A New Money Making Era Is Here" (All About AI, Kristian Fagerlie, 2026-06-04): an LLM agent (Codex 5.5 /
  Claude Code with a long-running /goal loop) trading Hyperliquid perps and Polymarket; a sub-agent polls positions
  and market data into a compact JSON digest, a main agent judges P&L against a goal and issues buy/sell/hold with
  TP/SL every heartbeat; indicators computed locally (RSI, EMA, MACD, Bollinger, ATR, volume, OI); small test capital
  (~$200 USDC), hard loss limits, trade logs; the creator's evidence is live experiments with real wallets, not a
  validated edge. The first video's ID is not indexed anywhere reachable: NOT reviewed (said so to the user).
* Reference open-source agents with the same architecture were read (Gajesh2007/ai-trading-agent "Nocturne";
  akshatttt321/trading-agent proposer+verifier with deterministic risk gate, daily-loss halt, drawdown kill switch,
  reward:risk model). Built `research/agent/`: feeds (live API / offline replay), digest, deciders (deterministic
  rule-consensus default; Anthropic LLM decider wired but never called: no key and paid calls are disallowed),
  verifier gate, deterministic risk gate ($1 risk, 2x leverage, 1 position, $3/day loss halt, $10 pause, $20 kill,
  cooldown, max trades/day, US-session rules for stock perps), paper venue (fills, funding, stop-first sequencing),
  key-gated live venue (hyperliquid-python-sdk; reduce-only trigger stops; untested here), heartbeat loop with JSONL
  journal, state file, kill file, watchdog-friendly --once. 5 agent tests pass; replay loop vs engine adapter agree on
  matched trades (60 of 70/74 entries matched, mean |P&L difference| $0.002; the rest are entry-bar stop handling).
* Backtests of the deterministic core (family A, 8 predeclared configs, base+adverse; budget now 53/60):
  BTC/ETH 24/7 1h: best dev +6.6 / val +4.2 (PF 1.11/1.12, 4/7 windows, adverse +2.2) — fails the PF ≥ 1.3 gate;
  shorts carried it (+9.1 vs −4.9 longs). BTC/ETH US session: all negative. crypto15: all negative on validation.
  Stocks (sampled mids): two configs pass the validation gates (+4.9, PF 1.6/1.45) but lose in development
  (−6.1/−13.8): inconsistent, and the stock holdout is already spent. Decision: no holdout opened for family A;
  nothing certified. The loop's default config is the most consistent one (crypto 24/7, threshold 2, stop 2 ATR,
  target 4 ATR) and is labelled unproven.

## Phase 2b: "try to watch both videos"
* YouTube pages, media and captions, every transcript mirror, archive.org and the creators' sites are egress-blocked;
  `youtubei.googleapis.com` (InnerTube) is reachable. Its `next` endpoint returned titles, channels, dates, full
  descriptions with link targets, chapters and top comments for both videos and the follow-ups; `player` answered
  "Sign in to confirm you're not a bot" and `get_transcript` "precondition failed" for every client context tried
  (WEB, MWEB, ANDROID, TVHTML5, embedded, with and without visitor id / API key). No transcript was obtained.
* Video 1 identified: Torin, "How to Actually BUILD a CLAUDE TRADING BOT (10 Minutes)" (2026-04-30, 73.8K views):
  Claude Code writes a Hyperliquid testnet bot; follow-up 24-hour four-LLM race. Video 2's repository
  (`AllAboutAI-YT/agentic-ai-trading-for-beginners`) contains only a wiring guide; its follow-ups describe a
  two-tier Codex heartbeat (Hyperliquid) and a fair-value/market-making strategy (Polymarket). Evidence saved in
  `docs/VIDEO_REVIEW.md` and `docs/video_review/`.
* Added from the videos' safe workflow: `agent/cli.py` (gate/preflight/flatten), testnet-first live venue with the
  mainnet gate (USE_TESTNET=false + CONFIRM_MAINNET=true + --acknowledge-risk), test `test_network_gate`.
* No change to the research conclusion: neither video supplies a strategy with evidence; the deterministic core of
  the agentic approach shows no demonstrated edge; the LLM decider is untested and can only be judged prospectively.
