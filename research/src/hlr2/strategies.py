"""Strategy families with coarse, predeclared parameters (see docs/PROTOCOL_FREEZE.md). All rules are causal: a signal
at grid index k uses only bars completed at or before k. Higher-timeframe (1h) values are taken from COMPLETED 1h bars
via data.tf_view (map[k] = last completed 1h bar at or before k)."""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from .indicators import ema, sma, atr, bollinger, rolling_max, rolling_min, rolling_pct_rank
from .data import tf_view


@dataclass
class Signal:
    dir: int
    stop: float
    target: float | None = None
    tag: str = ""
    state: dict = field(default_factory=dict)


class Base:
    name = "base"
    params: dict = {}
    def __init__(self, **kw):
        self.params = {**self.params, **kw}
    def describe(self) -> str:
        return f"{self.name}({', '.join(f'{k}={v}' for k, v in sorted(self.params.items()))})"
    def prepare(self, panel):
        st = {"panel": panel, "sampled": panel.kind == "sampled", "align": "session" if panel.inst[panel.symbols[0]].session_close.notna().any() else "utc"}
        for s in panel.symbols:
            st[s] = self.prep_symbol(panel, s, st)
        return st
    def prep_symbol(self, panel, s, st): return {}
    def signal(self, s, k, st): return None
    def exit(self, s, k, pos, st): return None
    def trail(self, s, k, pos, st): return None


def _tf(panel, s, tf, st):
    v = tf_view(panel, s, tf, st["align"])
    v["atr"] = atr(v["h"], v["l"], v["c"], 14, sampled=st["sampled"])
    return v


class MATrend(Base):
    """1. EMA fast/slow crossover on the signal timeframe. Entry on the bar that completes the cross; stop 2 ATR(14);
    exit on the opposite cross (same timeframe) or session end. Optional strength filter |fast-slow| > 0.5 ATR."""
    name = "ma_trend"; params = {"tf": "15m", "fast": 10, "slow": 40, "filter": False, "atr_mult": 2.0}
    def prep_symbol(self, panel, s, st):
        v = _tf(panel, s, self.params["tf"], st); v["f"] = ema(v["c"], self.params["fast"]); v["sl"] = ema(v["c"], self.params["slow"]); return v
    def signal(self, s, k, st):
        v = st[s]
        if not v["is_close"][k]: return None
        i = v["map"][k]
        if i < 1 or not np.isfinite(v["atr"][i]) or not np.isfinite(v["sl"][i]) or not np.isfinite(v["sl"][i - 1]): return None
        d_now = v["f"][i] - v["sl"][i]; d_prev = v["f"][i - 1] - v["sl"][i - 1]
        if not self.params["filter"]:
            if d_prev <= 0 < d_now: dr = 1
            elif d_prev >= 0 > d_now: dr = -1
            else: return None
        else:
            # confirmed cross (F1b, redefined 2026-09-25 after the original filter produced zero trades because |fast-slow|
            # is ~0 on the crossing bar): a sign change occurred within the last 5 bars and |fast-slow| exceeds 0.5 ATR
            # for the first time since that cross.
            if i < 6: return None
            th = 0.5 * v["atr"][i]
            if not (abs(d_now) >= th and abs(d_prev) < th): return None
            dd = v["f"][i - 5: i + 1] - v["sl"][i - 5: i + 1]
            if d_now > 0 and np.any(dd[:-1] <= 0): dr = 1
            elif d_now < 0 and np.any(dd[:-1] >= 0): dr = -1
            else: return None
        px = v["c"][i]; a = v["atr"][i]
        if a <= 0: return None
        return Signal(dr, px - dr * self.params["atr_mult"] * a, None, self.describe(), {"i": i})
    def exit(self, s, k, pos, st):
        v = st[s]
        if not v["is_close"][k]: return None
        i = v["map"][k]
        if i < 1: return None
        d_now = v["f"][i] - v["sl"][i]
        if (pos["dir"] == 1 and d_now < 0) or (pos["dir"] == -1 and d_now > 0): return "opposite_cross"
        return None


