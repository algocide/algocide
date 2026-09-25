"""The article's composite: P(rise) = 1 / (1 + e^-(b0 + b1*retail + b2*institutional + b3*fundamental + b4*momentum + b5*options)).
The five-term formula is kept as written; terms with no data source are fixed at 0 and reported as unavailable. The fit
is a plain logistic regression (statsmodels) on first-trigger events; coefficients come with standard errors and p-values
exactly so that the illustrative table in the article can be replaced by a real one."""
from __future__ import annotations
import numpy as np, pandas as pd

ARTICLE_TERMS = ["retail", "institutional", "fundamental", "momentum", "options"]


def probability(b: dict, retail=0.0, institutional=0.0, fundamental=0.0, momentum=0.0, options=0.0) -> float:
    z = b.get("b0", 0.0) + b.get("retail", 0.0) * retail + b.get("institutional", 0.0) * institutional + b.get("fundamental", 0.0) * fundamental \
        + b.get("momentum", 0.0) * momentum + b.get("options", 0.0) * options
    return float(1.0 / (1.0 + np.exp(-z)))


def event_table(features: pd.DataFrame, inst_flow: pd.DataFrame | None = None, fundamental: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per first-trigger event with the composite's inputs. momentum = z_momentum; institutional = latest
    institutional_flow whose filing date is <= event date (else NaN); fundamental/options/retail NaN unless supplied."""
    ev = features[features.first_trigger].copy()
    ev["momentum"] = ev["z_momentum"]; ev["price_anomaly"] = ev["z_return"]; ev["volume_anomaly"] = ev["z_volume"]
    ev["institutional"] = np.nan; ev["fundamental"] = np.nan; ev["options"] = np.nan; ev["retail"] = np.nan
    if inst_flow is not None and len(inst_flow):
        for i, r in ev.iterrows():
            h = inst_flow[(inst_flow.symbol == r.symbol) & (pd.to_datetime(inst_flow.filing_date) <= r.date)]
            if len(h): ev.at[i, "institutional"] = float(h.sort_values("period_of_report").iloc[-1].institutional_flow)
    if fundamental is not None and len(fundamental):
        for i, r in ev.iterrows():
            h = fundamental[(fundamental.symbol == r.symbol) & (pd.to_datetime(fundamental.date) <= r.date)]
            if len(h): ev.at[i, "fundamental"] = float(h.sort_values("date").iloc[-1].fundamental)
    return ev.reset_index(drop=True)


def fit_composite(events: pd.DataFrame, candidate_features=("price_anomaly", "volume_anomaly", "momentum", "institutional", "fundamental", "options", "retail"), min_coverage: float = 0.5) -> dict:
    """min_coverage excludes terms with no data source (placeholders at 0% coverage); rows missing an included term are dropped."""
    import statsmodels.api as sm
    ev = events.dropna(subset=["label"])
    feats = [f for f in candidate_features if f in ev.columns and ev[f].notna().mean() >= min_coverage]
    unavailable = [f for f in candidate_features if f not in feats]
    ev = ev.dropna(subset=feats)
    y = ev.label.astype(float).values; X = ev[feats].astype(float)
    mean, std = X.mean(), X.std(ddof=0).replace(0, 1.0)
    Xs = (X - mean) / std; Xc = sm.add_constant(Xs, has_constant="add")
    try: res = sm.Logit(y, Xc).fit(disp=0, maxiter=200)
    except Exception: res = sm.Logit(y, Xc).fit_regularized(alpha=0.01, disp=0)
    params = dict(zip(Xc.columns, map(float, res.params))); bse = dict(zip(Xc.columns, map(float, getattr(res, "bse", pd.Series(np.nan, index=Xc.columns)))))
    pv = dict(zip(Xc.columns, map(float, getattr(res, "pvalues", pd.Series(np.nan, index=Xc.columns)))))
    return {"n": int(len(ev)), "positives": int(y.sum()), "base_rate": float(y.mean()) if len(y) else None, "features": feats, "unavailable": unavailable,
            "coef": {("b0" if k == "const" else k): v for k, v in params.items()}, "se": {("b0" if k == "const" else k): v for k, v in bse.items()},
            "p": {("b0" if k == "const" else k): v for k, v in pv.items()}, "standardization": {"mean": mean.to_dict(), "std": std.to_dict()},
            "llf": float(getattr(res, "llf", np.nan)), "llnull": float(getattr(res, "llnull", np.nan))}


def predict(model: dict, events: pd.DataFrame) -> np.ndarray:
    z = np.full(len(events), model["coef"]["b0"], dtype=float)
    for f in model["features"]:
        x = (events[f].astype(float).values - model["standardization"]["mean"][f]) / model["standardization"]["std"][f]
        z = z + model["coef"][f] * np.nan_to_num(x, nan=0.0)
    return 1.0 / (1.0 + np.exp(-z))


def auc(p: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0: return None
    from scipy.stats import rankdata
    r = rankdata(np.concatenate([pos, neg])); return float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def evaluate(p: np.ndarray, y: np.ndarray, threshold: float = 0.5) -> dict:
    y = y.astype(float); m = ~np.isnan(y); p, y = p[m], y[m]
    if len(y) == 0: return {"n": 0}
    flag = p >= threshold; tp = float((flag & (y == 1)).sum()); fp = float((flag & (y == 0)).sum()); fn = float((~flag & (y == 1)).sum())
    base = float(y.mean()); prec = tp / (tp + fp) if tp + fp else None; rec = tp / (tp + fn) if tp + fn else None
    order = np.argsort(-p); top = order[: max(1, len(p) // 10)]; top_prec = float(y[top].mean())
    dec = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False) if len(p) >= 10 else pd.Series(np.zeros(len(p), dtype=int))
    calib = pd.DataFrame({"decile": dec.values, "p": p, "y": y}).groupby("decile").agg(n=("y", "size"), mean_p=("p", "mean"), hit_rate=("y", "mean")).reset_index()
    return {"n": int(len(y)), "positives": int(y.sum()), "base_rate": base, "auc": auc(p, y), "threshold": threshold, "flagged": int(flag.sum()),
            "precision": prec, "recall": rec, "lift": (prec / base) if (prec is not None and base > 0) else None,
            "top_decile_precision": top_prec, "top_decile_lift": (top_prec / base) if base > 0 else None, "calibration": calib.to_dict("records")}
