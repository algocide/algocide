#!/usr/bin/env python3
"""Pipeline for the hypotheses that could NOT be tested in this session because api.hyperliquid.xyz was blocked:
H8 volatility-conditioned trend/momentum on native perps, H9 post-liquidation mean reversion (needs the forward
collector's data or the S3 fills archive), H10 funding x OI interaction (needs collector OI history).

STATUS: NOT RUN. No results exist for these hypotheses; nothing here has been validated against live data.
It downloads what the REST API offers (candles: most recent 5000 per interval; fundingHistory: full), caches to
Parquet, and runs the pre-registered tests with the shared cost model. Run: PYTHONPATH=src python3 experiments/e8_e10_pipeline_when_api_available.py --interval 1h
"""
import argparse, os, sys, time, json
import numpy as np, pandas as pd
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from hlr.hl_api import HLInfo, INTERVAL_MS
from hlr.costs import FEE_REGIMES
from hlr.stats import describe_pnl, circular_block_bootstrap_ci, deflated_sharpe

OUT = os.path.join(ROOT, "results", "e8_e10"); CACHE = os.path.join(ROOT, "data", "cache")


def download(api: HLInfo, interval: str, max_coins: int) -> dict[str, pd.DataFrame]:
    meta = api.meta(); ctx = api.meta_and_asset_ctxs()[1]
    uni = [(a["name"], float(c["dayNtlVlm"])) for a, c in zip(meta["universe"], ctx) if not a.get("isDelisted")]
    uni = [n for n, _ in sorted(uni, key=lambda x: -x[1])[:max_coins]]   # point-in-time caveat: today's universe
    end = int(time.time() * 1000); start = end - INTERVAL_MS[interval] * 5000
    data = {}
    for coin in uni:
        f = os.path.join(CACHE, f"candles_{coin}_{interval}.parquet")
        if os.path.exists(f):
            data[coin] = pd.read_parquet(f); continue
        rows = api.candles(coin, interval, start, end)
        df = pd.DataFrame(rows)
        if len(df):
            for c in ["o", "h", "l", "c", "v"]: df[c] = df[c].astype(float)
            df["ts"] = pd.to_datetime(df["t"], unit="ms", utc=True); df["coin"] = coin
            df.to_parquet(f, index=False); data[coin] = df
    return data


def h8_vol_conditioned_momentum(data: dict, interval: str):
    """Pre-registered: signal = sign of trailing 24-bar return, scaled by 1/realised vol (96 bars), traded only when
    realised vol is below its 30-day median (low-vol regime); rebalance every 24 bars; taker fees native_base; delay 1 bar.
    Benchmark: buy-and-hold equal weight and the unconditioned momentum."""
    rets = pd.DataFrame({c: np.log(d.set_index("ts")["c"]).diff() for c, d in data.items()})
    mom = rets.rolling(24).sum().shift(1); vol = rets.rolling(96).std().shift(1)
    lowvol = vol < vol.rolling(24 * 30, min_periods=24 * 7).median()
    pos = np.sign(mom) / vol.replace(0, np.nan); pos = pos.div(pos.abs().sum(axis=1), axis=0)
    pos_c = pos.where(lowvol, 0.0)
    fee = FEE_REGIMES["native_base"].taker
    out = {}
    for name, p in [("momentum", pos), ("momentum_lowvol", pos_c)]:
        p = p.iloc[::24].reindex(rets.index).ffill()        # rebalance every 24 bars
        turnover = p.diff().abs().sum(axis=1).fillna(0)
        pnl = (p.shift(1) * rets).sum(axis=1) - turnover * (fee + 0.0002)
        daily = pnl.resample("1D").sum()
        d = describe_pnl(daily.values, per_year=365); _, lo, hi = circular_block_bootstrap_ci(daily.values, block=5)
        out[name] = {**d, "ci95_daily_mean": [lo, hi], "ann_return": float(daily.mean() * 365)}
    bh = rets.mean(axis=1).resample("1D").sum(); out["buy_hold_ew"] = describe_pnl(bh.values, per_year=365)
    return out


def h10_funding_oi(collector_dir: str):
    """Needs forward/collector.py output (assetctx JSONL): OI growth over 24h x funding extreme -> next-24h return.
    Returns None until data exists."""
    files = sorted(__import__("glob").glob(os.path.join(collector_dir, "assetctx", "*.jsonl")))
    if len(files) < 30:
        return {"status": f"insufficient collector data ({len(files)} days); need >= 30"}
    df = pd.concat([pd.read_json(f, lines=True) for f in files]); df = df[df["dex"] == "main"]
    df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    h = df.set_index("ts").groupby("coin").resample("1h").last()
    # placeholder for the pre-registered regression: dOI_24h * sign(funding) on fwd 24h log(mark) change (HAC t)
    return {"status": "data present; implement regression per pre-registration before use"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--interval", default="1h"); ap.add_argument("--max-coins", type=int, default=40)
    ap.add_argument("--collector-dir", default=os.path.join(ROOT, "data", "forward"))
    a = ap.parse_args(); os.makedirs(OUT, exist_ok=True); os.makedirs(CACHE, exist_ok=True)
    api = HLInfo(cache_dir=CACHE)
    data = download(api, a.interval, a.max_coins)
    res = {"h8": h8_vol_conditioned_momentum(data, a.interval), "h10": h10_funding_oi(a.collector_dir),
           "caveats": ["universe = today's top-volume coins (survivorship); candles limited to 5000 bars", "no order-book: taker fill at close + 2 bps assumed"]}
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1, default=str); print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
