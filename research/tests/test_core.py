"""Focused tests: no future information, completed-1h alignment, session calendar, sizing/rounding, costs+funding,
stop/target sequencing, single concurrent position, drawdown pause. Run: cd research && PYTHONPATH=src python3 -m pytest -q tests
or PYTHONPATH=src python3 tests/test_core.py"""
import sys, os, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from hlr2.sessions import session_for_date, annotate, ET
from hlr2.data import Panel, tf_view, _session_flags
from hlr2.specs import round_size, round_price, Spec
from hlr2.backtest import run, size_position, Account
from hlr2.costs import CostModel
from hlr2.strategies import MATrend, Base, Signal, ChannelBO
import hlr2.backtest as bt


def synth_panel(n=800, seed=1, kind="candles", start="2026-06-01 13:30", step_min=15, syms=("A", "B"), drift=0.0):
    rng = np.random.default_rng(seed)
    grid = pd.date_range(start, periods=n, freq=f"{step_min}min", tz="UTC")
    inst = {}
    for j, s in enumerate(syms):
        r = rng.normal(drift, 0.002, n); c = 100 * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]]
        wick = np.abs(rng.normal(0, 0.001, n)) * c
        h = np.maximum(o, c) + wick; l = np.minimum(o, c) - wick
        d = pd.DataFrame({"ts": grid, "bucket": grid - pd.Timedelta(minutes=step_min), "o": o, "h": h, "l": l, "c": c, "v": 1.0, "n": 1.0})
        d["exec_px"] = np.append(c[1:], np.nan) if kind == "sampled" else np.append(o[1:], np.nan)
        d["exec_ts"] = pd.to_datetime(np.append(grid[1:].values, np.datetime64("NaT")), utc=True) if kind == "sampled" else grid
        d["gap_before_min"] = float(step_min); d["gap_after_min"] = float(step_min); d["valid"] = True; d["exec_valid"] = np.isfinite(d.exec_px)
        fl = _session_flags(pd.Series(grid), "us_regular", 90, 45)
        for col in fl.columns: d[col] = fl[col].values
        inst[s] = d
    return Panel(kind, grid, inst, step_min)


def _patch_specs():
    bt.SPECS.update({"A": Spec("A", 3, 20), "B": Spec("B", 3, 20)})


def test_no_future_information():
    """Signals up to bar k must be identical when all prices after bar k are perturbed."""
    _patch_specs()
    p1 = synth_panel(); p2 = synth_panel()
    K = 500
    for s in p2.symbols:
        d = p2.inst[s]; d.loc[K + 1:, ["o", "h", "l", "c"]] *= 1.05; d.loc[K:, "exec_px"] = d.loc[K:, "exec_px"] * 1.05
    for strat in [MATrend(tf="15m", fast=10, slow=40), MATrend(tf="1h", fast=5, slow=15), ChannelBO(tf="1h", n=12)]:
        st1, st2 = strat.prepare(p1), strat.prepare(p2)
        for s in p1.symbols:
            for k in range(K + 1):
                a, b = strat.signal(s, k, st1), strat.signal(s, k, st2)
                assert (a is None) == (b is None), (strat.describe(), s, k)
                if a: assert abs(a.stop - b.stop) < 1e-9 and a.dir == b.dir
    print("test_no_future_information ok")


def test_hourly_alignment_completed_only():
    p = synth_panel(n=200, start="2026-06-01 13:30")
    v = tf_view(p, "A", "1h", "session")
    d = p.inst["A"]
    for j, idx in enumerate(v["idx"]):
        b = d.bucket.iloc[idx].tz_convert(ET)
        assert (b.hour * 60 + b.minute - 570) % 60 == 45, "1h bar must complete on the 4th 15-min slot after 09:30 ET"
        k0 = idx - 3
        assert abs(v["o"][j] - d.o.iloc[k0]) < 1e-9 and abs(v["c"][j] - d.c.iloc[idx]) < 1e-9
        assert abs(v["h"][j] - d.h.iloc[k0: idx + 1].max()) < 1e-9
    # map[k] never points to a bar completing after k
    for k in range(len(d)):
        m = v["map"][k]
        if m >= 0: assert v["idx"][m] <= k
    # a gap (invalid bar) must prevent completion
    d.loc[50, "valid"] = False; v2 = tf_view(p, "A", "1h", "session")
    assert not any(v2["idx"] == d.index[d.bucket.dt.tz_convert(ET).dt.hour.eq(d.bucket.iloc[50].tz_convert(ET).hour) & (d.bucket.dt.date == d.bucket.iloc[50].date())].max())
    print("test_hourly_alignment_completed_only ok")


