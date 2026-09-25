#!/usr/bin/env python3
"""Run the predeclared configuration set (docs/PROTOCOL_FREEZE.md) on (a) the selected stock-linked universe (sampled
15-min mids) and (b) BTC/ETH real candles under the same US-session rules; write trade logs and EXPERIMENTS.csv.

HOLDOUT DISCIPLINE: the last 20% of trading days is never summarised here. Trades are stored in full, but every metric
in EXPERIMENTS.csv is computed on the development (first 50%) and validation (next 30%) periods only. The holdout is
evaluated once, for at most one candidate, by experiments/evaluate_holdout.py.
Usage: PYTHONPATH=src python3 experiments/run_experiments.py --universe stocks|crypto15|crypto1h|crypto247 [--regimes base,adverse,standard_fee]
"""
import argparse, os, sys, json, time, itertools
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.data import load_sampled, load_candles
from hlr2.strategies import MATrend, BollingerMR, VolCompressionBO, ChannelBO, TrendPullback, OpeningRange, SessionLong
from hlr2.backtest import run
from hlr2.metrics import summarize, by_group, windows
from hlr2.funding import load_funding

STOCKS = ["xyz:SNDK", "xyz:MU", "xyz:NVDA", "xyz:META", "xyz:GOOGL"]
CRYPTO = ["BTC", "ETH"]


def configs(universe: str):
    C = []
    for tf in ("15m", "1h"):
        for fast, slow in ((10, 40), (20, 80)):
            for flt in (False, True):
                C.append(("F1" if not flt else "F1b", MATrend(tf=tf, fast=fast, slow=slow, filter=flt)))
        for k in (2.0, 2.5):
            for reg in (False, True):
                C.append(("F2", BollingerMR(tf=tf, k=k, regime=reg)))
        for pct in (0.1, 0.2):
            C.append(("F3", VolCompressionBO(tf=tf, pct=pct)))
        for n in (12, 24):
            C.append(("F4", ChannelBO(tf=tf, n=n)))
    for defn in ("A", "B"):
        for tr in (1.5, 2.0):
            C.append(("F5", TrendPullback(defn=defn, target_r=tr)))
    if universe != "crypto247":
        for orm in (30, 60):
            for tr in (2.0, None):
                C.append(("F6", OpeningRange(or_min=orm, target_r=tr)))
    if universe == "crypto1h":   # 1h-only data: only 1h configs are meaningful
        C = [(f, s) for f, s in C if s.params.get("tf") == "1h" and f in ("F1", "F2", "F3", "F4")]
    if universe == "crypto247":
        C = [(f, s) for f, s in C if f in ("F1", "F4") and s.params.get("tf") == "1h"]   # small distinct experiment
    return C


