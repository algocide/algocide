import pytest
from pydantic import ValidationError
from conftest import mk_state
from hlagent.schema import (JEV_QUESTIONS, MAX_STATE_CHARS, Decision, Direction, L2Snapshot, RiskState, approx_tokens,
                            decision_json_schema)


def _d(**kw):
    base = dict(regime="trending", direction="long", toxic_flow=False, setup_quality=3, risk_state="safe", confidence=0.9)
    base.update(kw)
    return Decision(**base)


def test_decision_validation_bounds_and_extras():
    with pytest.raises(ValidationError):
        _d(setup_quality=4)
    with pytest.raises(ValidationError):
        _d(setup_quality=-1)
    with pytest.raises(ValidationError):
        _d(confidence=1.2)
    with pytest.raises(ValidationError):
        _d(regime="bullish")
    with pytest.raises(ValidationError):
        Decision(regime="trending", direction="long", toxic_flow=False, setup_quality=3, risk_state="safe", confidence=0.9, size=100)


def test_decision_is_frozen():
    d = _d()
    with pytest.raises(ValidationError):
        d.confidence = 0.1


def test_p_up_mapping():
    assert _d(direction="long", confidence=0.9).p_up() == pytest.approx(0.9)
    assert _d(direction="short", confidence=0.9).p_up() == pytest.approx(0.1)
    assert _d(direction="neutral", confidence=0.9).p_up() == 0.5


def test_neutral_fails_closed():
    n = Decision.neutral("x", 123, note="boom")
    assert n.direction == Direction.neutral and n.toxic_flow and n.risk_state == RiskState.reduce and n.confidence == 0.0
    assert n.state_ts_ms == 123 and n.note == "boom"


def test_state_prompt_budget_with_extreme_values():
    s = mk_state(mid=123456.789012, depth_bid_usd=9.87e9, depth_ask_usd=9.87e9, ret_20_bps=-12345.6, oi_usd=1.2e12,
                 pos_units=-123456.123456, pos_notional_usd=-9.9e9, equity_usd=1e9, gross_other_usd=9.9e9, staleness_ms=999_999_999)
    txt = s.to_prompt()
    assert len(txt) <= MAX_STATE_CHARS
    assert approx_tokens(txt) < 400
    assert txt.startswith("coin=X ts=1000000")


def test_state_prompt_is_order_stable():
    assert mk_state().to_prompt() == mk_state().to_prompt()


def test_l2_from_hl_parses_strings():
    resp = {"coin": "BTC", "time": 123, "levels": [[{"px": "100", "sz": "1", "n": 1}], [{"px": "101", "sz": "2", "n": 1}]]}
    l2 = L2Snapshot.from_hl("BTC", resp)
    assert l2.ts_ms == 123 and l2.bids[0].px == 100.0 and l2.asks[0].sz == 2.0


def test_jev_questions_cover_the_decision():
    assert set(JEV_QUESTIONS) == {"regime", "direction", "toxic_flow", "setup_quality", "risk_state"}
    assert JEV_QUESTIONS["direction"]["choices"] == ["long", "short", "neutral"]
    schema = decision_json_schema()
    assert schema["properties"]["setup_quality"]["maximum"] == 3
    assert "confidence" in schema["properties"]