def test_session_calendar():
    assert session_for_date(dt.date(2026, 7, 3)) is None            # Independence Day observed
    assert session_for_date(dt.date(2026, 9, 7)) is None            # Labor Day
    assert session_for_date(dt.date(2026, 9, 5)) is None            # Saturday
    o, c = session_for_date(dt.date(2026, 7, 6)); assert o.hour == 13 and o.minute == 30 and c.hour == 20   # EDT: 09:30 ET = 13:30 UTC
    o, c = session_for_date(dt.date(2026, 12, 15)); assert o.hour == 14 and c.hour == 21                     # EST
    o, c = session_for_date(dt.date(2026, 11, 27)); assert c.hour == 18                                      # early close 13:00 EST = 18:00 UTC
    ts = pd.Series(pd.to_datetime(["2026-07-06 13:29", "2026-07-06 13:30", "2026-07-06 18:30", "2026-07-06 18:31", "2026-07-06 19:15", "2026-07-06 19:59", "2026-07-06 20:00"], utc=True))
    f = annotate(ts, 90, 45)
    assert list(f.in_session) == [False, True, True, True, True, True, False]
    assert list(f.can_enter) == [False, True, True, False, False, False, False]
    assert list(f.must_exit) == [True, False, False, False, True, True, True]
    print("test_session_calendar ok")


def test_sizing_and_rounding():
    cm = CostModel("t", 0.00009, 1.0, 1.0, 2.0)
    spec = Spec("X", 3, 20)
    qty, why = size_position(200.0, 198.0, spec, cm, 100.0, 1.0, 2.0)   # stop 1% -> raw qty ~ 1/(2+costs)
    assert why == "ok" and qty <= 1.0 / 2.0 and qty * 200 <= 200 and round(qty, 3) == qty
    qty, why = size_position(200.0, 199.9, spec, cm, 100.0, 1.0, 2.0)   # 5 bp stop -> leverage cap binds: 200/200 = 1.0
    assert why == "ok" and qty == 1.0
    qty, why = size_position(5000.0, 4990.0, Spec("Y", 3, 20), cm, 100.0, 1.0, 2.0)  # 0.1 units -> $500 > 2x100 -> cap 0.04
    assert why == "ok" and abs(qty - 0.04) < 1e-9
    qty, why = size_position(0.5, 0.4999, Spec("Z", 0, 20), cm, 100.0, 1.0, 2.0)   # rounds to 400 units -> ok; with szDecimals 0 and price 100000: rounds to zero
    qty, why = size_position(100000.0, 99000.0, Spec("W", 0, 20), cm, 100.0, 1.0, 2.0); assert why == "rounds_to_zero"
    qty, why = size_position(100.0, 50.0, Spec("V", 3, 20), cm, 100.0, 1.0, 2.0); assert why == "below_min_order"   # qty 0.019 -> $1.9
    assert round_price(216.7371, 3) == 216.74 and round_price(1520.86, 3) == 1520.9 and round_price(0.012345678, 0) == 0.012346 and round_price(88035.6, 5) == 88036.0
    assert round_size(0.99999, 3) == 0.999
    print("test_sizing_and_rounding ok")


class OneShot(Base):
    """Enters long at bar k0 with given stop/target; used to test sequencing and costs."""
    name = "oneshot"; params = {"k0": 100, "stop_bps": 100, "target_bps": 100, "dir": 1}
    def signal(self, s, k, st):
        if k == self.params["k0"] and s == "A":
            d = st["panel"].inst[s]; c = d.c.iat[k]; dr = self.params["dir"]
            return Signal(dr, c * (1 - dr * self.params["stop_bps"] / 1e4), c * (1 + dr * self.params["target_bps"] / 1e4), "oneshot", {})
        return None


