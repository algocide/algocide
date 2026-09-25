#!/usr/bin/env python3
"""Rank xyz stock-linked markets on practical liquidity evidence and write MARKET_SELECTION.csv.
Evidence: (1) repeated daily snapshots of 24h notional volume and open interest (Tohshi-memo universe file history,
2026-05-01..2026-09-24, one snapshot/day ~15:30 UTC); (2) impact-price half-spreads from ONE metaAndAssetCtxs snapshot
(Freedom fixture, ~2026-09-02); (3) data continuity of the 15-minute price history in the US regular session.
Eligibility: single-name US-listed stock (or US-listed stock ETF) referenced by an xyz market; Korean names (SKHX,
SMSN, SKHY) are excluded because their regular session is KRX, not the US session required by the task."""
import os, json, numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
liq = pd.read_parquet(os.path.join(R, "data/raw/tohshi_liquidity_daily.parquet"))
liq["snapshot_time"] = pd.to_datetime(liq.snapshot_time, utc=True)
px = pd.read_parquet(os.path.join(R, "data/raw/tohshi_mid_15m.parquet"))
fx = json.load(open("/tmp/claude-0/-home-user-algocide/03f84c32-5c53-56f6-bea6-1f8525ef7c47/scratchpad/ext/Freedom/tests/fixtures/hyperliquid/metaAndAssetCtxs_xyz.json"))
meta, ctxs = fx
imp = {}
for a, c in zip(meta["universe"], ctxs):
    ip = c.get("impactPxs")
    if ip and float(c.get("midPx") or 0) > 0:
        imp[a["name"]] = {"impact_half_spread_bps": (float(ip[1]) - float(ip[0])) / float(c["midPx"]) / 2 * 1e4, "fixture_dayNtlVlm": float(c["dayNtlVlm"]), "fixture_oi_usd": float(c["openInterest"]) * float(c["markPx"])}
NON_STOCK = {"xyz:CL","xyz:BRENTOIL","xyz:GOLD","xyz:SILVER","xyz:COPPER","xyz:NATGAS","xyz:PLATINUM","xyz:PALLADIUM","xyz:ALUMINIUM","xyz:CORN","xyz:WHEAT","xyz:TTF","xyz:URANIUM",
             "xyz:SP500","xyz:XYZ100","xyz:DXY","xyz:EUR","xyz:GBP","xyz:JPY","xyz:KRW","xyz:VIX","xyz:VOL","xyz:DRAM","xyz:H100","xyz:JP225","xyz:KR200","xyz:NIFTY","xyz:IBOV",
             "xyz:SPCX","xyz:MINIMAX","xyz:ZHIPU","xyz:UNITREE","xyz:SHEIN","xyz:CXMT","xyz:KIOXIA","xyz:IBIDEN","xyz:HYUNDAI","xyz:SOFTBANK","xyz:GIGADEV","xyz:BOT","xyz:PURRDAT","xyz:LYTE","xyz:STRC","xyz:MAGS","xyz:SHAZ","xyz:NCLD","xyz:KSTR","xyz:SKHX","xyz:SMSN","xyz:SKHY"}
ETF = {"xyz:SOXL","xyz:EWY","xyz:EWJ","xyz:EWT","xyz:EWZ","xyz:SMH","xyz:XBI","xyz:XLE","xyz:URNM","xyz:KORU","xyz:MAGS"}
xs = liq[liq.symbol.str.startswith("xyz:") & ~liq.symbol.isin(NON_STOCK)].copy()
xs["oi_usd"] = xs.open_interest.astype(float) * xs.mark_px.astype(float)
last = xs.snapshot_time.max()
rows = []
et = px.collected_at.dt.tz_convert("America/New_York"); px["insess"] = (et.dt.weekday < 5) & ((et.dt.hour*60+et.dt.minute) >= 570) & ((et.dt.hour*60+et.dt.minute) < 960); px["date"] = et.dt.date
for s, g in xs.groupby("symbol"):
    g = g.sort_values("snapshot_time"); g30 = g[g.snapshot_time >= last - pd.Timedelta(days=30)]; g90 = g[g.snapshot_time >= last - pd.Timedelta(days=90)]
    pp = px[(px.symbol == s)]; ins = pp[pp.insess]
    rows.append({"symbol": s, "type": "etf" if s in ETF else "stock", "n_snapshots": len(g), "first_snapshot": str(g.snapshot_time.min().date()),
                 "vol24h_median_30d": g30.day_ntl_vlm.median(), "vol24h_p10_30d": g30.day_ntl_vlm.quantile(.1), "vol24h_median_90d": g90.day_ntl_vlm.median(),
                 "days_vol_below_1m_30d": int((g30.day_ntl_vlm < 1e6).sum()), "oi_usd_median_30d": g30.oi_usd.median(),
                 "impact_half_spread_bps_snapshot": imp.get(s, {}).get("impact_half_spread_bps"), "fixture_vol24h": imp.get(s, {}).get("fixture_dayNtlVlm"),
                 "price_obs_total": len(pp), "price_obs_insession": len(ins), "insession_days": ins.date.nunique(), "first_price_obs": str(pp.collected_at.min().date()) if len(pp) else None,
                 "max_leverage": g.max_leverage.iloc[-1], "sz_decimals": g.sz_decimals.iloc[-1], "growth_mode": g.growth_mode.iloc[-1], "only_isolated": g.only_isolated.iloc[-1], "oi_cap": g.streaming_oi_cap.iloc[-1],
                 "last_mid": g.mid_px.iloc[-1]})
df = pd.DataFrame(rows)
# execution cost estimate for a $150 order: half-spread (impact, conservative for tiny size) + fee 0.9 bp + 1 bp slippage, per side
df["est_cost_bps_per_side_150usd"] = df.impact_half_spread_bps_snapshot.fillna(df.impact_half_spread_bps_snapshot.max()) + 0.9 + 1.0
df["min_order_units_ok"] = (10.0 / df.last_mid).apply(lambda u: u) <= 2 * 100 / df.last_mid  # $10 min order fits within 2x of $100
# score: rank by 30d median volume (primary), then lower spread, require continuity >= 60 in-session days
elig = df[(df.insession_days >= 60) & (df.days_vol_below_1m_30d == 0)].copy()
elig["rank_vol"] = elig.vol24h_median_30d.rank(ascending=False); elig["rank_spread"] = elig.impact_half_spread_bps_snapshot.rank(ascending=True, na_option="bottom")
elig["score"] = elig.rank_vol + 0.5 * elig.rank_spread
elig = elig.sort_values(["score", "vol24h_median_30d"], ascending=[True, False])
df["eligible"] = df.symbol.isin(elig.symbol); df["selected"] = df.symbol.isin(elig.symbol.head(5))
df = df.sort_values("vol24h_median_30d", ascending=False)
df.to_csv(os.path.join(R, "MARKET_SELECTION.csv"), index=False)
pd.set_option("display.width", 250)
print(df[["symbol","type","vol24h_median_30d","vol24h_p10_30d","vol24h_median_90d","oi_usd_median_30d","impact_half_spread_bps_snapshot","insession_days","first_price_obs","max_leverage","growth_mode","eligible","selected"]].head(25).round(2).to_string())
print("SELECTED:", list(elig.symbol.head(5)))
