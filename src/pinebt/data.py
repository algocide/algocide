"""Bar data for the engine: resample Binance 1m klines to any Pine timeframe, attach funding, derive builtin series."""
from __future__ import annotations
import datetime as dt
import math
import os
from functools import lru_cache
import numpy as np
import pandas as pd

DATA_DIR = os.environ.get("PINEBT_DATA", "data/binance")
MINTICK = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.01}
NA = float("nan")


@lru_cache(maxsize=8)
def load_1m(symbol: str):
    df = pd.read_parquet(os.path.join(DATA_DIR, f"{symbol}_1m.parquet"))
    t = df["open_time"].to_numpy(np.int64)
    return (t, df["open"].to_numpy(float), df["high"].to_numpy(float), df["low"].to_numpy(float),
            df["close"].to_numpy(float), df["volume"].to_numpy(float))


@lru_cache(maxsize=8)
def load_funding(symbol: str):
    p = os.path.join(DATA_DIR, f"{symbol}_funding.parquet")
    if not os.path.exists(p):
        return np.array([], np.int64), np.array([], float)
    df = pd.read_parquet(p)
    return df["time"].to_numpy(np.int64), df["rate"].to_numpy(float)


WEEK_ANCHOR = 4 * 86_400_000          # 1970-01-05 is a Monday


