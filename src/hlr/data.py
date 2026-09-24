"""Build canonical research datasets from the public upstream GitHub repositories.

Upstream sources (public, cloned read-only; commit hashes recorded in provenance.json):
  * michaelaviv/perp-basis            -> 5-minute cross-venue tape for gold & WTI perps
                                         (Binance, OKX, Hyperliquid xyz:GOLD / xyz:CL, CME via Yahoo)
  * MiggoyGHP/hyperliquid-rwa-dashboard -> hourly Hyperliquid fundingHistory bundles (main, xyz, para,
                                         hyna, mkts dexes), daily stock OHLC (Yahoo), OI snapshots

Nothing here fabricates observations: every row is copied from upstream files.
"""
from __future__ import annotations
import argparse, glob, json, os, subprocess, datetime as dt
import pandas as pd
import numpy as np

DERIVED = os.path.join(os.path.dirname(__file__), "..", "..", "data", "derived")


def _git_head(path: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", path, "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def build_perp_basis(upstream: str) -> pd.DataFrame:
    """Concatenate compacted daily files plus any snapshot files newer than the last daily file."""
    daily = sorted(glob.glob(os.path.join(upstream, "data", "daily", "**", "*.parquet"), recursive=True))
    frames = [pd.read_parquet(f) for f in daily]
    df = pd.concat(frames, ignore_index=True)
    last_daily_ts = df["ts"].max()
    snaps = sorted(glob.glob(os.path.join(upstream, "data", "snapshots", "**", "*.parquet"), recursive=True))
    extra = []
    for f in snaps[-600:]:  # only the tail can be newer than the last compacted day
        s = pd.read_parquet(f)
        if s["ts"].max() > last_daily_ts:
            extra.append(s)
    if extra:
        df = pd.concat([df] + extra, ignore_index=True)
    df = df.rename(columns={"product": "asset"})
    df = df.drop_duplicates(subset=["ts", "venue", "asset"]).sort_values(["ts", "venue", "asset"]).reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def build_funding(upstream: str) -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(os.path.join(upstream, "data", "funding", "*.json"))):
        name = os.path.basename(f)
        if name in ("health.json", "known_gaps.json", "index.json", "summary.json"):
            continue
        d = json.load(open(f))
        dex = d.get("dex") or "main"
        for coin, v in d["coins"].items():
            t = np.asarray(v["t"], dtype="int64")
            r = np.asarray(v["r"], dtype="float64")
            p = np.asarray(v.get("p", [np.nan] * len(t)), dtype="float64")
            rows.append(pd.DataFrame({"dex": dex, "coin": coin, "t": t, "funding": r, "premium": p}))
    df = pd.concat(rows, ignore_index=True)
    df["ts"] = pd.to_datetime(df["t"], unit="s", utc=True)
    df = df.drop_duplicates(subset=["dex", "coin", "t"]).sort_values(["dex", "coin", "t"]).reset_index(drop=True)
    return df


def build_stock_daily(upstream: str) -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(os.path.join(upstream, "data", "ohlc", "*.json"))):
        d = json.load(open(f))
        c = pd.DataFrame(d["candles"])
        c["symbol"] = d["symbol"]
        rows.append(c)
    df = pd.concat(rows, ignore_index=True).rename(columns={"t": "date"})
    df["date"] = pd.to_datetime(df["date"])
    return df[["symbol", "date", "o", "h", "l", "c", "v"]]


def build_oi(upstream: str) -> pd.DataFrame:
    d = json.load(open(os.path.join(upstream, "data", "oi", "snapshots.json")))
    rows = []
    for coin, v in d["coins"].items():
        rows.append(pd.DataFrame({"coin": coin, "t": v["t"], "oi": v["oi"], "px": v["px"], "vlm": v["vlm"]}))
    df = pd.concat(rows, ignore_index=True)
    df["ts"] = pd.to_datetime(df["t"], unit="s", utc=True)
    return df


def build_meta(upstream: str) -> dict:
    return json.load(open(os.path.join(upstream, "data", "meta.json")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perp-basis", required=True, help="path to clone of michaelaviv/perp-basis")
    ap.add_argument("--rwa", required=True, help="path to clone of MiggoyGHP/hyperliquid-rwa-dashboard")
    ap.add_argument("--out", default=DERIVED)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    prov = {"built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "sources": {}}

    pb = build_perp_basis(a.perp_basis)
    pb.to_parquet(os.path.join(a.out, "perp_basis_5m.parquet"), index=False)
    prov["sources"]["perp_basis"] = {
        "repo": "https://github.com/michaelaviv/perp-basis", "commit": _git_head(a.perp_basis),
        "rows": int(len(pb)), "ts_min": str(pb["ts"].min()), "ts_max": str(pb["ts"].max()),
        "note": "GitHub Actions cron (*/5) snapshots; CME rows are Yahoo quotes ~15 min delayed with data_age_sec recorded",
    }
    fu = build_funding(a.rwa)
    fu.to_parquet(os.path.join(a.out, "funding_hourly.parquet"), index=False)
    st = build_stock_daily(a.rwa)
    st.to_parquet(os.path.join(a.out, "stock_daily.parquet"), index=False)
    oi = build_oi(a.rwa)
    oi.to_parquet(os.path.join(a.out, "oi_snapshots.parquet"), index=False)
    json.dump(build_meta(a.rwa), open(os.path.join(a.out, "rwa_meta.json"), "w"))
    prov["sources"]["rwa_dashboard"] = {
        "repo": "https://github.com/MiggoyGHP/hyperliquid-rwa-dashboard", "commit": _git_head(a.rwa),
        "funding_rows": int(len(fu)), "funding_coins": int(fu.groupby(["dex", "coin"]).ngroups),
        "funding_ts_min": str(fu["ts"].min()), "funding_ts_max": str(fu["ts"].max()),
        "stock_rows": int(len(st)), "oi_rows": int(len(oi)),
        "note": "fundingHistory bundles fetched by upstream from api.hyperliquid.xyz (fields t, r=fundingRate, p=premium); stock OHLC from Yahoo via yfinance",
    }
    json.dump(prov, open(os.path.join(a.out, "provenance.json"), "w"), indent=2)
    print(json.dumps(prov, indent=2))


if __name__ == "__main__":
    main()
