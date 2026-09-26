# Hyperliquid edge research (session 2026-09-24)

Skeptical, reproducible search for economically meaningful trading edges executable on Hyperliquid, produced
autonomously in a sandboxed cloud session. **Start with `docs/decision_memo.md`.**

**Bottom line:** no credible, economically meaningful edge was found. The only candidate passing its own pre-registered
bar is the HYPE perp-vs-spot funding carry (≈7% net on capital in 2026 at 4x on the perp leg, ≈+3 points over T-bills), a
known carry rather than an alpha. The premium-reversion idea on trade.xyz equity perps is statistically real but not
capturable at standard HIP-3 fees; it is worth a conditional forward paper test only if growth-mode fees are confirmed.

## Session 2026-09-26: the "24/7 AI trading agent" prompt, run against this repository

A widely shared prompt asks for a two-layer autonomous agent (a slow BRAIN for research and review, a fast typed REFLEX
judge on every candle, a deterministic state engine, a hard risk layer, an overnight self-improvement loop). It was
run here in good faith and with the same scepticism as the research above. Read `docs/agent/decision_memo.md` first;
its last section is "WHAT COULD I BE WRONG ABOUT?".

| Path | Content |
|---|---|
| `docs/agent/decision_memo.md` | What was possible, what was built, the prompt's own final check applied, the verdict, what could be wrong |
| `docs/agent/research_2026-09-26.md` | Regime call (trend, flows, leverage, stablecoins, macro, narratives) with dated citations; ten candidate setups graded paper / watch / rejected |
| `docs/agent/build_plan.md` | The six phases (spec, architecture, plan, test-first build, review, ship) with exact files and the approval gate at each |
| `config/setups_2026-09-26.json` | The candidates compiled into typed specs with numeric invalidation rules evaluated in code |
| `config/policy.json`, `config/risk.json` | Every threshold and limit; the model never sets any of them |
| `src/hlagent/` | The agent: `schema`, `state_engine`, `policy`, `risk`, `judge` (Jev adapter, rule baseline, replay), `setups`, `execution` (paper; live disarmed), `datafeed`, `loop`, `brain`, `review` |
| `tests/test_agent_*.py` | 73 tests: causality, determinism, token budget, gate, Kelly cap, kill switch, clipping, fail-closed judge, paper accounting, loop end to end, Brier and proposals |
| `results/agent_review/2026-09-26-dryrun-synthetic*.md` | Reviews of the two offline dry runs on synthetic data (plumbing checks, not evidence) |

```bash
pip install -r requirements.txt
python3 -m pytest                                                              # all suites
PYTHONPATH=src python3 -m hlagent.loop --mode synthetic --ticks 600 --coins BTC,HYPE --out data/agent_dryrun
PYTHONPATH=src python3 -m hlagent.review --out data/agent_dryrun --results results/agent_review
# with network access to api.hyperliquid.xyz (paper fills on live data; no keys):
PYTHONPATH=src python3 -m hlagent.loop --mode paper --coins BTC,HYPE --interval 1m --judge rule --out data/agent
```

**Bottom line of that session:** the agent is a measurement instrument with bounded downside, not a money machine.
Its research layer reached the same conclusion as the 2026-09-24 work: one modest carry worth an operations test,
one conditional paper test, a watch list. AgenKit (the harness the prompt names) and Jev (the judge) were unreachable
from the sandbox; the Jev adapter's wire format is inferred and fails closed, and the six-phase discipline was
followed by hand. Nothing live was run.

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
