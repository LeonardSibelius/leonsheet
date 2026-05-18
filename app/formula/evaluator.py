"""AST evaluator + 7 v1.0 functions.

Architecture: an Evaluator instance is constructed with a `lookup` callback
that maps (row, col) -> Value. The dep graph layer is responsible for ensuring
lookups return up-to-date values BEFORE evaluator runs — the evaluator itself
never re-triggers computation. Cycle detection is also the dep graph's job.

Functions are dispatched through the FUNCTIONS table. They receive the raw
AST args (not pre-evaluated values) plus the Evaluator instance. Most
functions wrap their args with `_flat_eval_args` for eager evaluation +
range expansion; IF is the lone short-circuit, evaluating only the chosen
branch (predicted bug #9). This "fexpr-style" signature also gives later
versions a clean slot for IFERROR/IFS/SWITCH without changing the interface.

Predicted bug #3 (range expansion): handled exactly once, in
`_flat_eval_args`. No function ever sees a raw `Range` node.

Predicted bug #14 (JSON encoding of inf/NaN): `_finite_or_error` clamps
any arithmetic result to ErrorValue("#NUM!") if non-finite. Division by
zero is checked earlier and returns ErrorValue("#DIV/0!"). The evaluator
will never hand the UI layer a float that JSON can't encode.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from .ast_nodes import (
    ASTNode,
    BinaryOp,
    CellRef,
    FunctionCall,
    Number,
    Range,
    String,
    UnaryOp,
)
from .values import (
    EMPTY,
    ERR_DIV_ZERO,
    ERR_NAME,
    ERR_NUM,
    ERR_VALUE,
    ErrorValue,
    Value,
    _EmptyType,
)

LookupFn = Callable[[int, int], Value]


# --- coercion helpers ---


def _to_number(v: Value) -> Value:
    """Coerce v to a finite float; return ErrorValue on failure.

    EMPTY -> 0.0, bool -> 1/0, numeric string -> float, anything else -> #VALUE!.
    Non-finite results (inf, nan) become #NUM!.
    """
    if isinstance(v, ErrorValue):
        return v
    if isinstance(v, _EmptyType):
        return 0.0
    if isinstance(v, bool):  # must precede the int/float branch — bool IS int in Python
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return _finite_or_error(float(v))
    if isinstance(v, str):
        if v == "":
            return 0.0
        try:
            n = float(v)
        except ValueError:
            return ERR_VALUE
        return _finite_or_error(n)
    return ERR_VALUE


def _try_number(v: Value) -> Optional[float]:
    """Lenient coercion for comparison and aggregate functions.

    Returns the float on success or None on "couldn't make a number out of this."
    Distinct from `_to_number` which returns an ErrorValue on failure (because
    arithmetic operators want to propagate the error, while SUM wants to skip).
    """
    if isinstance(v, ErrorValue):
        return None
    if isinstance(v, _EmptyType):
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        n = float(v)
        return n if math.isfinite(n) else None
    if isinstance(v, str):
        try:
            n = float(v)
        except ValueError:
            return None
        return n if math.isfinite(n) else None
    return None


def _to_string(v: Value) -> str:
    if isinstance(v, ErrorValue):
        return v.code
    if isinstance(v, _EmptyType):
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        # Display nicety: integer-valued floats render without trailing ".0".
        if math.isfinite(v) and v == int(v):
            return str(int(v))
        return str(v)
    if isinstance(v, str):
        return v
    return ""


def _to_bool(v: Value) -> bool:
    """Truthiness for IF's condition arg. Errors are handled by IF before calling this."""
    if isinstance(v, _EmptyType):
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return bool(v)
    return False


def _finite_or_error(n: float) -> Value:
    """Wrap arithmetic results — predicted bug #14, no inf/nan ever leaves the evaluator."""
    if math.isfinite(n):
        return n
    return ERR_NUM


_CMP_FNS = {
    "=":  lambda a, b: a == b,
    "<>": lambda a, b: a != b,
    "<":  lambda a, b: a < b,
    ">":  lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
}


# --- the evaluator ---


class Evaluator:
    def __init__(self, lookup: LookupFn):
        self.lookup = lookup

    def evaluate(self, node: ASTNode) -> Value:
        if isinstance(node, Number):
            return _finite_or_error(node.value)
        if isinstance(node, String):
            return node.value
        if isinstance(node, CellRef):
            return self.lookup(node.row, node.col)
        if isinstance(node, UnaryOp):
            return self._eval_unary(node)
        if isinstance(node, BinaryOp):
            return self._eval_binary(node)
        if isinstance(node, FunctionCall):
            return self._eval_function(node)
        if isinstance(node, Range):
            # Range is only legal inside a function arg list, and `_flat_eval_args`
            # consumes Range nodes directly without going through `evaluate`.
            # Reaching here means a malformed AST.
            return ERR_VALUE
        raise TypeError(f"unknown AST node type: {type(node).__name__}")

    def _eval_unary(self, node: UnaryOp) -> Value:
        operand = self.evaluate(node.operand)
        if isinstance(operand, ErrorValue):
            return operand
        n = _to_number(operand)
        if isinstance(n, ErrorValue):
            return n
        if node.op == "-":
            return _finite_or_error(-n)
        if node.op == "+":
            return n
        return ERR_VALUE

    def _eval_binary(self, node: BinaryOp) -> Value:
        left = self.evaluate(node.left)
        if isinstance(left, ErrorValue):
            return left
        right = self.evaluate(node.right)
        if isinstance(right, ErrorValue):
            return right

        if node.op in ("+", "-", "*", "/"):
            return self._arith(node.op, left, right)
        if node.op in _CMP_FNS:
            return self._compare(node.op, left, right)
        return ERR_VALUE

    def _arith(self, op: str, a: Value, b: Value) -> Value:
        na = _to_number(a)
        if isinstance(na, ErrorValue):
            return na
        nb = _to_number(b)
        if isinstance(nb, ErrorValue):
            return nb
        if op == "+":
            return _finite_or_error(na + nb)
        if op == "-":
            return _finite_or_error(na - nb)
        if op == "*":
            return _finite_or_error(na * nb)
        # division
        if nb == 0:
            return ERR_DIV_ZERO
        return _finite_or_error(na / nb)

    def _compare(self, op: str, a: Value, b: Value) -> Value:
        # Try numeric comparison first; fall back to string.
        # (Excel sorts numbers below strings under cross-type compare; we simplify to
        # numeric-if-possible, otherwise string, since v1.0 doesn't expose mixed sorts.)
        na = _try_number(a)
        nb = _try_number(b)
        if na is not None and nb is not None:
            return _CMP_FNS[op](na, nb)
        sa = _to_string(a)
        sb = _to_string(b)
        if op in ("=", "<>"):
            # Excel: equality is case-insensitive.
            return _CMP_FNS[op](sa.upper(), sb.upper())
        return _CMP_FNS[op](sa, sb)

    def _eval_function(self, node: FunctionCall) -> Value:
        fn = FUNCTIONS.get(node.name)
        if fn is None:
            return ERR_NAME
        return fn(node.args, self)


