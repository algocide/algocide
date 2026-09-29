#!/usr/bin/env python3
"""F4 evidence: engine ta.hma vs TradingView's Hull MA source wma(2*wma(src, len/2) - wma(src, len), floor(sqrt(len)))
on BTC daily closes. Usage: PYTHONPATH=src python3 results/pine_review/hma_check.py"""
import math, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src")
from pinebt import runtime as rt, engine
from pinebt.engine import CompiledScript
from pinebt.broker import Broker
from pinebt.data import Bars


def probe(body, bars):
    cs = CompiledScript("//@version=5\nstrategy('p')\n" + body + "\n")
    code = cs.pysrc.replace("        return None", "        PROBE.append(g_out)")
    g = dict(engine.EXEC_GLOBALS); rec = []; g["PROBE"] = rec
    exec(compile(code, "<p>", "exec"), g)
    bk = Broker(bars, cs.cfg, 0.0)
    step = g["build"](rt, bars, bk, None, engine.tf_cfg("BTCUSDT", bars.tf_ms))
    for i in range(bars.n):
        bk.begin_bar(i); step(i); bk.end_bar(i)
    return np.array(rec, float)


def wma(x, n):
    w = np.arange(1, n + 1, dtype=float)
    return pd.Series(x).rolling(n).apply(lambda a: np.dot(a, w) / w.sum(), raw=True).to_numpy()


def hma_tv(x, n):
    d = 2 * wma(x, n // 2) - wma(x, n)
    return wma(d, int(math.floor(math.sqrt(n))))


if __name__ == "__main__":
    b = Bars("BTCUSDT", 86_400_000, end_ms=1790640000000, max_bars=400)
    c = np.array(b.C)
    for n in (9, 14, 21, 55):
        e = probe(f"out = ta.hma(close, {n})", b)
        ref = hma_tv(c, n)
        ok = ~np.isnan(e) & ~np.isnan(ref)
        print(f"hma({n}): max |engine - floor(sqrt) reference| / price = {np.max(np.abs(e[ok] - ref[ok]) / c[ok]):.2e}")
