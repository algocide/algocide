"""Daily-bar backtester for multi-day strategies (phase 3). Two modes:
  * portfolio: equal-$ positions, up to max_positions concurrent, no leverage (edge measurement);
  * account:   $100 single-position rules (risk $1 incl. costs, gross <= 2x equity, rounding, $10 minimum, $10 pause).
Conventions: signals are computed on completed daily bars (index t); entries fill at the next bar's open; stops are
checked on each bar's high/low (stop first; an open beyond the stop fills at the open with an adverse penalty); rule
exits fill at the next open. Funding accrues per calendar day held (measured mean or actual hourly table)."""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from .indicators import ema, sma, rolling_std, rolling_max, rolling_min, atr
from .specs import round_size, round_price


def rsi(c, n=2):
    d = np.diff(c, prepend=np.nan); up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    au = ema(np.nan_to_num(up), n); ad = ema(np.nan_to_num(dn), n)
    out = 100 - 100 / (1 + au / np.where(ad == 0, np.nan, ad)); out[:n] = np.nan; out[np.isnan(out) & (au > 0) & (ad == 0)] = 100.0
    return out


def features(df: pd.DataFrame) -> dict:
    c, h, l = df.c.values.astype(float), df.h.values.astype(float), df.l.values.astype(float)
    f = {"c": c, "h": h, "l": l, "o": df.o.values.astype(float), "sma200": sma(c, 200), "sma50": sma(c, 50), "sma20": sma(c, 20), "sma5": sma(c, 5), "atr": atr(h, l, c, 14), "rsi2": rsi(c, 2)}
    sd = rolling_std(c, 20); f["bb_lo20"] = f["sma20"] - 2.0 * sd; f["bb_lo25"] = f["sma20"] - 2.5 * sd; f["bb_hi20"] = f["sma20"] + 2.0 * sd; f["bb_hi25"] = f["sma20"] + 2.5 * sd
    f["hh20"] = np.r_[np.nan, rolling_max(h, 20)[:-1]]; f["hh55"] = np.r_[np.nan, rolling_max(h, 55)[:-1]]
    f["ret60"] = np.r_[[np.nan] * 60, c[60:] / c[:-60] - 1]; f["ret120"] = np.r_[[np.nan] * 120, c[120:] / c[:-120] - 1]
    prev_h = np.r_[np.nan, h[:-1]]; f["prev_h"] = prev_h
    d1 = np.r_[np.nan, np.diff(c)]; f["down3"] = np.array([i >= 3 and d1[i] < 0 and d1[i - 1] < 0 and d1[i - 2] < 0 for i in range(len(c))])
    return f


# ---- strategy rule functions: return (dir, stop_px, strength, exit_fn) or None; exit_fn(f, i, pos) -> bool ----
def mr_rsi2(th=10, max_bars=10):
    def sig(f, i):
        if not (np.isfinite(f["sma200"][i]) and np.isfinite(f["rsi2"][i]) and np.isfinite(f["atr"][i])): return None
        if f["rsi2"][i] < th and f["c"][i] > f["sma200"][i]: return (1, f["c"][i] - 2 * f["atr"][i], -f["rsi2"][i])
        return None
    def ex(f, i, pos): return f["c"][i] > f["sma5"][i] or (i - pos["entry_i"]) >= max_bars
    return sig, ex, f"MR-RSI2(th={th})"


def mr_short_rsi2(th=90, max_bars=10):
    def sig(f, i):
        if not (np.isfinite(f["sma200"][i]) and np.isfinite(f["rsi2"][i]) and np.isfinite(f["atr"][i])): return None
        if f["rsi2"][i] > th and f["c"][i] < f["sma200"][i]: return (-1, f["c"][i] + 2 * f["atr"][i], f["rsi2"][i])
        return None
    def ex(f, i, pos): return f["c"][i] < f["sma5"][i] or (i - pos["entry_i"]) >= max_bars
    return sig, ex, f"MR-SHORT-RSI2(th={th})"


def mr_bb(k=2.0, max_bars=10):
    key = "bb_lo20" if k == 2.0 else "bb_lo25"
    def sig(f, i):
        if not (np.isfinite(f["sma200"][i]) and np.isfinite(f[key][i]) and np.isfinite(f["atr"][i])): return None
        if f["c"][i] < f[key][i] and f["c"][i] > f["sma200"][i]: return (1, f["c"][i] - 2 * f["atr"][i], (f[key][i] - f["c"][i]) / f["atr"][i])
        return None
    def ex(f, i, pos): return f["c"][i] >= f["sma20"][i] or (i - pos["entry_i"]) >= max_bars
    return sig, ex, f"MR-BB(k={k})"


