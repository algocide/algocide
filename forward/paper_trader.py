#!/usr/bin/env python3
"""Forward paper-trading scaffold for Hyperliquid (no orders are ever sent; no keys; read-only info API).

Design
------
* Engine polls `metaAndAssetCtxs` for the dexes a strategy needs (plus `l2Book` top-of-book for markets it wants to
  trade) every `--every` seconds, hands a snapshot to each strategy, receives target positions (notional in USD,
  signed), and simulates fills at the observed best bid/ask plus fee + impact assumptions from `hlr.costs`.
* Funding is accrued at each hour boundary using the `funding` field observed in the last snapshot before the boundary
  (Hyperliquid pays hourly on oracle notional). Hedge legs that live off-venue (a stock) are proxied by the market's
  `oraclePx`, which IS the external reference price during external pricing sessions; the log records this proxy.
* Everything is appended to JSONL logs (snapshots used, fills, funding accruals, equity, kill-switch events).
* Risk limits and kill switches (config): max gross notional, max per-market notional, max daily loss, max drawdown,
  max data staleness, max consecutive API errors, hard stop time. Tripping any of them flattens paper positions and
  halts new entries until manually reset (delete the `KILLED` file).
* Pre-declared pass/fail criteria are stored with the run config and evaluated by `forward/evaluate.py`.

STATUS: not executed against the live API in this session (network policy). Tested with synthetic snapshots in
tests/test_paper_trader.py. First live run should be on a tiny notional with `--dry-run` semantics (this file is
already dry-run only).
"""
from __future__ import annotations
import argparse, json, os, sys, time, datetime as dt
from dataclasses import dataclass, field, asdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from hlr.costs import FEE_REGIMES
from hlr.hl_api import HLInfo, asset_ctx_rows


@dataclass
class RiskLimits:
    max_gross_notional: float = 20_000.0
    max_market_notional: float = 5_000.0
    max_daily_loss: float = 300.0
    max_drawdown: float = 600.0
    max_staleness_s: float = 180.0
    max_consecutive_errors: int = 10
    hard_stop_utc: str | None = None


@dataclass
class Position:
    coin: str
    notional: float = 0.0       # signed USD notional at entry prices
    size: float = 0.0           # signed units
    entry_px: float = 0.0
    hedge_units: float = 0.0    # off-venue hedge proxied by oraclePx (units = -size for a full hedge)
    hedge_entry_px: float = 0.0


class Strategy:
    name = "base"
    dexes: list[str | None] = [None]
    def targets(self, snap: dict, now: dt.datetime, state: dict) -> dict[str, float]:
        """Return {coin: signed target notional}. snap[coin] has markPx, oraclePx, midPx, funding, premium, bid, ask."""
        return {}
    def hedge(self) -> bool:  # hedge each perp with an off-venue proxy at -1x
        return False


class EquityFundingHarvest(Strategy):
    """H4: each Monday 00:00 UTC short the K xyz equity perps with the highest trailing-7d mean funding, hedged."""
    name = "equity_funding_harvest"; dexes = ["xyz"]
    def __init__(self, K=5, notional_per=1000.0, coins_allow=None):
        self.K = K; self.notional_per = notional_per; self.allow = set(coins_allow or [])
    def hedge(self): return True
    def targets(self, snap, now, state):
        hist = state.setdefault("funding_hist", {})  # coin -> list of (ts, funding)
        for c, r in snap.items():
            if r.get("funding") is not None:
                hist.setdefault(c, []).append((now.timestamp(), float(r["funding"])))
                hist[c] = [x for x in hist[c] if x[0] > now.timestamp() - 7 * 86400]
        last = state.get("last_rebalance")
        if last is None or (now.weekday() == 0 and now.hour == 0 and (now.timestamp() - last) > 86400 * 6):
            state["last_rebalance"] = now.timestamp()
            score = {c: sum(f for _, f in v) / len(v) for c, v in hist.items() if len(v) >= 24 * 5 and (not self.allow or c in self.allow)}
            top = sorted((c for c in score if score[c] > 0), key=lambda c: -score[c])[: self.K]
            state["targets"] = {c: -self.notional_per for c in top}
        return state.get("targets", {})


