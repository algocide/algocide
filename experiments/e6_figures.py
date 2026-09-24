#!/usr/bin/env python3
"""Report figures from saved results (no new computations beyond plotting)."""
import os, json
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); OUT = os.path.join(ROOT, "results", "figures"); os.makedirs(OUT, exist_ok=True)
# H7 reversion by horizon (liquid, external, persisting) from e4b json
r = json.load(open(os.path.join(ROOT, "results", "e4", "e4b_h7_liquid.json")))["tradable"]
fig, ax = plt.subplots(figsize=(7, 4))
for key, lab in [("top15_liquid|extreme persists at t+1|external", "top-15 liquid, external, persists (n=%d)"), ("all|extreme persists at t+1|external", "all names, external, persists (n=%d)"), ("top15_liquid|extreme persists at t+1|weekend", "top-15 liquid, weekend, persists (n=%d)")]:
    v = r[key]; hs = [1, 3, 6]; m = [v[f"h{h}"][0] for h in hs]; lo = [v[f"h{h}"][1] for h in hs]; hi = [v[f"h{h}"][2] for h in hs]
    ax.errorbar(hs, m, yerr=[np.array(m) - np.array(lo), np.array(hi) - np.array(m)], marker="o", capsize=3, label=lab % v["n"])
ax.axhline(24, ls="--", c="grey", lw=0.8); ax.text(1.05, 24.5, "≈ round-trip cost, standard fees + hedge", fontsize=8, color="grey")
ax.axhline(6, ls=":", c="grey", lw=0.8); ax.text(1.05, 6.5, "≈ round-trip cost, growth-mode fees + hedge", fontsize=8, color="grey")
ax.set_xlabel("hours after entry (entry one hour after the extreme hour)"); ax.set_ylabel("premium reversion toward zero (bps)"); ax.set_title("H7: premium-extreme reversion on xyz perps (95% block-bootstrap CI)"); ax.legend(fontsize=8)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "h7_reversion.png"), dpi=120); plt.close(fig)
# H4 cumulative net (K=5 standard vs growth) from weekly csv (K=10 saved) -> recompute quickly via e4 for K=5
import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, "src"))
import e4_funding_carry as e4
fu, st, meta = e4.load()
fig, ax = plt.subplots(figsize=(7, 4))
for fee, lab in [("hip3_standard", "K=5, standard HIP-3 fees"), ("hip3_growth", "K=5, growth-mode fees")]:
    df = e4.h4_equity_harvest(fu, meta, 5, fee); ax.plot(df["week"], df["ret"].cumsum() * 100, label=lab)
df = e4.h4_equity_harvest(fu, meta, None, "hip3_standard"); ax.plot(df["week"], df["ret"].cumsum() * 100, label="all positive-funding names, standard fees", ls="--")
ax.set_ylabel("cumulative net return on capital (%)"); ax.set_title("H4: equity-perp funding harvest (weekly, hedged with stock)"); ax.legend(fontsize=8); ax.axvline(pd.Timestamp("2026-06-01", tz="UTC"), c="k", lw=0.6); ax.text(pd.Timestamp("2026-06-03", tz="UTC"), 0.2, "validation →", fontsize=8)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "h4_cumulative.png"), dpi=120); plt.close(fig)
# H5 rolling 30-day funding APR BTC/ETH/HYPE
fu2 = e4.per_hour_rate(fu)
fig, ax = plt.subplots(figsize=(8, 4))
for coin in ["BTC", "ETH", "HYPE"]:
    x = fu2[(fu2["dex"] == "main") & (fu2["coin"] == coin)].set_index("ts")["rate_ph"].rolling(24 * 30).mean() * 24 * 365 * 100
    ax.plot(x.index, x.values, lw=0.8, label=coin)
ax.axhline(4.03, c="grey", ls="--", lw=0.8); ax.text(fu2["ts"].min(), 4.5, "13-week T-bill 4.03%", fontsize=8, color="grey")
ax.axhline(10.95, c="grey", ls=":", lw=0.8); ax.text(fu2["ts"].min(), 11.4, "interest-rate floor 0.01%/8h ≈ 10.95%", fontsize=8, color="grey")
ax.set_ylim(-20, 80); ax.set_ylabel("30-day rolling funding paid to shorts, APR %"); ax.set_title("H5: Hyperliquid native funding carry (gross)"); ax.legend()
fig.tight_layout(); fig.savefig(os.path.join(OUT, "h5_funding_apr.png"), dpi=120); plt.close(fig)
print("figures written:", os.listdir(OUT))
