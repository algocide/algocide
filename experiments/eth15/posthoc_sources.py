#!/usr/bin/env python3
"""POST-HOC (docs/eth15/ledger.md item 6): do the frozen rule survivors hold on other price sources over the holdout
window? For Binance spot ETHUSDT and the USD-M index price (a multi-exchange spot composite): direction agreement
with the perpetual, and each rule's win rate (a) signal and outcome on that source, (b) signal from the perpetual,
outcome settled on that source. Models are not checked (they also use perpetual volume, taker flow and BTC inputs).

Usage: python3 experiments/eth15/posthoc_sources.py
"""
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402


def with_dummies(c):
    c = c.copy()
    for col, v in (("volume", 1.0), ("taker_buy", 0.5), ("btc_open", 1.0), ("btc_close", 1.0), ("ret_last1", 0.0),
                   ("ret_last5", 0.0), ("funding", np.nan)):
        if col not in c:
            c[col] = v
    return c


def main():
    split = json.load(open("results/eth15/split.json"))
    h0 = int(pd.Timestamp(split["holdout_from"]).value // 10**6)
    perp = pd.concat([pd.read_parquet("data/eth15/research.parquet"), pd.read_parquet("data/eth15/holdout.parquet")],
                     ignore_index=True)
    fp = lib.features(perp)
    perp_sig_frame = pd.DataFrame({"t": perp.t.values})
    surv = [r for r in json.load(open("results/eth15/survivors.json"))["survivors"] if r["kind"] == "rule"]
    for r in surv:
        perp_sig_frame[r["id"]] = lib.signal(fp, r["family"], r["params"])
    perp_sig_frame["perp_up"] = perp.up.values
    out = {"note": "POST-HOC; holdout window only; rules only", "sources": {}}
    for src in ("spot", "index"):
        c = with_dummies(pd.read_parquet(f"data/eth15/{src}_15m.parquet"))
        f = lib.features(c)
        m = pd.DataFrame({"t": c.t.values, "up": c.up.values}).merge(perp_sig_frame, on="t", how="inner")
        own = pd.DataFrame({"t": c.t.values})
        for r in surv:
            own[r["id"]] = lib.signal(f, r["family"], r["params"])
        m = m.merge(own, on="t", how="left", suffixes=("", "_own"))
        m = m[m.t >= h0].reset_index(drop=True)
        agree = float((m.up == m.perp_up).mean())
        res = {"n_candles": len(m), "direction_agreement_with_perp": agree, "up_share": float(m.up.mean()), "rules": []}
        up = m.up.values.astype(np.int8)
        for r in surv:
            n_own, w_own = lib.score(m[r["id"] + "_own"].values.astype(np.int8), up)
            n_x, w_x = lib.score(m[r["id"]].values.astype(np.int8), up)
            n_p, w_p = lib.score(m[r["id"]].values.astype(np.int8), m.perp_up.values.astype(np.int8))
            res["rules"].append({"id": r["id"], "family": r["family"], "params": r["params"],
                                 "own_n": n_own, "own_win_rate": w_own / n_own if n_own else None,
                                 "perp_signal_settled_here_n": n_x, "perp_signal_settled_here_win_rate": w_x / n_x if n_x else None,
                                 "perp_on_same_candles_win_rate": w_p / n_p if n_p else None})
        out["sources"][src] = res
    json.dump(out, open("results/eth15/posthoc_sources.json", "w"), indent=1)
    for src, res in out["sources"].items():
        print(f"{src}: {res['n_candles']} holdout candles, direction agrees with perp {res['direction_agreement_with_perp']:.4f}")
        fmt = lambda v: "  n/a " if v is None else f"{v:.4f}"
        for x in res["rules"]:
            print(f"   {x['id']} {x['family']:7s} {str(x['params']):40s} own {fmt(x['own_win_rate'])} (n={x['own_n']})  "
                  f"perp signal settled on {src} {fmt(x['perp_signal_settled_here_win_rate'])}  "
                  f"perp itself {fmt(x['perp_on_same_candles_win_rate'])}")


if __name__ == "__main__":
    main()
