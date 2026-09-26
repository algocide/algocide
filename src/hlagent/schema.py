"""Typed contracts for the reflex layer.

Design rule: the judge (Jev, a rule baseline, or a replay) answers FIVE typed questions about ONE compact state
snapshot. It never sets a threshold, a size, or a side effect; those live in policy.py, risk.py and execution.py.
"""
from __future__ import annotations
import math
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

# The spec asks for a state snapshot under 400 tokens. Numeric-heavy text tokenises badly, so we budget at a
# conservative 3 characters per token and cap the serialised state at 1000 characters (~333 tokens).
MAX_STATE_CHARS = 1000
CHARS_PER_TOKEN = 3.0


def approx_tokens(s: str) -> int:
    return int(math.ceil(len(s) / CHARS_PER_TOKEN))


class Regime(str, Enum):
    trending = "trending"
    mean_reverting = "mean_reverting"
    high_vol = "high_vol"
    crisis = "crisis"


class Direction(str, Enum):
    long = "long"
    short = "short"
    neutral = "neutral"


class RiskState(str, Enum):
    safe = "safe"
    near_limit = "near_limit"
    reduce = "reduce"


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- raw inputs (every one carries its own timestamp)
class Level(Frozen):
    px: float = Field(gt=0)
    sz: float = Field(ge=0)


class L2Snapshot(Frozen):
    ts_ms: int
    coin: str
    bids: list[Level]
    asks: list[Level]

    @classmethod
    def from_hl(cls, coin: str, resp: dict, ts_ms: int | None = None) -> "L2Snapshot":
        """Parse a Hyperliquid `l2Book` response: {"coin", "time", "levels": [[bids...], [asks...]]} with
        levels as {"px": str, "sz": str, "n": int}."""
        lv = resp.get("levels") or [[], []]
        t = int(ts_ms if ts_ms is not None else resp.get("time", 0))
        return cls(ts_ms=t, coin=coin,
                   bids=[Level(px=float(x["px"]), sz=float(x["sz"])) for x in lv[0]],
                   asks=[Level(px=float(x["px"]), sz=float(x["sz"])) for x in lv[1]])


class Candle(Frozen):
    t_ms: int   # open time
    T_ms: int   # close time (a candle is usable only once T_ms <= decision time)
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0

    @classmethod
    def from_hl(cls, row: dict) -> "Candle":
        return cls(t_ms=int(row["t"]), T_ms=int(row["T"]), o=float(row["o"]), h=float(row["h"]),
                   l=float(row["l"]), c=float(row["c"]), v=float(row.get("v", 0.0)))


class AssetCtx(Frozen):
    ts_ms: int
    coin: str
    mark_px: float
    oracle_px: float
    funding_1h: float = 0.0      # fraction per hour (Hyperliquid pays hourly)
    premium: float = 0.0         # fraction
    open_interest: float = 0.0   # units
    day_ntl_vlm: float = 0.0


class AccountState(Frozen):
    ts_ms: int
    equity_usd: float
    peak_equity_usd: float
    day_start_equity_usd: float
    position_units: float = 0.0     # signed, this coin
    entry_px: float = 0.0
    gross_notional_usd: float = 0.0  # all markets


