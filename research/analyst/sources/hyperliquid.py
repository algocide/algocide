"""Hyperliquid read-only client: POST /info only (candleSnapshot, allMids, clearinghouseState, metaAndAssetCtxs).
There is no exchange endpoint, no signing and no key anywhere in this module. Positions are read for portfolio math."""
from __future__ import annotations
import time, datetime as dt
import pandas as pd

DAY_MS = 86_400_000


class HLReadOnly:
    def __init__(self, base_url: str = "https://api.hyperliquid.xyz", post=None, timeout: float = 20.0):
        if post is None:
            import requests; post = requests.post
        self.base_url = base_url.rstrip("/"); self._post = post; self.timeout = timeout

    def info(self, payload: dict):
        r = self._post(self.base_url + "/info", json=payload, timeout=self.timeout)
        r.raise_for_status(); return r.json()

    def daily_candles(self, coin: str, days: int = 400, now_ms: int | None = None) -> pd.DataFrame:
        """Completed daily candles only (close time strictly in the past)."""
        now_ms = now_ms or int(time.time() * 1000)
        rows = self.info({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d", "startTime": now_ms - DAY_MS * (days + 2), "endTime": now_ms}})
        if not rows: return pd.DataFrame(columns=["symbol", "date", "o", "h", "l", "c", "v"])
        df = pd.DataFrame(rows)
        for c in ["o", "h", "l", "c", "v"]: df[c] = df[c].astype(float)
        df = df[df["T"].astype("int64") < now_ms]
        df["date"] = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
        df["symbol"] = coin.split(":")[-1]
        return df[["symbol", "date", "o", "h", "l", "c", "v"]].reset_index(drop=True)

    def all_mids(self, dex: str | None = None) -> dict:
        p = {"type": "allMids"}
        if dex: p["dex"] = dex
        return self.info(p)

    def clearinghouse_state(self, address: str, dex: str | None = None) -> dict:
        p = {"type": "clearinghouseState", "user": address}
        if dex: p["dex"] = dex
        return self.info(p)

    def positions(self, address: str, dexes=(None, "xyz")) -> list[dict]:
        out, seen = [], set()
        for dex in dexes:
            try: st = self.clearinghouse_state(address, dex)
            except Exception as e:                       # one dex failing must not hide the other
                out.append({"error": f"{dex}: {e}"}); continue
            for ap in st.get("assetPositions", []):
                p = ap.get("position", {}); coin = p.get("coin"); szi = float(p.get("szi", 0) or 0)
                if not coin or szi == 0 or coin in seen: continue
                seen.add(coin)
                out.append({"coin": coin, "symbol": coin.split(":")[-1], "size": szi, "side": "long" if szi > 0 else "short",
                            "entry_px": float(p.get("entryPx") or 0), "position_value": abs(float(p.get("positionValue") or 0)),
                            "unrealized_pnl": float(p.get("unrealizedPnl") or 0), "leverage": (p.get("leverage") or {}).get("value"),
                            "account_value": float((st.get("marginSummary") or {}).get("accountValue") or 0)})
        return out


def positions_to_weights(positions: list[dict]) -> pd.DataFrame:
    """Weights by absolute notional (the article's weight_i). Shorts keep their sign in `side` for the correlation read."""
    rows = [p for p in positions if "symbol" in p and p.get("position_value")]
    if not rows: return pd.DataFrame(columns=["symbol", "side", "notional", "weight"])
    df = pd.DataFrame(rows)[["symbol", "side", "position_value"]].rename(columns={"position_value": "notional"})
    df["weight"] = df.notional / df.notional.sum()
    return df.sort_values("weight", ascending=False).reset_index(drop=True)
