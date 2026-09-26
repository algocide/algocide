"""Executors: turn a signed target notional into fills.

  * PaperExecutor - simulated fills at the touch plus impact and taker fee; funding accrued hourly from the asset
                    context; full account accounting (cash, positions, equity, peak, day start). Never sends orders.
  * LiveExecutor  - thin wrapper over the official hyperliquid-python-sdk `Exchange`. DISARMED BY DEFAULT: it refuses
                    to construct unless HLAGENT_LIVE=1, HL_PRIVATE_KEY is set, an ARMED file exists in the output
                    directory, no KILLED file exists, and a positive max_live_notional is given. UNTESTED against the
                    live API in this session (network policy); the first run must be on testnet with a tiny size.
"""
from __future__ import annotations
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol
from .schema import AccountState, AssetCtx, L2Snapshot


@dataclass
class Fill:
    ts_ms: int
    coin: str
    units: float        # signed
    px: float
    fee_usd: float
    realised_usd: float
    action: str         # open | close | flip
    note: str = ""


@dataclass
class Position:
    coin: str
    units: float = 0.0
    entry_px: float = 0.0


@dataclass
class ExecReport:
    ok: bool
    fills: list[Fill] = field(default_factory=list)
    note: str = ""


class Executor(Protocol):
    def account(self, coin: str, ts_ms: int) -> AccountState: ...
    def mark(self, coin: str, l2: L2Snapshot) -> None: ...
    def target_position(self, coin: str, target_notional: float, l2: L2Snapshot, ts_ms: int) -> ExecReport: ...
    def flatten(self, coin: str, l2: L2Snapshot, ts_ms: int, note: str = "") -> ExecReport: ...
    def accrue_funding(self, coin: str, ctx: AssetCtx, ts_ms: int) -> float: ...


def _touch(l2: L2Snapshot) -> tuple[float, float]:
    bid = max(x.px for x in l2.bids)
    ask = min(x.px for x in l2.asks)
    return bid, ask


class PaperExecutor:
    def __init__(self, initial_equity: float = 10_000.0, fee_rate: float = 0.00045, impact_bps: float = 0.5,
                 min_notional: float = 10.0):
        self.cash = float(initial_equity)
        self.fee_rate = fee_rate
        self.impact = impact_bps / 1e4
        self.min_notional = min_notional
        self.pos: dict[str, Position] = {}
        self.mids: dict[str, float] = {}
        self.fills: list[Fill] = []
        self.funding_paid: list[dict] = []
        self.peak_equity = float(initial_equity)
        self.day_start_equity = float(initial_equity)
        self._day: Optional[int] = None
        self._last_funding_hour: dict[str, int] = {}

    # ---- accounting
    def equity(self) -> float:
        eq = self.cash
        for c, p in self.pos.items():
            if p.units != 0 and c in self.mids:
                eq += p.units * (self.mids[c] - p.entry_px)
        return eq

    def gross_notional(self) -> float:
        return sum(abs(p.units) * self.mids.get(c, p.entry_px) for c, p in self.pos.items())

    def roll(self, ts_ms: int) -> None:
        day = ts_ms // 86_400_000
        eq = self.equity()
        if self._day is None or day != self._day:
            self._day = day
            self.day_start_equity = eq
        self.peak_equity = max(self.peak_equity, eq)

    def mark(self, coin: str, l2: L2Snapshot) -> None:
        bid, ask = _touch(l2)
        self.mids[coin] = 0.5 * (bid + ask)

    def account(self, coin: str, ts_ms: int) -> AccountState:
        self.roll(ts_ms)
        p = self.pos.get(coin, Position(coin))
        other = self.gross_notional() - abs(p.units) * self.mids.get(coin, p.entry_px)
        return AccountState(ts_ms=ts_ms, equity_usd=self.equity(), peak_equity_usd=self.peak_equity,
                            day_start_equity_usd=self.day_start_equity, position_units=p.units, entry_px=p.entry_px,
                            gross_notional_usd=max(0.0, other))

    # ---- fills
    def _fill(self, coin: str, units: float, l2: L2Snapshot, ts_ms: int, note: str) -> Optional[Fill]:
        if units == 0:
            return None
        bid, ask = _touch(l2)
        px = ask * (1 + self.impact) if units > 0 else bid * (1 - self.impact)
        p = self.pos.setdefault(coin, Position(coin))
        fee = abs(units) * px * self.fee_rate
        realised = 0.0
        action = "open"
        if p.units != 0 and units * p.units < 0:            # closing some or all
            closing = -math.copysign(min(abs(units), abs(p.units)), p.units)
            realised = -closing * (px - p.entry_px)          # closing units are opposite in sign to the position
            p.units += closing
            remainder = units - closing
            action = "close"
            if abs(p.units) < 1e-12:
                p.units, p.entry_px = 0.0, 0.0
            if remainder != 0:                                # flipping
                p.units, p.entry_px = remainder, px
                action = "flip"
        else:                                                 # opening or adding
            tot = abs(p.units) + abs(units)
            p.entry_px = (p.entry_px * abs(p.units) + px * abs(units)) / tot
            p.units += units
        self.cash += realised - fee
        f = Fill(ts_ms=ts_ms, coin=coin, units=units, px=px, fee_usd=fee, realised_usd=realised, action=action, note=note)
        self.fills.append(f)
        self.mids[coin] = 0.5 * (bid + ask)
        return f

    def target_position(self, coin: str, target_notional: float, l2: L2Snapshot, ts_ms: int) -> ExecReport:
        self.mark(coin, l2)
        mid = self.mids[coin]
        p = self.pos.get(coin, Position(coin))
        current = p.units * mid
        delta = target_notional - current
        if abs(delta) < self.min_notional:
            return ExecReport(True, [], "no-op: delta below min notional")
        f = self._fill(coin, delta / mid, l2, ts_ms, note=f"target {target_notional:.2f}")
        return ExecReport(True, [f] if f else [], "filled")

    def flatten(self, coin: str, l2: L2Snapshot, ts_ms: int, note: str = "flatten") -> ExecReport:
        self.mark(coin, l2)
        p = self.pos.get(coin)
        if p is None or p.units == 0:
            return ExecReport(True, [], "flat already")
        f = self._fill(coin, -p.units, l2, ts_ms, note=note)
        return ExecReport(True, [f] if f else [], "flattened")

    def accrue_funding(self, coin: str, ctx: AssetCtx, ts_ms: int) -> float:
        """Hourly: longs pay when funding > 0 (Hyperliquid pays on oracle notional each hour)."""
        hour = ts_ms // 3_600_000
        last = self._last_funding_hour.get(coin)
        self._last_funding_hour[coin] = hour
        p = self.pos.get(coin)
        if last is None or hour <= last or p is None or p.units == 0:
            return 0.0
        pay = -p.units * ctx.oracle_px * ctx.funding_1h * (hour - last)
        self.cash += pay
        self.funding_paid.append({"ts_ms": ts_ms, "coin": coin, "rate": ctx.funding_1h, "usd": pay})
        return pay


