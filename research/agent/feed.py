"""Market feeds. LiveFeed = POST https://api.hyperliquid.xyz/info (candleSnapshot, allMids, l2Book, metaAndAssetCtxs,
clearinghouseState). ReplayFeed = the committed research datasets, served bar by bar so the same loop can be run
offline (used by the tests and the replay validation). Both expose the same interface."""
from __future__ import annotations
import os, sys, time
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(R, "src")); sys.path.insert(0, os.path.join(R, "..", "src"))

INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


class Feed:
    def candles(self, symbol: str, interval: str, n: int) -> pd.DataFrame: raise NotImplementedError
    def mid(self, symbol: str) -> float: raise NotImplementedError
    def quote(self, symbol: str) -> dict: raise NotImplementedError          # {bid, ask, half_spread_bps}
    def asset_ctx(self, symbol: str) -> dict: return {}                      # {funding, openInterest, dayNtlVlm, oraclePx, markPx}
    def now(self) -> pd.Timestamp: return pd.Timestamp.now(tz="UTC")


class LiveFeed(Feed):
    def __init__(self, base_url: str = "https://api.hyperliquid.xyz"):
        from hlr.hl_api import HLInfo
        self.api = HLInfo(base_url, cache_dir=None); self._ctx_cache = {}; self._ctx_time = 0.0

    def candles(self, symbol, interval, n):
        end = int(time.time() * 1000); start = end - INTERVAL_MS[interval] * (n + 2)
        rows = self.api.candles(symbol, interval, start, end, cache=False)
        df = pd.DataFrame(rows)
        for c in ["o", "h", "l", "c", "v"]: df[c] = df[c].astype(float)
        df["bucket"] = pd.to_datetime(df["t"].astype(int), unit="ms", utc=True); df["ts"] = pd.to_datetime(df["T"].astype(int) + 1, unit="ms", utc=True)
        df = df[df.ts <= self.now()]                                            # completed candles only
        return df[["ts", "bucket", "o", "h", "l", "c", "v"]].tail(n).reset_index(drop=True)

    def mid(self, symbol):
        dex = symbol.split(":")[0] if ":" in symbol else None
        mids = self.api.all_mids(dex); return float(mids[symbol])

    def quote(self, symbol):
        b = self.api.l2_book(symbol); lv = b["levels"]; bid = float(lv[0][0]["px"]); ask = float(lv[1][0]["px"])
        return {"bid": bid, "ask": ask, "half_spread_bps": (ask - bid) / (ask + bid) * 1e4, "bid_sz": float(lv[0][0]["sz"]), "ask_sz": float(lv[1][0]["sz"])}

    def asset_ctx(self, symbol):
        dex = symbol.split(":")[0] if ":" in symbol else None
        if time.time() - self._ctx_time > 30:
            self._ctx_cache = {}; self._ctx_time = time.time()
        if dex not in self._ctx_cache:
            meta, ctxs = self.api.meta_and_asset_ctxs(dex); self._ctx_cache[dex] = {a["name"]: c for a, c in zip(meta["universe"], ctxs)}
        c = self._ctx_cache[dex].get(symbol, {})
        return {k: (float(c[k]) if c.get(k) is not None else None) for k in ("funding", "openInterest", "dayNtlVlm", "oraclePx", "markPx", "premium")}


class ReplayFeed(Feed):
    """Serves the research datasets as of a movable clock. Candle data: hyperdata_candles_{15m,1h}.parquet (BTC/ETH).
    Sampled data: tohshi_mid_15m.parquet (two-point bars). set_time() advances the clock; only bars whose close time
    is <= clock are visible (no look-ahead)."""
    def __init__(self, symbols, interval="15m", source="candles", half_spread_bps=None):
        self.symbols = symbols; self.interval = interval; self.source = source; self._t = None; self.frames = {}
        from hlr2.costs import IMPACT_HALF_SPREAD_BPS
        self.hs = half_spread_bps or IMPACT_HALF_SPREAD_BPS
        if source == "candles":
            df = pd.read_parquet(os.path.join(R, "data", "raw", f"hyperdata_candles_{interval}.parquet"))
            mins = {"15m": 15, "1h": 60}[interval]
            for s in symbols:
                d = df[df.symbol == s].sort_values("ts")
                self.frames[s] = pd.DataFrame({"ts": pd.to_datetime(d.ts, utc=True) + pd.Timedelta(minutes=mins), "bucket": pd.to_datetime(d.ts, utc=True), "o": d.open.values, "h": d.high.values, "l": d.low.values, "c": d.close.values, "v": d.volume.values}).reset_index(drop=True)
        else:
            raw = pd.read_parquet(os.path.join(R, "data", "raw", "tohshi_mid_15m.parquet")); raw = raw[raw.symbol.isin(symbols)]
            for s in symbols:
                d = raw[raw.symbol == s].sort_values("collected_at"); c = d.price.values.astype(float); o = np.r_[np.nan, c[:-1]]
                self.frames[s] = pd.DataFrame({"ts": pd.to_datetime(d.collected_at, utc=True).values, "bucket": pd.to_datetime(d.observed_at, utc=True).values, "o": o, "h": np.fmax(o, c), "l": np.fmin(o, c), "c": c, "v": np.nan}).reset_index(drop=True)
        self.all_times = pd.DatetimeIndex(sorted(set().union(*[set(f.ts) for f in self.frames.values()])))

    def set_time(self, t): self._t = pd.Timestamp(t)
    def now(self): return self._t
    def candles(self, symbol, interval, n):
        f = self.frames[symbol]; return f[f.ts <= self._t].tail(n).reset_index(drop=True)
    def mid(self, symbol):
        f = self.frames[symbol]; v = f[f.ts <= self._t]
        return float(v.c.iloc[-1]) if len(v) else float("nan")
    def quote(self, symbol):
        m = self.mid(symbol); hs = self.hs.get(symbol, 2.5); return {"bid": m * (1 - hs / 1e4), "ask": m * (1 + hs / 1e4), "half_spread_bps": hs}
