"""TradingView-style strategy broker emulator with the tournament's uniform overrides.

Timing (TradingView defaults): the script runs on bar close; market orders fill at the next bar's open (or at the
same bar's close with process_orders_on_close / immediately=true); stop and limit orders are checked intrabar along
the OHLC path open -> nearer extreme -> farther extreme -> close, gaps fill at the open. strategy.entry reverses an
opposite position and respects pyramiding; strategy.exit brackets (profit/limit, loss/stop, trailing) persist and
modify by id, apply to trades of `from_entry` (or all), and allocate qty_percent per trade.

Overrides (pre-registered): every opening order is sized at 100/pyramiding percent of current equity (explicit qty
ignored for openings), total exposure is capped at 1x equity, each fill costs `fee` (fraction of notional), funding is
charged at bar close from the per-bar funding sum.
"""
from __future__ import annotations
import math

NA = float("nan")


def _isna(x):
    return x is None or (x.__class__ is float and x != x)


def _dir(d):
    """Pine direction -> +1/-1 (strategy.long is True in this runtime, strategy.short False)."""
    if d is True or d == 1 or d == "long":
        return 1
    if d is False or d == -1 or d == "short" or d == 0:
        return -1
    if isinstance(d, str):
        return 1 if d.lower().startswith("l") or d.lower() == "buy" else -1
    return 1 if d else -1


class Trade:
    __slots__ = ("eid", "q", "q0", "px", "bar", "t", "fee", "exits_done", "trail_on", "trail_ext")

    def __init__(self, eid, q, px, bar, t, fee):
        self.eid, self.q, self.px, self.bar, self.t, self.fee = eid, q, px, bar, t, fee
        self.q0 = abs(q)
        self.exits_done = set()
        self.trail_on = {}
        self.trail_ext = {}


class PriceOrder:
    __slots__ = ("kind", "id", "d", "qty", "limit", "stop", "bar")

    def __init__(self, kind, oid, d, qty, limit, stop, bar):
        self.kind, self.id, self.d, self.qty, self.limit, self.stop, self.bar = kind, oid, d, qty, limit, stop, bar


class ExitOrder:
    __slots__ = ("id", "frm", "qty", "pct", "profit", "limit", "loss", "stop", "tprice", "tpoints", "toffset", "bar")


