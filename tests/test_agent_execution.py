import os
import pytest
from conftest import book, ctx
from hlagent.execution import LiveExecutor, PaperExecutor

T0 = 1_790_000_000_000


def test_paper_open_mark_close_accounting():
    ex = PaperExecutor(initial_equity=10_000.0, fee_rate=0.00045, impact_bps=0.5)
    b = book(T0, mid=100.0, spread_bps=2.0)
    rep = ex.target_position("X", 1000.0, b, T0)
    f = rep.fills[0]
    assert f.action == "open" and f.units == pytest.approx(10.0)
    assert f.px == pytest.approx(100.01 * (1 + 0.5e-4))
    assert f.fee_usd == pytest.approx(10 * f.px * 0.00045)
    assert ex.cash == pytest.approx(10_000 - f.fee_usd)
    ex.mark("X", b)
    assert ex.equity() == pytest.approx(ex.cash + 10 * (100.0 - f.px))
    ex.mark("X", book(T0 + 1, mid=110.0))
    assert ex.equity() == pytest.approx(ex.cash + 10 * (110.0 - f.px))
    rep = ex.flatten("X", book(T0 + 2, mid=110.0), T0 + 2)
    c = rep.fills[0]
    assert c.action == "close" and c.units == pytest.approx(-10.0)
    assert c.px == pytest.approx((110.0 - 110.0 * 2.0 / 2e4) * (1 - 0.5e-4))
    assert c.realised_usd == pytest.approx(10 * (c.px - f.px))
    assert ex.pos["X"].units == 0.0 and ex.equity() == pytest.approx(ex.cash)
    assert ex.equity() > 10_000 + 90


def test_paper_flip_in_one_fill():
    ex = PaperExecutor(initial_equity=10_000.0)
    b = book(T0, mid=100.0)
    ex.target_position("X", 1000.0, b, T0)
    rep = ex.target_position("X", -1000.0, b, T0 + 1)
    assert len(rep.fills) == 1 and rep.fills[0].action == "flip"
    assert ex.pos["X"].units == pytest.approx(-10.0, rel=1e-3)


def test_paper_no_op_below_min_notional():
    ex = PaperExecutor(initial_equity=10_000.0, min_notional=10.0)
    assert ex.target_position("X", 5.0, book(T0), T0).fills == []


def test_funding_sign_long_pays_when_positive():
    ex = PaperExecutor(initial_equity=10_000.0)
    ex.target_position("X", 1000.0, book(T0), T0)
    assert ex.accrue_funding("X", ctx(T0, funding=1e-4), T0) == 0.0         # first observation sets the clock
    pay = ex.accrue_funding("X", ctx(T0 + 3_600_000, funding=1e-4), T0 + 3_600_000)
    assert pay == pytest.approx(-10.0 * 100.0 * 1e-4, rel=1e-3)             # long pays
    assert ex.accrue_funding("X", ctx(T0 + 3_600_000 + 1000, funding=1e-4), T0 + 3_600_000 + 1000) == 0.0


def test_account_rolls_day_and_peak():
    ex = PaperExecutor(initial_equity=10_000.0)
    a = ex.account("X", T0)
    assert a.day_start_equity_usd == 10_000 and a.peak_equity_usd == 10_000
    ex.target_position("X", 1000.0, book(T0), T0)
    ex.mark("X", book(T0 + 1, mid=120.0))
    a = ex.account("X", T0 + 1)
    assert a.peak_equity_usd > 10_000 and a.position_units == pytest.approx(10.0) and a.day_start_equity_usd == 10_000
    a2 = ex.account("X", T0 + 86_400_000)
    assert a2.day_start_equity_usd == pytest.approx(a.equity_usd)


def test_live_executor_is_disarmed_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HLAGENT_LIVE", raising=False)
    with pytest.raises(RuntimeError, match="disarmed"):
        LiveExecutor(str(tmp_path), max_live_notional=100.0)
    monkeypatch.setenv("HLAGENT_LIVE", "1")
    with pytest.raises(RuntimeError, match="ARMED"):
        LiveExecutor(str(tmp_path), max_live_notional=100.0)
    open(tmp_path / "ARMED", "w").write("yes")
    open(tmp_path / "KILLED", "w").write("no")
    with pytest.raises(RuntimeError, match="kill switch"):
        LiveExecutor(str(tmp_path), max_live_notional=100.0)
    os.remove(tmp_path / "KILLED")
    with pytest.raises(RuntimeError, match="max_live_notional"):
        LiveExecutor(str(tmp_path), max_live_notional=0.0)
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="HL_PRIVATE_KEY"):
        LiveExecutor(str(tmp_path), max_live_notional=100.0)
