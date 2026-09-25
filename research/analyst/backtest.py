"""'Backtesting: checking whether any of this means anything.' The article's event study: forward returns after
anomaly_score > 2 events versus quiet sessions, the whole distribution (not just the mean), precision/recall of the
strong flag against the 5%-in-20-sessions label and the base rate. Splits are phase 3's; the holdout is not touched."""
from __future__ import annotations
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from hlr2.daily_data import SPLITS   # dev 2024-09-23, val 2025-09-23, hold 2026-03-23 (not evaluated here)

SPLIT_RANGES = {"dev": (SPLITS["dev_start"], SPLITS["val_start"]), "val": (SPLITS["val_start"], SPLITS["hold_start"])}


def group_stats(x: pd.Series, label_pct: float = 0.05) -> dict:
    x = pd.Series(x).dropna().astype(float)
    if not len(x): return {"n": 0}
    return {"n": int(len(x)), "mean": float(x.mean()), "median": float(x.median()), "stdev": float(x.std(ddof=1)) if len(x) > 1 else None,
            "share_losers": float((x < 0).mean()), f"share_ge_{int(label_pct*100)}pct": float((x >= label_pct).mean()), "p10": float(x.quantile(0.1)), "p90": float(x.quantile(0.9)),
            "max": float(x.max()), "min": float(x.min())}


def clustered_bootstrap_diff(a: pd.DataFrame, b: pd.DataFrame, col: str, n_boot: int = 1000, seed: int = 0) -> dict:
    """Difference of means, A minus B, resampling session dates (clusters) with replacement within each group."""
    a = a.dropna(subset=[col]); b = b.dropna(subset=[col])
    if not len(a) or not len(b): return {"diff": None}
    rng = np.random.default_rng(seed); ga = {d: g[col].values for d, g in a.groupby("date")}; gb = {d: g[col].values for d, g in b.groupby("date")}
    ka, kb = list(ga), list(gb); diffs = []
    for _ in range(n_boot):
        sa = np.concatenate([ga[k] for k in rng.choice(ka, len(ka), replace=True)]); sb = np.concatenate([gb[k] for k in rng.choice(kb, len(kb), replace=True)])
        diffs.append(sa.mean() - sb.mean())
    d = np.array(diffs)
    return {"diff": float(a[col].mean() - b[col].mean()), "ci_lo": float(np.percentile(d, 2.5)), "ci_hi": float(np.percentile(d, 97.5)), "n_boot": n_boot, "clusters_a": len(ka), "clusters_b": len(kb)}


def split_frame(features: pd.DataFrame, split: str) -> pd.DataFrame:
    s, e = SPLIT_RANGES[split]; return features[(features.date >= pd.Timestamp(s)) & (features.date < pd.Timestamp(e))]


def event_study(features: pd.DataFrame, sig: dict, split: str, first_trigger_only: bool = True) -> tuple[pd.DataFrame, dict]:
    f = split_frame(features, split); ev = f[f.first_trigger] if first_trigger_only else f[f.event]
    groups = {"A_positive": ev[ev.direction > 0], "A_negative": ev[ev.direction < 0], "A_all": ev, "B_quiet": f[f.quiet]}
    rows, diffs = [], {}
    for k in sig["horizons"]:
        for name, g in groups.items():
            st = group_stats(g[f"fwd_{k}"], sig["label_pct"]); st.update({"split": split, "horizon": k, "group": name}); rows.append(st)
        diffs[f"A_positive_minus_B_{k}"] = clustered_bootstrap_diff(groups["A_positive"], groups["B_quiet"], f"fwd_{k}")
        diffs[f"A_negative_minus_B_{k}"] = clustered_bootstrap_diff(groups["A_negative"], groups["B_quiet"], f"fwd_{k}")
    return pd.DataFrame(rows), diffs


def precision_recall(features: pd.DataFrame, split: str, first_trigger_only: bool = True) -> dict:
    f = split_frame(features, split).dropna(subset=["label"]); y = f.label.astype(float).values
    strong = ((f.first_trigger if first_trigger_only else f.event) & (f.direction > 0)).values
    tp = float((strong & (y == 1)).sum()); fp = float((strong & (y == 0)).sum()); fn = float((~strong & (y == 1)).sum())
    base = float(y.mean()); prec = tp / (tp + fp) if tp + fp else None; rec = tp / (tp + fn) if tp + fn else None
    return {"split": split, "n_sessions": int(len(f)), "n_strong": int(strong.sum()), "true_positives": int(tp), "false_positives": int(fp), "false_negatives": int(fn),
            "precision": prec, "recall": rec, "base_rate": base, "lift": (prec / base) if (prec is not None and base > 0) else None}


def run_backtest(features: pd.DataFrame, cfg: dict, out_dir: str) -> dict:
    from .composite import event_table, fit_composite, predict, evaluate
    sig = cfg["signals"]; os.makedirs(out_dir, exist_ok=True); res = {"protocol": "docs/PROTOCOL_PHASE4.md", "splits": SPLIT_RANGES, "event_study": {}, "precision_recall": {}, "composite": {}}
    tables = []
    for split in ("dev", "val"):
        for ft in (True, False):
            t, d = event_study(features, sig, split, first_trigger_only=ft); t["first_trigger_only"] = ft; tables.append(t)
            res["event_study"][f"{split}_{'first_trigger' if ft else 'all_days'}"] = d
        res["precision_recall"][split] = precision_recall(features, split); res["precision_recall"][f"{split}_all_days"] = precision_recall(features, split, first_trigger_only=False)
    pd.concat(tables, ignore_index=True).to_csv(os.path.join(out_dir, "event_study.csv"), index=False)
    ev = event_table(features); dev, val = split_frame(ev, "dev"), split_frame(ev, "val")
    model = fit_composite(dev); res["composite"]["fit_dev"] = model
    res["composite"]["eval_dev_in_sample"] = evaluate(predict(model, dev), dev.label.values)
    res["composite"]["eval_val"] = evaluate(predict(model, val), val.label.values)
    m2 = fit_composite(dev, candidate_features=("price_anomaly", "volume_anomaly"))      # secondary: price/volume only (no momentum coverage loss)
    res["composite"]["secondary_price_volume_only"] = {"fit_dev": m2, "eval_val": evaluate(predict(m2, val), val.label.values)}
    ev.to_csv(os.path.join(out_dir, "events_first_trigger.csv"), index=False)
    json.dump(res, open(os.path.join(out_dir, "backtest.json"), "w"), indent=1, default=str)
    return res
