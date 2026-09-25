#!/usr/bin/env python3
"""ONE-SHOT holdout evaluation for at most one primary candidate. Reads the stored full-period trade logs (written by
run_experiments.py) and reports the holdout period (last 20% of trading days) for the candidate under every cost regime
that was run, plus by-instrument, by-direction, excluding-best, and day-clustered bootstrap intervals.
A marker file results/<universe>/HOLDOUT_OPENED.json records when and for which config the holdout was opened; a second
call for a different config on the same universe is refused (the holdout is no longer untouched).
Usage: PYTHONPATH=src python3 experiments/evaluate_holdout.py --universe stocks --config "F6:orb(or_min=30, target_r=2.0)"
"""
import argparse, os, sys, json, glob, datetime as dt
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.metrics import summarize, by_group, windows

ap = argparse.ArgumentParser(); ap.add_argument("--universe", required=True); ap.add_argument("--config", required=True); ap.add_argument("--force-secondary", action="store_true")
a = ap.parse_args()
out = os.path.join(R, "results", a.universe); marker = os.path.join(out, "HOLDOUT_OPENED.json")
if os.path.exists(marker):
    m = json.load(open(marker))
    if m["config"] != a.config and not a.force_secondary:
        raise SystemExit(f"REFUSED: holdout for {a.universe} already opened for {m['config']} at {m['opened_at']}. It is no longer untouched.")
else:
    json.dump({"config": a.config, "opened_at": dt.datetime.now(dt.timezone.utc).isoformat()}, open(marker, "w"), indent=1)
sp = json.load(open(os.path.join(out, "splits.json"))); hold_start = pd.Timestamp(sp["hold_start"]); end = pd.Timestamp(sp["end"])
ex = pd.read_csv(os.path.join(out, "experiments.csv"))
rows = ex[ex.config == a.config]
if not len(rows): raise SystemExit("config not found in experiments.csv")
report = {"universe": a.universe, "config": a.config, "hold_start": str(hold_start), "end": str(end), "regimes": {}}
for _, r in rows.iterrows():
    if not isinstance(r.trades_file, str): report["regimes"][r.regime] = {"n_trades": 0}; continue
    t = pd.read_parquet(os.path.join(out, "trades", r.trades_file))
    ent = pd.to_datetime(t.entry_ts, utc=True); xt = pd.to_datetime(t.exit_ts, utc=True)
    h = t[(ent >= hold_start)]                       # trades entered in the holdout (exits after the data end are force-closed by the engine)
    s = summarize(h)
    # holdout windows of ~10 trading days
    days = sorted(set(pd.to_datetime(h.entry_ts, utc=True).dt.tz_convert("America/New_York").dt.date)) if len(h) else []
    bounds = []
    for j in range(0, len(days), 10):
        a0 = pd.Timestamp(days[j]).tz_localize("UTC") - pd.Timedelta(hours=6)
        b0 = (pd.Timestamp(days[j + 10]).tz_localize("UTC") - pd.Timedelta(hours=6)) if j + 10 < len(days) else end
        bounds.append((a0, b0))
    w = windows(h, bounds)
    rep = {k: (v if not isinstance(v, (np.floating, np.integer)) else float(v)) for k, v in s.items()}
    rep["windows"] = w.to_dict("records"); rep["by_sym"] = by_group(h, "sym").round(3).to_dict("index"); rep["by_dir"] = by_group(h, "direction").round(3).to_dict("index")
    rep["by_month"] = by_group(h, "month").round(3).to_dict("index")
    # account path with the $10 pause, restarted at $100 at the holdout start (fresh account for the holdout)
    if len(h):
        eq, hw, paused, counted, shadow = 100.0, 100.0, False, [], []
        for _, tr in h.sort_values("entry_ts").iterrows():
            if not paused: eq += tr.net_pnl; counted.append(tr.net_pnl)
            else: shadow.append(tr.net_pnl)
            hw = max(hw, eq)
            if eq < hw - 10: paused = True
        rep["holdout_account"] = {"final_equity": eq, "paused": paused, "n_counted": len(counted), "n_shadow": len(shadow), "shadow_net_after_pause": float(sum(shadow))}
    report["regimes"][r.regime] = rep
    print(f"[{a.universe}/{r.regime}] holdout n={s['n_trades']} days={s.get('n_days')} net={s['net_pnl']:.2f} exp={s['expectancy_usd']:.3f} pf={s['profit_factor']:.2f} win={s['win_rate']:.2f} ci={s.get('exp_ci95')} ex_best_trade={s.get('net_ex_best_trade')} ex_best_day={s.get('net_ex_best_day')} windows_pos={int((w.net>0).sum())}/{len(w)}")
    print("  by_sym:", {k: v['net'] for k, v in rep['by_sym'].items()}, " by_dir:", {k: v['net'] for k, v in rep['by_dir'].items()})
json.dump(report, open(os.path.join(out, f"holdout_{abs(hash(a.config)) % 10**8}.json"), "w"), indent=1, default=str)
print("wrote holdout report")
