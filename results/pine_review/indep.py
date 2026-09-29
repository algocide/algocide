#!/usr/bin/env python3
"""Independent re-implementations of four vault scripts, written from their Pine source without pinebt's
compiler/runtime/broker. Bars are resampled here from the raw 1-minute parquet; indicators, signals, fills, sizing,
fees and funding are coded directly.

Rules reproduced (from the task brief / pre-registration):
  * bars labelled by open time (UTC), close = last minute's close; the run uses the most recent 200,000 bars that
    close on or before END_MS.
  * every opening order is sized at 100% of current equity (pyramiding = 1 in all four scripts); fee 7 bps of notional
    per fill; funding charged at each bar close on the position held then: cash -= qty * close * sum(rates in bar).
  * market orders fill at the next bar's open, or at the same bar's close with process_orders_on_close.
  * stop/limit exits are checked against the 1-minute candles of the bar, each minute walked
    open -> nearer extreme -> farther extreme -> close; a level already crossed at a minute's open fills at that open.

Usage: python3 results/pine_review/indep.py   (writes compare_*.txt next to this file)
"""
from __future__ import annotations
import math
import os
import pickle
import sys
from functools import lru_cache

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "data", "binance")
END_MS = 1790640000000
MAX_BARS = 200_000
FEE = 0.0007
DAY = 86_400_000


# ------------------------------------------------------------------------------------------------ data
@lru_cache(maxsize=4)
def minutes(sym):
    df = pd.read_parquet(os.path.join(DATA, f"{sym}_1m.parquet"))
    return (df.open_time.to_numpy(np.int64), df.open.to_numpy(float), df.high.to_numpy(float),
            df.low.to_numpy(float), df.close.to_numpy(float), df.volume.to_numpy(float))