class LiveExecutor:
    """Order path through the official SDK. See module docstring for the arming conditions."""

    def __init__(self, out_dir: str, max_live_notional: float = 0.0, base_url: Optional[str] = None,
                 slippage: float = 0.002, arm_file: str = "ARMED", kill_file: str = "KILLED"):
        if os.environ.get("HLAGENT_LIVE") != "1":
            raise RuntimeError("LiveExecutor disarmed: set HLAGENT_LIVE=1 to allow construction")
        if not os.path.exists(os.path.join(out_dir, arm_file)):
            raise RuntimeError(f"LiveExecutor disarmed: create {os.path.join(out_dir, arm_file)} to arm")
        if os.path.exists(os.path.join(out_dir, kill_file)):
            raise RuntimeError("LiveExecutor refused: kill switch file present")
        if max_live_notional <= 0:
            raise RuntimeError("LiveExecutor refused: max_live_notional must be > 0")
        key = os.environ.get("HL_PRIVATE_KEY")
        if not key:
            raise RuntimeError("LiveExecutor refused: HL_PRIVATE_KEY not set (use an API/agent wallet, never the main key)")
        try:
            from eth_account import Account                      # dependency of hyperliquid-python-sdk
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
            from hyperliquid.utils import constants
        except ImportError as e:  # pragma: no cover - optional dependency
            raise RuntimeError("install hyperliquid-python-sdk (requirements-live.txt)") from e
        self.base_url = base_url or constants.TESTNET_API_URL   # testnet unless told otherwise
        self.wallet = Account.from_key(key)
        self.exchange = Exchange(self.wallet, self.base_url)
        self.info = Info(self.base_url, skip_ws=True)
        self.max_live_notional = max_live_notional
        self.slippage = slippage
        self.out_dir = out_dir
        self.kill_path = os.path.join(out_dir, kill_file)
        self._sz_decimals = {a["name"]: int(a["szDecimals"]) for a in self.info.meta()["universe"]}

    def _round_sz(self, coin: str, sz: float) -> float:
        d = self._sz_decimals.get(coin, 4)
        return math.floor(abs(sz) * 10 ** d) / 10 ** d

    def _guard(self, notional: float) -> None:
        if os.path.exists(self.kill_path):
            raise RuntimeError("kill switch engaged; live order refused")
        if abs(notional) > self.max_live_notional:
            raise RuntimeError(f"live notional {notional:.2f} exceeds max_live_notional {self.max_live_notional:.2f}")

    def account(self, coin: str, ts_ms: int) -> AccountState:
        st = self.info.user_state(self.wallet.address)
        eq = float(st["marginSummary"]["accountValue"])
        units, entry, gross = 0.0, 0.0, 0.0
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            gross += abs(float(p.get("positionValue", 0.0)))
            if p["coin"] == coin:
                units = float(p["szi"]); entry = float(p.get("entryPx") or 0.0)
        # peak / day-start must be persisted by the loop for live accounts; here we report equity for both
        return AccountState(ts_ms=ts_ms, equity_usd=eq, peak_equity_usd=eq, day_start_equity_usd=eq,
                            position_units=units, entry_px=entry, gross_notional_usd=gross)

    def mark(self, coin: str, l2: L2Snapshot) -> None:
        return None

    def target_position(self, coin: str, target_notional: float, l2: L2Snapshot, ts_ms: int) -> ExecReport:
        self._guard(target_notional)
        bid, ask = _touch(l2)
        mid = 0.5 * (bid + ask)
        acct = self.account(coin, ts_ms)
        delta = target_notional - acct.position_units * mid
        sz = self._round_sz(coin, delta / mid)
        if sz <= 0:
            return ExecReport(True, [], "no-op")
        resp = self.exchange.market_open(coin, delta > 0, sz, None, self.slippage)
        return ExecReport(True, [], f"market_open sent: {str(resp)[:200]}")

    def flatten(self, coin: str, l2: L2Snapshot, ts_ms: int, note: str = "") -> ExecReport:
        if os.path.exists(self.kill_path):
            pass  # flattening is always allowed, even when killed
        resp = self.exchange.market_close(coin, None, None, self.slippage)
        return ExecReport(True, [], f"market_close sent: {str(resp)[:200]}")

    def accrue_funding(self, coin: str, ctx: AssetCtx, ts_ms: int) -> float:
        return 0.0   # the venue settles funding on the account itself
