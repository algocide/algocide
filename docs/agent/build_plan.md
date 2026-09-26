# Build plan: BRAIN / REFLEX trading agent (six phases, approval gate at each)

The prompt that started this work asks for the build to run through AgenKit (agenkit.xyz), a third-party
specialist-agent and skill pack for Claude Code. That site, and every write-up of it, is blocked by this session's
network policy, and installing an unvetted prompt pack into the coding agent is a supply-chain risk in any case. The
six-phase discipline it describes (spec, architecture, plan, test-first build, review, ship, each behind an approval
gate) was followed by hand instead. Below, each phase lists exact files, what was done in this session, and what the
operator must approve before the next phase runs for real.

## Phase 1: spec (done; operator approves the scope)

* Mission: a 24/7 agent on Hyperliquid perps that researches (slow), judges each candle (fast, typed), gates and
  sizes in code, executes on paper by default, and reviews itself nightly.
* Non-goals written down: no claim of an edge (the repository's 2026-09-24 conclusion stands); no live capital until
  the pre-declared forward criteria in `docs/forward_plan.md` and `src/hlagent/review.py` are met.
* Files: `docs/agent/research_2026-09-26.md` (regime, candidates), `config/setups_2026-09-26.json` (typed specs),
  `docs/agent/decision_memo.md` (final check and what could be wrong).
* Gate: the operator confirms the coin list and the two paper setups (`hype_carry`, `btc_regime_judge`).

## Phase 2: architecture (done)

Two layers, never blurred:

| Layer | Owns | Files |
|---|---|---|
| BRAIN (slow) | research, schema derivation, escalations, nightly review | `docs/agent/*.md`, `src/hlagent/brain.py`, `src/hlagent/review.py` |
| REFLEX (fast) | one typed decision per candle on a deterministic state | `src/hlagent/state_engine.py`, `src/hlagent/judge.py` |
| Code (authoritative) | every threshold, size and side effect | `src/hlagent/policy.py`, `src/hlagent/risk.py`, `src/hlagent/execution.py`, `src/hlagent/loop.py` |

Data path per candle: `datafeed` -> `state_engine.build_state` (causal, deterministic, <= 1000 chars) ->
`judge.judge` (Jev over HTTP, or the rule baseline) -> `policy.gate` + `policy.target_notional` (quarter-Kelly cap)
-> `risk.check_order` (kill switch, drawdown, daily loss, per-market and gross caps, staleness) -> `executor` (paper
by default; live executor disarmed by four independent conditions) -> JSONL logs -> outcomes at the horizon.

Gate: the operator confirms the risk limits in `config/risk.json` (15% max drawdown, 3% daily loss, 10% per
market, 30% gross, 5 s staleness, 5 consecutive errors) and the policy in `config/policy.json`.

## Phase 3: plan (done)

| Step | File(s) | Status |
|---|---|---|
| typed contracts and the five Jev questions | `src/hlagent/schema.py` | done, tested |
| deterministic state engine with causality checks | `src/hlagent/state_engine.py` | done, tested |
| gate and Kelly sizing | `src/hlagent/policy.py` | done, tested |
| hard risk layer with kill switch and escalation trigger | `src/hlagent/risk.py` | done, tested |
| judges: Jev adapter (fail-closed), rule baseline, replay | `src/hlagent/judge.py` | done, tested against a fake server; Jev wire format UNVERIFIED |
| setup book with code-evaluated invalidation | `src/hlagent/setups.py`, `config/setups_2026-09-26.json` | done, tested |
| executors: paper, disarmed live | `src/hlagent/execution.py` | paper tested; live UNTESTED (network) |
| feeds: Hyperliquid read-only, synthetic, replay | `src/hlagent/datafeed.py` | synthetic/replay tested; Hyperliquid UNTESTED (network) |
| reflex loop and CLI | `src/hlagent/loop.py` | done, tested end to end on synthetic data |
| BRAIN escalation and review proposals via the Anthropic SDK | `src/hlagent/brain.py` | done; not exercised (no credentials in session); fails closed |
| overnight review: Brier, reliability, proposals, approval gate | `src/hlagent/review.py` | done, tested |

## Phase 4: test-first build (done in this session)

* Tests: `tests/test_agent_schema.py`, `tests/test_agent_state_engine.py`, `tests/test_agent_policy.py`,
  `tests/test_agent_risk.py`, `tests/test_agent_judge.py`, `tests/test_agent_execution.py`, `tests/test_agent_loop.py`,
  `tests/test_agent_review.py`; run with `python3 -m pytest` (config in `pytest.ini`). The pre-existing research tests
  still pass.
* Offline dry run (no network): `PYTHONPATH=src python3 -m hlagent.loop --mode synthetic --ticks 600 --out data/agent_dryrun`
  then `PYTHONPATH=src python3 -m hlagent.review --out data/agent_dryrun --results results/agent_review`. Every record
  carries `synthetic: true`; the review refuses to recommend arming live on synthetic data.
* Gate: all tests green and the dry run completes without a kill event caused by a bug.

## Phase 5: review (partly done; the rest needs the operator)

* Done here: an adversarial re-read of the diff against the spec's own "final check" questions
  (`docs/agent/decision_memo.md`), and the ledger of what is unverified.
* Operator: read `src/hlagent/execution.py::LiveExecutor` line by line before ever arming it; verify the Jev request
  and response shape against `console.typesafe.ai` and adjust `judge.parse_jev_payload`; re-read the Hyperliquid fee
  and rate-limit pages live and update `src/hlr/costs.py` if they moved.
* Gate: sign-off on the risk layer and the live-executor arming conditions.

## Phase 6: ship (staged; nothing live in this session)

1. **Paper with live data (weeks 1-8).** On any small VM that can reach `api.hyperliquid.xyz`:
   `PYTHONPATH=src python3 -m hlagent.loop --mode paper --coins BTC,HYPE --interval 1m --judge rule --out data/agent`
   (add `--judge jev` once a key is in `TYPESAFE_API_KEY`, and `--brain` once Anthropic credentials are configured).
   Nightly: `python3 -m hlagent.review --out data/agent`; apply proposals only with `--apply <file> --approve`.
   Keep `forward/collector.py` running alongside to record funding, OI and top of book for the finalists.
2. **Pass/fail (pre-declared).** Live capital is considered only if the review shows Brier skill > 0 on at least 200
   real (non-synthetic) resolved decisions AND positive net P&L after fees, AND the finalist-specific criteria in
   `docs/forward_plan.md` hold (HYPE carry: realised net funding >= 6% annualised over 8 weeks with margin logic
   surviving a simulated 30% adverse move). Any one failing means stop.
3. **Live (only after 2).** Testnet first (`--base-url https://api.hyperliquid-testnet.xyz`), then mainnet with
   `HLAGENT_LIVE=1`, an `ARMED` file in the output directory, an API/agent wallet key in `HL_PRIVATE_KEY`, and
   `--max-live-notional` set to a number you can lose. The kill switch is the file `KILLED` in the output directory;
   creating it flattens and blocks everything; deleting it is the only reset.

## What the operator approves, in order

1. scope and coin list (Phase 1);  2. limits and policy (Phase 2);  3. tests green and dry run (Phase 4);
4. live-executor review and Jev contract check (Phase 5);  5. paper results against the pre-declared criteria, then
testnet, then a small mainnet notional (Phase 6). Nothing in the code advances a stage on its own.
