"""Lenient Pine Script (v1-v6) lexer and parser producing a small AST.

Covers what the vault's 5,283 scripts use: declarations (var/varip, typed, tuple), reassignments (:= += -= *= /= %=),
if/else-if/else, for..to..by, for..in, while, switch (with and without subject), single-line and block functions,
methods, user types (fields only), break/continue, ternaries, history references, calls with keyword arguments,
generic calls (array.new<float>), tuple literals, colour literals. Line continuation follows Pine's rule (a line whose
indentation is not a multiple of four continues the previous one) plus open brackets and dangling operators.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, Optional


class PineSyntaxError(Exception):
    pass


class Unsupported(Exception):
    pass


# ----------------------------------------------------------------------------------------------------------- lexer
TOKEN_RE = re.compile(r"""
 (?P<ws>[ \t\f\v 　]+)
|(?P<comment>//[^\n]*)
|(?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
|(?P<string>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
|(?P<color>\#[0-9A-Fa-f]{8}|\#[0-9A-Fa-f]{6}|\#[0-9A-Fa-f]{3})
|(?P<name>[^\W\d]\w*)
|(?P<op>:=|\+=|-=|\*=|/=|%=|==|!=|<=|>=|=>|[-+*/%<>=?:()\[\],.])
""", re.X | re.U)

BLOCK_COMMENT = re.compile(r"/\*backtest.*?\*/|^[ \t]*/\*.*?\*/", re.S | re.M)
KEYWORDS = {"if", "else", "for", "to", "by", "while", "switch", "var", "varip", "and", "or", "not", "true", "false",
            "na", "break", "continue", "in", "import", "export", "method", "type"}
CONT_END = {"+", "-", "*", "/", "%", "==", "!=", "<", ">", "<=", ">=", "and", "or", "not", "?", ":", ",", "(", "[",
            ":=", "=", "+=", "-=", "*=", "/=", "%="}
CONT_START = {"and", "or", "?", ":", "*", "/", "%", "==", "!=", "<=", ">=", ")", "]", ",", "."}


@dataclass(slots=True)
class Tok:
    kind: str   # NAME NUM STR COLOR OP NL IND DED EOF
    val: Any
    line: int

    def __repr__(self):
        return f"{self.kind}:{self.val!r}@{self.line}"


def _unescape(s: str) -> str:
    body = s[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), body)


def _tokenize_line(text: str, lineno: int) -> list[Tok]:
    out, pos, n = [], 0, len(text)
    while pos < n:
        m = TOKEN_RE.match(text, pos)
        if not m:
            ch = text[pos]
            if ch in "﻿​":
                pos += 1
                continue
            raise PineSyntaxError(f"line {lineno}: unexpected character {ch!r}")
        kind = m.lastgroup
        v = m.group(kind)
        pos = m.end()
        if kind in ("ws", "comment"):
            continue
        if kind == "number":
            out.append(Tok("NUM", float(v) if any(c in v for c in ".eE") else int(v), lineno))
        elif kind == "string":
            out.append(Tok("STR", _unescape(v), lineno))
        elif kind == "color":
            out.append(Tok("COLOR", v, lineno))
        elif kind == "name":
            out.append(Tok("NAME", v, lineno))
        else:
            out.append(Tok("OP", v, lineno))
    return out


def _indent_width(raw: str) -> int:
    w = 0
    for ch in raw:
        if ch == " ":
            w += 1
        elif ch == "\t":
            w += 4
        elif ch in "﻿ ":
            w += 1 if ch == " " else 0
        else:
            break
    return w


def _opens_block(toks: list[Tok]) -> bool:
    first, last = toks[0], toks[-1]
    if first.kind == "NAME" and first.val in ("if", "else", "for", "while", "switch", "type"):
        return True
    if last.kind == "OP" and last.val == "=>":
        return True
    for a, b in zip(toks, toks[1:]):
        if a.kind == "OP" and a.val in ("=", ":=") and b.kind == "NAME" and b.val in ("if", "switch", "for", "while"):
            return True
    return False


def tokenize(src: str) -> list[Tok]:
    src = BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), src.replace("\r\n", "\n").replace("\r", "\n"))
    logical: list[tuple[int, list[Tok]]] = []
    depth = 0
    for lineno, raw in enumerate(src.split("\n"), 1):
        toks = _tokenize_line(raw, lineno)
        if not toks:
            continue
        ind = _indent_width(raw)
        cont = False
        if logical:
            prev_ind, prev_toks = logical[-1]
            prev = prev_toks[-1]
            if depth > 0:
                cont = True
            elif prev.kind == "OP" and prev.val in CONT_END and prev.val != "=>":
                cont = True
            elif prev.kind == "NAME" and prev.val in ("and", "or", "not"):
                cont = True
            elif toks[0].kind in ("OP", "NAME") and toks[0].val in CONT_START and not (toks[0].kind == "OP" and toks[0].val in ("(", "[")):
                cont = True
            elif ind % 4 != 0 and ind > prev_ind and not _opens_block(prev_toks):
                cont = True
        if cont:
            logical[-1][1].extend(toks)
        else:
            logical.append((ind, toks))
        for t in toks:
            if t.kind == "OP":
                if t.val in ("(", "["):
                    depth += 1
                elif t.val in (")", "]"):
                    depth = max(0, depth - 1)
    out: list[Tok] = []
    stack = [0]
    for ind, toks in logical:
        line = toks[0].line
        if ind > stack[-1]:
            stack.append(ind)
            out.append(Tok("IND", ind, line))
        else:
            while ind < stack[-1]:
                stack.pop()
                out.append(Tok("DED", ind, line))
            if ind != stack[-1]:          # inconsistent dedent: treat as the nearest enclosing level
                stack.append(ind)
                out.append(Tok("IND", ind, line))
        out.extend(toks)
        out.append(Tok("NL", None, line))
    while len(stack) > 1:
        stack.pop()
        out.append(Tok("DED", 0, 0))
    out.append(Tok("EOF", None, 0))
    return out


# ----------------------------------------------------------------------------------------------------------- AST
class Node:
    __slots__ = ("line",)


@dataclass(eq=False)
class Num(Node):
    v: Any
    line: int = 0


@dataclass(eq=False)
class Str(Node):
    v: str
    line: int = 0


@dataclass(eq=False)
class Bool(Node):
    v: bool
    line: int = 0


@dataclass(eq=False)
class Na(Node):
    line: int = 0


@dataclass(eq=False)
class Color(Node):
    v: str
    line: int = 0


@dataclass(eq=False)
class Name(Node):
    id: str
    line: int = 0


@dataclass(eq=False)
class Attr(Node):
    obj: Node
    attr: str
    line: int = 0


@dataclass(eq=False)
class Call(Node):
    func: Node
    args: list
    kwargs: list          # [(name, node)]
    targs: Optional[list] = None
    line: int = 0


@dataclass(eq=False)
class Index(Node):
    obj: Node
    idx: Node
    line: int = 0


@dataclass(eq=False)
class BinOp(Node):
    op: str
    a: Node
    b: Node
    line: int = 0


@dataclass(eq=False)
class UnOp(Node):
    op: str
    a: Node
    line: int = 0


@dataclass(eq=False)
class Ternary(Node):
    c: Node
    a: Node
    b: Node
    line: int = 0


@dataclass(eq=False)
class TupleLit(Node):
    items: list
    line: int = 0


# statements
@dataclass(eq=False)
class Decl(Node):
    names: list           # one name, or several for tuple declarations
    mode: Optional[str]   # None | 'var' | 'varip'
    typ: Optional[str]
    value: Node           # expression or block-expression (If/Switch/For/While)
    line: int = 0


@dataclass(eq=False)
class Assign(Node):
    target: Node          # Name or Attr
    op: str               # := += -= *= /= %=
    value: Node
    line: int = 0


@dataclass(eq=False)
class ExprStmt(Node):
    expr: Node
    line: int = 0


@dataclass(eq=False)
class If(Node):
    cond: Node
    body: list
    orelse: Optional[list]
    line: int = 0


@dataclass(eq=False)
class For(Node):
    var: str
    start: Node
    end: Node
    step: Optional[Node]
    body: list
    line: int = 0


@dataclass(eq=False)
class ForIn(Node):
    names: list           # [x] or [i, x]
    iterable: Node
    body: list
    line: int = 0


@dataclass(eq=False)
class While(Node):
    cond: Node
    body: list
    line: int = 0


@dataclass(eq=False)
class Switch(Node):
    subject: Optional[Node]
    cases: list           # [(cond or None, body list)]
    line: int = 0


@dataclass(eq=False)
class FuncDef(Node):
    name: str
    params: list          # [(name, default node or None, type str or None)]
    body: list
    method: bool = False
    line: int = 0


@dataclass(eq=False)
class TypeDef(Node):
    name: str
    fields: list          # [(name, type, default node or None)]
    line: int = 0


@dataclass(eq=False)
class Break(Node):
    line: int = 0


@dataclass(eq=False)
class Continue(Node):
    line: int = 0


# ----------------------------------------------------------------------------------------------------------- parser
TYPE_QUALIFIERS = {"series", "simple", "const", "input"}
BASE_TYPES = {"float", "int", "bool", "string", "color", "line", "label", "box", "table", "linefill", "polyline",
              "chart.point", "array", "matrix", "map"}
AUG = {":=", "+=", "-=", "*=", "/=", "%="}


class Parser:
    def __init__(self, toks: list[Tok]):
        self.t = toks
        self.i = 0
        self.user_types: set[str] = set()

    # --- token helpers
    def peek(self, k: int = 0) -> Tok:
        j = self.i + k
        return self.t[j] if j < len(self.t) else self.t[-1]

    def next(self) -> Tok:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def at(self, kind: str, val=None, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok.kind == kind and (val is None or tok.val == val)

    def at_op(self, val, k=0):
        return self.at("OP", val, k)

    def at_name(self, val=None, k=0):
        return self.at("NAME", val, k)

    def expect(self, kind, val=None) -> Tok:
        tok = self.peek()
        if tok.kind != kind or (val is not None and tok.val != val):
            raise PineSyntaxError(f"line {tok.line}: expected {kind} {val!r}, got {tok.kind} {tok.val!r}")
        self.i += 1
        return tok

    def skip_nl(self):
        while self.at("NL"):
            self.i += 1

    # --- program
    def parse_program(self) -> list:
        stmts = []
        self.skip_nl()
        while not self.at("EOF"):
            if self.at("IND") or self.at("DED"):      # stray indentation at top level: tolerate
                self.i += 1
                continue
            stmts.append(self.parse_stmt())
            self.skip_nl()
        return stmts

    def parse_block(self) -> list:
        self.skip_nl()
        if not self.at("IND"):
            raise PineSyntaxError(f"line {self.peek().line}: expected an indented block")
        self.i += 1
        stmts = []
        while not self.at("DED") and not self.at("EOF"):
            if self.at("NL"):
                self.i += 1
                continue
            if self.at("IND"):                          # over-indented line inside a block: flatten
                inner = self.parse_block()
                stmts.extend(inner)
                continue
            stmts.append(self.parse_stmt())
        if self.at("DED"):
            self.i += 1
        return stmts

    def end_stmt(self):
        if self.at("NL"):
            self.i += 1
            return
        if self.at_op(","):          # Pine allows several statements on one line separated by commas
            self.i += 1
            return
        if self.at("EOF") or self.at("DED"):
            return
        tok = self.peek()
        raise PineSyntaxError(f"line {tok.line}: unexpected {tok.kind} {tok.val!r} at end of statement")

    # --- statements
    def parse_stmt(self):
        tok = self.peek()
        line = tok.line
        if tok.kind == "NAME":
            v = tok.val
            if v == "if":
                return self.parse_if()
            if v == "for":
                return self.parse_for()
            if v == "while":
                return self.parse_while()
            if v == "switch":
                return self.parse_switch()
            if v == "break":
                self.i += 1; self.end_stmt(); return Break(line)
            if v == "continue":
                self.i += 1; self.end_stmt(); return Continue(line)
            if v == "import":
                raise Unsupported("import of a library")
            if v == "export":
                self.i += 1
                return self.parse_stmt()
            if v == "type" and self.at("NAME", k=1) and (self.at("NL", k=2)):
                return self.parse_typedef()
            if v == "method" and self.at("NAME", k=1) and self.at_op("(", k=2):
                self.i += 1
                fd = self.parse_funcdef()
                fd.method = True
                return fd
            if v in ("var", "varip"):
                self.i += 1
                return self.parse_decl(mode=v, line=line)
            if self.is_funcdef():
                return self.parse_funcdef()
            if self.is_typed_decl():
                return self.parse_decl(mode=None, line=line)
        if tok.kind == "OP" and tok.val == "[" and self.is_tuple_decl():
            return self.parse_tuple_decl()
        expr = self.parse_expr()
        if self.at_op("="):
            self.i += 1
            val = self.parse_rhs()
            if isinstance(expr, Name):
                return Decl([expr.id], None, None, val, line)
            if isinstance(expr, Attr):
                return Assign(expr, "=", val, line)
            raise PineSyntaxError(f"line {line}: cannot declare {type(expr).__name__}")
        if self.peek().kind == "OP" and self.peek().val in AUG:
            op = self.next().val
            val = self.parse_rhs()
            if not isinstance(expr, (Name, Attr, Index)):
                raise PineSyntaxError(f"line {line}: bad assignment target")
            return Assign(expr, op, val, line)
        self.end_stmt()
        return ExprStmt(expr, line)

    def parse_rhs(self):
        if self.at_name("if"):
            return self.parse_if()
        if self.at_name("switch"):
            return self.parse_switch()
        if self.at_name("for"):
            return self.parse_for()
        if self.at_name("while"):
            return self.parse_while()
        e = self.parse_expr()
        self.end_stmt()
        return e

    def is_funcdef(self) -> bool:
        if not (self.at("NAME") and self.at_op("(", 1)):
            return False
        j, depth = self.i + 1, 0
        while j < len(self.t):
            tok = self.t[j]
            if tok.kind == "OP" and tok.val in ("(", "["):
                depth += 1
            elif tok.kind == "OP" and tok.val in (")", "]"):
                depth -= 1
                if depth == 0:
                    nt = self.t[j + 1] if j + 1 < len(self.t) else None
                    return bool(nt and nt.kind == "OP" and nt.val == "=>")
            elif tok.kind in ("NL", "EOF"):
                return False
            j += 1
        return False

    def try_type(self, j: int) -> Optional[int]:
        """If a type annotation starts at token j, return the index after it, else None."""
        t = self.t
        while j < len(t) and t[j].kind == "NAME" and t[j].val in TYPE_QUALIFIERS:
            j += 1
        if j >= len(t) or t[j].kind != "NAME" or t[j].val in KEYWORDS:
            return None
        j += 1
        while j + 1 < len(t) and t[j].kind == "OP" and t[j].val == "." and t[j + 1].kind == "NAME":   # chart.point, ns.Type
            j += 2
        if j < len(t) and t[j].kind == "OP" and t[j].val == "<":        # generic: array<float>, map<string, float>
            k, depth = j, 0
            while k < len(t):
                if t[k].kind == "OP" and t[k].val == "<":
                    depth += 1
                elif t[k].kind == "OP" and t[k].val == ">":
                    depth -= 1
                    if depth == 0:
                        break
                elif t[k].kind not in ("NAME",) and not (t[k].kind == "OP" and t[k].val in (",", ".")):
                    return None
                k += 1
            j = k + 1
        if j + 1 < len(t) and t[j].kind == "OP" and t[j].val == "[" and t[j + 1].kind == "OP" and t[j + 1].val == "]":
            j += 2
        return j

    def is_typed_decl(self) -> bool:
        j = self.try_type(self.i)
        return j is not None and j + 1 < len(self.t) and self.t[j].kind == "NAME" and self.t[j].val not in KEYWORDS \
            and self.t[j + 1].kind == "OP" and self.t[j + 1].val == "="

    def is_tuple_decl(self) -> bool:
        j, depth = self.i, 0
        while j < len(self.t):
            tok = self.t[j]
            if tok.kind == "OP" and tok.val == "[":
                depth += 1
            elif tok.kind == "OP" and tok.val == "]":
                depth -= 1
                if depth == 0:
                    nt = self.t[j + 1] if j + 1 < len(self.t) else None
                    return bool(nt and nt.kind == "OP" and nt.val in ("=", ":="))
            elif tok.kind in ("NL", "EOF"):
                return False
            j += 1
        return False

    def parse_decl(self, mode, line):
        if self.at_op("["):
            d = self.parse_tuple_decl()
            d.mode = mode
            return d
        typ = None
        j = self.try_type(self.i)
        if j is not None and j < len(self.t) and self.t[j].kind == "NAME" and self.t[j].val not in KEYWORDS \
                and j + 1 < len(self.t) and self.t[j + 1].kind == "OP" and self.t[j + 1].val == "=":
            typ = " ".join(str(x.val) for x in self.t[self.i:j])
            self.i = j
        name = self.expect("NAME").val
        self.expect("OP", "=")
        val = self.parse_rhs()
        return Decl([name], mode, typ, val, line)

    def parse_tuple_decl(self):
        line = self.peek().line
        self.expect("OP", "[")
        names = []
        while True:
            names.append(self.expect("NAME").val)
            if self.at_op(","):
                self.i += 1
                continue
            break
        self.expect("OP", "]")
        op = self.next().val      # '=' or ':='
        val = self.parse_rhs()
        if op == ":=":
            return Assign(TupleLit([Name(n, line) for n in names], line), ":=", val, line)
        return Decl(names, None, None, val, line)

    def parse_if(self):
        line = self.expect("NAME", "if").line
        cond = self.parse_expr()
        body = self.parse_block_or_inline()
        orelse = None
        if self.at_name("else"):
            self.i += 1
            if self.at_name("if"):
                orelse = [self.parse_if()]
            else:
                orelse = self.parse_block_or_inline()
        return If(cond, body, orelse, line)

    def parse_block_or_inline(self) -> list:
        if self.at("NL"):
            self.i += 1
            return self.parse_block()
        # tolerate `if cond stmt` on one line (not valid Pine but seen in ports)
        return [self.parse_stmt()]

    def parse_for(self):
        line = self.expect("NAME", "for").line
        if self.at_op("["):
            self.i += 1
            names = [self.expect("NAME").val]
            while self.at_op(","):
                self.i += 1
                names.append(self.expect("NAME").val)
            self.expect("OP", "]")
            self.expect("NAME", "in")
            it = self.parse_expr()
            return ForIn(names, it, self.parse_block_or_inline(), line)
        name = self.expect("NAME").val
        if self.at_name("in"):
            self.i += 1
            it = self.parse_expr()
            return ForIn([name], it, self.parse_block_or_inline(), line)
        if self.at_op("=") or self.at_op(":="):
            self.i += 1
        else:
            raise PineSyntaxError(f"line {line}: bad for loop")
        start = self.parse_expr()
        self.expect("NAME", "to")
        end = self.parse_expr()
        step = None
        if self.at_name("by"):
            self.i += 1
            step = self.parse_expr()
        return For(name, start, end, step, self.parse_block_or_inline(), line)

    def parse_while(self):
        line = self.expect("NAME", "while").line
        cond = self.parse_expr()
        return While(cond, self.parse_block_or_inline(), line)

    def parse_switch(self):
        line = self.expect("NAME", "switch").line
        subject = None
        if not self.at("NL"):
            subject = self.parse_expr()
        self.expect("NL")
        self.skip_nl()
        self.expect("IND")
        cases = []
        while not self.at("DED") and not self.at("EOF"):
            if self.at("NL"):
                self.i += 1
                continue
            if self.at_op("=>"):
                self.i += 1
                cond = None
            else:
                cond = self.parse_expr()
                self.expect("OP", "=>")
            if self.at("NL"):
                self.i += 1
                body = self.parse_block()
            else:
                body = [self.parse_stmt()]
            cases.append((cond, body))
        if self.at("DED"):
            self.i += 1
        return Switch(subject, cases, line)

    def parse_funcdef(self):
        name_tok = self.expect("NAME")
        self.expect("OP", "(")
        params = []
        while not self.at_op(")"):
            j = self.try_type(self.i)
            typ = None
            if j is not None and j < len(self.t) and self.t[j].kind == "NAME":
                typ = " ".join(str(x.val) for x in self.t[self.i:j])
                self.i = j
            pname = self.expect("NAME").val
            default = None
            if self.at_op("="):
                self.i += 1
                default = self.parse_expr()
            params.append((pname, default, typ))
            if self.at_op(","):
                self.i += 1
        self.expect("OP", ")")
        self.expect("OP", "=>")
        if self.at("NL"):
            self.i += 1
            body = self.parse_block()
        else:
            body = [self.parse_stmt()]
            while self.t[self.i - 1].kind == "OP" and self.t[self.i - 1].val == "," and not self.at("NL") \
                    and not self.at("EOF") and not self.at("DED"):
                body.append(self.parse_stmt())
        return FuncDef(name_tok.val, params, body, False, name_tok.line)

    def parse_typedef(self):
        line = self.expect("NAME", "type").line
        name = self.expect("NAME").val
        self.user_types.add(name)
        self.expect("NL")
        self.skip_nl()
        self.expect("IND")
        fields = []
        while not self.at("DED") and not self.at("EOF"):
            if self.at("NL"):
                self.i += 1
                continue
            j = self.try_type(self.i)
            if j is None:
                raise PineSyntaxError(f"line {self.peek().line}: bad field")
            typ = " ".join(str(x.val) for x in self.t[self.i:j])
            self.i = j
            fname = self.expect("NAME").val
            default = None
            if self.at_op("="):
                self.i += 1
                default = self.parse_expr()
            self.end_stmt()
            fields.append((fname, typ, default))
        if self.at("DED"):
            self.i += 1
        return TypeDef(name, fields, line)

    # --- expressions (precedence: ?: < or < and < == != < < > <= >= < + - < * / % < unary < postfix)
    def parse_expr(self):
        return self.parse_ternary()

    def parse_ternary(self):
        c = self.parse_or()
        if self.at_op("?"):
            line = self.next().line
            a = self.parse_ternary()
            self.expect("OP", ":")
            b = self.parse_ternary()
            return Ternary(c, a, b, line)
        return c

    def parse_or(self):
        a = self.parse_and()
        while self.at_name("or"):
            line = self.next().line
            a = BinOp("or", a, self.parse_and(), line)
        return a

    def parse_and(self):
        a = self.parse_eq()
        while self.at_name("and"):
            line = self.next().line
            a = BinOp("and", a, self.parse_eq(), line)
        return a

    def parse_eq(self):
        a = self.parse_rel()
        while self.peek().kind == "OP" and self.peek().val in ("==", "!="):
            tok = self.next()
            a = BinOp(tok.val, a, self.parse_rel(), tok.line)
        return a

    def parse_rel(self):
        a = self.parse_add()
        while self.peek().kind == "OP" and self.peek().val in ("<", ">", "<=", ">="):
            tok = self.next()
            a = BinOp(tok.val, a, self.parse_add(), tok.line)
        return a

    def parse_add(self):
        a = self.parse_mul()
        while self.peek().kind == "OP" and self.peek().val in ("+", "-"):
            tok = self.next()
            a = BinOp(tok.val, a, self.parse_mul(), tok.line)
        return a

    def parse_mul(self):
        a = self.parse_unary()
        while self.peek().kind == "OP" and self.peek().val in ("*", "/", "%"):
            tok = self.next()
            a = BinOp(tok.val, a, self.parse_unary(), tok.line)
        return a

    def parse_unary(self):
        tok = self.peek()
        if tok.kind == "OP" and tok.val in ("-", "+"):
            self.i += 1
            return UnOp(tok.val, self.parse_unary(), tok.line)
        if tok.kind == "NAME" and tok.val == "not":
            self.i += 1
            return UnOp("not", self.parse_unary(), tok.line)
        return self.parse_postfix()

    def parse_generic_args(self) -> Optional[list]:
        """At '<' after a name like array.new: parse <T, U> if it is a type list followed by '('."""
        t, j = self.t, self.i
        if not (t[j].kind == "OP" and t[j].val == "<"):
            return None
        k, depth, types, cur = j, 0, [], []
        while k < len(t):
            tok = t[k]
            if tok.kind == "OP" and tok.val == "<":
                depth += 1
                if depth > 1:
                    cur.append("<")
            elif tok.kind == "OP" and tok.val == ">":
                depth -= 1
                if depth == 0:
                    break
                cur.append(">")
            elif tok.kind == "OP" and tok.val == "," and depth == 1:
                types.append("".join(cur)); cur = []
            elif tok.kind == "NAME" or (tok.kind == "OP" and tok.val == "."):
                cur.append(str(tok.val))
            else:
                return None
            k += 1
        if k + 1 < len(t) and t[k + 1].kind == "OP" and t[k + 1].val == "(":
            types.append("".join(cur))
            self.i = k + 1
            return types
        return None

    def parse_postfix(self):
        e = self.parse_primary()
        while True:
            tok = self.peek()
            if tok.kind == "OP" and tok.val == "(":
                self.i += 1
                args, kwargs = self.parse_args()
                e = Call(e, args, kwargs, None, tok.line)
            elif tok.kind == "OP" and tok.val == "[":
                self.i += 1
                idx = self.parse_expr()
                self.expect("OP", "]")
                e = Index(e, idx, tok.line)
            elif tok.kind == "OP" and tok.val == "." and self.at("NAME", k=1):
                self.i += 1
                e = Attr(e, self.next().val, tok.line)
            elif tok.kind == "OP" and tok.val == "<" and isinstance(e, (Attr, Name)):
                targs = self.parse_generic_args()
                if targs is None:
                    break
                self.expect("OP", "(")
                args, kwargs = self.parse_args()
                e = Call(e, args, kwargs, targs, tok.line)
            else:
                break
        return e

    def parse_args(self):
        args, kwargs = [], []
        while not self.at_op(")"):
            if self.at("NAME") and self.at_op("=", 1):
                name = self.next().val
                self.i += 1
                kwargs.append((name, self.parse_expr()))
            else:
                if kwargs:
                    # positional after keyword: tolerate by appending
                    pass
                args.append(self.parse_expr())
            if self.at_op(","):
                self.i += 1
            elif not self.at_op(")"):
                tok = self.peek()
                raise PineSyntaxError(f"line {tok.line}: expected , or ) in call, got {tok.val!r}")
        self.expect("OP", ")")
        return args, kwargs

    def parse_primary(self):
        tok = self.next()
        k, v, line = tok.kind, tok.val, tok.line
        if k == "NUM":
            return Num(v, line)
        if k == "STR":
            # adjacent string literals are not Pine, but tolerate implicit concatenation
            return Str(v, line)
        if k == "COLOR":
            return Color(v, line)
        if k == "NAME":
            if v == "true":
                return Bool(True, line)
            if v == "false":
                return Bool(False, line)
            if v == "na":
                if self.at_op("("):
                    return Name("na", line)
                return Na(line)
            if v in ("if", "switch", "for", "while"):
                self.i -= 1
                raise PineSyntaxError(f"line {line}: block expression inside an expression")
            return Name(v, line)
        if k == "OP" and v == "(":
            e = self.parse_expr()
            self.expect("OP", ")")
            return e
        if k == "OP" and v == "[":
            items = []
            while not self.at_op("]"):
                items.append(self.parse_expr())
                if self.at_op(","):
                    self.i += 1
            self.expect("OP", "]")
            return TupleLit(items, line)
        raise PineSyntaxError(f"line {line}: unexpected {k} {v!r}")


def version_of(src: str) -> int:
    m = re.search(r"//\s*@version\s*=\s*(\d+)", src)
    return int(m.group(1)) if m else 2


def parse(src: str) -> list:
    return Parser(tokenize(src)).parse_program()
