"""Synthetic-response tests for the API client (no network). These do NOT validate live behaviour."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from hlr.hl_api import HLInfo, asset_ctx_rows, RateBudget


class FakeResp:
    def __init__(self, data, code=200): self._d = data; self.status_code = code; self.text = json.dumps(data)
    def json(self): return self._d


class FakeSession:
    def __init__(self, handler): self.h = handler; self.calls = []
    def post(self, url, json=None, timeout=None):
        self.calls.append(json); return FakeResp(self.h(json))


def test_funding_pagination():
    rows = [{"coin": "BTC", "fundingRate": "0.0000125", "premium": "0.0", "time": 1_000_000 + i * 3_600_000} for i in range(1200)]
    def h(p):
        assert p["type"] == "fundingHistory"
        sel = [r for r in rows if p["startTime"] <= r["time"] <= p["endTime"]]
        return sel[:500]
    api = HLInfo(session=FakeSession(h), cache_dir=None, budget=RateBudget(10**9))
    out = api.funding_history("BTC", 1_000_000, 1_000_000 + 1199 * 3_600_000)
    assert len(out) == 1200 and out[0]["time"] == 1_000_000 and out[-1]["time"] == rows[-1]["time"]


def test_candles_chunking_dedup():
    def h(p):
        r = p["req"]; step = 3_600_000
        return [{"t": t, "c": "1"} for t in range(r["startTime"] // step * step, r["endTime"], step)][:5000]
    api = HLInfo(session=FakeSession(h), cache_dir=None, budget=RateBudget(10**9))
    out = api.candles("BTC", "1h", 0, 10_000 * 3_600_000)
    ts = [o["t"] for o in out]
    assert ts == sorted(set(ts)) and len(ts) == 10_000


def test_asset_ctx_rows():
    resp = [{"universe": [{"name": "xyz:GOLD", "maxLeverage": 20}]},
            [{"markPx": "4300", "oraclePx": "4299", "midPx": "4300.5", "funding": "0.00001", "premium": "0.0002",
              "openInterest": "100", "dayNtlVlm": "5e6", "prevDayPx": "4250", "impactPxs": ["4300", "4301"]}]]
    rows = list(asset_ctx_rows("xyz", resp, 123))
    assert rows[0]["coin"] == "xyz:GOLD" and rows[0]["impactAsk"] == "4301" and rows[0]["dex"] == "xyz"


if __name__ == "__main__":
    test_funding_pagination(); test_candles_chunking_dedup(); test_asset_ctx_rows(); print("ok")
