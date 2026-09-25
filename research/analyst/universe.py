"""The 59-name universe: Hyperliquid xyz-listed stocks (data/derived/rwa_meta.json) that have >= min_rows sessions in
the daily panel. CIKs are filled in by the EDGAR ingest and kept in the warehouse's `universe` table."""
from __future__ import annotations
import json, os
import pandas as pd
from .config import resolve


def load_universe(cfg: dict, warehouse=None) -> pd.DataFrame:
    meta = json.load(open(resolve(cfg, "rwa_meta")))
    rows = [{"symbol": t["yahoo"], "coin": t["coin"], "name": t.get("name", t["yahoo"])} for t in meta["tickers"] if t.get("coin", "").startswith(cfg["dex"] + ":")]
    u = pd.DataFrame(rows).drop_duplicates("symbol")
    px = pd.read_parquet(resolve(cfg, "stock_daily"), columns=["symbol", "date"])
    counts = px.groupby("symbol").size()
    u = u[u.symbol.map(counts).fillna(0) >= cfg["universe"]["min_rows"]].sort_values("symbol").reset_index(drop=True)
    u["cik"] = pd.Series([None] * len(u), dtype="object")
    if warehouse is not None:
        stored = warehouse.read("universe")
        if len(stored):
            m = dict(zip(stored.symbol, stored.cik)); u["cik"] = u.symbol.map(m)
    return u