class BollingerMR(Base):
    """2. Bollinger(20,k) mean reversion: previous bar closed outside the band, this bar closes back inside (re-entry) and
    remains on the far side of the middle band. Optional range regime: |EMA20[i]-EMA20[i-5]| < 0.5 ATR. Stop: extreme
    of the last 3 bars +/- 0.5 ATR; target: middle band; time exit 8 bars."""
    name = "bb_mr"; params = {"tf": "15m", "n": 20, "k": 2.0, "regime": False, "max_bars": 8}
    def prep_symbol(self, panel, s, st):
        v = _tf(panel, s, self.params["tf"], st); v["m"], v["u"], v["lo"], v["bw"] = bollinger(v["c"], self.params["n"], self.params["k"]); v["e20"] = ema(v["c"], 20); return v
    def signal(self, s, k, st):
        v = st[s]
        if not v["is_close"][k]: return None
        i = v["map"][k]
        if i < 6 or not np.isfinite(v["u"][i]) or not np.isfinite(v["u"][i - 1]) or not np.isfinite(v["atr"][i]): return None
        c, pc = v["c"][i], v["c"][i - 1]
        if self.params["regime"] and abs(v["e20"][i] - v["e20"][i - 5]) >= 0.5 * v["atr"][i]: return None
        a = v["atr"][i]
        if pc < v["lo"][i - 1] and v["lo"][i] <= c < v["m"][i]:
            stop = min(v["l"][i - 2: i + 1]) - 0.5 * a
            return Signal(1, stop, v["m"][i], self.describe(), {"i": i})
        if pc > v["u"][i - 1] and v["u"][i] >= c > v["m"][i]:
            stop = max(v["h"][i - 2: i + 1]) + 0.5 * a
            return Signal(-1, stop, v["m"][i], self.describe(), {"i": i})
        return None
    def exit(self, s, k, pos, st):
        v = st[s]
        if v["is_close"][k] and v["map"][k] - pos["state"]["i"] >= self.params["max_bars"]: return "time"
        return None


class VolCompressionBO(Base):
    """3. Bandwidth(20) percentile over 60 bars <= p at bar i-1, and close beyond the band at bar i. Stop: opposite band;
    trail: 2 ATR from the best close since entry."""
    name = "vol_comp_bo"; params = {"tf": "15m", "pct": 0.2, "trail_atr": 2.0}
    def prep_symbol(self, panel, s, st):
        v = _tf(panel, s, self.params["tf"], st); v["m"], v["u"], v["lo"], v["bw"] = bollinger(v["c"], 20, 2.0); v["bwr"] = rolling_pct_rank(v["bw"], 60); return v
    def signal(self, s, k, st):
        v = st[s]
        if not v["is_close"][k]: return None
        i = v["map"][k]
        if i < 61 or not np.isfinite(v["bwr"][i - 1]) or not np.isfinite(v["atr"][i]): return None
        if v["bwr"][i - 1] > self.params["pct"]: return None
        c = v["c"][i]
        if c > v["u"][i]: return Signal(1, v["lo"][i], None, self.describe(), {"i": i, "best": c})
        if c < v["lo"][i]: return Signal(-1, v["u"][i], None, self.describe(), {"i": i, "best": c})
        return None
    def trail(self, s, k, pos, st):
        v = st[s]
        if not v["is_close"][k]: return None
        i = v["map"][k]; c = v["c"][i]; a = v["atr"][i]
        if pos["dir"] == 1: pos["state"]["best"] = max(pos["state"]["best"], c); return pos["state"]["best"] - self.params["trail_atr"] * a
        pos["state"]["best"] = min(pos["state"]["best"], c); return pos["state"]["best"] + self.params["trail_atr"] * a


class ChannelBO(Base):
    """4. Close beyond the prior N-bar high/low. Stop 1.5 ATR; trail = N-bar opposite extreme (ratchet)."""
    name = "channel_bo"; params = {"tf": "15m", "n": 24, "atr_mult": 1.5}
    def prep_symbol(self, panel, s, st):
        v = _tf(panel, s, self.params["tf"], st); n = self.params["n"]
        hh = rolling_max(v["h"], n); ll = rolling_min(v["l"], n)
        v["hh_prev"] = np.roll(hh, 1); v["ll_prev"] = np.roll(ll, 1); v["hh_prev"][0] = np.nan; v["ll_prev"][0] = np.nan
        v["ll"] = ll; v["hh"] = hh; return v
    def signal(self, s, k, st):
        v = st[s]
        if not v["is_close"][k]: return None
        i = v["map"][k]
        if i < self.params["n"] + 1 or not np.isfinite(v["hh_prev"][i]) or not np.isfinite(v["atr"][i]): return None
        c = v["c"][i]; a = v["atr"][i] * self.params["atr_mult"]
        if c > v["hh_prev"][i]: return Signal(1, c - a, None, self.describe(), {"i": i})
        if c < v["ll_prev"][i]: return Signal(-1, c + a, None, self.describe(), {"i": i})
        return None
    def trail(self, s, k, pos, st):
        v = st[s]
        if not v["is_close"][k]: return None
        i = v["map"][k]
        return v["ll"][i] if pos["dir"] == 1 else v["hh"][i]


