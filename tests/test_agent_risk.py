import os
import pytest
from conftest import mk_state, strong
from hlagent.risk import RiskLayer, RiskLimits
from hlagent.schema import RiskState


def layer(tmp_path, **over):
    return RiskLayer(RiskLimits(**over), str(tmp_path))


def test_kill_file_blocks_every_order(tmp_path):
    r = layer(tmp_path)
    open(r.kill_path, "w").write("manual\n")
    v = r.check_order(mk_state(), 500.0)
    assert not v.allowed and v.killed and r.code_risk_state(mk_state()) == RiskState.reduce


def test_drawdown_limit_kills_and_persists(tmp_path):
    r = layer(tmp_path)
    v = r.check_order(mk_state(drawdown_pct=15.0), 500.0)
    assert not v.allowed and v.killed and os.path.exists(r.kill_path)
    assert "drawdown" in open(r.kill_path).read()
    assert not r.check_order(mk_state(), 100.0).allowed       # stays killed until the file is removed
    os.remove(r.kill_path)
    assert r.check_order(mk_state(), 100.0).allowed


def test_daily_loss_limit_kills(tmp_path):
    r = layer(tmp_path)
    v = r.check_order(mk_state(daily_pnl_pct=-3.0), 500.0)
    assert not v.allowed and v.killed


def test_reduce_only_always_allowed(tmp_path):
    r = layer(tmp_path)
    st = mk_state(pos_units=5.0, pos_notional_usd=500.0, pos_frac=0.05, staleness_ms=99_999)
    assert r.check_order(st, 0.0).allowed
    assert r.check_order(st, 200.0).allowed
    assert not r.check_order(st, 800.0).allowed              # adding while stale is blocked


def test_clip_to_per_market_limit(tmp_path):
    r = layer(tmp_path)
    v = r.check_order(mk_state(equity_usd=10_000.0), 5000.0)
    assert v.allowed and v.target_notional == pytest.approx(1000.0) and "per-market" in v.reasons[0]
    v = r.check_order(mk_state(equity_usd=10_000.0), -5000.0)
    assert v.target_notional == pytest.approx(-1000.0)


def test_clip_to_gross_limit(tmp_path):
    r = layer(tmp_path)
    v = r.check_order(mk_state(equity_usd=10_000.0, gross_other_usd=2500.0), 1000.0)
    assert v.allowed and v.target_notional == pytest.approx(500.0) and any("gross" in x for x in v.reasons)
    v = r.check_order(mk_state(equity_usd=10_000.0, gross_other_usd=3000.0), 1000.0)
    assert not v.allowed


def test_code_risk_state_transitions(tmp_path):
    r = layer(tmp_path)
    assert r.code_risk_state(mk_state()) == RiskState.safe
    assert r.code_risk_state(mk_state(drawdown_pct=7.5)) == RiskState.near_limit
    assert r.code_risk_state(mk_state(pos_frac=0.08)) == RiskState.near_limit
    assert r.code_risk_state(mk_state(pos_frac=0.11)) == RiskState.reduce
    assert r.code_risk_state(mk_state(staleness_ms=6000)) == RiskState.reduce
    assert r.code_risk_state(mk_state(daily_pnl_pct=-1.5)) == RiskState.near_limit


def test_should_escalate(tmp_path):
    r = layer(tmp_path)
    st = mk_state()
    assert r.should_escalate(strong(confidence=0.59), st)
    assert r.should_escalate(strong(regime="crisis"), st)
    assert not r.should_escalate(strong(confidence=0.7), st)


def test_consecutive_errors_kill(tmp_path):
    r = layer(tmp_path, max_consecutive_errors=3)
    r.record_error(); r.record_error(); r.record_ok(); r.record_error(); r.record_error()
    assert not r.is_killed()
    r.record_error()
    assert r.is_killed()


def test_limits_roundtrip(tmp_path):
    p = tmp_path / "risk.json"
    RiskLimits(max_drawdown_pct=9.0).to_json(str(p))
    assert RiskLimits.from_json(str(p)).max_drawdown_pct == 9.0