def mr_3down(max_bars=5):
    def sig(f, i):
        if not (np.isfinite(f["sma200"][i]) and np.isfinite(f["atr"][i])): return None
        if f["down3"][i] and f["c"][i] > f["sma200"][i]: return (1, f["c"][i] - 2 * f["atr"][i], (f["c"][i - 3] - f["c"][i]) / f["atr"][i])
        return None
    def ex(f, i, pos): return f["c"][i] > f["prev_h"][i] or (i - pos["entry_i"]) >= max_bars
    return sig, ex, "MR-3DOWN"


def breakout(n=20):
    key = "hh20" if n == 20 else "hh55"
    def sig(f, i):
        if not (np.isfinite(f["sma200"][i]) and np.isfinite(f[key][i]) and np.isfinite(f["atr"][i])): return None
        if f["c"][i] > f[key][i] and f["c"][i] > f["sma200"][i]: return (1, f["c"][i] - 3 * f["atr"][i], (f["c"][i] - f[key][i]) / f["atr"][i])
        return None
    def ex(f, i, pos):   # trailing 3 ATR from the highest close since entry
        pos["best"] = max(pos.get("best", f["c"][pos["entry_i"]]), f["c"][i]); pos["stop"] = max(pos["stop"], pos["best"] - 3 * f["atr"][i]); return False
    return sig, ex, f"BO({n})"


def momentum_rs(lookback=60, top=5, hold=20):
    key = "ret60" if lookback == 60 else "ret120"
    def sig(f, i):   # candidate flag; ranking happens in the engine via 'strength'
        if not (np.isfinite(f["sma50"][i]) and np.isfinite(f[key][i]) and np.isfinite(f["atr"][i])): return None
        if f["c"][i] > f["sma50"][i] and f[key][i] > 0: return (1, f["c"][i] - 2 * f["atr"][i], f[key][i])
        return None
    def ex(f, i, pos): return (i - pos["entry_i"]) >= hold
    return sig, ex, f"MOM-RS({lookback},top{top})"


STRATEGIES = {"MR-RSI2(th=10)": lambda: mr_rsi2(10), "MR-RSI2(th=5)": lambda: mr_rsi2(5), "MR-BB(k=2.0)": lambda: mr_bb(2.0), "MR-BB(k=2.5)": lambda: mr_bb(2.5), "MR-3DOWN": mr_3down,
              "MR-SHORT-RSI2(th=90)": lambda: mr_short_rsi2(90), "MOM-RS(60,top5)": lambda: momentum_rs(60), "MOM-RS(120,top5)": lambda: momentum_rs(120), "BO(20)": lambda: breakout(20), "BO(55)": lambda: breakout(55)}


class DailyCosts:
    def __init__(self, fee=0.00009, half_spread_bps=1.0, slip_bps=1.0, adverse_stop_bps=2.0, funding_bps_per_day=1.5, funding_tables=None):
        self.fee, self.hs, self.slip, self.adv, self.fund_bps, self.tables = fee, half_spread_bps, slip_bps, adverse_stop_bps, funding_bps_per_day, funding_tables or {}
    def funding(self, sym, dr, notional, t_from, t_to):
        f = self.tables.get(sym)
        a = pd.Timestamp(t_from); b = pd.Timestamp(t_to)
        a = a.tz_localize("UTC") if a.tzinfo is None else a.tz_convert("UTC"); b = b.tz_localize("UTC") if b.tzinfo is None else b.tz_convert("UTC")
        if f is not None and len(f) and f.ts.min() <= a:
            r = f[(f.ts > a) & (f.ts <= b)].rate.sum(); return -dr * r * notional
        days = max((t_to - t_from).days, 0); return -dr * self.fund_bps / 1e4 * days * notional


