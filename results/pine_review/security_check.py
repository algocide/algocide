#!/usr/bin/env python3
"""Task 4: does request.security ever expose a higher-timeframe value before that bar has closed?

Runs small scripts through pinebt's Runner on real BTCUSDT data and records every value the SecurityHub returns
(monkeypatched in this process only), then checks it against an independent pandas computation: the HTF value
visible at a chart bar closing at TC must be the one of the last HTF bar whose close time is <= TC.

Usage: PYTHONPATH=src python3 results/pine_review/security_check.py
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, "src")
from pinebt import engine as E

DATA = "data/binance"
END = 1790640000000
H, D = 3_600_000, 86_400_000


def htf_table(sym, tf):
    df = pd.read_parquet(os.path.join(DATA, f"{sym}_1m.parquet"))
    if tf % (7 * D) == 0:
        b = (df.open_time - 4 * D) // tf * tf + 4 * D
    else:
        b = df.open_time // tf * tf
    g = df.groupby(b)
    t = pd.DataFrame({"o": g.open.first(), "h": g.high.max(), "l": g.low.min(), "c": g.close.last()})
    t["TC"] = t.index + tf
    return t


def run_recording(src, sym, tf, max_bars):
    rec = []
    orig = E.SecurityHub.__call__

    def patched(self, key, i, s, tfs, gaps, thunk):
        v = orig(self, key, i, s, tfs, gaps, thunk)
        if self.recording is None:               # the main chart's hub only
            rec.append((i, str(tfs), v))
        return v

    E.SecurityHub.__call__ = patched
    try:
        cs = E.CompiledScript(src)
        r = E.Runner(cs, sym, tf, end_ms=END, max_bars=max_bars).run()
    finally:
        E.SecurityHub.__call__ = orig
    return cs, r, rec


def check(name, src, tf, htf, field, max_bars, expr=None):
    cs, r, rec = run_recording(src, "BTCUSDT", tf, max_bars)
    TC = np.asarray(r["TC"], np.int64)
    t = htf_table("BTCUSDT", htf)
    ser = t[field] if expr is None else expr(t)
    tc_h = t["TC"].to_numpy()
    bad = leaks = checked = 0
    first_bad = None
    for i, tfs, v in rec:
        j = np.searchsorted(tc_h, TC[i], side="right") - 1
        exp = ser.iloc[j] if j >= 0 else np.nan
        if v != v and exp != exp:
            continue
        checked += 1
        if not (abs(v - exp) <= 1e-9 * max(1.0, abs(exp))):
            bad += 1
            # would it match the still-open HTF bar (a leak)?
            if j + 1 < len(ser) and abs(v - ser.iloc[j + 1]) <= 1e-9 * max(1.0, abs(v)):
                leaks += 1
            if first_bad is None:
                first_bad = (i, pd.to_datetime(TC[i], unit="ms"), v, exp)
    print(f"{name}: flags={sorted(cs.flags)} values checked {checked}, mismatches {bad}, of which equal to the "
          f"still-forming HTF bar {leaks}; first mismatch {first_bad}")


if __name__ == "__main__":
    v5 = "//@version=5\nstrategy('t')\n"
    check("1h chart, D close", v5 + "x = request.security(syminfo.tickerid, 'D', close)\nif x > 0\n    strategy.entry('L', strategy.long)\n",
          H, D, "c", 3000)
    check("1h chart, D high (lookahead_on requested)", v5 + "x = request.security(syminfo.tickerid, 'D', high, lookahead=barmerge.lookahead_on)\nif x > 0\n    strategy.entry('L', strategy.long)\n",
          H, D, "h", 3000)
    check("4h chart, D sma(close,3)", v5 + "x = request.security(syminfo.tickerid, 'D', ta.sma(close, 3))\nif x > 0\n    strategy.entry('L', strategy.long)\n",
          4 * H, D, None, 3000, expr=lambda t: t["c"].rolling(3).mean())
    check("1d chart, W close", v5 + "x = request.security(syminfo.tickerid, 'W', close)\nif x > 0\n    strategy.entry('L', strategy.long)\n",
          D, 7 * D, "c", 2100)
    check("1h chart, 240 close[1]", v5 + "x = request.security(syminfo.tickerid, '240', close[1])\nif x > 0\n    strategy.entry('L', strategy.long)\n",
          H, 4 * H, None, 3000, expr=lambda t: t["c"].shift(1))
    check("v2 security (implicit lookahead on TradingView), 1h chart, D close",
          "//@version=2\nstrategy('t')\nx = security(tickerid, 'D', close)\nif x > 0\n    strategy.entry('L', strategy.long)\n",
          H, D, "c", 3000)
