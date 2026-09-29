"""Runtime library for compiled Pine scripts: na semantics, ta.* indicators, math/str/array/time helpers.

Conventions
  * na is float('nan') for numbers; bools are Python bools (a bool na is treated as False in conditions).
  * Window functions take a series accessor (arr, end): the series values are arr[0:end], the current value arr[end-1].
    Builtin OHLCV series pass (list, i + 1); tracked variables pass their history buffer; expressions pass a call-site
    buffer that is appended once per evaluation (Pine's "each call site has its own history").
  * Recursive indicators (ema, rma, rsi, atr, sar, supertrend, dmi, ...) keep state per call site and update only when
    called, which is Pine's documented behaviour for calls inside conditional blocks.
"""
from __future__ import annotations
import calendar
import datetime as dt
import math
import random as _random

NA = float("nan")
_isnan = math.isnan


class PineRuntimeError(Exception):
    pass


class LoopGuard(Exception):
    pass


def isna(x) -> bool:
    if x is None:
        return True
    if x.__class__ is float:
        return x != x
    return False


def nz(x, repl=0.0):
    if x is None or (x.__class__ is float and x != x):
        return repl
    return x


def T(x) -> bool:
    """Pine truthiness: na and 0 are false."""
    if x is True:
        return True
    if x is False or x is None:
        return False
    try:
        return x == x and x != 0
    except Exception:
        return bool(x)


def AND(a, b) -> bool:      # eager (v1-v5 evaluate both operands)
    return T(a) and T(b)


def OR(a, b) -> bool:
    return T(a) or T(b)


def div(a, b):
    try:
        if b == 0:
            return NA
        return a / b
    except TypeError:
        if a is None or b is None:
            return NA
        raise


def idiv(a, b):             # const int / const int in v<=5 truncates toward zero
    if b == 0:
        return NA
    q = a / b
    return int(q)


def mod(a, b):
    try:
        if b == 0 or a != a or b != b:
            return NA
        return math.fmod(a, b)
    except TypeError:
        return NA


def neg(a):
    return -a if a is not None else NA


def iff(c, a, b):
    return a if T(c) else b


def to_int(x):
    if x is None or (x.__class__ is float and x != x):
        return NA
    if x is True:
        return 1
    if x is False:
        return 0
    return int(x)


def to_float(x):
    if x is None:
        return NA
    if x is True:
        return 1.0
    if x is False:
        return 0.0
    try:
        return float(x)
    except Exception:
        return NA


def to_bool(x):
    return T(x)


def L(n):
    """Coerce a length argument to int (None if na or < 1)."""
    if n is None or (n.__class__ is float and n != n):
        return None
    n = int(round(n)) if n.__class__ is float else int(n)
    return n if n >= 1 else None


def hist(buf, n):
    """buf[-1] is the current value; return the value n steps back (n may be float/na)."""
    if n is None or (n.__class__ is float and n != n):
        return NA
    n = int(n)
    if n < 0:
        raise PineRuntimeError("negative history offset (future reference)")
    if n < len(buf):
        return buf[-1 - n]
    return NA


def hist_pre(buf, n):
    """buf does not yet contain the current value (used inside a declaration's own right-hand side)."""
    if n is None or (n.__class__ is float and n != n):
        return NA
    n = int(n)
    if n <= 0:
        return NA
    if n <= len(buf):
        return buf[-n]
    return NA


def bar(arr, i, n):
    if n.__class__ is not int:
        if n is None or n != n:
            return NA
        n = int(n)
    if n < 0:
        raise PineRuntimeError("negative history offset (future reference)")
    j = i - n
    return arr[j] if j >= 0 else NA


def child(ctx, k, factory):
    c = ctx[k]
    if c is None:
        c = ctx[k] = factory()
    return c


class Box:
    __slots__ = ("v", "init")

    def __init__(self):
        self.v = NA
        self.init = False


class HBuf(list):
    """Call-site history buffer for an expression."""
    __slots__ = ()

    def push(self, v):
        self.append(v)
        return self

    def pg(self, v, n):     # push then get n back
        self.append(v)
        return hist(self, n)


# ------------------------------------------------------------------------------------------------ window helpers
def _win(arr, end, n):
    if n is None or end < n:
        return None
    return arr[end - n:end]


