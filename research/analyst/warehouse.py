"""Layer 1: the data warehouse. One parquet file per table under data/warehouse, every row carrying `source` and
(where it matters) a filing date, because the article's rule is "always show the filing date". Tables that have no
free source are typed placeholders: they exist, they are empty, and reports say "not available" instead of guessing."""
from __future__ import annotations
import os, json, datetime as dt
import pandas as pd
from .config import resolve

TABLES = {
    "prices_daily":      ["symbol", "date", "o", "h", "l", "c", "v", "source", "fetched_at"],
    "universe":          ["symbol", "coin", "name", "cik", "source"],
    "filings_index":     ["symbol", "cik", "form", "filing_date", "report_date", "accession", "primary_doc", "items", "source", "fetched_at"],
    "earnings_events":   ["symbol", "cik", "event_date", "filing_date", "accession", "source"],
    "insider_form4":     ["symbol", "cik", "accession", "filing_date", "transaction_date", "insider", "relationship", "code",
                          "acquired_disposed", "shares", "price", "shares_after", "source"],
    "holdings_13f":      ["filer_cik", "filer_name", "symbol", "issuer_name", "cusip", "period_of_report", "filing_date", "accession",
                          "shares", "value", "source"],
    "filing_text":       ["symbol", "cik", "accession", "form", "filing_date", "report_date", "section", "chars", "text", "source"],
    "xbrl_quarterly":    ["symbol", "cik", "metric", "tag", "frame", "end", "val", "form", "filed", "source"],
    "cusip_map":         ["cusip", "symbol", "issuer_name", "matched_by"],
    "catalyst_calendar": ["symbol", "event_date", "kind", "source", "note"],
    "filer_weights":     ["filer_cik", "weight", "n_obs", "hit_rate", "base_rate", "source"],
    # placeholders: no free source exists; reports print "not available" for these
    "estimate_revisions": ["symbol", "date", "metric", "old", "new", "source"],
    "options_activity":   ["symbol", "date", "put_call_ratio", "unusual", "source"],
    "retail_flow":        ["symbol", "date", "net_flow", "source"],
    "news":               ["symbol", "ts", "headline", "url", "source"],
}
PLACEHOLDERS = {"estimate_revisions", "options_activity", "retail_flow", "news"}
KEYS = {"prices_daily": ["symbol", "date"], "universe": ["symbol"], "filings_index": ["accession", "symbol"],
        "earnings_events": ["symbol", "event_date"], "insider_form4": ["accession", "insider", "transaction_date", "code", "shares"],
        "holdings_13f": ["filer_cik", "cusip", "period_of_report"], "filing_text": ["accession", "section"],
        "xbrl_quarterly": ["symbol", "metric", "frame"], "cusip_map": ["cusip"], "catalyst_calendar": ["symbol", "event_date", "kind"],
        "filer_weights": ["filer_cik"]}
DATE_COLS = {"date", "filing_date", "report_date", "event_date", "transaction_date", "period_of_report", "end", "filed"}


def now_utc() -> str: return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Warehouse:
    def __init__(self, directory: str):
        self.dir = directory; os.makedirs(directory, exist_ok=True)

    def path(self, table: str) -> str:
        if table not in TABLES: raise KeyError(f"unknown table {table}")
        return os.path.join(self.dir, f"{table}.parquet")

    def empty(self, table: str) -> pd.DataFrame:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in TABLES[table]})

    def read(self, table: str) -> pd.DataFrame:
        p = self.path(table)
        if not os.path.exists(p): return self.empty(table)
        df = pd.read_parquet(p)
        for c in df.columns:
            if c in DATE_COLS: df[c] = pd.to_datetime(df[c], errors="coerce")
        return df

    def write(self, table: str, df: pd.DataFrame, mode: str = "replace") -> int:
        cols = TABLES[table]
        df = df.copy()
        for c in cols:
            if c not in df.columns: df[c] = None
        df = df[cols]
        for c in df.columns:
            if c in DATE_COLS: df[c] = pd.to_datetime(df[c], errors="coerce")
        if mode == "append":
            old = self.read(table)
            if len(old): df = pd.concat([old, df], ignore_index=True)
        key = KEYS.get(table)
        if key: df = df.drop_duplicates(key, keep="last")
        df = df.reset_index(drop=True)
        df.to_parquet(self.path(table), index=False)
        return len(df)

    def status(self) -> dict:
        out = {}
        for t in TABLES:
            df = self.read(t)
            d = {"rows": int(len(df)), "placeholder": t in PLACEHOLDERS}
            dcol = next((c for c in ("date", "filing_date", "event_date", "period_of_report", "end", "ts") if c in df.columns), None)
            if len(df) and dcol:
                s = pd.to_datetime(df[dcol], errors="coerce"); d["min"] = str(s.min())[:10]; d["max"] = str(s.max())[:10]
            if len(df) and "source" in df.columns: d["sources"] = sorted(map(str, df["source"].dropna().unique()))[:5]
            out[t] = d
        return out


def ingest_offline_prices(wh: Warehouse, cfg: dict, universe: pd.DataFrame) -> int:
    """The committed daily panel (Yahoo OHLCV via the upstream dashboard repo) as the warehouse's price table."""
    prov = json.load(open(resolve(cfg, "provenance")))["sources"]["rwa_dashboard"]
    src = f"yahoo_daily via {prov['repo']}@{prov['commit'][:8]}"
    df = pd.read_parquet(resolve(cfg, "stock_daily")); df = df[df.symbol.isin(set(universe.symbol))].copy()
    df["date"] = pd.to_datetime(df["date"]); df["source"] = src; df["fetched_at"] = prov.get("built_at_utc") or now_utc()
    wh.write("universe", universe.assign(source="data/derived/rwa_meta.json"), mode="replace")
    return wh.write("prices_daily", df.sort_values(["symbol", "date"]), mode="replace")
