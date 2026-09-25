"""Hourly funding tables per symbol: DataFrame(ts, rate) where rate is the hourly funding rate (fraction of notional,
positive = longs pay). Sources: xyz markets -> data/derived/funding_hourly.parquet (prior session build of
MiggoyGHP/hyperliquid-rwa-dashboard fundingHistory bundles, through 2026-09-23); BTC/ETH -> hyperdata fundingHistory
(through 2026-07-17). Outside coverage no funding is charged (documented limitation)."""
import os, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
_cache = {}


def load_funding(symbols):
    out = {}
    need_xyz = [s for s in symbols if s.startswith("xyz:")]; need_nat = [s for s in symbols if ":" not in s]
    if need_xyz:
        if "xyz" not in _cache:
            f = pd.read_parquet(os.path.join(R, "..", "data", "derived", "funding_hourly.parquet"))
            _cache["xyz"] = f
        f = _cache["xyz"]
        tcol = "ts" if "ts" in f.columns else "time"; rcol = "funding" if "funding" in f.columns else ("r" if "r" in f.columns else "fundingRate")
        for s in need_xyz:
            g = f[f.coin == s]
            if len(g): out[s] = pd.DataFrame({"ts": pd.to_datetime(g[tcol], utc=True), "rate": g[rcol].astype(float)}).sort_values("ts")
    if need_nat:
        if "nat" not in _cache: _cache["nat"] = pd.read_parquet(os.path.join(R, "data", "raw", "hyperdata_funding.parquet"))
        f = _cache["nat"]
        for s in need_nat:
            g = f[f.symbol == s]
            if len(g): out[s] = pd.DataFrame({"ts": pd.to_datetime(g.ts, utc=True), "rate": g.funding_rate.astype(float)}).sort_values("ts")
    return out