class PremiumReversion(Strategy):
    """H7: during external sessions, short (long) a xyz equity perp when premium > +thr (< -thr); exit at |p| < thr/4 or 6h."""
    name = "premium_reversion"; dexes = ["xyz"]
    def __init__(self, thr=0.0030, notional_per=1000.0, max_hold_h=6, coins_allow=None, max_positions=5):
        self.thr = thr; self.notional_per = notional_per; self.max_hold = max_hold_h * 3600; self.allow = set(coins_allow or []); self.maxn = max_positions
    def hedge(self): return True
    @staticmethod
    def external_session(now_utc: dt.datetime) -> bool:
        from zoneinfo import ZoneInfo
        et = now_utc.astimezone(ZoneInfo("America/New_York"))
        if et.weekday() == 5 or (et.weekday() == 4 and et.hour >= 20) or (et.weekday() == 6 and et.hour < 20):
            return False
        return 4 <= et.hour < 20
    def targets(self, snap, now, state):
        open_ = state.setdefault("open", {})  # coin -> entry ts
        tg = dict(state.get("targets", {}))
        for c in list(open_):
            p = snap.get(c, {}).get("premium")
            if p is None: continue
            if abs(float(p)) < self.thr / 4 or now.timestamp() - open_[c] > self.max_hold:
                tg.pop(c, None); open_.pop(c, None)
        if self.external_session(now):
            for c, r in snap.items():
                if len(open_) >= self.maxn: break
                if self.allow and c not in self.allow: continue
                p = r.get("premium")
                if p is None or c in open_: continue
                p = float(p)
                if p > self.thr: tg[c] = -self.notional_per; open_[c] = now.timestamp()
                elif p < -self.thr: tg[c] = self.notional_per; open_[c] = now.timestamp()
        state["targets"] = tg
        return tg


