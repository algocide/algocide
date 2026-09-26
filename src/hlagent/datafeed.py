"""Data feeds: everything the state engine needs for one coin at one decision time.

  * HyperliquidFeed - read-only info API through hlr.hl_api.HLInfo (l2Book, candleSnapshot, metaAndAssetCtxs).
                      Not exercised live in this session (network policy blocks api.hyperliquid.xyz).
  * SyntheticFeed   - deterministic geometric-Brownian path with a synthetic book. FOR TESTS AND DRY RUNS ONLY; the
                      loop stamps every record produced on it as synthetic so it can never be mistaken for evidence.
  * ReplayFeed      - candles from a recorded list (e.g. a Parquet/JSONL dump) with a synthetic top of book.
"""
from __future__ import annotations
import math
import random
from dataclasses import dataclass
from typing import Optional
from .schema import AssetCtx, Candle, L2Snapshot, Level


@dataclass(frozen=True)
class FeedSnapshot:
    l2: L2Snapshot
    candles: list[Candle]
    ctx: AssetCtx
    synthetic: bool = False


INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def synthetic_book(coin: str, ts_ms: int, mid: float, spread_bps: float, depth_usd: float, skew: float = 0.0,
                   levels: int = 5) -> L2Snapshot:
    half = mid * spread_bps / 2e4
    bids, asks = [], []
    for i in range(levels):
        px_b = mid - half * (1 + i)
        px_a = mid + half * (1 + i)
        sz_b = depth_usd * (1 + skew) / levels / px_b
        sz_a = depth_usd * (1 - skew) / levels / px_a
        bids.append(Level(px=px_b, sz=sz_b)); asks.append(Level(px=px_a, sz=sz_a))
    return L2Snapshot(ts_ms=ts_ms, coin=coin, bids=bids, asks=asks)


class SyntheticFeed:
    def __init__(self, coin: str = "SYN", s0: float = 100.0, vol_bps: float = 20.0, drift_bps: float = 0.0,
                 interval: str = "1m", spread_bps: float = 1.0, depth_usd: float = 200_000.0, seed: int = 0,
                 funding_1h: float = 1e-5, history: int = 200, t0_ms: int = 1_790_000_000_000):
        self.coin, self.interval_ms, self.spread_bps, self.depth_usd = coin, INTERVAL_MS[interval], spread_bps, depth_usd
        self.vol, self.drift, self.funding = vol_bps / 1e4, drift_bps / 1e4, funding_1h
        self.rng = random.Random(seed)
        self.candles: list[Candle] = []
        self.t0 = t0_ms
        px = s0
        for i in range(history):
            px = self._step(px, i)
        self.px = px

    def _step(self, px: float, i: int) -> float:
        o = px
        r = self.drift + self.vol * self.rng.gauss(0, 1)
        c = o * math.exp(r)
        h = max(o, c) * (1 + abs(self.rng.gauss(0, 0.3)) * self.vol)
        l = min(o, c) * (1 - abs(self.rng.gauss(0, 0.3)) * self.vol)
        t = self.t0 + i * self.interval_ms
        self.candles.append(Candle(t_ms=t, T_ms=t + self.interval_ms - 1, o=o, h=h, l=l, c=c, v=1.0))
        return c

    def advance_to(self, now_ms: int) -> None:
        while self.candles[-1].T_ms + self.interval_ms <= now_ms:
            self.px = self._step(self.px, len(self.candles))

    def snapshot(self, coin: str, now_ms: int, n: int = 120) -> FeedSnapshot:
        self.advance_to(now_ms)
        mid = self.candles[-1].c
        l2 = synthetic_book(coin, now_ms, mid, self.spread_bps, self.depth_usd, skew=0.05 * self.rng.gauss(0, 1))
        ctx = AssetCtx(ts_ms=now_ms, coin=coin, mark_px=mid, oracle_px=mid, funding_1h=self.funding, premium=0.0,
                       open_interest=1e6 / mid, day_ntl_vlm=1e7)
        return FeedSnapshot(l2=l2, candles=self.candles[-n:], ctx=ctx, synthetic=True)

    def close_at(self, coin: str, ts_ms: int) -> Optional[float]:
        """Close of the last candle completed at or before ts_ms (used to resolve outcomes)."""
        self.advance_to(ts_ms)
        for c in reversed(self.candles):
            if c.T_ms <= ts_ms:
                return c.c
        return None