def test_stop_target_sequencing_and_costs():
    _patch_specs()
    p = synth_panel(n=300, seed=3)
    d = p.inst["A"]; k0 = 100
    # craft bar k0+1 (entry bar) so both stop and target are touched -> stop must win
    px = d.c.iat[k0]; d.loc[k0 + 1, ["o", "h", "l", "c"]] = [px, px * 1.02, px * 0.98, px]; d.loc[k0, "exec_px"] = px
    for s in p.symbols: p.inst[s].loc[:, ["in_session", "can_enter"]] = True; p.inst[s].loc[:, "must_exit"] = False
    r = run(p, OneShot(k0=k0), cost_regime="zero")
    t = r["trades"]; assert len(t) == 1 and t.reason.iat[0] == "stop" and abs(t.exit_px.iat[0] - t.stop.iat[0]) < 1e-9
    # gap through the stop: open below stop -> fill at open
    d.loc[k0 + 1, ["o", "h", "l", "c"]] = [px * 0.97, px * 0.975, px * 0.96, px * 0.97]
    r = run(p, OneShot(k0=k0), cost_regime="zero"); t = r["trades"]; assert t.reason.iat[0] == "stop" and abs(t.exit_px.iat[0] - px * 0.97) < 1e-9
    # costs: with base costs the net pnl equals gross - fees - spread/slip - adverse stop penalty
    d.loc[k0 + 1, ["o", "h", "l", "c"]] = [px, px * 1.02, px * 0.98, px]
    cm = CostModel("t", 0.0001, 1.0, 1.0, 2.0)
    bt.base_model = lambda s, regime="base": cm
    r = run(p, OneShot(k0=k0), cost_regime="base"); t = r["trades"].iloc[0]
    entry_eff = px * (1 + 2e-4); exit_eff = t.stop * (1 - 2e-4 - 2e-4)
    expected = (exit_eff - entry_eff) * t.qty - 1e-4 * entry_eff * t.qty - 1e-4 * exit_eff * t.qty
    assert abs(t.net_pnl - expected) < 1e-9, (t.net_pnl, expected)
    # funding: long pays positive funding accrued between entry and exit
    fund = {"A": pd.DataFrame({"ts": [p.grid[k0 + 1] + pd.Timedelta(minutes=1)], "rate": [0.001]})}
    d.loc[k0 + 1, ["o", "h", "l", "c"]] = [px, px * 1.001, px * 0.999, px]   # no exit on entry bar
    d.loc[k0 + 2, ["o", "h", "l", "c"]] = [px, px * 1.02, px * 1.0, px * 1.015]  # target hit next bar
    r = run(p, OneShot(k0=k0), cost_regime="base", funding=fund); t = r["trades"].iloc[0]
    assert t.reason == "target" and abs(t.funding - (-0.001 * t.qty * t.entry_px)) < 1e-12
    print("test_stop_target_sequencing_and_costs ok")


def test_single_position_and_drawdown_pause():
    _patch_specs()
    p = synth_panel(n=600, seed=5)
    for s in p.symbols: p.inst[s].loc[:, ["in_session", "can_enter"]] = True; p.inst[s].loc[:, "must_exit"] = False
    class Always(Base):
        name = "always"; params = {}
        def signal(self, s, k, st):
            d = st["panel"].inst[s]; c = d.c.iat[k]
            return Signal(1, c * 0.995, c * 1.001, "always", {})   # tiny target, 50 bp stop -> mostly losers over time? we only check overlap
    r = run(p, Always(), cost_regime="zero")
    t = r["trades"].sort_values("entry_ts")
    # no overlapping positions
    for i in range(1, len(t)):
        assert pd.Timestamp(t.entry_ts.iat[i]) >= pd.Timestamp(t.exit_ts.iat[i - 1]), "overlapping positions"
    # drawdown pause: force losses via a losing strategy
    class Loser(Base):
        name = "loser"; params = {}
        def signal(self, s, k, st):
            d = st["panel"].inst[s]; c = d.c.iat[k]
            return Signal(1, c * 0.999, None, "loser", {})       # 10 bp stop: risk $1 each, will be stopped often
    p2 = synth_panel(n=1500, seed=9, drift=-0.001)
    for s in p2.symbols: p2.inst[s].loc[:, ["in_session", "can_enter"]] = True; p2.inst[s].loc[:, "must_exit"] = False
    r = run(p2, Loser(), cost_regime="zero")
    eq = r["equity"]; t = r["trades"]
    assert eq.paused.any(), "pause never triggered"
    first_pause = eq.ts[eq.paused].iloc[0]
    counted = t[t.counted]; shadow = t[~t.counted]
    assert len(shadow) > 0 and (pd.to_datetime(shadow.signal_ts, utc=True) >= first_pause).all()
    assert abs(r["account"].equity - (100 + counted.net_pnl.sum())) < 1e-9
    assert abs(r["account"].shadow_equity - (100 + t.net_pnl.sum())) < 1e-9
    assert r["account"].equity <= r["account"].hwm - 10 + 1.5   # paused close to the trigger (one open trade may close after)
    print("test_single_position_and_drawdown_pause ok")


if __name__ == "__main__":
    test_no_future_information(); test_hourly_alignment_completed_only(); test_session_calendar(); test_sizing_and_rounding()
    test_stop_target_sequencing_and_costs(); test_single_position_and_drawdown_pause(); print("ALL TESTS PASSED")
