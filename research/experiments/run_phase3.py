#!/usr/bin/env python3
"""Phase 3 runner: the ten predeclared daily configurations on the 62-name underlying panel (and BTC/ETH), portfolio and
$100-account modes, base + adverse costs; development and validation metrics only (holdout sealed; one look later via
--holdout for ONE candidate). Writes results/phase3/experiments.csv and trade logs."""
import argparse, os, sys, json, time, datetime as dt
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.daily import run_daily, DailyCosts, STRATEGIES
from hlr2.daily_data import build, SPLITS, val_windows
from hlr2.metrics import summarize, by_group, windows

ap = argparse.ArgumentParser(); ap.add_argument("--universe", default="stocks"); ap.add_argument("--modes", default="portfolio,account"); ap.add_argument("--regimes", default="base,adverse")
ap.add_argument("--strategies", default=None); ap.add_argument("--holdout", default=None, help="ONE config name: evaluate the sealed holdout once"); a = ap.parse_args()
panel, cpanel, cal, audit = build(); out = os.path.join(R, "results", "phase3"); os.makedirs(os.path.join(out, "trades"), exist_ok=True)
P = panel if a.universe == "stocks" else cpanel
# funding tables (xyz hourly where available) and specs from the liquidity snapshot
try:
    from hlr2.funding import load_funding
    liq = pd.read_parquet(os.path.join(R, "data", "raw", "tohshi_liquidity_daily.parquet")); last = liq.sort_values("snapshot_time").groupby("symbol").last()
    specs = {s.split(":")[1]: (int(r.sz_decimals) if pd.notna(r.sz_decimals) else 3, 10.0) for s, r in last.iterrows() if s.startswith("xyz:")}
    ft = load_funding([f"xyz:{s}" for s in P] if a.universe == "stocks" else list(P)); ftab = {k.split(":")[-1]: v for k, v in ft.items()}
except Exception as e:
    specs, ftab = {}, {}; print("funding/specs unavailable:", e)
REG = {"base": DailyCosts(funding_tables=ftab), "adverse": DailyCosts(half_spread_bps=2.0, slip_bps=2.0, adverse_stop_bps=5.0, funding_tables=ftab), "standard_fee": DailyCosts(fee=0.0009, funding_tables=ftab)}
vs, hs, end = pd.Timestamp(SPLITS["val_start"]), pd.Timestamp(SPLITS["hold_start"]), pd.Timestamp(SPLITS["end"]); vw = val_windows(cal)
names = a.strategies.split(",") if a.strategies else list(STRATEGIES)
if a.holdout:
    marker = os.path.join(out, f"HOLDOUT_OPENED_{a.universe}.json")
    if os.path.exists(marker) and json.load(open(marker))["config"] != a.holdout: raise SystemExit(f"REFUSED: holdout already opened for {json.load(open(marker))['config']}")
    json.dump({"config": a.holdout, "opened_at": dt.datetime.now(dt.timezone.utc).isoformat()}, open(marker, "w"))
    rows = []
    for mode in a.modes.split(","):
        for reg in ["base", "adverse", "standard_fee"]:
            t = run_daily(P, a.holdout, mode=mode, costs=REG[reg], start=hs, end=end, specs=specs)
            s = summarize(t); rows.append({"config": a.holdout, "mode": mode, "regime": reg, **{k: v for k, v in s.items() if not isinstance(v, dict)}})
            print(f"[HOLDOUT {a.universe}/{mode}/{reg}] n={s['n_trades']} days={s.get('n_days')} net={s['net_pnl']:.2f} exp={s['expectancy_usd']:.3f} pf={s['profit_factor']:.2f} win={s['win_rate']:.2f} ci={s.get('exp_ci95')} ex_best={s.get('net_ex_best_trade')} long={s.get('net_long')} short={s.get('net_short')}")
            if len(t): print("   by_sym top/bottom:", by_group(t, "sym").net.sort_values().round(2).head(3).to_dict(), by_group(t, "sym").net.sort_values().round(2).tail(3).to_dict()); t.to_parquet(os.path.join(out, "trades", f"HOLDOUT_{a.universe}_{mode}_{reg}.parquet"), index=False)
    pd.DataFrame(rows).to_csv(os.path.join(out, f"holdout_{a.universe}.csv"), index=False); raise SystemExit
