#!/usr/bin/env python3
"""Holdout validation (docs/eth15/plan.md). Refuses to run unless results/eth15/survivors.json is committed and
unchanged and the holdout file matches its recorded SHA-256. Runs every frozen survivor unchanged on the holdout
candles, sizes bets at half Kelly from the survivor's research win rate, and writes results/eth15/holdout_results.json
and results/eth15/bets.json (per-bet outcomes for the control panel).

Usage: python3 experiments/eth15/validate.py
"""
import hashlib, json, os, pickle, subprocess, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

RES = "results/eth15"
SURV = os.path.join(RES, "survivors.json")


def guard():
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", SURV], capture_output=True).returncode == 0
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", SURV]).returncode == 0
    if not (tracked and clean):
        sys.exit("survivors.json must be committed and unchanged before the holdout is opened")
    split = json.load(open(os.path.join(RES, "split.json")))
    sha = hashlib.sha256(open("data/eth15/holdout.parquet", "rb").read()).hexdigest()
    if sha != split["holdout_sha256"]:
        sys.exit("holdout file does not match its recorded hash")
    commit = subprocess.run(["git", "log", "-1", "--format=%H %cI", "--", SURV], capture_output=True, text=True).stdout.strip()
    return split, commit


def bankroll(wins: np.ndarray, frac: float, start: float = 1000.0) -> np.ndarray:
    step = np.where(wins == 1, 1 + frac * lib.WIN_NET, 1 - frac * lib.LOSS_NET)
    return start * np.cumprod(step)


def main():
    split, commit = guard()
    s = json.load(open(SURV))
    res = pd.read_parquet("data/eth15/research.parquet")
    hold = pd.read_parquet("data/eth15/holdout.parquet")
    c = pd.concat([res, hold], ignore_index=True)
    f = lib.features(c)
    up = c.up.values.astype(np.int8)
    n_res = len(res)
    is_hold = np.arange(len(c)) >= n_res
    X = f[lib.ML_FEATURES].astype(float)
    ok = (X.notna().sum(axis=1) >= len(lib.ML_FEATURES) - 2).values
    probs = {}
    out, bets = [], {}
    t0 = int(c.t.iloc[0])
    for r in s["survivors"]:
        if r["kind"] == "model":
            name = r["family"]
            if name not in probs:
                m = pickle.load(open(os.path.join(RES, "models", f"{name}.pkl"), "rb"))
                p = np.full(len(c), 0.5)
                p[ok] = m.predict_proba(X[ok])[:, 1]
                probs[name] = p
            thr = r["params"]["threshold"]
            p = probs[name]
            sig = np.where(p > thr, 1, np.where(p < 1 - thr, -1, 0)).astype(np.int8)
            research_mask = (~is_hold) & (np.arange(len(c)) >= int(n_res * 0.7))     # the slice it was scored on
        else:
            sig = lib.signal(f, r["family"], r["params"])
            research_mask = ~is_hold
        n, w = lib.score(sig, up, is_hold)
        wr = w / n if n else float("nan")
        lo, hi = lib.wilson(w, n)
        p_res = r["win_rate"]
        frac = lib.kelly(p_res) / 2
        hb = is_hold & (sig != 0)
        won = (((sig == 1) & (up == 1)) | ((sig == -1) & (up == 0)))[hb].astype(np.int8)
        path = bankroll(won, frac)
        peak = np.maximum.accumulate(np.r_[1000.0, path])
        mdd = float((np.r_[1000.0, path] / peak - 1).min())
        # half-year stability inside the holdout
        mid = n_res + (len(c) - n_res) // 2
        n1, w1 = lib.score(sig, up, is_hold & (np.arange(len(c)) < mid))
        n2, w2 = lib.score(sig, up, is_hold & (np.arange(len(c)) >= mid))
        rb = research_mask & (sig != 0)
        rwon = (((sig == 1) & (up == 1)) | ((sig == -1) & (up == 0)))[rb].astype(np.int8)
        row = {"id": r["id"], "kind": r["kind"], "family": r["family"], "params": r["params"],
               "research_n": r["n"], "research_win_rate": p_res, "half_kelly_fraction": frac,
               "holdout_n": n, "holdout_wins": w, "holdout_win_rate": wr, "wilson95": [lo, hi],
               "p_value_vs_break_even": lib.binom_p_above(w, n), "passes_57": bool(n > 0 and wr > lib.BAR),
               "above_break_even": bool(n > 0 and wr > lib.BREAK_EVEN),
               "holdout_h1": [n1, w1 / n1 if n1 else None], "holdout_h2": [n2, w2 / n2 if n2 else None],
               "ev_per_unit_stake": (wr * lib.WIN_NET - (1 - wr) * lib.LOSS_NET) if n else None,
               "bankroll_end": float(path[-1]) if n else 1000.0, "max_drawdown": mdd,
               "up_share_of_bets": float((sig[hb] == 1).mean()) if n else None}
        out.append(row)
        bets[r["id"]] = {"hold_idx": (np.flatnonzero(hb) - n_res).tolist(), "hold_win": "".join(map(str, won.tolist())),
                         "hold_dir": "".join("u" if x == 1 else "d" for x in sig[hb]),
                         "res_idx": np.flatnonzero(rb).tolist(), "res_win": "".join(map(str, rwon.tolist()))}
    meta = {"validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "survivors_commit": commit,
            "holdout_sha256": split["holdout_sha256"], "holdout_from": split["holdout_from"],
            "holdout_to": split["holdout_to"], "n_holdout_candles": int(is_hold.sum()),
            "holdout_up_share": float(up[is_hold].mean()), "break_even": lib.BREAK_EVEN, "bar": lib.BAR,
            "payout": lib.PAYOUT, "fee": lib.FEE, "t0_ms": t0, "n_research_candles": n_res,
            "research_from": split["research_from"]}
    json.dump({"meta": meta, "results": out}, open(os.path.join(RES, "holdout_results.json"), "w"), indent=1)
    json.dump({"meta": meta, "bets": bets}, open(os.path.join(RES, "bets.json"), "w"))
    D = pd.DataFrame(out)
    pd.set_option("display.width", 220)
    print(json.dumps(meta, indent=1))
    print(D[["id", "family", "params", "research_win_rate", "holdout_n", "holdout_win_rate", "wilson95",
             "p_value_vs_break_even", "passes_57", "half_kelly_fraction", "bankroll_end", "max_drawdown"]]
          .to_string(max_colwidth=40))
    print("passed 57%:", int(D.passes_57.sum()), "of", len(D), "; above break-even:", int(D.above_break_even.sum()))


if __name__ == "__main__":
    main()
