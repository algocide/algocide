import json
import pytest
import requests
from conftest import mk_state, strong
from hlagent.judge import JevJudge, JevSchemaError, ReplayJudge, RuleJudge, parse_jev_payload
from hlagent.schema import Decision, Direction, Regime, RiskState


def test_parse_shape_with_probability_maps():
    payload = {"answers": {
        "regime": {"choice": "trending", "probabilities": {"trending": 0.7, "mean_reverting": 0.2, "high_vol": 0.05, "crisis": 0.05}},
        "direction": {"choice": "long", "probabilities": {"long": 0.85, "short": 0.10, "neutral": 0.05}},
        "toxic_flow": {"probability": 0.2},
        "setup_quality": {"score": 2.6},
        "risk_state": {"choice": "safe"}}}
    d = parse_jev_payload(payload, 42, 81.0)
    assert d.direction == Direction.long and d.confidence == pytest.approx(0.85) and d.setup_quality == 3
    assert d.regime == Regime.trending and not d.toxic_flow and d.risk_state == RiskState.safe
    assert d.source == "jev" and d.latency_ms == 81.0 and d.state_ts_ms == 42


def test_parse_flat_shape():
    payload = {"regime": "mean_reverting", "direction": {"value": "short", "confidence": 0.9}, "toxic_flow": True,
               "setup_quality": 1, "risk_state": "near_limit"}
    d = parse_jev_payload(payload, 1, 1.0)
    assert d.direction == Direction.short and d.confidence == 0.9 and d.toxic_flow and d.setup_quality == 1


def test_parse_refuses_to_invent_confidence():
    with pytest.raises(JevSchemaError):
        parse_jev_payload({"regime": "trending", "direction": "long", "toxic_flow": False, "setup_quality": 2, "risk_state": "safe"}, 1, 1.0)
    with pytest.raises(JevSchemaError):
        parse_jev_payload({"regime": "trending", "toxic_flow": False, "setup_quality": 2, "risk_state": "safe"}, 1, 1.0)


class _Resp:
    def __init__(self, code, payload):
        self.status_code, self._p, self.text = code, payload, json.dumps(payload)
    def json(self):
        return self._p


class _Session:
    def __init__(self, handler):
        self.h, self.calls = handler, []
    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self.h(json)


GOOD = {"answers": {"regime": "high_vol", "direction": {"choice": "short", "probabilities": {"short": 0.83, "long": 0.1, "neutral": 0.07}},
                    "toxic_flow": False, "setup_quality": 2, "risk_state": "safe"}}


def test_jev_judge_round_trip_with_fake_server():
    s = _Session(lambda body: _Resp(200, GOOD))
    j = JevJudge(api_key="k", session=s, timeout_s=0.1)
    d = j.judge(mk_state(), None)
    assert d.source == "jev" and d.direction == Direction.short and d.confidence == pytest.approx(0.83) and d.latency_ms > 0
    call = s.calls[0]
    assert call["url"].endswith("/v1/systemone") and call["headers"]["Authorization"] == "Bearer k"
    assert set(call["json"]) == {"model", "state", "questions"} and call["json"]["state"].startswith("coin=X")
    assert call["timeout"] == 0.1


def test_jev_judge_fails_closed_on_timeout_http_and_schema():
    def boom(_):
        raise requests.Timeout("slow")
    d = JevJudge(api_key="k", session=_Session(boom)).judge(mk_state(), None)
    assert d.direction == Direction.neutral and d.source == "jev_error" and d.confidence == 0.0
    d = JevJudge(api_key="k", session=_Session(lambda b: _Resp(500, {"error": "x"}))).judge(mk_state(), None)
    assert d.source == "jev_error"
    d = JevJudge(api_key="k", session=_Session(lambda b: _Resp(200, {"answers": {"regime": "trending"}}))).judge(mk_state(), None)
    assert d.source == "jev_error"


def test_jev_judge_without_key_never_calls(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    s = _Session(lambda b: _Resp(200, GOOD))
    d = JevJudge(api_key=None, session=s).judge(mk_state(), None)
    assert d.source == "jev_error" and s.calls == []


def test_rule_judge_is_capped_and_deterministic():
    st = mk_state(ret_20_bps=200.0, rv20_bps=10.0, rv100_bps=10.0, imbalance=0.3)   # trend z = 200/(10*sqrt(20)) = 4.5
    j = RuleJudge()
    a, b = j.judge(st, None), j.judge(st, None)
    assert a.direction == Direction.long and a.regime == Regime.trending
    assert a.confidence <= 0.75
    assert a.model_dump(exclude={"latency_ms"}) == b.model_dump(exclude={"latency_ms"})


def test_rule_judge_uses_measured_calibration_when_present():
    st = mk_state(ret_20_bps=200.0, rv20_bps=10.0, rv100_bps=10.0, imbalance=0.3)
    j = RuleJudge(calibration={"0.95": 0.62})
    assert j.judge(st, None).confidence == pytest.approx(0.62)


def test_rule_judge_regimes_and_toxicity():
    j = RuleJudge()
    assert j.judge(mk_state(rv_ratio=3.5), None).regime == Regime.crisis
    assert j.judge(mk_state(rv_ratio=2.0), None).regime == Regime.high_vol
    assert j.judge(mk_state(spread_bps=9.0), None).toxic_flow
    assert j.judge(mk_state(imbalance=0.95), None).toxic_flow


def test_replay_judge_sequence_then_neutral():
    j = ReplayJudge([strong(), strong(direction="short")])
    st = mk_state()
    assert j.judge(st, None).direction == Direction.long
    assert j.judge(st, None).direction == Direction.short
    assert j.judge(st, None).direction == Direction.neutral
    fn = ReplayJudge(lambda s: strong(confidence=0.7))
    assert fn.judge(st, None).confidence == 0.7 and fn.judge(st, None).source == "replay"
