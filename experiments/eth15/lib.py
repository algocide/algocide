"""ETH 15-minute up/down: causal features, strategy families, evaluation and Kelly sizing (docs/eth15/plan.md).

Every feature for candle t uses only candles before t (and the minutes of candle t-1), so a signal is known at t's open.
A signal is +1 (bet Up), -1 (bet Down) or 0 (no bet). Up wins if close >= open.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

PAYOUT = 1.8
FEE = 0.0006
WIN_NET = PAYOUT - 1 - FEE          # a = 0.7994
LOSS_NET = 1 + FEE                  # c = 1.0006
BREAK_EVEN = LOSS_NET / (WIN_NET + LOSS_NET)     # = (1 + fee) / 1.8 = 0.55589
BAR = 0.57
MIN_BETS = 200


def kelly(p: float) -> float:
    """Full Kelly stake fraction for win probability p with net odds WIN_NET and net loss LOSS_NET (0 if no edge)."""
    return max(0.0, (p * WIN_NET - (1 - p) * LOSS_NET) / (WIN_NET * LOSS_NET))


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features(c: pd.DataFrame) -> pd.DataFrame:
    """c: 15-minute candles sorted by time. Returns one row of causal features per candle."""
    f = pd.DataFrame(index=c.index)
    r = c.close / c.open - 1
    d = np.where(r >= 0, 1, -1)
    sd96 = r.rolling(96, min_periods=48).std()
    f["r1"] = r.shift(1)
    f["z1"] = (r / sd96).shift(1)
    for h in (4, 16, 96):
        rh = c.close / c.open.shift(h - 1) - 1              # return of the last h candles, ending at this candle
        f[f"z{h}"] = (rh / (sd96 * math.sqrt(h))).shift(1)
    grp = pd.Series(d, index=c.index).ne(pd.Series(d, index=c.index).shift()).cumsum()
    streak = pd.Series(d, index=c.index).groupby(grp).cumcount() + 1
    f["streak"] = (streak * d).shift(1)
    rng = (c.high - c.low).replace(0, np.nan)
    f["upper_wick"] = ((c.high - np.maximum(c.open, c.close)) / rng).shift(1)
    f["lower_wick"] = ((np.minimum(c.open, c.close) - c.low) / rng).shift(1)
    lr = np.log(rng / c.close)
    f["range_z"] = ((lr - lr.rolling(96, min_periods=48).mean()) / lr.rolling(96, min_periods=48).std()).shift(1)
    lv = np.log(c.volume.replace(0, np.nan))
    f["vol_z"] = ((lv - lv.rolling(96, min_periods=48).mean()) / lv.rolling(96, min_periods=48).std()).shift(1)
    f["taker"] = (c.taker_buy / c.volume.replace(0, np.nan) - 0.5).shift(1)
    f["taker4"] = (c.taker_buy.rolling(4).sum() / c.volume.rolling(4).sum() - 0.5).shift(1)
    f["rsi14"] = rsi(c.close, 14).shift(1)
    f["rsi3"] = rsi(c.close, 3).shift(1)
    m20, s20 = c.close.rolling(20).mean(), c.close.rolling(20).std()
    f["boll_z"] = ((c.close - m20) / s20).shift(1)
    br = c.btc_close / c.btc_open - 1
    bsd = br.rolling(96, min_periods=48).std()
    f["btc_z1"] = (br / bsd).shift(1)
    f["resid_z"] = f.z1 - f.btc_z1                          # ETH moved more (+) or less (-) than BTC last candle
    f["last1"] = (c.ret_last1 / (sd96 / math.sqrt(15))).shift(1)
    f["last5"] = (c.ret_last5 / (sd96 / math.sqrt(3))).shift(1)
    f["funding"] = c.funding * 1e4                          # basis points per 8h, latest published before the open
    mins = (c.t // 60_000) % 1440
    f["slot"] = (mins // 15).astype(int)
    f["hour"] = (mins // 60).astype(int)
    f["dow"] = pd.to_datetime(c.t, unit="ms").dt.dayofweek.values
    f["pre_funding"] = f.slot.isin([31, 63, 95]).astype(int)    # candle ending at 08:00, 16:00, 00:00 UTC
    f["post_funding"] = f.slot.isin([0, 32, 64]).astype(int)
    f["vol96"] = sd96.shift(1)
    return f


ML_FEATURES = ["z1", "z4", "z16", "z96", "streak", "upper_wick", "lower_wick", "range_z", "vol_z", "taker", "taker4",
               "rsi14", "rsi3", "boll_z", "btc_z1", "resid_z", "last1", "last5", "funding", "slot", "dow",
               "pre_funding", "post_funding", "vol96"]


def sgn(x):
    return np.sign(np.nan_to_num(np.asarray(x, float))).astype(np.int8)


# ---------------------------------------------------------------- rule families: f(features, **params) -> signal
def fam_prev(f, k, mode):
    """Previous candle's move, |z| > k: follow it (mode=1) or fade it (mode=-1)."""
    s = sgn(f.z1) * mode
    return np.where(f.z1.abs() > k, s, 0).astype(np.int8)


def fam_horizon(f, h, k, mode):
    z = f[f"z{h}"]
    return np.where(z.abs() > k, sgn(z) * mode, 0).astype(np.int8)


def fam_streak(f, n, mode):
    s = f.streak.fillna(0)
    return np.where(s.abs() >= n, sgn(s) * mode, 0).astype(np.int8)


def fam_slot(f, slot, side):
    return np.where(f.slot == slot, side, 0).astype(np.int8)


