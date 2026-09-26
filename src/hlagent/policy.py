"""Policy: the gate and the sizing rule. All thresholds live here (or in config/policy.json), never in the model.

Gate (all must hold):      direction != neutral, setup_quality >= 2, confidence > 0.80, not toxic_flow,
                           judge risk_state == safe AND code risk_state == safe (the code's view is authoritative;
                           the judge can only add caution, never remove it), setup not invalidated.
Size:                      fractional Kelly on the judge's calibrated probability with an assumed payoff ratio b,
                           f = fraction * (p - (1-p)/b); `fraction` is capped in code at quarter Kelly and the
                           resulting notional is capped at max_position_frac of equity.
"""
from __future__ import annotations
import json
from dataclasses import asdict, dataclass, replace
from .schema import Decision, Direction, RiskState, StateVector

KELLY_HARD_CAP = 0.25   # quarter Kelly: config may go lower, never higher


@dataclass(frozen=True)
class PolicyConfig:
    min_setup_quality: int = 2
    min_confidence: float = 0.80          # strict: confidence must be ABOVE this
    kelly_fraction: float = 0.25
    payoff_ratio: float = 1.0             # ASSUMPTION until review.py measures realised win/loss sizes
    max_position_frac: float = 0.10       # per market, fraction of equity (risk.py enforces its own copy)
    horizon_candles: int = 12             # decision horizon used for outcomes/Brier and for position expiry
    max_spread_bps: float = 5.0           # do not take liquidity through a wider spread
    max_staleness_ms: int = 5_000

    # keys the overnight review may propose to change (never risk limits)
    TUNABLE = ("min_setup_quality", "min_confidence", "kelly_fraction", "payoff_ratio", "horizon_candles",
               "max_spread_bps")

    def __post_init__(self):
        if not (0.0 < self.kelly_fraction <= KELLY_HARD_CAP):
            raise ValueError(f"kelly_fraction must be in (0, {KELLY_HARD_CAP}]")
        if not (0.5 <= self.min_confidence < 1.0):
            raise ValueError("min_confidence must be in [0.5, 1)")
        if not (0 <= self.min_setup_quality <= 3):
            raise ValueError("min_setup_quality must be in 0..3")
        if self.payoff_ratio <= 0:
            raise ValueError("payoff_ratio must be > 0")

    @classmethod
    def from_json(cls, path: str) -> "PolicyConfig":
        with open(path) as f:
            d = json.load(f)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    def with_updates(self, **kw) -> "PolicyConfig":
        bad = [k for k in kw if k not in self.TUNABLE]
        if bad:
            raise ValueError(f"not tunable by proposals: {bad}")
        return replace(self, **kw)


@dataclass(frozen=True)
class GateResult:
    fire: bool
    reasons: tuple[str, ...]


def gate(decision: Decision, code_risk_state: RiskState, cfg: PolicyConfig, state: StateVector | None = None,
         setup_invalidated: bool = False) -> GateResult:
    reasons: list[str] = []
    if decision.direction == Direction.neutral:
        reasons.append("direction neutral")
    if decision.setup_quality < cfg.min_setup_quality:
        reasons.append(f"setup_quality {decision.setup_quality} < {cfg.min_setup_quality}")
    if not decision.confidence > cfg.min_confidence:
        reasons.append(f"confidence {decision.confidence:.3f} <= {cfg.min_confidence}")
    if decision.toxic_flow:
        reasons.append("toxic flow")
    if decision.risk_state != RiskState.safe:
        reasons.append(f"judge risk_state {decision.risk_state.value}")
    if code_risk_state != RiskState.safe:
        reasons.append(f"code risk_state {code_risk_state.value}")
    if setup_invalidated:
        reasons.append("setup invalidated")
    if state is not None:
        if state.spread_bps > cfg.max_spread_bps:
            reasons.append(f"spread {state.spread_bps:.2f}bps > {cfg.max_spread_bps}")
        if state.staleness_ms > cfg.max_staleness_ms:
            reasons.append(f"stale {state.staleness_ms}ms > {cfg.max_staleness_ms}")
    return GateResult(fire=not reasons, reasons=tuple(reasons))


def kelly_fraction(p: float, payoff_ratio: float, fraction: float, hard_cap: float = KELLY_HARD_CAP) -> float:
    """Fraction of equity to risk. f* = p - (1-p)/b; scaled by min(fraction, hard_cap); never negative."""
    if payoff_ratio <= 0:
        raise ValueError("payoff_ratio must be > 0")
    f_star = p - (1.0 - p) / payoff_ratio
    if f_star <= 0:
        return 0.0
    return min(fraction, hard_cap) * f_star


def target_notional(decision: Decision, state: StateVector, cfg: PolicyConfig) -> float:
    """Signed target notional in USD for the market, or 0.0 if the decision carries no direction."""
    if decision.direction == Direction.neutral:
        return 0.0
    f = kelly_fraction(decision.confidence, cfg.payoff_ratio, cfg.kelly_fraction)
    f = min(f, cfg.max_position_frac)
    notional = f * state.equity_usd
    return notional if decision.direction == Direction.long else -notional
