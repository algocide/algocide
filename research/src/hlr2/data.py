"""Dataset builders. Two data kinds:
  * 'candles'  : real Hyperliquid OHLCV candles (BTC/ETH from lukasbecker36-dot/hyperdata).
  * 'sampled'  : 15-minute sampled mid prices (Tohshi-memo/HyperLiquid-Bot-test). No intrabar high/low exist.
Both are turned into a common Panel: a shared time grid (decision times) with per-instrument arrays
o,h,l,c (bar ending at the decision time), exec_px (price at which an order placed right after the decision fills:
next bar open for candles, next observation for sampled data), exec_ts, and session flags.
"""
from __future__ import annotations
import os
import numpy as np, pandas as pd
from .sessions import annotate, ET

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "raw")


class Panel:
    def __init__(self, kind: str, grid: pd.DatetimeIndex, inst: dict[str, pd.DataFrame], bar_minutes: int, note: str = ""):
        self.kind, self.grid, self.inst, self.bar_minutes, self.note = kind, grid, inst, bar_minutes, note
        self.symbols = sorted(inst)

    def __repr__(self):
        return f"Panel(kind={self.kind}, bars={self.bar_minutes}m, n={len(self.grid)}, {self.grid[0]}..{self.grid[-1]}, symbols={self.symbols})"


def _session_flags(ts: pd.Series, session: str, entry_cutoff_min: int, exit_buffer_min: int) -> pd.DataFrame:
    if session == "us_regular":
        return annotate(ts, entry_cutoff_min, exit_buffer_min)
    # 24/7: always in session, never forced out; session_date = UTC date
    t = pd.to_datetime(ts, utc=True)
    return pd.DataFrame({"in_session": True, "can_enter": True, "must_exit": False, "session_date": t.dt.date,
                         "session_open": pd.NaT, "session_close": pd.NaT}, index=ts.index)


def load_sampled(symbols: list[str], session: str = "us_regular", entry_cutoff_min: int = 90, exit_buffer_min: int = 45,
                 path: str | None = None) -> Panel:
    """15-minute sampled mids -> Panel on the observation grid. Bar k: close = price at observation k, open = price at
    observation k-1, high/low = max/min of the two (two-point bars, NOT true ranges). exec_px = price at observation k+1."""
    df = pd.read_parquet(path or os.path.join(RAW, "tohshi_mid_15m.parquet"))
    df = df[df.symbol.isin(symbols)]
    wide = df.pivot_table(index="collected_at", columns="symbol", values="price").sort_index()
    # drop observations where all requested symbols are missing; keep the union grid
    wide = wide.dropna(how="all")
    grid = wide.index
    bucket_map = df.drop_duplicates("collected_at").set_index("collected_at")["observed_at"]
    buckets = pd.to_datetime(bucket_map.reindex(grid).values, utc=True)
    # observation spacing: flag gaps > 45 min (a bar spanning a gap must not be used for signals/execution)
    gap_min = pd.Series(grid).diff().dt.total_seconds().div(60).fillna(15).values
    flags = _session_flags(pd.Series(grid), session, entry_cutoff_min, exit_buffer_min)
    inst = {}
    for s in symbols:
        if s not in wide: continue
        c = wide[s].values.astype(float)
        o = np.roll(c, 1); o[0] = np.nan
        d = pd.DataFrame({"ts": grid, "bucket": buckets, "o": o, "c": c, "h": np.fmax(o, c), "l": np.fmin(o, c), "v": np.nan, "n": np.nan})
        d["exec_px"] = np.append(c[1:], np.nan); d["exec_ts"] = np.append(grid[1:].values, np.datetime64("NaT"))
        d["exec_ts"] = pd.to_datetime(d["exec_ts"], utc=True)
        d["gap_before_min"] = gap_min; d["gap_after_min"] = np.append(gap_min[1:], np.nan)
        d["valid"] = np.isfinite(c) & np.isfinite(o) & (d.gap_before_min <= 45)
        d["exec_valid"] = np.isfinite(d.exec_px) & (d.gap_after_min <= 45)
        for col in flags.columns: d[col] = flags[col].values
        inst[s] = d.reset_index(drop=True)
    return Panel("sampled", grid, inst, 15, "two-point bars from 15-min sampled mids; execution at next observation")