def split_days(panel):
    """Chronological split on distinct session dates: dev 50% / val 30% / holdout 20%. Returns boundary timestamps."""
    d0 = panel.inst[panel.symbols[0]]
    days = sorted({x for x in d0.session_date.values if x is not None})
    n = len(days); i_dev, i_val = int(n * 0.5), int(n * 0.8)
    def start_of(day): return pd.Timestamp(day).tz_localize("UTC") - pd.Timedelta(hours=6)   # before any US-session bar of that ET date
    return {"n_days": n, "dev_start": panel.grid[0], "val_start": start_of(days[i_dev]), "hold_start": start_of(days[i_val]), "end": panel.grid[-1] + pd.Timedelta(minutes=1),
            "val_windows": [(start_of(days[j]), start_of(days[min(j + 10, i_val)]) if j + 10 < i_val else start_of(days[i_val])) for j in range(i_dev, i_val, 10)]}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--universe", default="stocks"); ap.add_argument("--regimes", default="base,adverse")
    ap.add_argument("--out", default=None); ap.add_argument("--families", default=None); ap.add_argument("--append", action="store_true"); a = ap.parse_args()
    u = a.universe
    if u == "stocks": panel = load_sampled(STOCKS); syms = STOCKS
    elif u == "crypto15": panel = load_candles(CRYPTO, "15m", "us_regular"); syms = CRYPTO
    elif u == "crypto1h": panel = load_candles(CRYPTO, "1h", "us_regular"); syms = CRYPTO
    elif u == "crypto247": panel = load_candles(CRYPTO, "1h", "24x7"); syms = CRYPTO
    else: raise SystemExit(u)
    fund = load_funding(syms)
    sp = split_days(panel)
    out = a.out or os.path.join(R, "results", u); os.makedirs(os.path.join(out, "trades"), exist_ok=True)
    json.dump({k: str(v) if not isinstance(v, list) else [(str(x), str(y)) for x, y in v] for k, v in sp.items()}, open(os.path.join(out, "splits.json"), "w"), indent=1)
    print(panel, "\nsplits:", {k: str(v) for k, v in sp.items() if k != "val_windows"}, "val windows:", len(sp["val_windows"]), flush=True)
    rows = []
    cfgs = configs(u) + [("B0", SessionLong())]
    if a.families: cfgs = [(f, s) for f, s in cfgs if f in a.families.split(",")]
    for regime in a.regimes.split(","):
        for fam, strat in cfgs:
            t0 = time.time()
            res = run(panel, strat, cost_regime=regime, funding=fund)
            tr = res["trades"]; eq = res["equity"]
            cid = f"{fam}:{strat.describe()}"
            fn = os.path.join(out, "trades", f"{fam}_{strat.name}_{abs(hash(cid)) % 10**8}_{regime}.parquet")
            if len(tr): tr.assign(config=cid, regime=regime).to_parquet(fn, index=False)
            ent = pd.to_datetime(tr.entry_ts, utc=True) if len(tr) else pd.Series([], dtype="datetime64[ns, UTC]")
            xt = pd.to_datetime(tr.exit_ts, utc=True) if len(tr) else ent
            dev = tr[(ent < sp["val_start"]) & (xt <= sp["val_start"])] if len(tr) else tr
            val = tr[(ent >= sp["val_start"]) & (ent < sp["hold_start"]) & (xt <= sp["hold_start"])] if len(tr) else tr
            sdev, sval = summarize(dev), summarize(val)
            eq_dv = eq[eq.ts < sp["hold_start"]]
            sacc = summarize(tr[(ent < sp["hold_start"]) & (xt <= sp["hold_start"])] if len(tr) else tr, eq_dv)
            w = windows(val, sp["val_windows"])
            row = {"universe": u, "family": fam, "config": cid, "regime": regime, "seconds": round(time.time() - t0, 1), "n_rejections": len(res["rejections"]),
                   "dev_n": sdev["n_trades"], "dev_days": sdev.get("n_days"), "dev_net": sdev["net_pnl"], "dev_exp": sdev["expectancy_usd"], "dev_pf": sdev["profit_factor"], "dev_win": sdev["win_rate"],
                   "val_n": sval["n_trades"], "val_days": sval.get("n_days"), "val_net": sval["net_pnl"], "val_exp": sval["expectancy_usd"], "val_exp_r": sval.get("expectancy_r"), "val_pf": sval["profit_factor"], "val_win": sval["win_rate"],
                   "val_ci95": sval.get("exp_ci95"), "val_p_le0": sval.get("p_mean_le_0"), "val_ex_best_trade": sval.get("net_ex_best_trade"), "val_ex_best_day": sval.get("net_ex_best_day"),
                   "val_windows_pos": int((w.net > 0).sum()), "val_windows_n": int(len(w)), "val_windows_with_trades": int((w.n > 0).sum()),
                   "val_net_long": sval.get("net_long"), "val_net_short": sval.get("net_short"), "val_n_long": sval.get("n_long"), "val_n_short": sval.get("n_short"),
                   "val_cost_share": sval.get("cost_share_of_gross_profits"), "val_maxdd": sval.get("max_dd_usd"), "val_hold_min": sval.get("avg_hold_min"),
                   "devval_final_equity": sacc.get("final_equity"), "devval_shadow_equity": sacc.get("final_shadow_equity"), "devval_paused": sacc.get("paused"), "devval_paused_at": sacc.get("paused_at"),
                   "devval_exposure": sacc.get("exposure"), "devval_acct_maxdd": sacc.get("account_max_dd_usd"), "trades_file": os.path.basename(fn) if len(tr) else None}
            if len(val):
                bi = by_group(val, "sym"); row["val_by_sym_net"] = {k: round(v, 2) for k, v in bi.net.items()}; row["val_syms_positive"] = int((bi.net > 0).sum())
            rows.append(row)
            print(f"[{u}/{regime}] {cid:70s} dev n={sdev['n_trades']:4d} net={sdev['net_pnl']:7.2f} pf={sdev['profit_factor']:5.2f} | val n={sval['n_trades']:4d} net={sval['net_pnl']:7.2f} pf={sval['profit_factor']:5.2f} win_w={row['val_windows_pos']}/{row['val_windows_n']} ({row['seconds']}s)", flush=True)
    df = pd.DataFrame(rows)
    if a.append and os.path.exists(os.path.join(out, "experiments.csv")):
        old = pd.read_csv(os.path.join(out, "experiments.csv")); old = old[~old.config.isin(df.config)]; df = pd.concat([old, df], ignore_index=True)
    df.to_csv(os.path.join(out, "experiments.csv"), index=False)
    print("wrote", os.path.join(out, "experiments.csv"), len(df), "rows")


if __name__ == "__main__":
    main()
