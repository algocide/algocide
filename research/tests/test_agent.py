"""Agent tests: digest causality, decision schema, verifier geometry, risk gate (halt/pause/kill/caps/session), paper venue
fills and stop-first sequencing, replay loop end-to-end, kill switch. Run: cd research && PYTHONPATH=src python3 tests/test_agent.py"""
import os, sys, json, shutil, tempfile
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src")); sys.path.insert(0, R)
from agent.digest import asset_digest, account_digest, rsi
from agent.decide import RuleConsensusDecider, VerifierGate, Decision
from agent.risk import RiskGate
from agent.venues import PaperVenue
from agent.config import load_config
from agent.loop import Agent, new_state
from agent.feed import ReplayFeed


def synth_bars(n=300, seed=1, drift=0.0, start="2026-06-01 00:00"):
    rng = np.random.default_rng(seed); r = rng.normal(drift, 0.003, n); c = 100 * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, 0.001, n)) * c
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"ts": ts, "bucket": ts - pd.Timedelta(minutes=15), "o": o, "h": np.maximum(o, c) + w, "l": np.minimum(o, c) - w, "c": c, "v": rng.uniform(1, 2, n)})


def test_digest_causal_and_schema():
    b = synth_bars(); d1 = asset_digest(b, None, False)
    b2 = b.copy(); b2.loc[b2.index[-1] + 1] = b2.iloc[-1]; b2.loc[b2.index[-1], "c"] = 999.0    # append a future bar
    d2 = asset_digest(b2.iloc[:-1], None, False)                                                # digest as of the same last completed bar
    for k in ("price", "ema20", "rsi14", "macd_hist", "atr", "hh_12"): assert abs(d1[k] - d2[k]) < 1e-9
    assert d1["ok"] and d1["bar_close"] == str(b.ts.iloc[-1]) and 0 <= d1["rsi14"] <= 100
    assert asset_digest(b.head(30), None, False)["ok"] is False
    print("test_digest_causal_and_schema ok")


def test_decider_and_verifier():
    up = synth_bars(drift=0.002, seed=2); dn = synth_bars(drift=-0.002, seed=3)
    dec = RuleConsensusDecider(score_threshold=2, require_trend_tf=False)
    acct = {"position": None}
    out = dec.decide({"UP": asset_digest(up, None, False), "DN": asset_digest(dn, None, False)}, acct)
    assert {o.asset for o in out} == {"UP", "DN"} and all(o.action in ("buy", "sell", "hold", "close") for o in out)
    for o in out:
        if o.action == "buy": assert o.sl_price < o.tp_price and o.sl_price < asset_digest(up, None, False)["price"]
        if o.action == "sell": assert o.sl_price > o.tp_price
    v = VerifierGate(1.5)
    assert v.check(Decision("X", "buy", 0, tp_price=110, sl_price=95), 100)[0]
    assert not v.check(Decision("X", "buy", 0, tp_price=110, sl_price=105), 100)[0]        # stop wrong side
    assert not v.check(Decision("X", "buy", 0, tp_price=104, sl_price=95), 100)[0]         # rr < 1.5
    assert not v.check(Decision("X", "sell", 0, tp_price=90, sl_price=120), 100)[0]        # stop too far (>5%)
    assert v.check(Decision("X", "close"), 100)[0] and v.check(Decision("X", "hold"), 100)[0]
    print("test_decider_and_verifier ok")


def test_risk_gate():
    cfg = load_config(None); rg = RiskGate(cfg); st = new_state(100.0); now = pd.Timestamp("2026-06-02 15:00", tz="UTC")
    rg.roll_day(st, now)
    d = Decision("BTC", "buy", 0, tp_price=104000, sl_price=99000)
    ok, why, qty = rg.evaluate(d, 100000.0, st, now, None, 5); assert ok and qty > 0 and qty * 100000 <= 200 + 1e-9, (ok, why, qty)
    st["position"] = {"sym": "ETH"}; assert not rg.evaluate(d, 100000.0, st, now, None, 5)[0]; st["position"] = None
    st["day_pnl"] = -3.5; rg.update_pause_flags(st); assert st["halted_today"] and not rg.evaluate(d, 100000.0, st, now, None, 5)[0]
    st["halted_today"] = False; st["day_pnl"] = 0; st["equity"] = 89.0; st["hwm"] = 100.0; rg.update_pause_flags(st); assert st["paused"] and "pause" in rg.evaluate(d, 100000.0, st, now, None, 5)[1]
    st["equity"] = 79.0; assert rg.kill_reason(st, "/nonexistent/KILL") is not None
    tmp = tempfile.mkdtemp(); kf = os.path.join(tmp, "KILL"); open(kf, "w").write("x"); st["equity"] = 100.0; assert "manual" in rg.kill_reason(st, kf); shutil.rmtree(tmp)
    st = new_state(100.0); assert not rg.evaluate(d, 100000.0, st, now, 5, 5)[0]           # cooldown: same bar
    st["trades_today"] = 6; assert "max trades" in rg.evaluate(d, 100000.0, st, now, None, 9)[1]
    cfg2 = load_config(None); cfg2["session"] = "us_regular"; rg2 = RiskGate(cfg2)
    assert not rg2.evaluate(d, 100000.0, new_state(100.0), pd.Timestamp("2026-06-02 03:00", tz="UTC"), None, 5)[0]   # outside US session
    assert rg2.evaluate(d, 100000.0, new_state(100.0), pd.Timestamp("2026-06-02 15:00", tz="UTC"), None, 5)[0]
    print("test_risk_gate ok")


