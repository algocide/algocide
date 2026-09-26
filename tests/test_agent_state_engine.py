import math
import pytest
from conftest import acct, book, candles, ctx
from hlagent.schema import L2Snapshot, Level
from hlagent.state_engine import BookError, CausalityError, build_state, completed_candles

MS = 60_000


def _inputs(n=30, now=None):
    closes = [100.0 * math.exp(0.001 * i) for i in range(n)]
    cs = candles(closes, t0=0, ms=MS)
    now = now if now is not None else cs[-1].T_ms + 1
    return now, book(now), cs, ctx(now), acct(now)


def test_future_l2_raises():
    now, l2, cs, c, a = _inputs()
    with pytest.raises(CausalityError):
        build_state(now, "X", book(now + 1), cs, c, a)


def test_future_ctx_and_account_raise():
    now, l2, cs, c, a = _inputs()
    with pytest.raises(CausalityError):
        build_state(now, "X", l2, cs, ctx(now + 5), a)
    with pytest.raises(CausalityError):
        build_state(now, "X", l2, cs, c, acct(now + 5))


def test_candle_opening_in_future_raises():
    now, l2, cs, c, a = _inputs()
    fut = candles([1.0], t0=now + 10, ms=MS)
    with pytest.raises(CausalityError):
        build_state(now, "X", l2, cs + fut, c, a)


def test_forming_candle_is_excluded():
    now, l2, cs, c, a = _inputs(n=30)
    mid_of_last = cs[-1].t_ms + MS // 2          # inside the last candle: it has not closed
    s = build_state(mid_of_last, "X", book(mid_of_last), cs, ctx(mid_of_last), acct(mid_of_last))
    assert s.n_candles == 29
    assert s.ret_1_bps == pytest.approx(10.0, rel=1e-6)     # log(c28/c27) = 0.001 -> 10 bps


def test_completed_candles_dedupes_and_sorts():
    cs = candles([1, 2, 3], t0=0, ms=MS)
    dup = list(reversed(cs)) + [cs[1]]
    out = completed_candles(dup, cs[-1].T_ms)
    assert [c.c for c in out] == [1, 2, 3]


def test_deterministic():
    now, l2, cs, c, a = _inputs()
    assert build_state(now, "X", l2, cs, c, a) == build_state(now, "X", l2, cs, c, a)


def test_features_known_values():
    now, l2, cs, c, a = _inputs(n=30)
    l2 = book(now, mid=100.0, spread_bps=2.0, bid_usd=3000.0, ask_usd=1000.0)
    a = acct(now, equity=9000.0, peak=10_000.0, day0=9500.0, pos=2.0, entry=90.0, gross=1234.0)
    s = build_state(now, "X", l2, cs, c, a)
    assert s.spread_bps == pytest.approx(2.0)
    assert s.imbalance == pytest.approx(0.5)
    assert s.ret_1_bps == pytest.approx(10.0, rel=1e-6)
    assert s.ret_5_bps == pytest.approx(50.0, rel=1e-6)
    assert s.ret_20_bps == pytest.approx(200.0, rel=1e-6)
    assert s.rv20_bps == pytest.approx(0.0, abs=1e-9)        # constant returns -> zero vol
    assert s.drawdown_pct == pytest.approx(10.0)
    assert s.daily_pnl_pct == pytest.approx(-500 / 9500 * 100)
    assert s.pos_notional_usd == pytest.approx(200.0)
    assert s.pos_frac == pytest.approx(200 / 9000)
    assert s.upnl_bps == pytest.approx((100 - 90) / 90 * 1e4)
    assert s.gross_other_usd == 1234.0
    assert s.funding_1h_bps == pytest.approx(0.1) and s.premium_bps == pytest.approx(2.0)
    assert s.oi_usd == pytest.approx(1000.0)
    assert s.staleness_ms == 0


def test_short_position_upnl_sign():
    now, l2, cs, c, a = _inputs()
    s = build_state(now, "X", l2, cs, c, acct(now, pos=-1.0, entry=110.0))
    assert s.upnl_bps > 0        # short from 110, mid 100 -> profit


def test_crossed_and_empty_book_raise():
    now, l2, cs, c, a = _inputs()
    crossed = L2Snapshot(ts_ms=now, coin="X", bids=[Level(px=101, sz=1)], asks=[Level(px=100, sz=1)])
    with pytest.raises(BookError):
        build_state(now, "X", crossed, cs, c, a)
    empty = L2Snapshot(ts_ms=now, coin="X", bids=[], asks=[Level(px=100, sz=1)])
    with pytest.raises(BookError):
        build_state(now, "X", empty, cs, c, a)


def test_staleness_is_age_of_freshest_input():
    now, l2, cs, c, a = _inputs()
    now2 = now + 5000                                   # the last candle closed 5 s ago
    s = build_state(now2, "X", book(now2 - 3000), cs, ctx(now2 - 2000), acct(now2 - 500))
    assert s.staleness_ms == 2000                       # account freshness does not count
    s = build_state(now, "X", book(now - 3000), cs, ctx(now - 2000), acct(now - 500))
    assert s.staleness_ms == 1                          # the freshest input is the candle that just closed


def test_rv_ratio_uses_long_window():
    closes = [100.0]
    for i in range(1, 130):
        closes.append(closes[-1] * math.exp((0.0005 if i < 110 else 0.005) * (1 if i % 2 else -1)))
    cs = candles(closes, t0=0, ms=MS)
    now = cs[-1].T_ms + 1
    s = build_state(now, "X", book(now), cs, ctx(now), acct(now))
    assert s.rv_ratio > 1.5
