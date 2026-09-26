"""Judges: anything that turns one StateVector into one typed Decision.

  * JevJudge   - HTTP call to the TypeSafe "System One" endpoint (Jev). WIRE FORMAT UNVERIFIED: the vendor docs were
                 blocked from this session; the request/response shape below follows public write-ups (Sept 2026).
                 The adapter is tolerant on parse and FAILS CLOSED (a neutral decision) on any error or timeout.
  * RuleJudge  - deterministic, transparent baseline so the whole loop runs and can be paper-traded with no key.
                 Its confidence is NOT calibrated, so it is capped at `max_confidence` (default 0.75, below the
                 0.80 gate) until review.py supplies a measured calibration table.
  * ReplayJudge - scripted decisions for tests and dry runs.
"""
from __future__ import annotations
import math
import os
import time
from typing import Callable, Iterable, Optional, Protocol
import requests
from .schema import JEV_QUESTIONS, Decision, Direction, Regime, RiskState, StateVector
from .setups import SetupSpec


class Judge(Protocol):
    name: str
    def judge(self, state: StateVector, setup: Optional[SetupSpec]) -> Decision: ...


# ---------------------------------------------------------------------------------------------------- Jev
class JevSchemaError(ValueError):
    pass


def _pick(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return None


def parse_jev_payload(payload: dict, state_ts_ms: int, latency_ms: float) -> Decision:
    """Normalise a System One response into a Decision. Accepts a few plausible shapes; raises JevSchemaError
    when a required answer is missing or malformed (the caller fails closed)."""
    answers = _pick(payload, "answers", "results", "decisions", "output")
    if answers is None:
        answers = payload
    if not isinstance(answers, dict):
        raise JevSchemaError("answers is not an object")

    def choice(name: str) -> tuple[str, Optional[float]]:
        a = answers.get(name)
        if a is None:
            raise JevSchemaError(f"missing {name}")
        if isinstance(a, str):
            return a, None
        value = _pick(a, "choice", "value", "answer", "label")
        probs = _pick(a, "probabilities", "scores", "distribution")
        conf = _pick(a, "confidence", "probability", "p")
        if value is None and isinstance(probs, dict) and probs:
            value = max(probs, key=lambda k: float(probs[k]))
        if isinstance(probs, dict) and value in probs:
            conf = float(probs[value])
        if value is None:
            raise JevSchemaError(f"no value for {name}")
        return str(value), (float(conf) if conf is not None else None)

    def yes_no(name: str) -> bool:
        a = answers.get(name)
        if a is None:
            raise JevSchemaError(f"missing {name}")
        if isinstance(a, bool):
            return a
        if isinstance(a, (int, float)):
            return float(a) >= 0.5
        p = _pick(a, "probability", "p_yes", "yes", "p")
        if p is not None:
            return float(p) >= 0.5
        v = _pick(a, "value", "answer", "choice")
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("yes", "true", "y")
        raise JevSchemaError(f"no boolean for {name}")

    def score(name: str) -> int:
        a = answers.get(name)
        if a is None:
            raise JevSchemaError(f"missing {name}")
        if isinstance(a, (int, float)):
            return int(round(float(a)))
        v = _pick(a, "score", "value", "expected", "answer")
        if v is None:
            raise JevSchemaError(f"no score for {name}")
        return int(round(float(v)))

    regime, _ = choice("regime")
    direction, conf = choice("direction")
    if conf is None:
        raise JevSchemaError("direction has no probability; refusing to invent a confidence")
    risk_state, _ = choice("risk_state")
    return Decision(regime=Regime(regime), direction=Direction(direction), toxic_flow=yes_no("toxic_flow"),
                    setup_quality=max(0, min(3, score("setup_quality"))), risk_state=RiskState(risk_state),
                    confidence=max(0.0, min(1.0, conf)), source="jev", latency_ms=latency_ms, state_ts_ms=state_ts_ms)


class JevJudge:
    name = "jev"
    ENDPOINT = "/v1/systemone"

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.typesafe.ai", model: str = "jev",
                 timeout_s: float = 0.25, session: Optional[requests.Session] = None, log: Optional[Callable] = None):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.s = session or requests.Session()
        self.log = log or (lambda rec: None)
        self.calls = 0
        self.failures = 0

    def request_body(self, state: StateVector, setup: Optional[SetupSpec]) -> dict:
        ctx = state.to_prompt()
        if setup is not None:
            ctx += f" | setup: {setup.name}; bias={setup.bias.value}; horizon={setup.horizon_candles} candles"
        return {"model": self.model, "state": ctx, "questions": JEV_QUESTIONS}

    def judge(self, state: StateVector, setup: Optional[SetupSpec]) -> Decision:
        self.calls += 1
        if not self.api_key:
            self.failures += 1
            return Decision.neutral("jev_error", state.ts_ms, note="no TYPESAFE_API_KEY")
        t0 = time.perf_counter()
        try:
            r = self.s.post(self.base_url + self.ENDPOINT, json=self.request_body(state, setup),
                            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                            timeout=self.timeout_s)
            latency = (time.perf_counter() - t0) * 1e3
            if r.status_code != 200:
                raise JevSchemaError(f"HTTP {r.status_code}: {r.text[:200]}")
            d = parse_jev_payload(r.json(), state.ts_ms, latency)
            self.log({"kind": "jev_call", "latency_ms": latency, "ok": True})
            return d
        except Exception as e:  # timeout, connection, schema: all fail closed
            latency = (time.perf_counter() - t0) * 1e3
            self.failures += 1
            self.log({"kind": "jev_call", "latency_ms": latency, "ok": False, "error": repr(e)[:300]})
            return Decision.neutral("jev_error", state.ts_ms, note=repr(e)[:200], latency_ms=latency)


