import json
import os
import pytest
from hlagent.policy import PolicyConfig
from hlagent.review import apply_proposal, brier, reliability, review, write_review


def test_brier_reference_and_perfect():
    assert brier([(1.0, 1), (0.0, 0)]) == 0.0
    assert brier([(0.5, 1), (0.5, 0)]) == 0.25
    assert brier([]) is None


def test_reliability_is_directional():
    rows = [{"p_up": 0.9, "y": 1}, {"p_up": 0.1, "y": 0}, {"p_up": 0.1, "y": 1}, {"p_up": 0.65, "y": 1}]
    t = reliability(rows)
    top = [r for r in t if r["bin_lo"] == 0.9][0]
    assert top["n"] == 3 and top["obs_freq"] == pytest.approx(2 / 3) and top["mean_conf"] == pytest.approx(0.9)
    mid = [r for r in t if r["bin_lo"] == 0.6][0]
    assert mid["n"] == 1 and mid["obs_freq"] == 1.0


def _write(out, rows, synthetic=False):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "decisions.jsonl"), "w") as f:
        for i, r in enumerate(rows):
            f.write(json.dumps({"id": i, "decision": {}}) + "\n")
    with open(os.path.join(out, "outcomes.jsonl"), "w") as f:
        for i, (p, y) in enumerate(rows):
            f.write(json.dumps({"decision_id": i, "coin": "X", "ts_ms": i, "horizon_ts_ms": i + 1, "p_up": p, "y": y,
                                "ret_bps": 0.0, "direction": "long" if p >= 0.5 else "short", "source": "rule",
                                "regime": "trending", "confidence": max(p, 1 - p), "synthetic": synthetic}) + "\n")


def test_overconfident_bucket_proposes_tighter_gate_and_apply_gate(tmp_path):
    out = str(tmp_path / "run")
    rows = [(0.9, i % 2) for i in range(40)]          # claims 90%, realises 50%
    _write(out, rows)
    res = review(out, PolicyConfig(), date="2026-09-26")
    assert res.stats["hit_rate"] == 0.5 and res.stats["brier_skill"] < 0
    assert res.proposals["policy_updates"]["min_confidence"] == 0.85 and res.proposals["do_not_arm_live"]
    md, pj = write_review(res, str(tmp_path / "results"))
    assert os.path.exists(md) and os.path.exists(pj) and "Overnight review 2026-09-26" in open(md).read()
    policy_path = str(tmp_path / "policy.json")
    with pytest.raises(PermissionError):
        apply_proposal(pj, policy_path, approve=False)
    new = apply_proposal(pj, policy_path, approve=True)
    assert new.min_confidence == 0.85 and PolicyConfig.from_json(policy_path).min_confidence == 0.85
    bad = str(tmp_path / "bad.json")
    json.dump({"policy_updates": {"max_drawdown_pct": 50}}, open(bad, "w"))
    with pytest.raises(ValueError):
        apply_proposal(bad, policy_path, approve=True)
    bad2 = str(tmp_path / "bad2.json")
    json.dump({"policy_updates": {"kelly_fraction": 0.9}}, open(bad2, "w"))
    with pytest.raises(ValueError):
        apply_proposal(bad2, policy_path, approve=True)


def test_synthetic_outcomes_are_flagged_and_never_arm_live(tmp_path):
    out = str(tmp_path / "run")
    _write(out, [(0.9, 1)] * 300, synthetic=True)
    res = review(out, PolicyConfig(), date="2026-09-26")
    assert res.warnings and "SYNTHETIC" in res.warnings[0]
    assert res.proposals["do_not_arm_live"] and res.stats["brier"] == pytest.approx(0.01)


def test_empty_run_reviews_cleanly(tmp_path):
    res = review(str(tmp_path), PolicyConfig(), date="2026-09-26")
    assert res.stats["brier"] is None and "n/a" in res.markdown
