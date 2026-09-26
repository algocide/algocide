"""The REFLEX loop: one pass per candle per coin.

    feed -> state_engine (causal, deterministic) -> judge (typed decision) -> gate + Kelly size (policy)
         -> hard risk check (risk) -> executor (paper by default) -> JSONL logs -> outcomes resolved at the horizon

Every decision is logged whether or not it fires (the "shadow" log), and every decision is resolved against the
realised move at its horizon, so calibration (Brier score) accumulates without trading. Records produced on a
synthetic feed carry synthetic=true and are never evidence.

Run:  PYTHONPATH=src python3 -m hlagent.loop --mode synthetic --ticks 600            # offline dry run, no network
      PYTHONPATH=src python3 -m hlagent.loop --mode paper --coins BTC,ETH --judge rule  # live data, paper fills
      (live mode needs HLAGENT_LIVE=1, HL_PRIVATE_KEY, an ARMED file and --max-live-notional; see execution.py)
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from typing import Optional
from .datafeed import INTERVAL_MS, FeedSnapshot, HyperliquidFeed, MultiFeed, SyntheticFeed
from .execution import ExecReport, PaperExecutor
from .judge import JevJudge, RuleJudge
from .policy import PolicyConfig, gate, target_notional
from .risk import RiskLayer, RiskLimits
from .schema import Decision, Direction, RiskState
from .setups import SetupBook
from .state_engine import BookError, CausalityError, build_state


class JsonlLog:
    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)

    def write(self, kind: str, rec: dict) -> None:
        rec = {"ts_wall": dt.datetime.now(dt.timezone.utc).isoformat(), "kind": kind, **rec}
        with open(os.path.join(self.out_dir, f"{kind}.jsonl"), "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")


class ReflexLoop:
    def __init__(self, feed, judge, executor, risk: RiskLayer, policy: PolicyConfig, setups: Optional[SetupBook],
                 out_dir: str, coins: list[str], interval: str = "1m", brain=None,
                 escalation_cooldown_ms: int = 3_600_000, tag: str = "paper", brain_sync: bool = False):
        self.feed, self.judge, self.executor, self.risk, self.policy, self.setups = feed, judge, executor, risk, policy, setups
        self.coins, self.interval_ms, self.brain = coins, INTERVAL_MS[interval], brain
        self.escalation_cooldown_ms = escalation_cooldown_ms
        self.log = JsonlLog(out_dir)
        self.out_dir = out_dir
        self.decision_seq = 0
        self.pending: list[dict] = []
        self.open_targets: dict[str, dict] = {}
        self.paused_until: dict[str, int] = {}
        self.last_escalation: dict[str, int] = {}
        self.recent: dict[str, deque] = {c: deque(maxlen=20) for c in coins}
        # BRAIN calls never block the hot path: they run on one worker thread and land on a later tick.
        self.brain_sync = brain_sync
        self._pool = ThreadPoolExecutor(max_workers=1) if (brain is not None and not brain_sync) else None
        self._pending_verdicts: dict[str, Future] = {}
        with open(os.path.join(out_dir, "run_config.json"), "w") as f:
            json.dump({"started": dt.datetime.now(dt.timezone.utc).isoformat(), "tag": tag, "coins": coins,
                       "interval": interval, "judge": getattr(judge, "name", type(judge).__name__),
                       "executor": type(executor).__name__, "policy": asdict(policy), "risk": asdict(risk.limits),
                       "brain": None if brain is None else getattr(brain, "model", "?")}, f, indent=1)

    # ------------------------------------------------------------------ helpers
    def _paused(self, coin: str, now_ms: int) -> bool:
        return now_ms < self.paused_until.get(coin, -1)

    def _log_fills(self, rep: ExecReport, synthetic: bool) -> None:
        for f in rep.fills:
            self.log.write("fills", {**asdict(f), "synthetic": synthetic})

    def _flatten(self, coin: str, snap: FeedSnapshot, now_ms: int, note: str) -> None:
        rep = self.executor.flatten(coin, snap.l2, now_ms, note=note)
        self._log_fills(rep, snap.synthetic)
        self.open_targets.pop(coin, None)
        self.log.write("events", {"coin": coin, "ts_ms": now_ms, "event": "flatten", "note": note})

    def _apply_verdict(self, coin: str, now_ms: int, state, snap: FeedSnapshot, verdict, trigger: dict) -> str:
        action = verdict.action if verdict is not None else "hold"
        self.log.write("escalations", {"coin": coin, "ts_ms": now_ms, "trigger": trigger, "phase": "verdict",
                                       "verdict": verdict.model_dump() if verdict is not None else None, "action": action})
        if action == "flatten" and state.pos_units != 0:
            self._flatten(coin, snap, now_ms, "brain: flatten")
        elif action == "resume":
            self.paused_until.pop(coin, None)
        return action

    def _escalate(self, coin: str, now_ms: int, state, decision: Decision, snap: FeedSnapshot, setup) -> str:
        """Pause new entries for the market and ask the BRAIN. The default outcome is 'hold'; a verdict either
        applies now (brain_sync, for replays and tests) or on the first tick after it arrives."""
        last = self.last_escalation.get(coin)
        if last is not None and now_ms - last < self.escalation_cooldown_ms:
            return "already_escalated"
        self.last_escalation[coin] = now_ms
        self.paused_until[coin] = now_ms + self.escalation_cooldown_ms
        trigger = {"confidence": decision.confidence, "regime": decision.regime.value}
        if self.brain is None:
            self.log.write("escalations", {"coin": coin, "ts_ms": now_ms, "trigger": trigger, "phase": "no_brain", "action": "hold"})
            return "hold"
        if self.brain_sync:
            try:
                verdict = self.brain.escalate(state, decision, list(self.recent[coin]), setup)
            except Exception as e:
                self.log.write("events", {"coin": coin, "ts_ms": now_ms, "event": "brain_error", "error": repr(e)[:300]})
                verdict = None
            return self._apply_verdict(coin, now_ms, state, snap, verdict, trigger)
        self._pending_verdicts[coin] = self._pool.submit(self.brain.escalate, state, decision, list(self.recent[coin]), setup)
        self.log.write("escalations", {"coin": coin, "ts_ms": now_ms, "trigger": trigger, "phase": "dispatched", "action": "hold"})
        return "hold"

    def _collect_verdict(self, coin: str, now_ms: int, state, snap: FeedSnapshot) -> Optional[str]:
        fut = self._pending_verdicts.get(coin)
        if fut is None or not fut.done():
            return None
        self._pending_verdicts.pop(coin, None)
        try:
            verdict = fut.result()
        except Exception as e:
            self.log.write("events", {"coin": coin, "ts_ms": now_ms, "event": "brain_error", "error": repr(e)[:300]})
            verdict = None
        return self._apply_verdict(coin, now_ms, state, snap, verdict, {"async": True})

    # ------------------------------------------------------------------ one coin, one candle
    def _tick_coin(self, coin: str, now_ms: int) -> dict:
        summary = {"coin": coin, "ts_ms": now_ms, "action": "none"}
        try:
            snap = self.feed.snapshot(coin, now_ms)
        except Exception as e:
            self.risk.record_error()
            self.log.write("events", {"coin": coin, "ts_ms": now_ms, "event": "feed_error", "error": repr(e)[:300]})
            summary["action"] = "feed_error"
            return summary
        self.executor.mark(coin, snap.l2)
        pay = self.executor.accrue_funding(coin, snap.ctx, now_ms)
        if pay:
            self.log.write("funding", {"coin": coin, "ts_ms": now_ms, "usd": pay, "rate": snap.ctx.funding_1h, "synthetic": snap.synthetic})
        acct = self.executor.account(coin, now_ms)
        setup = self.setups.for_coin(coin) if self.setups is not None else None
        try:
            state = build_state(now_ms, coin, snap.l2, snap.candles, snap.ctx, acct, setup_id=setup.id if setup else "")
        except (CausalityError, BookError, ValueError) as e:
            self.risk.record_error()
            self.log.write("events", {"coin": coin, "ts_ms": now_ms, "event": "state_error", "error": repr(e)[:300]})
            summary["action"] = "state_error"
            return summary
        self.risk.record_ok()
        breach = self.risk.enforce_limits(state)
        if breach is not None:
            self.log.write("events", {"coin": coin, "ts_ms": now_ms, "event": "limit_breach", "reason": breach})
        if setup is not None:
            note = self.setups.evaluate(setup, state)
            if note:
                self.log.write("events", {"coin": coin, "ts_ms": now_ms, "event": "setup_invalidated", "setup": setup.id, "note": note})
        setup_invalid = setup is not None and self.setups.is_invalidated(setup.id)
        code_rs = self.risk.code_risk_state(state)

        decision = self.judge.judge(state, setup)
        self.decision_seq += 1
        did = f"{coin}-{now_ms}-{self.decision_seq}"
        horizon_ms = now_ms + self.policy.horizon_candles * self.interval_ms
        rec = {"id": did, "coin": coin, "ts_ms": now_ms, "synthetic": snap.synthetic, "setup_id": setup.id if setup else "",
               "state": state.model_dump(), "decision": decision.model_dump(mode="json"), "p_up": decision.p_up(),
               "mid": state.mid, "horizon_ts_ms": horizon_ms, "code_risk_state": code_rs.value}
        self.log.write("decisions", rec)
        self.pending.append({"id": did, "coin": coin, "ts_ms": now_ms, "mid": state.mid, "p_up": decision.p_up(),
                             "horizon_ts_ms": horizon_ms, "direction": decision.direction.value, "source": decision.source,
                             "regime": decision.regime.value, "confidence": decision.confidence, "synthetic": snap.synthetic})
        self.recent[coin].append({"ts_ms": now_ms, "mid": state.mid, "decision": decision.model_dump(mode="json")})

        killed = self.risk.is_killed()
        landed = self._collect_verdict(coin, now_ms, state, snap)
        if landed is not None:
            summary["verdict"] = landed
            if landed == "flatten":
                summary["action"] = "flatten_brain"
                return summary
        if not killed and self.risk.should_escalate(decision, state):
            summary["escalation"] = self._escalate(coin, now_ms, state, decision, snap, setup)
            if summary["escalation"] == "flatten":
                summary["action"] = "flatten_brain"
                return summary
        g = gate(decision, code_rs, self.policy, state, setup_invalid)

        # exits first
        if state.pos_units != 0:
            if killed or code_rs == RiskState.reduce:
                self._flatten(coin, snap, now_ms, "kill switch" if killed else "risk: reduce")
                summary["action"] = "flatten_risk"
                return summary
            ot = self.open_targets.get(coin)
            same_dir = g.fire and ((decision.direction == Direction.long) == (state.pos_units > 0))
            if ot is not None and now_ms >= ot["expires_ms"] and not same_dir:
                self._flatten(coin, snap, now_ms, "horizon expired")
                summary["action"] = "flatten_expiry"
                return summary

        # entries, re-affirmations and flips
        if g.fire and not killed and not self._paused(coin, now_ms):
            tgt = target_notional(decision, state, self.policy)
            v = self.risk.check_order(state, tgt)
            self.log.write("orders", {"coin": coin, "ts_ms": now_ms, "decision_id": did, "requested": tgt,
                                      "allowed": v.allowed, "target": v.target_notional, "reasons": v.reasons, "synthetic": snap.synthetic})
            if v.allowed:
                rep = self.executor.target_position(coin, v.target_notional, snap.l2, now_ms)
                self._log_fills(rep, snap.synthetic)
                self.open_targets[coin] = {"expires_ms": horizon_ms, "direction": decision.direction.value, "decision_id": did}
                summary.update(action="target", target=v.target_notional, fills=len(rep.fills))
            else:
                summary.update(action="blocked", reasons=v.reasons)
        else:
            summary.update(action="hold", reasons=g.reasons if not g.fire else ("paused" if self._paused(coin, now_ms) else "killed",))
        acct2 = self.executor.account(coin, now_ms)
        self.log.write("equity", {"coin": coin, "ts_ms": now_ms, "equity": acct2.equity_usd, "pos_units": acct2.position_units,
                                  "drawdown_pct": state.drawdown_pct, "synthetic": snap.synthetic})
        return summary

    # ------------------------------------------------------------------ outcomes
    def resolve_outcomes(self, now_ms: int) -> int:
        keep, n = [], 0
        for r in self.pending:
            if r["horizon_ts_ms"] > now_ms:
                keep.append(r); continue
            close = self.feed.close_at(r["coin"], r["horizon_ts_ms"])
            if close is None:
                keep.append(r); continue
            y = 1 if close > r["mid"] else 0
            self.log.write("outcomes", {"decision_id": r["id"], "coin": r["coin"], "ts_ms": r["ts_ms"], "horizon_ts_ms": r["horizon_ts_ms"],
                                        "p_up": r["p_up"], "y": y, "ret_bps": (close / r["mid"] - 1.0) * 1e4,
                                        "direction": r["direction"], "source": r["source"], "regime": r["regime"],
                                        "confidence": r["confidence"], "synthetic": r["synthetic"]})
            n += 1
        self.pending = keep
        return n

    def tick(self, now_ms: int) -> list[dict]:
        out = [self._tick_coin(c, now_ms) for c in self.coins]
        self.resolve_outcomes(now_ms)
        return out


# ---------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["synthetic", "paper", "live"], default="synthetic")
    ap.add_argument("--coins", default="BTC,ETH")
    ap.add_argument("--interval", default="1m", choices=list(INTERVAL_MS))
    ap.add_argument("--judge", choices=["rule", "jev", "rule_uncapped"], default="rule",
                    help="rule_uncapped lifts the baseline's confidence cap so the fill path is exercised; synthetic mode only")
    ap.add_argument("--out", default="data/agent")
    ap.add_argument("--ticks", type=int, default=600, help="synthetic mode: number of candles to simulate")
    ap.add_argument("--every", type=float, default=None, help="wall-clock seconds between ticks (default: the interval)")
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--policy", default="config/policy.json")
    ap.add_argument("--risk", default="config/risk.json")
    ap.add_argument("--setups", default="config/setups_2026-09-26.json")
    ap.add_argument("--brain", action="store_true", help="enable BRAIN escalations through the Anthropic API")
    ap.add_argument("--base-url", default="https://api.hyperliquid.xyz")
    ap.add_argument("--max-live-notional", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    coins = [c for c in a.coins.split(",") if c]
    policy = PolicyConfig.from_json(a.policy) if os.path.exists(a.policy) else PolicyConfig()
    limits = RiskLimits.from_json(a.risk) if os.path.exists(a.risk) else RiskLimits()
    setups = SetupBook.load(a.setups) if os.path.exists(a.setups) else None
    risk = RiskLayer(limits, a.out)
    if a.judge == "rule_uncapped":
        if a.mode != "synthetic":
            ap.error("--judge rule_uncapped is allowed only with --mode synthetic (its confidence is not calibrated)")
        judge = RuleJudge(max_spread_bps=policy.max_spread_bps, max_confidence=0.99)
    else:
        judge = RuleJudge(max_spread_bps=policy.max_spread_bps) if a.judge == "rule" else JevJudge()
    brain = None
    if a.brain:
        from .brain import Brain
        brain = Brain()

    if a.mode == "synthetic":
        feed = MultiFeed({c: SyntheticFeed(coin=c, seed=a.seed + i, interval=a.interval) for i, c in enumerate(coins)})
        executor = PaperExecutor(initial_equity=a.equity)
        loop = ReflexLoop(feed, judge, executor, risk, policy, setups, a.out, coins, a.interval, brain, tag="synthetic")
        t = next(iter(feed.feeds.values())).candles[-1].T_ms + 1
        for _ in range(a.ticks):
            loop.tick(t); t += loop.interval_ms
        print(json.dumps({"equity": executor.equity(), "fills": len(executor.fills), "decisions": loop.decision_seq,
                          "killed": risk.is_killed(), "note": "SYNTHETIC DATA: not evidence"}, indent=1))
        return

    from hlr.hl_api import HLInfo
    feed = HyperliquidFeed(HLInfo(a.base_url, cache_dir=None), interval=a.interval)
    if a.mode == "paper":
        executor = PaperExecutor(initial_equity=a.equity)
    else:
        from .execution import LiveExecutor
        executor = LiveExecutor(a.out, max_live_notional=a.max_live_notional, base_url=a.base_url)
    loop = ReflexLoop(feed, judge, executor, risk, policy, setups, a.out, coins, a.interval, brain, tag=a.mode)
    every = a.every or loop.interval_ms / 1000.0
    while True:
        t0 = time.time()
        now_ms = int(t0 * 1000)
        for s in loop.tick(now_ms):
            print(json.dumps(s, default=str))
        time.sleep(max(0.0, every - (time.time() - t0)))


if __name__ == "__main__":
    main()
