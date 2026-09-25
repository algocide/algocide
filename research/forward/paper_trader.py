#!/usr/bin/env python3
"""Prospective paper trader for the frozen candidate (research/STRATEGY_SPEC.md). DRY-RUN ONLY: no keys, no signing,
no order submission; it only reads public info-endpoint data and writes JSON/CSV state.

Modes
  live   : poll POST https://api.hyperliquid.xyz/info every 15 minutes (candleSnapshot 15m + allMids + l2Book for each
           market), compute signals on COMPLETED candles, simulate fills at the mid of the next poll + modelled costs,
           record every signal, rejection and execution failure. Requires network access to api.hyperliquid.xyz
           (blocked in the research sandbox; run this on any machine that can reach the API).
  replay : same loop driven by the stored 15-minute sampled mids (research/data/raw/tohshi_mid_15m.parquet) from a
           given start time -- used to validate the loop end-to-end without network access.
Account rules: $100 start, $1 planned risk incl. costs, 2x gross leverage cap, one position across the universe,
$10 drawdown pause (new entries stop; shadow book continues), session-end flat, latest entry 90 min before close.
Usage:
  PYTHONPATH=src python3 forward/paper_trader.py --mode replay --start 2026-09-10 --state results/forward/replay_state.json
  PYTHONPATH=src python3 forward/paper_trader.py --mode live --state results/forward/state.json --once
"""
from __future__ import annotations
import argparse, json, os, sys, time, datetime as dt
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src"))
from hlr2.data import Panel, _session_flags
from hlr2.strategies import REGISTRY
from hlr2.backtest import size_position
from hlr2.costs import base_model
from hlr2.specs import SPECS, round_price
from hlr2.sessions import ET

DEFAULT_SPEC = os.path.join(R, "STRATEGY_SPEC.json")
INFO_URL = "https://api.hyperliquid.xyz/info"


def load_spec(path):
    return json.load(open(path))


def fetch_live(symbols, lookback_bars=400):
    import requests
    end = int(time.time() * 1000); start = end - lookback_bars * 900_000
    data = {}; mids = requests.post(INFO_URL, json={"type": "allMids"}, timeout=20).json()
    for s in symbols:
        rows = requests.post(INFO_URL, json={"type": "candleSnapshot", "req": {"coin": s, "interval": "15m", "startTime": start, "endTime": end}}, timeout=30).json()
        df = pd.DataFrame(rows)
        for c in ["o", "h", "l", "c", "v"]: df[c] = df[c].astype(float)
        df["ts"] = pd.to_datetime(df["T"].astype(int) + 1, unit="ms", utc=True)   # candle close time
        df["bucket"] = pd.to_datetime(df["t"].astype(int), unit="ms", utc=True)
        # only COMPLETED candles: close time <= now
        df = df[df.ts <= pd.Timestamp.now(tz="UTC")]
        book = requests.post(INFO_URL, json={"type": "l2Book", "coin": s}, timeout=20).json()
        bid = float(book["levels"][0][0]["px"]); ask = float(book["levels"][1][0]["px"])
        data[s] = {"candles": df, "mid": float(mids.get(s, (bid + ask) / 2)), "bid": bid, "ask": ask, "half_spread_bps": (ask - bid) / (ask + bid) * 1e4}
    return data


def panel_from_frames(frames: dict[str, pd.DataFrame], sampled: bool) -> Panel:
    grid = pd.DatetimeIndex(sorted(set().union(*[set(f.ts) for f in frames.values()])))
    flags = _session_flags(pd.Series(grid), "us_regular", 90, 45)
    inst = {}
    for s, f in frames.items():
        f = f.set_index("ts").reindex(grid)
        d = pd.DataFrame({"ts": grid, "bucket": f.bucket.values, "o": f.o.values, "h": f.h.values, "l": f.l.values, "c": f.c.values, "v": np.nan, "n": np.nan})
        d["exec_px"] = np.nan; d["exec_ts"] = pd.NaT; d["gap_before_min"] = 15.0; d["gap_after_min"] = 15.0
        d["valid"] = np.isfinite(d.c.values); d["exec_valid"] = True
        for col in flags.columns: d[col] = flags[col].values
        inst[s] = d.reset_index(drop=True)
    return Panel("sampled" if sampled else "candles", grid, inst, 15)


