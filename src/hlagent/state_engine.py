"""Deterministic state engine: raw inputs -> one compact numeric StateVector.

Guarantees
  * Causality: every input carries a timestamp; anything stamped after the decision time raises CausalityError,
    and only candles whose close time is <= the decision time are used (a forming candle never leaks).
  * Determinism: pure functions, plain floats, no randomness, no I/O. Same inputs -> same state.
  * Budget: StateVector.to_prompt() is capped at schema.MAX_STATE_CHARS.
  * Staleness is the age of the freshest MARKET input (book, asset context, last closed candle); the account
    snapshot is excluded because executors stamp it at request time.
"""
from __future__ import annotations
import math
from .schema import AccountState, AssetCtx, Candle, L2Snapshot, StateVector


class CausalityError(ValueError):
    """An input is stamped after the decision time (a future leak)."""


class BookError(ValueError):
    """The order book is empty or crossed; no decision should be made on it."""


def _log_returns_bps(closes: list[float]) -> list[float]:
    out = []
    for a, b in zip(closes[:-1], closes[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a) * 1e4)
    return out


def _pstdev(x: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    m = sum(x) / n
    return math.sqrt(sum((v - m) ** 2 for v in x) / n)


def completed_candles(candles: list[Candle], now_ms: int) -> list[Candle]:
    """Only candles that have closed at or before now_ms, sorted, de-duplicated by open time."""
    for c in candles:
        if c.t_ms > now_ms:
            raise CausalityError(f"candle opening at {c.t_ms} is after decision time {now_ms}")
    by_open: dict[int, Candle] = {}
    for c in candles:
        if c.T_ms <= now_ms:
            by_open[c.t_ms] = c
    return [by_open[k] for k in sorted(by_open)]


def build_state(now_ms: int, coin: str, l2: L2Snapshot, candles: list[Candle], ctx: AssetCtx,
                account: AccountState, setup_id: str = "", n_short: int = 20, n_long: int = 100,
                depth_levels: int = 5) -> StateVector:
    for name, ts in (("l2", l2.ts_ms), ("ctx", ctx.ts_ms), ("account", account.ts_ms)):
        if ts > now_ms:
            raise CausalityError(f"{name} stamped {ts} is after decision time {now_ms}")
    closed = completed_candles(candles, now_ms)

    bids = sorted(l2.bids, key=lambda x: -x.px)[:depth_levels]
    asks = sorted(l2.asks, key=lambda x: x.px)[:depth_levels]
    if not bids or not asks:
        raise BookError("empty side of book")
    best_bid, best_ask = bids[0].px, asks[0].px
    if best_ask <= best_bid:
        raise BookError(f"crossed book bid {best_bid} >= ask {best_ask}")
    mid = 0.5 * (best_bid + best_ask)
    spread_bps = (best_ask - best_bid) / mid * 1e4
    depth_bid = sum(x.px * x.sz for x in bids)
    depth_ask = sum(x.px * x.sz for x in asks)
    imbalance = (depth_bid - depth_ask) / (depth_bid + depth_ask) if (depth_bid + depth_ask) > 0 else 0.0

    closes = [c.c for c in closed]
    rets = _log_returns_bps(closes)
    ret_1 = rets[-1] if rets else 0.0
    ret_5 = sum(rets[-5:]) if rets else 0.0
    ret_20 = sum(rets[-n_short:]) if rets else 0.0
    rv20 = _pstdev(rets[-n_short:])
    rv100 = _pstdev(rets[-n_long:]) if len(rets) >= n_short + 5 else 0.0
    rv_ratio = rv20 / rv100 if rv100 > 0 else 1.0
    last = closed[-n_short:]
    if last:
        hi, lo = max(c.h for c in last), min(c.l for c in last)
        range_pos = (mid - lo) / (hi - lo) if hi > lo else 0.5
        range_pos = min(1.0, max(0.0, range_pos))
    else:
        range_pos = 0.5

    pos_units = account.position_units
    pos_notional = pos_units * mid
    equity = account.equity_usd
    pos_frac = abs(pos_notional) / equity if equity > 0 else 0.0
    if pos_units != 0 and account.entry_px > 0:
        upnl_bps = (mid - account.entry_px) / account.entry_px * 1e4 * (1.0 if pos_units > 0 else -1.0)
    else:
        upnl_bps = 0.0
    peak = account.peak_equity_usd
    drawdown_pct = max(0.0, (peak - equity) / peak * 100.0) if peak > 0 else 0.0
    d0 = account.day_start_equity_usd
    daily_pnl_pct = (equity - d0) / d0 * 100.0 if d0 > 0 else 0.0

    latest_input = max(l2.ts_ms, ctx.ts_ms, closed[-1].T_ms if closed else 0)   # market inputs only
    staleness = max(0, now_ms - latest_input)

    return StateVector(
        ts_ms=now_ms, coin=coin, setup_id=setup_id,
        mid=mid, spread_bps=spread_bps, imbalance=imbalance, depth_bid_usd=depth_bid, depth_ask_usd=depth_ask,
        ret_1_bps=ret_1, ret_5_bps=ret_5, ret_20_bps=ret_20, rv20_bps=rv20, rv100_bps=rv100, rv_ratio=rv_ratio,
        range_pos_20=range_pos,
        funding_1h_bps=ctx.funding_1h * 1e4, premium_bps=ctx.premium * 1e4, oi_usd=ctx.open_interest * ctx.mark_px,
        pos_units=pos_units, pos_notional_usd=pos_notional, pos_frac=pos_frac, upnl_bps=upnl_bps,
        equity_usd=equity, gross_other_usd=max(0.0, account.gross_notional_usd), drawdown_pct=drawdown_pct, daily_pnl_pct=daily_pnl_pct,
        staleness_ms=int(staleness), n_candles=len(closed),
    )