def test_paper_venue():
    pv = PaperVenue("zero"); now = pd.Timestamp("2026-06-02 15:00", tz="UTC")
    pos = pv.open("BTC", 1, 0.001, 100000.0, 99000.0, 102000.0, now, 1)
    assert pv.check_exits(pos, {"o": 100000, "h": 102500, "l": 98500, "c": 100000}, False) == ("stop", 99000.0)      # both touched -> stop first
    assert pv.check_exits(pos, {"o": 98000, "h": 98500, "l": 97500, "c": 98000}, False) == ("stop", 98000)           # gap through stop -> open
    assert pv.check_exits(pos, {"o": 100000, "h": 102500, "l": 99500, "c": 102000}, False) == ("target", 102000.0)
    assert pv.check_exits(pos, {"o": 100000, "h": 101000, "l": 99500, "c": 100500}, False) == (None, None)
    assert pv.check_exits(pos, {"o": 100000, "h": 100000, "l": 99000, "c": 99000}, True) == ("stop", 99000)          # sampled: close-based
    rec = pv.close(pos, 101000.0, now, "test"); assert abs(rec["net_pnl"] - 1.0) < 1e-9
    pv2 = PaperVenue("base"); pos2 = pv2.open("BTC", 1, 0.001, 100000.0, 99000.0, None, now, 1); rec2 = pv2.close(pos2, 100000.0, now, "flat")
    assert rec2["net_pnl"] < 0   # round trip costs money
    print("test_paper_venue ok")


def test_replay_loop_and_kill_switch():
    tmp = tempfile.mkdtemp(); cfg = load_config(None); cfg["universe"] = ["BTC", "ETH"]; cfg["interval"] = "1h"; cfg["decider_params"]["tf_trend"] = "1h"
    feed = ReplayFeed(cfg["universe"], "1h", "candles"); venue = PaperVenue("base")
    ag = Agent(cfg, feed, venue, RuleConsensusDecider(**cfg["decider_params"]), os.path.join(tmp, "j.jsonl"), os.path.join(tmp, "s.json"), os.path.join(tmp, "KILL"))
    times = feed.all_times[(feed.all_times >= pd.Timestamp("2026-01-15", tz="UTC")) & (feed.all_times <= pd.Timestamp("2026-02-15", tz="UTC"))]
    for t in times: feed.set_time(t); ag.step(t)
    st = ag.state; tr = pd.DataFrame(st["trades"])
    assert st["bar_idx"] > 700 and os.path.exists(os.path.join(tmp, "j.jsonl"))
    for i in range(1, len(tr)): assert pd.Timestamp(tr.entry_ts.iat[i]) >= pd.Timestamp(tr.exit_ts.iat[i - 1]), "overlapping positions"
    if len(tr): assert (tr.notional <= 2 * 100 * 1.5).all()
    lines = [json.loads(l) for l in open(os.path.join(tmp, "j.jsonl"))]; kinds = {l["kind"] for l in lines}
    assert "entry" in kinds and "exit" in kinds
    # kill switch: flatten and refuse
    open(os.path.join(tmp, "KILL"), "w").write("stop"); t2 = feed.all_times[feed.all_times > times[-1]][:3]
    for t in t2: feed.set_time(t); r = ag.step(t)
    assert ag.state["killed"] and ag.state["position"] is None and r == "killed"
    shutil.rmtree(tmp); print("test_replay_loop_and_kill_switch ok", "trades:", len(tr))


def test_network_gate():
    from agent.cli import resolve_network, TESTNET_URL, MAINNET_URL
    assert resolve_network({}, True) == (TESTNET_URL, "testnet")                                            # default: testnet
    assert resolve_network({"USE_TESTNET": "false"}, True) == (TESTNET_URL, "testnet")                       # no confirmation -> testnet
    assert resolve_network({"USE_TESTNET": "false", "CONFIRM_MAINNET": "true"}, False) == (TESTNET_URL, "testnet")   # no acknowledgement -> testnet
    assert resolve_network({"USE_TESTNET": "false", "CONFIRM_MAINNET": "true"}, True) == (MAINNET_URL, "mainnet")
    assert resolve_network({"USE_TESTNET": "true", "CONFIRM_MAINNET": "true"}, True) == (TESTNET_URL, "testnet")
    print("test_network_gate ok")


if __name__ == "__main__":
    test_digest_causal_and_schema(); test_decider_and_verifier(); test_risk_gate(); test_paper_venue(); test_replay_loop_and_kill_switch(); test_network_gate(); print("ALL AGENT TESTS PASSED")
