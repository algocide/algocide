#!/usr/bin/env python3
"""Evaluate paper-trading logs against the pre-declared criteria in docs/forward_plan.md.
Usage: python forward/evaluate.py data/paper"""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from hlr.stats import circular_block_bootstrap_ci


def read(path):
    if not os.path.exists(path): return []
    return [json.loads(l) for l in open(path) if l.strip()]


def main(d):
    fills = read(os.path.join(d, "fills.jsonl")); fund = read(os.path.join(d, "funding.jsonl")); eq = read(os.path.join(d, "equity.jsonl"))
    kills = read(os.path.join(d, "kill.jsonl"))
    print(f"fills={len(fills)} funding accruals={len(fund)} equity points={len(eq)} kills={len(kills)}")
    # per strategy: realised per closed event (bps of notional)
    by = {}
    for f in fills:
        if f.get("action") == "close":
            notional = abs(f["units"] * f["px"])
            bps = (f.get("realised", 0) + f.get("hedge_realised", 0) - f.get("fee_usd", 0)) / notional * 1e4 if notional else 0.0
            by.setdefault(f["strategy"], []).append(bps)
    for s, x in by.items():
        x = np.array(x); m, lo, hi = circular_block_bootstrap_ci(x, block=5, n_boot=2000)
        print(f"{s}: n_events={len(x)} mean_net_bps={m:.2f} CI95=[{lo:.2f},{hi:.2f}] win={100*(x>0).mean():.0f}%  -> "
              + ("PASS candidate" if (len(x) >= 60 and m >= 8 and lo > 0 and (x > 0).mean() >= 0.55) else ("FAIL" if (len(x) >= 60 and m <= 0) else "insufficient sample")))
    if eq:
        e = np.array([p["equity"] for p in eq]); print(f"equity: last={e[-1]:.2f} min={e.min():.2f} max={e.max():.2f} maxDD={(e - np.maximum.accumulate(e)).min():.2f}")
    for k in kills: print("KILL:", k["ts"], k["reason"])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/paper")
