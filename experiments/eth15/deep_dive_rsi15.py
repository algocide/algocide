#!/usr/bin/env python3
"""Descriptive deep dive on the frozen survivor R0050 (RSI(3) < 15 bet Up, > 85 bet Down): what it does, when it bet,
how half Kelly turned 1,000 into ~375,000 on the holdout, where it drew down, and what a one-minute delay does.
Nothing here changes a result; it re-reads the same data (holdout already opened, ledger item 5).

Usage: python3 experiments/eth15/deep_dive_rsi15.py
"""
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

SURV = json.load(open("results/eth15/survivors.json"))
R = next(r for r in SURV["survivors"] if r["id"] == "R0050")
P_RES = R["win_rate"]
F = lib.kelly(P_RES) / 2


def run(frame, sig, up, start=1000.0):
    bet = sig != 0
    idx = np.flatnonzero(bet)
    win = ((sig == 1) & (up == 1)) | ((sig == -1) & (up == 0))
    w = win[idx].astype(int)
    stake, bank = np.empty(len(idx)), np.empty(len(idx))
    B = start
    for k, x in enumerate(w):
        stake[k] = F * B
        B = B + stake[k] * (lib.WIN_NET if x else -lib.LOSS_NET)
        bank[k] = B
    t = pd.to_datetime(frame.t.values[idx], unit="ms")
    return pd.DataFrame({"t": t, "dir": np.where(sig[idx] == 1, "Up", "Down"), "win": w, "stake": stake, "bank": bank,
                         "rsi": frame.rsi3.values[idx], "open": frame.open.values[idx], "close": frame.close.values[idx]})


def dd(df, start=1000.0):
    b = np.r_[start, df.bank.values]
    peak = np.maximum.accumulate(b)
    d = b / peak - 1
    k = int(d.argmin())
    p = int(np.flatnonzero(b[:k + 1] == peak[k])[-1])
    rec = np.flatnonzero(b[k:] >= peak[k])
    t_of = lambda i: df.t.iloc[max(0, i - 1)]
    return {"max_dd": float(d[k]), "peak": float(peak[k]), "trough": float(b[k]), "peak_time": str(t_of(p)),
            "trough_time": str(t_of(k)), "recovered": str(t_of(k + int(rec[0]))) if len(rec) else "not recovered"}


def main():
    res = pd.read_parquet("data/eth15/research.parquet")
    hold = pd.read_parquet("data/eth15/holdout.parquet")
    c = pd.concat([res, hold], ignore_index=True)
    f = lib.features(c)
    c["rsi3"] = f.rsi3.values
    sig = lib.signal(f, "rsi", R["params"])
    up = c.up.values.astype(np.int8)
    is_hold = np.arange(len(c)) >= len(res)
    H = run(c[is_hold].reset_index(drop=True), np.where(is_hold, sig, 0)[is_hold], up[is_hold])
    I = run(c[~is_hold].reset_index(drop=True), sig[~is_hold], up[~is_hold])
    out = {"rule": "RSI(3) of 15-minute closes, as of the previous candle's close: < 15 bet Up, > 85 bet Down",
           "research_win_rate": P_RES, "research_bets": R["n"], "half_kelly_fraction": F}
    days = (H.t.iloc[-1] - pd.Timestamp("2024-10-30")).days + 1
    out["holdout"] = {"period": f"2024-10-30 to 2026-09-28 ({days} days)", "bets": len(H), "bets_per_day": len(H) / days,
                      "win_rate": H.win.mean(), "up_bets": int((H.dir == "Up").sum()), "up_win_rate": H[H.dir == "Up"].win.mean(),
                      "down_bets": int((H.dir == "Down").sum()), "down_win_rate": H[H.dir == "Down"].win.mean(),
                      "end_bankroll": float(H.bank.iloc[-1]), "first_stake": float(H.stake.iloc[0]),
                      "last_stake": float(H.stake.iloc[-1]), "largest_stake": float(H.stake.max()),
                      "longest_losing_run": int(max((sum(1 for _ in g) for k, g in __import__("itertools").groupby(H.win.tolist()) if k == 0), default=0)),
                      "drawdown": dd(H), "flat_10_dollar_profit": float(10 * (H.win * lib.WIN_NET - (1 - H.win) * lib.LOSS_NET).sum())}
    H["q"] = H.t.dt.to_period("Q")
    qs = []
    prev = 1000.0
    for q, g in H.groupby("q"):
        qs.append({"quarter": str(q), "bets": len(g), "win_rate": round(g.win.mean(), 4), "start": round(prev, 0), "end": round(g.bank.iloc[-1], 0),
                   "change": round(g.bank.iloc[-1] / prev - 1, 3)})
        prev = g.bank.iloc[-1]
    out["holdout_by_quarter"] = qs
    out["research_in_sample"] = {"period": "2021-01-01 to 2024-10-29", "bets": len(I), "win_rate": I.win.mean(),
                                 "end_bankroll": float(I.bank.iloc[-1]), "drawdown": dd(I)}
    # one minute late: reference = open of the candle's second minute
    m1 = pd.read_parquet("data/binance/ETHUSDT_1m.parquet", columns=["open_time", "open"])
    ref = pd.Series(m1.open.values, index=m1.open_time.values).reindex(c.t.values + 60_000).values
    up1 = (c.close.values >= ref).astype(np.int8)
    L = run(c[is_hold].reset_index(drop=True), sig[is_hold], up1[is_hold])
    out["one_minute_late"] = {"win_rate": L.win.mean(), "end_bankroll": float(L.bank.iloc[-1]), "drawdown": dd(L)}
    ex = H.iloc[[0, 1, 2]].copy()
    out["first_bets"] = [{"time": str(r.t), "rsi3": round(float(r.rsi), 1), "bet": r.dir, "open": r.open, "close": r.close,
                          "won": bool(r.win), "stake": round(float(r.stake), 2), "bankroll_after": round(float(r.bank), 2)} for r in ex.itertuples()]
    json.dump(out, open("results/eth15/deep_dive_rsi15.json", "w"), indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
