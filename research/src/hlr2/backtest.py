"""Single-position, bar-driven backtest engine with the $100 account rules.

Conventions (documented in README): a decision is taken at the close of bar k (time grid[k]); orders fill at
exec_px[k] (next bar open / next observation) at time exec_ts[k]. Exit rules are evaluated on subsequent bars:
 * candles: from the entry bar itself (its high/low can hit the stop after the open fill) -- bar k+1 onward;
 * sampled: from bar k+2 onward (the fill consumed observation k+1).
Stop/target on the same candle: stop first (conservative). Gap through the stop: fill at the bar open when the open
is beyond the stop (candles) or at the observed price (sampled), plus an adverse_stop_bps penalty. Targets on sampled
data fill at the target price only when the observation is beyond it (never better than the target).
Funding: hourly settlements while a position is open, at the settlement timestamps in the funding table.
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from .costs import CostModel, base_model
from .specs import SPECS, round_size, round_price


class Account:
    def __init__(self, equity: float = 100.0, risk_per_trade: float = 1.0, max_leverage: float = 2.0, dd_pause: float = 10.0):
        self.equity0 = equity; self.equity = equity; self.risk = risk_per_trade; self.max_lev = max_leverage
        self.dd_pause = dd_pause; self.hwm = equity; self.paused = False; self.paused_at = None
        # shadow account: continues trading through the pause
        self.shadow_equity = equity


def size_position(price: float, stop_px: float, spec, cm: CostModel, equity: float, risk: float, max_lev: float):
    """Returns (qty, reason). Planned risk = stop distance + estimated round-trip costs (entry+exit sides at entry price)."""
    stop_dist = abs(price - stop_px)
    if stop_dist <= 0: return 0.0, "zero_stop"
    unit_cost = 2 * (price * (cm.taker_fee + (cm.half_spread_bps + cm.slippage_bps) / 1e4)) + price * cm.adverse_stop_bps / 1e4
    qty = risk / (stop_dist + unit_cost)
    qty = min(qty, max_lev * equity / price)                       # gross leverage cap
    qty = round_size(qty, spec.sz_decimals)
    if qty <= 0: return 0.0, "rounds_to_zero"
    if qty * price < spec.min_order_usd: return 0.0, "below_min_order"
    return qty, "ok"


def run(panel, strategy, cost_regime: str = "base", funding: dict[str, pd.DataFrame] | None = None, account: Account | None = None,
        start=None, end=None, allowed_dirs=("long", "short"), priority: dict[str, float] | None = None, record_rejections: bool = True,
        stop_proximity_atr: float = 0.0):
    """stop_proximity_atr (sampled panels only): a stop counts as hit when the observed price comes within this many
    engine-ATRs (EMA14 of |dClose| x 2) of the stop level, filled AT the stop plus the adverse penalty. It models the
    unobserved intrabar excursion between 15-minute samples; calibrated on BTC/ETH against real candles."""
    """strategy: object with .prepare(panel) -> per-symbol dict of arrays and .signal(sym, k, state) -> Signal|None and
    .exit(sym, k, pos, state) -> str|None (reason) for discretionary exits. See strategies.py."""
    acct = account or Account()
    syms = panel.symbols
    if "+prox" in cost_regime:
        cost_regime, pf = cost_regime.split("+prox"); stop_proximity_atr = float(pf)
    cms = {s: base_model(s, cost_regime) for s in syms}
    specs = {s: SPECS[s] for s in syms}
    st = strategy.prepare(panel)
    grid = panel.grid
    n = len(grid)
    k0 = 0 if start is None else int(np.searchsorted(grid.values, np.datetime64(pd.Timestamp(start))))
    k1 = n if end is None else int(np.searchsorted(grid.values, np.datetime64(pd.Timestamp(end))))
    pos = None
    trades, rejections, equity_curve = [], [], []
    fund_idx = {}
    if funding:
        for s, f in funding.items():
            f = f.sort_values("ts"); fund_idx[s] = (f.ts.values.astype("datetime64[ns]"), f.rate.values.astype(float))
    order = sorted(syms, key=lambda s: ((priority or {}).get(s, cms[s].half_spread_bps), s))   # concurrent-signal rule
    sampled = panel.kind == "sampled"
    eatr = {}
    if sampled and stop_proximity_atr > 0:
        from .indicators import ema
        for s in syms:
            c = panel.inst[s].c.values.astype(float); dc = np.abs(np.diff(c, prepend=np.nan)); eatr[s] = ema(2.0 * dc, 14)

    def apply_funding(p, t_from, t_to):
        if p["sym"] not in fund_idx: return 0.0
        ts, rates = fund_idx[p["sym"]]
        a = pd.Timestamp(t_from).tz_convert("UTC").tz_localize(None).to_datetime64(); b = pd.Timestamp(t_to).tz_convert("UTC").tz_localize(None).to_datetime64()
        i0 = np.searchsorted(ts, a, side="right"); i1 = np.searchsorted(ts, b, side="right")
        if i1 <= i0: return 0.0
        r = rates[i0:i1].sum()
        return -p["dir"] * r * p["qty"] * p["entry_px"]        # long pays positive funding

    def close_position(p, px, t, reason, k, stop_fill=False):
        cm = cms[p["sym"]]
        eff = px - p["dir"] * px * (cm.half_spread_bps + cm.slippage_bps) / 1e4
        if stop_fill: eff -= p["dir"] * px * cm.adverse_stop_bps / 1e4
        fee = cm.taker_fee * eff * p["qty"]
        fund = apply_funding(p, p["entry_ts"], t) if cm.funding else 0.0
        gross = p["dir"] * (eff - p["entry_eff"]) * p["qty"]
        net = gross - fee - p["entry_fee"] + fund
        rec = {**{k_: v for k_, v in p.items() if k_ not in ("state", "pending_exit", "manage_from")}, "exit_ts": t, "exit_px": px, "exit_eff": eff, "exit_fee": fee, "funding": fund,
               "gross_pnl": gross, "net_pnl": net, "reason": reason, "bars_held": k - p["entry_k"],
               "hold_min": (pd.Timestamp(t) - pd.Timestamp(p["entry_ts"])).total_seconds() / 60, "r_multiple": net / p["planned_risk"],
               "cost_total": fee + p["entry_fee"] + abs(eff - px) * p["qty"] + abs(p["entry_eff"] - p["entry_px"]) * p["qty"],
               "counted": not p["shadow_only"]}
        if not p["shadow_only"]: acct.equity += net
        acct.shadow_equity += net
        trades.append(rec)
        return None

    for k in range(k0, k1):
        # 1) manage open position
        if pos is not None:
            s = pos["sym"]; d = panel.inst[s]; cm = cms[s]
            if k >= pos["manage_from"] and bool(d.valid.iat[k]):
                o, h, l, c = d.o.iat[k], d.h.iat[k], d.l.iat[k], d.c.iat[k]
                stop, target = pos["stop"], pos["target"]
                hit_stop = hit_tgt = False; fill = None
                if sampled:
                    px_now = c
                    prox = (stop_proximity_atr * eatr[s][k]) if (stop_proximity_atr > 0 and s in eatr and np.isfinite(eatr[s][k])) else 0.0
                    if pos["dir"] == 1 and px_now <= stop: hit_stop, fill = True, px_now
                    elif pos["dir"] == -1 and px_now >= stop: hit_stop, fill = True, px_now
                    elif prox > 0 and pos["dir"] == 1 and min(px_now, l) <= stop + prox: hit_stop, fill = True, stop
                    elif prox > 0 and pos["dir"] == -1 and max(px_now, h) >= stop - prox: hit_stop, fill = True, stop
                    elif target is not None and ((pos["dir"] == 1 and px_now >= target) or (pos["dir"] == -1 and px_now <= target)): hit_tgt, fill = True, target
                else:
                    if pos["dir"] == 1:
                        if o <= stop: hit_stop, fill = True, o
                        elif l <= stop: hit_stop, fill = True, stop
                        elif target is not None and h >= target: hit_tgt, fill = True, (o if o >= target else target)
                    else:
                        if o >= stop: hit_stop, fill = True, o
                        elif h >= stop: hit_stop, fill = True, stop
                        elif target is not None and l <= target: hit_tgt, fill = True, (o if o <= target else target)
                if hit_stop:
                    pos = close_position(pos, fill, grid[k], "stop", k, stop_fill=True)
                elif hit_tgt:
                    pos = close_position(pos, fill, grid[k], "target", k)
                else:
                    # trailing stop update (strategy-provided) and discretionary / time / session exits
                    new_stop = strategy.trail(s, k, pos, st) if hasattr(strategy, "trail") else None
                    if new_stop is not None:
                        pos["stop"] = max(pos["stop"], new_stop) if pos["dir"] == 1 else min(pos["stop"], new_stop)
                    reason = None
                    if bool(d.must_exit.iat[k]): reason = "session_end"
                    else:
                        r = strategy.exit(s, k, pos, st)
                        if r: reason = r
                    if reason and bool(d.exec_valid.iat[k]):
                        pos["pending_exit"] = (reason, k)
            if pos is not None and pos.get("pending_exit") and k == pos["pending_exit"][1]:
                reason, kk = pos["pending_exit"]; d = panel.inst[s]
                pos = close_position(pos, float(d.exec_px.iat[kk]), d.exec_ts.iat[kk], reason, kk + 1)
        # 2) drawdown pause bookkeeping
        acct.hwm = max(acct.hwm, acct.equity)
        if not acct.paused and acct.equity < acct.hwm - acct.dd_pause:
            acct.paused = True; acct.paused_at = grid[k]
        # 3) entries
        if pos is None:
            for s in order:
                d = panel.inst[s]
                if not (bool(d.valid.iat[k]) and bool(d.can_enter.iat[k]) and bool(d.exec_valid.iat[k])): continue
                sig = strategy.signal(s, k, st)
                if sig is None: continue
                if ("long" if sig.dir == 1 else "short") not in allowed_dirs: continue
                px = float(d.exec_px.iat[k]); cm = cms[s]; spec = specs[s]
                entry_eff = px + sig.dir * px * (cm.half_spread_bps + cm.slippage_bps) / 1e4
                stop_px = round_price(sig.stop, spec.sz_decimals)
                if (sig.dir == 1 and stop_px >= px) or (sig.dir == -1 and stop_px <= px):
                    if record_rejections: rejections.append({"ts": grid[k], "sym": s, "dir": sig.dir, "reason": "stop_on_wrong_side"})
                    continue
                eq_for_size = acct.equity if not acct.paused else acct.shadow_equity
                qty, why = size_position(px, stop_px, spec, cm, eq_for_size, acct.risk, acct.max_lev)
                if qty <= 0:
                    if record_rejections: rejections.append({"ts": grid[k], "sym": s, "dir": sig.dir, "reason": why})
                    continue
                fee = cm.taker_fee * entry_eff * qty
                pos = {"sym": s, "dir": sig.dir, "qty": qty, "entry_k": k + 1, "entry_ts": d.exec_ts.iat[k], "signal_ts": grid[k], "entry_px": px,
                       "entry_eff": entry_eff, "entry_fee": fee, "stop": stop_px, "target": (round_price(sig.target, spec.sz_decimals) if sig.target else None),
                       "planned_risk": acct.risk, "notional": qty * px, "margin": qty * px / spec.max_leverage, "leverage": qty * px / max(eq_for_size, 1e-9),
                       "manage_from": k + (2 if sampled else 1), "shadow_only": acct.paused, "tag": sig.tag, "session_date": d.session_date.iat[k],
                       "stop_dist_bps": abs(px - stop_px) / px * 1e4, "pending_exit": None, "state": sig.state}
                break
        equity_curve.append((grid[k], acct.equity, acct.shadow_equity, acct.paused, pos is not None))
    if pos is not None:   # force-close at the last valid price
        s = pos["sym"]; d = panel.inst[s]; kk = min(k1 - 1, len(d) - 1)
        pos = close_position(pos, float(d.c.iat[kk]), grid[kk], "end_of_data", kk)
    tr = pd.DataFrame(trades); rj = pd.DataFrame(rejections)
    ec = pd.DataFrame(equity_curve, columns=["ts", "equity", "shadow_equity", "paused", "in_position"])
    return {"trades": tr, "rejections": rj, "equity": ec, "account": acct}
