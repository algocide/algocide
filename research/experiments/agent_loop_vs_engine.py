#!/usr/bin/env python3
"""Consistency check: the heartbeat loop in replay mode (agent/loop.py, PaperVenue) vs the engine adapter
(agent/backtest_adapter.py through hlr2.backtest.run) on the same window, rules and costs. Differences arise only from
the loop's account-level rules that the engine does not model (daily-loss halt, max trades/day, cooldown, max age)."""
import os, sys, json, tempfile, shutil
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src")); sys.path.insert(0, R)
from agent.config import load_config
from agent.feed import ReplayFeed
from agent.venues import PaperVenue
from agent.decide import RuleConsensusDecider
from agent.loop import Agent
from agent.backtest_adapter import RuleConsensusStrategy
from hlr2.data import load_candles
from hlr2.backtest import run
from hlr2.funding import load_funding
from hlr2.metrics import summarize
start, end = pd.Timestamp("2026-02-01", tz="UTC"), pd.Timestamp("2026-04-01", tz="UTC")
params = dict(score_threshold=2, sl_atr=2.0, tp_atr=4.0, max_hold_bars=32, tf_trend="1h")
cfg = load_config(None); cfg["universe"] = ["BTC", "ETH"]; cfg["interval"] = "1h"; cfg["decider_params"].update(params)
# make the loop's extra account rules inert for the comparison
cfg["account"].update({"daily_loss_halt_usd": 1e9, "max_trades_per_day": 1e9, "cooldown_bars": 0, "max_position_age_bars": 10**6, "drawdown_pause_usd": 1e9, "drawdown_kill_usd": 1e9})
tmp = tempfile.mkdtemp(); fund = load_funding(cfg["universe"])
feed = ReplayFeed(cfg["universe"], "1h", "candles"); ag = Agent(cfg, feed, PaperVenue("base", fund), RuleConsensusDecider(**params), os.path.join(tmp, "j.jsonl"), os.path.join(tmp, "s.json"), os.path.join(tmp, "KILL"))
times = feed.all_times[(feed.all_times >= start) & (feed.all_times <= end)]
for t in times: feed.set_time(t); ag.step(t)
lt = pd.DataFrame(ag.state["trades"]); ls = summarize(lt) if len(lt) else {"n_trades": 0, "net_pnl": 0}
panel = load_candles(cfg["universe"], "1h", "24x7"); acct_free = None
from hlr2.backtest import Account
res = run(panel, RuleConsensusStrategy(**params), cost_regime="base", funding=fund, start=start, end=end, account=Account(100.0, 1.0, 2.0, 1e9))
et = res["trades"]; es = summarize(et)
print(f"loop:   trades={ls['n_trades']} net={ls['net_pnl']:.2f}"); print(f"engine: trades={es['n_trades']} net={es['net_pnl']:.2f}")
if len(lt) and len(et):
    m = pd.merge(lt.assign(k=pd.to_datetime(lt.entry_ts, utc=True).dt.floor("h")), et.assign(k=pd.to_datetime(et.entry_ts, utc=True).dt.floor("h")), on=["sym", "k"], suffixes=("_loop", "_eng"))
    print(f"matched entries (same symbol & hour): {len(m)} of loop {len(lt)} / engine {len(et)}; mean |net diff| on matched: {np.abs(m.net_pnl_loop - m.net_pnl_eng).mean():.3f}")
json.dump({"loop": {"n": int(ls["n_trades"]), "net": float(ls["net_pnl"])}, "engine": {"n": int(es["n_trades"]), "net": float(es["net_pnl"])}}, open(os.path.join(R, "results", "agent_loop_vs_engine.json"), "w"), indent=1)
shutil.rmtree(tmp)
