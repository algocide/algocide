"""Causal indicators on numpy arrays. Every value at index i uses data at indices <= i only."""
from __future__ import annotations
import numpy as np


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan); a = 2.0 / (n + 1); prev = np.nan
    for i, v in enumerate(x):
        if not np.isfinite(v):
            out[i] = prev; continue
        prev = v if not np.isfinite(prev) else prev + a * (v - prev); out[i] = prev
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    s = np.convolve(np.nan_to_num(x), np.ones(n), "full")[: len(x)]
    out = s / n; out[: n - 1] = np.nan
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1: i + 1]
        if np.all(np.isfinite(w)): out[i] = w.std(ddof=0)
    return out


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    pc = np.roll(c, 1); pc[0] = np.nan
    return np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))


def atr(h, l, c, n: int = 14, sampled: bool = False) -> np.ndarray:
    """ATR as EMA(n) of true range. For sampled two-point bars the true range collapses to |close - prev close|;
    we scale by 2.0 (Brownian ratio E[range]/E|dX| = 2) so stop distances in 'ATR units' are comparable. Approximation."""
    tr = true_range(h, l, c)
    if sampled:
        tr = 2.0 * tr
    return ema(tr, n)


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = np.max(x[i - n + 1: i + 1])
    return out


def rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = np.min(x[i - n + 1: i + 1])
    return out


def rolling_pct_rank(x: np.ndarray, n: int) -> np.ndarray:
    """Percentile rank (0..1) of x[i] within the trailing window x[i-n+1..i] (inclusive)."""
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1: i + 1]
        if np.all(np.isfinite(w)): out[i] = (w < x[i]).mean()
    return out


def bollinger(c: np.ndarray, n: int = 20, k: float = 2.0):
    m = sma(c, n); s = rolling_std(c, n)
    return m, m + k * s, m - k * s, (2 * k * s) / m