class PaperState:
    def __init__(self, path):
        self.path = path
        if os.path.exists(path): self.d = json.load(open(path))
        else: self.d = {"equity": 100.0, "shadow_equity": 100.0, "hwm": 100.0, "paused": False, "paused_at": None, "position": None, "pending": None,
                        "trades": [], "signals": [], "rejections": [], "failures": [], "sessions_seen": [], "last_decision_ts": None}
    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True); json.dump(self.d, open(self.path, "w"), indent=1, default=str)


def step(state: PaperState, panel: Panel, strat, now_px: dict[str, float], now_ts, spec_cfg):
    """One decision step at the latest completed bar of the panel. now_px = current mid per symbol (the fill price for
    orders decided at the previous step); now_ts = time of that observation."""
    d = state.d; k = len(panel.grid) - 1
    if d["last_decision_ts"] == str(panel.grid[k]): return "already_decided"
    # 1) fill pending order at the current mid
    if d["pending"]:
        p = d["pending"]; s = p["sym"]; px = now_px.get(s)
        if px is None or not np.isfinite(px):
            d["failures"].append({"ts": str(now_ts), "sym": s, "what": "no_price_for_fill", "order": p}); d["pending"] = None
        else:
            cm = base_model(s, "base"); spec = SPECS[s]
            if p["kind"] == "entry":
                eff = px + p["dir"] * px * (cm.half_spread_bps + cm.slippage_bps) / 1e4
                qty, why = size_position(px, p["stop"], spec, cm, d["shadow_equity"] if d["paused"] else d["equity"], 1.0, 2.0)
                if qty <= 0: d["rejections"].append({"ts": str(now_ts), "sym": s, "reason": why, "order": p})
                else:
                    d["position"] = {"sym": s, "dir": p["dir"], "qty": qty, "entry_ts": str(now_ts), "entry_px": px, "entry_eff": eff, "entry_fee": cm.taker_fee * eff * qty,
                                     "stop": p["stop"], "target": p["target"], "tag": p["tag"], "state": p.get("state", {}), "shadow_only": d["paused"], "entry_k": k, "signal_ts": p["signal_ts"]}
            else:   # exit
                pos = d["position"]; eff = px - pos["dir"] * px * (cm.half_spread_bps + cm.slippage_bps) / 1e4
                fee = cm.taker_fee * eff * pos["qty"]; net = pos["dir"] * (eff - pos["entry_eff"]) * pos["qty"] - fee - pos["entry_fee"]
                rec = {**pos, "exit_ts": str(now_ts), "exit_px": px, "exit_eff": eff, "net_pnl": net, "reason": p["reason"]}
                d["trades"].append(rec); d["shadow_equity"] += net
                if not pos["shadow_only"]: d["equity"] += net
                d["position"] = None
            d["pending"] = None
    # 2) manage the open position on the latest completed bar
    st = strat.prepare(panel)
    pos = d["position"]
    if pos:
        s = pos["sym"]; di = panel.inst[s]; c, h, l = di.c.iat[k], di.h.iat[k], di.l.iat[k]
        reason = None
        if pos["dir"] == 1 and (l <= pos["stop"] if panel.kind == "candles" else c <= pos["stop"]): reason = "stop"
        elif pos["dir"] == -1 and (h >= pos["stop"] if panel.kind == "candles" else c >= pos["stop"]): reason = "stop"
        elif pos["target"] and ((pos["dir"] == 1 and c >= pos["target"]) or (pos["dir"] == -1 and c <= pos["target"])): reason = "target"
        elif bool(di.must_exit.iat[k]): reason = "session_end"
        else:
            fake = {**pos, "state": pos.get("state", {})}
            r = strat.exit(s, k, fake, st)
            if r: reason = r
            ns = strat.trail(s, k, fake, st) if hasattr(strat, "trail") else None
            if ns is not None: pos["stop"] = max(pos["stop"], ns) if pos["dir"] == 1 else min(pos["stop"], ns)
        if reason: d["pending"] = {"kind": "exit", "sym": s, "reason": reason, "signal_ts": str(panel.grid[k])}
    # 3) pause bookkeeping
    d["hwm"] = max(d["hwm"], d["equity"])
    if not d["paused"] and d["equity"] < d["hwm"] - 10: d["paused"] = True; d["paused_at"] = str(panel.grid[k])
    # 4) entries
    if d["position"] is None and d["pending"] is None:
        cands = []
        for s in panel.symbols:
            di = panel.inst[s]
            if not (bool(di.valid.iat[k]) and bool(di.can_enter.iat[k])): continue
            sig = strat.signal(s, k, st)
            if sig is None: continue
            d["signals"].append({"ts": str(panel.grid[k]), "sym": s, "dir": sig.dir, "stop": sig.stop, "target": sig.target, "tag": sig.tag})
            cands.append((base_model(s).half_spread_bps, s, sig))
        if cands:
            cands.sort(key=lambda x: (x[0], x[1])); _, s, sig = cands[0]
            stop_px = round_price(sig.stop, SPECS[s].sz_decimals)
            d["pending"] = {"kind": "entry", "sym": s, "dir": sig.dir, "stop": stop_px, "target": sig.target, "tag": sig.tag, "state": sig.state, "signal_ts": str(panel.grid[k])}
    d["last_decision_ts"] = str(panel.grid[k])
    sd = panel.inst[panel.symbols[0]].session_date.iat[k]
    if sd is not None and str(sd) not in d["sessions_seen"]: d["sessions_seen"].append(str(sd))
    return "ok"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mode", default="replay"); ap.add_argument("--spec", default=DEFAULT_SPEC); ap.add_argument("--state", required=True)
    ap.add_argument("--start", default=None); ap.add_argument("--end", default=None); ap.add_argument("--once", action="store_true"); a = ap.parse_args()
    spec = load_spec(a.spec); strat = REGISTRY[spec["strategy"]](**spec["params"]); syms = spec["symbols"]
    state = PaperState(a.state)
    if a.mode == "replay":
        raw = pd.read_parquet(os.path.join(R, "data", "raw", "tohshi_mid_15m.parquet")); raw = raw[raw.symbol.isin(syms)]
        wide = raw.pivot_table(index="collected_at", columns="symbol", values="price").sort_index().dropna(how="all")
        bmap = raw.drop_duplicates("collected_at").set_index("collected_at")["observed_at"]
        times = wide.index[(wide.index >= pd.Timestamp(a.start, tz="UTC")) if a.start else slice(None)]
        if a.end: times = times[times <= pd.Timestamp(a.end, tz="UTC")]
        warm = 400
        for t in times:
            i = wide.index.get_loc(t); lo = max(0, i - warm)
            frames = {}
            for s in syms:
                w = wide[s].iloc[lo: i + 1]
                c = w.values.astype(float); o = np.r_[np.nan, c[:-1]]
                frames[s] = pd.DataFrame({"ts": w.index, "bucket": pd.to_datetime(bmap.reindex(w.index).values, utc=True), "o": o, "h": np.fmax(o, c), "l": np.fmin(o, c), "c": c})
            panel = panel_from_frames(frames, sampled=True)
            step(state, panel, strat, {s: float(wide[s].iloc[i]) for s in syms}, t, spec)
        state.save()
        d = state.d; tr = pd.DataFrame(d["trades"])
        print(f"replay done: sessions={len(d['sessions_seen'])} signals={len(d['signals'])} trades={len(tr)} rejections={len(d['rejections'])} failures={len(d['failures'])} equity={d['equity']:.2f} shadow={d['shadow_equity']:.2f} paused={d['paused']}")
        if len(tr): print(tr[["sym", "dir", "entry_ts", "entry_px", "exit_ts", "exit_px", "reason", "net_pnl"]].tail(5).to_string())
    else:
        while True:
            try:
                live = fetch_live(syms)
                panel = panel_from_frames({s: v["candles"] for s, v in live.items()}, sampled=False)
                out = step(state, panel, strat, {s: v["mid"] for s, v in live.items()}, pd.Timestamp.now(tz="UTC"), spec)
                state.d.setdefault("quotes", []).append({"ts": str(pd.Timestamp.now(tz='UTC')), **{s: {"bid": v["bid"], "ask": v["ask"], "hs_bps": v["half_spread_bps"]} for s, v in live.items()}})
                state.save(); print(pd.Timestamp.now(tz="UTC"), out, "equity", state.d["equity"], "position", state.d["position"] and state.d["position"]["sym"])
            except Exception as e:
                state.d["failures"].append({"ts": str(pd.Timestamp.now(tz='UTC')), "what": f"poll_error: {e}"}); state.save(); print("error", e)
            if a.once: break
            time.sleep(900)


if __name__ == "__main__":
    main()
