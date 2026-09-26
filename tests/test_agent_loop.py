import json
import os
import pytest
from conftest import strong
from hlagent.datafeed import MultiFeed, ReplayFeed, SyntheticFeed
from hlagent.execution import PaperExecutor
from hlagent.judge import JevJudge, ReplayJudge
from hlagent.loop import ReflexLoop
from hlagent.policy import PolicyConfig
from hlagent.risk import RiskLayer, RiskLimits
from hlagent.schema import Candle, Decision

MS = 60_000


def _lines(out, kind):
    p = os.path.join(out, f"{kind}.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def _loop(tmp_path, judge, brain=None, policy=None, limits=None, feed=None, brain_sync=True):
    feed = feed or MultiFeed({"SYN": SyntheticFeed(coin="SYN", seed=1)})
    ex = PaperExecutor(initial_equity=10_000.0)
    risk = RiskLayer(limits or RiskLimits(), str(tmp_path))
    loop = ReflexLoop(feed, judge, ex, risk, policy or PolicyConfig(horizon_candles=5), None, str(tmp_path), ["SYN"], "1m",
                      brain=brain, escalation_cooldown_ms=3 * MS, tag="test", brain_sync=brain_sync)
    t = feed.feeds["SYN"].candles[-1].T_ms + 1 if isinstance(feed, MultiFeed) else feed.candles[149].T_ms + 1
    return loop, ex, risk, t


def test_entry_expiry_and_outcomes(tmp_path):
    script = [strong()] + [strong(direction="neutral", confidence=0.5, quality=0)] * 20
    loop, ex, risk, t = _loop(tmp_path, ReplayJudge(script))
    out = [loop.tick(t + i * MS) for i in range(12)]
    assert out[0][0]["action"] == "target" and out[0][0]["fills"] == 1
    assert ex.pos["SYN"].units > 0 or any(f.action == "close" for f in ex.fills)
    assert [o[0]["action"] for o in out[1:5]] == ["hold"] * 4
    assert out[5][0]["action"] == "flatten_expiry" and ex.pos["SYN"].units == 0.0
    decisions = _lines(str(tmp_path), "decisions")
    assert len(decisions) == 12 and all(d["synthetic"] for d in decisions)
    outcomes = _lines(str(tmp_path), "outcomes")
    assert len(outcomes) == 12 - 5 and all(o["y"] in (0, 1) for o in outcomes)
    assert outcomes[0]["p_up"] == pytest.approx(0.9) and outcomes[1]["p_up"] == 0.5
    assert os.path.exists(os.path.join(str(tmp_path), "run_config.json"))
    assert len(_lines(str(tmp_path), "fills")) == 2 and len(_lines(str(tmp_path), "orders")) == 1


def test_kill_switch_blocks_entries(tmp_path):
    loop, ex, risk, t = _loop(tmp_path, ReplayJudge(lambda s: strong()))
    risk.kill("test")
    for i in range(5):
        loop.tick(t + i * MS)
    assert ex.fills == [] and _lines(str(tmp_path), "orders") == []
    assert len(_lines(str(tmp_path), "decisions")) == 5     # shadow decisions still logged


class _FakeBrain:
    model = "fake"
    def __init__(self, action):
        self.action, self.calls = action, []
    def escalate(self, state, decision, recent, setup):
        from hlagent.brain import BrainVerdict
        self.calls.append((state.coin, decision.confidence, len(recent)))
        return BrainVerdict(action=self.action, rationale="test", confidence=0.7)


def test_escalation_flattens_and_pauses(tmp_path):
    brain = _FakeBrain("flatten")
    script = [strong(), strong(confidence=0.3, quality=1), strong(), strong()]
    loop, ex, risk, t = _loop(tmp_path, ReplayJudge(script), brain=brain)
    s0 = loop.tick(t)[0]; assert s0["action"] == "target"
    s1 = loop.tick(t + MS)[0]
    assert s1["action"] == "flatten_brain" and ex.pos["SYN"].units == 0.0 and brain.calls[0][1] == 0.3
    esc = _lines(str(tmp_path), "escalations")
    assert esc[0]["action"] == "flatten" and esc[0]["verdict"]["rationale"] == "test"
    s2 = loop.tick(t + 2 * MS)[0]
    assert s2["action"] == "hold" and "paused" in s2["reasons"]       # cooldown after escalation
    s3 = loop.tick(t + 4 * MS)[0]                                       # cooldown (3 candles) over
    assert s3["action"] == "target"


def test_escalation_without_brain_holds_and_resume_lifts_pause(tmp_path):
    loop, ex, risk, t = _loop(tmp_path, ReplayJudge([strong(confidence=0.3, quality=1), strong()]))
    s = loop.tick(t)[0]
    assert s["escalation"] == "hold" and ex.fills == []
    assert loop.tick(t + MS)[0]["action"] == "hold"
    brain = _FakeBrain("resume")
    loop2, ex2, risk2, t2 = _loop(tmp_path / "b", ReplayJudge([strong(confidence=0.3, quality=1), strong()]), brain=brain)
    assert loop2.tick(t2)[0]["escalation"] == "resume"
    assert loop2.tick(t2 + MS)[0]["action"] == "target"


def test_jev_error_path_never_trades(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    loop, ex, risk, t = _loop(tmp_path, JevJudge(api_key=None))
    for i in range(4):
        loop.tick(t + i * MS)
    ds = _lines(str(tmp_path), "decisions")
    assert all(d["decision"]["source"] == "jev_error" for d in ds) and ex.fills == []


def test_daily_loss_kill_flattens_position(tmp_path):
    closes = [100.0] * 150 + [50.0] * 30
    cs = [Candle(t_ms=i * MS, T_ms=(i + 1) * MS - 1, o=c, h=c, l=c, c=c, v=1.0) for i, c in enumerate(closes)]
    feed = MultiFeed({"SYN": ReplayFeed("SYN", cs)})
    loop, ex, risk, t = _loop(tmp_path, ReplayJudge([strong()] + [strong(direction="neutral", confidence=0.5, quality=0)] * 50),
                              feed=feed)
    t = cs[149].T_ms + 1
    assert loop.tick(t)[0]["action"] == "target"
    s = loop.tick(t + MS)[0]                                      # price halves: 10% position -> -5% equity
    assert s["action"] == "flatten_risk" and ex.pos["SYN"].units == 0.0 and risk.is_killed()
    ev = _lines(str(tmp_path), "events")
    assert any(l["event"] == "limit_breach" and "daily loss" in l["reason"] for l in ev)
    assert any(l["event"] == "flatten" and l["note"] == "kill switch" for l in ev)
    assert loop.tick(t + 2 * MS)[0]["action"] == "hold"           # killed: no re-entry


def test_causality_error_is_logged_not_fatal(tmp_path):
    class BadFeed:
        def snapshot(self, coin, now_ms, n=120):
            raise RuntimeError("api down")
        def close_at(self, coin, ts):
            return None
    ex = PaperExecutor(); risk = RiskLayer(RiskLimits(max_consecutive_errors=2), str(tmp_path))
    loop = ReflexLoop(BadFeed(), ReplayJudge([]), ex, risk, PolicyConfig(), None, str(tmp_path), ["SYN"])
    assert loop.tick(1)[0]["action"] == "feed_error" and not risk.is_killed()
    loop.tick(2)
    assert risk.is_killed()


def test_escalation_async_lands_on_a_later_tick(tmp_path):
    brain = _FakeBrain("flatten")
    script = [strong(), strong(confidence=0.3, quality=1)] + [strong(direction="neutral", confidence=0.5, quality=0)] * 5
    loop, ex, risk, t = _loop(tmp_path, ReplayJudge(script), brain=brain, brain_sync=False)
    assert loop.tick(t)[0]["action"] == "target"
    s1 = loop.tick(t + MS)[0]
    assert s1["escalation"] == "hold" and s1["action"] == "hold"          # dispatched, hold by default
    loop._pending_verdicts["SYN"].result(timeout=5)                        # worker finished
    s2 = loop.tick(t + 2 * MS)[0]
    assert s2["verdict"] == "flatten" and s2["action"] == "flatten_brain" and ex.pos["SYN"].units == 0.0
    esc = _lines(str(tmp_path), "escalations")
    assert [e["phase"] for e in esc] == ["dispatched", "verdict"]


def test_brain_exception_means_hold(tmp_path):
    class Boom:
        model = "boom"
        def escalate(self, *a):
            raise RuntimeError("api down")
    loop, ex, risk, t = _loop(tmp_path, ReplayJudge([strong(confidence=0.3, quality=1), strong()]), brain=Boom())
    assert loop.tick(t)[0]["escalation"] == "hold"
    assert loop.tick(t + MS)[0]["action"] == "hold" and ex.fills == []
    assert any(e["event"] == "brain_error" for e in _lines(str(tmp_path), "events"))
