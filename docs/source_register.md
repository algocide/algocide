# Source and data register

All retrieval on 2026-09-24 (UTC). "via search snippet" = content summary returned by the search tool for that URL
(the page itself could not be fetched from this sandbox).

## Datasets used in experiments

| Source | URL / commit | Coverage | Granularity | Provenance | Limitations |
|---|---|---|---|---|---|
| perp-basis (michaelaviv) | https://github.com/michaelaviv/perp-basis @ `a40f37e` (2026-09-24) | 2026-04-30 → 2026-09-24 | ~5-min GitHub-Actions snapshots; 8 series | Collector hits Binance USDT-M, OKX swap, Hyperliquid `metaAndAssetCtxs`/`l2Book`, Yahoo (CME `GC=F`, `CL=F`); Parquet committed per run and compacted daily | Cron jitter: dense May–Jul (≈300/day), sparse Aug–Sep (≈50/day); Binance missing 16 days; CME rows are 15-min-delayed Yahoo quotes with `data_age_sec`; top-of-book only (no depth); funding fields are the venues' current rates |
| hyperliquid-rwa-dashboard (MiggoyGHP) | https://github.com/MiggoyGHP/hyperliquid-rwa-dashboard @ `0a7c1fd` (2026-09-24) | funding: 2023-05-12 → 2026-09-23; stock OHLC: 2024-09 → 2026-09-23; OI: 2026-08-14 → 2026-09-23 | hourly settled funding (`r`) and premium (`p`) per market from `fundingHistory`; daily stock candles (Yahoo); daily OI | Upstream `scripts/refresh_funding.py` paginates `fundingHistory`, dedupes, snaps to the settlement grid and audits gaps (`data/funding/health.json`: status ok, 0 duplicates, 0 off-grid) | `main` dex only BTC/ETH/HYPE; 8h→1h cadence change on 2023-06-08 handled in code; coin membership is as of the last refresh (delisted markets could be absent — see review); premium is the funding-formula premium (average over the hour), not the executable quote |
| hyperliquid-research (CozanetHQ) | https://github.com/CozanetHQ/hyperliquid-research | 2026-09-10 → 2026-09-24 | 10-min, top-30 perps by OI | GitHub Actions snapshots of `metaAndAssetCtxs` | Two weeks only; used for the provenance cross-check (E0) |
| funding-scout (hazwop) | https://github.com/hazwop/funding-scout | 2026-09-14 → 2026-09-24 | 4-hourly, all perps | GitHub Actions snapshots | Ten days only; cross-check (E0) |
| hyperliquid-dex/historical_data (official) | https://github.com/hyperliquid-dex/historical_data | Feb–May 2023 | trades, liquidations, ledger (CSV) | Official, closed-alpha era | Not representative of 2025–26 markets; not used |

## Official / primary sources consulted (mechanics)

See the "Source register" section of `docs/venue_verification.md` (44 URLs on hyperliquid.gitbook.io, docs.trade.xyz,
GitHub hyperliquid-dex/hyperliquid-python-sdk @ `2fdb18f`). Key pages: Fees, Funding, Contract specifications,
Margin tiers, Liquidations, Auto-deleveraging, Rate limits, Info endpoint, Historical data, HIP-3; trade.xyz Oracle
Price, Discovery Bounds, Commodities, US stocks, Fees, Open Interest Caps, Roll Schedules.

## Sources considered and not used

| Source | Why not |
|---|---|
| `s3://hyperliquid-archive`, `s3://hl-mainnet-node-data` (official) | Requester-pays; needs an AWS account and incurs egress charges (not allowed) |
| `s3://hydromancer-reservoir` (Hydromancer Reservoir, ap-northeast-1) | Same: anonymous listing refused ("Anonymous users cannot invoke requests against Requester Pays buckets"); would be the best source for a funded follow-up (fills, 1-s candles, daily positions, 1-min L2 for all dexes) |
| Hyperliquid REST/WS, Binance, Bybit, OKX, Coinbase, Kraken APIs | Blocked by network policy |
| Yahoo Finance, FRED, Cboe, archive.org, Kaggle, Hugging Face, Dune, DefiLlama | Blocked by network policy |
| Bigdata.com connector | Financial news/filings, pay-as-you-go balance belongs to the user; not a market-data source for this task; not used |
