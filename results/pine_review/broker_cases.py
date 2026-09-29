#!/usr/bin/env python3
"""Task 2: minimal Pine cases that probe the broker emulator (src/pinebt/broker.py) where it may differ from
TradingView. Each case prints what the engine did and what TradingView's documented behaviour implies.
Synthetic bars (and synthetic 1-minute candles for the magnifier cases); nothing under src/ is modified.

Usage: PYTHONPATH=src python3 results/pine_review/broker_cases.py
"""
import os, sys
import numpy as np

sys.path.insert(0, "src")
from pinebt import runtime as rt
from pinebt.data import Bars
from pinebt.engine import CompiledScript, Runner, SecurityHub, tf_cfg
from pinebt.broker import Broker, Magnifier

H1 = 3_600_000
T0 = 1_700_000_000_000 // H1 * H1


def bars_from(closes, opens=None, highs=None, lows=None, tf=H1, funding=None):
    c = np.asarray(closes, float)
    o = np.asarray(opens if opens is not None else np.r_[c[0], c[:-1]], float)
    h = np.asarray(highs if highs is not None else np.maximum(o, c), float)
    l = np.asarray(lows if lows is not None else np.minimum(o, c), float)
    t = T0 + np.arange(len(c)) * tf
    arrs = [t, o, h, l, c, np.ones(len(c))]
    if funding is not None:
        arrs.append(funding)
    return Bars("BTCUSDT", tf, arrays=arrs)


def run(src, bars, fee=0.0, minutes=None):
    cs = CompiledScript(src)
    r = Runner(cs, bars.symbol, bars.tf_ms, fee=fee)
    bk = Broker(bars, cs.cfg, fee)
    if minutes is not None:
        bk.mag = Magnifier(*minutes, bars.T, bars.TC)
    hub = SecurityHub(r, bars.symbol, bars.tf_ms, bars)
    step = cs.build(rt, bars, bk, hub, tf_cfg(bars.symbol, bars.tf_ms))
    for i in range(bars.n):
        bk.begin_bar(i)
        step(i)
        bk.end_bar(i)
    return bk


def show(bk):
    out = []
    for c in bk.closed:
        out.append(f"closed {c[0]} entry bar {c[1]} @ {c[6]:.2f} -> exit bar {c[2]} @ {c[7]:.2f}, qty {c[5]:+.4f}")
    for t in bk.trades:
        out.append(f"open   {t.eid} entry bar {t.bar} @ {t.px:.2f}, qty {t.q:+.4f}")
    return "\n    ".join(out) if out else "(no trades)"


CASES = []


def case(f):
    CASES.append(f)
    return f


@case
def stop_limit_entry():
    """Buy stop-limit (stop 105 above the market, limit 106). TradingView: nothing happens until price reaches the
    stop (bar 4), then a buy limit at 106 fills at about 105. Engine: the limit leg alone is 'marketable' at the next
    open, so it buys at 100 on bar 1 - before and without the breakout."""
    closes = [100, 100, 100, 100, 107, 107]
    highs = [100, 100.5, 100.5, 100.5, 107.5, 107.5]
    lows = [100, 99.5, 99.5, 99.5, 99.9, 106.5]
    src = "//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long, stop=105, limit=106)\n"
    return show(run(src, bars_from(closes, highs=highs, lows=lows)))


@case
def stop_limit_entry_short():
    """Sell stop-limit for a breakdown (stop 95, limit 94). TradingView: waits for 95. Engine: sells at the next open."""
    closes = [100, 100, 100, 93, 93]
    lows = [100, 99.5, 99.5, 92.5, 92.5]
    highs = [100, 100.5, 100.5, 100.2, 93.5]
    src = "//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('S', strategy.short, stop=95, limit=94)\n"
    return show(run(src, bars_from(closes, highs=highs, lows=lows)))


@case
def close_with_script_qty():
    """Script enters with qty=2 (its own units) and takes half off with strategy.close(qty=1). The tournament resizes
    the entry to 100% of equity (100 units at price 100) but keeps the close's absolute qty: 1 unit (1%) is closed
    instead of half. Repeated every bar, the script's 'close the second half' also only takes 1 unit."""
    closes = [100, 100, 101, 102, 103, 104]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long, qty=2)\n"
           "if bar_index >= 2\n    strategy.close('L', qty=1)\n")
    return show(run(src, bars_from(closes)))


@case
def exit_with_script_qty():
    """strategy.exit(qty=1) intended as 'half of my 2 units': engine closes 1 unit of a 100-unit position."""
    closes = [100, 100, 100, 110, 110]
    highs = [100, 100, 100, 111, 111]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long, qty=2)\n"
           "strategy.exit('TP1', 'L', qty=1, limit=105)\n")
    return show(run(src, bars_from(closes, highs=highs)))


@case
def order_with_script_qty():
    """strategy.order('S', short, qty=2) used to flatten a 2-unit long: engine keeps 98 of 100 units long."""
    closes = [100, 100, 100, 100, 100]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.order('B', strategy.long, qty=2)\n"
           "if bar_index == 2\n    strategy.order('S', strategy.short, qty=2)\n")
    return show(run(src, bars_from(closes)))


@case
def exit_qty_per_trade_pyramid():
    """Two pyramided entries; one strategy.exit(qty=10) without from_entry. TradingView closes 10 units of the
    position; the engine creates the exit per trade and closes 10 units of each (20)."""
    closes = [100, 100, 100, 100, 110, 110]
    highs = [100, 100, 100, 100, 111, 111]
    src = ("//@version=5\nstrategy('t', pyramiding=2)\nif bar_index <= 1\n    strategy.entry('L' + str.tostring(bar_index), strategy.long)\n"
           "if bar_index == 2\n    strategy.exit('X', qty=10, limit=105)\n")
    return show(run(src, bars_from(closes, highs=highs)))