# ---------------------------------------------------------------- the compact numeric state the judge sees
class StateVector(Frozen):
    ts_ms: int
    coin: str
    setup_id: str = ""
    # book
    mid: float
    spread_bps: float
    imbalance: float            # (bid depth - ask depth) / (bid + ask), top-k levels, in USD
    depth_bid_usd: float
    depth_ask_usd: float
    # price path (completed candles only)
    ret_1_bps: float
    ret_5_bps: float
    ret_20_bps: float
    rv20_bps: float             # stdev of per-candle log returns, last 20 completed candles
    rv100_bps: float
    rv_ratio: float             # rv20 / rv100 (1.0 when rv100 is unavailable)
    range_pos_20: float         # where mid sits in the last-20-candle high/low range, 0..1
    # venue context
    funding_1h_bps: float
    premium_bps: float
    oi_usd: float
    # inventory and account
    pos_units: float
    pos_notional_usd: float
    pos_frac: float             # |position notional| / equity
    upnl_bps: float             # unrealised P&L in bps of entry, signed for the position
    equity_usd: float
    gross_other_usd: float = 0.0   # |notional| held in other markets
    drawdown_pct: float         # from peak equity, >= 0
    daily_pnl_pct: float
    # hygiene
    staleness_ms: int
    n_candles: int

    def to_prompt(self) -> str:
        """Compact, order-stable text form. Every value is computed in code; the judge only reads it."""
        s = (f"coin={self.coin} ts={self.ts_ms} setup={self.setup_id or '-'} "
             f"mid={self.mid:.6g} spr={self.spread_bps:.2f}bps imb={self.imbalance:+.3f} "
             f"dB={self.depth_bid_usd:.0f} dA={self.depth_ask_usd:.0f} "
             f"r1={self.ret_1_bps:+.1f} r5={self.ret_5_bps:+.1f} r20={self.ret_20_bps:+.1f} "
             f"rv20={self.rv20_bps:.1f} rv100={self.rv100_bps:.1f} rvr={self.rv_ratio:.2f} rng={self.range_pos_20:.2f} "
             f"f1h={self.funding_1h_bps:+.3f}bps prem={self.premium_bps:+.1f}bps oi={self.oi_usd:.3g} "
             f"pos={self.pos_units:+.6g} posN={self.pos_notional_usd:+.0f} posF={self.pos_frac:.3f} upnl={self.upnl_bps:+.1f} "
             f"eq={self.equity_usd:.0f} gO={self.gross_other_usd:.0f} dd={self.drawdown_pct:.2f}% dpnl={self.daily_pnl_pct:+.2f}% "
             f"stale={self.staleness_ms}ms n={self.n_candles}")
        if len(s) > MAX_STATE_CHARS:
            raise ValueError(f"state prompt {len(s)} chars exceeds budget {MAX_STATE_CHARS}")
        return s


# ---------------------------------------------------------------- the judged decision
class Decision(Frozen):
    """One judged decision for one state. `confidence` is the judge's calibrated probability that `direction` is
    right over the setup horizon. p_up() maps it to P(price up) for Brier scoring."""
    regime: Regime
    direction: Direction
    toxic_flow: bool
    setup_quality: int = Field(ge=0, le=3)
    risk_state: RiskState
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = "unknown"
    latency_ms: float = 0.0
    state_ts_ms: int = 0
    note: str = ""

    def p_up(self) -> float:
        if self.direction == Direction.long:
            return self.confidence
        if self.direction == Direction.short:
            return 1.0 - self.confidence
        return 0.5

    @classmethod
    def neutral(cls, source: str, state_ts_ms: int, note: str = "", latency_ms: float = 0.0) -> "Decision":
        """Fail-closed decision: cannot pass the gate under any configuration."""
        return cls(regime=Regime.high_vol, direction=Direction.neutral, toxic_flow=True, setup_quality=0,
                   risk_state=RiskState.reduce, confidence=0.0, source=source, latency_ms=latency_ms,
                   state_ts_ms=state_ts_ms, note=note)


# The five typed questions the judge answers. The `type` vocabulary (choice / yes_no / score) and the request shape
# follow public descriptions of the TypeSafe "System One" endpoint (Sept 2026 write-ups): the vendor docs were not
# reachable from this session, so judge.JevJudge treats the wire format as UNVERIFIED and fails closed.
JEV_QUESTIONS: dict[str, dict] = {
    "regime": {"type": "choice", "choices": [r.value for r in Regime],
               "question": "Which regime best describes this market state right now?"},
    "direction": {"type": "choice", "choices": [d.value for d in Direction],
                  "question": "Which direction has positive expected value over the setup horizon, net of a taker round trip?"},
    "toxic_flow": {"type": "yes_no",
                   "question": "Is recent order flow toxic, i.e. would a taker entering now likely be adversely selected?"},
    "setup_quality": {"type": "score", "min": 0, "max": 3,
                      "question": "How well does the state match the named setup? 0 = not at all, 3 = textbook."},
    "risk_state": {"type": "choice", "choices": [r.value for r in RiskState],
                   "question": "Given inventory, drawdown and staleness, is it safe to add risk?"},
}


def decision_json_schema() -> dict:
    return Decision.model_json_schema()
