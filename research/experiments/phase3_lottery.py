#!/usr/bin/env python3
"""Video 2 ("15 AI bots, $1,000 each, 3 rounds; 11 lost, the winner made $175 on a 40x BTC long"): what does a
population of leveraged bets look like WITHOUT any edge? Monte Carlo on real BTC daily returns (2024-09 → 2026-09):
each bot places one directional bet per round (random side), holds `hold_days`, at leverage L, with liquidation when
the adverse move exceeds 1/L (isolated margin, no maintenance buffer), taker fees 4.5 bp per side, funding ignored.
Reports the distribution of the best-of-15 and the share of bots that lose, for L in {1, 5, 10, 40}."""
import os, sys, json
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.daily_data import build
_, cpanel, _, _ = build(); btc = cpanel["BTC"].dropna(subset=["c"]).reset_index(drop=True)
c = btc.c.values; h = btc.h.values; l = btc.l.values; n = len(c); rng = np.random.default_rng(42)
def bet(i, side, L, hold, stake=1000.0, fee=0.00045):
    """Return P&L of a bet opened at close i, closed at close i+hold or liquidated (path checked on daily extremes)."""
    entry = c[i]; notional = stake * L
    for k in range(1, hold + 1):
        if i + k >= n: break
        adverse = (entry - l[i + k]) / entry if side == 1 else (h[i + k] - entry) / entry
        if adverse >= 1.0 / L: return -stake                              # liquidated: lose the margin
    j = min(i + hold, n - 1); ret = side * (c[j] / entry - 1)
    return notional * ret - 2 * fee * notional
rows = []
for L in [1, 5, 10, 40]:
    for hold in [1, 3]:
        best, med, share_lose, winners_pnl = [], [], [], []
        for sim in range(2000):
            pnl = np.zeros(15)
            for b in range(15):
                for rnd in range(3):
                    i = rng.integers(200, n - hold - 1); pnl[b] += bet(i, rng.choice([-1, 1]), L, hold)
            best.append(pnl.max()); med.append(np.median(pnl)); share_lose.append((pnl < 0).mean())
        rows.append({"leverage": L, "hold_days": hold, "best_of_15_median": float(np.median(best)), "best_of_15_p90": float(np.percentile(best, 90)), "median_bot": float(np.median(med)), "share_bots_losing": float(np.mean(share_lose)),
                     "p_best_ge_175": float(np.mean(np.array(best) >= 175))})
        print(rows[-1], flush=True)
df = pd.DataFrame(rows); df.to_csv(os.path.join(R, "results", "phase3", "lottery_video2.csv"), index=False); print(df.round(2).to_string(index=False))
