#!/usr/bin/env python3
"""Reconstruct the full 15-minute mid-price history of Hyperliquid assets (incl. HIP-3 xyz stock perps) from the
commit history of the public repo Tohshi-memo/HyperLiquid-Bot-test (GitHub Actions cron collector; each commit
rewrites a rolling-window JSON file). We fetch one blob per rolling window (partial clone, lazy blob fetch), union
the records, and write a tidy Parquet. Provenance: every commit hash used is written to a manifest.

Price semantics (collector/asset_universe.py::build_row): price = first_positive(midPx from allMids/metaAndAssetCtxs,
markPx, oraclePx). observed_at = 15-minute bucket (floor of run time); collected_at = actual request time (cron
jitter of 0-13 minutes). Signals must be aligned on collected_at (the real observation time), never on observed_at.
"""
import subprocess, json, sys, os, time
import pandas as pd

REPO = sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/-home-user-algocide/03f84c32-5c53-56f6-bea6-1f8525ef7c47/scratchpad/ext/HyperLiquid-Bot-test"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/home/user/algocide/research/data/raw"
PATH = "data/processed/asset_price_history.json"
KEEP_PREFIX = ("xyz:",)
KEEP_EXACT = {"BTC", "ETH", "HYPE", "SOL"}

def git(*args, binary=False):
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()

log = [l for l in git("log", "--reverse", "--format=%H %cI", "--", PATH).split("\n") if l.strip()]
commits = [(l.split(" ")[0], pd.Timestamp(l.split(" ")[1])) for l in log]
print("commits touching file:", len(commits), commits[0][1], commits[-1][1], flush=True)

records = {}      # observed_at -> record (latest collected_at wins)
manifest = []
i = 0; t0 = time.time()
while i < len(commits):
    h, d = commits[i]
    try:
        blob = git("show", f"{h}:{PATH}", binary=True)
        data = json.loads(blob)
    except Exception as e:
        print("skip", h[:8], d, e, flush=True); i += 1; continue
    recs = data["records"] if isinstance(data, dict) else data
    if not recs:
        i += 1; continue
    obs = []
    for r in recs:
        oa = r.get("observed_at")
        if not oa: continue
        prev = records.get(oa)
        if prev is None or str(r.get("collected_at")) > str(prev.get("collected_at")):
            records[oa] = {"observed_at": oa, "collected_at": r.get("collected_at"),
                           "prices": {k: v for k, v in (r.get("prices") or {}).items() if k.startswith(KEEP_PREFIX) or k in KEEP_EXACT}}
        obs.append(pd.Timestamp(oa))
    tmin, tmax = min(obs), max(obs)
    window = tmax - tmin
    manifest.append({"commit": h, "commit_time": str(d), "n_records": len(recs), "obs_min": str(tmin), "obs_max": str(tmax), "window_hours": window.total_seconds()/3600})
    # next commit: the last one whose oldest record still overlaps (jump by window - 1h, min 1h)
    step = max(window - pd.Timedelta(hours=1), pd.Timedelta(hours=1))
    target = d + step
    j = i + 1
    while j < len(commits) and commits[j][1] < target:
        j += 1
    if j >= len(commits) and i < len(commits) - 1:
        j = len(commits) - 1
    i = j if j > i else i + 1
    if len(manifest) % 5 == 0:
        print(f"{len(manifest)} blobs, {len(records)} records, up to {tmax}, {time.time()-t0:.0f}s", flush=True)

rows = []
for oa, r in records.items():
    for sym, px in r["prices"].items():
        rows.append((oa, r["collected_at"], sym, float(px)))
df = pd.DataFrame(rows, columns=["observed_at", "collected_at", "symbol", "price"])
df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True); df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True)
df = df.sort_values(["symbol", "collected_at"]).reset_index(drop=True)
os.makedirs(OUT, exist_ok=True)
df.to_parquet(os.path.join(OUT, "tohshi_mid_15m.parquet"), index=False)
pd.DataFrame(manifest).to_csv(os.path.join(OUT, "tohshi_mid_15m_manifest.csv"), index=False)
print("done rows", len(df), "symbols", df.symbol.nunique(), "span", df.collected_at.min(), df.collected_at.max(), f"{time.time()-t0:.0f}s")
