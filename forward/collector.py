#!/usr/bin/env python3
"""Forward data collector: records the full Hyperliquid perp universe (all dexes) at a fixed cadence.

Why: the info API exposes only CURRENT open interest, funding, premium and oracle. Hypotheses H8-H10 (trend, liquidation
reversal, funding x OI) and the forward tests for H3/H14 need a recorded history. Every hour not collected is lost.

Usage:  python forward/collector.py --out data/forward --every 60 [--l2 xyz:CL,xyz:GOLD --l2-every 60]
Writes: data/forward/assetctx/YYYY-MM-DD.jsonl  (one row per asset per tick)
        data/forward/l2/YYYY-MM-DD.jsonl        (top-of-book snapshots for the watch list)
        data/forward/health.json                (last tick, error counts)
Weight budget: (n_dexes+1) x 20 per tick; with 10 dexes at 60 s cadence that is ~220/min, well under 1200/min.
NOT run in this session (network policy). Smoke-test on testnet first: --base https://api.hyperliquid-testnet.xyz
"""
import argparse, json, os, sys, time, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from hlr.hl_api import HLInfo, asset_ctx_rows


def day_file(out: str, sub: str, ts: float) -> str:
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(os.path.join(out, sub), exist_ok=True)
    return os.path.join(out, sub, f"{d}.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/forward"); ap.add_argument("--every", type=float, default=60.0)
    ap.add_argument("--base", default="https://api.hyperliquid.xyz"); ap.add_argument("--l2", default="")
    ap.add_argument("--l2-every", type=float, default=60.0); ap.add_argument("--dex-refresh", type=float, default=3600.0)
    a = ap.parse_args()
    api = HLInfo(a.base, cache_dir=None)
    dexes = [None]; last_dex = 0.0; last_l2 = 0.0; errors = 0; ticks = 0
    watch = [c for c in a.l2.split(",") if c]
    while True:
        t0 = time.time()
        try:
            if t0 - last_dex > a.dex_refresh:
                dexes = [None] + [d["name"] for d in (api.perp_dexs() or []) if d]
                last_dex = t0
            ts_ms = int(t0 * 1000)
            with open(day_file(a.out, "assetctx", t0), "a") as f:
                for dex in dexes:
                    resp = api.meta_and_asset_ctxs(dex)
                    for row in asset_ctx_rows(dex, resp, ts_ms):
                        f.write(json.dumps(row) + "\n")
            if watch and t0 - last_l2 >= a.l2_every:
                with open(day_file(a.out, "l2", t0), "a") as f:
                    for coin in watch:
                        b = api.l2_book(coin)
                        lv = b.get("levels", [[], []])
                        f.write(json.dumps({"ts_ms": ts_ms, "coin": coin, "bids": lv[0][:5], "asks": lv[1][:5]}) + "\n")
                last_l2 = t0
            ticks += 1
        except Exception as e:  # keep collecting; log the error
            errors += 1
            with open(os.path.join(a.out, "errors.log"), "a") as f:
                f.write(f"{dt.datetime.now(dt.timezone.utc).isoformat()} {e!r}\n")
        json.dump({"last_tick_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "ticks": ticks, "errors": errors, "dexes": dexes},
                  open(os.path.join(a.out, "health.json"), "w"))
        time.sleep(max(0.0, a.every - (time.time() - t0)))


if __name__ == "__main__":
    main()
