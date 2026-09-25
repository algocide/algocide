"""Venues. PaperVenue simulates fills at the venue mid +/- half-spread and slippage with taker fees and hourly funding,
and checks stop/target against each new completed bar (conservative: stop first). HyperliquidVenue places real orders
with the official SDK and is enabled ONLY when cfg.live.enabled is true AND the private key env var is set AND the
process was started with --live --acknowledge-risk. It was never run from this sandbox (API blocked) and must be
smoke-tested on testnet first (base_url https://api.hyperliquid-testnet.xyz)."""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.costs import base_model
from hlr2.specs import SPECS, round_price


class PaperVenue:
    name = "paper"
    def __init__(self, cost_regime: str = "base", funding: dict | None = None):
        self.cost_regime = cost_regime; self.funding = funding or {}

    def open(self, sym, dr, qty, price, stop, target, now, bar_idx):
        cm = base_model(sym, self.cost_regime)
        eff = price + dr * price * (cm.half_spread_bps + cm.slippage_bps) / 1e4; fee = cm.taker_fee * eff * qty
        return {"sym": sym, "dir": dr, "qty": qty, "entry_ts": str(now), "entry_px": price, "entry_eff": eff, "entry_fee": fee, "stop": stop, "target": target,
                "bars_held": 0, "entry_bar_idx": bar_idx, "notional": qty * price, "margin": qty * price / SPECS[sym].max_leverage}

    def close(self, pos, price, now, reason, stop_fill=False):
        cm = base_model(pos["sym"], self.cost_regime)
        eff = price - pos["dir"] * price * (cm.half_spread_bps + cm.slippage_bps) / 1e4
        if stop_fill: eff -= pos["dir"] * price * cm.adverse_stop_bps / 1e4
        fee = cm.taker_fee * eff * pos["qty"]; fund = self._funding(pos, now)
        gross = pos["dir"] * (eff - pos["entry_eff"]) * pos["qty"]; net = gross - fee - pos["entry_fee"] + fund
        planned = pos.get("planned_risk", 1.0); hold_min = (pd.Timestamp(now) - pd.Timestamp(pos["entry_ts"])).total_seconds() / 60
        cost = fee + pos["entry_fee"] + abs(eff - price) * pos["qty"] + abs(pos["entry_eff"] - pos["entry_px"]) * pos["qty"]
        return {**pos, "exit_ts": str(now), "exit_px": price, "exit_eff": eff, "exit_fee": fee, "funding": fund, "gross_pnl": gross, "net_pnl": net, "reason": reason,
                "planned_risk": planned, "r_multiple": net / planned, "hold_min": hold_min, "cost_total": cost, "leverage": pos.get("notional", 0) / 100.0, "counted": True}

    def _funding(self, pos, now):
        f = self.funding.get(pos["sym"])
        if f is None or not len(f): return 0.0
        a, b = pd.Timestamp(pos["entry_ts"]), pd.Timestamp(now)
        r = f[(f.ts > a) & (f.ts <= b)].rate.sum(); return -pos["dir"] * r * pos["qty"] * pos["entry_px"]

    def check_exits(self, pos, bar, sampled: bool):
        """bar: completed bar dict with o,h,l,c. Returns (reason, fill_px) or (None, None). Stop first (conservative)."""
        o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]; dr = pos["dir"]; st, tg = pos["stop"], pos.get("target")
        if sampled:
            if dr == 1 and c <= st: return "stop", c
            if dr == -1 and c >= st: return "stop", c
            if tg and ((dr == 1 and c >= tg) or (dr == -1 and c <= tg)): return "target", tg
            return None, None
        if dr == 1:
            if o <= st: return "stop", o
            if l <= st: return "stop", st
            if tg and h >= tg: return "target", (o if o >= tg else tg)
        else:
            if o >= st: return "stop", o
            if h >= st: return "stop", st
            if tg and l <= tg: return "target", (o if o <= tg else tg)
        return None, None


