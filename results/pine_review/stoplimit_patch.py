#!/usr/bin/env python3
"""Sensitivity run: TradingView stop-limit semantics for entry/order calls that pass both stop= and limit=.
pinebt treats such an order as 'stop OR limit' (either level fills it); TradingView treats it as a stop-limit
(the stop must trigger first, then a limit order at `limit` works). This file monkeypatches the broker in this
process only (src/ is untouched) and re-runs one script to show the effect.

Usage: PYTHONPATH=src python3 results/pine_review/stoplimit_patch.py <vault file> <tf_ms>
"""
import math, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src")
from pinebt import broker as BR
from pinebt.engine import CompiledScript, Runner

TRIG = {}
_orig_cand = BR.Broker._candidates
_orig_hot = BR.Broker._next_hot


def _hit(lvl, rising, p0, p1, first, touch):
    up = p1 >= p0
    if touch and lvl == p0:
        return 0.0, p0
    if rising:
        if first and p0 >= lvl:
            return 0.0, p0
        if up and p0 < lvl <= p1:
            return lvl - p0, lvl
    else:
        if first and p0 <= lvl:
            return 0.0, p0
        if not up and p0 > lvl >= p1:
            return p0 - lvl, lvl
    return None


def candidates(self, p0, p1, first, touch=False):
    both = {k: po for k, po in self.porders.items() if po.limit is not None and po.stop is not None and po.bar < self.i}
    saved = {k: self.porders.pop(k) for k in both}
    try:
        out = _orig_cand(self, p0, p1, first, touch)
    finally:
        self.porders.update(saved)
    for k, po in both.items():
        buy = po.d == 1
        key = (po.id, po.bar, po.stop, po.limit)       # a re-issued/modified order starts untriggered
        if not TRIG.get(key):
            h = _hit(po.stop, buy, p0, p1, first, touch)          # buy stop rises to the level, sell stop falls
            if h is None:
                continue
            d, px = h
            if (buy and px <= po.limit) or ((not buy) and px >= po.limit):
                out.append((d, px, "po", po))
            else:
                TRIG[key] = True                                   # now a resting limit order
        else:
            h = _hit(po.limit, not buy, p0, p1, first, touch)     # buy limit falls to the level, sell limit rises
            if h is not None:
                out.append((h[0], h[1], "po", po))
    return out


def next_hot(self, mg, m, b):
    saved = []
    for po in self.porders.values():
        if po.limit is not None and po.stop is not None:
            if TRIG.get((po.id, po.bar, po.stop, po.limit)):
                saved.append((po, "stop", po.stop)); po.stop = None
            else:
                saved.append((po, "limit", po.limit)); po.limit = None
    try:
        return _orig_hot(self, mg, m, b)
    finally:
        for po, attr, v in saved:
            setattr(po, attr, v)


def run(src, sym, tf, patched):
    BR.Broker._candidates = candidates if patched else _orig_cand
    BR.Broker._next_hot = next_hot if patched else _orig_hot
    TRIG.clear()
    try:
        return Runner(CompiledScript(src), sym, tf, end_ms=1790640000000, max_bars=200000, fee=0.0007, magnify=True).run()
    finally:
        BR.Broker._candidates, BR.Broker._next_hot = _orig_cand, _orig_hot


def oos_sharpe(r, lm):
    tc = np.asarray(r["TC"], np.int64); eq = np.asarray(r["equity"])
    day = (tc - 1) // 86_400_000
    s = pd.Series(eq, index=day).groupby(level=0).last()
    lm_ms = int(pd.Timestamp(lm).value // 10**6)
    d0 = max(int(s.index[0]) + 30, (lm_ms + 86_400_000) // 86_400_000 + 1)
    d1 = int(pd.Timestamp("2026-09-28").value // 10**6 // 86_400_000)
    x = s.loc[d0 - 1:d1].pct_change().dropna()
    return x.mean() / x.std() * math.sqrt(365), sum(1 for c in r["closed"] if c[4] >= d0 * 86_400_000)


if __name__ == "__main__":
    f, tf = sys.argv[1], int(sys.argv[2])
    idx = pd.read_parquet("data/pine/index.parquet").set_index("file")
    src, lm = idx.loc[f, "source"], idx.loc[f, "last_modified"]
    res = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        for patched in (False, True):
            r = run(src, sym, tf, patched)
            sh, nt = oos_sharpe(r, lm)
            res[(sym, "stop-limit (TV)" if patched else "engine (stop OR limit)")] = {"oos_sharpe": sh, "oos_trades": nt,
                                                                                     "all_trades": len(r["closed"])}
    df = pd.DataFrame(res).T
    print(f)
    print(df.to_string())
    for m in ("engine (stop OR limit)", "stop-limit (TV)"):
        print(m, "score", np.mean([res[(s, m)]["oos_sharpe"] for s in ("BTCUSDT", "ETHUSDT")]))
