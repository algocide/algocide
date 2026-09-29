#!/usr/bin/env python3
"""Compare the pre-registered ranking (TradingView OHLC-path fills) with the magnified one (1-minute fills).

POST-HOC by construction (docs/research_ledger.md item 35). Writes <out>/comparison.json and
<out>/plain_top50_magnified.csv.
Usage: python3 experiments/pine/compare_rankings.py --plain results/pine --mag results/pine_mag --out results/pine_mag
"""
import argparse, json, os
import numpy as np
import pandas as pd
from scipy import stats as sps


def kept_order(rank: pd.DataFrame) -> pd.DataFrame:
    """Eligible scripts in score order with duplicates removed (the order the top 10 is read from)."""
    return rank[rank.duplicate_of.isna()].reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plain", default="results/pine")
    ap.add_argument("--mag", default="results/pine_mag")
    ap.add_argument("--out", default="results/pine_mag")
    a = ap.parse_args()
    rp = pd.read_csv(os.path.join(a.plain, "ranking.csv"))
    rm = pd.read_csv(os.path.join(a.mag, "ranking.csv"))
    ep = pd.read_csv(os.path.join(a.plain, "eligibility.csv")).set_index("file")
    em = pd.read_csv(os.path.join(a.mag, "eligibility.csv")).set_index("file")
    rp["plain_rank"] = np.arange(1, len(rp) + 1)
    rm["mag_rank"] = np.arange(1, len(rm) + 1)
    kp, km = kept_order(rp), kept_order(rm)

    # where the pre-registered top 50 land with 1-minute fills
    top = kp.head(50)[["file", "plain_rank", "score", "oos_sharpe_btc", "oos_sharpe_eth"]].copy()
    top = top.rename(columns={"score": "plain_score", "oos_sharpe_btc": "plain_btc", "oos_sharpe_eth": "plain_eth"})
    top["mag_score"] = top.file.map(em.score)
    top["mag_eligible"] = top.file.map(em.eligible)
    top["mag_reasons"] = top.file.map(em.reasons)
    top["mag_rank"] = top.file.map(rm.set_index("file").mag_rank)
    top.to_csv(os.path.join(a.out, "plain_top50_magnified.csv"), index=False)

    # where the magnified top 10 sat in the pre-registered ranking
    mt = km.head(10)[["file", "mag_rank", "score"]].rename(columns={"score": "mag_score"})
    mt["plain_score"] = mt.file.map(ep.score)
    mt["plain_rank"] = mt.file.map(rp.set_index("file").plain_rank)
    mt["plain_kept_position"] = mt.file.map({f: k + 1 for k, f in enumerate(kp.file)})

    both = ep[ep.eligible].index.intersection(em[em.eligible].index)
    s_p, s_m = ep.loc[both, "score"], em.loc[both, "score"]
    sp = sps.spearmanr(s_p, s_m)
    out = {
        "n_eligible_plain": int(ep.eligible.sum()), "n_eligible_mag": int(em.eligible.sum()),
        "n_eligible_both": int(len(both)),
        "spearman_plain_vs_mag_score": float(sp.correlation),
        "median_score_change": float((s_m - s_p).median()),
        "plain_top10_mean_score": float(top.head(10).plain_score.mean()),
        "plain_top10_mean_mag_score": float(top.head(10).mag_score.mean()),
        "plain_top10_still_eligible": int(top.head(10).mag_eligible.fillna(False).sum()),
        "plain_top10_positive_mag_score": int((top.head(10).mag_score > 0).sum()),
        "plain_top50_positive_mag_score": int((top.mag_score > 0).sum()),
        "mag_top10": mt.to_dict(orient="records"),
        "plain_top10": top.head(10).to_dict(orient="records"),
    }
    json.dump(out, open(os.path.join(a.out, "comparison.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if not isinstance(v, list)}, indent=1))
    print(mt.to_string())
    print(top.head(12).to_string())


if __name__ == "__main__":
    main()
