"""Run a compiled Pine script on Binance bars with the broker emulator; also the non-repainting security hub."""
from __future__ import annotations
import copy
import math
import os
import time
import numpy as np
from . import runtime as rt
from .broker import Broker
from .compiler import compile_source
from .data import Bars as _Bars
from functools import lru_cache

NA = rt.NA
_PAGE_MB = os.sysconf("SC_PAGE_SIZE") / 2**20


def rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * _PAGE_MB
    except OSError:
        return 0.0


@lru_cache(maxsize=4)
def _bars_cached(symbol, tf_ms, start_ms, end_ms, max_bars, heikin_ashi):
    return _Bars(symbol, tf_ms, start_ms=start_ms, end_ms=end_ms, max_bars=max_bars, heikin_ashi=heikin_ashi)


def Bars(symbol, tf_ms, start_ms=None, end_ms=None, max_bars=None, heikin_ashi=False, arrays=None):
    if arrays is not None:
        return _Bars(symbol, tf_ms, arrays=arrays)
    return _bars_cached(symbol, tf_ms, start_ms, end_ms, max_bars, heikin_ashi)
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}


class Ctx(list):
    __slots__ = ("path",)


def rt_ctx(n, path):
    c = Ctx([None] * n)
    c.path = path
    return c


def rt_tget(t, k):
    if isinstance(t, (tuple, list)):
        return t[k] if k < len(t) else NA
    return NA


def rt_prange(a, b, step):
    if rt.isna(a) or rt.isna(b):
        return ()
    s = 1 if step is None or rt.isna(step) or step == 0 else abs(step)
    ints = all(isinstance(x, int) or (isinstance(x, float) and x.is_integer()) for x in (a, b)) and float(s).is_integer()
    if ints:
        a, b, s = int(a), int(b), int(s)
        return range(a, b + 1, s) if a <= b else range(a, b - 1, -s)
    out = []
    x = a
    if a <= b:
        while x <= b + 1e-12 and len(out) < 20001:
            out.append(x); x += s
    else:
        while x >= b - 1e-12 and len(out) < 20001:
            out.append(x); x -= s
    return out


def rt_time(D, cfg, i, tf, session, tz, is_close):
    t = D.T[i]
    tf_ms = rt.tf_to_ms(tf) if tf not in (None, "") else None
    if tf_ms and tf_ms != cfg["tf_ms"]:
        from .data import bin_index, bin_open_time
        b = bin_index(np.array([t], np.int64), tf_ms)
        t0 = int(bin_open_time(b, tf_ms)[0])
        t = t0 + tf_ms if is_close else t0
    elif is_close:
        t = D.TC[i]
    if session is not None and isinstance(session, str) and session:
        if not rt.in_session(D.T[i], session, tz):
            return NA
    return t


def rt_cci(arr, end, n):
    m = rt.w_sma(arr, end, n)
    d = rt.w_dev(arr, end, n)
    if m != m or d != d or d == 0 or end < 1:
        return NA
    return (arr[end - 1] - m) / (0.015 * d)


def rt_bb(arr, end, n, mult):
    mid = rt.w_sma(arr, end, n)
    sd = rt.w_stdev(arr, end, n)
    return (mid, mid + mult * sd, mid - mult * sd)


def rt_bbw(arr, end, n, mult):
    mid, up, lo = rt_bb(arr, end, n, mult)
    return (up - lo) / mid if mid else NA


def rt_kcw(t):
    mid, up, lo = t
    return (up - lo) / mid if mid else NA


def rt_copy(o):
    return copy.copy(o)


EXEC_GLOBALS = {"rt": rt, "rt_ctx": rt_ctx, "rt_tget": rt_tget, "rt_prange": rt_prange, "rt_time": rt_time,
                "rt_cci": rt_cci, "rt_bb": rt_bb, "rt_bbw": rt_bbw, "rt_kcw": rt_kcw, "rt_copy": rt_copy}


class CompiledScript:
    def __init__(self, src: str):
        self.pysrc, self.cfg, self.flags = compile_source(src)
        g = dict(EXEC_GLOBALS)
        exec(compile(self.pysrc, "<pine>", "exec"), g)
        self.build = g["build"]


