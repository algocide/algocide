import json
import pytest
from conftest import mk_state, strong
from hlagent.policy import KELLY_HARD_CAP, PolicyConfig, gate, kelly_fraction, target_notional
from hlagent.schema import RiskState

CFG = PolicyConfig()


def test_gate_passes_for_strong_safe_decision():
    g = gate(strong(), RiskState.safe, CFG, mk_state())
    assert g.fire and g.reasons == ()


@pytest.mark.parametrize("dec, code_rs, state_over, invalid, needle", [
    (strong(direction="neutral"), RiskState.safe, {}, False, "neutral"),
    (strong(quality=1), RiskState.safe, {}, False, "setup_quality"),
    (strong(confidence=0.80), RiskState.safe, {}, False, "confidence"),
    (strong(toxic=True), RiskState.safe, {}, False, "toxic"),
    (strong(risk_state="near_limit"), RiskState.safe, {}, False, "judge risk_state"),
    (strong(), RiskState.reduce, {}, False, "code risk_state"),
    (strong(), RiskState.safe, {"spread_bps": 6.0}, False, "spread"),
    (strong(), RiskState.safe, {"staleness_ms": 6000}, False, "stale"),
    (strong(), RiskState.safe, {}, True, "invalidated"),
])
def test_gate_blocks_each_condition(dec, code_rs, state_over, invalid, needle):
    g = gate(dec, code_rs, CFG, mk_state(**state_over), setup_invalidated=invalid)
    assert not g.fire and any(needle in r for r in g.reasons)


def test_judge_risk_state_can_only_add_caution():
    # judge says safe, code says reduce -> blocked; judge says reduce, code says safe -> blocked
    assert not gate(strong(risk_state="safe"), RiskState.reduce, CFG).fire
    assert not gate(strong(risk_state="reduce"), RiskState.safe, CFG).fire


def test_kelly_math_and_hard_cap():
    assert kelly_fraction(0.9, 1.0, 0.25) == pytest.approx(0.2)
    assert kelly_fraction(0.5, 1.0, 0.25) == 0.0
    assert kelly_fraction(0.3, 1.0, 0.25) == 0.0
    assert kelly_fraction(0.9, 1.0, 0.5) == pytest.approx(0.25 * 0.8)   # requested half Kelly, capped at quarter
    assert kelly_fraction(0.9, 2.0, 0.25) == pytest.approx(0.25 * (0.9 - 0.05))
    with pytest.raises(ValueError):
        kelly_fraction(0.9, 0.0, 0.25)


def test_target_notional_caps_at_position_fraction():
    st = mk_state(equity_usd=10_000.0)
    assert target_notional(strong(confidence=0.9), st, CFG) == pytest.approx(1000.0)      # min(0.2, 0.10) * equity
    assert target_notional(strong(confidence=0.9, direction="short"), st, CFG) == pytest.approx(-1000.0)
    assert target_notional(strong(confidence=0.85), st, PolicyConfig(max_position_frac=0.5)) == pytest.approx(0.25 * 0.7 * 10_000)
    assert target_notional(strong(direction="neutral"), st, CFG) == 0.0


def test_policy_config_validation_and_tunables(tmp_path):
    with pytest.raises(ValueError):
        PolicyConfig(kelly_fraction=0.5)
    with pytest.raises(ValueError):
        PolicyConfig(min_confidence=0.3)
    with pytest.raises(ValueError):
        PolicyConfig(min_setup_quality=5)
    with pytest.raises(ValueError):
        CFG.with_updates(max_position_frac=0.9)
    new = CFG.with_updates(min_confidence=0.85, payoff_ratio=1.3)
    assert new.min_confidence == 0.85 and new.payoff_ratio == 1.3 and CFG.min_confidence == 0.80
    p = tmp_path / "policy.json"
    new.to_json(str(p))
    assert PolicyConfig.from_json(str(p)) == new
    assert json.load(open(p))["kelly_fraction"] <= KELLY_HARD_CAP
