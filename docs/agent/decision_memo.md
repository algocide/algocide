# Decision memo: the 24/7 agent prompt, run against this repository (2026-09-26)

## What was asked, and what was actually possible

A widely shared prompt asks for a complete 24/7 autonomous crypto trading agent: deep research (the BRAIN), a
typed per-candle decision engine (Jev, from TypeSafe AI), a deterministic state engine, a hard risk layer, an
overnight self-improvement loop, and a build run through AgenKit. The post carrying it says it "will probably make
you a lot of money."

Facts about this run:

* **This repository already contains the honest version of the research layer.** Two days earlier (2026-09-24) a
  pre-registered, adversarially reviewed search for edges on Hyperliquid concluded: no credible, economically
  meaningful edge; one modest carry (HYPE perp/spot funding) passed its own bar; one reversion effect is real but
  not capturable at standard fees. Nothing found today overturns that. See `docs/decision_memo.md` (2026-09-24).
* **AgenKit and Jev could not be reached.** agenkit.xyz, typesafe.ai, console.typesafe.ai and every third-party
  write-up of Jev are blocked by this session's network policy. AgenKit is a third-party prompt and agent pack for
  Claude Code; installing one unvetted into the coding agent would be a supply-chain risk even if reachable, so its
  six-phase discipline was followed by hand (`docs/agent/build_plan.md`). Jev is real (released around 2026-09-19,
  early access behind a waitlist, one endpoint `POST https://api.typesafe.ai/v1/systemone`); its wire format here
  is inferred from search snippets and the adapter fails closed until the contract is verified with a key.
* **Market data could not be pulled live** (`api.hyperliquid.xyz` and every exchange API are blocked, as on
  2026-09-24). The regime call rests on the Bigdata.com market tearsheet and dated news; the venue numbers are
  snippets. All of it is cited in `docs/agent/research_2026-09-26.md`.
* **Nothing was bought, no key was used, no order was sent.** The live executor is disarmed by four independent
  conditions and was never constructed.

## What was built

`src/hlagent/` (tested; 78 tests across the new and the pre-existing suites pass with `python3 -m pytest`):

| Piece | What it guarantees |
|---|---|
| `schema.py` | Five typed questions (regime, direction, toxic_flow, setup_quality 0-3, risk_state) and a frozen, extra-forbidding `Decision`; the state prompt is capped at 1000 characters (about 333 tokens) |
| `state_engine.py` | Pure, deterministic features (mid, spread, imbalance, depth, returns, realised vol, range position, funding, premium, OI, inventory, drawdown); any input stamped after the decision time raises; a forming candle is never used |
| `policy.py` | The gate (quality >= 2, confidence > 0.80, judge AND code risk state safe, no toxic flow, spread and staleness caps, setup valid) and quarter-Kelly-capped sizing, capped again at 10% of equity |
| `risk.py` | Kill switch file checked before every order and at the top of every tick; 15% max drawdown and 3% daily loss engage it; per-market and gross caps clip; staleness and consecutive errors block; the model has no path to any of it |
| `judge.py` | `JevJudge` (fail-closed HTTP adapter, 250 ms timeout), `RuleJudge` (transparent baseline whose uncalibrated confidence is capped below the gate until measured), `ReplayJudge` |
| `execution.py` | Paper executor with touch-plus-impact fills, taker fees, hourly funding; live executor that refuses to exist without `HLAGENT_LIVE=1`, an `ARMED` file, a key, a positive notional cap, and no `KILLED` file |
| `loop.py` | One pass per candle: feed -> state -> judge -> gate -> risk -> execute -> logs; every decision (fired or not) is logged and scored at its horizon; escalations run on a worker thread with "hold" as the default |
| `brain.py` | Escalation (hold / flatten / resume only) and nightly proposals through the Anthropic SDK with structured output; rate-limited; a refusal or error means hold |
| `review.py` | Brier score, directional reliability table, hit rate, P&L after fees; proposals limited to tunable policy keys and applied only with `--approve`; synthetic runs can never recommend arming live |

Two offline dry runs (`--mode synthetic`, no network) exercise the whole path; their reviews are in
`results/agent_review/2026-09-26-dryrun-synthetic.md` (capped baseline: 1,200 decisions, no fills, negative Brier
skill on a random walk as expected) and `results/agent_review/2026-09-26-dryrun-synthetic-fills.md` (uncapped
baseline, allowed in synthetic mode only: 3,000 decisions, 142 fills, net -49.77 USD after 63.70 USD of fees, which
is what an uninformative judge should produce). Both are labelled synthetic on every line and neither can
recommend arming live.

## The final check the prompt asks for

**Is the edge organic or incentive driven?** The HYPE carry is compensation for supplying leverage to speculative
longs whose demand is partly incentive-driven (treasury companies raising money to buy the token, listing hype).
The xyz premium reversion is mechanical (funding and oracle construction) and its viability is a fee subsidy
(growth mode) that the deployer has proposed to withdraw. The BTC judge setup claims no edge at all.

