"""The 'sub-agent': turns raw feed data into a compact JSON digest (the videos' packaged position/market summary).
Everything is computed from COMPLETED bars only; the digest carries the bar close time it is valid for."""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.indicators import ema, atr, bollinger


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=np.nan); up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    au = ema(np.nan_to_num(up), n); ad = ema(np.nan_to_num(dn), n)
    out = 100 - 100 / (1 + au / np.where(ad == 0, np.nan, ad)); out[: n] = np.nan
    return out


def macd(c: np.ndarray, fast=12, slow=26, sig=9):
    m = ema(c, fast) - ema(c, slow); s = ema(np.nan_to_num(m), sig); return m, s, m - s


def asset_digest(cs: pd.DataFrame, cs_trend: pd.DataFrame | None, sampled: bool, ctx: dict | None = None, quote: dict | None = None) -> dict:
    """cs: completed bars of the decision timeframe (>= 60 rows); cs_trend: completed bars of the higher timeframe."""
    c = cs.c.values.astype(float); h = cs.h.values.astype(float); l = cs.l.values.astype(float)
    if len(c) < 60: return {"ok": False, "reason": f"need 60 bars, have {len(c)}"}
    e20, e50 = ema(c, 20), ema(c, 50); a = atr(h, l, c, 14, sampled=sampled); r = rsi(c, 14); _, _, hist = macd(c)
    m, u, lo, bw = bollinger(c, 20, 2.0); sd = (u[-1] - m[-1]) / 2 if np.isfinite(u[-1]) else np.nan
    vol = cs.v.values.astype(float) if "v" in cs and np.isfinite(cs.v.values.astype(float)).any() else None
    d = {"ok": True, "bar_close": str(cs.ts.iloc[-1]), "price": float(c[-1]), "ret_4bars_pct": float((c[-1] / c[-5] - 1) * 100) if len(c) > 5 else None,
         "ret_24bars_pct": float((c[-1] / c[-25] - 1) * 100) if len(c) > 25 else None,
         "ema20": float(e20[-1]), "ema50": float(e50[-1]), "price_vs_ema20_pct": float((c[-1] / e20[-1] - 1) * 100), "ema20_vs_ema50_pct": float((e20[-1] / e50[-1] - 1) * 100),
         "rsi14": float(r[-1]) if np.isfinite(r[-1]) else None, "macd_hist": float(hist[-1]), "macd_hist_prev": float(hist[-2]),
         "bb_z": float((c[-1] - m[-1]) / sd) if sd and np.isfinite(sd) and sd > 0 else None, "bb_width_pct": float(bw[-1] * 100) if np.isfinite(bw[-1]) else None,
         "atr": float(a[-1]), "atr_pct": float(a[-1] / c[-1] * 100), "hh_12": float(np.max(h[-13:-1])), "ll_12": float(np.min(l[-13:-1])),
         "volume_ratio": float(vol[-1] / np.nanmean(vol[-21:-1])) if vol is not None and np.nanmean(vol[-21:-1]) > 0 else None}
    if cs_trend is not None and len(cs_trend) >= 55:
        ct = cs_trend.c.values.astype(float); t20, t50 = ema(ct, 20), ema(ct, 50)
        d["trend_tf"] = {"bar_close": str(cs_trend.ts.iloc[-1]), "ema20_vs_ema50_pct": float((t20[-1] / t50[-1] - 1) * 100), "direction": int(np.sign(t20[-1] - t50[-1])), "price_vs_ema20_pct": float((ct[-1] / t20[-1] - 1) * 100)}
    else: d["trend_tf"] = None
    if ctx: d["ctx"] = {k: ctx.get(k) for k in ("funding", "openInterest", "dayNtlVlm", "premium")}
    if quote: d["quote"] = {"half_spread_bps": quote.get("half_spread_bps")}
    return d


def account_digest(state: dict, mids: dict, goal: dict) -> dict:
    eq = state["equity"]; pos = state.get("position"); unreal = 0.0; posd = None
    if pos:
        px = mids.get(pos["sym"]); unreal = pos["dir"] * (px - pos["entry_px"]) * pos["qty"] if px else 0.0
        posd = {"sym": pos["sym"], "side": "long" if pos["dir"] == 1 else "short", "qty": pos["qty"], "entry_px": pos["entry_px"], "mark": px, "unrealized_usd": unreal,
                "stop": pos["stop"], "target": pos.get("target"), "dist_to_stop_pct": (px - pos["stop"]) / px * 100 * pos["dir"] if px else None,
                "dist_to_target_pct": (pos["target"] - px) / px * 100 * pos["dir"] if (px and pos.get("target")) else None, "bars_held": pos.get("bars_held", 0), "opened": pos.get("entry_ts")}
    return {"equity": eq, "equity_incl_unrealized": eq + unreal, "hwm": state["hwm"], "drawdown_usd": state["hwm"] - eq, "day_pnl_usd": state.get("day_pnl", 0.0),
            "trades_today": state.get("trades_today", 0), "paused": state.get("paused", False), "halted_today": state.get("halted_today", False), "killed": state.get("killed", False),
            "goal": goal, "position": posd}