class Broker:
    def __init__(self, D, cfg: dict, fee: float = 0.0007, cap: float = 1.0, shadow: bool = False):
        self.D = D
        self.O, self.H, self.L, self.C, self.T, self.TC = D.O, D.H, D.L, D.C, D.T, D.TC
        self.FUND = getattr(D, "FUND", None)
        self.tick = D.mintick
        self.pyr = max(1, int(cfg.get("pyramiding", 1) or 1))
        self.poc = bool(cfg.get("process_orders_on_close", False))
        self.initial_capital = float(cfg.get("initial_capital", 10000.0) or 10000.0)
        self.fee = fee
        self.cap = cap
        self.shadow = shadow
        self.pct = 1.0 / self.pyr
        self.cash = self.initial_capital
        self.trades: list[Trade] = []
        self.closed: list[tuple] = []     # (eid, entry_bar, exit_bar, entry_t, exit_t, q, entry_px, exit_px, net)
        self.mkt: list[tuple] = []
        self.porders: dict = {}
        self.exits: dict = {}
        self.allowed = "all"
        self.blown = False
        self.i = 0
        self.n_fills = 0
        self.fees_paid = 0.0
        self.funding_paid = 0.0
        # pine-visible
        self.pos_size = 0.0
        self.avg_price = NA
        self.entry_name = ""
        self.equity = self.initial_capital
        self.openprofit = 0.0
        self.netprofit = 0.0
        self.grossprofit = 0.0
        self.grossloss = 0.0
        self.n_open = 0
        self.n_closed = 0
        self.wintrades = 0
        self.losstrades = 0
        self.eventrades = 0
        self.max_drawdown = 0.0
        self.max_runup = 0.0
        self.avg_trade = NA
        self.max_held = 0.0
        self.netprofit_percent = 0.0
        self.openprofit_percent = 0.0
        self.peak = self.initial_capital
        self.h_pos, self.h_avg, self.h_eq, self.h_nopen, self.h_nclosed, self.h_net, self.h_open = [], [], [], [], [], [], []
        self.eq_close: list[float] = []

    # ------------------------------------------------------------------ script API (called during step)
    def _ok(self, when):
        if self.blown or self.shadow:
            return False
        return when is True or (when is not False and when is not None and when == when and when != 0)

    def entry(self, oid, d, qty=None, limit=None, stop=None, oca=None, when=True):
        if not self._ok(when):
            return
        d = _dir(d)
        if _isna(limit):
            limit = None
        if _isna(stop):
            stop = None
        if limit is None and stop is None:
            self.mkt = [m for m in self.mkt if not (m[0] == "entry" and m[1] == oid)]
            self.mkt.append(("entry", oid, d, None))
        else:
            self.porders[oid] = PriceOrder("entry", oid, d, None, limit, stop, self.i)

    def order(self, oid, d, qty=None, limit=None, stop=None, oca=None, when=True):
        if not self._ok(when):
            return
        d = _dir(d)
        q = None if _isna(qty) else abs(float(qty))
        if _isna(limit):
            limit = None
        if _isna(stop):
            stop = None
        if limit is None and stop is None:
            self.mkt.append(("order", oid, d, q))
        else:
            self.porders[oid] = PriceOrder("order", oid, d, q, limit, stop, self.i)

    def exit(self, oid, frm=None, qty=None, qty_percent=None, profit=None, limit=None, loss=None, stop=None,
             trail_price=None, trail_points=None, trail_offset=None, when=True):
        if not self._ok(when):
            return
        x = ExitOrder()
        x.id, x.frm = oid, (None if frm is None or frm == "" else frm)
        x.qty = None if _isna(qty) else abs(float(qty))
        x.pct = 100.0 if _isna(qty_percent) else float(qty_percent)
        x.profit = None if _isna(profit) else float(profit)
        x.limit = None if _isna(limit) else float(limit)
        x.loss = None if _isna(loss) else float(loss)
        x.stop = None if _isna(stop) else float(stop)
        x.tprice = None if _isna(trail_price) else float(trail_price)
        x.tpoints = None if _isna(trail_points) else float(trail_points)
        x.toffset = None if _isna(trail_offset) else float(trail_offset)
        x.bar = self.i
        if x.profit is None and x.limit is None and x.loss is None and x.stop is None and \
                (x.toffset is None or (x.tprice is None and x.tpoints is None)):
            return      # TradingView ignores an exit without any price condition
        self.exits[(oid, x.frm)] = x

    def close(self, oid, qty=None, qty_percent=None, immediately=False, when=True):
        if not self._ok(when):
            return
        q = None if _isna(qty) else abs(float(qty))
        p = None if _isna(qty_percent) else float(qty_percent)
        if immediately is True:
            self._close_id(oid, q, p, self.C[self.i], self.i)
            self._refresh(self.C[self.i])
        else:
            self.mkt.append(("close", oid, q, p))

    def close_all(self, immediately=False, when=True):
        if not self._ok(when):
            return
        if immediately is True:
            self._close_all(self.C[self.i], self.i)
            self._refresh(self.C[self.i])
        else:
            self.mkt.append(("close_all",))

    def cancel(self, oid, when=True):
        if not self._ok(when):
            return
        self.porders.pop(oid, None)
        for k in [k for k in self.exits if k[0] == oid]:
            del self.exits[k]
        self.mkt = [m for m in self.mkt if not (len(m) > 1 and m[0] in ("entry", "order") and m[1] == oid)]

    def cancel_all(self, when=True):
        if not self._ok(when):
            return
        self.porders.clear()
        self.exits.clear()

    def allow_entry_in(self, d):
        self.allowed = "all" if d in (None, "all") else ("long" if _dir(d) == 1 else "short")

    def trade_attr(self, which, attr, k):
        k = int(k) if not _isna(k) else 0
        if which == "opentrades":
            if k < 0 or k >= len(self.trades):
                return NA
            tr = self.trades[k]
            return {"entry_price": tr.px, "entry_bar_index": tr.bar, "entry_time": tr.t, "entry_id": tr.eid,
                    "size": tr.q, "entry_comment": "", "commission": tr.fee,
                    "profit": tr.q * (self.C[self.i] - tr.px) - tr.fee}.get(attr, NA)
        if k < 0 or k >= len(self.closed):
            return NA
        c = self.closed[k]
        return {"entry_id": c[0], "entry_bar_index": c[1], "exit_bar_index": c[2], "entry_time": c[3],
                "exit_time": c[4], "size": c[5], "entry_price": c[6], "exit_price": c[7], "profit": c[8],
                "exit_id": "", "commission": 0.0}.get(attr, NA)

    def default_qty(self, px):
        return self.pct * self.equity / px if px else NA

    # ------------------------------------------------------------------ fills
    def _equity_at(self, px):
        return self.cash + sum(t.q * (px - t.px) for t in self.trades)

    def _gross(self, px):
        return sum(abs(t.q) for t in self.trades) * px

    def _open(self, eid, d, px, i, qty_units=None):
        eq = self._equity_at(px)
        if eq <= 0:
            return
        q = qty_units if qty_units is not None else self.pct * eq / px
        room = max(0.0, self.cap * eq - self._gross(px)) / px
        q = min(q, room)
        if q <= 1e-12:
            return
        fee = q * px * self.fee
        self.cash -= fee
        self.fees_paid += fee
        self.n_fills += 1
        self.trades.append(Trade(eid, d * q, px, i, self.T[i], fee))

    def _close_trade(self, tr: Trade, q: float, px: float, i: int):
        """Close q units (q > 0) of trade tr at px."""
        q = min(q, abs(tr.q))
        if q <= 1e-15:
            return
        sgn = 1 if tr.q > 0 else -1
        pnl = sgn * q * (px - tr.px)
        fee = q * px * self.fee
        share = q / abs(tr.q)
        entry_fee = tr.fee * share
        tr.fee -= entry_fee
        self.cash += pnl - fee
        self.fees_paid += fee
        self.n_fills += 1
        net = pnl - fee - entry_fee
        self.closed.append((tr.eid, tr.bar, i, tr.t, self.T[i], sgn * q, tr.px, px, net))
        if net > 0:
            self.grossprofit += net; self.wintrades += 1
        elif net < 0:
            self.grossloss += -net; self.losstrades += 1
        else:
            self.eventrades += 1
        self.netprofit += net
        tr.q -= sgn * q
        if abs(tr.q) <= 1e-12:
            self.trades.remove(tr)

    def _close_all(self, px, i):
        for tr in list(self.trades):
            self._close_trade(tr, abs(tr.q), px, i)

    def _close_id(self, oid, q, pct, px, i):
        tgt = [t for t in self.trades if t.eid == oid]
        if not tgt:
            return
        total = sum(abs(t.q) for t in tgt)
        want = total if (q is None and pct is None) else (min(q, total) if q is not None else total * pct / 100.0)
        for tr in tgt:
            if want <= 1e-15:
                break
            take = min(abs(tr.q), want)
            self._close_trade(tr, take, px, i)
            want -= take

    def _pos(self):
        return sum(t.q for t in self.trades)

    def _exec_entry(self, oid, d, px, i):
        if self.allowed != "all" and ((d == 1) != (self.allowed == "long")):
            pos = self._pos()
            if pos != 0 and (pos > 0) != (d == 1):
                self._close_all(px, i)
            return
        pos = self._pos()
        if pos != 0 and (pos > 0) != (d == 1):
            self._close_all(px, i)
        same = sum(1 for t in self.trades if (t.q > 0) == (d == 1))
        if same >= self.pyr:
            return
        self._open(oid, d, px, i)

    def _exec_order(self, oid, d, q, px, i):
        pos = self._pos()
        if pos != 0 and (pos > 0) != (d == 1):
            want = q if q is not None else abs(pos)
            if want <= abs(pos) + 1e-12:
                rem = want
                for tr in list(self.trades):
                    if rem <= 1e-15:
                        break
                    take = min(abs(tr.q), rem)
                    self._close_trade(tr, take, px, i)
                    rem -= take
                return
            self._close_all(px, i)
            self._open(oid, d, px, i)
            return
        if self.allowed != "all" and ((d == 1) != (self.allowed == "long")):
            return
        self._open(oid, d, px, i)

    def _exec_market(self, m, px, i):
        k = m[0]
        if k == "entry":
            self._exec_entry(m[1], m[2], px, i)
        elif k == "order":
            self._exec_order(m[1], m[2], m[3], px, i)
        elif k == "close":
            self._close_id(m[1], m[2], m[3], px, i)
        elif k == "close_all":
            self._close_all(px, i)

    # ------------------------------------------------------------------ intrabar price orders
    def _exit_levels(self, x: ExitOrder, tr: Trade):
        long = tr.q > 0
        e, tk = tr.px, self.tick
        lim = None
        if x.limit is not None:
            lim = x.limit
        if x.profit is not None:
            p = e + x.profit * tk if long else e - x.profit * tk
            lim = p if lim is None else (min(lim, p) if long else max(lim, p))
        stp = None
        if x.stop is not None:
            stp = x.stop
        if x.loss is not None:
            s = e - x.loss * tk if long else e + x.loss * tk
            stp = s if stp is None else (max(stp, s) if long else min(stp, s))
        key = (x.id, x.frm)
        if x.toffset is not None and (x.tprice is not None or x.tpoints is not None) and tr.trail_on.get(key):
            ext = tr.trail_ext[key]
            ts = ext - x.toffset * tk if long else ext + x.toffset * tk
            stp = ts if stp is None else (max(stp, ts) if long else min(stp, ts))
        return lim, stp

    def _trail_update(self, p0, p1):
        for x in self.exits.values():
            if x.toffset is None or (x.tprice is None and x.tpoints is None):
                continue
            key = (x.id, x.frm)
            for tr in self.trades:
                if x.frm is not None and tr.eid != x.frm:
                    continue
                long = tr.q > 0
                act = x.tprice if x.tprice is not None else (tr.px + x.tpoints * self.tick if long else tr.px - x.tpoints * self.tick)
                hi, lo = max(p0, p1), min(p0, p1)
                if long:
                    if not tr.trail_on.get(key) and hi >= act:
                        tr.trail_on[key] = True
                        tr.trail_ext[key] = hi
                    elif tr.trail_on.get(key):
                        tr.trail_ext[key] = max(tr.trail_ext[key], hi)
                else:
                    if not tr.trail_on.get(key) and lo <= act:
                        tr.trail_on[key] = True
                        tr.trail_ext[key] = lo
                    elif tr.trail_on.get(key):
                        tr.trail_ext[key] = min(tr.trail_ext[key], lo)

    def _candidates(self, p0, p1, first):
        """Triggered orders on the segment p0 -> p1: list of (distance, fill_px, kind, payload)."""
        up = p1 >= p0
        out = []
        for po in self.porders.values():
            if po.bar >= self.i:        # placed on this bar's close in poc mode: not active yet
                continue
            buy = po.d == 1
            for lvl, typ in ((po.limit, "limit"), (po.stop, "stop")):
                if lvl is None:
                    continue
                trig = (typ == "stop" and buy) or (typ == "limit" and not buy)     # needs price rising to lvl
                if trig:
                    if first and p0 >= lvl:
                        out.append((0.0, p0, "po", po)); break
                    if up and p0 < lvl <= p1:
                        out.append((lvl - p0, lvl, "po", po)); break
                else:
                    if first and p0 <= lvl:
                        out.append((0.0, p0, "po", po)); break
                    if not up and p0 > lvl >= p1:
                        out.append((p0 - lvl, lvl, "po", po)); break
        for x in self.exits.values():
            key = (x.id, x.frm)
            for tr in self.trades:
                if x.frm is not None and tr.eid != x.frm:
                    continue
                if key in tr.exits_done:
                    continue
                lim, stp = self._exit_levels(x, tr)
                long = tr.q > 0
                for lvl, typ in ((lim, "limit"), (stp, "stop")):
                    if lvl is None:
                        continue
                    rising = (typ == "limit" and long) or (typ == "stop" and not long)
                    if rising:
                        if first and p0 >= lvl:
                            out.append((0.0, p0, "x", (x, tr))); break
                        if up and p0 < lvl <= p1:
                            out.append((lvl - p0, lvl, "x", (x, tr))); break
                    else:
                        if first and p0 <= lvl:
                            out.append((0.0, p0, "x", (x, tr))); break
                        if not up and p0 > lvl >= p1:
                            out.append((p0 - lvl, lvl, "x", (x, tr))); break
        return out

    def _fill_exit(self, x: ExitOrder, tr: Trade, px, i):
        """TradingView allocates qty_percent from the entry's original quantity, capped at what remains."""
        key = (x.id, x.frm)
        q = x.qty if x.qty is not None else tr.q0 * x.pct / 100.0
        tr.exits_done.add(key)
        self._close_trade(tr, min(q, abs(tr.q)), px, i)

    def _process_path(self, i):
        o, h, l, c = self.O[i], self.H[i], self.L[i], self.C[i]
        path = (o, h, l, c) if (h - o) <= (o - l) else (o, l, h, c)
        for s in range(3):
            p0, p1 = path[s], path[s + 1]
            first = s == 0
            for _ in range(50):
                if not self.porders and not (self.exits and self.trades):
                    break
                cands = self._candidates(p0, p1, first)
                if not cands:
                    break
                cands.sort(key=lambda z: z[0])
                dist, px, kind, payload = cands[0]
                if kind == "po":
                    po = payload
                    del self.porders[po.id]
                    if po.kind == "entry":
                        self._exec_entry(po.id, po.d, px, i)
                    else:
                        self._exec_order(po.id, po.d, po.qty, px, i)
                else:
                    self._fill_exit(payload[0], payload[1], px, i)
                p0 = px
                first = False
            self._trail_update(path[s], path[s + 1])

    # ------------------------------------------------------------------ per-bar driver
    def begin_bar(self, i):
        """Fills that happen during bar i before the script runs at its close."""
        self.i = i
        if self.blown:
            self._snapshot(self.C[i])
            return
        if self.mkt:
            q, self.mkt = self.mkt, []
            o = self.O[i]
            for m in q:
                self._exec_market(m, o, i)
        if self.porders or (self.exits and self.trades):
            self._process_path(i)
        c = self.C[i]
        if self.FUND is not None and self.trades:
            f = self.FUND[i]
            if f:
                pos = self._pos()
                pay = pos * c * f
                self.cash -= pay
                self.funding_paid += pay
        self._snapshot(c)

    def end_bar(self, i):
        """After the script ran at bar i's close: process-on-close fills and the bar's final equity."""
        c = self.C[i]
        if self.poc and self.mkt and not self.blown:
            q, self.mkt = self.mkt, []
            for m in q:
                self._exec_market(m, c, i)
            self._refresh(c)
        eq = self._equity_at(c)
        if eq <= 0 and not self.blown:
            self._close_all(c, i)
            self.blown = True
            eq = self._equity_at(c)
        self.eq_close.append(eq)

    def _refresh(self, px):
        self.equity = self._equity_at(px)
        pos = self._pos()
        self.pos_size = pos
        self.n_open = len(self.trades)
        self.n_closed = len(self.closed)
        if self.trades:
            tot = sum(abs(t.q) for t in self.trades)
            self.avg_price = sum(abs(t.q) * t.px for t in self.trades) / tot if tot else NA
            self.entry_name = self.trades[0].eid
        else:
            self.avg_price = NA
            self.entry_name = ""
        self.openprofit = sum(t.q * (px - t.px) for t in self.trades)

    def _snapshot(self, px):
        self._refresh(px)
        eq = self.equity
        self.peak = max(self.peak, eq)
        self.max_drawdown = max(self.max_drawdown, self.peak - eq)
        self.max_runup = max(self.max_runup, eq - self.initial_capital)
        self.avg_trade = self.netprofit / self.n_closed if self.n_closed else NA
        self.netprofit_percent = 100.0 * self.netprofit / self.initial_capital
        self.openprofit_percent = 100.0 * self.openprofit / self.initial_capital
        self.max_held = max(self.max_held, abs(self.pos_size))
        self.h_pos.append(self.pos_size)
        self.h_avg.append(self.avg_price)
        self.h_eq.append(eq)
        self.h_nopen.append(self.n_open)
        self.h_nclosed.append(self.n_closed)
        self.h_net.append(self.netprofit)
        self.h_open.append(self.openprofit)
