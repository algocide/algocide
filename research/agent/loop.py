#!/usr/bin/env python3
"""Heartbeat loop: feed -> digest -> decide -> verify -> risk gate -> venue -> reconcile -> journal, once per completed
bar. Paper by default. Replay mode drives the same code from the research datasets (offline validation).
Usage:
  PYTHONPATH=src python3 agent/loop.py --mode replay --source candles --interval 1h --universe BTC,ETH --start 2026-05-01 --end 2026-06-01 --state results/agent/replay_state.json
  PYTHONPATH=src python3 agent/loop.py --mode paper --once            # live data, paper fills (needs api.hyperliquid.xyz)
  PYTHONPATH=src python3 agent/loop.py --mode live --live --acknowledge-risk   # real orders; key-gated; NOT run here
Kill switch: create the file named in cfg.paths.kill_file (default results/agent/KILL); the agent flattens and refuses to run.
"""
from __future__ import annotations
import argparse, json, os, sys, time
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(HERE, ".."); sys.path.insert(0, os.path.join(R, "src")); sys.path.insert(0, R)
from agent.config import load_config
from agent.feed import LiveFeed, ReplayFeed
from agent.digest import asset_digest, account_digest
from agent.decide import RuleConsensusDecider, LLMDecider, VerifierGate, Decision
from agent.risk import RiskGate
from agent.venues import PaperVenue, HyperliquidVenue

TF_MIN = {"15m": 15, "1h": 60, "4h": 240}


def new_state(equity):
    return {"equity": equity, "shadow_equity": equity, "hwm": equity, "paused": False, "paused_at": None, "halted_today": False, "killed": False, "day": None, "day_pnl": 0.0,
            "trades_today": 0, "position": None, "shadow_position": None, "trades": [], "last_bar": None, "last_entry_bar_idx": None, "bar_idx": 0, "now": None}


class Agent:
    def __init__(self, cfg, feed, venue, decider, journal_path, state_path, kill_file, sampled=False):
        self.cfg, self.feed, self.venue, self.decider = cfg, feed, venue, decider
        self.verifier = VerifierGate(cfg["account"]["min_reward_risk"]); self.risk = RiskGate(cfg)
        self.journal_path, self.state_path, self.kill_file = journal_path, state_path, kill_file; self.sampled = sampled
        os.makedirs(os.path.dirname(journal_path) or ".", exist_ok=True)
        self.state = json.load(open(state_path)) if os.path.exists(state_path) else new_state(cfg["account"]["start_equity"])

    def log(self, kind, **kw):
        rec = {"ts": str(self.state.get("now")), "kind": kind, **kw}
        with open(self.journal_path, "a") as f: f.write(json.dumps(rec, default=str) + "\n")

    def save(self): json.dump(self.state, open(self.state_path, "w"), indent=1, default=str)

    def step(self, now):
        st = self.state; st["now"] = str(now); cfg = self.cfg; iv = cfg["interval"]; tf = cfg["decider_params"].get("tf_trend", "1h")
        self.risk.roll_day(st, now)
        # 0) kill switch
        kr = self.risk.kill_reason(st, self.kill_file)
        if kr and not st["killed"]:
            st["killed"] = True; self.log("KILL", reason=kr)
            if st["position"]: self._close(st["position"], self.feed.mid(st["position"]["sym"]), now, "kill")
            self.save(); return "killed"
        if st["killed"]: return "killed"
        # 1) data (completed bars only)
        bars = {s: self.feed.candles(s, iv, 260) for s in cfg["universe"]}
        latest = max((b.ts.iloc[-1] for b in bars.values() if len(b)), default=None)
        if latest is None or (st["last_bar"] and str(latest) <= st["last_bar"]): return "no_new_bar"
        st["last_bar"] = str(latest); st["bar_idx"] += 1
        trend = {s: (self.feed.candles(s, tf, 120) if tf != iv else None) for s in cfg["universe"]} if TF_MIN.get(tf, 0) > TF_MIN.get(iv, 0) else {s: None for s in cfg["universe"]}
        if trend and any(v is not None for v in trend.values()):
            for s in cfg["universe"]:   # 1h bars aggregated from 15m when the feed has no native 1h: use only fully completed hours
                if trend[s] is not None and len(trend[s]) and trend[s].ts.iloc[-1] > latest: trend[s] = trend[s][trend[s].ts <= latest]
        mids = {s: float(bars[s].c.iloc[-1]) for s in cfg["universe"] if len(bars[s])}
        # 2) manage open position on the new bar (stop/target/session/time)
        pos = st["position"]
        if pos:
            b = bars[pos["sym"]].iloc[-1]; pos["bars_held"] = st["bar_idx"] - pos["entry_bar_idx"]
            reason, fill = self.venue.check_exits(pos, {"o": b.o, "h": b.h, "l": b.l, "c": b.c}, self.sampled) if pos["bars_held"] >= 1 else (None, None)
            if reason: self._close(pos, fill, now, reason, stop_fill=(reason == "stop")); pos = None
            elif self.risk.session_flags(now)["must_exit"]: self._close(pos, mids[pos["sym"]], now, "session_end"); pos = None
            elif pos["bars_held"] >= cfg["account"]["max_position_age_bars"]: self._close(pos, mids[pos["sym"]], now, "max_age"); pos = None
        self.risk.update_pause_flags(st)
        # 3) digest + decide
        digests = {s: asset_digest(bars[s], trend.get(s), self.sampled, self.feed.asset_ctx(s) if hasattr(self.feed, "asset_ctx") else None, self.feed.quote(s)) for s in cfg["universe"]}
        acct = account_digest(st, mids, {"daily_loss_halt_usd": cfg["account"]["daily_loss_halt_usd"], "risk_per_trade_usd": cfg["account"]["risk_per_trade_usd"]})
        try: decisions = self.decider.decide(digests, acct)
        except Exception as e:
            self.log("decider_error", error=str(e)); decisions = [Decision(s, "hold", rationale=f"decider error: {e}") for s in cfg["universe"]]
        # 4) verify + risk gate + execute (one entry max per bar; lowest spread first on ties)
        order = sorted(cfg["universe"], key=lambda s: (digests[s].get("quote", {}).get("half_spread_bps", 9), s))
        for s in order:
            dec = next((d for d in decisions if d.asset == s), None)
            if dec is None: continue
            price = mids.get(s)
            if dec.action == "close" and st["position"] and st["position"]["sym"] == s:
                self._close(st["position"], price, now, "decider_close"); self.log("decision", **dec.to_dict()); continue
            ok, why = self.verifier.check(dec, price)
            if not ok: self.log("rejected", stage="verifier", reason=why, **dec.to_dict()); continue
            allowed, why, qty = self.risk.evaluate(dec, price, st, now, st["last_entry_bar_idx"], st["bar_idx"])
            if dec.action in ("buy", "sell"):
                if not allowed: self.log("rejected", stage="risk", reason=why, **dec.to_dict()); continue
                dr = 1 if dec.action == "buy" else -1
                try:
                    pos = self.venue.open(s, dr, qty, price, float(dec.sl_price), float(dec.tp_price) if dec.tp_price else None, now, st["bar_idx"])
                except Exception as e:
                    self.log("execution_failure", reason=str(e), **dec.to_dict()); continue
                pos["rationale"] = dec.rationale; pos["decider"] = dec.decider; pos["confidence"] = dec.confidence
                st["position"] = pos; st["last_entry_bar_idx"] = st["bar_idx"]; st["trades_today"] += 1
                self.log("entry", **{k: v for k, v in pos.items()}); break
            else: self.log("decision", **dec.to_dict())
        self.save(); return "ok"

    def _close(self, pos, price, now, reason, stop_fill=False):
        rec = self.venue.close(pos, price, now, reason, stop_fill=stop_fill); st = self.state
        if rec.get("net_pnl") is not None:
            st["equity"] += rec["net_pnl"]; st["shadow_equity"] += rec["net_pnl"]; st["day_pnl"] += rec["net_pnl"]
        st["trades"].append(rec); st["position"] = None; self.log("exit", **rec)