def tf_cfg(symbol: str, tf_ms: int) -> dict:
    return {"tickerid": f"BINANCE:{symbol}", "ticker": symbol, "base": symbol.replace("USDT", ""),
            "mintick": {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.01}.get(symbol, 0.01),
            "period": rt.tf_string(tf_ms), "multiplier": _multiplier(tf_ms), "tf_ms": tf_ms,
            "isintraday": tf_ms < 86_400_000, "isdaily": 86_400_000 <= tf_ms < 604_800_000,
            "isweekly": 604_800_000 <= tf_ms < 2_592_000_000, "ismonthly": tf_ms >= 2_592_000_000,
            "isdwm": tf_ms >= 86_400_000, "isminutes": tf_ms < 86_400_000}


def _multiplier(tf_ms):
    for unit in (2_592_000_000, 604_800_000, 86_400_000):
        if tf_ms >= unit and tf_ms % unit == 0:
            return tf_ms // unit
    return tf_ms // 60_000


class UnsupportedSymbol(Exception):
    pass


def resolve_symbol(sym, current: str):
    """Map a Pine symbol argument to (binance_symbol, heikin_ashi)."""
    ha = False
    s = "" if sym is None or (isinstance(sym, float) and sym != sym) else str(sym)
    if s.startswith("HA:"):
        ha, s = True, s[3:]
    if s in ("", current, f"BINANCE:{current}"):
        return current, ha
    up = s.upper()
    for base, full in SYMBOLS.items():
        if base in up:
            if "DOM" in up or "SPX" in up or up.startswith("CRYPTOCAP"):
                break
            return full, ha
    if up.split(":")[-1].replace(".P", "") in (current, current.replace("USDT", "USD"), current.replace("USDT", "USDTPERP")):
        return current, ha
    raise UnsupportedSymbol(s)


class SecurityHub:
    def __init__(self, runner: "Runner", symbol: str, tf_ms: int, bars: Bars, ha: bool = False, recording=False):
        self.runner, self.symbol, self.tf_ms, self.bars, self.ha = runner, symbol, tf_ms, bars, ha
        self.recording = {} if recording else None
        self.maps = {}
        self.calls = 0

    def __call__(self, key, i, sym, tf, gaps, thunk):
        self.calls += 1
        tf_ms = rt.tf_to_ms(tf) if tf not in (None, "") else None
        if tf_ms is None:
            tf_ms = self.tf_ms
        sk = resolve_symbol(sym, self.symbol)
        if tf_ms == self.tf_ms and sk == (self.symbol, self.ha):
            v = thunk()
            if self.recording is not None:
                self.recording.setdefault(key, {})[i] = v
            return v
        vals, tc = self.runner.shadow(sk, tf_ms)
        mk = (sk, tf_ms)
        m = self.maps.get(mk)
        if m is None:
            m = (np.searchsorted(np.asarray(tc, np.int64), np.asarray(self.bars.TC, np.int64), side="right") - 1).tolist()
            self.maps[mk] = m
        j = m[i]
        if j < 0:
            return NA
        if gaps and i > 0 and m[i - 1] == j:
            return NA
        d = vals.get(key)
        if d is None:
            return NA
        return d.get(j, NA)


