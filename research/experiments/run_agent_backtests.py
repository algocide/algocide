#!/usr/bin/env python3
"""Predeclared grid for the agent's rule-consensus decider (family 'A'), run through the tested engine with the same
protocol as the earlier trials (dev 50% / val 30% / holdout 20% sealed; base + adverse costs). Appends rows to
results/<universe>/experiments.csv keyed by (config, regime). Budget: 8 distinct configurations.
Grid (declared before running): score_threshold {2, 3} x (sl_atr, tp_atr) {(2.0, 4.0), (1.5, 3.0)} x trend tf {higher tf, same tf}.
Universes: crypto247 (1h bars, 24/7), crypto1h (1h, US session), crypto15 (15m, US session, 1h trend), stocks (15m sampled, dev+val only)."""
import argparse, os, sys, json, time
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src")); sys.path.insert(0, R)
from hlr2.data import load_sampled, load_candles
from hlr2.backtest import run
from hlr2.metrics import summarize, by_group, windows
from hlr2.funding import load_funding
from agent.backtest_adapter import RuleConsensusStrategy
from run_experiments import STOCKS, CRYPTO, split_days

ap = argparse.ArgumentParser(); ap.add_argument("--universe", required=True); ap.add_argument("--regimes", default="base,adverse"); a = ap.parse_args(); u = a.universe
if u == "stocks": panel = load_sampled(STOCKS); syms = STOCKS
elif u == "crypto15": panel = load_candles(CRYPTO, "15m", "us_regular"); syms = CRYPTO
elif u == "crypto1h": panel = load_candles(CRYPTO, "1h", "us_regular"); syms = CRYPTO
elif u == "crypto247": panel = load_candles(CRYPTO, "1h", "24x7"); syms = CRYPTO
else: raise SystemExit(u)
fund = load_funding(syms); sp = split_days(panel); out = os.path.join(R, "results", u); os.makedirs(os.path.join(out, "trades"), exist_ok=True)
same_tf = "15m" if panel.bar_minutes == 15 else "1h"
cfgs = []
for th in (2, 3):
    for sl, tp in ((2.0, 4.0), (1.5, 3.0)):
        for tf in ("1h", same_tf):
            if tf == "1h" and same_tf == "1h" and tf != same_tf: continue
            cfgs.append(RuleConsensusStrategy(score_threshold=th, sl_atr=sl, tp_atr=tp, tf_trend=tf))
seen = set(); rows = []
for regime in a.regimes.split(","):
    for strat in cfgs:
        cid = f"A:{strat.describe()}"
        if (cid, regime) in seen: continue
        seen.add((cid, regime)); t0 = time.time()
        res = run(panel, strat, cost_regime=regime, funding=fund); tr = res["trades"]; eq = res["equity"]
        fn = os.path.join(out, "trades", f"A_{strat.name}_{abs(hash(cid)) % 10**8}_{regime}.parquet")
        if len(tr): tr.assign(config=cid, regime=regime).to_parquet(fn, index=False)
        ent = pd.to_datetime(tr.entry_ts, utc=True) if len(tr) else pd.Series([], dtype="datetime64[ns, UTC]"); xt = pd.to_datetime(tr.exit_ts, utc=True) if len(tr) else ent
        dev = tr[(ent < sp["val_start"]) & (xt <= sp["val_start"])] if len(tr) else tr
        val = tr[(ent >= sp["val_start"]) & (ent < sp["hold_start"]) & (xt <= sp["hold_start"])] if len(tr) else tr
        sd, sv = summarize(dev), summarize(val); w = windows(val, sp["val_windows"]); eq_dv = eq[eq.ts < sp["hold_start"]]
        sacc = summarize(tr[(ent < sp["hold_start"]) & (xt <= sp["hold_start"])] if len(tr) else tr, eq_dv)
        row = {"universe": u, "family": "A", "config": cid, "regime": regime, "seconds": round(time.time() - t0, 1), "n_rejections": len(res["rejections"]),
               "dev_n": sd["n_trades"], "dev_days": sd.get("n_days"), "dev_net": sd["net_pnl"], "dev_exp": sd["expectancy_usd"], "dev_pf": sd["profit_factor"], "dev_win": sd["win_rate"],
               "val_n": sv["n_trades"], "val_days": sv.get("n_days"), "val_net": sv["net_pnl"], "val_exp": sv["expectancy_usd"], "val_exp_r": sv.get("expectancy_r"), "val_pf": sv["profit_factor"], "val_win": sv["win_rate"],
               "val_ci95": sv.get("exp_ci95"), "val_p_le0": sv.get("p_mean_le_0"), "val_ex_best_trade": sv.get("net_ex_best_trade"), "val_ex_best_day": sv.get("net_ex_best_day"),
               "val_windows_pos": int((w.net > 0).sum()), "val_windows_n": int(len(w)), "val_windows_with_trades": int((w.n > 0).sum()),
               "val_net_long": sv.get("net_long"), "val_net_short": sv.get("net_short"), "val_n_long": sv.get("n_long"), "val_n_short": sv.get("n_short"),
               "val_cost_share": sv.get("cost_share_of_gross_profits"), "val_maxdd": sv.get("max_dd_usd"), "val_hold_min": sv.get("avg_hold_min"),
               "devval_final_equity": sacc.get("final_equity"), "devval_shadow_equity": sacc.get("final_shadow_equity"), "devval_paused": sacc.get("paused"), "devval_paused_at": sacc.get("paused_at"),
               "devval_exposure": sacc.get("exposure"), "devval_acct_maxdd": sacc.get("account_max_dd_usd"), "trades_file": os.path.basename(fn) if len(tr) else None}
        if len(val):
            bi = by_group(val, "sym"); row["val_by_sym_net"] = {k: round(v, 2) for k, v in bi.net.items()}; row["val_syms_positive"] = int((bi.net > 0).sum())
        rows.append(row)
        print(f"[{u}/{regime}] {cid:95s} dev n={sd['n_trades']:4d} net={sd['net_pnl']:7.2f} pf={sd['profit_factor']:5.2f} | val n={sv['n_trades']:4d} net={sv['net_pnl']:7.2f} pf={sv['profit_factor']:5.2f} win_w={row['val_windows_pos']}/{row['val_windows_n']} ({row['seconds']}s)", flush=True)
df = pd.DataFrame(rows); f = os.path.join(out, "experiments.csv")
if os.path.exists(f):
    old = pd.read_csv(f); key = set(zip(df.config, df.regime)); old = old[[(c, g) not in key for c, g in zip(old.config, old.regime)]]; df = pd.concat([old, df], ignore_index=True)
df.to_csv(f, index=False); print("wrote", f, len(df), "rows")
