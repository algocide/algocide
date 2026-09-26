"""Shared builders for the hlagent tests (plain functions so they can be imported explicitly)."""
import math
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from hlagent.schema import AccountState, AssetCtx, Candle, Decision, L2Snapshot, Level, StateVector


def book(ts, mid=100.0, spread_bps=2.0, bid_usd=1000.0, ask_usd=1000.0, coin="X"):
    half = mid * spread_bps / 2e4
    return L2Snapshot(ts_ms=ts, coin=coin, bids=[Level(px=mid - half, sz=bid_usd / (mid - half))],
                      asks=[Level(px=mid + half, sz=ask_usd / (mid + half))])


def candles(closes, t0=0, ms=60_000):
    return [Candle(t_ms=t0 + i * ms, T_ms=t0 + (i + 1) * ms - 1, o=c, h=c * 1.001, l=c * 0.999, c=c, v=1.0)
            for i, c in enumerate(closes)]


def ctx(ts, coin="X", mark=100.0, funding=1e-5, premium=2e-4):
    return AssetCtx(ts_ms=ts, coin=coin, mark_px=mark, oracle_px=mark, funding_1h=funding, premium=premium, open_interest=10.0)


def acct(ts, equity=10_000.0, peak=10_000.0, day0=10_000.0, pos=0.0, entry=0.0, gross=0.0):
    return AccountState(ts_ms=ts, equity_usd=equity, peak_equity_usd=peak, day_start_equity_usd=day0,
                        position_units=pos, entry_px=entry, gross_notional_usd=gross)


def mk_state(**over) -> StateVector:
    base = dict(ts_ms=1_000_000, coin="X", setup_id="", mid=100.0, spread_bps=2.0, imbalance=0.1, depth_bid_usd=1000.0,
                depth_ask_usd=900.0, ret_1_bps=5.0, ret_5_bps=20.0, ret_20_bps=60.0, rv20_bps=10.0, rv100_bps=10.0,
                rv_ratio=1.0, range_pos_20=0.7, funding_1h_bps=0.1, premium_bps=2.0, oi_usd=1e6, pos_units=0.0,
                pos_notional_usd=0.0, pos_frac=0.0, upnl_bps=0.0, equity_usd=10_000.0, gross_other_usd=0.0,
                drawdown_pct=0.0, daily_pnl_pct=0.0, staleness_ms=0, n_candles=120)
    base.update(over)
    return StateVector(**base)


def strong(direction="long", confidence=0.9, quality=3, regime="trending", risk_state="safe", toxic=False, **kw) -> Decision:
    return Decision(regime=regime, direction=direction, toxic_flow=toxic, setup_quality=quality, risk_state=risk_state,
                    confidence=confidence, source="test", **kw)