class Runner:
    def __init__(self, script: CompiledScript, symbol: str, tf_ms: int, end_ms: int | None = None,
                 max_bars: int | None = None, fee: float = 0.0007, time_limit: float = 300.0,
                 max_rss_mb: float | None = None, magnify: bool = False, mag_brute: bool = False):
        self.script, self.symbol, self.tf_ms = script, symbol, tf_ms
        self.end_ms, self.max_bars, self.fee, self.time_limit = end_ms, max_bars, fee, time_limit
        self.max_rss_mb = max_rss_mb
        self.magnify, self.mag_brute = magnify, mag_brute
        self.shadow_bars = {}           # "symbol|tf_ms|ha" -> (bars, capped)
        self.shadows = {}
        self.in_progress = set()
        self.main_start = None
        self.deadline = None

    def check_memory(self):
        if self.max_rss_mb is not None:
            m = rss_mb()
            if m > self.max_rss_mb:
                raise MemoryError(f"process RSS {m:.0f} MB above the {self.max_rss_mb:.0f} MB limit")

    def instantiate(self, bars: Bars, bk: Broker, hub: SecurityHub, symbol: str, tf_ms: int):
        cfg = tf_cfg(symbol, tf_ms)
        return self.script.build(rt, bars, bk, hub, cfg)

    def shadow(self, sk, tf_ms):
        key = (sk, tf_ms)
        if key in self.shadows:
            return self.shadows[key]
        if key in self.in_progress:
            return {}, []
        self.in_progress.add(key)
        sym, ha = sk
        start = None
        if tf_ms < self.tf_ms and self.main_start is not None:
            start = self.main_start - 2000 * tf_ms
        # the bar cap applies to every series the engine computes, security shadow runs included
        bars = Bars(sym, tf_ms, start_ms=start, end_ms=self.end_ms, max_bars=self.max_bars, heikin_ashi=ha)
        self.shadow_bars[f"{sym}|{tf_ms}|{int(ha)}"] = (bars.n, bool(getattr(bars, "capped", False)))
        bk = Broker(bars, self.script.cfg, self.fee, shadow=True)
        hub = SecurityHub(self, sym, tf_ms, bars, ha=ha, recording=True)
        step = self.instantiate(bars, bk, hub, sym, tf_ms)
        errors = 0
        for i in range(bars.n):
            if (i & 1023) == 0:
                if self.deadline is not None and time.time() > self.deadline:
                    raise TimeoutError(f"time limit in security shadow run after {i}/{bars.n} bars")
                self.check_memory()
            bk.begin_bar(i)
            try:
                step(i)
            except (rt.PineRuntimeError, rt.LoopGuard, ArithmeticError, TypeError, ValueError, IndexError,
                    KeyError, AttributeError):
                errors += 1
            bk.end_bar(i)
        res = (hub.recording, bars.TC)
        self.shadows[key] = res
        self.in_progress.discard(key)
        return res

    def run(self) -> dict:
        t0 = time.time()
        bars = Bars(self.symbol, self.tf_ms, end_ms=self.end_ms, max_bars=self.max_bars)
        self.main_start = bars.T[0] if bars.n else None
        bk = Broker(bars, self.script.cfg, self.fee)
        if self.magnify:
            from .broker import Magnifier
            from .data import load_1m
            t, o, h, l, c = load_1m(self.symbol)[:5]
            bk.mag = Magnifier(t, o, h, l, c, bars.T, bars.TC, brute=self.mag_brute)
        hub = SecurityHub(self, self.symbol, self.tf_ms, bars)
        step = self.instantiate(bars, bk, hub, self.symbol, self.tf_ms)
        err_bars, first_err = 0, None
        n = bars.n
        deadline = t0 + self.time_limit
        self.deadline = deadline
        for i in range(n):
            bk.begin_bar(i)
            try:
                step(i)
            except (rt.PineRuntimeError, rt.LoopGuard, ArithmeticError, TypeError, ValueError, IndexError,
                    KeyError, AttributeError, RecursionError) as e:
                err_bars += 1
                if first_err is None:
                    first_err = f"bar {i}: {type(e).__name__}: {str(e)[:160]}"
            bk.end_bar(i)
            if (i & 1023) == 0:
                if time.time() > deadline:
                    raise TimeoutError(f"time limit after {i}/{n} bars")
                self.check_memory()
        return {"n_bars": n, "T": bars.T, "TC": bars.TC, "close": bars.C, "equity": bk.eq_close,
                "closed": bk.closed, "n_fills": bk.n_fills, "fees": bk.fees_paid, "funding": bk.funding_paid,
                "blown": bk.blown, "err_bars": err_bars, "first_err": first_err, "seconds": time.time() - t0,
                "sec_calls": hub.calls, "initial_capital": bk.initial_capital, "shadow_bars": dict(self.shadow_bars),
                "minutes_walked": bk.mag.minutes_walked if bk.mag is not None else 0}
