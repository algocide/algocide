#!/usr/bin/env python3
"""Nearby-parameter robustness for MOM-RS on dev+validation only (holdout sealed). Variants: lookback {90, 150}, top {3, 8},
hold {10, 30} around (120, top5, hold20). Each counts as a trial. Portfolio mode, base costs."""
import os, sys, json
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
import hlr2.daily as D
from hlr2.daily import run_daily, DailyCosts, momentum_rs, features
from hlr2.daily_data import build, SPLITS
from hlr2.metrics import summarize
# extend features with extra lookbacks
_orig_features = D.features
def features_ext(df):
    f = _orig_features(df); c = f["c"]
    for lb in (90, 150): f[f"ret{lb}"] = np.r_[[np.nan] * lb, c[lb:] / c[:-lb] - 1]
    return f
D.features = features_ext
def mom(lookback, top, hold):
    key = f"ret{lookback}"
    def sig(f, i):
        if not (np.isfinite(f["sma50"][i]) and np.isfinite(f[key][i]) and np.isfinite(f["atr"][i])): return None
        if f["c"][i] > f["sma50"][i] and f[key][i] > 0: return (1, f["c"][i] - 2 * f["atr"][i], f[key][i])
        return None
    def ex(f, i, pos): return (i - pos["entry_i"]) >= hold
    return sig, ex, f"MOM-RS({lookback},top{top},hold{hold})"
variants = [(120, 5, 20), (90, 5, 20), (150, 5, 20), (120, 3, 20), (120, 8, 20), (120, 5, 10), (120, 5, 30)]
for lb, top, hold in variants: D.STRATEGIES[f"MOM-RS({lb},top{top},hold{hold})"] = (lambda lb=lb, top=top, hold=hold: mom(lb, top, hold))
panel, _, cal, _ = build(); vs, hs = pd.Timestamp(SPLITS["val_start"]), pd.Timestamp(SPLITS["hold_start"]); rows = []
for lb, top, hold in variants:
    name = f"MOM-RS({lb},top{top},hold{hold})"
    t = run_daily(panel, name, mode="portfolio", costs=DailyCosts(), end=hs, top=top, max_positions=max(5, top))
    ent = pd.to_datetime(t.entry_ts); xt = pd.to_datetime(t.exit_ts)
    dev = t[(ent < vs) & (xt <= vs)]; val = t[(ent >= vs) & (ent < hs) & (xt <= hs)]; sd, sv = summarize(dev), summarize(val)
    rows.append({"config": name, "is_base": (lb, top, hold) == (120, 5, 20), "dev_n": sd["n_trades"], "dev_net": sd["net_pnl"], "dev_pf": sd["profit_factor"], "val_n": sv["n_trades"], "val_net": sv["net_pnl"], "val_pf": sv["profit_factor"], "val_win": sv["win_rate"], "val_ex_best_trade": sv.get("net_ex_best_trade"), "val_syms_pos": int((val.groupby("sym").net_pnl.sum() > 0).sum()) if len(val) else 0})
    print(rows[-1], flush=True)
df = pd.DataFrame(rows); df.to_csv(os.path.join(R, "results", "phase3", "robustness_momrs.csv"), index=False); print(df.round(2).to_string(index=False))