def build(cfg, args):
    if args.mode == "replay":
        feed = ReplayFeed(cfg["universe"], cfg["interval"], args.source); sampled = args.source == "sampled"
    else:
        feed = LiveFeed(cfg["live"]["base_url"]); sampled = False
    if args.mode == "live": venue = HyperliquidVenue(cfg, acknowledge_risk=args.acknowledge_risk and args.live)
    else:
        funding = None
        try:
            from hlr2.funding import load_funding; funding = load_funding(cfg["universe"])
        except Exception: pass
        venue = PaperVenue(cfg.get("costs", {}).get("regime", "base"), funding)
    decider = LLMDecider(cfg) if cfg["decider"] == "llm" else RuleConsensusDecider(**cfg["decider_params"])
    return Agent(cfg, feed, venue, decider, args.journal or cfg["paths"]["journal"], args.state or cfg["paths"]["state"], cfg["paths"]["kill_file"], sampled)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--config", default=None); ap.add_argument("--mode", default="replay", choices=["replay", "paper", "live"])
    ap.add_argument("--source", default="candles", choices=["candles", "sampled"]); ap.add_argument("--interval", default=None); ap.add_argument("--universe", default=None)
    ap.add_argument("--start", default=None); ap.add_argument("--end", default=None); ap.add_argument("--state", default=None); ap.add_argument("--journal", default=None)
    ap.add_argument("--once", action="store_true"); ap.add_argument("--live", action="store_true"); ap.add_argument("--acknowledge-risk", action="store_true"); a = ap.parse_args()
    cfg = load_config(a.config)
    if a.interval: cfg["interval"] = a.interval
    if a.universe: cfg["universe"] = a.universe.split(",")
    agent = build(cfg, a)
    if a.mode == "replay":
        times = agent.feed.all_times
        if a.start: times = times[times >= pd.Timestamp(a.start, tz="UTC")]
        if a.end: times = times[times <= pd.Timestamp(a.end, tz="UTC")]
        n = 0
        for t in times:
            agent.feed.set_time(t); r = agent.step(t); n += 1
            if r == "killed": break
        st = agent.state; tr = pd.DataFrame(st["trades"])
        print(f"replay steps={n} trades={len(tr)} equity={st['equity']:.2f} paused={st['paused']} halted_today={st['halted_today']} killed={st['killed']}")
        if len(tr): print(tr[["sym", "dir", "entry_ts", "entry_px", "exit_ts", "exit_px", "reason", "net_pnl"]].tail(8).to_string())
        return
    while True:
        try:
            r = agent.step(pd.Timestamp.now(tz="UTC")); print(pd.Timestamp.now(tz="UTC"), r, "equity", round(agent.state["equity"], 2), "pos", agent.state["position"] and agent.state["position"]["sym"], flush=True)
        except Exception as e:
            agent.log("loop_error", error=str(e)); print("error", e, flush=True)
        if a.once: break
        time.sleep(60)


if __name__ == "__main__":
    main()