rows = []
for name in names:
    for mode in a.modes.split(","):
        for reg in a.regimes.split(","):
            t0 = time.time(); t = run_daily(P, name, mode=mode, costs=REG[reg], start=None, end=hs, specs=specs)   # dev+val only; holdout never simulated here
            if len(t): t.to_parquet(os.path.join(out, "trades", f"{a.universe}_{name}_{mode}_{reg}.parquet".replace("(", "_").replace(")", "").replace(",", "_").replace("=", "")), index=False)
            ent = pd.to_datetime(t.entry_ts) if len(t) else pd.Series([], dtype="datetime64[ns]"); xt = pd.to_datetime(t.exit_ts) if len(t) else ent
            dev = t[(ent < vs) & (xt <= vs)] if len(t) else t; val = t[(ent >= vs) & (ent < hs) & (xt <= hs)] if len(t) else t
            sd, sv = summarize(dev), summarize(val); w = windows(val.assign(entry_ts=pd.to_datetime(val.entry_ts).dt.tz_localize("UTC"), exit_ts=pd.to_datetime(val.exit_ts).dt.tz_localize("UTC")) if len(val) else val, [(x.tz_localize("UTC"), y.tz_localize("UTC")) for x, y in vw])
            row = {"universe": a.universe, "config": name, "mode": mode, "regime": reg, "seconds": round(time.time() - t0, 1), "dev_n": sd["n_trades"], "dev_days": sd.get("n_days"), "dev_net": sd["net_pnl"], "dev_exp": sd["expectancy_usd"], "dev_pf": sd["profit_factor"], "dev_win": sd["win_rate"],
                   "val_n": sv["n_trades"], "val_days": sv.get("n_days"), "val_net": sv["net_pnl"], "val_exp": sv["expectancy_usd"], "val_pf": sv["profit_factor"], "val_win": sv["win_rate"], "val_ci95": sv.get("exp_ci95"), "val_p_le0": sv.get("p_mean_le_0"),
                   "val_ex_best_trade": sv.get("net_ex_best_trade"), "val_ex_best_day": sv.get("net_ex_best_day"), "val_windows_pos": int((w.net > 0).sum()), "val_windows_n": len(w), "val_net_long": sv.get("net_long"), "val_net_short": sv.get("net_short"),
                   "val_cost_share": sv.get("cost_share_of_gross_profits"), "val_maxdd": sv.get("max_dd_usd"), "val_hold_days": float(val.hold_days.mean()) if len(val) else None, "val_funding": sv.get("funding")}
            if len(val): bi = by_group(val, "sym"); row["val_syms_positive"] = int((bi.net > 0).sum()); row["val_syms_n"] = int(len(bi))
            rows.append(row)
            print(f"[{a.universe}/{mode}/{reg}] {name:22s} dev n={sd['n_trades']:4d} net={sd['net_pnl']:8.2f} pf={sd['profit_factor']:5.2f} | val n={sv['n_trades']:4d} net={sv['net_pnl']:8.2f} pf={sv['profit_factor']:5.2f} win={sv['win_rate']:.2f} w={row['val_windows_pos']}/{row['val_windows_n']} syms+={row.get('val_syms_positive')}/{row.get('val_syms_n')} ({row['seconds']}s)", flush=True)
# baselines: equal-weight buy-and-hold over dev and val (no costs beyond one round trip)
bh = []
for s, d in P.items():
    c = d.set_index("date").c
    for per, (x, y) in {"dev": (pd.Timestamp(SPLITS["dev_start"]), vs), "val": (vs, hs)}.items():
        cc = c[(c.index >= x) & (c.index < y)].dropna()
        if len(cc) > 20: bh.append({"sym": s, "period": per, "ret": cc.iloc[-1] / cc.iloc[0] - 1})
bh = pd.DataFrame(bh); base = bh.groupby("period").ret.agg(["mean", "median", "count"]) * [1, 1, 1]
print("equal-weight buy-and-hold return (mean/median across names):\n", base.round(3).to_string())
df = pd.DataFrame(rows); f = os.path.join(out, f"experiments_{a.universe}.csv")
if os.path.exists(f):
    old = pd.read_csv(f); key = set(zip(df.config, df["mode"], df.regime)); old = old[[(c, m, g) not in key for c, m, g in zip(old.config, old["mode"], old.regime)]]; df = pd.concat([old, df], ignore_index=True)
df.to_csv(f, index=False); base.to_csv(os.path.join(out, f"baseline_bh_{a.universe}.csv")); print("wrote", f, len(df))