@lru_cache(maxsize=8)
def bars(sym, tf):
    """Resample with pandas groupby (a different code path from pinebt.data.resample)."""
    t, o, h, l, c, v = minutes(sym)
    df = pd.DataFrame({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})
    df["b"] = (df.t // tf) * tf
    g = df.groupby("b", sort=True)
    B = pd.DataFrame({"o": g.o.first(), "h": g.h.max(), "l": g.l.min(), "c": g.c.last(), "v": g.v.sum(),
                      "m0": g.t.min(), "nmin": g.t.size()})
    B.index.name = "T"
    B = B.reset_index()
    B["TC"] = B["T"] + tf
    B = B[B.TC <= END_MS].tail(MAX_BARS).reset_index(drop=True)
    # funding: every settlement whose timestamp falls in [T, T + tf) is charged at that bar's close
    f = pd.read_parquet(os.path.join(DATA, f"{sym}_funding.parquet"))
    ft, fr = f.time.to_numpy(np.int64), f.rate.to_numpy(float)
    # unpublished tail (the engine's documented rule, ledger item 28): mean of the last 30 days at 8h settlements
    last = ft[-1]
    m = fr[ft > last - 30 * DAY].mean()
    full_end = (t[-1] // tf) * tf + tf           # close of the last (possibly partial) bar in the raw data
    extra = np.arange(last + 8 * 3_600_000, full_end, 8 * 3_600_000)
    ft = np.r_[ft, extra]
    fr = np.r_[fr, np.full(len(extra), m)]
    fund = np.zeros(len(B))
    T = B["T"].to_numpy(np.int64)
    for tt, rr in zip(ft, fr):
        k = (tt // tf) * tf
        j = np.searchsorted(T, k)
        if j < len(T) and T[j] == k:
            fund[j] += rr
    B["fund"] = fund
    return B


def minute_slices(sym, B):
    t = minutes(sym)[0]
    a = np.searchsorted(t, B["T"].to_numpy(np.int64), "left")
    b = np.searchsorted(t, B["TC"].to_numpy(np.int64), "left")
    return a, b


# ------------------------------------------------------------------------------------------------ indicators
def rma(x, n):
    """Wilder: seeded with the SMA of the first n non-na values, then (x + (n-1) * prev) / n."""
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    start = np.flatnonzero(~np.isnan(x))[0]
    if len(x) - start < n:
        return out
    v = x[start:start + n].mean()
    out[start + n - 1] = v
    for k in range(start + n, len(x)):
        v = (x[k] + (n - 1) * v) / n
        out[k] = v
    return out


def ema_first(x, n):
    """ta.ema seeded with the first value (pine_ema in the reference manual)."""
    a = 2.0 / (n + 1)
    out = np.empty(len(x))
    v = np.nan
    for k, xv in enumerate(x):
        v = xv if v != v else a * xv + (1 - a) * v
        out[k] = v
    return out


def ema_sma(x, n):
    """ta.ema seeded with the SMA of the first n values (na before)."""
    a = 2.0 / (n + 1)
    out = np.full(len(x), np.nan)
    v = float(np.mean(x[:n]))
    out[n - 1] = v
    for k in range(n, len(x)):
        v = a * x[k] + (1 - a) * v
        out[k] = v
    return out


def atr(h, l, c, n):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    tr[0] = h[0] - l[0]
    return rma(tr, n)


def rsi(c, n):
    ch = np.r_[np.nan, np.diff(c)]
    up = np.where(np.isnan(ch), np.nan, np.maximum(ch, 0.0))
    dn = np.where(np.isnan(ch), np.nan, np.maximum(-ch, 0.0))
    u, d = rma(up, n), rma(dn, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + u / d)
    r = np.where(d == 0, 100.0, r)
    r = np.where((u == 0) & (d != 0), 0.0, r)
    r[np.isnan(u) | np.isnan(d)] = np.nan
    return r


def sma(x, n):
    return pd.Series(x).rolling(n).mean().to_numpy()


def pivot(x, left, right, high=True):
    """ta.pivothigh/low confirmed `right` bars later: value at bar k - right, na otherwise.
    Left neighbours strictly lower (higher), right neighbours lower-or-equal (higher-or-equal)."""
    n = len(x)
    out = np.full(n, np.nan)
    for k in range(left + right, n):
        cidx = k - right
        pv = x[cidx]
        L_ = x[cidx - left:cidx]
        R_ = x[cidx + 1:k + 1]
        if high:
            ok = np.all(L_ < pv) and np.all(R_ <= pv)
        else:
            ok = np.all(L_ > pv) and np.all(R_ >= pv)
        if ok:
            out[k] = pv
    return out


# ------------------------------------------------------------------------------------------------ account
class Acct:
    def __init__(self, cap=10000.0, fee=FEE):
        self.cash, self.fee = cap, fee
        self.q = 0.0            # signed units
        self.px = np.nan
        self.efee = 0.0
        self.et = None
        self.eid = None
        self.trades = []        # (eid, entry_t, exit_t, q, entry_px, exit_px, net)

    def equity(self, px):
        return self.cash + (self.q * (px - self.px) if self.q else 0.0)

    def open(self, eid, d, px, t):
        eq = self.equity(px)
        q = eq / px
        f = q * px * self.fee
        self.cash -= f
        self.q, self.px, self.efee, self.et, self.eid = d * q, px, f, t, eid

    def close(self, px, t):
        if not self.q:
            return
        pnl = self.q * (px - self.px)
        f = abs(self.q) * px * self.fee
        self.cash += pnl - f
        self.trades.append((self.eid, self.et, t, self.q, self.px, px, pnl - f - self.efee))
        self.q, self.px, self.efee, self.et, self.eid = 0.0, np.nan, 0.0, None, None

    def fund(self, close, rate):
        if self.q and rate:
            self.cash -= self.q * close * rate


# ------------------------------------------------------------------------------------------------ strategies
def s_atr_trail(sym="BTCUSDT"):
    """Dynamic-ATR-Trailing-Stop (1d): stop-and-reverse on crosses of an ATR 'trailing stop' (a=1, ATR 10)."""
    B = bars(sym, DAY)
    o, h, l, c = (B[k].to_numpy() for k in "ohlc")
    T = B["T"].to_numpy()
    nloss = 1 * atr(h, l, c, 10)
    n = len(c)
    stop = np.full(n, np.nan)
    for i in range(n):
        prev = stop[i - 1] if i > 0 else np.nan
        prevz = 0.0 if prev != prev else prev
        stop[i] = c[i] - nloss[i] if c[i] > prevz else c[i] + nloss[i]
    buy = np.zeros(n, bool); sell = np.zeros(n, bool)
    for i in range(1, n):
        buy[i] = c[i] > stop[i] and c[i - 1] <= stop[i - 1]
        sell[i] = c[i] < stop[i] and c[i - 1] >= stop[i - 1]
    A = Acct()
    eq = np.empty(n)
    pend = None
    for i in range(n):
        if pend == "buy":                  # close("Sell") then entry("Buy")
            if A.q < 0:
                A.close(o[i], T[i])
            if A.q == 0:
                A.open("Buy", 1, o[i], T[i])
        elif pend == "sell":
            if A.q > 0:
                A.close(o[i], T[i])
            if A.q == 0:
                A.open("Sell", -1, o[i], T[i])
        pend = None
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        if buy[i]:
            pend = "buy"
        elif sell[i]:
            pend = "sell"
    return B, A, eq


def s_rsi2(sym="BTCUSDT"):
    """RSI2 breakout (1d): buy when rsi[1] < 25 and rsi rising, flat, close > SMA50; close 4 bars after the signal."""
    B = bars(sym, DAY)
    o, c = B.o.to_numpy(), B.c.to_numpy()
    T = B["T"].to_numpy()
    r = rsi(c, 2)
    ma = sma(c, 50)
    n = len(c)
    A = Acct()
    eq = np.empty(n)
    held = None
    pend = None
    for i in range(n):
        if pend == "buy" and A.q == 0:
            A.open("Buy", 1, o[i], T[i])
        elif pend == "close" and A.q > 0:
            A.close(o[i], T[i])
        pend = None
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        pos0 = A.q == 0
        cond = (i > 0 and r[i - 1] < 25 and r[i] > r[i - 1] and pos0 and c[i] > ma[i])
        if cond and held is None:
            pend = "buy"
            held = 0
        if held is not None:
            held += 1
        if held is not None and held >= 5:
            pend = "close" if pend is None else pend
            held = None
    return B, A, eq


def walk_minute(o, h, l, c, orders):
    """orders: list of (name, level, kind) with kind in {'rise', 'fall'}: 'rise' triggers when price reaches the level
    from below (buy stop / sell limit), 'fall' from above. Returns (name, fill_px) of the first trigger or None."""
    path = (o, h, l, c) if (h - o) <= (o - l) else (o, l, h, c)
    # gap / already-crossed check at the minute's open
    for name, lvl, kind in orders:
        if (kind == "rise" and o >= lvl) or (kind == "fall" and o <= lvl):
            return name, o
    for s in range(3):
        p0, p1 = path[s], path[s + 1]
        best = None
        for name, lvl, kind in orders:
            if kind == "rise" and p1 > p0 and p0 < lvl <= p1:
                d = lvl - p0
            elif kind == "fall" and p1 < p0 and p0 > lvl >= p1:
                d = p0 - lvl
            else:
                continue
            if best is None or d < best[0]:
                best = (d, name, lvl)
        if best is not None:
            return best[1], best[2]
    return None


def s_pivot(sym="BTCUSDT"):
    """Pivot S/R breakout/reversal (1d) with TP 3% / SL 2% exits re-issued each bar from that bar's close."""
    B = bars(sym, DAY)
    o, h, l, c, v = (B[k].to_numpy() for k in "ohlcv")
    T = B["T"].to_numpy()
    n = len(c)
    ph = pivot(h, 10, 10, True)
    pl = pivot(l, 10, 10, False)
    res = np.full(n, np.nan); sup = np.full(n, np.nan)
    rz = sz = np.nan
    for i in range(n):
        if ph[i] == ph[i]:
            rz = ph[i]
        if pl[i] == pl[i]:
            sz = pl[i]
        res[i], sup[i] = rz, sz
    vs = sma(v, 20)
    hv = v > vs * 1.5
    with np.errstate(invalid="ignore"):
        longc = ((c > res * 1.01) & hv) | ((c >= sup * 0.99) & (c <= sup * 1.01) & (l <= sup) & (c > sup) & hv)
        shortc = ((c < sup * 0.99) & hv) | ((c >= res * 0.99) & (c <= res * 1.01) & (h >= res) & (c < res) & hv)
    mt, mo, mh, ml, mc, _ = minutes(sym)
    ma_, mb_ = minute_slices(sym, B)
    A = Acct()
    eq = np.empty(n)
    pend = []          # market entries queued at the previous close, in script order
    xl = xs = None     # exit levels issued at the previous close: (tp, sl)
    for i in range(n):
        for d in pend:
            if (A.q > 0 and d == 1) or (A.q < 0 and d == -1):
                continue                      # pyramiding 1: same-direction entry ignored
            if A.q != 0:
                A.close(o[i], T[i])           # reversal
            A.open("Long" if d == 1 else "Short", d, o[i], T[i])
        pend = []
        if A.q != 0:
            lv = xl if A.q > 0 else xs
            # the exit applies only if it was issued at/after the entry order (always true here: re-issued each bar)
            if lv is not None:
                tp, sl = lv
                orders = [("tp", tp, "rise"), ("sl", sl, "fall")] if A.q > 0 else [("tp", tp, "fall"), ("sl", sl, "rise")]
                for m in range(ma_[i], mb_[i]):
                    hit = walk_minute(mo[m], mh[m], ml[m], mc[m], orders)
                    if hit is not None:
                        A.close(hit[1], T[i])
                        break
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        if longc[i]:
            pend.append(1)
        if shortc[i]:
            pend.append(-1)
        xl = (c[i] * 1.03, c[i] * 0.98)
        xs = (c[i] * 0.97, c[i] * 1.02)
    return B, A, eq


def s_multitrend(sym="BTCUSDT", seed="first"):
    """Multi Trend Cross (3h, process_orders_on_close): long when EMA10 > EMA200, flat when EMA10 < EMA200."""
    B = bars(sym, 3 * 3_600_000)
    c = B.c.to_numpy()
    T, TC = B["T"].to_numpy(), B["TC"].to_numpy()
    e = ema_first if seed == "first" else ema_sma
    f, s = e(c, 10), e(c, 200)
    n = len(c)
    A = Acct(cap=1_000_000.0)
    eq = np.empty(n)
    for i in range(n):
        A.fund(c[i], B.fund[i])
        if f[i] > s[i] and A.q == 0:
            A.open("Long", 1, c[i], T[i])
        elif f[i] < s[i] and A.q > 0:
            A.close(c[i], T[i])
        eq[i] = A.equity(c[i])
    return B, A, eq


# ------------------------------------------------------------------------------------------------ comparison
def load_engine(file, sym="BTCUSDT", tag="mag"):
    p = os.path.join(HERE, "engine_runs", f"{file[:-3]}__{sym}__{tag}.pkl")
    with open(p, "rb") as f:
        return pickle.load(f)


def compare(name, file, fn, **kw):
    B, A, eq = fn(**kw)
    r = load_engine(file)
    lines = [f"== {name}: {file}"]
    eT = np.asarray(r["T"], np.int64)
    lines.append(f"bars: mine {len(B)} ({pd.to_datetime(B['T'].iloc[0], unit='ms')} .. "
                 f"{pd.to_datetime(B['T'].iloc[-1], unit='ms')}), engine {len(eT)}; "
                 f"same open times: {len(B) == len(eT) and bool((B['T'].to_numpy() == eT).all())}")
    ee = np.asarray(r["equity"], float)
    if len(ee) == len(eq):
        d = np.abs(ee - eq) / ee
        k = int(np.argmax(d))
        lines.append(f"final equity: mine {eq[-1]:.6f}, engine {ee[-1]:.6f}; max rel diff {d.max():.3e} at bar {k} "
                     f"({pd.to_datetime(B['T'].iloc[k], unit='ms')})")
    et = [(x[0], x[3], x[4], x[5], x[6], x[7], x[8]) for x in r["closed"]]
    mine = A.trades
    lines.append(f"closed trades: mine {len(mine)}, engine {len(et)}")
    nm = 0
    for k in range(max(len(mine), len(et))):
        a = mine[k] if k < len(mine) else None
        b = et[k] if k < len(et) else None
        same = (a is not None and b is not None and a[0] == b[0] and a[1] == b[1] and a[2] == b[2]
                and abs(a[3] - b[3]) <= 1e-9 * abs(b[3]) and abs(a[4] - b[4]) < 1e-9 and abs(a[5] - b[5]) < 1e-6)
        if not same:
            nm += 1
            if nm <= 12:
                fmt = lambda z: "None" if z is None else (f"{z[0]} {pd.to_datetime(z[1], unit='ms'):%Y-%m-%d %H:%M} -> "
                                                        f"{pd.to_datetime(z[2], unit='ms'):%Y-%m-%d %H:%M} q={z[3]:.6f} "
                                                        f"{z[4]:.2f} -> {z[5]:.4f} net={z[6]:.2f}")
                lines.append(f"  MISMATCH #{k}: mine   {fmt(a)}")
                lines.append(f"               engine {fmt(b)}")
    lines.append(f"trade mismatches: {nm}")
    return "\n".join(lines), (B, A, eq, r)


if __name__ == "__main__":
    jobs = [
        ("a", "Dynamic-ATR-Trailing-Stop-Trading-Strategy-Market-Volatility-Adaptive-System.md", s_atr_trail, {}),
        ("b", "RSI2-Based-Dynamic-Breakout-Trading-Strategy-with-Moving-Average-Filter-System.md", s_rsi2, {}),
        ("c", "Pivot-Based-Volume-Weighted-Breakout-Reversal-Strategy.md", s_pivot, {}),
        ("d", "Multi-Trend-Crossover-Strategy.md", s_multitrend, {}),
    ]
    only = sys.argv[1:]
    for tag, f, fn, kw in jobs:
        if only and tag not in only:
            continue
        txt, _ = compare(tag, f, fn, **kw)
        print(txt)
        with open(os.path.join(HERE, f"compare_{tag}.txt"), "w") as fo:
            fo.write(txt + "\n")


def s_staged(sym="BTCUSDT", mode="engine"):
    """Dual-EMA-Trend-Following-Strategy-with-Staged-Position-Exit (1d). Entries use qty=0.02 lots; partial exit
    strategy.close(qty=0.01) every bar while close >= entryPrice + 200 ticks; full close on the opposite cross.
    mode='engine': absolute 0.01 units against the tournament's 100%-of-equity position (what pinebt does);
    mode='fraction': the script's own units rescaled - each close(qty=0.01) takes 0.01/0.02 of the entry size, capped
    at what is left (what the script does on TradingView, with the pre-registered 100%-of-equity entry size)."""
    B = bars(sym, DAY)
    o, c = B.o.to_numpy(), B.c.to_numpy()
    T = B["T"].to_numpy()
    tick = 0.1 if sym == "BTCUSDT" else 0.01
    f, s = ema_first(c, 9), ema_first(c, 21)
    n = len(c)
    trades = []
    cash, fee = 10000.0, FEE
    pos = []          # list of [q, px, efee_remaining, et, eid, q0]
    eq = np.empty(n)
    in_trade, is_long, entry_px = False, False, np.nan
    queue = []

    def equity(px):
        return cash + sum(p[0] * (px - p[1]) for p in pos)

    def close_units(eid, units, px, t):
        nonlocal cash
        for p in list(pos):
            if p[4] != eid or units <= 1e-15:
                continue
            take = min(abs(p[0]), units)
            sg = 1 if p[0] > 0 else -1
            pnl = sg * take * (px - p[1])
            fx = take * px * fee
            share = take / abs(p[0])
            ef = p[2] * share
            p[2] -= ef
            cash += pnl - fx
            trades.append((eid, p[3], t, sg * take, p[1], px, pnl - fx - ef))
            p[0] -= sg * take
            units -= take
            if abs(p[0]) <= 1e-12:
                pos.remove(p)

    for i in range(n):
        for q in queue:
            kind, eid, arg = q
            if kind == "entry":
                d = arg
                cur = sum(p[0] for p in pos)
                if cur != 0 and (cur > 0) != (d > 0):
                    for p in list(pos):
                        close_units(p[4], abs(p[0]), o[i], T[i])
                if not any((p[0] > 0) == (d > 0) for p in pos):
                    e = equity(o[i]); u = e / o[i]; fx = u * o[i] * fee
                    cash -= fx
                    pos.append([d * u, o[i], fx, T[i], eid, u])
            elif kind == "close":
                have = sum(abs(p[0]) for p in pos if p[4] == eid)
                if arg is None:
                    close_units(eid, have, o[i], T[i])
                elif mode == "engine":
                    close_units(eid, min(arg, have), o[i], T[i])
                else:
                    q0 = sum(p[5] for p in pos if p[4] == eid)
                    close_units(eid, min(q0 * arg / 0.02, have), o[i], T[i])
        queue = []
        # funding at bar close
        cur = sum(p[0] for p in pos)
        if cur and B.fund[i]:
            cash -= cur * c[i] * B.fund[i]
        eq[i] = equity(c[i])
        le = i > 0 and f[i] > s[i] and f[i - 1] <= s[i - 1]
        se = i > 0 and f[i] < s[i] and f[i - 1] >= s[i - 1]
        if le and not in_trade:
            queue.append(("entry", "Long", 1)); entry_px = c[i]; in_trade = True; is_long = True
        if se and not in_trade:
            queue.append(("entry", "Short", -1)); entry_px = c[i]; in_trade = True; is_long = False
        if is_long and in_trade and c[i] >= entry_px + 200 * tick:
            queue.append(("close", "Long", 0.01))
        if (not is_long) and in_trade and c[i] <= entry_px - 200 * tick:
            queue.append(("close", "Short", 0.01))
        if is_long and se:
            queue.append(("close", "Long", None)); in_trade = False
        if (not is_long) and le:
            queue.append(("close", "Short", None)); in_trade = False

    class R:
        pass
    A = R(); A.trades = trades
    return B, A, eq


def s_mtf_ha(sym="BTCUSDT"):
    """Multi-Timeframe-Heikin-Ashi-MA (1d chart): fast = the 3h Heikin-Ashi close (script-computed HA on 3h bars),
    taken from the last 3h bar that has closed by the daily close, lagged one day; slow = EMA30 of the daily HA close
    lagged one day; stop-and-reverse on crosses. Checks the lower-timeframe request.security mapping."""
    D = bars(sym, DAY)
    H3 = bars(sym, 3 * 3_600_000)
    # script HA on the 3h series
    o3, h3, l3, c3 = (H3[k].to_numpy() for k in "ohlc")
    hc3 = (o3 + h3 + l3 + c3) / 4
    ho3 = np.empty(len(hc3)); ho3[0] = (o3[0] + c3[0]) / 2
    for k in range(1, len(hc3)):
        ho3[k] = (ho3[k - 1] + hc3[k - 1]) / 2
    # visible at daily bar i: last 3h bar with close time <= daily close time
    tc3 = H3.TC.to_numpy()
    j = np.searchsorted(tc3, D.TC.to_numpy(), side="right") - 1
    mha = np.where(j >= 0, hc3[np.clip(j, 0, None)], np.nan)
    o, h, l, c = (D[k].to_numpy() for k in "ohlc")
    hcd = (o + h + l + c) / 4
    n = len(c)
    fma = np.r_[np.nan, mha[:-1]]                      # ema(mha_close[1], 1) == mha_close[1]
    slow_src = np.r_[np.nan, hcd[:-1]]
    sma_ = np.full(n, np.nan); v = np.nan; a = 2 / 31
    for k in range(n):
        x = slow_src[k]
        v = x if v != v else a * x + (1 - a) * v
        sma_[k] = v
    T = D["T"].to_numpy()
    A = Acct(); eq = np.empty(n); pend = None
    for i in range(n):
        if pend is not None:
            d = pend
            if A.q != 0 and (A.q > 0) != (d > 0):
                A.close(o[i], T[i])
            if A.q == 0:
                A.open("Long" if d > 0 else "Short", d, o[i], T[i])
        pend = None
        A.fund(c[i], D.fund[i])
        eq[i] = A.equity(c[i])
        if i > 0:
            if fma[i] > sma_[i] and fma[i - 1] <= sma_[i - 1]:
                pend = 1
            elif fma[i] < sma_[i] and fma[i - 1] >= sma_[i - 1]:
                pend = -1
    return D, A, eq


def s_ribbon(sym="BTCUSDT"):
    """Moving-Average-Ribbon (1d): SMA20 of ohlc4, channel = highest/lowest of the SMA over 20; long when close > h[1],
    short when close < l[1] (strategy.entry reverses)."""
    B = bars(sym, DAY)
    o, h, l, c = (B[k].to_numpy() for k in "ohlc")
    src = (o + h + l + c) / 4
    ma = sma(src, 20)
    hh = pd.Series(ma).rolling(20).max().to_numpy()
    ll = pd.Series(ma).rolling(20).min().to_numpy()
    n = len(c); T = B["T"].to_numpy()
    A = Acct(); eq = np.empty(n); pend = []
    for i in range(n):
        for d in pend:
            if A.q != 0 and (A.q > 0) == (d > 0):
                continue
            if A.q != 0:
                A.close(o[i], T[i])
            A.open("Long" if d > 0 else "Short", d, o[i], T[i])
        pend = []
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        if i > 0 and c[i] > hh[i - 1]:
            pend.append(1)
        if i > 0 and c[i] < ll[i - 1]:
            pend.append(-1)
    return B, A, eq


def s_breakout_zone(sym="BTCUSDT"):
    """Breakout-Zone-Momentum (1d): long on crossover(close, SMA50) if close > lowest(low,20); short on crossunder if
    close < highest(high,20); strategy.entry reverses."""
    B = bars(sym, DAY)
    o, h, l, c = (B[k].to_numpy() for k in "ohlc")
    m = sma(c, 50)
    lo = pd.Series(l).rolling(20).min().to_numpy(); hi = pd.Series(h).rolling(20).max().to_numpy()
    n = len(c); T = B["T"].to_numpy()
    A = Acct(); eq = np.empty(n); pend = []
    for i in range(n):
        for d in pend:
            if A.q != 0 and (A.q > 0) == (d > 0):
                continue
            if A.q != 0:
                A.close(o[i], T[i])
            A.open("Long" if d > 0 else "Short", d, o[i], T[i])
        pend = []
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        if i > 0:
            if c[i] > m[i] and c[i - 1] <= m[i - 1] and c[i] > lo[i]:
                pend.append(1)
            if c[i] < m[i] and c[i - 1] >= m[i - 1] and c[i] < hi[i]:
                pend.append(-1)
    return B, A, eq


def s_turnaround(sym="BTCUSDT"):
    """Turnaround-Tuesday (2h): buy on Monday bars when close[1] < SMA30[1], rsi3[1] < 51, close[1]/atr10[1] < 95, not
    May; strategy.close on Wednesday bars when long."""
    tf = 2 * 3_600_000
    B = bars(sym, tf)
    o, h, l, c = (B[k].to_numpy() for k in "ohlc")
    T = B["T"].to_numpy()
    dt = pd.to_datetime(T, unit="ms")
    dow = dt.dayofweek.to_numpy()        # Monday = 0
    month = dt.month.to_numpy()
    ma = sma(c, 30); a10 = atr(h, l, c, 10); r3 = rsi(c, 3)
    n = len(c)
    A = Acct(); eq = np.empty(n); pend = []
    for i in range(n):
        for kind in pend:
            if kind == "buy" and A.q == 0:
                A.open("Buy", 1, o[i], T[i])
            elif kind == "close" and A.q > 0:
                A.close(o[i], T[i])
        pend = []
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        if i == 0:
            continue
        with np.errstate(invalid="ignore"):
            cond = (dow[i] == 0 and c[i - 1] < ma[i - 1] and month[i] != 5 and r3[i - 1] < 51
                    and c[i - 1] / a10[i - 1] < 95)
        if cond:
            pend.append("buy")
        if dow[i] == 2 and A.q > 0:
            pend.append("close")
    return B, A, eq


def s_renko_tema(sym="BTCUSDT"):
    """Renko-Boxes-and-TEMA (1h, v2, pyramiding=100 -> each entry 1% of equity): buy when TEMA5 crosses over SMA3(TEMA)
    below SMMA30 and close <= average entry price (or flat); close everything when TEMA crosses under its SMA and
    close >= 1.02 * average price."""
    B = bars(sym, 3_600_000)
    o, c = B.o.to_numpy(), B.c.to_numpy()
    T = B["T"].to_numpy()
    e1 = ema_first(c, 5); e2 = ema_first(e1, 5); e3 = ema_first(e2, 5)
    tema = 3 * e1 - 3 * e2 + e3
    s3 = sma(tema, 3)
    sm = np.full(len(c), np.nan)
    base = sma(c, 30)
    for i in range(len(c)):
        prev = sm[i - 1] if i > 0 else np.nan
        sm[i] = base[i] if prev != prev else (prev * 29 + c[i]) / 30
    n = len(c)
    cash = 10000.0
    pos = []            # [q, px, efee, t]
    trades = []
    eq = np.empty(n)
    pend = []

    def equity(px):
        return cash + sum(q * (px - p) for q, p, _, _ in pos)

    for i in range(n):
        for kind in pend:
            if kind == "buy":
                if len(pos) < 100:
                    e = equity(o[i])
                    q = 0.01 * e / o[i]
                    room = max(0.0, e - sum(abs(x[0]) for x in pos) * o[i]) / o[i]
                    q = min(q, room)
                    if q > 1e-12:
                        fx = q * o[i] * FEE
                        cash -= fx
                        pos.append([q, o[i], fx, T[i]])
            else:
                for q, p, ef, t in pos:
                    pnl = q * (o[i] - p); fx = q * o[i] * FEE
                    cash += pnl - fx
                    trades.append(("Buy", t, T[i], q, p, o[i], pnl - fx - ef))
                pos = []
        pend = []
        if pos and B.fund[i]:
            cash -= sum(x[0] for x in pos) * c[i] * B.fund[i]
        eq[i] = equity(c[i])
        avg = (sum(x[0] * x[1] for x in pos) / sum(x[0] for x in pos)) if pos else np.nan
        if i == 0:
            continue
        lc = tema[i] > s3[i] and tema[i - 1] <= s3[i - 1] and tema[i] < sm[i]
        sc = tema[i] < s3[i] and tema[i - 1] >= s3[i - 1]
        if lc and (avg != avg or c[i] <= avg):
            pend.append("buy")
        if sc and avg == avg and c[i] >= 1.02 * avg:
            pend.append("close")

    class R:
        pass
    A = R(); A.trades = trades
    return B, A, eq


def s_golden(sym="BTCUSDT"):
    """Dual-Moving-Average-Golden-Cross-Algorithm (1d, v4): enter long on buy_signal; on sell_signal while in a
    position place strategy.exit('Buy', limit=avg*1.2) (no from_entry: covers that position until it closes)."""
    B = bars(sym, DAY)
    o, h, l, c = (B[k].to_numpy() for k in "ohlc")
    T = B["T"].to_numpy()
    n = len(c)
    e = ema_first(c, 8)
    e1 = ema_first(np.r_[np.nan, c[:-1]], 8)
    # the engine's EMA leaves na inputs alone until the first value: replicate: seed at bar 1
    e1 = np.r_[np.nan, ema_first(c[:-1], 8)]
    low_e = pd.Series(e).rolling(8, min_periods=1).min().to_numpy().copy()
    low_e[:7] = np.nan                           # window not full yet -> na (engine: _win needs n values)
    diff = c - e
    diff1 = np.r_[np.nan, c[:-1]] - e1
    diffLow = e - low_e
    A = Acct(); eq = np.empty(n)
    pend = None
    xlim = None          # active exit limit for the current position
    mt, mo, mh, ml, mc, _ = minutes(sym)
    ma_, mb_ = minute_slices(sym, B)
    for i in range(n):
        if pend == "buy" and A.q == 0:
            A.open("Buy", 1, o[i], T[i])
            xlim = None
        pend = None
        if A.q > 0 and xlim is not None:
            for m in range(ma_[i], mb_[i]):
                hit = walk_minute(mo[m], mh[m], ml[m], mc[m], [("tp", xlim, "rise")])
                if hit is not None:
                    A.close(hit[1], T[i]); xlim = None
                    break
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        if i < 2:
            continue
        with np.errstate(invalid="ignore"):
            buy = diff[i] < diffLow[i] and diff[i] > diff1[i] and diff[i - 1] <= diff1[i - 1] and diff[i] < 0
            sell = diff[i] > 0 and diff[i] < diffLow[i] and diff[i] < diff1[i] and diff[i - 1] >= diff1[i - 1]
        if buy:
            pend = "buy"
        if sell and A.q > 0:
            xlim = A.px * 1.2
    return B, A, eq


def wma_np(x, n):
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    w = np.arange(1, n + 1, dtype=float)
    for k in range(n - 1, len(x)):
        seg = x[k - n + 1:k + 1]
        out[k] = np.dot(seg, w) / w.sum()          # nan if any nan in the window
    return out


def rma_reset(x, n):
    """RMA with the seeding rule 'n consecutive non-na values -> SMA seed', re-seeding after an na."""
    out = np.full(len(x), np.nan)
    v = np.nan
    seed = []
    for k, xv in enumerate(x):
        if v != v:
            if xv != xv:
                seed = []
                continue
            seed.append(xv)
            if len(seed) > n:
                seed.pop(0)
            if len(seed) == n:
                v = sum(seed) / n
                out[k] = v
                seed = []
            continue
        v = (xv + (n - 1) * v) / n
        out[k] = v
    return out


def s_bollinger_pb(sym="BTCUSDT"):
    """Bollinger-Percentage-Bands (1d, v4): %B of open/high/low/close vs BB(100, 10 sd), each smoothed by RMA(22);
    a %B 'true range' smoothed by HMA(10) x 4 drives a chandelier-style direction; long while dir == 1, short while
    dir == -1 (strategy.entry every bar, strategy.close on the flip)."""
    B = bars(sym, DAY)
    o, h, l, c = (B[k].to_numpy() for k in "ohlc")
    T = B["T"].to_numpy()
    n = len(c)

    def pb(x):
        s = pd.Series(x)
        mid = s.rolling(100).mean(); sd = s.rolling(100).std(ddof=0)
        up, lo = mid + 10 * sd, mid - 10 * sd
        return ((s - lo) * 100 / (up - lo)).to_numpy()

    nO, nH, nL, nC = (rma_reset(pb(x), 22) for x in (o, h, l, c))
    pc = np.r_[np.nan, nC[:-1]]
    with np.errstate(invalid="ignore"):
        tr = np.where(np.isnan(nH) | np.isnan(pc) | np.isnan(nL), np.nan, np.maximum(nH, pc) - np.minimum(nL, pc))
    half = wma_np(tr, 5); full = wma_np(tr, 10)
    atr_ = wma_np(2 * half - full, 3) * 4           # hma(10): floor/round(sqrt(10)) = 3 either way
    ls = np.full(n, np.nan); ss = np.full(n, np.nan); d = np.ones(n)
    lsp_ = np.full(n, np.nan); ssp_ = np.full(n, np.nan)
    for i in range(n):
        l0 = nC[i] - atr_[i]
        lsp = ls[i - 1] if i > 0 and ls[i - 1] == ls[i - 1] else l0
        prevc = nC[i - 1] if i > 0 else np.nan
        ls[i] = max(l0, lsp) if (prevc == prevc and lsp == lsp and prevc > lsp) else l0
        s0 = nC[i] + atr_[i]
        ssp = ss[i - 1] if i > 0 and ss[i - 1] == ss[i - 1] else s0
        ss[i] = min(s0, ssp) if (prevc == prevc and ssp == ssp and prevc < ssp) else s0
        dp = d[i - 1] if i > 0 else 1
        if dp == -1 and nC[i] > ssp:
            d[i] = 1
        elif dp == 1 and nC[i] < lsp:
            d[i] = -1
        else:
            d[i] = dp
    A = Acct(cap=1000.0); eq = np.empty(n); pend = []     # the script declares initial_capital = 1000
    for i in range(n):
        for kind, arg in pend:
            if kind == "entry":
                if A.q != 0 and (A.q > 0) == (arg > 0):
                    continue
                if A.q != 0:
                    A.close(o[i], T[i])
                A.open("Buy" if arg > 0 else "Sell", arg, o[i], T[i])
            elif kind == "close":
                if A.q != 0 and A.eid == arg:
                    A.close(o[i], T[i])
        pend = []
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        if d[i] == 1:
            pend.append(("entry", 1))
        if d[i] == -1:
            pend.append(("close", "Buy"))
            pend.append(("entry", -1))
        if d[i] == 1:
            pend.append(("close", "Sell"))
    return B, A, eq


def s_vwap_rsi(sym="BTCUSDT"):
    """VWAP-and-RSI-Crossover (2d, v4): rolling 20-bar VWAP of hlc3; long on crossover(close, vwap) with RSI20 > 30,
    short on crossunder with RSI20 < 70; the strategy.exit calls carry no price, so they are no-ops; entries reverse."""
    B = bars(sym, 2 * DAY)
    o, h, l, c, v = (B[k].to_numpy() for k in "ohlcv")
    T = B["T"].to_numpy()
    tp = (h + l + c) / 3
    vw = pd.Series(tp * v).rolling(20).sum().to_numpy() / pd.Series(v).rolling(20).sum().to_numpy()
    r = rsi(c, 20)
    n = len(c)
    A = Acct(); eq = np.empty(n); pend = []
    for i in range(n):
        for d in pend:
            if A.q != 0 and (A.q > 0) == (d > 0):
                continue
            if A.q != 0:
                A.close(o[i], T[i])
            A.open("Long" if d > 0 else "Short", d, o[i], T[i])
        pend = []
        A.fund(c[i], B.fund[i])
        eq[i] = A.equity(c[i])
        if i == 0:
            continue
        up = c[i] > vw[i] and c[i - 1] <= vw[i - 1]
        dn = c[i] < vw[i] and c[i - 1] >= vw[i - 1]
        if up and r[i] > 30:
            pend.append(1)
        if dn and r[i] < 70:
            pend.append(-1)
    return B, A, eq
