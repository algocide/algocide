# The AI market analyst (phase 4): what it is and how to run it

Built from the article digested in `docs/PHASE4_ARTICLE_DIGEST.md`; evaluation in `docs/PHASE4_REPORT.md`; the
predeclared rules in `docs/PROTOCOL_PHASE4.md`. It is an analyst, not a trading system: it writes what changed, where
the fact came from, and what contradicts it. No scores, no buy/sell language, no order code.

## Layout

| path | role |
|---|---|
| `analyst/config.py` | defaults; override with `--config my.json`; secrets only via env vars (`ANTHROPIC_API_KEY`, `SEC_USER_AGENT`, `HL_ACCOUNT_ADDRESS`) |
| `analyst/universe.py` | the 59 xyz-listed stocks with >= 200 sessions in the panel |
| `analyst/warehouse.py` | parquet tables under `data/warehouse/` (gitignored, rebuilt from committed data on first use) |
| `analyst/sources/hyperliquid.py` | read-only `info` client: daily candles, positions (for portfolio math) |
| `analyst/sources/edgar.py` | SEC EDGAR client and parsers; ingest functions for CIKs, filings index, Form 4, 13F, XBRL, filing text |
| `analyst/signals.py` | the article's formulas (layer 2) |
| `analyst/composite.py` | the logistic composite, fit and evaluation |
| `analyst/funnel.py` | stage budgets, ranking, cost estimate |
| `analyst/llm/` | provider interface (stub / Anthropic), prompts, deep read, contradiction engine |
| `analyst/portfolio.py` | contribution, HHI, correlation, PCA share, flags |
| `analyst/report.py` | radar renderer and language check |
| `analyst/pipeline.py`, `analyst/cli.py` | orchestration and commands |
| `results/phase4/` | `backtest.json`, `event_study.csv`, `events_first_trigger.csv`, `radar_<date>.md/.json`, `scan_<date>.json`, `positions_example.csv` |

## Run it here (offline, stub LLM)

```bash
cd research
python3 -m analyst.cli status                                   # warehouse tables and row counts
python3 -m analyst.cli backtest                                 # event study + composite, dev/val, holdout untouched
python3 -m analyst.cli run --date 2026-09-23 --positions results/phase4/positions_example.csv
python3 -m pytest tests/test_analyst.py -q
```

## Run it on your machine (free data, your key)

1. Prices: `python3 -m analyst.cli ingest --source hyperliquid` appends completed daily candles for the 59 xyz markets
   from `api.hyperliquid.xyz/info` (read-only). Or keep the committed Yahoo panel.
2. Filings: `export SEC_USER_AGENT="Your Name you@example.com"` (SEC requires a contact string), then
   `python3 -m analyst.cli ingest --source edgar --what all`. Order: CIKs, filings index (8-K, 10-Q, 10-K, 6-K, 20-F,
   Form 4, 13F-HR), Form 4 transactions (last 365 days), 13F holdings for the filers you list in your config
   (`"edgar": {"filers": [CIK, ...]}`; an empty list skips 13F), XBRL quarterly facts, MD&A and press-release text for
   the last eight quarters. The client stays under 8 requests per second. Errors go to `results/phase4/ingest_errors.json`.
3. Optional: `data/warehouse/catalyst_calendar.parquet` (symbol, event_date, kind, source, note) for known upcoming
   dates; `filer_weights` are computed by `signals.filer_accuracy` once 13F history exists.
4. Report with the model: `export ANTHROPIC_API_KEY=...` then
   `python3 -m analyst.cli run --date YYYY-MM-DD --live-llm [--positions positions.csv | --address 0x...]`.
   `--address` reads your Hyperliquid positions through `clearinghouseState` (read-only, no key). Budgets are in
   `funnel` (default 5 deep reads, 3 contradiction runs per day); set `llm.deep_read_model`, `llm.reasoning_model` and
   `llm.prices_per_million` in your config to get a cost line. Every call is logged to the radar JSON with token counts.
5. Read `results/phase4/radar_<date>.md`. If the language check fails, the command exits 2 and the file says why.

## What the report contains and why

- Header counts (assets monitored, anomalies, high-information events, portfolio risks, upcoming catalysts) and a data
  line naming the price source and which filings tables are loaded.
- Per name: 5-session and last-session return, volume versus its 20-session average, z-scores and anomaly score, the
  institutional signal with its period end and filing age, insider purchases and sales with the last filing date,
  the deep read's lowest management-versus-XBRL consistency, the catalyst with its 8-K filing date, the sequence read
  (did the institutional change predate the move; was the filing public before it), the arbiter's top contradiction with
  the checked bull and bear strengths and the thesis gap, and what would change the read.
- Portfolio: contribution per position, HHI against the equal-weight reference, average pairwise correlation and the
  first principal component's share, flags.
- Priority is categorical from a fixed rule (high = anomaly_score > 3 with a corroborating source; medium = > 2).

## Guarantees

- Read-only: no exchange endpoint, no signing, no transfers, no settings; the tests grep the client for it.
- No paid call without `--live-llm` and a key in the environment; no key is written anywhere.
- consistency and thesis_gap are computed in code from numbers the model extracts and from XBRL; model output is a
  claim to check, never data.
- The evaluation splits are phase 3's; the holdout has not been used in phase 4.
