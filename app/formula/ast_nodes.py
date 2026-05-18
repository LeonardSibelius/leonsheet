"""AST node types produced by the parser, consumed by the evaluator.

Frozen dataclasses everywhere — these are immutable values, never mutated
after parse. The evaluator walks them with isinstance dispatch (small node
set; a visitor pattern would be overkill at v1.0).

Cell references and ranges store (row, col) 0-indexed ints — the canonical
internal form. Letter form ("A1") lives only at the user-facing edges:
parser input, UI output, CSV output. See app/formula/refs.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class Number:
    value: float


@dataclass(frozen=True)
class String:
    value: str


@dataclass(frozen=True)
class CellRef:
    row: int
    col: int


@dataclass(frozen=True)
class Range:
    """A1:A5 — only legal inside function arguments. The evaluator expands this
    into a flat list of cell values before passing to the function.
    """
    start_row: int
    start_col: int
    end_row: int
    end_col: int


@dataclass(frozen=True)
class UnaryOp:
    op: str  # "+" or "-"
    operand: "ASTNode"


@dataclass(frozen=True)
class BinaryOp:
    op: str  # one of "+ - * / = <> < > <= >="
    left: "ASTNode"
    right: "ASTNode"


@dataclass(frozen=True)
class FunctionCall:
    name: str  # uppercase, normalized at tokenize time
    args: tuple["ASTNode", ...]


ASTNode = Union[
    Number, String, CellRef, Range, UnaryOp, BinaryOp, FunctionCall
]
