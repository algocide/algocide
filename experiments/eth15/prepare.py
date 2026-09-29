#!/usr/bin/env python3
"""Build 15-minute ETH candles (with taker-buy volume and trade counts from the raw Binance archives, BTC candles and
the latest published funding rate) and split them chronologically: first two thirds -> research set, last third ->
holdout. Prints nothing about the holdout except its size and date range; records its SHA-256 (docs/eth15/plan.md).

Usage: python3 experiments/eth15/prepare.py
"""
import glob, hashlib, io, json, os, zipfile
import numpy as np
import pandas as pd

RAW = "data/binance/raw"
OUT = "data/eth15"
RES = "results/eth15"
M15 = 15 * 60_000
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
        "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def load_raw(symbol: str) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(os.path.join(RAW, f"{symbol}-1m-*.zip"))):
        z = zipfile.ZipFile(f)
        raw = z.read(z.namelist()[0])
        first = raw.split(b"\n", 1)[0]
        header = 0 if first[:1].isalpha() else None
        d = pd.read_csv(io.BytesIO(raw), header=header, names=None if header == 0 else COLS)
        d.columns = COLS[: len(d.columns)]
        frames.append(d[["open_time", "open", "high", "low", "close", "volume", "count", "taker_buy_volume"]])
    d = pd.concat(frames, ignore_index=True)
    d["open_time"] = d.open_time.astype(np.int64)
    d.loc[d.open_time > 10**14, "open_time"] //= 1000          # microsecond files, if any
    d = d.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return d


def to_15m(d: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    b = d.open_time // M15 * M15
    g = d.groupby(b, sort=True)
    out = pd.DataFrame({
        "t": g.open_time.first().index.values,
        "open": g.open.first().values, "high": g.high.max().values, "low": g.low.min().values,
        "close": g.close.last().values, "volume": g.volume.sum().values,
        "trades": g["count"].sum().values, "taker_buy": g.taker_buy_volume.sum().values,
        "n_min": g.open_time.size().values,
        # the last minute and last 5 minutes of the candle: known before the next candle opens
        "ret_last1": (g.close.last() / g.open.last() - 1).values,
    })
    last5 = d.assign(b=b).groupby("b").tail(5).groupby("b")
    out["ret_last5"] = (last5.close.last() / last5.open.first() - 1).reindex(out.t).values
    if prefix:
        out = out[["t", "open", "high", "low", "close", "volume"]].rename(
            columns={c: prefix + c for c in ["open", "high", "low", "close", "volume"]})
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(RES, exist_ok=True)
    eth = to_15m(load_raw("ETHUSDT"))
    btc = to_15m(load_raw("BTCUSDT"), prefix="btc_")
    fund = pd.read_parquet("data/binance/ETHUSDT_funding.parquet").sort_values("time")
    c = eth.merge(btc, on="t", how="left")
    # latest funding rate published strictly before the candle opens
    idx = np.searchsorted(fund.time.values, c.t.values, side="left") - 1
    c["funding"] = np.where(idx >= 0, fund.rate.values[np.clip(idx, 0, None)], np.nan)
    c = c[c.n_min == 15].reset_index(drop=True)
    c["up"] = (c.close >= c.open).astype(np.int8)
    n = len(c)
    cut = int(round(n * 2 / 3))
    research, holdout = c.iloc[:cut].reset_index(drop=True), c.iloc[cut:].reset_index(drop=True)
    research.to_parquet(os.path.join(OUT, "research.parquet"), index=False)
    hp = os.path.join(OUT, "holdout.parquet")
    holdout.to_parquet(hp, index=False)
    sha = hashlib.sha256(open(hp, "rb").read()).hexdigest()
    fmt = lambda ms: str(pd.Timestamp(int(ms), unit="ms"))
    split = {"n_candles": n, "n_research": len(research), "n_holdout": len(holdout),
             "research_from": fmt(research.t.iloc[0]), "research_to": fmt(research.t.iloc[-1]),
             "holdout_from": fmt(holdout.t.iloc[0]), "holdout_to": fmt(holdout.t.iloc[-1]),
             "holdout_sha256": sha, "dropped_incomplete": int((eth.n_min != 15).sum())}
    json.dump(split, open(os.path.join(RES, "split.json"), "w"), indent=1)
    print(json.dumps(split, indent=1))
    print("research up share %.4f, ties %.4f" % (research.up.mean(), (research.close == research.open).mean()))


if __name__ == "__main__":
    main()
