"""Tests for the Pine engine: parser, series semantics, indicators, broker fills, security mapping."""
import math
import os
import sys
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pinebt import runtime as rt
from pinebt.parser import parse, tokenize
from pinebt.data import Bars
from pinebt.engine import CompiledScript, Runner
from pinebt.broker import Broker

H1 = 3_600_000
T0 = 1_700_000_000_000 // H1 * H1


def bars_from(closes, opens=None, highs=None, lows=None, tf=H1, funding=None, symbol="BTCUSDT"):
    c = np.asarray(closes, float)
    o = np.asarray(opens if opens is not None else np.r_[c[0], c[:-1]], float)
    h = np.asarray(highs if highs is not None else np.maximum(o, c), float)
    l = np.asarray(lows if lows is not None else np.minimum(o, c), float)
    t = T0 + np.arange(len(c)) * tf
    arrs = [t, o, h, l, c, np.ones(len(c))]
    if funding is not None:
        arrs.append(funding)
    return Bars(symbol, tf, arrays=arrs)


def run_on(src, bars, fee=0.0):
    cs = CompiledScript(src)
    r = Runner(cs, bars.symbol, bars.tf_ms, fee=fee)
    bk = Broker(bars, cs.cfg, fee)
    from pinebt.engine import SecurityHub, tf_cfg
    hub = SecurityHub(r, bars.symbol, bars.tf_ms, bars)
    vals = {}
    step = cs.build(rt, bars, bk, hub, tf_cfg(bars.symbol, bars.tf_ms))
    for i in range(bars.n):
        bk.begin_bar(i)
        step(i)
        bk.end_bar(i)
    return bk, cs


# ---------------------------------------------------------------- parser
def test_parser_continuations_and_commas():
    src = "//@version=4\nstrategy('x')\na = 1, b = 2\nc = a +\n     b\nif a > 0\n    d = 3\nelse\n    d = 4\nf(x) => x * 2\n"
    stmts = parse(src)
    assert len(stmts) == 6


def test_parser_switch_and_tuple():
    src = "//@version=5\n[m, s, h] = ta.macd(close, 12, 26, 9)\nx = switch 'EMA'\n    'SMA' => 1\n    => 2\n"
    stmts = parse(src)
    assert type(stmts[0]).__name__ == "Decl" and stmts[0].names == ["m", "s", "h"]
    assert type(stmts[1].value).__name__ == "Switch"


# ---------------------------------------------------------------- series semantics
def probe(src_body, closes, version=5):
    """Run a script that records the value of `out` every bar through a strategy-free probe."""
    src = f"//@version={version}\nstrategy('p')\n" + src_body + "\n"
    cs = CompiledScript(src)
    code = cs.pysrc.replace("        return None", "        PROBE.append(g_out)")
    g = {}
    from pinebt import engine
    g.update(engine.EXEC_GLOBALS)
    rec = []
    g["PROBE"] = rec
    exec(compile(code, "<p>", "exec"), g)
    b = bars_from(closes)
    bk = Broker(b, cs.cfg, 0.0)
    step = g["build"](rt, b, bk, None, engine.tf_cfg("BTCUSDT", H1))
    for i in range(b.n):
        bk.begin_bar(i); step(i); bk.end_bar(i)
    return rec


def test_self_reference_history():
    out = probe("out = 0.0\nout := nz(out[1]) + 1", [1, 2, 3, 4])
    assert out == [1, 2, 3, 4]


def test_var_persists():
    out = probe("var out = 10\nout += 1", [1, 2, 3])
    assert out == [11, 12, 13]


def test_function_local_history_per_call_site():
    body = "f(x) =>\n    s = 0.0\n    s := nz(s[1]) + x\n    s\na = f(1)\nb = f(10)\nout = a + b"
    assert probe(body, [1, 1, 1]) == [11, 22, 33]


def test_close_history_and_na():
    out = probe("out = close[2]", [5, 6, 7, 8])
    assert math.isnan(out[0]) and math.isnan(out[1]) and out[2:] == [5, 6]


def test_v4_iff_and_const_int_division():
    assert probe("out = iff(close > 2, 1, 0) + 1/2", [1, 3], version=4) == [0, 1]
    assert probe("out = 1/2", [1], version=6) == [0.5]


# ---------------------------------------------------------------- indicators vs references
def test_indicators_match_reference():
    rng = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    s = pd.Series(c)
    sma = probe("out = ta.sma(close, 14)", c)
    assert np.allclose(sma[20:], s.rolling(14).mean().to_numpy()[20:])
    ema = probe("out = ta.ema(close, 10)", c)
    ref = [c[0]]
    for x in c[1:]:
        ref.append(2 / 11 * x + (1 - 2 / 11) * ref[-1])
    assert np.allclose(ema, ref)
    sd = probe("out = ta.stdev(close, 20)", c)
    assert np.allclose(sd[25:], s.rolling(20).std(ddof=0).to_numpy()[25:])
    rsi = probe("out = ta.rsi(close, 14)", c)
    d = s.diff()
    up = d.clip(lower=0); dn = (-d).clip(lower=0)
    def rma(x, n):
        x = x.to_numpy(); out = np.full(len(x), np.nan)
        first = n  # first valid change is index 1 -> seed at index n
        out[first] = np.mean(x[1:n + 1])
        for k in range(first + 1, len(x)):
            out[k] = (x[k] + (n - 1) * out[k - 1]) / n
        return out
    ref_rsi = 100 - 100 / (1 + rma(up, 14) / rma(dn, 14))
    assert np.allclose(rsi[20:], ref_rsi[20:])
    hi = probe("out = ta.highest(close, 5)", c)
    assert np.allclose(hi[10:], s.rolling(5).max().to_numpy()[10:])
    cross = probe("out = ta.crossover(close, ta.sma(close, 5)) ? 1 : 0", c)
    m = s.rolling(5).mean()
    ref_c = ((s > m) & (s.shift(1) <= m.shift(1))).astype(int).to_numpy()
    assert cross[10:] == list(ref_c[10:])


