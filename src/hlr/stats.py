"""Dependence-aware statistics: HAC regression, stationary block bootstrap, Sharpe helpers, deflated Sharpe."""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats as sps
import statsmodels.api as sm

EULER_GAMMA = 0.5772156649015329


def hac_regression(y: np.ndarray, x: np.ndarray, maxlags: int) -> dict:
    X = sm.add_constant(np.asarray(x, dtype=float))
    m = sm.OLS(np.asarray(y, dtype=float), X, missing="drop").fit(cov_type="HAC", cov_kwds={"maxlags": int(maxlags)})
    return {"n": int(m.nobs), "alpha": float(m.params[0]), "beta": float(m.params[1]),
            "t_beta_hac": float(m.tvalues[1]), "r2": float(m.rsquared)}


def stationary_bootstrap_indices(n: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """Politis & Romano (1994) stationary bootstrap index sampler."""
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty(n, dtype=int)
    i = rng.integers(0, n)
    for k in range(n):
        if k > 0 and rng.random() >= p:
            i = (i + 1) % n
        else:
            i = rng.integers(0, n)
        idx[k] = i
    return idx


def block_bootstrap_ci(x: np.ndarray, stat=np.mean, mean_block: float = 5.0, n_boot: int = 2000,
                       alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    vals = np.empty(n_boot)
    for b in range(n_boot):
        vals[b] = stat(x[stationary_bootstrap_indices(n, mean_block, rng)])
    return (float(stat(x)), float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2)))


def sharpe(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    s = x.std(ddof=1)
    return float(x.mean() / s) if s > 0 and len(x) > 1 else np.nan


def sharpe_ann(x: np.ndarray, periods_per_year: float) -> float:
    return float(sharpe(x) * np.sqrt(periods_per_year))


def probabilistic_sharpe(sr: float, n: int, skew: float, kurt: float, sr0: float = 0.0) -> float:
    """PSR (Bailey & Lopez de Prado 2012): prob that true SR > sr0 given estimated sr over n obs (kurt = non-excess)."""
    if n < 3 or not np.isfinite(sr):
        return np.nan
    denom = np.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4.0 * sr ** 2))
    z = (sr - sr0) * np.sqrt(n - 1) / denom
    return float(sps.norm.cdf(z))


def deflated_sharpe(sr: float, n: int, skew: float, kurt: float, n_trials: int, var_sr_trials: float) -> dict:
    """DSR (Bailey & Lopez de Prado 2014). sr, skew, kurt are per-period; var_sr_trials is the variance of the
    per-period SR across the n_trials tested variants. Returns the benchmark SR0 and the deflated probability."""
    if n_trials <= 1 or var_sr_trials <= 0:
        sr0 = 0.0
    else:
        sd = np.sqrt(var_sr_trials)
        sr0 = sd * ((1 - EULER_GAMMA) * sps.norm.ppf(1 - 1.0 / n_trials) + EULER_GAMMA * sps.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    return {"sr0": float(sr0), "dsr": probabilistic_sharpe(sr, n, skew, kurt, sr0)}


def max_drawdown(cum: np.ndarray) -> float:
    cum = np.asarray(cum, dtype=float)
    peak = np.maximum.accumulate(cum)
    return float((cum - peak).min()) if len(cum) else np.nan


def describe_pnl(x: np.ndarray, per_year: float | None = None) -> dict:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return {"n": 0}
    wins = x[x > 0]; losses = x[x < 0]
    out = {"n": int(len(x)), "mean": float(x.mean()), "median": float(np.median(x)), "sd": float(x.std(ddof=1)) if len(x) > 1 else np.nan,
           "t_iid": float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 1 and x.std(ddof=1) > 0 else np.nan,
           "win_rate": float((x > 0).mean()), "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else np.nan,
           "total": float(x.sum()), "skew": float(sps.skew(x)) if len(x) > 2 else np.nan, "kurt": float(sps.kurtosis(x, fisher=False)) if len(x) > 3 else np.nan,
           "max_dd": max_drawdown(np.cumsum(x)), "sharpe_per_obs": sharpe(x)}
    if per_year:
        out["sharpe_ann"] = sharpe_ann(x, per_year)
    return out
