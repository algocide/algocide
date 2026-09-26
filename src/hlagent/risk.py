"""Hard, deterministic risk layer. The model never sees or sets anything here.

Checked before EVERY order (RiskLayer.check_order):
  * kill switch file present            -> blocked (manual reset: delete the file)
  * drawdown from peak >= max            -> kill + blocked
  * daily loss >= max                    -> kill + blocked
  * data staleness > max                 -> blocked
  * consecutive errors >= max            -> kill + blocked
  * resulting |position| > max per market or gross > max -> the order is clipped to the limit; reductions always pass
Escalation (RiskLayer.should_escalate): judge confidence < 0.60 or regime == crisis -> the loop pauses entries for
the market and asks the BRAIN for a deep re-read; until a verdict arrives nothing new is opened.
"""
from __future__ import annotations
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Optional
from .schema import Decision, Regime, RiskState, StateVector


@dataclass(frozen=True)
class RiskLimits:
    max_drawdown_pct: float = 15.0
    max_daily_loss_pct: float = 3.0
    max_position_frac: float = 0.10     # per market, fraction of equity
    max_gross_frac: float = 0.30        # all markets
    max_staleness_ms: int = 5_000
    max_consecutive_errors: int = 5
    escalate_below_confidence: float = 0.60
    kill_file: str = "KILLED"

    @classmethod
    def from_json(cls, path: str) -> "RiskLimits":
        with open(path) as f:
            d = json.load(f)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)


@dataclass(frozen=True)
class RiskVerdict:
    allowed: bool
    target_notional: float      # possibly clipped
    reasons: tuple[str, ...]
    killed: bool = False


class RiskLayer:
    def __init__(self, limits: RiskLimits, out_dir: str):
        self.limits = limits
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.errors = 0
        self.events: list[dict] = []

    # ---- kill switch
    @property
    def kill_path(self) -> str:
        return os.path.join(self.out_dir, self.limits.kill_file)

    def is_killed(self) -> bool:
        return os.path.exists(self.kill_path)

    def kill(self, reason: str) -> None:
        with open(self.kill_path, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {reason}\n")
        self.events.append({"kind": "kill", "reason": reason})

    def record_error(self) -> None:
        self.errors += 1
        if self.errors >= self.limits.max_consecutive_errors:
            self.kill(f"{self.errors} consecutive errors")

    def record_ok(self) -> None:
        self.errors = 0

    # ---- the code's own view of risk state (authoritative for the gate)
    def code_risk_state(self, state: StateVector) -> RiskState:
        L = self.limits
        if (self.is_killed() or state.drawdown_pct >= L.max_drawdown_pct or -state.daily_pnl_pct >= L.max_daily_loss_pct
                or state.pos_frac > L.max_position_frac or state.staleness_ms > L.max_staleness_ms):
            return RiskState.reduce
        if (state.drawdown_pct >= 0.5 * L.max_drawdown_pct - 1e-12 or -state.daily_pnl_pct >= 0.5 * L.max_daily_loss_pct - 1e-12
                or state.pos_frac >= 0.8 * L.max_position_frac - 1e-12):
            return RiskState.near_limit
        return RiskState.safe

    def should_escalate(self, decision: Decision, state: StateVector) -> bool:
        return decision.confidence < self.limits.escalate_below_confidence or decision.regime == Regime.crisis

    # ---- hard limits: evaluated on every state, before any decision is even requested
    def enforce_limits(self, state: StateVector) -> Optional[str]:
        """Engage the kill switch if a hard limit is breached; return the reason (None if within limits)."""
        L = self.limits
        if state.drawdown_pct >= L.max_drawdown_pct:
            reason = f"max drawdown: {state.drawdown_pct:.2f}% >= {L.max_drawdown_pct}%"
        elif -state.daily_pnl_pct >= L.max_daily_loss_pct:
            reason = f"max daily loss: {-state.daily_pnl_pct:.2f}% >= {L.max_daily_loss_pct}%"
        else:
            return None
        if not self.is_killed():
            self.kill(reason)
        return reason

    # ---- pre-order check
    def check_order(self, state: StateVector, target_notional: float) -> RiskVerdict:
        L = self.limits
        reasons: list[str] = []
        current = state.pos_notional_usd
        reducing = target_notional == 0.0 or (abs(target_notional) < abs(current) - 1e-9 and target_notional * current >= 0)
        breach = self.enforce_limits(state)
        if breach is not None:
            return RiskVerdict(False, current, (breach,), killed=True)
        if self.is_killed():
            return RiskVerdict(False, current, ("kill switch engaged",), killed=True)
        if reducing:
            return RiskVerdict(True, target_notional, ("reduce-only always allowed",))
        if state.staleness_ms > L.max_staleness_ms:
            reasons.append(f"stale {state.staleness_ms}ms")
        if state.equity_usd <= 0:
            reasons.append("no equity")
        if reasons:
            return RiskVerdict(False, current, tuple(reasons))
        cap = L.max_position_frac * state.equity_usd
        clipped = max(-cap, min(cap, target_notional))
        if abs(clipped) < abs(target_notional) - 1e-9:
            reasons.append("clipped to per-market limit")
        room = max(0.0, L.max_gross_frac * state.equity_usd - state.gross_other_usd)
        if abs(clipped) > room + 1e-9:
            clipped = room if clipped > 0 else -room
            reasons.append("clipped to gross limit")
        if abs(clipped) < 1e-9 and abs(target_notional) > 1e-9:
            return RiskVerdict(False, current, tuple(reasons) or ("no room",))
        return RiskVerdict(True, clipped, tuple(reasons) or ("ok",))