def w_sma(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    try:
        return math.fsum(w) / n
    except TypeError:
        return NA


def w_sum(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    try:
        return math.fsum(w)
    except TypeError:
        return NA


def w_wma(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    s = 0.0
    for k, v in enumerate(w, 1):
        s += v * k
    return s / (n * (n + 1) / 2)


def w_swma(arr, end):
    w = _win(arr, end, 4)
    if w is None:
        return NA
    return w[0] / 6 + w[1] * 2 / 6 + w[2] * 2 / 6 + w[3] / 6


def w_highest(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    m = NA
    for v in w:
        if v == v and (m != m or v > m):
            m = v
    return m


def w_lowest(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    m = NA
    for v in w:
        if v == v and (m != m or v < m):
            m = v
    return m


def w_highestbars(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    best, bi = NA, 0
    for k, v in enumerate(w):
        if v == v and (best != best or v >= best):
            best, bi = v, k
    return -(n - 1 - bi)


def w_lowestbars(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    best, bi = NA, 0
    for k, v in enumerate(w):
        if v == v and (best != best or v <= best):
            best, bi = v, k
    return -(n - 1 - bi)


def w_stdev(arr, end, n, biased=True):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    try:
        m = math.fsum(w) / n
        ss = math.fsum((v - m) * (v - m) for v in w)
    except TypeError:
        return NA
    if ss != ss:
        return NA
    d = n if T(biased) else n - 1
    if d <= 0:
        return NA
    return math.sqrt(ss / d) if ss > 0 else 0.0


def w_variance(arr, end, n, biased=True):
    s = w_stdev(arr, end, n, biased)
    return s * s if s == s else NA


def w_dev(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    m = math.fsum(w) / n
    return math.fsum(abs(v - m) for v in w) / n


def w_median(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    w = sorted(v for v in w if v == v)
    if not w:
        return NA
    k = len(w)
    return w[k // 2] if k % 2 else (w[k // 2 - 1] + w[k // 2]) / 2


def w_mode(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    cnt = {}
    for v in w:
        if v == v:
            cnt[v] = cnt.get(v, 0) + 1
    if not cnt:
        return NA
    best = max(cnt.values())
    return min(v for v, c in cnt.items() if c == best)


def w_linreg(arr, end, n, offset=0):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    sx = n * (n - 1) / 2
    sxx = (n - 1) * n * (2 * n - 1) / 6
    sy = 0.0
    sxy = 0.0
    for k, v in enumerate(w):
        sy += v
        sxy += k * v
    den = n * sxx - sx * sx
    if den == 0:
        return NA
    slope = (n * sxy - sx * sy) / den
    intercept = (sy - slope * sx) / n
    off = 0 if offset is None or offset != offset else int(offset)
    return intercept + slope * (n - 1 - off)


def w_percentrank(arr, end, n):
    n = L(n)
    if n is None or end < n + 1:
        return NA
    cur = arr[end - 1]
    w = arr[end - 1 - n:end - 1]
    c = sum(1 for v in w if v <= cur)
    return 100.0 * c / n


def w_percentile_linear(arr, end, n, pct):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    w = sorted(v for v in w if v == v)
    if not w:
        return NA
    p = pct / 100.0 * (len(w) - 1)
    lo = int(math.floor(p))
    hi = min(lo + 1, len(w) - 1)
    return w[lo] + (w[hi] - w[lo]) * (p - lo)


def w_percentile_nearest(arr, end, n, pct):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    w = sorted(v for v in w if v == v)
    if not w:
        return NA
    k = max(0, min(len(w) - 1, int(math.ceil(pct / 100.0 * len(w))) - 1))
    return w[k]


def w_change(arr, end, n=1):
    if n is None or n != n:
        return NA
    n = int(n)
    if end < n + 1:
        return NA
    a, b = arr[end - 1], arr[end - 1 - n]
    if a.__class__ is bool or b.__class__ is bool:
        return a != b
    return a - b


def w_roc(arr, end, n):
    n = L(n)
    if n is None or end < n + 1:
        return NA
    prev = arr[end - 1 - n]
    if prev == 0 or prev != prev:
        return NA
    return 100.0 * (arr[end - 1] - prev) / prev


def w_rising(arr, end, n):
    n = L(n)
    if n is None or end < n + 1:
        return False
    cur = arr[end - 1]
    for k in range(1, n + 1):
        if not cur > arr[end - 1 - k]:
            return False
    return True


def w_falling(arr, end, n):
    n = L(n)
    if n is None or end < n + 1:
        return False
    cur = arr[end - 1]
    for k in range(1, n + 1):
        if not cur < arr[end - 1 - k]:
            return False
    return True


def cross_over(a_arr, a_end, b_arr, b_end):
    if a_end < 2 or b_end < 2:
        return False
    a0, a1, b0, b1 = a_arr[a_end - 1], a_arr[a_end - 2], b_arr[b_end - 1], b_arr[b_end - 2]
    return a0 > b0 and a1 <= b1


def cross_under(a_arr, a_end, b_arr, b_end):
    if a_end < 2 or b_end < 2:
        return False
    a0, a1, b0, b1 = a_arr[a_end - 1], a_arr[a_end - 2], b_arr[b_end - 1], b_arr[b_end - 2]
    return a0 < b0 and a1 >= b1


def cross_any(a_arr, a_end, b_arr, b_end):
    return cross_over(a_arr, a_end, b_arr, b_end) or cross_under(a_arr, a_end, b_arr, b_end)


def w_correlation(a_arr, a_end, b_arr, b_end, n):
    n = L(n)
    wa, wb = _win(a_arr, a_end, n), _win(b_arr, b_end, n)
    if wa is None or wb is None:
        return NA
    ma, mb = math.fsum(wa) / n, math.fsum(wb) / n
    sab = saa = sbb = 0.0
    for x, y in zip(wa, wb):
        dx, dy = x - ma, y - mb
        sab += dx * dy; saa += dx * dx; sbb += dy * dy
    den = math.sqrt(saa * sbb)
    return sab / den if den > 0 else NA


def w_cog(arr, end, n):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    s = math.fsum(w)
    num = 0.0
    for k in range(n):
        num += w[n - 1 - k] * (k + 1)
    return -num / s if s != 0 else NA


def w_cmo(arr, end, n):
    n = L(n)
    if n is None or end < n + 1:
        return NA
    up = dn = 0.0
    for k in range(end - n, end):
        d = arr[k] - arr[k - 1]
        if d > 0:
            up += d
        else:
            dn -= d
    s = up + dn
    return 100.0 * (up - dn) / s if s != 0 else 0.0


def w_alma(arr, end, n, offset=0.85, sigma=6.0, floor=False):
    n = L(n)
    w = _win(arr, end, n)
    if w is None:
        return NA
    m = offset * (n - 1)
    if T(floor):
        m = math.floor(m)
    s = n / sigma
    norm = total = 0.0
    for k in range(n):
        wt = math.exp(-((k - m) ** 2) / (2 * s * s))
        total += w[k] * wt
        norm += wt
    return total / norm


def w_range(arr, end, n):
    h, l = w_highest(arr, end, n), w_lowest(arr, end, n)
    return h - l


def w_vwma(s_arr, s_end, v_arr, v_end, n):
    n = L(n)
    ws, wv = _win(s_arr, s_end, n), _win(v_arr, v_end, n)
    if ws is None or wv is None:
        return NA
    num = math.fsum(a * b for a, b in zip(ws, wv))
    den = math.fsum(wv)
    return num / den if den != 0 else NA


def w_stoch(s_arr, s_end, h_arr, h_end, l_arr, l_end, n):
    hh, ll = w_highest(h_arr, h_end, n), w_lowest(l_arr, l_end, n)
    if hh != hh or ll != ll or s_end < 1:
        return NA
    rng = hh - ll
    return 100.0 * (s_arr[s_end - 1] - ll) / rng if rng != 0 else NA


def pivot_high(arr, end, left, right):
    left, right = int(left), int(right)
    if end < left + right + 1:
        return NA
    c = end - 1 - right
    pv = arr[c]
    if pv != pv:
        return NA
    for k in range(c - left, c):
        if not arr[k] < pv:
            return NA
    for k in range(c + 1, end):
        if not arr[k] <= pv:
            return NA
    return pv


def pivot_low(arr, end, left, right):
    left, right = int(left), int(right)
    if end < left + right + 1:
        return NA
    c = end - 1 - right
    pv = arr[c]
    if pv != pv:
        return NA
    for k in range(c - left, c):
        if not arr[k] > pv:
            return NA
    for k in range(c + 1, end):
        if not arr[k] >= pv:
            return NA
    return pv


# ------------------------------------------------------------------------------------------------ stateful indicators
class EMA:
    __slots__ = ("v",)

    def __init__(self):
        self.v = NA

    def u(self, x, n):
        if n is None or n != n or n <= 0:
            return NA
        if self.v != self.v:
            self.v = x
        else:
            a = 2.0 / (n + 1)
            self.v = a * x + (1 - a) * self.v
        return self.v


class RMA:
    __slots__ = ("v", "seed")

    def __init__(self):
        self.v = NA
        self.seed = []

    def u(self, x, n):
        n = L(n)
        if n is None:
            return NA
        if self.v != self.v:
            if x != x:
                self.seed = []
                return NA
            self.seed.append(x)
            if len(self.seed) > n:
                del self.seed[0]
            if len(self.seed) < n:
                return NA
            self.v = math.fsum(self.seed) / n
            self.seed = []
            return self.v
        self.v = (x + (n - 1) * self.v) / n
        return self.v


class RSI:
    __slots__ = ("prev", "up", "dn")

    def __init__(self):
        self.prev = NA
        self.up = RMA()
        self.dn = RMA()

    def u(self, x, n):
        p = self.prev
        self.prev = x
        if p != p or x != x:
            ch = NA
        else:
            ch = x - p
        u = self.up.u(max(ch, 0.0) if ch == ch else NA, n)
        d = self.dn.u(max(-ch, 0.0) if ch == ch else NA, n)
        if u != u or d != d:
            return NA
        if d == 0:
            return 100.0 if u != 0 else 50.0
        if u == 0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + u / d)


class ATR:
    __slots__ = ("r",)

    def __init__(self):
        self.r = RMA()

    def u(self, tr, n):
        return self.r.u(tr, n)


class Cum:
    __slots__ = ("v",)

    def __init__(self):
        self.v = 0.0

    def u(self, x):
        if x == x and x is not None:
            self.v += x
        return self.v


class FixNan:
    __slots__ = ("v",)

    def __init__(self):
        self.v = NA

    def u(self, x):
        if not (x is None or (x.__class__ is float and x != x)):
            self.v = x
        return self.v


class MaxAll:
    __slots__ = ("v",)

    def __init__(self):
        self.v = NA

    def u(self, x):
        if x == x and (self.v != self.v or x > self.v):
            self.v = x
        return self.v


class MinAll:
    __slots__ = ("v",)

    def __init__(self):
        self.v = NA

    def u(self, x):
        if x == x and (self.v != self.v or x < self.v):
            self.v = x
        return self.v


class ValueWhen:
    __slots__ = ("vals",)

    def __init__(self):
        self.vals = []

    def u(self, cond, src, occ=0):
        if T(cond):
            self.vals.append(src)
            if len(self.vals) > 1000:
                del self.vals[:500]
        occ = 0 if occ is None or occ != occ else int(occ)
        return self.vals[-1 - occ] if occ < len(self.vals) else NA


class BarsSince:
    __slots__ = ("n",)

    def __init__(self):
        self.n = None

    def u(self, cond):
        if T(cond):
            self.n = 0
        elif self.n is not None:
            self.n += 1
        return NA if self.n is None else self.n


class HMA:
    __slots__ = ("diff",)

    def __init__(self):
        self.diff = []

    def u(self, arr, end, n):
        n = L(n)
        if n is None:
            return NA
        half = max(1, int(n / 2))
        a, b = w_wma(arr, end, half), w_wma(arr, end, n)
        self.diff.append(2 * a - b if a == a and b == b else NA)
        m = max(1, int(round(math.sqrt(n))))
        return w_wma(self.diff, len(self.diff), m)


class MACD:
    __slots__ = ("f", "s", "sig")

    def __init__(self):
        self.f, self.s, self.sig = EMA(), EMA(), EMA()

    def u(self, x, fast, slow, signal):
        m = self.f.u(x, fast) - self.s.u(x, slow)
        sg = self.sig.u(m, signal)
        return (m, sg, m - sg)


class BB:
    __slots__ = ()

    def u(self, arr, end, n, mult):
        mid = w_sma(arr, end, n)
        sd = w_stdev(arr, end, n)
        return (mid, mid + mult * sd, mid - mult * sd)


class KC:
    __slots__ = ("e", "r", "rbuf")

    def __init__(self):
        self.e, self.r = EMA(), EMA()

    def u(self, x, n, mult, rng):
        mid = self.e.u(x, n)
        rg = self.r.u(rng, n)
        return (mid, mid + mult * rg, mid - mult * rg)


class Supertrend:
    __slots__ = ("atr", "up", "dn", "dir", "st", "pclose")

    def __init__(self):
        self.atr = RMA()
        self.up = NA
        self.dn = NA
        self.dir = NA
        self.st = NA
        self.pclose = NA

    def u(self, factor, period, h, l, c, tr):
        atr = self.atr.u(tr, period)
        src = (h + l) / 2
        up = src + factor * atr
        dn = src - factor * atr
        pu, pd, pc = self.up, self.dn, self.pclose
        if pd == pd and dn == dn:
            dn = dn if (dn > pd or pc < pd) else pd
        if pu == pu and up == up:
            up = up if (up < pu or pc > pu) else pu
        pst = self.st
        if atr != atr:
            d = 1
        elif pst != pst or pu != pu:
            d = 1
        elif pst == pu:
            d = -1 if c > up else 1
        else:
            d = 1 if c < dn else -1
        st = dn if d == -1 else up
        self.up, self.dn, self.dir, self.st, self.pclose = up, dn, d, st, c
        return (st, d)


class DMI:
    __slots__ = ("tr", "p", "m", "adx", "ph", "pl")

    def __init__(self):
        self.tr, self.p, self.m, self.adx = RMA(), RMA(), RMA(), RMA()
        self.ph = NA
        self.pl = NA

    def u(self, dilen, adxlen, h, l, tr):
        ph, pl = self.ph, self.pl
        self.ph, self.pl = h, l
        up = h - ph if ph == ph else NA
        dn = pl - l if pl == pl else NA
        pdm = (up if (up > dn and up > 0) else 0.0) if up == up and dn == dn else NA
        mdm = (dn if (dn > up and dn > 0) else 0.0) if up == up and dn == dn else NA
        trr = self.tr.u(tr, dilen)
        pp = self.p.u(pdm, dilen)
        mm = self.m.u(mdm, dilen)
        if trr != trr or trr == 0:
            return (NA, NA, NA)
        plus = 100 * pp / trr
        minus = 100 * mm / trr
        s = plus + minus
        dx = abs(plus - minus) / (s if s != 0 else 1)
        adx = 100 * self.adx.u(dx, adxlen)
        return (plus, minus, adx)


class SAR:
    __slots__ = ("result", "maxmin", "acc", "long", "started", "ph", "pl", "pc", "pph", "ppl")

    def __init__(self):
        self.result = NA
        self.maxmin = NA
        self.acc = NA
        self.long = True
        self.started = 0
        self.ph = self.pl = self.pc = self.pph = self.ppl = NA

    def u(self, start, inc, mx, h, l, c):
        # Port of TradingView's documented pine_sar()
        st = self.started
        prev_h, prev_l, prev_c = self.ph, self.pl, self.pc
        prev2_h, prev2_l = self.pph, self.ppl
        self.pph, self.ppl = prev_h, prev_l
        self.ph, self.pl, self.pc = h, l, c
        self.started = st + 1
        if st == 0:
            return NA
        if st == 1:
            if c > prev_c:
                self.long, self.maxmin, self.result = True, max(h, prev_h), min(l, prev_l)
            else:
                self.long, self.maxmin, self.result = False, min(l, prev_l), max(h, prev_h)
            self.acc = start
            is_first = True
        else:
            is_first = False
        result = self.result + self.acc * (self.maxmin - self.result)
        is_below = self.long
        if self.long:
            if l < result:
                is_first, is_below = True, False
                result = max(h, self.maxmin)
                self.maxmin, self.acc = l, start
        else:
            if h > result:
                is_first, is_below = True, True
                result = min(l, self.maxmin)
                self.maxmin, self.acc = h, start
        if not is_first:
            if is_below:
                if h > self.maxmin:
                    self.maxmin = h
                    self.acc = min(self.acc + inc, mx)
            else:
                if l < self.maxmin:
                    self.maxmin = l
                    self.acc = min(self.acc + inc, mx)
        if is_below:
            result = min(result, prev_l)
            if prev2_l == prev2_l:
                result = min(result, prev2_l)
        else:
            result = max(result, prev_h)
            if prev2_h == prev2_h:
                result = max(result, prev2_h)
        self.long = is_below
        self.result = result
        return result


class TSI:
    __slots__ = ("prev", "a1", "a2", "b1", "b2")

    def __init__(self):
        self.prev = NA
        self.a1, self.a2, self.b1, self.b2 = EMA(), EMA(), EMA(), EMA()

    def u(self, x, short, long):
        p = self.prev
        self.prev = x
        m = x - p if p == p else NA
        if m != m:
            return NA
        num = self.a2.u(self.a1.u(m, long), short)
        den = self.b2.u(self.b1.u(abs(m), long), short)
        return num / den if den != 0 else NA


class MFI:
    __slots__ = ("prev", "pos", "neg")

    def __init__(self):
        self.prev = NA
        self.pos = []
        self.neg = []

    def u(self, src, vol, n):
        n = L(n)
        p = self.prev
        self.prev = src
        ch = src - p if p == p else NA
        self.pos.append(vol * src if ch == ch and ch > 0 else 0.0)
        self.neg.append(vol * src if ch == ch and ch < 0 else 0.0)
        if n is None or len(self.pos) < n + 1:
            return NA
        up = math.fsum(self.pos[-n:])
        dn = math.fsum(self.neg[-n:])
        if dn == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + up / dn)


class VWAP:
    __slots__ = ("day", "pv", "v")

    def __init__(self):
        self.day = None
        self.pv = 0.0
        self.v = 0.0

    def u(self, src, vol, t):
        d = t // 86_400_000
        if d != self.day:
            self.day, self.pv, self.v = d, 0.0, 0.0
        self.pv += src * vol
        self.v += vol
        return self.pv / self.v if self.v else NA


# ------------------------------------------------------------------------------------------------ math
def m_max(*a):
    vals = [x for x in a]
    if any(isna(x) for x in vals):
        return NA
    return max(vals)


def m_min(*a):
    vals = [x for x in a]
    if any(isna(x) for x in vals):
        return NA
    return min(vals)


def m_avg(*a):
    if any(isna(x) for x in a):
        return NA
    return math.fsum(a) / len(a)


def m_abs(x):
    return abs(x) if not isna(x) else NA


def m_round(x, precision=None):
    if isna(x):
        return NA
    if precision is None or isna(precision):
        return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))
    f = 10 ** int(precision)
    return (math.floor(x * f + 0.5) if x >= 0 else -math.floor(-x * f + 0.5)) / f


def m_floor(x):
    return NA if isna(x) else int(math.floor(x))


def m_ceil(x):
    return NA if isna(x) else int(math.ceil(x))


def m_sqrt(x):
    return math.sqrt(x) if (not isna(x) and x >= 0) else NA


def m_pow(a, b):
    try:
        if isna(a) or isna(b):
            return NA
        r = math.pow(a, b)
        return r
    except (ValueError, OverflowError, ZeroDivisionError):
        return NA


def m_log(x):
    return math.log(x) if (not isna(x) and x > 0) else NA


def m_log10(x):
    return math.log10(x) if (not isna(x) and x > 0) else NA


def m_exp(x):
    try:
        return math.exp(x) if not isna(x) else NA
    except OverflowError:
        return NA


def m_sign(x):
    if isna(x):
        return NA
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


def _safe1(f):
    def g(x):
        try:
            return f(x) if not isna(x) else NA
        except (ValueError, OverflowError):
            return NA
    return g


m_sin, m_cos, m_tan = _safe1(math.sin), _safe1(math.cos), _safe1(math.tan)
m_asin, m_acos, m_atan = _safe1(math.asin), _safe1(math.acos), _safe1(math.atan)
m_todegrees, m_toradians = _safe1(math.degrees), _safe1(math.radians)
_rng = _random.Random(42)


def m_random(lo=0.0, hi=1.0, seed=None):
    return lo + (hi - lo) * _rng.random()


# ------------------------------------------------------------------------------------------------ strings / colours
def s_tostring(x, fmt=None):
    if isna(x):
        return "NaN"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float):
        if fmt and isinstance(fmt, str) and "." in fmt:
            return f"{x:.{fmt.count('#') + fmt.count('0') - 1}f}"
        return repr(round(x, 8))
    return str(x)


def s_format(fmt, *args):
    try:
        import re as _re
        return _re.sub(r"\{(\d+)[^}]*\}", lambda m: s_tostring(args[int(m.group(1))]) if int(m.group(1)) < len(args) else "", fmt)
    except Exception:
        return str(fmt)


def s_tonumber(s):
    try:
        return float(s)
    except Exception:
        return NA


def s_contains(s, sub):
    return isinstance(s, str) and isinstance(sub, str) and sub in s


def s_length(s):
    return len(s) if isinstance(s, str) else NA


def s_replace_all(s, a, b):
    return s.replace(a, b) if isinstance(s, str) else s


def s_split(s, sep):
    return PArray(s.split(sep)) if isinstance(s, str) else PArray([])


def color_passthrough(c=None, *a, **k):
    return c if c is not None else "#000000"


def color_rgb(r, g, b, t=0):
    return "#%02x%02x%02x" % (int(nz(r)) & 255, int(nz(g)) & 255, int(nz(b)) & 255)


def noop(*a, **k):
    return None


class Drawing:
    """Stand-in for label/line/box/table handles; setters are no-ops, line.get_price is supported."""
    __slots__ = ("x1", "y1", "x2", "y2")

    def __init__(self, x1=NA, y1=NA, x2=NA, y2=NA):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


def line_new(x1=NA, y1=NA, x2=NA, y2=NA, *a, **k):
    return Drawing(x1, y1, x2, y2)


def line_get_price(ln, x):
    if not isinstance(ln, Drawing):
        return NA
    try:
        if ln.x2 == ln.x1:
            return ln.y1
        return ln.y1 + (ln.y2 - ln.y1) * (x - ln.x1) / (ln.x2 - ln.x1)
    except Exception:
        return NA


def line_set_xy1(ln, x, y):
    if isinstance(ln, Drawing):
        ln.x1, ln.y1 = x, y


def line_set_xy2(ln, x, y):
    if isinstance(ln, Drawing):
        ln.x2, ln.y2 = x, y


def drawing_get(attr):
    def g(obj, *a):
        return getattr(obj, attr, NA) if isinstance(obj, Drawing) else NA
    return g


def drawing_new(*a, **k):
    return Drawing()


def runtime_error(msg=""):
    raise PineRuntimeError(f"runtime.error: {msg}")


# ------------------------------------------------------------------------------------------------ arrays
class PArray(list):
    __slots__ = ()


def a_new(size=0, initial=NA, *a):
    n = 0 if isna(size) else int(size)
    return PArray([initial] * n)


def a_from(*vals):
    return PArray(list(vals))


def a_get(a, i):
    if i is None or (i.__class__ is float and i != i):
        return NA
    i = int(i)
    if i < 0:
        i += len(a)
    if i < 0 or i >= len(a):
        raise PineRuntimeError("array index out of bounds")
    return a[i]


def a_set(a, i, v):
    if i is None or (i.__class__ is float and i != i):
        return
    i = int(i)
    if i < 0:
        i += len(a)
    if i < 0 or i >= len(a):
        raise PineRuntimeError("array index out of bounds")
    a[i] = v


def a_push(a, v):
    a.append(v)


def a_pop(a):
    if not a:
        raise PineRuntimeError("pop from empty array")
    return a.pop()


def a_shift(a):
    if not a:
        raise PineRuntimeError("shift from empty array")
    return a.pop(0)


def a_unshift(a, v):
    a.insert(0, v)


def a_insert(a, i, v):
    a.insert(int(i), v)


def a_remove(a, i):
    i = int(i)
    if i < 0 or i >= len(a):
        raise PineRuntimeError("array index out of bounds")
    return a.pop(i)


def a_size(a):
    return len(a) if a is not None else NA


def a_clear(a):
    a.clear()


def _vals(a):
    return [v for v in a if not isna(v)]


def a_sum(a):
    v = _vals(a)
    return math.fsum(v) if v else NA


def a_avg(a):
    v = _vals(a)
    return math.fsum(v) / len(v) if v else NA


def a_max(a, nth=0):
    v = sorted(_vals(a), reverse=True)
    return v[int(nth)] if len(v) > nth else NA


def a_min(a, nth=0):
    v = sorted(_vals(a))
    return v[int(nth)] if len(v) > nth else NA


def a_stdev(a, biased=True):
    v = _vals(a)
    if len(v) < 2:
        return NA if not v else 0.0
    m = math.fsum(v) / len(v)
    d = len(v) if T(biased) else len(v) - 1
    return math.sqrt(math.fsum((x - m) ** 2 for x in v) / d)


def a_variance(a, biased=True):
    s = a_stdev(a, biased)
    return s * s if not isna(s) else NA


def a_median(a):
    v = sorted(_vals(a))
    if not v:
        return NA
    k = len(v)
    return v[k // 2] if k % 2 else (v[k // 2 - 1] + v[k // 2]) / 2


def a_sort(a, order=None):
    desc = order == "desc"
    a.sort(key=lambda x: (isna(x), x if not isna(x) else 0), reverse=desc)


def a_reverse(a):
    a.reverse()


def a_slice(a, i, j):
    return PArray(a[int(i):int(j)])


def a_copy(a):
    return PArray(a)


def a_concat(a, b):
    a.extend(b)
    return a


def a_indexof(a, v):
    try:
        return a.index(v)
    except ValueError:
        return -1


def a_includes(a, v):
    return v in a


def a_fill(a, v, i=0, j=None):
    j = len(a) if j is None or isna(j) else int(j)
    for k in range(int(i), j):
        a[k] = v


def a_first(a):
    if not a:
        raise PineRuntimeError("array is empty")
    return a[0]


def a_last(a):
    if not a:
        raise PineRuntimeError("array is empty")
    return a[-1]


def a_range(a):
    v = _vals(a)
    return max(v) - min(v) if v else NA


def a_abs(a):
    return PArray([abs(x) if not isna(x) else NA for x in a])


def a_percentrank(a, i):
    v = a[int(i)]
    return 100.0 * sum(1 for x in a if x <= v) / len(a) if a else NA


def a_join(a, sep=","):
    return sep.join(s_tostring(x) for x in a)


def a_sort_indices(a, order=None):
    idx = sorted(range(len(a)), key=lambda k: a[k], reverse=(order == "desc"))
    return PArray(idx)


# ------------------------------------------------------------------------------------------------ time
_TZ_OFFSETS = {"UTC": 0, "GMT": 0, "Etc/UTC": 0, "America/New_York": -5, "Asia/Shanghai": 8, "Asia/Hong_Kong": 8,
               "Asia/Singapore": 8, "Asia/Tokyo": 9, "Europe/London": 0, "Europe/Berlin": 1, "Asia/Kolkata": 5.5}


def _tz_offset_ms(tz):
    if tz is None or isna(tz):
        return 0
    if isinstance(tz, str):
        if tz in _TZ_OFFSETS:
            return int(_TZ_OFFSETS[tz] * 3_600_000)
        import re as _re
        m = _re.match(r"^(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", tz.strip())
        if m:
            sign = 1 if m.group(1) == "+" else -1
            return sign * (int(m.group(2)) * 3_600_000 + int(m.group(3) or 0) * 60_000)
    return 0


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def timestamp(*args):
    """timestamp(year, month, day, hour=0, minute=0, second=0) | timestamp(tz, y, m, d, ...) | timestamp(dateString)."""
    a = list(args)
    tz = None
    if a and isinstance(a[0], str) and len(a) == 1:
        return _parse_datestr(a[0])
    if a and isinstance(a[0], str):
        tz = a.pop(0)
    vals = [0 if isna(x) else int(x) for x in a] + [0] * 6
    y, mo, d, h, mi, s = vals[:6]
    if y <= 0:
        return NA
    mo = min(max(mo, 1), 12)
    last = calendar.monthrange(y, mo)[1] if 1 <= y <= 9999 else 28
    d = min(max(d, 1), last)
    try:
        base = calendar.timegm((y, mo, d, 0, 0, 0)) * 1000
    except Exception:
        return NA
    return base + h * 3_600_000 + mi * 60_000 + s * 1000 - _tz_offset_ms(tz)


def _parse_datestr(s):
    import re as _re
    s = s.strip()
    m = _re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h, mi, se = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        return calendar.timegm((y, mo, d, h, mi, se)) * 1000
    m = _re.match(r"^(\d{1,2})\s*([A-Za-z]{3})[a-z]*\s+(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2).lower(), 1), int(m.group(3))
        h, mi, se = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        return calendar.timegm((y, mo, d, h, mi, se)) * 1000
    m = _re.match(r"^([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        return calendar.timegm((int(m.group(3)), _MONTHS.get(m.group(1).lower(), 1), int(m.group(2)), 0, 0, 0)) * 1000
    return NA


def _utc(t):
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc)


def t_year(t, tz=None):
    return NA if isna(t) else _utc(t + _tz_offset_ms(tz)).year


def t_month(t, tz=None):
    return NA if isna(t) else _utc(t + _tz_offset_ms(tz)).month


def t_dayofmonth(t, tz=None):
    return NA if isna(t) else _utc(t + _tz_offset_ms(tz)).day


def t_dayofweek(t, tz=None):      # Pine: 1 = Sunday ... 7 = Saturday
    return NA if isna(t) else (_utc(t + _tz_offset_ms(tz)).isoweekday() % 7) + 1


def t_hour(t, tz=None):
    return NA if isna(t) else _utc(t + _tz_offset_ms(tz)).hour


def t_minute(t, tz=None):
    return NA if isna(t) else _utc(t + _tz_offset_ms(tz)).minute


def t_second(t, tz=None):
    return NA if isna(t) else _utc(t + _tz_offset_ms(tz)).second


def t_weekofyear(t, tz=None):
    return NA if isna(t) else _utc(t + _tz_offset_ms(tz)).isocalendar()[1]


def in_session(t, session, tz=None):
    """True if bar time t falls in a session string like '0930-1600' or '0930-1600:23456'."""
    import re as _re
    if not isinstance(session, str) or not session:
        return True
    if session.strip() in ("24x7", "0000-0000", "0000-2400"):
        return True
    t2 = t + _tz_offset_ms(tz)
    d = _utc(t2)
    minutes = d.hour * 60 + d.minute
    pine_dow = (d.isoweekday() % 7) + 1
    ok_any = False
    for part in session.split(","):
        m = _re.match(r"^\s*(\d{2})(\d{2})-(\d{2})(\d{2})(?::(\d+))?", part)
        if not m:
            continue
        a = int(m.group(1)) * 60 + int(m.group(2))
        b = int(m.group(3)) * 60 + int(m.group(4))
        days = m.group(5)
        if days and str(pine_dow) not in days:
            continue
        if a < b:
            if a <= minutes < b:
                ok_any = True
        elif a > b:
            if minutes >= a or minutes < b:
                ok_any = True
        else:
            ok_any = True
    return ok_any


def tf_to_ms(tf) -> int | None:
    """Pine timeframe string -> milliseconds ('' means chart timeframe -> None)."""
    if tf is None or tf == "" or isna(tf):
        return None
    s = str(tf).strip().upper()
    import re as _re
    m = _re.match(r"^(\d*)([SDWM]?)$", s)
    if not m:
        m2 = _re.match(r"^(\d+)\s*(MIN|M|H|D|W)$", s)
        if m2:
            n = int(m2.group(1))
            u = m2.group(2)
            return n * {"MIN": 60_000, "M": 60_000, "H": 3_600_000, "D": 86_400_000, "W": 604_800_000}[u]
        return None
    n = int(m.group(1)) if m.group(1) else 1
    u = m.group(2)
    if u == "":
        return n * 60_000
    if u == "S":
        return max(60_000, n * 1000)
    if u == "D":
        return n * 86_400_000
    if u == "W":
        return n * 604_800_000
    if u == "M":
        return n * 2_592_000_000
    return None


def tf_string(ms: int) -> str:
    if ms % 2_592_000_000 == 0 and ms >= 2_592_000_000:
        return f"{ms // 2_592_000_000}M" if ms // 2_592_000_000 > 1 else "M"
    if ms % 604_800_000 == 0:
        return f"{ms // 604_800_000}W" if ms // 604_800_000 > 1 else "W"
    if ms % 86_400_000 == 0:
        return f"{ms // 86_400_000}D" if ms // 86_400_000 > 1 else "D"
    return str(ms // 60_000)
