"""Runtime values produced by the evaluator and stored in cells.

Five concrete value kinds:
  * float       — numeric result of arithmetic, COUNT, comparisons treated as numbers
  * str         — string literals, CONCAT output, lookups of text-valued cells
  * bool        — comparison results; coerced to 1/0 on arithmetic, "TRUE"/"FALSE" on display
  * EMPTY       — the cell at (r, c) has no content. Distinct from "" and 0.
                  Predicted bug #4 (empty-cell semantics): each function decides
                  how empty contributes (SUM skips, AVERAGE skips its count, etc.)
  * ErrorValue  — spreadsheet errors: #DIV/0!, #VALUE!, #NAME?, #REF!, #CIRCULAR!
                  Errors propagate through every operation that touches them.

Predicted bug #14 (JSON encoding of inf/NaN): the evaluator never emits
Python `inf` or `nan`. Any arithmetic that would produce them returns an
ErrorValue instead — see `_finite_or_error` in evaluator.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


class _EmptyType:
    """Singleton for absent cell content. Falsy, equal only to itself."""

    _instance: "_EmptyType | None" = None

    def __new__(cls) -> "_EmptyType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "EMPTY"

    def __bool__(self) -> bool:
        return False


EMPTY: _EmptyType = _EmptyType()


@dataclass(frozen=True)
class ErrorValue:
    """Spreadsheet error sentinel. The code is the display string (e.g., "#DIV/0!")."""

    code: str

    def __str__(self) -> str:
        return self.code


Value = Union[float, str, bool, _EmptyType, ErrorValue]


# Error codes used in v1.0:
ERR_DIV_ZERO = ErrorValue("#DIV/0!")
ERR_VALUE = ErrorValue("#VALUE!")
ERR_NAME = ErrorValue("#NAME?")
ERR_REF = ErrorValue("#REF!")
ERR_NUM = ErrorValue("#NUM!")
ERR_CIRCULAR = ErrorValue("#CIRCULAR!")
