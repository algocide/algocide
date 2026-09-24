# Hyperliquid edge research (session 2026-09-24)

Skeptical, reproducible search for economically meaningful trading edges executable on Hyperliquid, produced
autonomously in a sandboxed cloud session. **Start with `docs/decision_memo.md`.**

**Bottom line:** no credible, economically meaningful edge was found. The only candidate passing its own pre-registered
bar is the HYPE perp-vs-spot funding carry (≈7% net on capital in 2026 at 4x on the perp leg, ≈+3 points over T-bills), a
known carry rather than an alpha. The premium-reversion idea on trade.xyz equity perps is statistically real but not
capturable at standard HIP-3 fees; it is worth a conditional forward paper test only if growth-mode fees are confirmed.

## What is in here

| Path | Content |
|---|---|
| `docs/decision_memo.md` | Decision: what was found, evidence strength, next action |
| `docs/hypothesis_comparison.md` | All 14 hypotheses, status and reasons (including rejected / untestable) |
| `docs/finalists.md` | Detailed specs, results, failure modes and preconditions for the three finalists |
| `docs/hypotheses_and_preregistration.md` | Pre-registration written and committed before any backtest ran |
| `docs/research_ledger.md` | Every run, bug fix, methodological change and validation "look" |
| `docs/venue_verification.md` | Hyperliquid + trade.xyz mechanics with citations (via search snippets and the official SDK) |
| `docs/source_register.md` | Data sources: URLs, retrieval dates, coverage, granularity, provenance, limitations |
| `docs/review_adversarial.md` | Independent reviewer's attack on the finalists (17 objections; scripts `experiments/review_*.py`, outputs `results/review/`) |
| `docs/forward_plan.md` | Forward paper-trading plan: sizing, monitoring, kill switches, pre-declared pass/fail |
| `data/derived/` | Canonical datasets (Parquet) built from public GitHub snapshot repos, with `provenance.json` |
| `src/hlr/` | Package: data build, cost model, dependence-aware stats, API client (untested live) |
| `experiments/` | Reproducible experiment scripts (E0–E5) and the API-dependent pipeline (E8–E10, not run) |
| `results/` | Outputs of every experiment that ran (JSON, CSV, Markdown, PNG) |
| `forward/` | Data collector, paper trader (dry-run only, no keys), evaluator |
| `tests/` | Synthetic-data unit tests for the API client and paper trader |

## Reproduce

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
# 1) rebuild datasets from the upstream public repos (pinned commits in data/derived/provenance.json)
git clone --depth 1 https://github.com/michaelaviv/perp-basis /tmp/perp-basis
git clone --depth 1 https://github.com/MiggoyGHP/hyperliquid-rwa-dashboard /tmp/rwa
#   (to reproduce EXACTLY, check out the commits listed in provenance.json)
PYTHONPATH=src python3 -m hlr.data --perp-basis /tmp/perp-basis --rwa /tmp/rwa
# 2) experiments (each writes results/<name>/)
PYTHONPATH=src python3 experiments/e0_provenance_crosscheck.py     # needs the two small snapshot repos (see script)
PYTHONPATH=src python3 experiments/e1_xvenue_dislocation.py        # H1, H2, H3 on the 5-min gold/WTI tape
PYTHONPATH=src python3 experiments/e4_funding_carry.py             # H4, H5, H6, H7, H11, H14 on hourly funding
PYTHONPATH=src python3 experiments/e4b_h7_liquid_subset.py         # post-hoc liquid-subset and realistic-timing H7
PYTHONPATH=src python3 experiments/e5_robustness_h4.py             # robustness battery for H4
python3 tests/test_hl_api.py && python3 tests/test_paper_trader.py
```

The derived Parquet files are committed, so steps 2+ run without any network access.

## Environment limits that shaped this work (facts)

* The session's network policy blocked `api.hyperliquid.xyz`, the Hyperliquid and trade.xyz docs, every CEX API and
  every data archive tried. Only GitHub, PyPI and npm were reachable. Consequently: no candles, order books,
  liquidation or wallet data could be downloaded; the empirical work uses real observations that other people
  committed to public GitHub repositories (see `docs/source_register.md`).
* Dollar credit usage is not observable from inside the session; the budget was managed as a bounded work plan.
* Nothing was purchased, no keys or wallets were used, no orders were sent.
