#!/usr/bin/env python3
"""Download Binance USDT-M futures 1m klines and funding rates from the public archive (data.binance.vision via its
S3 endpoint) and store them as Parquet. Nothing here needs keys.

Usage: python3 experiments/pine/download_binance.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --start 2021-01 --end-day 2026-09-28
"""
import argparse, datetime as dt, io, os, subprocess, sys, zipfile
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/data/futures/um"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
        "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def fetch(url: str, dest: str) -> str:
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return "cached"
    tmp = dest + ".part"
    r = subprocess.run(["curl", "-sS", "-f", "--retry", "4", "--retry-delay", "2", "--max-time", "300", "-o", tmp, url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        return f"fail {r.returncode} {r.stderr.strip()[:120]}"
    os.replace(tmp, dest)
    return "ok"


def months(start: str, end: str):
    y, m = map(int, start.split("-")); ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def read_klines_zip(path: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        raw = z.read(name)
    first = raw.split(b"\n", 1)[0]
    header = 0 if first[:1].isalpha() else None
    df = pd.read_csv(io.BytesIO(raw), header=header)
    df = df.iloc[:, :12]
    df.columns = COLS
    df = df[["open_time", "open", "high", "low", "close", "volume"]].astype({"open_time": "int64"})
    return df


def read_funding_zip(path: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    return df.rename(columns={"calc_time": "time", "last_funding_rate": "rate"})[["time", "rate"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--start", default="2021-01")
    ap.add_argument("--end-month", default="2026-08")
    ap.add_argument("--end-day", default="2026-09-28")
    ap.add_argument("--out", default="data/binance")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    raw_dir = os.path.join(a.out, "raw"); os.makedirs(raw_dir, exist_ok=True)
    jobs = []
    last_day = dt.date.fromisoformat(a.end_day)
    for sym in a.symbols.split(","):
        for ym in months(a.start, a.end_month):
            jobs.append((f"{BASE}/monthly/klines/{sym}/1m/{sym}-1m-{ym}.zip", os.path.join(raw_dir, f"{sym}-1m-{ym}.zip")))
            jobs.append((f"{BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{ym}.zip", os.path.join(raw_dir, f"{sym}-funding-{ym}.zip")))
        d = dt.date(last_day.year, last_day.month, 1)
        while d <= last_day:
            jobs.append((f"{BASE}/daily/klines/{sym}/1m/{sym}-1m-{d.isoformat()}.zip", os.path.join(raw_dir, f"{sym}-1m-{d.isoformat()}.zip")))
            d += dt.timedelta(days=1)
    with ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(lambda j: (j[1], fetch(*j)), jobs))
    fails = [(p, s) for p, s in res if s.startswith("fail")]
    print(f"{len(jobs)} files; {len(fails)} failed", flush=True)
    for p, s in fails[:20]:
        print("  ", os.path.basename(p), s)
    for sym in a.symbols.split(","):
        parts = sorted(f for f in os.listdir(raw_dir) if f.startswith(f"{sym}-1m-") and f.endswith(".zip"))
        k = pd.concat([read_klines_zip(os.path.join(raw_dir, f)) for f in parts], ignore_index=True)
        k = k.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
        k.to_parquet(os.path.join(a.out, f"{sym}_1m.parquet"), index=False)
        fparts = sorted(f for f in os.listdir(raw_dir) if f.startswith(f"{sym}-funding-") and f.endswith(".zip"))
        if fparts:
            fu = pd.concat([read_funding_zip(os.path.join(raw_dir, f)) for f in fparts], ignore_index=True)
            fu = fu.drop_duplicates("time").sort_values("time").reset_index(drop=True)
            fu.to_parquet(os.path.join(a.out, f"{sym}_funding.parquet"), index=False)
        t0 = pd.to_datetime(k.open_time.iloc[0], unit="ms"); t1 = pd.to_datetime(k.open_time.iloc[-1], unit="ms")
        gaps = (k.open_time.diff() > 60_000).sum()
        print(f"{sym}: {len(k)} 1m bars {t0} -> {t1}; gaps>1m: {gaps}; funding rows: {len(fu) if fparts else 0}", flush=True)


if __name__ == "__main__":
    main()
