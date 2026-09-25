"""Deterministic risk gate: the only thing standing between the decider and the venue. The decider sees these limits
but cannot change them. Risk-reducing actions (close, hold) always pass. Sizing: $1 planned risk incl. costs, gross
leverage cap, szDecimals rounding, $10 minimum (same rules as the research engine)."""
from __future__ import annotations
import os, sys, datetime as dt
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.specs import SPECS, round_size, round_price, Spec
from hlr2.costs import base_model
from hlr2.sessions import annotate


class RiskGate:
    def __init__(self, cfg: dict):
        self.a = cfg["account"]; self.session = cfg.get("session", "24x7"); self.cost_regime = cfg.get("costs", {}).get("regime", "base")

    # ---- account-level checks ----
    def kill_reason(self, state: dict, kill_file: str) -> str | None:
        if os.path.exists(kill_file): return f"manual kill switch present: {kill_file}"
        if state["hwm"] - state["equity"] >= self.a["drawdown_kill_usd"]: return f"drawdown kill: {state['hwm'] - state['equity']:.2f} >= {self.a['drawdown_kill_usd']}"
        return None

    def roll_day(self, state: dict, now: pd.Timestamp):
        day = str(pd.Timestamp(now).tz_convert("UTC").date())
        if state.get("day") != day:
            state["day"] = day; state["day_pnl"] = 0.0; state["trades_today"] = 0; state["halted_today"] = False

    def update_pause_flags(self, state: dict):
        state["hwm"] = max(state.get("hwm", state["equity"]), state["equity"])
        if not state.get("paused") and state["equity"] < state["hwm"] - self.a["drawdown_pause_usd"]:
            state["paused"] = True; state["paused_at"] = state.get("now")
        if state.get("day_pnl", 0.0) <= -self.a["daily_loss_halt_usd"]: state["halted_today"] = True

    def session_flags(self, now: pd.Timestamp) -> dict:
        if self.session == "24x7": return {"in_session": True, "can_enter": True, "must_exit": False}
        f = annotate(pd.Series([pd.Timestamp(now)]), 90, 45).iloc[0]
        return {"in_session": bool(f.in_session), "can_enter": bool(f.can_enter), "must_exit": bool(f.must_exit)}

    # ---- per-decision check + sizing ----
    def evaluate(self, dec, price: float, state: dict, now: pd.Timestamp, last_entry_bar_idx: int | None, bar_idx: int) -> tuple[bool, str, float]:
        """Returns (allowed, reason, qty)."""
        if dec.action in ("hold", "close"): return True, "risk-reducing", 0.0
        if state.get("killed"): return False, "killed", 0.0
        if state.get("paused"): return False, "drawdown pause active (shadow only)", 0.0
        if state.get("halted_today"): return False, "daily loss halt", 0.0
        if state.get("position"): return False, "max_positions=1 reached", 0.0
        if state.get("trades_today", 0) >= self.a["max_trades_per_day"]: return False, "max trades per day", 0.0
        if last_entry_bar_idx is not None and bar_idx - last_entry_bar_idx < self.a["cooldown_bars"]: return False, "cooldown", 0.0
        sf = self.session_flags(now)
        if not sf["can_enter"]: return False, "outside entry window", 0.0
        spec = SPECS.get(dec.asset)
        if spec is None: return False, f"no contract spec for {dec.asset}", 0.0
        cm = base_model(dec.asset, self.cost_regime)
        stop = round_price(float(dec.sl_price), spec.sz_decimals); dr = 1 if dec.action == "buy" else -1
        if dr * (price - stop) <= 0: return False, "stop on wrong side after tick rounding", 0.0
        stop_dist = abs(price - stop)
        unit_cost = 2 * price * (cm.taker_fee + (cm.half_spread_bps + cm.slippage_bps) / 1e4) + price * cm.adverse_stop_bps / 1e4
        qty = self.a["risk_per_trade_usd"] / (stop_dist + unit_cost)
        qty = min(qty, self.a["max_gross_leverage"] * state["equity"] / price)
        if dec.allocation_usd and dec.allocation_usd > 0: qty = min(qty, dec.allocation_usd / price)   # decider may only shrink
        qty = round_size(qty, spec.sz_decimals)
        if qty <= 0: return False, "rounds to zero", 0.0
        if qty * price < spec.min_order_usd: return False, f"below ${spec.min_order_usd} minimum", 0.0
        return True, "ok", qty
