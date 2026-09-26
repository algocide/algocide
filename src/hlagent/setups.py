"""Finalist setups compiled into typed specs the loop and the judge can use.

A SetupSpec is a hypothesis with a fixed coin, a direction bias, a horizon, and numeric invalidation rules that CODE
evaluates on every state (no model involved). When a rule persists for `persist_ticks`, the setup is invalidated
and the gate refuses new entries for it until an operator re-enables it.
"""
from __future__ import annotations
import json
import operator
from typing import Optional
from pydantic import Field
from .schema import Direction, Frozen, StateVector

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}


class InvalidationRule(Frozen):
    metric: str                     # a StateVector field name
    op: str                         # one of < <= > >= ==
    value: float
    persist_ticks: int = 1          # consecutive ticks the condition must hold
    note: str = ""

    def holds(self, state: StateVector) -> bool:
        if self.op not in _OPS:
            raise ValueError(f"bad op {self.op}")
        return _OPS[self.op](float(getattr(state, self.metric)), self.value)


class SetupSpec(Frozen):
    id: str
    name: str
    coin: str
    bias: Direction = Direction.neutral        # fixed direction for carry-type setups; neutral = judge decides
    horizon_candles: int = 12
    evidence_class: str = "speculation"        # measured | reported | speculation
    status: str = "watch"                      # paper | watch | rejected | invalidated
    thesis: str = ""
    catalyst: str = ""
    bear_case: str = ""
    invalidation: list[InvalidationRule] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class SetupBook:
    def __init__(self, setups: list[SetupSpec]):
        self.setups = {s.id: s for s in setups}
        self._counters: dict[tuple[str, int], int] = {}
        self.invalidated: dict[str, str] = {}

    @classmethod
    def load(cls, path: str) -> "SetupBook":
        with open(path) as f:
            d = json.load(f)
        return cls([SetupSpec(**s) for s in d["setups"]])

    def for_coin(self, coin: str, tradeable_only: bool = True) -> Optional[SetupSpec]:
        for s in self.setups.values():
            if s.coin == coin and (not tradeable_only or s.status == "paper") and s.id not in self.invalidated:
                return s
        return None

    def evaluate(self, setup: SetupSpec, state: StateVector) -> Optional[str]:
        """Update persistence counters; return the invalidation note if the setup just became invalid."""
        for i, rule in enumerate(setup.invalidation):
            key = (setup.id, i)
            if rule.holds(state):
                self._counters[key] = self._counters.get(key, 0) + 1
                if self._counters[key] >= rule.persist_ticks:
                    note = f"{rule.metric} {rule.op} {rule.value} for {rule.persist_ticks} ticks ({rule.note})"
                    self.invalidated[setup.id] = note
                    return note
            else:
                self._counters[key] = 0
        return None

    def is_invalidated(self, setup_id: str) -> bool:
        return setup_id in self.invalidated
