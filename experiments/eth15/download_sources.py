#!/usr/bin/env python3
"""POST-HOC check (docs/eth15/ledger.md item 6): fetch Binance spot ETHUSDT and USD-M futures index-price 1-minute
klines for the holdout period (from 2024-10-01), build 15-minute candles, write data/eth15/{spot,index}_15m.parquet.

Usage: python3 experiments/eth15/download_sources.py
"""
import io, os, subprocess, zipfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/data"
SOURCES = {"spot": ("spot/monthly/klines", "spot/daily/klines"),
           "index": ("futures/um/monthly/indexPriceKlines", "futures/um/daily/indexPriceKlines")}
RAW = "data/eth15/raw"
M15 = 15 * 60_000


def periods():
    months = pd.period_range("2024-10", "2026-08", freq="M").strftime("%Y-%m").tolist()
    days = pd.date_range("2026-09-01", "2026-09-28", freq="D").strftime("%Y-%m-%d").tolist()
    return [("m", p) for p in months] + [("d", p) for p in days]


def fetch(args):
    src, kind, p = args
    base = SOURCES[src][0 if kind == "m" else 1]
    url = f"{S3}/{base}/ETHUSDT/1m/ETHUSDT-1m-{p}.zip"
    dest = os.path.join(RAW, f"{src}-{p}.zip")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    r = subprocess.run(["curl", "-sS", "-f", "--retry", "4", "--retry-delay", "2", "--max-time", "300", "-o", dest, url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{url}: {r.stderr.strip()[:200]}")
    return dest


def read(path):
    z = zipfile.ZipFile(path)
    raw = z.read(z.namelist()[0])
    header = 0 if raw[:1].isalpha() else None
    d = pd.read_csv(io.BytesIO(raw), header=header)
    d = d.iloc[:, :5]
    d.columns = ["open_time", "open", "high", "low", "close"]
    d["open_time"] = d.open_time.astype(np.int64)
    d.loc[d.open_time > 10**14, "open_time"] //= 1000        # spot files switched to microseconds in 2025
    return d


def main():
    os.makedirs(RAW, exist_ok=True)
    for src in SOURCES:
        jobs = [(src, k, p) for k, p in periods()]
        with ThreadPoolExecutor(8) as ex:
            paths = list(ex.map(fetch, jobs))
        d = pd.concat([read(p) for p in paths]).drop_duplicates("open_time").sort_values("open_time")
        b = d.open_time // M15 * M15
        g = d.groupby(b)
        c = pd.DataFrame({"t": g.open_time.first().index.values, "open": g.open.first().values,
                          "high": g.high.max().values, "low": g.low.min().values, "close": g.close.last().values,
                          "n_min": g.open_time.size().values})
        c = c[c.n_min == 15].reset_index(drop=True)
        c["up"] = (c.close >= c.open).astype(np.int8)
        c.to_parquet(f"data/eth15/{src}_15m.parquet", index=False)
        print(src, len(c), "candles", pd.Timestamp(int(c.t.iloc[0]), unit="ms"), "->", pd.Timestamp(int(c.t.iloc[-1]), unit="ms"))


if __name__ == "__main__":
    main()