**Is the catalyst already priced in?** ENA's 2026-10-05 unlock has been in SEC filings since 2026-08-31; UNI's
2026-10-19 CME listing produced a +111% month and an 11.5% one-day drop before the event; HYPE's Binance listing
happened on 2026-09-24, the day after its all-time high. Each is at best partly unpriced; none is a clean setup.

**Does value accrue to the token?** HYPE: yes, directly (99% of protocol revenue to buybacks), against an unlock
schedule that triples supply over time. For ENA and UNI no accrual claim is made here.

**Will the strategy survive costs and slippage?** The carry does while funding stays above the T-bill rate. The
reversion does only under growth fees with half-spreads at or below 3-5 bps, unobserved. A calibrated judge on
1-minute candles faces a 9 bps round trip plus spread; the gate is the only defence and the review measures whether
it is enough. Paper fills at the touch plus 0.5 bps impact understate real slippage.

**Is any hard limit delegated to the model?** No. Limits live in `RiskLimits`; the judge's `risk_state` can only add
caution; sizing is capped in code below the model's probability; the BRAIN can only choose hold, flatten or resume;
overnight proposals touch tunable policy keys only and require an explicit approval flag.

## Verdict on the claim

Running this prompt does not produce a system that "will probably make you a lot of money." It produces a decent
skeleton for measuring whether a fast typed judge has any calibrated skill, wrapped in limits that make the cost of
finding out bounded. The research layer's honest output is the same as two days ago: one modest carry worth an
operations test, one conditional paper test, and a watch list. The prompt's own final-check questions, applied to
its own premises, argue against expecting profit from it.

## WHAT COULD I BE WRONG ABOUT?

1. **The regime call.** I read the rally as flow-driven and fragile into a hiking Fed. Bitwise and Fundstrat read it
   as the start of the largest bull market yet. If they are right, the "fragile" framing under-weights ETF demand and
   the crisis trigger (BTC below $76-78k with outflows) never fires; the cost of being wrong is missed upside, not
   loss, because the loop trades both directions and sizes small.
2. **The Jev contract.** The request and response shapes are inferred. A subtly wrong mapping (for example reading a
   regime probability as the direction's probability) would pass the parser and mis-calibrate every decision.
   Mitigation: the adapter refuses to invent a confidence and the reliability table would expose it within a day;
   still, verify the contract against the vendor docs before trusting a single fill.
3. **"Calibrated" is a vendor claim** on the vendor's benchmark, not on this state vector or this market. The review
   measures calibration, but 200 resolved decisions is a low bar and the horizon (12 one-minute candles) may be the
   wrong one for whatever signal exists.
4. **The state vector may omit what matters.** No trade tape, no liquidation feed, no cross-venue price, no
   order-flow toxicity measure beyond spread and imbalance. A 400-token cap forces omissions; the omitted field might
   be the one with information.
5. **Outcome labels are cost-blind.** A decision is "right" if the horizon close is above the entry mid; a right
   decision can still lose after 9 bps of costs. Net P&L is reported separately, but the calibration table alone
   could look good while the book bleeds.
6. **The payoff ratio is assumed at 1.0** until measured; Kelly with the wrong ratio over-sizes. The 10% cap bounds
   the damage but does not make the size right.
7. **Growth mode for trade.xyz is a search snippet.** If it is off for the liquid names, the reversion finalist is
   dead at standard fees, exactly as on 2026-09-24. If the proposed 3x fee rise lands, it is dead either way.
8. **HYPE funding is a 2026-09-04 number.** The Binance listing makes cross-venue arbitrage easier and may have
   compressed the carry since; the paper run exists to find out.
9. **The rule baseline is not fitted** and may be systematically wrong. It is a placeholder to make the plumbing run
   without a key, capped so it cannot fire on its own uncalibrated confidence.
10. **Narrative research is read after the moves.** Every story in the research doc explains the past month; none
    of it is shown to predict the next one. The pre-registration discipline of the earlier session is the guard, and
    it has not yet been applied to a single one of today's candidates.
11. **The BRAIN model.** The prompt assumes Claude Opus 5.5; the default model id here is `claude-opus-5-5` and may
    not be enabled on the account, in which case escalations fall back to "hold" until `HLAGENT_BRAIN_MODEL` is set.
    A fallback to hold is safe but silently disables the deep re-read.
12. **Operational fragility.** One process, one thread for market work, file-based kill switch, no persistence of
    peak or day-start equity for a live account across restarts (the live executor reports current equity for both,
    which under-counts drawdown after a restart). Fix before any live run.
13. **The prior session's conclusion may itself be wrong** in the direction of being too pessimistic: it could only
    see two GitHub datasets. Live order-book data could revive H7, or reveal edges (liquidation reversal, H9) it
    could not test. That is an argument for the collector and the paper run, not for capital.