def bin_index(t: np.ndarray, tf_ms: int) -> np.ndarray:
    if tf_ms >= 2_592_000_000:            # months
        months = tf_ms // 2_592_000_000
        d = pd.to_datetime(t, unit="ms")
        m = (d.year.to_numpy() - 1970) * 12 + d.month.to_numpy() - 1
        return (m // months).astype(np.int64)
    if tf_ms % 604_800_000 == 0:
        return (t - WEEK_ANCHOR) // tf_ms
    return t // tf_ms


def bin_open_time(b: np.ndarray, tf_ms: int) -> np.ndarray:
    if tf_ms >= 2_592_000_000:
        months = tf_ms // 2_592_000_000
        m = b * months
        y, mo = 1970 + m // 12, m % 12 + 1
        return np.array([int(dt.datetime(int(a), int(c), 1, tzinfo=dt.timezone.utc).timestamp() * 1000) for a, c in zip(y, mo)], np.int64)
    if tf_ms % 604_800_000 == 0:
        return b * tf_ms + WEEK_ANCHOR
    return b * tf_ms


@lru_cache(maxsize=64)
def resample(symbol: str, tf_ms: int):
    t, o, h, l, c, v = load_1m(symbol)
    b = bin_index(t, tf_ms)
    starts = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    ends = np.r_[starts[1:], len(b)]
    bo = bin_open_time(b[starts], tf_ms)
    ro = o[starts]
    rh = np.maximum.reduceat(h, starts)
    rl = np.minimum.reduceat(l, starts)
    rc = c[ends - 1]
    rv = np.add.reduceat(v, starts)
    if tf_ms >= 2_592_000_000:
        tc = np.r_[bo[1:], bo[-1] + 31 * 86_400_000]
    else:
        tc = bo + tf_ms
    ft, fr = load_funding(symbol)
    fund = np.zeros(len(bo))
    if len(ft):
        fb = np.searchsorted(bo, ft, side="right") - 1
        ok = (fb >= 0) & (fb < len(bo))
        np.add.at(fund, fb[ok], fr[ok])
        # months not yet published in the archive: carry the last 30 days' mean rate at 8h settlements
        last = ft[-1]
        tail = fr[ft > last - 30 * 86_400_000]
        if len(tail) and tc[-1] > last + 8 * 3_600_000:
            m = float(tail.mean())
            extra = np.arange(last + 8 * 3_600_000, tc[-1], 8 * 3_600_000)
            eb = np.searchsorted(bo, extra, side="right") - 1
            ok = (eb >= 0) & (eb < len(bo))
            np.add.at(fund, eb[ok], m)
    return bo, tc, ro, rh, rl, rc, rv, fund


class Bars:
    """Python-list bar data (list indexing is faster than numpy scalar access in the per-bar loop)."""

    def __init__(self, symbol: str, tf_ms: int, start_ms: int | None = None, end_ms: int | None = None,
                 max_bars: int | None = None, heikin_ashi: bool = False, arrays=None):
        if arrays is not None:
            bo, o, h, l, c, v = (np.asarray(x) for x in arrays[:6])
            bo = bo.astype(np.int64)
            tc = bo + tf_ms
            fund = np.asarray(arrays[6], float) if len(arrays) > 6 else np.zeros(len(bo))
        else:
            bo, tc, o, h, l, c, v, fund = resample(symbol, tf_ms)
        sel = np.ones(len(bo), bool)
        if start_ms is not None:
            sel &= bo >= start_ms
        if end_ms is not None:
            sel &= tc <= end_ms
        idx = np.flatnonzero(sel)
        if max_bars is not None and len(idx) > max_bars:
            idx = idx[-max_bars:]
        bo, tc, o, h, l, c, v, fund = (x[idx] for x in (bo, tc, o, h, l, c, v, fund))
        if heikin_ashi:
            hc = (o + h + l + c) / 4
            ho = np.empty_like(hc)
            ho[0] = (o[0] + c[0]) / 2
            for k in range(1, len(hc)):
                ho[k] = (ho[k - 1] + hc[k - 1]) / 2
            h = np.maximum.reduce([h, ho, hc])
            l = np.minimum.reduce([l, ho, hc])
            o, c = ho, hc
        self.symbol, self.tf_ms, self.n = symbol, tf_ms, len(bo)
        self.mintick = MINTICK.get(symbol, 0.01)
        self.T = bo.tolist(); self.TC = tc.tolist()
        self.O = o.tolist(); self.H = h.tolist(); self.L = l.tolist(); self.C = c.tolist(); self.V = v.tolist()
        self.FUND = fund.tolist()
        self.BI = list(range(self.n))
        self.HL2 = ((h + l) / 2).tolist(); self.HLC3 = ((h + l + c) / 3).tolist()
        self.OHLC4 = ((o + h + l + c) / 4).tolist(); self.HLCC4 = ((h + l + 2 * c) / 4).tolist()
        d = pd.to_datetime(bo, unit="ms", utc=True)
        self.YEAR = d.year.tolist(); self.MONTH = d.month.tolist(); self.DOM = d.day.tolist()
        self.DOW = ((d.dayofweek.to_numpy() + 1) % 7 + 1).tolist()     # Pine: Sunday = 1
        self.HOUR = d.hour.tolist(); self.MINUTE = d.minute.tolist(); self.SECOND = [0] * self.n
        self.WOY = d.isocalendar().week.astype(int).tolist()
        self.TDAY = (bo // 86_400_000 * 86_400_000).tolist()
        pc = np.r_[np.nan, c[:-1]]
        tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
        tr1 = tr.copy(); tr1[0] = h[0] - l[0]
        tr[0] = np.nan
        self.TR = tr.tolist(); self.TR1 = tr1.tolist()
        chg = np.r_[0.0, np.diff(c)]
        self.OBV = np.cumsum(np.sign(chg) * v).tolist()
        rng = h - l
        mfm = np.where(rng > 0, ((c - l) - (h - c)) / np.where(rng > 0, rng, 1), 0.0)
        self.ACCDIST = np.cumsum(mfm * v).tolist()
        self.III = ((2 * c - h - l) / np.where(rng > 0, rng, np.nan) * v).tolist()
        pcr = np.r_[np.nan, c[:-1]]
        self.PVT = np.nancumsum(np.where(np.isnan(pcr), 0, (c - pcr) / pcr * v)).tolist()
        self.WVAD = (np.where(rng > 0, (c - o) / np.where(rng > 0, rng, 1), 0.0) * v).tolist()
        trh = np.maximum(h, pc); trl = np.minimum(l, pc)
        ad = np.where(c > pc, c - trl, np.where(c < pc, c - trh, 0.0))
        self.WAD = np.nancumsum(ad).tolist()
        nvi = np.empty(self.n); pvi = np.empty(self.n)
        nvi[0] = pvi[0] = 1.0
        for k in range(1, self.n):
            r = (c[k] - c[k - 1]) / c[k - 1] if c[k - 1] else 0.0
            nvi[k] = nvi[k - 1] * (1 + r) if v[k] < v[k - 1] else nvi[k - 1]
            pvi[k] = pvi[k - 1] * (1 + r) if v[k] > v[k - 1] else pvi[k - 1]
        self.NVI = nvi.tolist(); self.PVI = pvi.tolist()
        day = bo // 86_400_000
        hlc3 = (h + l + c) / 3
        pv = hlc3 * v
        vw = np.empty(self.n)
        cpv = cv = 0.0; cur = None
        for k in range(self.n):
            if day[k] != cur:
                cur, cpv, cv = day[k], 0.0, 0.0
            cpv += pv[k]; cv += v[k]
            vw[k] = cpv / cv if cv else np.nan
        self.VWAP = vw.tolist()
