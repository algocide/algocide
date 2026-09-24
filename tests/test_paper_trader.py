"""Synthetic-snapshot tests for the paper-trading engine (no network)."""
import os, sys, datetime as dt, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "forward")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from paper_trader import Engine, RiskLimits, Strategy, PremiumReversion


class OneShot(Strategy):
    name = "oneshot"; dexes = ["xyz"]
    def __init__(self): self.t = {"xyz:TSLA": -1000.0}
    def hedge(self): return True
    def targets(self, snap, now, state): return self.t


def snap(mark, oracle, funding=0.0001, premium=0.0):
    return {"xyz:TSLA": {"markPx": str(mark), "oraclePx": str(oracle), "midPx": str(mark), "bid": str(mark - 0.05), "ask": str(mark + 0.05),
                         "funding": str(funding), "premium": str(premium), "isDelisted": False}}


def test_short_hedged_funding_and_kill():
    out = tempfile.mkdtemp(); s = OneShot()
    eng = Engine(api=None, strategies=[s], out=out, limits=RiskLimits(max_daily_loss=50.0))
    t0 = dt.datetime(2026, 9, 28, 0, 0, tzinfo=dt.timezone.utc)
    eng.step(t0, snap(400.0, 400.0)); assert abs(eng.pos["oneshot"]["xyz:TSLA"].notional + 1000) < 1e-9
    fee_paid = -eng.cash["oneshot"]; assert 0.9 < fee_paid < 1.2   # 1000 x (0.09% + 0.015%) + impact
    # one hour later: funding accrues to the short (positive funding), hedged so mark move is neutral vs oracle
    eng.step(t0 + dt.timedelta(hours=1), snap(410.0, 410.0))
    lines = open(os.path.join(out, "funding.jsonl")).read().strip().splitlines(); assert len(lines) == 1
    eq = eng.equity(snap(410.0, 410.0)); assert abs(eq - (-fee_paid + 1000 / 400.0 * 400.0 * 0.0001 * -1 * -1)) < 0.3
    # a large adverse basis move trips the daily-loss kill switch and flattens
    eng.step(t0 + dt.timedelta(hours=2), snap(440.0, 410.0)); assert eng.killed
    eng.step(t0 + dt.timedelta(hours=3), snap(440.0, 410.0)); assert eng.pos["oneshot"]["xyz:TSLA"].size == 0


def test_premium_reversion_sessions():
    pr = PremiumReversion(thr=0.003)
    assert pr.external_session(dt.datetime(2026, 9, 29, 15, 0, tzinfo=dt.timezone.utc))      # Tue 11:00 ET
    assert not pr.external_session(dt.datetime(2026, 9, 27, 15, 0, tzinfo=dt.timezone.utc))  # Sunday
    st = {}; tg = pr.targets({"xyz:TSLA": {"premium": "0.005"}}, dt.datetime(2026, 9, 29, 15, 0, tzinfo=dt.timezone.utc), st)
    assert tg == {"xyz:TSLA": -1000.0}
    tg = pr.targets({"xyz:TSLA": {"premium": "0.0002"}}, dt.datetime(2026, 9, 29, 16, 0, tzinfo=dt.timezone.utc), st)
    assert tg == {}


if __name__ == "__main__":
    test_short_hedged_funding_and_kill(); test_premium_reversion_sessions(); print("ok")