# ---------------------------------------------------------------- broker
def test_market_entry_fills_next_open_and_reverses():
    closes = [100, 101, 102, 103, 104, 105]
    opens = [100, 100.5, 101.5, 102.5, 103.5, 104.5]
    src = "//@version=5\nstrategy('t')\nif bar_index == 1\n    strategy.entry('L', strategy.long)\nif bar_index == 3\n    strategy.entry('S', strategy.short)\n"
    bk, _ = run_on(src, bars_from(closes, opens))
    assert len(bk.closed) == 1
    eid, eb, xb, et, xt, q, epx, xpx, net = bk.closed[0]
    assert eid == "L" and eb == 2 and xb == 4 and epx == 101.5 and xpx == 103.5
    assert bk.trades[0].q < 0 and bk.trades[0].px == 103.5
    assert abs(q - 10000 / 101.5) < 1e-6


def test_stop_and_limit_exits_with_path():
    # entry at bar 1 open 100; bar 2 dips to 95 first (open closer to low) then 110
    opens = [100, 100, 99, 100]
    closes = [100, 99, 108, 100]
    highs = [100, 100, 110, 101]
    lows = [100, 98, 95, 99]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long)\n"
           "strategy.exit('X', 'L', stop=96, limit=109)\n")
    bk, _ = run_on(src, bars_from(closes, opens, highs, lows))
    assert len(bk.closed) == 1 and bk.closed[0][7] == 96 and bk.closed[0][2] == 2


def test_profit_loss_ticks_and_gap():
    opens = [100, 100, 120, 120]
    closes = [100, 101, 121, 120]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long)\n"
           "strategy.exit('X', 'L', profit=50, loss=50)\n")
    b = bars_from(closes, opens)      # mintick 0.1 -> 5.0 price
    bk, _ = run_on(src, b)
    assert bk.closed[0][7] == 120      # gap above the 105 target fills at the open


def test_pyramiding_and_fee_and_funding():
    closes = [100] * 6
    fund = [0, 0, 0.001, 0, 0, 0]
    src = "//@version=5\nstrategy('t', pyramiding=2)\nstrategy.entry('L', strategy.long)\n"
    bk, _ = run_on(src, bars_from(closes, funding=fund), fee=0.001)
    assert len(bk.trades) == 2
    q1 = 0.5 * 10000 / 100
    assert abs(bk.trades[0].q - q1) < 1e-9
    total_q = sum(t.q for t in bk.trades)
    fees = bk.fees_paid
    assert abs(bk.funding_paid - total_q * 100 * 0.001) < 1e-9
    assert abs(bk.eq_close[-1] - (10000 - fees - bk.funding_paid)) < 1e-6


def test_close_by_id_partial_and_poc():
    closes = [100, 100, 100, 100, 100]
    src = ("//@version=5\nstrategy('t', process_orders_on_close=true)\nif bar_index == 0\n    strategy.entry('L', strategy.long)\n"
           "if bar_index == 2\n    strategy.close('L', qty_percent=50)\n")
    bk, _ = run_on(src, bars_from(closes))
    assert len(bk.closed) == 1 and bk.closed[0][1] == 0 and bk.closed[0][2] == 2
    assert abs(bk.trades[0].q - 50) < 1e-9


def test_security_uses_only_closed_htf_bars():
    # 48 hourly bars; daily close of day 0 must only be visible from the last hour of day 0 on
    c = np.arange(1, 49, dtype=float)
    t = (1_700_006_400_000 // 86_400_000) * 86_400_000
    arrs = [t + np.arange(48) * H1, c, c, c, c, np.ones(48)]
    b = Bars("BTCUSDT", H1, arrays=arrs)
    src = "//@version=5\nstrategy('t')\nd = request.security(syminfo.tickerid, 'D', close)\n"
    cs = CompiledScript(src)
    # build a runner whose shadow uses daily bars made from the same arrays
    from pinebt import engine
    class R(engine.Runner):
        def shadow(self, sk, tf_ms):
            days = Bars("BTCUSDT", 86_400_000, arrays=[[t, t + 86_400_000], [1, 25], [24, 48], [1, 25], [24, 48], [1, 1]])
            return {(0,): {0: 24.0, 1: 48.0}}, days.TC
    r = R(cs, "BTCUSDT", H1)
    hub = engine.SecurityHub(r, "BTCUSDT", H1, b)
    seen = [hub((0,), i, "BINANCE:BTCUSDT", "D", False, lambda: None) for i in range(48)]
    assert all(math.isnan(x) for x in seen[:23]) and seen[23] == 24.0 and seen[46] == 24.0 and seen[47] == 48.0


def test_two_partial_take_profits_use_original_qty():
    opens = [100, 100, 100, 100]
    closes = [100, 100, 104, 108]
    highs = [100, 100, 106, 112]
    lows = [100, 100, 99, 103]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long)\n"
           "strategy.exit('TP1', 'L', qty_percent=50, limit=105)\nstrategy.exit('TP2', 'L', qty_percent=50, limit=110)\n")
    bk, _ = run_on(src, bars_from(closes, opens, highs, lows))
    assert len(bk.closed) == 2 and not bk.trades
    assert abs(bk.closed[0][5] - 50) < 1e-9 and abs(bk.closed[1][5] - 50) < 1e-9
