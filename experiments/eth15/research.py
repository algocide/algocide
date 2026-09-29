#!/usr/bin/env python3
"""Research stage (docs/eth15/plan.md): evaluate every candidate on the research set only, log each one, apply the
pre-registered survivor rule and write results/eth15/survivors.json. Never reads the holdout.

Usage: python3 experiments/eth15/research.py
"""
import json, os, pickle, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

OUT = "results/eth15"


def rule_row(fam, params, sig, up, h1):
    n, w = lib.score(sig, up)
    n1, w1 = lib.score(sig, up, h1)
    n2, w2 = lib.score(sig, up, ~h1)
    return {"family": fam, "params": params, "n": n, "wins": w, "win_rate": w / n if n else None,
            "n_h1": n1, "wr_h1": w1 / n1 if n1 else None, "n_h2": n2, "wr_h2": w2 / n2 if n2 else None}


def survives(r):
    return (r["n"] >= lib.MIN_BETS and r["win_rate"] is not None and r["win_rate"] > lib.BAR
            and (r["wr_h1"] or 0) > lib.BREAK_EVEN and (r["wr_h2"] or 0) > lib.BREAK_EVEN)


def main():
    t0 = time.time()
    os.makedirs(os.path.join(OUT, "models"), exist_ok=True)
    c = pd.read_parquet("data/eth15/research.parquet")
    f = lib.features(c)
    up = c.up.values.astype(np.int8)
    n = len(c)
    h1 = np.arange(n) < n // 2
    rows = []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ---- rule families on the whole research set
    for k, (fam, params) in enumerate(lib.candidates()):
        r = rule_row(fam, params, lib.signal(f, fam, params), up, h1)
        r.update(id=f"R{k:04d}", kind="rule", sample="research (all)", time=stamp)
        rows.append(r)

    # ---- models: fit on the first 70%, thresholds scored on the last 30% (its two halves for the stability rule)
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer
    X = f[lib.ML_FEATURES].astype(float)
    ok = X.notna().sum(axis=1) >= len(lib.ML_FEATURES) - 2
    cut70, cut60 = int(n * 0.7), int(n * 0.6)
    tr = ok.values & (np.arange(n) < cut70)
    te = ok.values & (np.arange(n) >= cut70)
    te_h1 = np.arange(n) < cut70 + (n - cut70) // 2
    models = {}
    logit = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.1, max_iter=500))
    logit.fit(X[tr], up[tr])
    models["logit"] = logit
    tr_in = ok.values & (np.arange(n) < cut60)
    va_in = ok.values & (np.arange(n) >= cut60) & (np.arange(n) < cut70)
    gbm = lgb.LGBMClassifier(n_estimators=2000, learning_rate=0.02, num_leaves=15, min_child_samples=300,
                             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
                             random_state=0, verbose=-1)
    gbm.fit(X[tr_in], up[tr_in], eval_set=[(X[va_in], up[va_in])],
            callbacks=[lgb.early_stopping(100, verbose=False)])
    models["lgbm"] = gbm
    ml_info = {}
    for name, m in models.items():
        p = np.full(n, 0.5)
        p[ok.values] = m.predict_proba(X[ok])[:, 1]
        auc_te = float(pd.Series(p[te]).corr(pd.Series(up[te].astype(float)), method="spearman"))
        ml_info[name] = {"test_rank_corr": auc_te, "n_train": int(tr.sum()), "n_test": int(te.sum()),
                         "best_iteration": int(getattr(m, "best_iteration_", 0) or 0)}
        pickle.dump(m, open(os.path.join(OUT, "models", f"{name}.pkl"), "wb"))
        for thr in np.round(np.arange(0.51, 0.625, 0.01), 2):
            sig = np.where(p > thr, 1, np.where(p < 1 - thr, -1, 0)).astype(np.int8)
            sig[~te] = 0
            nn, w = lib.score(sig, up)
            n1, w1 = lib.score(sig, up, te_h1)
            n2, w2 = lib.score(sig, up, ~te_h1)
            rows.append({"id": f"M-{name}-{thr:.2f}", "kind": "model", "family": name, "params": {"threshold": float(thr)},
                         "sample": "research, last 30% (model fitted on the first 70%)", "time": stamp,
                         "n": nn, "wins": w, "win_rate": w / nn if nn else None, "n_h1": n1,
                         "wr_h1": w1 / n1 if n1 else None, "n_h2": n2, "wr_h2": w2 / n2 if n2 else None})

    for r in rows:
        r["survivor"] = bool(survives(r))
        r["research_kelly_half"] = lib.kelly(r["win_rate"]) / 2 if r["win_rate"] else 0.0
    with open(os.path.join(OUT, "research_log.jsonl"), "w") as fo:
        for r in rows:
            fo.write(json.dumps(r) + "\n")
    surv = [r for r in rows if r["survivor"]]
    json.dump({"frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rule": "n >= 200, win rate > 57%, "
               "both halves above break-even 55.59% (models: on the last 30% of the research set)",
               "n_candidates": len(rows), "survivors": surv, "models": ml_info},
              open(os.path.join(OUT, "survivors.json"), "w"), indent=1)
    D = pd.DataFrame(rows)
    big = D[D.n >= lib.MIN_BETS]
    print(f"{len(rows)} candidates in {time.time()-t0:.0f}s; with >= {lib.MIN_BETS} bets: {len(big)}")
    print("win rate of candidates with >= 200 bets: max %.4f, 99th pct %.4f, median %.4f" %
          (big.win_rate.max(), big.win_rate.quantile(0.99), big.win_rate.median()))
    print("above break-even:", int((big.win_rate > lib.BREAK_EVEN).sum()), " above 57%:", int((big.win_rate > lib.BAR).sum()))
    print("models:", json.dumps(ml_info))
    cols = ["id", "family", "params", "n", "win_rate", "wr_h1", "wr_h2", "survivor"]
    print(big.sort_values("win_rate", ascending=False)[cols].head(25).to_string(max_colwidth=60))
    print(f"SURVIVORS: {len(surv)}")
    for r in surv:
        print("  ", r["id"], r["family"], r["params"], r["n"], round(r["win_rate"], 4))


if __name__ == "__main__":
    main()
