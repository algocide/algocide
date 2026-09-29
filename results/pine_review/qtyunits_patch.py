#!/usr/bin/env python3
"""Sensitivity run for F1: absolute quantities in strategy.close / strategy.exit / strategy.order interpreted in the
script's own units (rescaled to the tournament's resized entry), instead of the engine's units.

For every trade the script's requested entry qty is remembered (explicit `qty=` of the entry/order call). An absolute
reducing qty q (script units) then closes q * engine_qty0 / script_qty0 engine units of that trade (capped at what is
left). Trades whose entry had no explicit qty keep the engine behaviour. Exits with absolute qty are applied per
trade as in the engine (a per-position allocation would need more state). Monkeypatch in this process only.

Usage: PYTHONPATH=src python3 results/pine_review/qtyunits_patch.py            (runs the class, writes qtyunits_class.csv)
"""
import json, math, os, re, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pinebt import broker as BR
from pinebt.engine import CompiledScript, Runner
from stoplimit_class import stats, tf_ms

HERE = os.path.dirname(os.path.abspath(__file__))
O = {k: getattr(BR.Broker, k) for k in ("entry", "order", "_open", "_close_id", "_fill_exit", "_exec_order", "_refresh")}
REQ = {}          # entry id -> script qty requested at the latest call (explicit only)
SQ0 = {}          # id(Trade) -> script qty of that trade's entry


def _num(q):
    try:
        q = float(q)
        return q if q == q and q > 0 else None
    except (TypeError, ValueError):
        return None


def entry(self, oid, d, qty=None, limit=None, stop=None, oca=None, when=True):
    if self._ok(when):
        REQ[oid] = _num(qty)
    return O["entry"](self, oid, d, qty, limit, stop, oca, when)


def order(self, oid, d, qty=None, limit=None, stop=None, oca=None, when=True):
    if self._ok(when):
        REQ[oid] = _num(qty)
    return O["order"](self, oid, d, qty, limit, stop, oca, when)


def _open(self, eid, d, px, i, qty_units=None, obar=None):
    n0 = len(self.trades)
    O["_open"](self, eid, d, px, i, qty_units, obar)
    if len(self.trades) > n0:
        SQ0[id(self.trades[-1])] = REQ.get(eid)


def scale(tr):
    s = SQ0.get(id(tr))
    return None if not s else tr.q0 / s          # engine units per script unit


def _close_id(self, oid, q, pct, px, i):
    if q is not None:
        tgt = [t for t in self.trades if t.eid == oid]
        if tgt and all(scale(t) for t in tgt):
            want = q
            for tr in tgt:                            # script units, FIFO
                if want <= 1e-15:
                    break
                rem_script = abs(tr.q) / scale(tr)
                take = min(rem_script, want)
                self._close_trade(tr, take * scale(tr), px, i)
                want -= take
            return
    return O["_close_id"](self, oid, q, pct, px, i)


def _fill_exit(self, x, tr, px, i):
    if x.qty is not None and scale(tr):
        key = (x.id, x.frm)
        tr.exits_done.add(key)
        self._close_trade(tr, min(x.qty * scale(tr), abs(tr.q)), px, i)
        return
    return O["_fill_exit"](self, x, tr, px, i)


def _exec_order(self, oid, d, q, px, i, obar=None):
    pos = self._pos()
    if q is not None and pos != 0 and (pos > 0) != (d == 1) and self.trades and all(scale(t) for t in self.trades):
        want = q
        for tr in list(self.trades):
            if want <= 1e-15:
                break
            rem_script = abs(tr.q) / scale(tr)
            take = min(rem_script, want)
            self._close_trade(tr, take * scale(tr), px, i)
            want -= take
        if want > 1e-12:                              # more than the position: the rest opens the other side
            REQ[oid] = want
            self._open(oid, d, px, i, obar=obar)
        return
    return O["_exec_order"](self, oid, d, q, px, i, obar)


def _refresh(self, px):
    """The script sees strategy.position_size in its own units when every open trade has a known script qty, so
    quantities it derives from position_size stay consistent with the conversion above."""
    O["_refresh"](self, px)
    if self.trades and all(scale(t) for t in self.trades):
        self.pos_size = sum(t.q / scale(t) for t in self.trades)


def install(on):
    for k in O:
        setattr(BR.Broker, k, globals()[k] if on else O[k])
    REQ.clear(); SQ0.clear()


def run(src, sym, tf, patched):
    install(patched)
    try:
        return Runner(CompiledScript(src), sym, tf, end_ms=1790640000000, max_bars=200000, fee=0.0007, magnify=True).run()
    finally:
        install(False)


if __name__ == "__main__":
    idx = pd.read_parquet("data/pine/index.parquet")
    p = idx[idx.lang == "pinescript"].set_index("file")
    pats = [re.compile(r"strategy\.exit\s*\([^\n]*\bqty\s*="), re.compile(r"strategy\.close\s*\([^\n]*\bqty\s*="),
            re.compile(r"strategy\.order\s*\([^\n]*\bqty\s*=|strategy\.order\s*\([^,\n]+,[^,\n]+,\s*[0-9.]+")]
    files = [f for f in p.index if any(pt.search(p.loc[f, "source"]) for pt in pats)]
    only = sys.argv[1:]
    rows = []
    for f in files:
        if only and f not in only:
            continue
        tf = tf_ms(p.loc[f, "bt_period"])
        if tf < 3_600_000:
            continue
        src, lm = p.loc[f, "source"], p.loc[f, "last_modified"]
        row = {"file": f, "tf_ms": tf}
        try:
            for sym in ("BTCUSDT", "ETHUSDT"):
                for patched in (False, True):
                    r = run(src, sym, tf, patched)
                    tag = ("su_" if patched else "eng_") + sym[:3].lower()
                    sh, nt, nd, bl = stats(r, lm)
                    row.update({tag + "_sharpe": sh, tag + "_trades": nt, tag + "_days": nd, tag + "_blown": bl,
                                tag + "_err": r["err_bars"]})
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"[:200]
        for m in ("eng", "su"):
            row[m + "_eligible"] = all(row.get(f"{m}_{s}_trades", 0) >= 20 and row.get(f"{m}_{s}_days", 0) >= 365 and
                                       not row.get(f"{m}_{s}_blown", True) and row.get(f"{m}_{s}_err", 1) == 0
                                       for s in ("btc", "eth"))
            row[m + "_score"] = np.nanmean([row.get(f"{m}_btc_sharpe", np.nan), row.get(f"{m}_eth_sharpe", np.nan)])
        rows.append(row)
        print(f"{f[:60]:60s} eng {row['eng_score']:.2f} {row['eng_eligible']}  script-units {row['su_score']:.2f} "
              f"{row['su_eligible']}", flush=True)
    df = pd.DataFrame(rows)
    if not only:
        df.to_csv(os.path.join(HERE, "qtyunits_class.csv"), index=False)
    ch = df[(df.eng_score - df.su_score).abs() > 1e-9]
    print(f"{len(df)} scripts run; {len(ch)} change score; eligible engine {df.eng_eligible.sum()} vs script units "
          f"{df.su_eligible.sum()}")
