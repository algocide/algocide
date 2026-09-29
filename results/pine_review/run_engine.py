#!/usr/bin/env python3
"""Run the pinebt engine on one vault script (BTCUSDT by default) and pickle the result for comparison.

Usage: PYTHONPATH=src python3 results/pine_review/run_engine.py <vault file> <tf_ms> [symbol] [--plain]
"""
import os, pickle, sys, time
import pandas as pd

sys.path.insert(0, "src")
from pinebt.engine import CompiledScript, Runner

END_MS = 1790640000000
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine_runs")


def source(file):
    df = pd.read_parquet("data/pine/index.parquet")
    return df[df.file == file].iloc[0].source


def run(file, tf_ms, symbol="BTCUSDT", magnify=True):
    cs = CompiledScript(source(file))
    t0 = time.time()
    r = Runner(cs, symbol, tf_ms, end_ms=END_MS, max_bars=200000, fee=0.0007, magnify=magnify).run()
    r["cfg"] = cs.cfg
    r["flags"] = sorted(cs.flags)
    r["pysrc"] = cs.pysrc
    r["wall"] = time.time() - t0
    return r


if __name__ == "__main__":
    f, tf = sys.argv[1], int(sys.argv[2])
    sym = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "BTCUSDT"
    mag = "--plain" not in sys.argv
    os.makedirs(OUT, exist_ok=True)
    r = run(f, tf, sym, mag)
    tag = f"{f[:-3]}__{sym}__{'mag' if mag else 'plain'}.pkl"
    with open(os.path.join(OUT, tag), "wb") as fo:
        pickle.dump(r, fo)
    print(f, sym, "mag" if mag else "plain", "bars", r["n_bars"], "closed", len(r["closed"]), "eq_end",
          r["equity"][-1], "err_bars", r["err_bars"], "first_err", r["first_err"], "wall", round(r["wall"], 1))