def run_daily(panel: dict, strategy_name: str, mode="portfolio", costs: DailyCosts | None = None, start=None, end=None, max_positions=5, equity0=100.0,
              risk_usd=1.0, max_lev=2.0, dd_pause=10.0, specs=None, top=5, rebalance_every=5):
    """panel: {sym: DataFrame(date, o, h, l, c)} on a common calendar (missing days = NaN rows). Returns trades DataFrame."""
    sig_fn, exit_fn, label = STRATEGIES[strategy_name]()
    costs = costs or DailyCosts(); syms = sorted(panel); F = {s: features(panel[s]) for s in syms}; dates = panel[syms[0]].date.values
    n = len(dates); i0 = 0 if start is None else int(np.searchsorted(dates, np.datetime64(pd.Timestamp(start)))); i1 = n if end is None else int(np.searchsorted(dates, np.datetime64(pd.Timestamp(end))))
    positions = {}; trades = []; equity = equity0; hwm = equity0; paused = False; shadow = equity0; is_mom = strategy_name.startswith("MOM")
    pending = []   # entries decided at close i, filled at open i+1
    for i in range(i0, i1):
        d = pd.Timestamp(dates[i])
        # 1) fill pending entries at today's open
        for p in pending:
            s = p["sym"]; f = F[s]; o = f["o"][i]
            if not np.isfinite(o): continue
            px = o; eff = px + p["dir"] * px * (costs.hs + costs.slip) / 1e4
            if mode == "account":
                eq_for = shadow if paused else equity; stop_dist = abs(px - p["stop"]); unit_cost = 2 * px * (costs.fee + (costs.hs + costs.slip) / 1e4) + px * costs.adv / 1e4
                qty = risk_usd / (stop_dist + unit_cost); qty = min(qty, max_lev * eq_for / px)
                szd, minord = (specs or {}).get(s, (3, 10.0)); qty = round_size(qty, szd)
                if qty <= 0 or qty * px < minord: continue
            else:
                qty = (equity0 / max_positions) / px
            positions[s] = {"sym": s, "dir": p["dir"], "qty": qty, "entry_i": i, "entry_ts": d, "entry_px": px, "entry_eff": eff, "entry_fee": costs.fee * eff * qty, "stop": p["stop"], "notional": qty * px, "shadow_only": paused and mode == "account", "strength": p["strength"], "planned_risk": risk_usd}
        pending = []
        # 2) manage open positions on today's bar
        for s in list(positions):
            pos = positions[s]; f = F[s]; o, h, l, c = f["o"][i], f["h"][i], f["l"][i], f["c"][i]
            if not np.isfinite(c): continue
            reason = None; fill = None; stop_fill = False
            if i > pos["entry_i"] or True:
                if pos["dir"] == 1 and o <= pos["stop"]: reason, fill, stop_fill = "stop", o, True
                elif pos["dir"] == 1 and l <= pos["stop"]: reason, fill, stop_fill = "stop", pos["stop"], True
                elif pos["dir"] == -1 and o >= pos["stop"]: reason, fill, stop_fill = "stop", o, True
                elif pos["dir"] == -1 and h >= pos["stop"]: reason, fill, stop_fill = "stop", pos["stop"], True
            if reason is None and i > pos["entry_i"] and exit_fn(f, i, pos) and i + 1 < n and np.isfinite(f["o"][i + 1]):
                reason, fill = "rule", f["o"][i + 1]; pos["exit_next_open"] = True
            if reason is None and i == i1 - 1: reason, fill = "end", c
            if reason:
                t_exit = pd.Timestamp(dates[min(i + 1, n - 1)]) if pos.get("exit_next_open") else d
                eff = fill - pos["dir"] * fill * (costs.hs + costs.slip) / 1e4 - (pos["dir"] * fill * costs.adv / 1e4 if stop_fill else 0)
                fee = costs.fee * eff * pos["qty"]; fund = costs.funding(s, pos["dir"], pos["notional"], pos["entry_ts"], t_exit)
                gross = pos["dir"] * (eff - pos["entry_eff"]) * pos["qty"]; net = gross - fee - pos["entry_fee"] + fund
                rec = {**pos, "exit_ts": t_exit, "exit_px": fill, "net_pnl": net, "gross_pnl": gross, "funding": fund, "reason": reason, "hold_days": (t_exit - pos["entry_ts"]).days,
                       "cost_total": fee + pos["entry_fee"] + abs(eff - fill) * pos["qty"] + abs(pos["entry_eff"] - pos["entry_px"]) * pos["qty"], "r_multiple": net / risk_usd, "hold_min": (t_exit - pos["entry_ts"]).total_seconds() / 60,
                       "leverage": pos["notional"] / equity0, "counted": not pos["shadow_only"], "label": label}
                if not pos["shadow_only"]: equity += net
                shadow += net; trades.append(rec); del positions[s]
        if mode == "account":
            hwm = max(hwm, equity)
            if not paused and equity < hwm - dd_pause: paused = True
        # 3) signals at today's close -> pending entries at tomorrow's open
        cands = []
        for s in syms:
            if s in positions: continue
            f = F[s]
            if not np.isfinite(f["c"][i]) or i + 1 >= n or not np.isfinite(f["o"][i + 1]): continue
            r = sig_fn(f, i)
            if r: cands.append({"sym": s, "dir": r[0], "stop": r[1], "strength": r[2]})
        if is_mom and (i % rebalance_every) != 0: cands = []
        cands.sort(key=lambda x: (-x["strength"], x["sym"]))
        cap = (1 - len(positions)) if mode == "account" else (max_positions - len(positions))
        if is_mom: cap = min(cap, top)
        pending = cands[:max(cap, 0)]
    return pd.DataFrame(trades)