# ---------------------------------------------------------------------------------------------------- rule baseline
class RuleJudge:
    """Transparent heuristics. Every number below is a design choice, not a fitted parameter."""
    name = "rule"

    def __init__(self, max_confidence: float = 0.75, calibration: Optional[dict[str, float]] = None,
                 max_spread_bps: float = 5.0, crisis_rv_ratio: float = 3.0, high_vol_rv_ratio: float = 1.5,
                 trend_z: float = 1.0):
        self.max_confidence = max_confidence
        self.calibration = calibration or {}
        self.max_spread_bps = max_spread_bps
        self.crisis_rv_ratio = crisis_rv_ratio
        self.high_vol_rv_ratio = high_vol_rv_ratio
        self.trend_z = trend_z

    def _confidence(self, strength: float) -> float:
        raw = 0.5 + 0.5 * math.tanh(abs(strength))       # 0.5 .. 1.0 before the cap
        bucket = f"{min(0.95, math.floor(raw * 20) / 20):.2f}"
        if bucket in self.calibration:                    # measured by review.py from shadow outcomes
            return min(1.0, max(0.0, float(self.calibration[bucket])))
        return min(self.max_confidence, raw)

    def judge(self, state: StateVector, setup: Optional[SetupSpec]) -> Decision:
        t0 = time.perf_counter()
        scale = state.rv20_bps * math.sqrt(20) if state.rv20_bps > 0 else 0.0
        tz = state.ret_20_bps / scale if scale > 0 else 0.0
        # regime
        if state.rv_ratio >= self.crisis_rv_ratio or state.drawdown_pct >= 10.0:
            regime = Regime.crisis
        elif state.rv_ratio >= self.high_vol_rv_ratio:
            regime = Regime.high_vol
        elif abs(tz) >= self.trend_z:
            regime = Regime.trending
        else:
            regime = Regime.mean_reverting
        # direction and strength
        direction, strength = Direction.neutral, 0.0
        if setup is not None and setup.bias != Direction.neutral:
            direction, strength = setup.bias, 0.5 + abs(tz) * (1.0 if (tz > 0) == (setup.bias == Direction.long) else -0.5)
        elif regime == Regime.trending:
            if tz > 0 and state.imbalance > 0:
                direction, strength = Direction.long, tz * (1 + state.imbalance)
            elif tz < 0 and state.imbalance < 0:
                direction, strength = Direction.short, -tz * (1 - state.imbalance)
        elif regime == Regime.mean_reverting and state.rv20_bps > 0 and abs(state.ret_5_bps) > state.rv20_bps * math.sqrt(5):
            direction = Direction.short if state.ret_5_bps > 0 else Direction.long
            strength = abs(state.ret_5_bps) / (state.rv20_bps * math.sqrt(5)) - 1.0
        toxic = state.spread_bps > self.max_spread_bps or abs(state.imbalance) > 0.8
        quality = 0
        if direction != Direction.neutral:
            quality += 1
            if strength >= 1.0:
                quality += 1
            if (direction == Direction.long and state.funding_1h_bps <= 0) or (direction == Direction.short and state.funding_1h_bps >= 0):
                quality += 1   # carry not against the trade
        if state.drawdown_pct >= 10.0 or state.pos_frac > 0.10:
            risk_state = RiskState.reduce
        elif state.drawdown_pct >= 5.0 or state.pos_frac >= 0.08:
            risk_state = RiskState.near_limit
        else:
            risk_state = RiskState.safe
        conf = self._confidence(strength) if direction != Direction.neutral else 0.5
        return Decision(regime=regime, direction=direction, toxic_flow=toxic, setup_quality=min(3, quality),
                        risk_state=risk_state, confidence=conf, source="rule",
                        latency_ms=(time.perf_counter() - t0) * 1e3, state_ts_ms=state.ts_ms)


# ---------------------------------------------------------------------------------------------------- replay
class ReplayJudge:
    name = "replay"

    def __init__(self, decisions: Iterable[Decision] | Callable[[StateVector], Decision]):
        self._fn = decisions if callable(decisions) else None
        self._it = iter(decisions) if not callable(decisions) else None

    def judge(self, state: StateVector, setup: Optional[SetupSpec]) -> Decision:
        if self._fn is not None:
            d = self._fn(state)
        else:
            try:
                d = next(self._it)
            except StopIteration:
                return Decision.neutral("replay", state.ts_ms, note="script exhausted")
        return d.model_copy(update={"state_ts_ms": state.ts_ms, "source": "replay"})
