"""Compile a Pine Script AST into Python source for a per-bar `step(i)` function.

Generated module shape:
    def build(rt, D, bk, SEC, cfg):          # D: bar data, bk: broker, SEC: security hub, cfg: run config
        <aliases, root context, history buffers>
        def step(i):
            <prologue: push history placeholders>
            <script statements; user functions are nested defs so they can read globals>
        return step

Series semantics
  * Top-level variables referenced with [n] (or passed bare to window functions) keep a history buffer that receives a
    placeholder at the start of each bar; declarations and := write buffer[-1]. `x[n]` reads buffer[-1-n].
  * Variables declared in local blocks or functions with history use per-call-site buffers that are appended when the
    declaration executes (their own history), which is Pine's rule for function-local series.
  * Stateful built-ins (ema, rsi, ...) keep one state object per call site and per user-function call path.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional
from . import parser as P
from .parser import (Assign, Attr, BinOp, Bool, Break, Call, Color, Continue, Decl, ExprStmt, For, ForIn, FuncDef, If,
                     Index, Na, Name, Num, Str, Switch, Ternary, TupleLit, TypeDef, UnOp, While, Unsupported)


class CompileError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ builtin tables
SERIES = {"open": "O", "high": "H", "low": "L", "close": "C", "volume": "V", "time": "T", "time_close": "TC",
          "bar_index": "BI", "hl2": "HL2", "hlc3": "HLC3", "ohlc4": "OHLC4", "hlcc4": "HLCC4", "n": "BI",
          "year": "YEAR", "month": "MONTH", "dayofmonth": "DOM", "dayofweek": "DOW", "hour": "HOUR", "minute": "MINUTE",
          "second": "SECOND", "weekofyear": "WOY", "time_tradingday": "TDAY",
          "ta.tr": "TR", "tr": "TR", "ta.obv": "OBV", "obv": "OBV", "ta.accdist": "ACCDIST", "accdist": "ACCDIST",
          "ta.vwap": "VWAP", "vwap": "VWAP", "ta.pvt": "PVT", "pvt": "PVT", "ta.nvi": "NVI", "nvi": "NVI",
          "ta.pvi": "PVI", "pvi": "PVI", "ta.iii": "III", "iii": "III", "ta.wad": "WAD", "wad": "WAD",
          "ta.wvad": "WVAD", "wvad": "WVAD"}
COLORS = {"aqua", "black", "blue", "fuchsia", "gray", "green", "lime", "maroon", "navy", "olive", "orange", "purple",
          "red", "silver", "teal", "white", "yellow"}
LEGACY_CONST = {"integer": "'integer'", "bool": "'bool'", "string": "'string'", "symbol": "'symbol'",
                "resolution": "'resolution'", "session": "'session'", "source": "'source'",
                "linebr": "'linebr'", "histogram": "'histogram'", "cross": "'cross'", "area": "'area'",
                "columns": "'columns'", "circles": "'circles'", "stepline": "'stepline'", "dashed": "'dashed'",
                "dotted": "'dotted'", "solid": "'solid'", "monday": "2", "tuesday": "3", "wednesday": "4",
                "thursday": "5", "friday": "6", "saturday": "7", "sunday": "1",
                "tickerid": "cfg['tickerid']", "ticker": "cfg['ticker']", "period": "cfg['period']",
                "interval": "cfg['multiplier']", "isintraday": "cfg['isintraday']", "isdaily": "cfg['isdaily']",
                "isweekly": "cfg['isweekly']", "ismonthly": "cfg['ismonthly']", "isdwm": "cfg['isdwm']",
                "timenow": "D.T[-1]", "last_bar_index": "(D.n - 1)", "last_bar_time": "D.T[-1]"}
ATTR_CONST = {
    "strategy.long": "True", "strategy.short": "False", "strategy.fixed": "'fixed'", "strategy.cash": "'cash'",
    "strategy.percent_of_equity": "'percent_of_equity'", "strategy.direction.long": "'long'",
    "strategy.direction.short": "'short'", "strategy.direction.all": "'all'", "strategy.oca.cancel": "'cancel'",
    "strategy.oca.reduce": "'reduce'", "strategy.oca.none": "'none'", "strategy.commission.percent": "'percent'",
    "strategy.commission.cash_per_contract": "'cpc'", "strategy.commission.cash_per_order": "'cpo'",
    "strategy.account_currency": "'USDT'",
    "barmerge.lookahead_on": "'lookahead_on'", "barmerge.lookahead_off": "'lookahead_off'",
    "barmerge.gaps_on": "'gaps_on'", "barmerge.gaps_off": "'gaps_off'",
    "order.ascending": "'asc'", "order.descending": "'desc'",
    "dayofweek.sunday": "1", "dayofweek.monday": "2", "dayofweek.tuesday": "3", "dayofweek.wednesday": "4",
    "dayofweek.thursday": "5", "dayofweek.friday": "6", "dayofweek.saturday": "7",
    "math.pi": "3.141592653589793", "math.e": "2.718281828459045", "math.phi": "1.618033988749895",
    "math.rphi": "0.6180339887498948",
    "syminfo.tickerid": "cfg['tickerid']", "syminfo.ticker": "cfg['ticker']", "syminfo.prefix": "'BINANCE'",
    "syminfo.mintick": "cfg['mintick']", "syminfo.pointvalue": "1.0", "syminfo.currency": "'USDT'",
    "syminfo.basecurrency": "cfg['base']", "syminfo.timezone": "'UTC'", "syminfo.session": "'24x7'",
    "syminfo.type": "'crypto'", "syminfo.description": "cfg['ticker']", "syminfo.root": "cfg['base']",
    "syminfo.volumetype": "'base'",
    "timeframe.period": "cfg['period']", "timeframe.multiplier": "cfg['multiplier']",
    "timeframe.isintraday": "cfg['isintraday']", "timeframe.isdaily": "cfg['isdaily']",
    "timeframe.isweekly": "cfg['isweekly']", "timeframe.ismonthly": "cfg['ismonthly']",
    "timeframe.isdwm": "cfg['isdwm']", "timeframe.isminutes": "cfg['isminutes']",
    "timeframe.isseconds": "False", "timeframe.isticks": "False", "timeframe.main_period": "cfg['period']",
    "timeframe.in_seconds": "(cfg['tf_ms'] // 1000)",
    "barstate.isfirst": "(i == 0)", "barstate.islast": "(i == D.n - 1)", "barstate.ishistory": "True",
    "barstate.isrealtime": "False", "barstate.isnew": "True", "barstate.isconfirmed": "True",
    "barstate.islastconfirmedhistory": "(i == D.n - 1)",
    "session.regular": "'regular'", "session.extended": "'extended'", "session.ismarket": "True",
    "session.ispremarket": "False", "session.ispostmarket": "False", "session.isfirstbar": "False",
    "session.islastbar": "False", "session.isfirstbar_regular": "False", "session.islastbar_regular": "False",
    "adjustment.none": "'none'", "adjustment.splits": "'splits'", "adjustment.dividends": "'dividends'",
    "chart.is_standard": "True", "chart.is_heikinashi": "False",
}
BROKER_ATTR = {
    "strategy.position_size": "bk.pos_size", "strategy.position_avg_price": "bk.avg_price",
    "strategy.position_entry_name": "bk.entry_name", "strategy.equity": "bk.equity",
    "strategy.openprofit": "bk.openprofit", "strategy.netprofit": "bk.netprofit",
    "strategy.grossprofit": "bk.grossprofit", "strategy.grossloss": "bk.grossloss",
    "strategy.initial_capital": "bk.initial_capital", "strategy.opentrades": "bk.n_open",
    "strategy.closedtrades": "bk.n_closed", "strategy.wintrades": "bk.wintrades", "strategy.losstrades": "bk.losstrades",
    "strategy.eventrades": "bk.eventrades", "strategy.max_drawdown": "bk.max_drawdown",
    "strategy.max_runup": "bk.max_runup", "strategy.cash": "'cash'",
    "strategy.margin_liquidation_price": "rt.NA", "strategy.avg_trade": "bk.avg_trade",
    "strategy.max_contracts_held_all": "bk.max_held", "strategy.max_contracts_held_long": "bk.max_held",
    "strategy.max_contracts_held_short": "bk.max_held", "strategy.netprofit_percent": "bk.netprofit_percent",
    "strategy.openprofit_percent": "bk.openprofit_percent",
}
BROKER_HIST = {"strategy.position_size": "bk.h_pos", "strategy.position_avg_price": "bk.h_avg",
               "strategy.equity": "bk.h_eq", "strategy.opentrades": "bk.h_nopen", "strategy.closedtrades": "bk.h_nclosed",
               "strategy.netprofit": "bk.h_net", "strategy.openprofit": "bk.h_open"}
OPAQUE_NS = {"color", "location", "shape", "size", "plot", "hline", "label", "line", "box", "table", "extend", "xloc",
             "yloc", "position", "text", "font", "format", "scale", "display", "currency", "alert", "earnings",
             "dividends", "splits", "linefill", "polyline", "chart", "input", "dayofweek"}

# window functions over one series: name -> (runtime fn, param names, index of series params)
WIN1 = {
    "sma": ("w_sma", ["source", "length"]), "wma": ("w_wma", ["source", "length"]),
    "stdev": ("w_stdev", ["source", "length", "biased"]), "variance": ("w_variance", ["source", "length", "biased"]),
    "dev": ("w_dev", ["source", "length"]), "median": ("w_median", ["source", "length"]),
    "mode": ("w_mode", ["source", "length"]), "linreg": ("w_linreg", ["source", "length", "offset"]),
    "percentrank": ("w_percentrank", ["source", "length"]),
    "percentile_linear_interpolation": ("w_percentile_linear", ["source", "length", "percentage"]),
    "percentile_nearest_rank": ("w_percentile_nearest", ["source", "length", "percentage"]),
    "change": ("w_change", ["source", "length"]), "mom": ("w_change", ["source", "length"]),
    "roc": ("w_roc", ["source", "length"]), "rising": ("w_rising", ["source", "length"]),
    "falling": ("w_falling", ["source", "length"]), "cog": ("w_cog", ["source", "length"]),
    "cmo": ("w_cmo", ["source", "length"]), "alma": ("w_alma", ["series", "length", "offset", "sigma", "floor"]),
    "range": ("w_range", ["source", "length"]), "swma": ("w_swma", ["source"]), "sum": ("w_sum", ["source", "length"]),
}
TA_V4 = {"sma", "ema", "wma", "rma", "rsi", "atr", "stdev", "variance", "dev", "highest", "lowest", "highestbars",
         "lowestbars", "change", "mom", "roc", "rising", "falling", "crossover", "crossunder", "cross", "cum",
         "valuewhen", "barssince", "pivothigh", "pivotlow", "linreg", "percentrank", "correlation", "cog", "cmo",
         "alma", "swma", "vwma", "stoch", "tsi", "mfi", "cci", "macd", "bb", "bbw", "kc", "kcw", "supertrend", "dmi",
         "sar", "hma", "median", "mode", "percentile_linear_interpolation", "percentile_nearest_rank", "wpr", "range",
         "sum", "vwap", "tr", "max", "min"}
MATH = {"abs": "m_abs", "max": "m_max", "min": "m_min", "round": "m_round", "floor": "m_floor", "ceil": "m_ceil",
        "sqrt": "m_sqrt", "pow": "m_pow", "log": "m_log", "log10": "m_log10", "exp": "m_exp", "sign": "m_sign",
        "avg": "m_avg", "sin": "m_sin", "cos": "m_cos", "tan": "m_tan", "asin": "m_asin", "acos": "m_acos",
        "atan": "m_atan", "todegrees": "m_todegrees", "toradians": "m_toradians", "random": "m_random"}
STR = {"tostring": "s_tostring", "format": "s_format", "tonumber": "s_tonumber", "contains": "s_contains",
       "length": "s_length", "replace_all": "s_replace_all", "split": "s_split"}
ARRAY = {"get": "a_get", "set": "a_set", "push": "a_push", "pop": "a_pop", "shift": "a_shift", "unshift": "a_unshift",
         "insert": "a_insert", "remove": "a_remove", "size": "a_size", "clear": "a_clear", "sum": "a_sum",
         "avg": "a_avg", "max": "a_max", "min": "a_min", "stdev": "a_stdev", "variance": "a_variance",
         "median": "a_median", "sort": "a_sort", "reverse": "a_reverse", "slice": "a_slice", "copy": "a_copy",
         "concat": "a_concat", "indexof": "a_indexof", "includes": "a_includes", "fill": "a_fill", "first": "a_first",
         "last": "a_last", "range": "a_range", "abs": "a_abs", "percentrank": "a_percentrank", "join": "a_join",
         "sort_indices": "a_sort_indices", "from": "a_from"}
NOOP_CALLS = {"plot", "plotshape", "plotchar", "plotarrow", "plotcandle", "plotbar", "hline", "fill", "bgcolor",
              "barcolor", "alert", "alertcondition", "study", "indicator", "strategy", "library", "max_bars_back",
              "runtime.log", "log.info", "log.warning", "log.error", "table.cell", "table.delete", "table.clear",
              "table.set_position", "table.cell_set_text", "table.cell_set_bgcolor", "table.cell_set_text_color",
              "table.merge_cells", "label.delete", "line.delete", "box.delete", "linefill.delete", "linefill.new",
              "polyline.delete"}
DRAW_NS = {"label", "line", "box", "table", "linefill", "polyline"}
BUILTIN_NS = {"ta", "math", "str", "array", "matrix", "map", "request", "strategy", "syminfo", "timeframe", "barstate",
              "color", "location", "shape", "size", "plot", "hline", "label", "line", "box", "table", "extend", "xloc",
              "yloc", "position", "text", "font", "format", "scale", "display", "currency", "alert", "session",
              "dayofweek", "barmerge", "order", "input", "ticker", "runtime", "log", "chart", "earnings", "dividends",
              "splits", "adjustment", "linefill", "polyline"}
BOOL_FUNCS = {"na", "ta.crossover", "ta.crossunder", "ta.cross", "crossover", "crossunder", "cross", "ta.rising",
              "ta.falling", "rising", "falling", "str.contains", "array.includes", "bool", "time_in_session"}

START_WORDS = {"start", "from", "begin", "beginning", "since", "starting", "strt", "st"}
END_WORDS = {"end", "to", "stop", "finish", "until", "till", "ending", "thru", "through", "fin"}


def _words(s: str) -> set:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s or "")
    return {w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w}


def dotted(node) -> Optional[str]:
    if isinstance(node, Name):
        return node.id
    if isinstance(node, Attr):
        base = dotted(node.obj)
        return f"{base}.{node.attr}" if base else None
    return None


# ------------------------------------------------------------------------------------------------ symbols
@dataclass
class Var:
    pine: str
    py: str
    kind: str            # plain | ghist | lhist | box | param
    mode: Optional[str] = None
    slot: Optional[int] = None
    decl_node: object = None
    forward: bool = False
    claimed: bool = False


@dataclass
class Func:
    name: str
    py: str
    node: FuncDef
    slots: list = field(default_factory=list)   # slot factories (python expr strings)
    method: bool = False
    param_py: list = field(default_factory=list)
    plain_locals: list = field(default_factory=list)


class Scope:
    def __init__(self, parent: Optional["Scope"], kind: str, func: Optional[Func] = None):
        self.parent, self.kind, self.func = parent, kind, func
        self.vars: dict[str, Var] = {}

    def lookup(self, name: str) -> Optional[Var]:
        s = self
        while s is not None:
            if name in s.vars:
                return s.vars[name]
            s = s.parent
        return None


class Emitter:
    def __init__(self):
        self.lines: list[str] = []
        self.ind = 0

    def w(self, s: str):
        self.lines.append("    " * self.ind + s)


# ------------------------------------------------------------------------------------------------ compiler
class Compiler:
    def __init__(self, stmts: list, version: int, src: str):
        self.stmts = stmts
        self.version = version
        self.src = src
        self.cfg = {"pyramiding": 1, "process_orders_on_close": False, "initial_capital": 10000.0,
                    "close_entries_rule": "FIFO", "calc_on_every_tick": False, "decl": None}
        self.flags: set[str] = set()
        self.hist_names: set[str] = set()
        self.root_slots: list[str] = []
        self.gh_vars: list[Var] = []       # global history vars (placeholder semantics)
        self.funcs: dict[str, Func] = {}
        self.types: dict[str, list] = {}
        self.uid = 0
        self.e = Emitter()
        self.cur_func: Optional[Func] = None
        self.scope = Scope(None, "global")
        self.pre_decl: set[int] = set()     # id(Var) whose own declaration RHS is being compiled
        self.decl_name_stack: list[str] = []
        self.global_plain: list[str] = []
        self.sec_sites = 0

    # ---- helpers
    def new(self, prefix="t"):
        self.uid += 1
        return f"_{prefix}{self.uid}"

    def slot(self, factory: str) -> str:
        """Allocate a call-site slot in the current context; return the python expression addressing it."""
        if self.cur_func is None:
            self.root_slots.append(factory)
            return f"_R{len(self.root_slots) - 1}"
        self.cur_func.slots.append(factory)
        return f"_C[{len(self.cur_func.slots) - 1}]"

    def ctx_expr(self) -> str:
        return "K" if self.cur_func is None else "_C"

    def path_expr(self, k: int) -> str:
        return f"({k},)" if self.cur_func is None else f"(_C.path + ({k},))"

    # ---- analysis
    def analyse(self):
        def visit(n):
            if isinstance(n, list):
                for x in n:
                    visit(x)
                return
            if isinstance(n, tuple):
                for x in n:
                    visit(x)
                return
            if not isinstance(n, P.Node) and not hasattr(n, "__dataclass_fields__"):
                return
            if isinstance(n, Index) and isinstance(n.obj, Name):
                self.hist_names.add(n.obj.id)
            if isinstance(n, Call):
                fname = dotted(n.func) or ""
                base = fname.split(".")[-1]
                if fname.startswith("ta.") or fname in TA_V4 or fname.startswith("math.sum") or fname == "sum":
                    for a in n.args[:3]:
                        if isinstance(a, Name):
                            self.hist_names.add(a.id)
                    for k, a in n.kwargs:
                        if isinstance(a, Name):
                            self.hist_names.add(a.id)
                if fname in ("strategy", "study", "indicator"):
                    self.cfg["decl"] = fname
                    for k, v in n.kwargs:
                        if k == "pyramiding" and isinstance(v, Num):
                            self.cfg["pyramiding"] = max(1, int(v.v))
                        elif k == "process_orders_on_close" and isinstance(v, Bool):
                            self.cfg["process_orders_on_close"] = v.v
                        elif k == "initial_capital" and isinstance(v, Num):
                            self.cfg["initial_capital"] = float(v.v)
                        elif k == "close_entries_rule" and isinstance(v, Str):
                            self.cfg["close_entries_rule"] = v.v
                        elif k == "calc_on_every_tick" and isinstance(v, Bool):
                            self.cfg["calc_on_every_tick"] = v.v
                    if fname == "strategy" and len(n.args) > 6 and isinstance(n.args[6], Num):
                        self.cfg["pyramiding"] = max(1, int(n.args[6].v))
                if fname in ("request.security", "security"):
                    for k, v in n.kwargs:
                        if k == "lookahead" and dotted(v) in ("barmerge.lookahead_on",):
                            self.flags.add("lookahead_on")
                    if len(n.args) > 4 and dotted(n.args[4]) == "barmerge.lookahead_on":
                        self.flags.add("lookahead_on")
            for f in getattr(n, "__dataclass_fields__", {}):
                if f != "line":
                    visit(getattr(n, f))
        visit(self.stmts)

    # ---- entry
    def compile(self) -> str:
        self.analyse()
        self.scope_global = self.scope
        self.toplevel_decl = {}
        for st in self.stmts:
            if isinstance(st, Decl):
                for nm in st.names:
                    self.toplevel_decl.setdefault(nm, st.mode)
        e = self.e
        e.w("def build(rt, D, bk, SEC, cfg):")
        e.ind += 1
        e.w("NA = rt.NA; T_ = rt.T; hist = rt.hist; hist_pre = rt.hist_pre; bar = rt.bar; div = rt.div")
        for arr in ("O", "H", "L", "C", "V", "T", "TC", "BI", "HL2", "HLC3", "OHLC4", "HLCC4", "YEAR", "MONTH", "DOM",
                    "DOW", "HOUR", "MINUTE", "SECOND", "WOY", "TDAY", "TR", "TR1", "OBV", "ACCDIST", "VWAP", "PVT", "NVI",
                    "PVI", "III", "WAD", "WVAD"):
            e.w(f"{arr} = D.{arr}")
        body_start = len(e.lines)
        # compile step into a separate emitter, then splice
        step = Emitter()
        step.ind = 1
        self.e = step
        step.w("def step(i):")
        step.ind += 1
        prologue_at = len(step.lines)
        self.gen_block(self.stmts, top=True)
        if len(step.lines) == prologue_at:
            step.w("pass")
        # prologue
        pro = []
        for v in self.gh_vars:
            if v.mode:
                pro.append(f"{v.py}_h.append({v.py}_h[-1] if {v.py}_h else NA)")
            else:
                pro.append(f"{v.py}_h.append(NA)")
        if self.global_plain:
            names = sorted(set(self.global_plain))
            for k in range(0, len(names), 40):
                pro.append(" = ".join(names[k:k + 40]) + " = NA")
        step.lines[prologue_at:prologue_at] = ["        " + p for p in pro]
        step.w("return None")
        self.e = e
        # root context and history buffers
        e.w(f"K = rt_ctx({len(self.root_slots)}, ())")
        for k, fac in enumerate(self.root_slots):
            e.w(f"K[{k}] = {fac}")
            e.w(f"_R{k} = K[{k}]")
        for v in self.gh_vars:
            e.w(f"{v.py}_h = []")
        for name, fields in self.types.items():
            pass
        for f in self.funcs.values():
            e.w(f"def {f.py}_fac(path):")
            e.w(f"    c = rt_ctx({len(f.slots)}, path)")
            for k, fac in enumerate(f.slots):
                e.w(f"    c[{k}] = {fac}")
            e.w("    return c")
        e.lines.extend(step.lines)
        e.w("return step")
        return "\n".join(e.lines) + "\n"

    # ---- blocks and statements
    def gen_block(self, stmts, top=False):
        start = len(self.e.lines)
        for s in stmts:
            self.gen_stmt(s, top)
        if len(self.e.lines) == start:
            self.e.w("pass")

    def gen_stmt(self, s, top=False):
        if isinstance(s, Decl):
            return self.gen_decl(s, top)
        if isinstance(s, Assign):
            return self.gen_assign(s)
        if isinstance(s, ExprStmt):
            return self.gen_exprstmt(s)
        if isinstance(s, If):
            return self.gen_if(s, None)
        if isinstance(s, For):
            return self.gen_for(s, None)
        if isinstance(s, ForIn):
            return self.gen_forin(s, None)
        if isinstance(s, While):
            return self.gen_while(s, None)
        if isinstance(s, Switch):
            return self.gen_switch(s, None)
        if isinstance(s, FuncDef):
            return self.gen_funcdef(s)
        if isinstance(s, TypeDef):
            return self.gen_typedef(s)
        if isinstance(s, Break):
            self.e.w("break"); return
        if isinstance(s, Continue):
            self.e.w("continue"); return
        raise CompileError(f"unsupported statement {type(s).__name__}")

    def push_scope(self, kind, func=None):
        self.scope = Scope(self.scope, kind, func)

    def pop_scope(self):
        self.scope = self.scope.parent

    def declare(self, name: str, mode, top: bool, node=None) -> Var:
        if top and self.scope.kind == "global" and self.cur_func is None:
            ex = self.scope.vars.get(name)
            if ex is not None and getattr(ex, "forward", False) and not getattr(ex, "claimed", False):
                ex.claimed = True
                ex.decl_node = node
                return ex
        self.uid += 1
        safe = re.sub(r"\W", "_", name)
        is_global_top = top and self.scope.kind == "global" and self.cur_func is None
        hist = name in self.hist_names
        if is_global_top:
            py = f"g_{safe}"
            if hist:
                v = Var(name, py, "ghist", mode)
                self.gh_vars.append(v)
            elif mode:
                v = Var(name, py, "box", mode)
                v.slot = self.slot("rt.Box()")
            else:
                v = Var(name, py, "plain", mode)
                self.global_plain.append(py)
        else:
            py = f"l{self.uid}_{safe}"
            if hist:
                v = Var(name, py, "lhist", mode)
                v.slot = self.slot("rt.HBuf()" if not mode else "rt.Box()")
            elif mode:
                v = Var(name, py, "box", mode)
                v.slot = self.slot("rt.Box()")
            else:
                v = Var(name, py, "plain", mode)
                if self.cur_func is None:
                    self.global_plain.append(py)
                else:
                    self.cur_func.plain_locals.append(py)
        v.decl_node = node
        self.scope.vars[name] = v
        return v

    def slot_ref(self, v: Var) -> str:
        return v.slot if isinstance(v.slot, str) else str(v.slot)

    def store(self, v: Var, val: str, first_decl: bool):
        """Emit code assigning python value expr `val` to Pine variable v (declaration or reassignment)."""
        e = self.e
        if v.kind == "plain" or v.kind == "param":
            e.w(f"{v.py} = {val}")
        elif v.kind == "ghist":
            if first_decl and v.mode:
                flag = self.slot("rt.Box()")
                e.w(f"if not {flag}.init:")
                e.w(f"    {flag}.init = True; {v.py}_h[-1] = {val}")
                e.w(f"{v.py} = {v.py}_h[-1]")
            else:
                e.w(f"{v.py} = {v.py}_h[-1] = {val}")
        elif v.kind == "lhist":
            s = v.slot
            if first_decl:
                if v.mode:
                    e.w(f"if not {s}.init:")
                    e.w(f"    {s}.init = True; {s}.v = rt.HBuf(); {s}.v.append({val})")
                    e.w(f"else:")
                    e.w(f"    {s}.v.append({s}.v[-1])")
                    e.w(f"{v.py} = {s}.v[-1]")
                else:
                    e.w(f"{v.py} = {val}; {s}.append({v.py})")
            else:
                if v.mode:
                    e.w(f"{v.py} = {s}.v[-1] = {val}")
                else:
                    e.w(f"{v.py} = {s}[-1] = {val}")
        elif v.kind == "box":
            s = v.slot
            if first_decl:
                e.w(f"if not {s}.init:")
                e.w(f"    {s}.init = True; {s}.v = {val}")
                e.w(f"{v.py} = {s}.v")
            else:
                e.w(f"{v.py} = {s}.v = {val}")

    def value_of(self, node, want_tmp=True) -> str:
        """Compile an expression or block-expression; returns a python expression string."""
        if isinstance(node, (If, Switch, For, ForIn, While)):
            tmp = self.new("bv")
            self.gen_valued(node, tmp)
            return tmp
        return self.expr(node)

    def gen_decl(self, s: Decl, top: bool):
        if len(s.names) == 1:
            name = s.names[0]
            self.decl_name_stack.append(name)
            if not s.mode and not self._self_hist(s.value, name):
                try:
                    val = self.value_of(s.value)
                finally:
                    self.decl_name_stack.pop()
                v = self.declare(name, s.mode, top, s)
                self.store(v, val, first_decl=True)
                return
            v = self.declare(name, s.mode, top, s)
            is_var_first = bool(s.mode)
            if v.kind == "lhist" and not s.mode:
                self.pre_decl.add(id(v))
            try:
                if is_var_first and v.kind in ("ghist", "box", "lhist"):
                    # evaluate the initialiser only once: compile it inside the init branch
                    val = self._lazy_init(s.value)
                else:
                    val = self.value_of(s.value)
            finally:
                self.pre_decl.discard(id(v))
                self.decl_name_stack.pop()
            if is_var_first and isinstance(val, tuple):
                self._store_var_lazy(v, val)
            else:
                self.store(v, val, first_decl=True)
            return
        # tuple declaration
        tmp = self.new("tp")
        val = self.value_of(s.value)
        self.e.w(f"{tmp} = {val}")
        for k, name in enumerate(s.names):
            v = self.declare(name, s.mode, top, s)
            if name == "_":
                continue
            self.store(v, f"rt_tget({tmp}, {k})", first_decl=True)

    def _self_hist(self, node, name) -> bool:
        """Does the expression reference `name[...]` (self-referencing declaration, legal in Pine)?"""
        found = False
        def visit(n):
            nonlocal found
            if found:
                return
            if isinstance(n, list):
                for x in n:
                    visit(x)
                return
            if isinstance(n, tuple):
                for x in n:
                    visit(x)
                return
            if not hasattr(n, "__dataclass_fields__"):
                return
            if isinstance(n, Index) and isinstance(n.obj, Name) and n.obj.id == name:
                found = True
                return
            if isinstance(n, Name) and n.id == name and self.scope.lookup(name) is None:
                found = True       # v1/v2 self reference without history
                return
            for f in n.__dataclass_fields__:
                if f != "line":
                    visit(getattr(n, f))
        visit(node)
        return found

    def _lazy_init(self, node):
        """Compile a var initialiser into a nested emitter so it is evaluated only on the first execution."""
        saved = self.e
        sub = Emitter()
        sub.ind = saved.ind + 1
        self.e = sub
        try:
            val = self.value_of(node)
        finally:
            self.e = saved
        return ("lazy", sub.lines, val)

    def _store_var_lazy(self, v: Var, lazy):
        _, lines, val = lazy
        e = self.e
        if v.kind == "ghist":
            flag = self.slot("rt.Box()")
            e.w(f"if not {flag}.init:")
            e.w(f"    {flag}.init = True")
            e.lines.extend(lines)
            e.w(f"    {v.py}_h[-1] = {val}")
            e.w(f"{v.py} = {v.py}_h[-1]")
        elif v.kind == "box":
            s = v.slot
            e.w(f"if not {s}.init:")
            e.w(f"    {s}.init = True")
            e.lines.extend(lines)
            e.w(f"    {s}.v = {val}")
            e.w(f"{v.py} = {s}.v")
        elif v.kind == "lhist":
            s = v.slot
            e.w(f"if not {s}.init:")
            e.w(f"    {s}.init = True; {s}.v = rt.HBuf()")
            e.lines.extend(lines)
            e.w(f"    {s}.v.append({val})")
            e.w(f"else:")
            e.w(f"    {s}.v.append({s}.v[-1])")
            e.w(f"{v.py} = {s}.v[-1]")
        else:
            e.lines.extend([l[4:] if l.startswith("    ") else l for l in lines])
            e.w(f"{v.py} = {val}")

    def gen_assign(self, s: Assign):
        t = s.target
        if isinstance(t, TupleLit):
            tmp = self.new("tp")
            self.e.w(f"{tmp} = {self.value_of(s.value)}")
            for k, item in enumerate(t.items):
                if isinstance(item, Name):
                    v = self.scope.lookup(item.id)
                    if v is None:
                        raise CompileError(f"assignment to undeclared {item.id}")
                    self.store(v, f"rt_tget({tmp}, {k})", first_decl=False)
            return
        if isinstance(t, Name):
            v = self.scope.lookup(t.id)
            if v is None:
                if s.op == ":=":
                    # v1/v2 scripts sometimes := an undeclared name; treat as declaration
                    v = self.declare(t.id, None, self.scope.kind == "global" and self.cur_func is None)
                    self.store(v, self.value_of(s.value), first_decl=True)
                    return
                raise CompileError(f"assignment to undeclared {t.id}")
            val = self.value_of(s.value)
            if s.op != ":=" and s.op != "=":
                op = s.op[0]
                cur = v.py
                if op == "/":
                    val = f"div({cur}, {val})"
                elif op == "%":
                    val = f"rt.mod({cur}, {val})"
                else:
                    val = f"({cur} {op} {val})"
            self.store(v, val, first_decl=False)
            return
        if isinstance(t, Attr):
            name = dotted(t)
            if name and (name.startswith("strategy.") or name.split(".")[0] in ("syminfo", "timeframe", "barstate")):
                self.e.w(f"pass  # ignored assignment to {name}")
                return
            obj = self.expr(t.obj)
            val = self.value_of(s.value)
            if s.op not in (":=", "="):
                op = s.op[0]
                val = f"({obj}.{t.attr} {op} {val})" if op not in "/%" else f"div({obj}.{t.attr}, {val})"
            self.e.w(f"{obj}.{t.attr} = {val}")
            return
        raise CompileError("unsupported assignment target")

    def gen_exprstmt(self, s: ExprStmt):
        ex = s.expr
        if isinstance(ex, Call):
            fname = dotted(ex.func) or ""
            if fname in NOOP_CALLS:
                return
            ns = fname.split(".")[0]
            if ns in DRAW_NS and fname not in ("line.set_xy1", "line.set_xy2", "line.new", "label.new", "box.new"):
                return
        self.e.w(self.expr(ex))

    def cond(self, node) -> str:
        c = self.expr(node)
        return c if self.is_bool(node) else f"T_({c})"

    def is_bool(self, node) -> bool:
        if isinstance(node, Bool):
            return True
        if isinstance(node, BinOp) and node.op in ("==", "!=", "<", ">", "<=", ">=", "and", "or"):
            return True
        if isinstance(node, UnOp) and node.op == "not":
            return True
        if isinstance(node, Call) and (dotted(node.func) or "") in BOOL_FUNCS:
            return True
        return False

    def gen_if(self, s: If, tmp: Optional[str]):
        e = self.e
        e.w(f"if {self.cond(s.cond)}:")
        e.ind += 1
        self.push_scope("block")
        if tmp:
            self.gen_block_valued(s.body, tmp)
        else:
            self.gen_block(s.body)
        self.pop_scope()
        e.ind -= 1
        if s.orelse is not None:
            e.w("else:")
            e.ind += 1
            self.push_scope("block")
            if tmp:
                self.gen_block_valued(s.orelse, tmp)
            else:
                self.gen_block(s.orelse)
            self.pop_scope()
            e.ind -= 1
        elif tmp:
            e.w("else:")
            e.w(f"    {tmp} = NA")

    def gen_block_valued(self, stmts, tmp):
        if not stmts:
            self.e.w(f"{tmp} = NA")
            return
        for st in stmts[:-1]:
            self.gen_stmt(st)
        last = stmts[-1]
        if isinstance(last, ExprStmt):
            fname = dotted(last.expr.func) if isinstance(last.expr, Call) else None
            if fname in NOOP_CALLS:
                self.e.w(f"{tmp} = None")
            else:
                self.e.w(f"{tmp} = {self.expr(last.expr)}")
        elif isinstance(last, Decl):
            self.gen_decl(last, False)
            if len(last.names) == 1:
                self.e.w(f"{tmp} = {self.scope.lookup(last.names[0]).py}")
            else:
                self.e.w(f"{tmp} = ({', '.join(self.scope.lookup(n).py for n in last.names)},)")
        elif isinstance(last, Assign):
            self.gen_assign(last)
            if isinstance(last.target, Name):
                self.e.w(f"{tmp} = {self.scope.lookup(last.target.id).py}")
            else:
                self.e.w(f"{tmp} = None")
        elif isinstance(last, (If, Switch, For, ForIn, While)):
            self.gen_valued(last, tmp)
        else:
            self.gen_stmt(last)
            self.e.w(f"{tmp} = NA")

    def gen_valued(self, node, tmp):
        if isinstance(node, If):
            self.gen_if(node, tmp)
        elif isinstance(node, Switch):
            self.gen_switch(node, tmp)
        elif isinstance(node, For):
            self.e.w(f"{tmp} = NA")
            self.gen_for(node, tmp)
        elif isinstance(node, ForIn):
            self.e.w(f"{tmp} = NA")
            self.gen_forin(node, tmp)
        elif isinstance(node, While):
            self.e.w(f"{tmp} = NA")
            self.gen_while(node, tmp)
        else:
            self.e.w(f"{tmp} = {self.expr(node)}")

    def gen_for(self, s: For, tmp):
        e = self.e
        a, b = self.expr(s.start), self.expr(s.end)
        st = self.expr(s.step) if s.step is not None else "None"
        self.push_scope("block")
        v = self.declare(s.var, None, False, s)
        g = self.new("g")
        e.w(f"{g} = 0")
        e.w(f"for {v.py} in rt_prange({a}, {b}, {st}):")
        e.ind += 1
        e.w(f"{g} += 1")
        e.w(f"if {g} > 20000: raise rt.LoopGuard('for')")
        if v.kind == "lhist":
            e.w(f"{v.slot}.append({v.py})")
        if tmp:
            self.gen_block_valued(s.body, tmp)
        else:
            self.gen_block(s.body)
        e.ind -= 1
        self.pop_scope()

    def gen_forin(self, s: ForIn, tmp):
        e = self.e
        it = self.expr(s.iterable)
        self.push_scope("block")
        vs = [self.declare(n, None, False, s) for n in s.names]
        if len(vs) == 1:
            e.w(f"for {vs[0].py} in list({it}):")
        else:
            e.w(f"for {vs[0].py}, {vs[1].py} in enumerate(list({it})):")
        e.ind += 1
        if tmp:
            self.gen_block_valued(s.body, tmp)
        else:
            self.gen_block(s.body)
        e.ind -= 1
        self.pop_scope()

    def gen_while(self, s: While, tmp):
        e = self.e
        g = self.new("g")
        e.w(f"{g} = 0")
        e.w(f"while {self.cond(s.cond)}:")
        e.ind += 1
        e.w(f"{g} += 1")
        e.w(f"if {g} > 20000: raise rt.LoopGuard('while')")
        self.push_scope("block")
        if tmp:
            self.gen_block_valued(s.body, tmp)
        else:
            self.gen_block(s.body)
        self.pop_scope()
        e.ind -= 1

    def gen_switch(self, s: Switch, tmp):
        e = self.e
        subj = None
        if s.subject is not None:
            subj = self.new("sw")
            e.w(f"{subj} = {self.expr(s.subject)}")
        first = True
        default = None
        for cond, body in s.cases:
            if cond is None:
                default = body
                continue
            c = f"{subj} == {self.expr(cond)}" if subj else self.cond(cond)
            e.w(("if " if first else "elif ") + c + ":")
            first = False
            e.ind += 1
            self.push_scope("block")
            if tmp:
                self.gen_block_valued(body, tmp)
            else:
                self.gen_block(body)
            self.pop_scope()
            e.ind -= 1
        if default is not None:
            if first:
                e.w("if True:")
            else:
                e.w("else:")
            e.ind += 1
            self.push_scope("block")
            if tmp:
                self.gen_block_valued(default, tmp)
            else:
                self.gen_block(default)
            self.pop_scope()
            e.ind -= 1
        elif tmp:
            if first:
                e.w(f"{tmp} = NA")
            else:
                e.w("else:")
                e.w(f"    {tmp} = NA")

    def gen_funcdef(self, s: FuncDef):
        if s.name in self.funcs and not s.method:
            pass
        py = f"f_{re.sub(chr(92) + 'W', '_', s.name)}_{len(self.funcs)}"
        fn = Func(s.name, py, s, [], s.method)
        key = s.name if not s.method else "method:" + s.name
        self.funcs[key] = fn
        saved_func, saved_e = self.cur_func, self.e
        self.cur_func = fn
        self.push_scope("func", fn)
        params = []
        pro = []
        for pname, default, typ in s.params:
            v = self.declare(pname, None, False, s)
            if v.kind == "plain":
                v.kind = "param"
            params.append((v, default))
        fn.param_py = [v.py for v, _ in params]
        sub = Emitter()
        sub.ind = saved_e.ind + 1
        self.e = sub
        for v, default in params:
            if v.kind == "lhist":
                sub.w(f"{v.slot}.append({v.py})")
        tmp = self.new("ret")
        mark = len(sub.lines)
        self.gen_block_valued(s.body, tmp)
        sub.w(f"return {tmp}")
        pl = [x for x in fn.plain_locals if x not in fn.param_py]
        if pl:
            sub.lines.insert(mark, "    " * sub.ind + " = ".join(pl) + " = NA")
        self.e = saved_e
        self.pop_scope()
        self.cur_func = saved_func
        sig = ", ".join(["_C"] + [f"{v.py}={self.const_default(d)}" if d is not None else v.py for v, d in params])
        saved_e.w(f"def {py}({sig}):")
        saved_e.lines.extend(sub.lines)

    def const_default(self, node) -> str:
        try:
            return self.expr(node)
        except Exception:
            return "NA"

    def gen_typedef(self, s: TypeDef):
        self.types[s.name] = s.fields
        e = self.e
        names = [f for f, _, _ in s.fields]
        defaults = [self.expr(d) if d is not None else "NA" for _, _, d in s.fields]
        cls = f"U_{s.name}"
        e.w(f"class {cls}:")
        e.w(f"    __slots__ = {tuple(names)!r}")
        args = ", ".join(f"{n}={d}" for n, d in zip(names, defaults)) or ""
        e.w(f"    def __init__(self{', ' + args if args else ''}):")
        if names:
            for n in names:
                e.w(f"        self.{n} = {n}")
        else:
            e.w("        pass")
        self.flags.add("udt")

    # ---- expressions
    def expr(self, n) -> str:
        m = getattr(self, "x_" + type(n).__name__, None)
        if m is None:
            raise CompileError(f"unsupported expression {type(n).__name__}")
        return m(n)

    def x_Num(self, n):
        return repr(n.v)

    def x_Str(self, n):
        return repr(n.v)

    def x_Bool(self, n):
        return "True" if n.v else "False"

    def x_Na(self, n):
        return "NA"

    def x_Color(self, n):
        return repr(n.v)

    def x_TupleLit(self, n):
        return "(" + ", ".join(self.expr(x) for x in n.items) + ("," if len(n.items) == 1 else "") + ")"

    def forward(self, name: str):
        """Create the Var of a top-level variable referenced before its declaration."""
        if name in self.toplevel_decl and name not in self.scope_global.vars:
            mode = self.toplevel_decl[name]
            saved, saved_f = self.scope, self.cur_func
            self.scope, self.cur_func = self.scope_global, None
            v = self.declare(name, mode, True)
            self.scope, self.cur_func = saved, saved_f
            v.forward = True
            self.flags.add("forward_reference")
            return v
        return None

    def x_Name(self, n):
        v = self.scope.lookup(n.id) or self.forward(n.id)
        if v is not None:
            return v.py
        nid = n.id
        if nid in SERIES:
            arr = SERIES[nid]
            return "i" if arr == "BI" else f"{arr}[i]"
        if nid in LEGACY_CONST:
            return LEGACY_CONST[nid]
        if nid in COLORS:
            return repr(nid)
        if nid == "float":
            return "'float'"
        if nid in ("syminfo", "timeframe", "strategy", "barstate", "math", "ta", "request", "str", "array", "color"):
            raise CompileError(f"bare namespace {nid}")
        if nid in self.types:
            return f"U_{nid}"
        raise CompileError(f"unknown name {nid}")

    def x_Attr(self, n):
        name = dotted(n)
        if name is not None:
            root = name.split(".")[0]
            known = name in SERIES or name in ATTR_CONST or name in BROKER_ATTR or root in OPAQUE_NS
            if self.scope.lookup(root) is None or (root in BUILTIN_NS and known):
                if name in SERIES:
                    arr = SERIES[name]
                    return f"{arr}[i]"
                if name in ATTR_CONST:
                    return ATTR_CONST[name]
                if name in BROKER_ATTR:
                    return BROKER_ATTR[name]
                if root in OPAQUE_NS or root == "strategy":
                    return repr(name)
                if root in self.types:
                    return f"U_{name}"
                raise CompileError(f"unknown attribute {name}")
        return f"{self.expr(n.obj)}.{n.attr}"

    def x_UnOp(self, n):
        a = self.expr(n.a)
        if n.op == "-":
            return f"(-{a})"
        if n.op == "+":
            return f"(+{a})"
        return f"(not {a})" if self.is_bool(n.a) else f"(not T_({a}))"

    def _const_int(self, n) -> bool:
        if isinstance(n, Num):
            return isinstance(n.v, int)
        if isinstance(n, UnOp) and n.op in "-+":
            return self._const_int(n.a)
        if isinstance(n, BinOp) and n.op in ("+", "-", "*"):
            return self._const_int(n.a) and self._const_int(n.b)
        return False

    def _const_timestamp(self, n) -> bool:
        if not isinstance(n, Call) or dotted(n.func) != "timestamp":
            return False
        time_names = {"year", "month", "dayofmonth", "hour", "minute", "time", "dayofweek", "timenow", "time_close"}
        def dyn(x):
            if isinstance(x, Name):
                return x.id in time_names
            if isinstance(x, (Call, Attr, Index)):
                return True
            if isinstance(x, BinOp):
                return dyn(x.a) or dyn(x.b)
            return False
        return not any(dyn(a) for a in n.args)

    def x_BinOp(self, n):
        op = n.op
        if op in ("and", "or"):
            a, b = self.expr(n.a), self.expr(n.b)
            ab = a if self.is_bool(n.a) else f"T_({a})"
            bb = b if self.is_bool(n.b) else f"T_({b})"
            if self.version >= 6 or self.pure(n.b):
                return f"({ab} {op} {bb})"
            return f"rt.{op.upper()}({a}, {b})"
        # neutralise hard-coded backtest windows: time >= timestamp(2019, ...) / time <= timestamp(2021, ...)
        if op in (">=", ">", "<=", "<") and ("date_windows" in self.flags or True):
            lt = isinstance(n.a, Name) and n.a.id == "time"
            rt_ = isinstance(n.b, Name) and n.b.id == "time"
            if lt and self._const_timestamp(n.b):
                return "True"
            if rt_ and self._const_timestamp(n.a):
                return "True"
        if op in ("==", "!="):
            if isinstance(n.b, Na):
                return f"rt.isna({self.expr(n.a)})" if op == "==" else f"(not rt.isna({self.expr(n.a)}))"
            if isinstance(n.a, Na):
                return f"rt.isna({self.expr(n.b)})" if op == "==" else f"(not rt.isna({self.expr(n.b)}))"
        a, b = self.expr(n.a), self.expr(n.b)
        if op == "/":
            if self.version <= 5 and self._const_int(n.a) and self._const_int(n.b):
                return f"rt.idiv({a}, {b})"
            return f"div({a}, {b})"
        if op == "%":
            return f"rt.mod({a}, {b})"
        return f"({a} {op} {b})"

    def pure(self, n) -> bool:
        """True if evaluating n has no side effects on call-site state (safe to short-circuit)."""
        if isinstance(n, (Num, Str, Bool, Na, Color, Name)):
            return True
        if isinstance(n, Attr):
            return True
        if isinstance(n, (BinOp,)):
            return self.pure(n.a) and self.pure(n.b)
        if isinstance(n, UnOp):
            return self.pure(n.a)
        if isinstance(n, Ternary):
            return self.pure(n.c) and self.pure(n.a) and self.pure(n.b)
        if isinstance(n, Index):
            return isinstance(n.obj, (Name, Attr)) and self.pure(n.idx)
        if isinstance(n, Call):
            f = dotted(n.func) or ""
            if f in ("na", "nz", "math.abs", "abs", "math.max", "math.min", "max", "min", "int", "float"):
                return all(self.pure(a) for a in n.args)
            return False
        return False

    def x_Ternary(self, n):
        return f"({self.expr(n.a)} if {self.cond(n.c)} else {self.expr(n.b)})"

    def x_Index(self, n):
        idx = self.expr(n.idx)
        obj = n.obj
        if isinstance(obj, Name):
            v = self.scope.lookup(obj.id) or self.forward(obj.id)
            if v is not None:
                if v.kind == "ghist":
                    return f"hist({v.py}_h, {idx})"
                if v.kind == "lhist":
                    buf = f"{v.slot}.v" if v.mode else v.slot
                    if id(v) in self.pre_decl:
                        return f"hist_pre({buf}, {idx})"
                    return f"hist({buf}, {idx})"
                # untracked (should not happen): fall back to call-site history of the value
                s = self.slot("rt.HBuf()")
                return f"{s}.pg({v.py}, {idx})"
            if obj.id in SERIES:
                arr = SERIES[obj.id]
                if arr == "BI":
                    return f"bar(BI, i, {idx})"
                return f"bar({arr}, i, {idx})"
        name = dotted(obj)
        if name in SERIES and self.scope.lookup(name.split(".")[0]) is None:
            return f"bar({SERIES[name]}, i, {idx})"
        if name in BROKER_HIST and self.scope.lookup("strategy") is None:
            return f"hist({BROKER_HIST[name]}, {idx})"
        s = self.slot("rt.HBuf()")
        return f"{s}.pg({self.expr(obj)}, {idx})"

    # series accessor for window functions
    def acc(self, node) -> tuple[str, str]:
        if isinstance(node, Name):
            v = self.scope.lookup(node.id)
            if v is not None:
                if v.kind == "ghist":
                    return f"{v.py}_h", f"len({v.py}_h)"
                if v.kind == "lhist" and not v.mode and id(v) not in self.pre_decl:
                    return v.slot, f"len({v.slot})"
            elif node.id in SERIES:
                return SERIES[node.id], "i + 1"
        name = dotted(node)
        if name in SERIES and self.scope.lookup(name.split(".")[0]) is None:
            return SERIES[name], "i + 1"
        s = self.slot("rt.HBuf()")
        b = self.new("b")
        return f"({b} := {s}.push({self.expr(node)}))", f"len({b})"

    def args_by(self, call: Call, names: list, fname: str) -> list:
        out = list(call.args[:len(names)])
        out += [None] * (len(names) - len(out))
        for k, v in call.kwargs:
            if k in names:
                out[names.index(k)] = v
        return out

    def x_Call(self, n):
        fname = dotted(n.func)
        if fname is None:
            # method call on an expression result: expr.method(...)
            if isinstance(n.func, Attr):
                return self.method_call(n, self.expr(n.func.obj), n.func.attr)
            raise CompileError("call of a non-name")
        root = fname.split(".")[0]
        # user function? (Pine keeps functions and variables apart: `rsi = rsi(close, 14)` is valid)
        if fname in self.funcs:
            return self.user_call(self.funcs[fname], [self.expr(a) for a in n.args], n.kwargs)
        # method-call syntax on a user variable: arr.push(x), obj.method()
        if "." in fname and self.scope.lookup(root) is not None and root not in BUILTIN_NS:
            objnode = n.func.obj
            return self.method_call(n, self.expr(objnode), n.func.attr)
        if root in self.types and fname.endswith(".new"):
            args = [self.expr(a) for a in n.args] + [f"{k}={self.expr(v)}" for k, v in n.kwargs]
            return f"U_{root}({', '.join(args)})"
        if root in self.types and fname.endswith(".copy"):
            return f"rt_copy({self.expr(n.args[0])})"
        h = self.builtin_call(fname, n)
        if h is None:
            raise CompileError(f"unsupported function {fname}")
        return h

    def method_call(self, n: Call, obj: str, meth: str) -> str:
        key = "method:" + meth
        if key in self.funcs:
            return self.user_call(self.funcs[key], [obj] + [self.expr(a) for a in n.args], n.kwargs)
        if meth in ARRAY:
            args = [obj] + [self.expr(a) for a in n.args]
            return f"rt.{ARRAY[meth]}({', '.join(args)})"
        if meth.startswith("set_") or meth in ("delete", "copy"):
            if meth in ("set_xy1", "set_xy2"):
                return f"rt.line_{meth}({obj}, {', '.join(self.expr(a) for a in n.args)})"
            return "None"
        if meth.startswith("get_"):
            if meth == "get_price":
                return f"rt.line_get_price({obj}, {self.expr(n.args[0])})"
            return f"rt.drawing_get({meth[4:]!r})({obj})"
        raise CompileError(f"unsupported method {meth}")

    def user_call(self, fn: Func, args: list, kwargs) -> str:
        k = len(self.root_slots) if self.cur_func is None else len(self.cur_func.slots)
        path = self.path_expr(k)
        slot = self.slot("None")
        ctx = self.ctx_expr()
        kw = [f"{self.kwname(fn, name)}={self.expr(v)}" for name, v in kwargs]
        return f"{fn.py}(rt.child({ctx}, {k}, lambda: {fn.py}_fac({path})), {', '.join(args + kw)})"

    def kwname(self, fn: Func, name: str) -> str:
        names = [p[0] for p in fn.node.params]
        if name not in names:
            raise CompileError(f"unknown keyword {name} for {fn.name}")
        return fn.param_py[names.index(name)]

    # ---- builtins
    def builtin_call(self, fname: str, n: Call) -> Optional[str]:
        ver = self.version
        base = fname[3:] if fname.startswith("ta.") else fname
        is_ta = fname.startswith("ta.") or fname in TA_V4
        E = self.expr
        a = n.args
        kw = dict(n.kwargs)

        if fname in ("na",):
            return f"rt.isna({E(a[0])})" if a else "NA"
        if fname == "nz":
            if len(a) >= 2:
                return f"rt.nz({E(a[0])}, {E(a[1])})"
            return f"rt.nz({E(a[0])})"
        if fname == "iff":
            return f"rt.iff({E(a[0])}, {E(a[1])}, {E(a[2])})"
        if fname == "fixnan":
            return f"{self.slot('rt.FixNan()')}.u({E(a[0])})"
        if fname in ("int",):
            return f"rt.to_int({E(a[0])})"
        if fname in ("float",):
            return f"rt.to_float({E(a[0])})"
        if fname in ("bool",):
            return f"rt.to_bool({E(a[0])})"
        if fname in ("string", "color", "line", "label", "box", "table", "linefill"):
            return E(a[0]) if a else "NA"
        if fname in ("tostring", "str.tostring"):
            return f"rt.s_tostring({', '.join(E(x) for x in a)})"
        if fname.startswith("str."):
            m = STR.get(fname[4:])
            if m:
                return f"rt.{m}({', '.join(E(x) for x in a)})"
            return "''"
        if fname == "offset":
            return self.x_Index(Index(a[0], a[1]))
        if fname in MATH or fname.startswith("math."):
            key = fname[5:] if fname.startswith("math.") else fname
            if key == "sum":
                src, length = self.args_by(n, ["source", "length"], fname)
                arr, end = self.acc(src)
                return f"rt.w_sum({arr}, {end}, {E(length)})"
            if key == "round_to_mintick":
                return f"rt.m_round(div({E(a[0])}, cfg['mintick'])) * cfg['mintick']"
            if key in MATH:
                return f"rt.{MATH[key]}({', '.join(E(x) for x in a)})"
            return None
        if fname == "timestamp":
            return f"rt.timestamp({', '.join(E(x) for x in a)})"
        if fname in ("year", "month", "dayofmonth", "dayofweek", "hour", "minute", "second", "weekofyear"):
            fn = {"dayofmonth": "t_dayofmonth"}.get(fname, "t_" + fname)
            return f"rt.{fn}({', '.join(E(x) for x in a)})"
        if fname in ("time", "time_close"):
            tf = E(a[0]) if a else "''"
            sess = E(a[1]) if len(a) > 1 else (E(kw["session"]) if "session" in kw else "None")
            tz = E(a[2]) if len(a) > 2 else (E(kw["timezone"]) if "timezone" in kw else "None")
            return f"rt_time(D, cfg, i, {tf}, {sess}, {tz}, {fname == 'time_close'})"
        if fname in ("input", "input.int", "input.float", "input.bool", "input.string", "input.source",
                     "input.timeframe", "input.session", "input.symbol", "input.time", "input.color", "input.price",
                     "input.text_area", "input.resolution", "input.integer", "input.enum"):
            return self.input_call(fname, n)
        if fname in ("request.security", "security"):
            return self.security_call(n)
        if fname in ("ticker.heikinashi", "heikinashi"):
            return "('HA:' + str(" + (E(a[0]) if a else "cfg['tickerid']") + "))"
        if fname in ("ticker.new",):
            return f"(str({E(a[0])}) + ':' + str({E(a[1])}))"
        if fname in ("ticker.standard", "ticker.modify"):
            return E(a[0]) if a else "cfg['tickerid']"
        if fname.startswith("strategy."):
            return self.strategy_call(fname, n)
        if fname in ("color.new",):
            return E(a[0]) if a else "'#000000'"
        if fname == "color.rgb":
            return f"rt.color_rgb({', '.join(E(x) for x in a)})"
        if fname.startswith("color."):
            return "'#000000'" if fname != "color.t" else "0"
        if fname in ("line.new", "label.new", "box.new", "table.new", "polyline.new"):
            if fname == "line.new":
                args = [E(x) for x in a[:4]]
                return f"rt.line_new({', '.join(args)})"
            return "rt.drawing_new()"
        if fname == "line.get_price":
            return f"rt.line_get_price({E(a[0])}, {E(a[1])})"
        if fname in ("line.set_xy1", "line.set_xy2"):
            return f"rt.{fname.replace('.', '_')}({', '.join(E(x) for x in a)})"
        if fname.split(".")[0] in DRAW_NS:
            if ".get_" in fname:
                return f"rt.drawing_get({fname.split('.get_')[1]!r})({E(a[0]) if a else 'None'})"
            return "None"
        if fname in NOOP_CALLS:
            return "None"
        if fname == "runtime.error":
            return f"rt.runtime_error({E(a[0]) if a else repr('')})"
        if fname.startswith("array."):
            meth = fname[6:]
            if meth.startswith("new"):
                if meth in ("new_float", "new_int", "new_bool", "new_string", "new_color", "new_line", "new_label",
                            "new_box", "new_table", "new"):
                    size = E(a[0]) if a else (E(kw["size"]) if "size" in kw else "0")
                    init = E(a[1]) if len(a) > 1 else (E(kw["initial_value"]) if "initial_value" in kw else "NA")
                    return f"rt.a_new({size}, {init})"
            if meth in ARRAY:
                return f"rt.{ARRAY[meth]}({', '.join(E(x) for x in a)})"
            raise CompileError(f"unsupported array function {fname}")
        if fname.startswith("matrix.") or fname.startswith("map."):
            raise Unsupported(f"{fname.split('.')[0]} functions")
        if fname.startswith("request."):
            raise Unsupported(f"{fname}")
        if is_ta:
            return self.ta_call(base, n)
        return None

    def input_call(self, fname: str, n: Call) -> str:
        E = self.expr
        kw = dict(n.kwargs)
        defval = kw.get("defval")
        if defval is None and n.args:
            defval = n.args[0]
        title = kw.get("title")
        if title is None and len(n.args) > 1 and isinstance(n.args[1], Str):
            title = n.args[1]
        title_s = title.v if isinstance(title, Str) else ""
        varname = self.decl_name_stack[-1] if self.decl_name_stack else ""
        if defval is None:
            return "NA"
        words = _words(varname) | _words(title_s)
        is_start = bool(words & START_WORDS)
        is_end = bool(words & END_WORDS)
        if is_start != is_end:
            if isinstance(defval, Call) and dotted(defval.func) == "timestamp" or fname == "input.time" or \
                    (isinstance(defval, Num) and defval.v >= 1e11):
                self.flags.add("date_window_neutralised")
                return "0" if is_start else "4102444800000"
            if isinstance(defval, Num) and isinstance(defval.v, int):
                v = defval.v
                if ("year" in words or "yr" in words or "yy" in words or "yyyy" in words) and 1970 <= v <= 9999:
                    self.flags.add("date_window_neutralised")
                    return "1970" if is_start else "2100"
                if ("month" in words or "mon" in words or "mm" in words or "m" in words) and 1 <= v <= 12:
                    self.flags.add("date_window_neutralised")
                    return "1" if is_start else "12"
                if ("day" in words or "dd" in words or "d" in words) and 1 <= v <= 31 and "dayofweek" not in words:
                    self.flags.add("date_window_neutralised")
                    return "1" if is_start else "31"
        return E(defval)

    def security_call(self, n: Call) -> str:
        E = self.expr
        names = ["symbol", "timeframe", "expression", "gaps", "lookahead", "ignore_invalid_symbol", "currency"]
        if self.version <= 2 and len(n.args) >= 3 and not n.kwargs:
            pass
        args = self.args_by(n, names, "security")
        if "resolution" in dict(n.kwargs):
            args[1] = dict(n.kwargs)["resolution"]
        if "expression" not in dict(n.kwargs) and args[2] is None and "series" in dict(n.kwargs):
            args[2] = dict(n.kwargs)["series"]
        sym, tf, expr_node, gaps = args[0], args[1], args[2], args[3]
        if expr_node is None:
            raise CompileError("security without expression")
        k = len(self.root_slots) if self.cur_func is None else len(self.cur_func.slots)
        self.slot("None")
        key = self.path_expr(k)
        self.sec_sites += 1
        sym_s = E(sym) if sym is not None else "cfg['tickerid']"
        tf_s = E(tf) if tf is not None else "''"
        gaps_s = "True" if gaps is not None and dotted(gaps) == "barmerge.gaps_on" else "False"
        thunk = f"lambda: {E(expr_node)}"
        return f"SEC({key}, i, {sym_s}, {tf_s}, {gaps_s}, {thunk})"

    STRAT_SIGS = {
        "entry": {2: ["id", "long", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "when"],
                  4: ["id", "direction", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "when", "alert_message"],
                  5: ["id", "direction", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "alert_message", "disable_alert"]},
        "order": {2: ["id", "long", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "when"],
                  4: ["id", "direction", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "when", "alert_message"],
                  5: ["id", "direction", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "alert_message", "disable_alert"]},
        "exit": {2: ["id", "from_entry", "qty", "qty_percent", "profit", "limit", "loss", "stop", "trail_price",
                     "trail_points", "trail_offset", "oca_name", "comment", "when", "alert_message"],
                 5: ["id", "from_entry", "qty", "qty_percent", "profit", "limit", "loss", "stop", "trail_price",
                     "trail_points", "trail_offset", "oca_name", "comment", "comment_profit", "comment_loss",
                     "comment_trailing", "alert_message", "alert_profit", "alert_loss", "alert_trailing", "disable_alert"]},
        "close": {2: ["id", "when", "comment", "qty", "qty_percent", "alert_message"],
                  5: ["id", "comment", "qty", "qty_percent", "alert_message", "immediately", "disable_alert"]},
        "close_all": {2: ["when", "comment", "alert_message"], 5: ["comment", "alert_message", "immediately", "disable_alert"]},
        "cancel": {2: ["id", "when"], 5: ["id"]},
        "cancel_all": {2: ["when"], 5: []},
    }

    def sig(self, kind):
        table = self.STRAT_SIGS[kind]
        best = None
        for v in sorted(table):
            if self.version >= v:
                best = table[v]
        return best or table[min(table)]

    def strategy_call(self, fname: str, n: Call) -> str:
        E = self.expr
        kind = fname[len("strategy."):]
        if kind in ("entry", "order", "exit", "close", "close_all", "cancel", "cancel_all"):
            names = self.sig(kind)
            vals = dict(zip(names, n.args))
            for k, v in n.kwargs:
                vals[k] = v
            if "long" in vals and "direction" not in vals:
                vals["direction"] = vals.pop("long")

            def g(key, default="None"):
                v = vals.get(key)
                return E(v) if v is not None else default
            when = g("when", "True")
            if kind in ("entry", "order"):
                return (f"bk.{kind}({g('id')}, {g('direction')}, {g('qty')}, {g('limit')}, {g('stop')}, "
                        f"{g('oca_name')}, {when})")
            if kind == "exit":
                return (f"bk.exit({g('id')}, {g('from_entry')}, {g('qty')}, {g('qty_percent')}, {g('profit')}, "
                        f"{g('limit')}, {g('loss')}, {g('stop')}, {g('trail_price')}, {g('trail_points')}, "
                        f"{g('trail_offset')}, {when})")
            if kind == "close":
                return f"bk.close({g('id')}, {g('qty')}, {g('qty_percent')}, {g('immediately', 'False')}, {when})"
            if kind == "close_all":
                return f"bk.close_all({g('immediately', 'False')}, {when})"
            if kind == "cancel":
                return f"bk.cancel({g('id')}, {when})"
            if kind == "cancel_all":
                return f"bk.cancel_all({when})"
        if kind == "risk.allow_entry_in":
            return f"bk.allow_entry_in({E(n.args[0]) if n.args else repr('all')})"
        if kind.startswith("risk."):
            return "None"
        if kind.startswith("opentrades.") or kind.startswith("closedtrades."):
            which, attr = kind.split(".", 1)
            return f"bk.trade_attr({which!r}, {attr!r}, {E(n.args[0]) if n.args else '0'})"
        if kind in ("convert_to_account", "convert_to_symbol"):
            return E(n.args[0])
        if kind == "default_entry_qty":
            return "bk.default_qty(" + E(n.args[0]) + ")"
        raise CompileError(f"unsupported {fname}")

    def ta_call(self, base: str, n: Call) -> str:
        E = self.expr
        a = n.args
        if base in WIN1:
            fn, names = WIN1[base]
            vals = self.args_by(n, names, base)
            if base in ("change", "mom") and vals[1] is None:
                vals[1] = Num(1)
            if base == "swma":
                arr, end = self.acc(vals[0])
                return f"rt.w_swma({arr}, {end})"
            arr, end = self.acc(vals[0])
            rest = [E(v) for v in vals[1:] if v is not None]
            # keep positional defaults only up to the last provided optional arg
            lastk = max([k for k, v in enumerate(vals) if v is not None] + [1])
            rest = [E(v) if v is not None else "None" for v in vals[1:lastk + 1]]
            if base in ("stdev", "variance") and len(vals) > 2 and vals[2] is None:
                rest = rest[:1]
            return f"rt.{fn}({arr}, {end}{', ' if rest else ''}{', '.join(rest)})"
        if base in ("highest", "lowest", "highestbars", "lowestbars"):
            fn = {"highest": "w_highest", "lowest": "w_lowest", "highestbars": "w_highestbars", "lowestbars": "w_lowestbars"}[base]
            kw = dict(n.kwargs)
            if len(a) == 1 and not kw.get("source"):
                src = Name("high" if "highest" in base else "low")
                length = a[0]
            else:
                src = a[0] if a else kw.get("source")
                length = a[1] if len(a) > 1 else kw.get("length")
            arr, end = self.acc(src)
            return f"rt.{fn}({arr}, {end}, {E(length)})"
        if base in ("crossover", "crossunder", "cross"):
            fn = {"crossover": "cross_over", "crossunder": "cross_under", "cross": "cross_any"}[base]
            s1, s2 = self.args_by(n, ["source1", "source2"], base)
            a1, e1 = self.acc(s1)
            a2, e2 = self.acc(s2)
            return f"rt.{fn}({a1}, {e1}, {a2}, {e2})"
        if base == "correlation":
            s1, s2, ln = self.args_by(n, ["source1", "source2", "length"], base)
            a1, e1 = self.acc(s1)
            a2, e2 = self.acc(s2)
            return f"rt.w_correlation({a1}, {e1}, {a2}, {e2}, {E(ln)})"
        if base in ("pivothigh", "pivotlow"):
            fn = "pivot_high" if base == "pivothigh" else "pivot_low"
            kw = dict(n.kwargs)
            if len(a) + len(kw) == 2:
                src = Name("high" if base == "pivothigh" else "low")
                left, right = (a + [kw.get("leftbars"), kw.get("rightbars")])[:2] if len(a) < 2 else a[:2]
                if left is None:
                    left = kw.get("leftbars")
                if right is None:
                    right = kw.get("rightbars")
            else:
                src, left, right = self.args_by(n, ["source", "leftbars", "rightbars"], base)
            arr, end = self.acc(src)
            return f"rt.{fn}({arr}, {end}, {E(left)}, {E(right)})"
        if base == "vwma":
            src, ln = self.args_by(n, ["source", "length"], base)
            arr, end = self.acc(src)
            return f"rt.w_vwma({arr}, {end}, V, i + 1, {E(ln)})"
        if base == "stoch":
            src, hi, lo, ln = self.args_by(n, ["source", "high", "low", "length"], base)
            a1, e1 = self.acc(src)
            a2, e2 = self.acc(hi)
            a3, e3 = self.acc(lo)
            return f"rt.w_stoch({a1}, {e1}, {a2}, {e2}, {a3}, {e3}, {E(ln)})"
        if base == "wpr":
            (ln,) = self.args_by(n, ["length"], base)
            return f"(rt.w_stoch(C, i + 1, H, i + 1, L, i + 1, {E(ln)}) - 100)"
        if base == "cci":
            src, ln = self.args_by(n, ["source", "length"], base)
            arr, end = self.acc(src)
            return f"rt_cci({arr}, {end}, {E(ln)})"
        if base == "bb":
            src, ln, mult = self.args_by(n, ["series", "length", "mult"], base)
            arr, end = self.acc(src)
            return f"rt_bb({arr}, {end}, {E(ln)}, {E(mult)})"
        if base == "bbw":
            src, ln, mult = self.args_by(n, ["series", "length", "mult"], base)
            arr, end = self.acc(src)
            return f"rt_bbw({arr}, {end}, {E(ln)}, {E(mult)})"
        if base == "hma":
            src, ln = self.args_by(n, ["source", "length"], base)
            arr, end = self.acc(src)
            return f"{self.slot('rt.HMA()')}.u({arr}, {end}, {E(ln)})"
        if base == "ema":
            src, ln = self.args_by(n, ["source", "length"], base)
            return f"{self.slot('rt.EMA()')}.u({E(src)}, {E(ln)})"
        if base == "rma":
            src, ln = self.args_by(n, ["source", "length"], base)
            return f"{self.slot('rt.RMA()')}.u({E(src)}, {E(ln)})"
        if base == "rsi":
            src, ln = self.args_by(n, ["source", "length"], base)
            return f"{self.slot('rt.RSI()')}.u({E(src)}, {E(ln)})"
        if base == "atr":
            (ln,) = self.args_by(n, ["length"], base)
            return f"{self.slot('rt.ATR()')}.u(TR1[i], {E(ln)})"
        if base == "tr":
            (hn,) = self.args_by(n, ["handle_na"], base)
            if hn is None:
                return "TR[i]"
            return f"(TR1[i] if T_({E(hn)}) else TR[i])"
        if base == "cum":
            (src,) = self.args_by(n, ["source"], base)
            return f"{self.slot('rt.Cum()')}.u({E(src)})"
        if base in ("max", "min"):
            (src,) = self.args_by(n, ["source"], base)
            return f"{self.slot('rt.MaxAll()' if base == 'max' else 'rt.MinAll()')}.u({E(src)})"
        if base == "valuewhen":
            c, src, occ = self.args_by(n, ["condition", "source", "occurrence"], base)
            return f"{self.slot('rt.ValueWhen()')}.u({E(c)}, {E(src)}, {E(occ) if occ is not None else '0'})"
        if base == "barssince":
            (c,) = self.args_by(n, ["condition"], base)
            return f"{self.slot('rt.BarsSince()')}.u({E(c)})"
        if base == "macd":
            src, f, s, g = self.args_by(n, ["source", "fastlen", "slowlen", "siglen"], base)
            return f"{self.slot('rt.MACD()')}.u({E(src)}, {E(f)}, {E(s)}, {E(g)})"
        if base in ("kc", "kcw"):
            src, ln, mult, utr = self.args_by(n, ["series", "length", "mult", "useTrueRange"], base)
            rng = f"(TR1[i] if T_({E(utr)}) else (H[i] - L[i]))" if utr is not None else "TR1[i]"
            call = f"{self.slot('rt.KC()')}.u({E(src)}, {E(ln)}, {E(mult)}, {rng})"
            if base == "kcw":
                t = self.new("kc")
                return f"rt_kcw({call})"
            return call
        if base == "supertrend":
            fac, per = self.args_by(n, ["factor", "atrPeriod"], base)
            return f"{self.slot('rt.Supertrend()')}.u({E(fac)}, {E(per)}, H[i], L[i], C[i], TR1[i])"
        if base == "dmi":
            dil, adxl = self.args_by(n, ["diLength", "adxSmoothing"], base)
            return f"{self.slot('rt.DMI()')}.u({E(dil)}, {E(adxl)}, H[i], L[i], TR1[i])"
        if base == "sar":
            st, inc, mx = self.args_by(n, ["start", "inc", "max"], base)
            return f"{self.slot('rt.SAR()')}.u({E(st)}, {E(inc)}, {E(mx)}, H[i], L[i], C[i])"
        if base == "tsi":
            src, sh, lo = self.args_by(n, ["source", "short_length", "long_length"], base)
            return f"{self.slot('rt.TSI()')}.u({E(src)}, {E(sh)}, {E(lo)})"
        if base == "mfi":
            src, ln = self.args_by(n, ["series", "length"], base)
            return f"{self.slot('rt.MFI()')}.u({E(src)}, V[i], {E(ln)})"
        if base == "vwap":
            (src,) = self.args_by(n, ["source"], base)
            if src is None:
                return "VWAP[i]"
            return f"{self.slot('rt.VWAP()')}.u({E(src)}, V[i], T[i])"
        raise CompileError(f"unsupported ta function {base}")


def compile_source(src: str) -> tuple[str, dict, set]:
    stmts = P.parse(src)
    c = Compiler(stmts, P.version_of(src), src)
    code = c.compile()
    return code, c.cfg, c.flags
