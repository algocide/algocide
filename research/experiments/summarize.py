#!/usr/bin/env python3
"""Merge per-universe experiment tables into EXPERIMENTS.csv, print screening leaderboards (dev+validation only) and
draw a few charts (results/figures). Screening gate columns follow docs/PROTOCOL_FREEZE.md."""
import os, sys, json, glob
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
frames = []
for u in ["stocks", "crypto15", "crypto1h", "crypto247"]:
    f = os.path.join(R, "results", u, "experiments.csv")
    if os.path.exists(f): frames.append(pd.read_csv(f))
df = pd.concat(frames, ignore_index=True)
df["trial_id"] = np.arange(1, len(df) + 1)
# gates on validation (adverse regime must also be positive)
base = df[df.regime == "base"].copy()
adv = df[df.regime == "adverse"][["universe", "config", "val_exp", "val_net", "val_pf"]].rename(columns={"val_exp": "adv_val_exp", "val_net": "adv_val_net", "val_pf": "adv_val_pf"})
base = base.merge(adv, on=["universe", "config"], how="left")
base["gate_pf"] = base.val_pf >= 1.3; base["gate_adverse"] = base.adv_val_exp > 0
base["gate_windows"] = base.val_windows_pos > base.val_windows_n / 2
base["gate_sample"] = (base.val_n >= 30)   # validation-period minimum for screening (the 100/40 rule applies to OOS in total)
base["gate_ex_best"] = base.val_ex_best_trade > 0
base["gates_passed"] = base[["gate_pf", "gate_adverse", "gate_windows", "gate_sample", "gate_ex_best"]].sum(axis=1)
df.to_csv(os.path.join(R, "EXPERIMENTS.csv"), index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 60)
for u in base.universe.unique():
    d = base[base.universe == u].sort_values(["gates_passed", "val_pf"], ascending=[False, False])
    print(f"\n===== {u} leaderboard (base regime; dev+validation only) =====")
    print(d[["family", "config", "dev_n", "dev_net", "dev_pf", "val_n", "val_net", "val_exp", "val_pf", "val_win", "val_windows_pos", "val_windows_n", "adv_val_exp", "val_ex_best_trade", "val_syms_positive", "gates_passed"]].head(12).round(2).to_string(index=False))
    print("families x mean val_net:", d.groupby("family").val_net.mean().round(2).to_dict())
base.to_csv(os.path.join(R, "results", "screening_base.csv"), index=False)
# figures: equity curves (dev+val) of the top-3 by gates for stocks and crypto15
os.makedirs(os.path.join(R, "results", "figures"), exist_ok=True)
for u in ["stocks", "crypto15", "crypto1h"]:
    d = base[base.universe == u].sort_values(["gates_passed", "val_pf"], ascending=[False, False]).head(3)
    if not len(d): continue
    sp = json.load(open(os.path.join(R, "results", u, "splits.json"))); hs = pd.Timestamp(sp["hold_start"]); vs = pd.Timestamp(sp["val_start"])
    fig, ax = plt.subplots(figsize=(10, 5))
    for _, r in d.iterrows():
        if not isinstance(r.trades_file, str): continue
        t = pd.read_parquet(os.path.join(R, "results", u, "trades", r.trades_file)); t["exit_ts"] = pd.to_datetime(t.exit_ts, utc=True)
        t = t[t.exit_ts < hs].sort_values("exit_ts")
        ax.plot(t.exit_ts, t.net_pnl.cumsum(), label=r.config[:60])
    ax.axvline(vs, color="k", ls="--", lw=0.8); ax.text(vs, ax.get_ylim()[1] * 0.9 if ax.get_ylim()[1] > 0 else 0, " validation →", fontsize=8)
    ax.axhline(0, color="grey", lw=0.5); ax.set_title(f"{u}: cumulative net P&L ($) of top-3 screened configs, development + validation (holdout hidden)"); ax.legend(fontsize=7); ax.set_ylabel("$ on a $100 account")
    fig.tight_layout(); fig.savefig(os.path.join(R, "results", "figures", f"{u}_top3_devval.png"), dpi=120); plt.close(fig)
# family heatmap: val_net by family and universe
piv = base.pivot_table(index="family", columns="universe", values="val_net", aggfunc="mean")
fig, ax = plt.subplots(figsize=(7, 4)); im = ax.imshow(piv.values, cmap="RdYlGn", vmin=-10, vmax=10); ax.set_xticks(range(len(piv.columns))); ax.set_xticklabels(piv.columns); ax.set_yticks(range(len(piv.index))); ax.set_yticklabels(piv.index)
for i in range(len(piv.index)):
    for j in range(len(piv.columns)):
        v = piv.values[i, j]; ax.text(j, i, "" if np.isnan(v) else f"{v:.1f}", ha="center", va="center", fontsize=8)
ax.set_title("Mean validation net P&L ($) by family and universe (base costs)"); fig.colorbar(im); fig.tight_layout(); fig.savefig(os.path.join(R, "results", "figures", "family_universe_heatmap.png"), dpi=120)
print("\nwrote EXPERIMENTS.csv with", len(df), "rows (trials), figures in results/figures")