# --- function arg evaluation helper ---


def _flat_eval_args(raw_args: tuple[ASTNode, ...], ev: Evaluator) -> list[Value]:
    """Evaluate each arg; Range args expand to a flat list of cell values.

    Predicted bug #3: every range-accepting function used to be where a
    SUM-vs-Range bug snuck in. Now there's one place that knows about Range,
    here. If a new function in v2.1 wants ranges, it just calls this helper.
    """
    out: list[Value] = []
    for arg in raw_args:
        if isinstance(arg, Range):
            for r in range(arg.start_row, arg.end_row + 1):
                for c in range(arg.start_col, arg.end_col + 1):
                    out.append(ev.lookup(r, c))
        else:
            out.append(ev.evaluate(arg))
    return out


# --- v1.0 functions ---


def fn_sum(raw_args: tuple[ASTNode, ...], ev: Evaluator) -> Value:
    values = _flat_eval_args(raw_args, ev)
    total = 0.0
    for v in values:
        if isinstance(v, ErrorValue):
            return v
        n = _try_number(v)
        if n is None:
            continue
        total += n
    return _finite_or_error(total)


def fn_average(raw_args: tuple[ASTNode, ...], ev: Evaluator) -> Value:
    values = _flat_eval_args(raw_args, ev)
    total = 0.0
    count = 0
    for v in values:
        if isinstance(v, ErrorValue):
            return v
        if isinstance(v, _EmptyType):
            continue  # empty cells do not contribute to the average's denominator
        n = _try_number(v)
        if n is None:
            continue
        total += n
        count += 1
    if count == 0:
        return ERR_DIV_ZERO
    return _finite_or_error(total / count)


def fn_min(raw_args: tuple[ASTNode, ...], ev: Evaluator) -> Value:
    values = _flat_eval_args(raw_args, ev)
    numbers: list[float] = []
    for v in values:
        if isinstance(v, ErrorValue):
            return v
        if isinstance(v, _EmptyType):
            continue
        n = _try_number(v)
        if n is not None:
            numbers.append(n)
    return min(numbers) if numbers else 0.0


def fn_max(raw_args: tuple[ASTNode, ...], ev: Evaluator) -> Value:
    values = _flat_eval_args(raw_args, ev)
    numbers: list[float] = []
    for v in values:
        if isinstance(v, ErrorValue):
            return v
        if isinstance(v, _EmptyType):
            continue
        n = _try_number(v)
        if n is not None:
            numbers.append(n)
    return max(numbers) if numbers else 0.0


def fn_count(raw_args: tuple[ASTNode, ...], ev: Evaluator) -> Value:
    """Count of NUMERIC cells (Excel COUNT, not COUNTA). Errors are skipped, not propagated."""
    values = _flat_eval_args(raw_args, ev)
    count = 0
    for v in values:
        if isinstance(v, (ErrorValue, _EmptyType)):
            continue
        if _try_number(v) is not None:
            count += 1
    return float(count)


def fn_if(raw_args: tuple[ASTNode, ...], ev: Evaluator) -> Value:
    """Three-arg conditional. Only the chosen branch is evaluated.

    Predicted bug #9: =IF(A1<>0, B1/A1, 0) with A1=0 must NOT divide by zero.
    Achieved by evaluating the chosen branch lazily, after the condition.
    """
    if len(raw_args) != 3:
        return ERR_VALUE
    cond = ev.evaluate(raw_args[0])
    if isinstance(cond, ErrorValue):
        return cond
    return ev.evaluate(raw_args[1] if _to_bool(cond) else raw_args[2])


def fn_concat(raw_args: tuple[ASTNode, ...], ev: Evaluator) -> Value:
    values = _flat_eval_args(raw_args, ev)
    parts: list[str] = []
    for v in values:
        if isinstance(v, ErrorValue):
            return v
        parts.append(_to_string(v))
    return "".join(parts)


FUNCTIONS: dict[str, Callable[[tuple[ASTNode, ...], Evaluator], Value]] = {
    "SUM": fn_sum,
    "AVERAGE": fn_average,
    "MIN": fn_min,
    "MAX": fn_max,
    "COUNT": fn_count,
    "IF": fn_if,
    "CONCAT": fn_concat,
}


def evaluate(node: ASTNode, lookup: LookupFn) -> Value:
    """Top-level convenience: walk `node` against the given lookup."""
    return Evaluator(lookup).evaluate(node)