class HyperliquidVenue:
    """Live execution via hyperliquid-python-sdk. Market entry (IOC at a 0.5% slippage limit), then reduce-only trigger
    orders for stop (sl) and take-profit (tp). If the stop cannot be placed the position is closed immediately.
    Requires: cfg.live.enabled, env HL_AGENT_PRIVATE_KEY (an API/agent wallet key, NOT the main wallet), env
    HL_ACCOUNT_ADDRESS (the main account the agent trades for). NEVER commit keys. UNTESTED in this sandbox."""
    name = "hyperliquid"
    def __init__(self, cfg: dict, acknowledge_risk: bool = False):
        live = cfg["live"]
        if not live.get("enabled") or not acknowledge_risk: raise RuntimeError("live venue disabled: set live.enabled and pass --live --acknowledge-risk")
        key = os.environ.get(live["private_key_env"]); addr = os.environ.get(live["account_address_env"])
        if not key or not addr: raise RuntimeError(f"missing {live['private_key_env']} / {live['account_address_env']}")
        from agent.cli import resolve_network
        base_url, self.network = resolve_network(acknowledge_risk=acknowledge_risk)   # testnet unless USE_TESTNET=false + CONFIRM_MAINNET=true
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        wallet = Account.from_key(key); self.info = Info(base_url, skip_ws=True); self.ex = Exchange(wallet, base_url, account_address=addr); self.addr = addr; self.base_url = base_url

    def open(self, sym, dr, qty, price, stop, target, now, bar_idx):
        spec = SPECS[sym]; is_buy = dr == 1
        limit = round_price(price * (1.005 if is_buy else 0.995), spec.sz_decimals)
        r = self.ex.order(sym, is_buy, qty, limit, {"limit": {"tif": "Ioc"}}, reduce_only=False)
        st = r.get("response", {}).get("data", {}).get("statuses", [{}])[0]
        if "filled" not in st: raise RuntimeError(f"entry not filled: {r}")
        fill_px = float(st["filled"]["avgPx"]); fqty = float(st["filled"]["totalSz"])
        # protective orders (reduce-only triggers)
        sl = self.ex.order(sym, not is_buy, fqty, round_price(stop, spec.sz_decimals), {"trigger": {"triggerPx": round_price(stop, spec.sz_decimals), "isMarket": True, "tpsl": "sl"}}, reduce_only=True)
        if "error" in str(sl):
            self.ex.market_close(sym); raise RuntimeError(f"stop rejected, position closed: {sl}")
        tp = None
        if target: tp = self.ex.order(sym, not is_buy, fqty, round_price(target, spec.sz_decimals), {"trigger": {"triggerPx": round_price(target, spec.sz_decimals), "isMarket": True, "tpsl": "tp"}}, reduce_only=True)
        return {"sym": sym, "dir": dr, "qty": fqty, "entry_ts": str(now), "entry_px": fill_px, "entry_eff": fill_px, "entry_fee": 0.0, "stop": stop, "target": target, "bars_held": 0, "entry_bar_idx": bar_idx,
                "notional": fqty * fill_px, "margin": fqty * fill_px / spec.max_leverage, "sl_resp": str(sl)[:200], "tp_resp": str(tp)[:200]}

    def close(self, pos, price, now, reason, stop_fill=False):
        r = self.ex.market_close(pos["sym"]); self.ex.cancel_all(pos["sym"]) if hasattr(self.ex, "cancel_all") else None
        return {**pos, "exit_ts": str(now), "exit_px": price, "reason": reason, "close_resp": str(r)[:300], "net_pnl": None}

    def reconcile(self):
        """Exchange truth supersedes local intent: returns current positions/orders for the account."""
        st = self.info.user_state(self.addr); return {"positions": st.get("assetPositions", []), "margin": st.get("marginSummary", {})}
