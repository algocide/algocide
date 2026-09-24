#!/usr/bin/env python3
"""Cross-check the RWA funding bundle (main source) against two independent GitHub snapshot repos that recorded
Hyperliquid funding in Sep 2026 (CozanetHQ/hyperliquid-research 10-min snapshots; hazwop/funding-scout 4-hourly).
If the bundle is faithful to fundingHistory, the snapshot 'funding' (the predicted/current hourly rate) should match
the settled hourly rate for the same hour closely (not exactly: snapshots are mid-hour predictions)."""
import os, sys, json, glob
import numpy as np, pandas as pd
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
EXT = "/tmp/claude-0/-home-user-algocide/5429b401-030f-59f0-af50-07bf4bddde5d/scratchpad/ext"
fu = pd.read_parquet(os.path.join(ROOT, "data", "derived", "funding_hourly.parquet"))
main = fu[(fu["dex"] == "main")].pivot_table(index="ts", columns="coin", values="funding")
rows = []
for f in sorted(glob.glob(os.path.join(EXT, "hyperliquid-research", "data", "snapshots", "*.jsonl"))):
    for line in open(f):
        d = json.loads(line); ts = pd.Timestamp(d["ts"])
        for p in d["perps"]:
            if p["coin"] in ("BTC", "ETH", "HYPE"):
                rows.append({"ts": ts, "coin": p["coin"], "snap_funding": float(p["funding"])})
cz = pd.DataFrame(rows, columns=["ts", "coin", "snap_funding"])
cz["hour"] = pd.to_datetime(cz["ts"]).dt.floor("h")
out = {"note_cozanet": "CozanetHQ snapshots hold the top-30 perps by OI in UNITS, so majors may be absent; coins found: " + ",".join(sorted(cz["coin"].unique()))}
for coin in ["BTC", "ETH", "HYPE"]:
    if (cz["coin"] == coin).sum() == 0: continue
    x = cz[cz["coin"] == coin].groupby("hour")["snap_funding"].mean()
    y = main[coin].reindex(x.index)
    j = pd.concat([x, y], axis=1).dropna(); j.columns = ["snapshot", "bundle"]
    out[coin] = {"n_hours": int(len(j)), "corr": float(j["snapshot"].corr(j["bundle"])), "mean_abs_diff": float((j["snapshot"] - j["bundle"]).abs().mean()),
                 "mean_bundle": float(j["bundle"].mean()), "mean_snapshot": float(j["snapshot"].mean()), "share_exact_1e-7": float(((j["snapshot"] - j["bundle"]).abs() < 1e-7).mean())}
rows = []
for f in sorted(glob.glob(os.path.join(EXT, "funding-scout", "data", "funding", "scout_*.json"))):
    d = json.load(open(f)); ts = pd.Timestamp(d["ts"])
    for m in d.get("majors", []):
        rows.append({"ts": ts, "coin": m["coin"], "funding_8h": float(m["funding_8h"])})
fs = pd.DataFrame(rows); fs["hour"] = fs["ts"].dt.floor("h")
for coin in ["BTC", "ETH"]:
    x = fs[fs["coin"] == coin].set_index("hour")["funding_8h"]
    y = main[coin].reindex(x.index)
    j = pd.concat([x, y], axis=1).dropna(); j.columns = ["scout", "bundle"]
    out[f"scout_{coin}"] = {"n": int(len(j)), "corr": float(j["scout"].corr(j["bundle"])) if len(j) > 3 else np.nan, "mean_abs_diff": float((j["scout"] - j["bundle"]).abs().mean()),
                            "note": "funding-scout 'funding_8h' field compared 1:1 with the hourly bundle rate (its unit is ambiguous; a 1:1 match means it is the hourly rate)"}
json.dump(out, open(os.path.join(ROOT, "results", "e0_provenance_crosscheck.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