def load_candles(symbols: list[str], interval: str = "15m", session: str = "us_regular", entry_cutoff_min: int = 90,
                 exit_buffer_min: int = 30, path: str | None = None) -> Panel:
    df = pd.read_parquet(path or os.path.join(RAW, f"hyperdata_candles_{interval}.parquet"))
    df = df[df.symbol.isin(symbols)].copy()
    minutes = {"15m": 15, "1h": 60}[interval]
    df["close_ts"] = df["ts"] + pd.Timedelta(minutes=minutes)      # decision time = bar close
    grid = pd.DatetimeIndex(sorted(df.close_ts.unique()))
    flags = _session_flags(pd.Series(grid), session, entry_cutoff_min, exit_buffer_min)
    inst = {}
    for s, d0 in df.groupby("symbol"):
        d0 = d0.set_index("close_ts").reindex(grid)
        d = pd.DataFrame({"ts": grid, "bucket": grid - pd.Timedelta(minutes=minutes), "o": d0.open.values, "h": d0.high.values, "l": d0.low.values, "c": d0.close.values, "v": d0.volume.values, "n": d0.num_trades.values})
        nxt_open = np.append(d0.open.values[1:], np.nan); nxt_ts = np.append(grid[1:].values, np.datetime64("NaT"))
        d["exec_px"] = nxt_open; d["exec_ts"] = pd.to_datetime(pd.Series(grid).shift(-1).values, utc=True)  # order fills at next bar open == this bar's close time
        d["exec_ts"] = pd.to_datetime(grid, utc=True)  # next bar opens at this bar's close time
        gap = pd.Series(grid).diff().dt.total_seconds().div(60).fillna(minutes).values
        d["gap_before_min"] = gap; d["gap_after_min"] = np.append(gap[1:], np.nan)
        d["valid"] = np.isfinite(d.c.values) & (gap <= minutes * 1.5)
        d["exec_valid"] = np.isfinite(nxt_open) & (d.gap_after_min <= minutes * 1.5)
        for col in flags.columns: d[col] = flags[col].values
        inst[s] = d.reset_index(drop=True)
    return Panel("candles", grid, inst, minutes, f"real {interval} candles; execution at next bar open")


def hourly_bars(d: pd.DataFrame, align: str = "session") -> pd.DataFrame:
    """Aggregate a 15-minute instrument frame into COMPLETED 1-hour bars using each bar's bucket (its nominal
    15-minute slot, immune to collection jitter). align='session': hours start at 09:30 ET (09:30-10:30, ...; the
    15:30-16:00 remainder is a partial bar and never completes); align='utc': hours at :00 UTC.
    Returns a frame with o,h,l,c, idx (grid index of the last constituent bar = completion/decision index) and
    'complete' (all four 15-minute slots present and valid)."""
    b = pd.to_datetime(d.bucket, utc=True)
    if align == "session":
        et = b.dt.tz_convert(ET)
        m = et.dt.hour * 60 + et.dt.minute - 570
        key = et.dt.strftime("%Y-%m-%d") + "_" + np.where(m >= 0, (m // 60), -1 - ((-m - 1) // 60)).astype(int).astype(str)
        pos = (np.where(m >= 0, m, m + 60 * 100) % 60) // 15
    else:
        key = b.dt.strftime("%Y-%m-%d_%H"); pos = (b.dt.minute // 15).values
    g = pd.DataFrame({"key": np.asarray(key), "pos": np.asarray(pos).astype(int), "o": d.o.values, "h": d.h.values, "l": d.l.values, "c": d.c.values,
                      "valid": d.valid.values.astype(bool), "idx": np.arange(len(d))})
    agg = g.groupby("key", sort=False).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), n=("pos", "size"),
                                            nvalid=("valid", "sum"), idx=("idx", "last"), pos_last=("pos", "last"), pos_first=("pos", "first"))
    agg["complete"] = (agg.n == 4) & (agg.nvalid == 4) & (agg.pos_last == 3) & (agg.pos_first == 0)
    return agg.sort_values("idx").reset_index()


def tf_view(panel, sym: str, tf: str, align: str = "session"):
    """Timeframe view of an instrument: arrays (o,h,l,c) of completed bars at timeframe tf, the grid index at which each
    bar completes (decision index), and is_close[k] (True when grid bar k completes a tf bar). For tf == panel bars the
    view is the grid itself. Bars flagged incomplete (gaps) are dropped, which breaks indicator continuity there --
    acceptable: indicators warm up again after gaps (documented)."""
    d = panel.inst[sym]
    if tf == "15m" and panel.bar_minutes == 15 or tf == "1h" and panel.bar_minutes == 60:
        n = len(d); idx = np.arange(n)
        return {"o": d.o.values, "h": d.h.values, "l": d.l.values, "c": d.c.values, "idx": idx, "valid": d.valid.values.astype(bool),
                "is_close": np.ones(n, bool), "map": idx}
    if tf == "1h" and panel.bar_minutes == 15:
        hb = hourly_bars(d, align)
        hb = hb[hb.complete]
        n = len(d); is_close = np.zeros(n, bool); is_close[hb.idx.values] = True
        mp = np.full(n, -1); pos = -1; j = 0; idxs = hb.idx.values
        for k in range(n):
            while j < len(idxs) and idxs[j] <= k:
                pos = j; j += 1
            mp[k] = pos   # index (into the tf arrays) of the last completed tf bar at or before k
        return {"o": hb.o.values, "h": hb.h.values, "l": hb.l.values, "c": hb.c.values, "idx": idxs, "valid": np.ones(len(hb), bool), "is_close": is_close, "map": mp}
    raise ValueError(f"unsupported tf {tf} on {panel.bar_minutes}m bars")
