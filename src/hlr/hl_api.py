"""Hyperliquid info-endpoint client with disk cache, pagination and rate-limit pacing.

STATUS: written but NOT exercised against the live API in this research session because the container's network
policy blocks api.hyperliquid.xyz. It is unit-tested only against synthetic responses (tests/test_hl_api.py), so the
first live run must be treated as a smoke test. Request types and payloads follow hyperliquid-python-sdk
(hyperliquid/info.py, cloned 2026-09-24) and the docs' "Info endpoint" page (via search snippets).

Rate limits (docs, 2026-09-24): 1200 weight per minute per IP. candleSnapshot weight 20 + 1 per 60 candles;
fundingHistory / userFills etc. weight 20 + 1 per 20 rows; l2Book / allMids weight 2. candleSnapshot returns at most
the most recent 5000 candles per interval; fundingHistory returns <= 500 rows per call (paginate on the last time).
"""
from __future__ import annotations
import hashlib, json, os, time
from typing import Any, Iterable
import requests

MAINNET = "https://api.hyperliquid.xyz"
TESTNET = "https://api.hyperliquid-testnet.xyz"
INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
               "2h": 7_200_000, "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
               "3d": 259_200_000, "1w": 604_800_000, "1M": 2_592_000_000}


class RateBudget:
    """Token bucket for the 1200 weight/min IP budget (keeps a safety margin)."""
    def __init__(self, per_minute: int = 1000):
        self.per_minute = per_minute; self.tokens = float(per_minute); self.t = time.monotonic()

    def take(self, w: int):
        now = time.monotonic(); self.tokens = min(self.per_minute, self.tokens + (now - self.t) * self.per_minute / 60.0); self.t = now
        if self.tokens < w:
            time.sleep((w - self.tokens) * 60.0 / self.per_minute); self.tokens = 0.0
        else:
            self.tokens -= w


class HLInfo:
    def __init__(self, base_url: str = MAINNET, cache_dir: str | None = "data/cache", timeout: float = 20.0,
                 session: requests.Session | None = None, budget: RateBudget | None = None):
        self.base_url = base_url.rstrip("/"); self.cache_dir = cache_dir; self.timeout = timeout
        self.s = session or requests.Session(); self.budget = budget or RateBudget()
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # ---- transport ----
    def _cache_path(self, payload: dict) -> str | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
        return os.path.join(self.cache_dir, f"{payload.get('type','x')}_{key}.json")

    def post(self, payload: dict, weight: int = 20, cache: bool = False, retries: int = 5) -> Any:
        cp = self._cache_path(payload) if cache else None
        if cp and os.path.exists(cp):
            return json.load(open(cp))
        for k in range(retries):
            self.budget.take(weight)
            r = self.s.post(f"{self.base_url}/info", json=payload, timeout=self.timeout)
            if r.status_code == 200:
                out = r.json()
                if cp:
                    json.dump(out, open(cp, "w"))
                return out
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(60, 2 ** k)); continue
            raise RuntimeError(f"info {payload.get('type')} -> HTTP {r.status_code}: {r.text[:200]}")
        raise RuntimeError(f"info {payload.get('type')} failed after {retries} retries")

    # ---- market structure ----
    def perp_dexs(self) -> list:
        return self.post({"type": "perpDexs"}, weight=20)

    def meta(self, dex: str | None = None) -> dict:
        p = {"type": "meta"}
        if dex:
            p["dex"] = dex
        return self.post(p, weight=20)

    def meta_and_asset_ctxs(self, dex: str | None = None) -> list:
        p = {"type": "metaAndAssetCtxs"}
        if dex:
            p["dex"] = dex
        return self.post(p, weight=20)

    def spot_meta_and_asset_ctxs(self) -> list:
        return self.post({"type": "spotMetaAndAssetCtxs"}, weight=20)

    def all_mids(self, dex: str | None = None) -> dict:
        p = {"type": "allMids"}
        if dex:
            p["dex"] = dex
        return self.post(p, weight=2)

    def l2_book(self, coin: str, n_sig_figs: int | None = None, mantissa: int | None = None) -> dict:
        p = {"type": "l2Book", "coin": coin}
        if n_sig_figs:
            p["nSigFigs"] = n_sig_figs
        if mantissa:
            p["mantissa"] = mantissa
        return self.post(p, weight=2)

    # ---- history ----
    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int, cache: bool = True) -> list[dict]:
        """Chunked candleSnapshot. Only the most recent 5000 candles per interval exist server-side; older
        requests silently return fewer rows -- callers must check coverage."""
        step = INTERVAL_MS[interval] * 4500
        out: dict[int, dict] = {}
        t0 = start_ms
        while t0 < end_ms:
            t1 = min(end_ms, t0 + step)
            rows = self.post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": t0, "endTime": t1}},
                             weight=20 + 75, cache=cache)
            for r in rows or []:
                out[int(r["t"])] = r
            t0 = t1
        return [out[k] for k in sorted(out)]

    def funding_history(self, coin: str, start_ms: int, end_ms: int | None = None, cache: bool = True) -> list[dict]:
        """Paginates fundingHistory (<= 500 rows per call) forward on the last timestamp."""
        out: dict[int, dict] = {}
        t0 = start_ms
        end_ms = end_ms or int(time.time() * 1000)
        while True:
            p = {"type": "fundingHistory", "coin": coin, "startTime": t0, "endTime": end_ms}
            rows = self.post(p, weight=20 + 25, cache=cache and (end_ms - t0) > 3_600_000 * 24)
            if not rows:
                break
            for r in rows:
                out[int(r["time"])] = r
            last = int(rows[-1]["time"])
            if len(rows) < 500 or last >= end_ms:
                break
            t0 = last + 1
        return [out[k] for k in sorted(out)]


def asset_ctx_rows(dex: str, resp: list, ts_ms: int) -> Iterable[dict]:
    """Flatten a metaAndAssetCtxs response into one row per asset (fields per docs' Perpetuals page)."""
    meta, ctxs = resp[0], resp[1]
    for a, c in zip(meta["universe"], ctxs):
        yield {"ts_ms": ts_ms, "dex": dex or "main", "coin": a["name"], "maxLeverage": a.get("maxLeverage"),
               "isDelisted": a.get("isDelisted", False), "markPx": c.get("markPx"), "oraclePx": c.get("oraclePx"),
               "midPx": c.get("midPx"), "funding": c.get("funding"), "premium": c.get("premium"),
               "openInterest": c.get("openInterest"), "dayNtlVlm": c.get("dayNtlVlm"), "prevDayPx": c.get("prevDayPx"),
               "impactBid": (c.get("impactPxs") or [None, None])[0], "impactAsk": (c.get("impactPxs") or [None, None])[1]}