class TrendPullback(Base):
    """5. 1h regime (EMA20 vs EMA50 on completed 1h bars) + 15m pullback/resumption. def A: close dipped below EMA10(15m)
    within the last 6 bars and now closes above it (long); def B: same with EMA20(15m). Stop: min low of the last 6 bars
    - 0.5 ATR(15m); target = R multiple; time exit 16 bars."""
    name = "trend_pullback"; params = {"defn": "A", "target_r": 2.0, "max_bars": 16}
    def prep_symbol(self, panel, s, st):
        v = _tf(panel, s, "15m", st); h = _tf(panel, s, "1h", st)
        h["e20"] = ema(h["c"], 20); h["e50"] = ema(h["c"], 50); v["h"]; v["hv"] = h
        v["ep"] = ema(v["c"], 10 if self.params["defn"] == "A" else 20); return v
    def signal(self, s, k, st):
        v = st[s]; h = v["hv"]; j = h["map"][k]
        if j < 0 or not np.isfinite(h["e50"][j]): return None
        regime = 1 if h["e20"][j] > h["e50"][j] else -1
        i = v["map"][k]
        if i < 7 or not np.isfinite(v["ep"][i - 6]) or not np.isfinite(v["atr"][i]): return None
        c = v["c"][i]; e = v["ep"]
        if regime == 1:
            if c > e[i] and v["c"][i - 1] <= e[i - 1] and np.any(v["c"][i - 6: i] < e[i - 6: i]):
                stop = min(v["l"][i - 6: i + 1]) - 0.5 * v["atr"][i]
                if stop >= c: return None
                return Signal(1, stop, c + self.params["target_r"] * (c - stop), self.describe(), {"i": i})
        else:
            if c < e[i] and v["c"][i - 1] >= e[i - 1] and np.any(v["c"][i - 6: i] > e[i - 6: i]):
                stop = max(v["h"][i - 6: i + 1]) + 0.5 * v["atr"][i]
                if stop <= c: return None
                return Signal(-1, stop, c - self.params["target_r"] * (stop - c), self.describe(), {"i": i})
        return None
    def exit(self, s, k, pos, st):
        v = st[s]
        if v["map"][k] - pos["state"]["i"] >= self.params["max_bars"]: return "time"
        return None


class OpeningRange(Base):
    """6. Opening-range breakout (US session only): range = high/low of the first `or_min` minutes (2 or 4 bars of 15m).
    First close beyond the range (after the range window) enters; stop = range midpoint; target = target_r x risk or
    session end. One trade per symbol per session."""
    name = "orb"; params = {"or_min": 30, "target_r": 2.0}
    def prep_symbol(self, panel, s, st):
        d = panel.inst[s]; v = _tf(panel, s, "15m", st)
        sd = d.session_date.values; nb = self.params["or_min"] // 15
        v["or_hi"] = np.full(len(d), np.nan); v["or_lo"] = np.full(len(d), np.nan); v["or_ready"] = np.zeros(len(d), bool); v["sd"] = sd
        start = 0
        while start < len(d):
            end = start
            while end < len(d) and sd[end] == sd[start]: end += 1
            if sd[start] is not None and end - start >= nb:
                ins = d.in_session.values[start:end]
                if ins.any():
                    first = start + int(np.argmax(ins))
                    if first + nb <= end and d.valid.values[first: first + nb].all():
                        hi = np.max(v["h"][first: first + nb]); lo = np.min(v["l"][first: first + nb])
                        v["or_hi"][first + nb: end] = hi; v["or_lo"][first + nb: end] = lo; v["or_ready"][first + nb: end] = True
            start = end
        return v
    def signal(self, s, k, st):
        v = st[s]
        if not v["or_ready"][k]: return None
        if st.get(("orb_done", s)) == v["sd"][k]: return None
        c = v["c"][k]; hi, lo = v["or_hi"][k], v["or_lo"][k]; mid = 0.5 * (hi + lo)
        if c > hi and mid < c:
            st[("orb_done", s)] = v["sd"][k]; r = c - mid
            return Signal(1, mid, (c + self.params["target_r"] * r) if self.params["target_r"] else None, self.describe(), {"i": k})
        if c < lo and mid > c:
            st[("orb_done", s)] = v["sd"][k]; r = mid - c
            return Signal(-1, mid, (c - self.params["target_r"] * r) if self.params["target_r"] else None, self.describe(), {"i": k})
        return None


class SessionLong(Base):
    """Baseline: session-matched long exposure. Enter long at the first enterable bar of each session with a wide stop
    (10 ATR, effectively never hit), exit at session end. Notional is capped by the leverage rule as for strategies."""
    name = "baseline_session_long"; params = {}
    def prep_symbol(self, panel, s, st):
        v = _tf(panel, s, "15m" if panel.bar_minutes == 15 else "1h", st); v["sd"] = panel.inst[s].session_date.values; return v
    def signal(self, s, k, st):
        v = st[s]
        if st.get(("done", s)) == v["sd"][k] or v["sd"][k] is None or not np.isfinite(v["atr"][k]): return None
        st[("done", s)] = v["sd"][k]
        return Signal(1, v["c"][k] - 10 * v["atr"][k], None, self.describe(), {"i": k})


REGISTRY = {c.name: c for c in [MATrend, BollingerMR, VolCompressionBO, ChannelBO, TrendPullback, OpeningRange, SessionLong]}