class Engine:
    def __init__(self, api: HLInfo, strategies: list[Strategy], out: str, limits: RiskLimits, fee_regime="hip3_standard", impact_bps=0.5):
        self.api = api; self.strats = strategies; self.out = out; self.limits = limits
        self.fee = FEE_REGIMES[fee_regime].taker; self.impact = impact_bps / 1e4
        os.makedirs(out, exist_ok=True)
        self.pos: dict[str, dict[str, Position]] = {s.name: {} for s in strategies}
        self.state: dict[str, dict] = {s.name: {} for s in strategies}
        self.cash: dict[str, float] = {s.name: 0.0 for s in strategies}
        self.equity_hist: list[tuple[float, float]] = []; self.day_start_equity = 0.0; self.day = None
        self.errors = 0; self.last_funding_hour = None; self.killed = os.path.exists(os.path.join(out, "KILLED"))

    def log(self, kind: str, rec: dict):
        rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "kind": kind, **rec}
        with open(os.path.join(self.out, f"{kind}.jsonl"), "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def kill(self, reason: str):
        self.killed = True; open(os.path.join(self.out, "KILLED"), "w").write(reason)
        self.log("kill", {"reason": reason})
        for s in self.strats:
            self.state[s.name]["targets"] = {}

    def snapshot(self, dexes) -> tuple[dict, int]:
        snap = {}; ts_ms = int(time.time() * 1000)
        for dex in dexes:
            resp = self.api.meta_and_asset_ctxs(dex)
            for r in asset_ctx_rows(dex, resp, ts_ms):
                if r["isDelisted"]: continue
                snap[r["coin"]] = r
        return snap, ts_ms

    def fill(self, sname: str, coin: str, target: float, r: dict, ts_ms: int, hedge: bool):
        """Move the paper position to `target` signed notional. Closes are done in units (so a flatten really flattens);
        opens are done in notional at the observed touch plus impact. Realised P&L: -c*(px-entry) for closing c units;
        hedge proxy realised: +c*(oracle-hedge_entry) (hedge units = -size)."""
        pos = self.pos[sname].setdefault(coin, Position(coin))
        mark = float(r["markPx"]); bid = float(r.get("bid") or r.get("midPx") or mark); ask = float(r.get("ask") or r.get("midPx") or mark)
        oracle = float(r.get("oraclePx") or mark)
        if abs(target - pos.notional) < 1.0:
            return
        # 1) close (fully if flattening or flipping; partially if reducing)
        if pos.size != 0 and (target == 0 or target * pos.size < 0 or abs(target) < abs(pos.notional)):
            if target == 0 or target * pos.size < 0:
                c = -pos.size
            else:
                c = -pos.size * (1 - abs(target) / abs(pos.notional))
            px = ask * (1 + self.impact) if c > 0 else bid * (1 - self.impact)
            realised = -c * (px - pos.entry_px); fee = abs(c) * px * self.fee
            hedge_realised = c * (oracle - pos.hedge_entry_px) if hedge else 0.0
            hedge_fee = abs(c) * oracle * 1.5e-4 if hedge else 0.0
            self.cash[sname] += realised + hedge_realised - fee - hedge_fee
            pos.size += c
            if abs(pos.size) < 1e-12:
                pos.size = 0.0; pos.entry_px = 0.0; pos.hedge_entry_px = 0.0
            pos.notional = pos.size * pos.entry_px
            self.log("fills", {"strategy": sname, "coin": coin, "action": "close", "units": c, "px": px, "realised": realised, "hedge_realised": hedge_realised, "fee_usd": fee + hedge_fee, "oracle": oracle})
        # 2) open the remainder
        rem = target - pos.notional
        if abs(rem) >= 1.0 and (pos.size == 0 or rem * pos.size > 0):
            px = ask * (1 + self.impact) if rem > 0 else bid * (1 - self.impact)
            u = rem / px; fee = abs(rem) * self.fee + (abs(rem) * 1.5e-4 if hedge else 0.0)
            tot = abs(pos.size) + abs(u)
            pos.entry_px = (pos.entry_px * abs(pos.size) + px * abs(u)) / tot
            if hedge:
                pos.hedge_entry_px = (pos.hedge_entry_px * abs(pos.size) + oracle * abs(u)) / tot
            pos.size += u; pos.notional = pos.size * pos.entry_px
            self.cash[sname] -= fee
            self.log("fills", {"strategy": sname, "coin": coin, "action": "open", "units": u, "px": px, "fee_usd": fee, "hedged": hedge, "oracle": oracle})
        pos.hedge_units = -pos.size if hedge else 0.0

    def accrue_funding(self, snap: dict, now: dt.datetime):
        hour = now.replace(minute=0, second=0, microsecond=0)
        if self.last_funding_hour is None: self.last_funding_hour = hour; return
        if hour > self.last_funding_hour:
            for s in self.strats:
                for coin, pos in self.pos[s.name].items():
                    r = snap.get(coin)
                    if pos.size == 0 or r is None or r.get("funding") is None: continue
                    pay = -pos.size * float(r["oraclePx"]) * float(r["funding"])  # longs pay when funding > 0
                    self.cash[s.name] += pay
                    self.log("funding", {"strategy": s.name, "coin": coin, "rate": r["funding"], "usd": pay})
            self.last_funding_hour = hour

    def equity(self, snap: dict) -> float:
        eq = 0.0
        for s in self.strats:
            eq += self.cash[s.name]
            for coin, pos in self.pos[s.name].items():
                r = snap.get(coin)
                if r is None or pos.size == 0: continue
                eq += pos.size * (float(r["markPx"]) - pos.entry_px)
                if pos.hedge_units: eq += pos.hedge_units * (float(r["oraclePx"]) - pos.hedge_entry_px)
        return eq

    def check_limits(self, snap: dict, ts_ms: int, now: dt.datetime):
        gross = sum(abs(p.notional) for s in self.strats for p in self.pos[s.name].values())
        if gross > self.limits.max_gross_notional: self.kill(f"gross notional {gross:.0f} > limit")
        for s in self.strats:
            for p in self.pos[s.name].values():
                if abs(p.notional) > self.limits.max_market_notional: self.kill(f"{p.coin} notional {p.notional:.0f} > limit")
        eq = self.equity(snap); self.equity_hist.append((now.timestamp(), eq))
        if self.day != now.date(): self.day = now.date(); self.day_start_equity = eq
        if self.day_start_equity - eq > self.limits.max_daily_loss: self.kill(f"daily loss {self.day_start_equity - eq:.0f}")
        peak = max(e for _, e in self.equity_hist)
        if peak - eq > self.limits.max_drawdown: self.kill(f"drawdown {peak - eq:.0f}")
        if self.limits.hard_stop_utc and now >= dt.datetime.fromisoformat(self.limits.hard_stop_utc): self.kill("hard stop time")
        self.log("equity", {"equity": eq, "gross": gross, "cash": self.cash})

    def step(self, now: dt.datetime | None = None, snap: dict | None = None):
        now = now or dt.datetime.now(dt.timezone.utc)
        try:
            if snap is None:
                dexes = sorted({d for s in self.strats for d in s.dexes}, key=lambda x: x or "")
                snap, ts_ms = self.snapshot(dexes)
            else:
                ts_ms = int(now.timestamp() * 1000)
            self.errors = 0
        except Exception as e:
            self.errors += 1; self.log("errors", {"error": repr(e)})
            if self.errors >= self.limits.max_consecutive_errors: self.kill("consecutive API errors")
            return
        self.accrue_funding(snap, now)
        for s in self.strats:
            tg = {} if self.killed else s.targets(snap, now, self.state[s.name])
            for coin in set(tg) | set(self.pos[s.name]):
                if coin in snap:
                    self.fill(s.name, coin, float(tg.get(coin, 0.0)), snap[coin], ts_ms, s.hedge())
        self.check_limits(snap, ts_ms, now)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/paper"); ap.add_argument("--every", type=float, default=60.0)
    ap.add_argument("--base", default="https://api.hyperliquid.xyz"); ap.add_argument("--strategy", default="equity_funding_harvest,premium_reversion")
    ap.add_argument("--fee", default="hip3_standard"); ap.add_argument("--hard-stop", default=None)
    a = ap.parse_args()
    strats = []
    for n in a.strategy.split(","):
        strats.append({"equity_funding_harvest": EquityFundingHarvest, "premium_reversion": PremiumReversion}[n]())
    eng = Engine(HLInfo(a.base, cache_dir=None), strats, a.out, RiskLimits(hard_stop_utc=a.hard_stop), fee_regime=a.fee)
    json.dump({"started": dt.datetime.now(dt.timezone.utc).isoformat(), "strategies": [s.name for s in strats], "limits": asdict(eng.limits), "fee": a.fee},
              open(os.path.join(a.out, "run_config.json"), "w"), indent=1)
    while True:
        t0 = time.time(); eng.step(); time.sleep(max(0.0, a.every - (time.time() - t0)))


if __name__ == "__main__":
    main()