@case
def trailing_activation_from_pre_entry_price():
    """Buy limit at 95 fills on the falling leg of bar 1 (open 100 -> low 94); the trailing exit (activate at +3 = 98,
    offset 1) is activated by the leg's *start* (100, before the fill), so the trailing stop sits at 99 right away
    even though price never traded above 95 after the entry until later. With magnifier-off OHLC paths."""
    closes = [100, 96, 96, 96]
    opens = [100, 100, 96, 96]
    highs = [100, 100, 97, 97]
    lows = [100, 94, 95.5, 95.5]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long, limit=95)\n"
           "    strategy.exit('T', 'L', trail_points=30, trail_offset=10)\n")
    return show(run(src, bars_from(closes, opens, highs, lows)))


@case
def exit_with_from_entry_before_entry():
    """strategy.exit('X', 'L', limit=110) is called once while flat (bar 0); the entry 'L' is created on bar 2.
    Engine (ledger item 37 rule): the exit does not cover the later entry, so the 111 high is ignored.
    TradingView's reference says an exit generated before its entry fills waits for the entry."""
    closes = [100, 100, 100, 100, 100, 100]
    highs = [100, 100, 100, 100, 111, 100]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.exit('X', 'L', limit=110)\n"
           "if bar_index == 2\n    strategy.entry('L', strategy.long)\n")
    return show(run(src, bars_from(closes, highs=highs)))


@case
def poc_entry_and_exit_same_bar():
    """process_orders_on_close: entry at bar 1's close; a stop placed on the same bar must only act from bar 2."""
    closes = [100, 100, 90, 90]
    lows = [100, 80, 89, 89]
    src = ("//@version=5\nstrategy('t', process_orders_on_close=true)\nif bar_index == 1\n    strategy.entry('L', strategy.long)\n"
           "    strategy.exit('X', 'L', stop=95)\n")
    return show(run(src, bars_from(closes, lows=lows)))


@case
def reversal_fees():
    """Reversal pays two fees (close + open) at 7 bps each."""
    closes = [100, 100, 100, 100]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long)\n"
           "if bar_index == 1\n    strategy.entry('S', strategy.short)\n")
    bk = run(src, bars_from(closes), fee=0.0007)
    return show(bk) + f"\n    fees paid {bk.fees_paid:.4f} (expected 10000*0.0007 + 2*~9993*0.0007 ~ 20.99)"


@case
def funding_sign():
    """Long through a +0.1% funding bar pays 0.1% of notional; short receives it."""
    closes = [100] * 4
    fund = [0, 0, 0.001, 0]
    out = []
    for d in ("long", "short"):
        src = f"//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('X', strategy.{d})\n"
        bk = run(src, bars_from(closes, funding=fund))
        out.append(f"{d}: funding paid {bk.funding_paid:+.2f}, final equity {bk.eq_close[-1]:.2f}")
    return "\n    ".join(out)


@case
def close_then_entry_same_bar():
    """strategy.close('L') and strategy.entry('L') on the same bar: TradingView and engine both close and re-open
    at the next open (two fills)."""
    closes = [100, 100, 101, 102]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long)\n"
           "if bar_index == 1\n    strategy.close('L')\n    strategy.entry('L', strategy.long)\n")
    return show(run(src, bars_from(closes)))


@case
def limit_entry_touch():
    """A buy limit fills when the low merely touches it (TradingView's backtest rule; optimistic for limit fills)."""
    closes = [100, 100, 100]
    lows = [100, 95, 99]
    src = "//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long, limit=95)\n"
    return show(run(src, bars_from(closes, lows=lows)))


@case
def stale_levels_when_exit_turns_na():
    """strategy.exit re-issued each bar with levels from position_avg_price; when flat the call has na levels and is
    ignored, so the previous exit object (old levels) stays stored. The scope rule must keep it off the next position."""
    closes = [100, 100, 100, 130, 130, 130, 130, 130]
    highs = [100, 100, 100, 131, 130, 130, 131, 130]
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0 or bar_index == 4\n    strategy.entry('L', strategy.long)\n"
           "strategy.exit('X', 'L', limit=strategy.position_avg_price * 1.2)\n")
    return show(run(src, bars_from(closes, highs=highs)))


@case
def magnifier_marketable_exit_intrabar():
    """Stop entry fills mid-minute at 101; its stop-loss (100.5 - above? no: 101.5 via stop=high of bar 0 + 1) is
    already through the market right after the fill. With the magnifier it fills at the next minute's open."""
    rows = [(100.0, 100.0, 100.0, 100.0)] * 60 + [(100.0, 101.2, 100.0, 101.0)] + [(100.8, 100.9, 100.7, 100.8)] * 59
    t = T0 + np.arange(len(rows)) * 60_000
    o, h, l, c = (np.array(x, float) for x in zip(*rows))
    b1 = rows[60:]
    bars = bars_from([100.0, b1[-1][3]], opens=[100.0, b1[0][0]], highs=[100.0, max(r[1] for r in b1)],
                     lows=[100.0, min(r[2] for r in b1)])
    src = ("//@version=5\nstrategy('t')\nif bar_index == 0\n    strategy.entry('L', strategy.long, stop=101)\n"
           "    strategy.exit('X', 'L', stop=101.1)\n")
    return show(run(src, bars, minutes=(t, o, h, l, c)))


if __name__ == "__main__":
    for f in CASES:
        print(f"== {f.__name__}: {' '.join(f.__doc__.split())}")
        try:
            print("    " + f())
        except Exception as e:      # a case that crashes is itself a finding
            print(f"    ERROR {type(e).__name__}: {e}")