def fam_dow_hour(f, dow, hour, side):
    return np.where((f.dow == dow) & (f.hour == hour), side, 0).astype(np.int8)


def fam_funding(f, when, k, mode):
    """Around funding timestamps: bet against (mode=-1) or with (mode=1) the funding sign when |funding| > k bps."""
    col = f.pre_funding if when == "pre" else (f.post_funding if when == "post" else pd.Series(1, index=f.index))
    fr = f.funding.fillna(0)
    return np.where((col == 1) & (fr.abs() > k), sgn(fr) * mode, 0).astype(np.int8)


def fam_wick(f, side, x):
    """Long lower wick -> Up (side=1 means follow the wick's rejection), long upper wick -> Down; side=-1 reverses."""
    s = np.where(f.lower_wick > x, 1, np.where(f.upper_wick > x, -1, 0))
    return (s * side).astype(np.int8)


def fam_rsi(f, col, lo, mode):
    v = f[col]
    s = np.where(v < lo, 1, np.where(v > 100 - lo, -1, 0))       # mean reversion when mode=1
    return (s * mode).astype(np.int8)


def fam_boll(f, k, mode):
    z = f.boll_z
    s = np.where(z < -k, 1, np.where(z > k, -1, 0))
    return (s * mode).astype(np.int8)


def fam_volmove(f, vk, k, mode):
    """Big previous move on unusual volume: follow or fade."""
    cond = (f.vol_z > vk) & (f.z1.abs() > k)
    return np.where(cond, sgn(f.z1) * mode, 0).astype(np.int8)


def fam_taker(f, col, d, mode):
    v = f[col]
    return np.where(v.abs() > d, sgn(v) * mode, 0).astype(np.int8)


def fam_resid(f, k, mode):
    """ETH vs BTC divergence last candle: mode=-1 bets ETH catches up (fades its residual)."""
    v = f.resid_z
    return np.where(v.abs() > k, sgn(v) * mode, 0).astype(np.int8)


def fam_last(f, col, k, mode):
    v = f[col]
    return np.where(v.abs() > k, sgn(v) * mode, 0).astype(np.int8)


def fam_combo(f, k, vk, rsi_lo):
    """Fade a big previous move on high volume only when RSI(3) agrees it is stretched."""
    cond = (f.z1.abs() > k) & (f.vol_z > vk) & ((f.rsi3 < rsi_lo) | (f.rsi3 > 100 - rsi_lo))
    return np.where(cond, -sgn(f.z1), 0).astype(np.int8)


FAMILIES = {"prev": fam_prev, "horizon": fam_horizon, "streak": fam_streak, "slot": fam_slot,
            "dow_hour": fam_dow_hour, "funding": fam_funding, "wick": fam_wick, "rsi": fam_rsi, "boll": fam_boll,
            "volmove": fam_volmove, "taker": fam_taker, "resid": fam_resid, "last": fam_last, "combo": fam_combo}


def candidates():
    """All rule candidates, in a fixed order."""
    out = []
    add = lambda fam, **p: out.append((fam, p))
    for mode in (1, -1):
        for k in (0, 0.5, 1, 1.5, 2, 2.5, 3, 4):
            add("prev", k=k, mode=mode)
        for h in (4, 16, 96):
            for k in (0, 1, 2, 3):
                add("horizon", h=h, k=k, mode=mode)
        for n in range(2, 10):
            add("streak", n=n, mode=mode)
        for when in ("pre", "post", "any"):
            for k in (0, 1, 3, 5):
                add("funding", when=when, k=k, mode=mode)
        for x in (0.5, 0.6, 0.7, 0.8):
            add("wick", side=mode, x=x)
        for col, los in (("rsi14", (15, 20, 25, 30)), ("rsi3", (5, 10, 15, 20))):
            for lo in los:
                add("rsi", col=col, lo=lo, mode=mode)
        for k in (1.5, 2, 2.5, 3):
            add("boll", k=k, mode=mode)
        for vk in (1, 2):
            for k in (1, 2, 3):
                add("volmove", vk=vk, k=k, mode=mode)
        for col, ds in (("taker", (0.02, 0.05, 0.1, 0.15)), ("taker4", (0.02, 0.05, 0.1))):
            for d in ds:
                add("taker", col=col, d=d, mode=mode)
        for k in (1, 1.5, 2, 3):
            add("resid", k=k, mode=mode)
        for col in ("last1", "last5"):
            for k in (1, 2, 3):
                add("last", col=col, k=k, mode=mode)
    for side in (1, -1):
        for slot in range(96):
            add("slot", slot=slot, side=side)
        for dow in range(7):
            for hour in range(24):
                add("dow_hour", dow=dow, hour=hour, side=side)
    for k in (1.5, 2, 3):
        for vk in (0.5, 1, 2):
            for lo in (10, 20):
                add("combo", k=k, vk=vk, rsi_lo=lo)
    return out


def signal(f: pd.DataFrame, fam: str, params: dict) -> np.ndarray:
    return FAMILIES[fam](f, **params)


def score(sig: np.ndarray, up: np.ndarray, mask=None) -> tuple[int, int]:
    bet = sig != 0
    if mask is not None:
        bet = bet & mask
    win = bet & (((sig == 1) & (up == 1)) | ((sig == -1) & (up == 0)))
    return int(bet.sum()), int(win.sum())


def wilson(w: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = w / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (mid - half, mid + half)


def binom_p_above(w: int, n: int, p0: float = BREAK_EVEN) -> float:
    """One-sided p-value of seeing >= w wins in n bets if the true win rate were p0."""
    from scipy.stats import binom
    return float(binom.sf(w - 1, n, p0)) if n else float("nan")