class ReplayFeed:
    def __init__(self, coin: str, candles: list[Candle], spread_bps: float = 1.0, depth_usd: float = 200_000.0,
                 funding_1h: float = 0.0):
        self.coin = coin
        self.candles = sorted(candles, key=lambda c: c.t_ms)
        self.spread_bps, self.depth_usd, self.funding = spread_bps, depth_usd, funding_1h

    def snapshot(self, coin: str, now_ms: int, n: int = 120) -> FeedSnapshot:
        done = [c for c in self.candles if c.T_ms <= now_ms]
        if not done:
            raise ValueError("no completed candle at or before now_ms")
        mid = done[-1].c
        l2 = synthetic_book(coin, now_ms, mid, self.spread_bps, self.depth_usd)
        ctx = AssetCtx(ts_ms=now_ms, coin=coin, mark_px=mid, oracle_px=mid, funding_1h=self.funding)
        return FeedSnapshot(l2=l2, candles=done[-n:], ctx=ctx, synthetic=False)

    def close_at(self, coin: str, ts_ms: int) -> Optional[float]:
        for c in reversed(self.candles):
            if c.T_ms <= ts_ms:
                return c.c
        return None


class HyperliquidFeed:
    """Read-only. Weight per snapshot: l2Book 2 + candleSnapshot ~22 + metaAndAssetCtxs 20 (cached per tick)."""

    def __init__(self, api, interval: str = "1m", dex: Optional[str] = None, n: int = 120):
        self.api, self.interval, self.dex, self.n = api, interval, dex, n
        self._ctx_cache: tuple[int, dict] = (0, {})

    def _ctxs(self, now_ms: int) -> dict:
        ts, cache = self._ctx_cache
        if now_ms - ts < 30_000 and cache:
            return cache
        from hlr.hl_api import asset_ctx_rows
        rows = {r["coin"]: r for r in asset_ctx_rows(self.dex, self.api.meta_and_asset_ctxs(self.dex), now_ms)}
        self._ctx_cache = (now_ms, rows)
        return rows

    def snapshot(self, coin: str, now_ms: int, n: Optional[int] = None) -> FeedSnapshot:
        n = n or self.n
        book = self.api.l2_book(coin)
        l2 = L2Snapshot.from_hl(coin, book, ts_ms=min(now_ms, int(book.get("time", now_ms))))
        ms = INTERVAL_MS[self.interval]
        rows = self.api.candles(coin, self.interval, now_ms - (n + 2) * ms, now_ms, cache=False)
        candles = [Candle.from_hl(r) for r in rows]
        r = self._ctxs(now_ms).get(coin)
        if r is None:
            raise ValueError(f"{coin} not in metaAndAssetCtxs")
        ctx = AssetCtx(ts_ms=now_ms, coin=coin, mark_px=float(r["markPx"]), oracle_px=float(r["oraclePx"] or r["markPx"]),
                       funding_1h=float(r["funding"] or 0.0), premium=float(r["premium"] or 0.0),
                       open_interest=float(r["openInterest"] or 0.0), day_ntl_vlm=float(r["dayNtlVlm"] or 0.0))
        return FeedSnapshot(l2=l2, candles=candles, ctx=ctx, synthetic=False)

    def close_at(self, coin: str, ts_ms: int) -> Optional[float]:
        ms = INTERVAL_MS[self.interval]
        rows = self.api.candles(coin, self.interval, ts_ms - 3 * ms, ts_ms, cache=False)
        done = [r for r in rows if int(r["T"]) <= ts_ms]
        return float(done[-1]["c"]) if done else None


class MultiFeed:
    """Route per-coin feeds (e.g. one SyntheticFeed per coin) behind the single-feed interface."""

    def __init__(self, feeds: dict[str, object]):
        self.feeds = feeds

    def snapshot(self, coin: str, now_ms: int, n: int = 120) -> FeedSnapshot:
        return self.feeds[coin].snapshot(coin, now_ms, n)

    def close_at(self, coin: str, ts_ms: int) -> Optional[float]:
        return self.feeds[coin].close_at(coin, ts_ms)
