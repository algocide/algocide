#!/usr/bin/env python3
"""Sample the daily history of data/processed/asset_universe_latest.json (Tohshi-memo/HyperLiquid-Bot-test) to obtain
REPEATED liquidity observations (24h notional volume, open interest, mid/mark/oracle) for xyz markets and BTC/ETH.
One snapshot per UTC day: the first commit at or after 15:30 UTC (mid US session). Provenance in a manifest."""
import subprocess, json, sys, os, time
import pandas as pd
REPO = sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/-home-user-algocide/03f84c32-5c53-56f6-bea6-1f8525ef7c47/scratchpad/ext/HyperLiquid-Bot-test"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/home/user/algocide/research/data/raw"
PATH = "data/processed/asset_universe_latest.json"
def git(*args, binary=False):
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()
log = [l for l in git("log", "--reverse", "--format=%H %cI", "--", PATH).split("\n") if l.strip()]
commits = [(l.split(" ")[0], pd.Timestamp(l.split(" ")[1]).tz_convert("UTC")) for l in log]
print("commits:", len(commits), commits[0][1], commits[-1][1], flush=True)
picked = {}
for h, d in commits:
    day = d.date()
    if d.hour * 60 + d.minute >= 15 * 60 + 30 and day not in picked:
        picked[day] = (h, d)
rows, manifest = [], []; t0 = time.time()
for day, (h, d) in sorted(picked.items()):
    try:
        data = json.loads(git("show", f"{h}:{PATH}", binary=True))
    except Exception as e:
        print("skip", day, e, flush=True); continue
    assets = data.get("assets") if isinstance(data, dict) else data
    if not assets: continue
    for r in assets:
        s = r.get("symbol", "")
        if s.startswith("xyz:") or s in ("BTC", "ETH", "HYPE", "SOL"):
            rows.append({"snapshot_time": str(d), "observed_at": data.get("observed_at"), "symbol": s, "asset_class": r.get("asset_class"),
                         "day_ntl_vlm": r.get("day_ntl_vlm"), "open_interest": r.get("open_interest"), "mid_px": r.get("mid_px"),
                         "mark_px": r.get("mark_px"), "oracle_px": r.get("oracle_px"), "funding": r.get("funding"), "premium": r.get("premium"),
                         "max_leverage": r.get("max_leverage"), "sz_decimals": r.get("sz_decimals"), "growth_mode": r.get("growth_mode"),
                         "only_isolated": r.get("only_isolated"), "margin_mode": r.get("margin_mode"), "streaming_oi_cap": r.get("streaming_oi_cap")})
    manifest.append({"day": str(day), "commit": h, "commit_time": str(d), "n_assets": len(assets)})
    if len(manifest) % 10 == 0: print(len(manifest), "days", f"{time.time()-t0:.0f}s", flush=True)
df = pd.DataFrame(rows); os.makedirs(OUT, exist_ok=True)
df.to_parquet(os.path.join(OUT, "tohshi_liquidity_daily.parquet"), index=False)
pd.DataFrame(manifest).to_csv(os.path.join(OUT, "tohshi_liquidity_daily_manifest.csv"), index=False)
print("done", len(df), "rows", df.snapshot_time.min(), df.snapshot_time.max(), f"{time.time()-t0:.0f}s")
