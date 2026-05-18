"""Smoke tests for the evaluator + 7 v1.0 functions.

Coverage:
  * literals, cell-ref lookup, empty cell
  * arithmetic incl. div-by-zero (predicted bug #14), unary +/-
  * comparison incl. mixed numeric/string fallback
  * range expansion in function args (predicted bug #3)
  * empty-cell semantics across SUM/AVERAGE/COUNT (predicted bug #4)
  * IF short-circuit on the unchosen branch (predicted bug #9 — the IF(A1<>0, B1/A1, 0) case)
  * error propagation through arithmetic and aggregates
  * unknown function → #NAME?; non-finite arithmetic → #NUM!
"""

from __future__ import annotations

import math

import pytest

from app.formula.evaluator import evaluate
from app.formula.parser import parse, parse_formula
from app.formula.values import (
    EMPTY,
    ERR_DIV_ZERO,
    ERR_NAME,
    ERR_NUM,
    ERR_VALUE,
    ErrorValue,
    Value,
)


def make_lookup(cells: dict[tuple[int, int], Value]):
    """Build a (row,col) -> Value lookup. Missing keys are EMPTY."""
    return lambda r, c: cells.get((r, c), EMPTY)


def eval_str(source: str, cells: dict | None = None) -> Value:
    """Parse + evaluate. Source is an expression (no leading =)."""
    return evaluate(parse(source), make_lookup(cells or {}))


def eval_formula(source: str, cells: dict | None = None) -> Value:
    """Parse + evaluate a full formula (with leading =)."""
    return evaluate(parse_formula(source), make_lookup(cells or {}))


# --- literals and cell refs ---


def test_number_literal():
    assert eval_str("42") == 42.0


def test_string_literal():
    assert eval_str('"hello"') == "hello"


def test_cell_ref_returns_lookup():
    assert eval_str("A1", {(0, 0): 10.0}) == 10.0


def test_cell_ref_empty_returns_empty():
    assert eval_str("A1") is EMPTY


# --- arithmetic ---


def test_addition():
    assert eval_str("1+2") == 3.0


def test_subtraction():
    assert eval_str("10-3") == 7.0


def test_multiplication():
    assert eval_str("4*5") == 20.0


def test_division():
    assert eval_str("10/4") == 2.5


def test_precedence_mul_before_add():
    assert eval_str("1+2*3") == 7.0


def test_parens_override_precedence():
    assert eval_str("(1+2)*3") == 9.0


def test_division_by_zero_returns_error():
    assert eval_str("5/0") == ERR_DIV_ZERO


def test_arithmetic_on_string_returns_value_error():
    # "abc" can't coerce to a number, so 1 + "abc" is #VALUE!
    assert eval_str('1+"abc"') == ERR_VALUE


def test_arithmetic_on_numeric_string_works():
    # "5" can coerce to 5.0; this matches Excel's lenient coercion.
    assert eval_str('1+"5"') == 6.0


def test_arithmetic_with_empty_cell_treats_empty_as_zero():
    # A1 is empty, so A1+5 -> 0+5 -> 5
    assert eval_str("A1+5") == 5.0


# --- unary +/- ---


def test_unary_minus_on_number():
    assert eval_str("-5") == -5.0


def test_unary_minus_on_cell_ref():
    assert eval_str("-A1", {(0, 0): 7.0}) == -7.0


def test_double_unary_minus():
    assert eval_str("--5") == 5.0


def test_unary_plus_is_identity():
    assert eval_str("+5") == 5.0


# --- arithmetic overflow / non-finite (predicted bug #14) ---


def test_arithmetic_overflow_returns_num_error():
    # 1e308 * 1e308 overflows float64 to inf; we must NOT return inf.
    # The literal grammar excludes e-notation (v1.0 §2), so we synthesize the
    # overflow via cell refs that hold a python float.
    assert eval_str("A1*A1", {(0, 0): 1e308}) == ERR_NUM


# --- comparisons ---


def test_numeric_comparison_true():
    assert eval_str("5>3") is True


def test_numeric_comparison_false():
    assert eval_str("3>5") is False


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("5=5", True),
        ("5<>3", True),
        ("5<=5", True),
        ("5>=6", False),
    ],
)
def test_comparison_operators(expr: str, expected: bool):
    assert eval_str(expr) is expected


def test_string_equality_case_insensitive():
    assert eval_str('"Hello"="hello"') is True


def test_mixed_compare_falls_back_to_string():
    # Number 5 vs string "abc": _try_number on "abc" returns None, so fallback
    # to string comparison. "5" < "abc" lexicographically.
    assert eval_str('5<"abc"') is True


# --- IF (predicted bug #9: short-circuit) ---


def test_if_basic_true_branch():
    assert eval_formula('=IF(A1>5,"big","small")', {(0, 0): 10.0}) == "big"


def test_if_basic_false_branch():
    assert eval_formula('=IF(A1>5,"big","small")', {(0, 0): 2.0}) == "small"


def test_if_short_circuits_unchosen_branch():
    """The big one — predicted bug #9.

    IF(A1<>0, B1/A1, 0) with A1=0 must return 0, NOT crash with #DIV/0!.
    Eager evaluation would compute B1/A1 = 5/0 = #DIV/0! BEFORE the condition picks.
    Lazy evaluation only touches the chosen branch.
    """
    result = eval_formula(
        "=IF(A1<>0, B1/A1, 0)",
        {(0, 0): 0.0, (0, 1): 5.0},
    )
    assert result == 0.0


def test_if_propagates_error_in_condition():
    # If the condition itself errors, IF returns that error.
    assert eval_formula("=IF(1/0, 1, 2)") == ERR_DIV_ZERO


def test_if_with_wrong_arity_errors():
    assert eval_formula("=IF(A1>0, 1)") == ERR_VALUE


def test_nested_if():
    cells = {(0, 0): 7.0}
    assert eval_formula(
        '=IF(A1>10, "big", IF(A1>5, "medium", "small"))', cells
    ) == "medium"


# --- SUM ---


def test_sum_simple_range():
    cells = {(0, 0): 10.0, (1, 0): 3.0, (2, 0): 13.0, (4, 0): 2.0}  # A4 (idx 3) empty
    assert eval_formula("=SUM(A1:A5)", cells) == 28.0


def test_sum_skips_empty_and_strings():
    cells = {(0, 0): 10.0, (1, 0): "hello", (2, 0): 5.0}
    assert eval_formula("=SUM(A1:A3)", cells) == 15.0


def test_sum_propagates_error():
    cells = {(0, 0): 1.0, (1, 0): ErrorValue("#DIV/0!")}
    assert eval_formula("=SUM(A1:A2)", cells) == ERR_DIV_ZERO


def test_sum_mixed_range_and_scalar():
    cells = {(0, 0): 1.0, (1, 0): 2.0, (2, 0): 3.0}
    assert eval_formula("=SUM(A1:A3, 100)", cells) == 106.0


# --- AVERAGE ---


def test_average_basic():
    cells = {(0, 0): 10.0, (1, 0): 20.0, (2, 0): 30.0}
    assert eval_formula("=AVERAGE(A1:A3)", cells) == 20.0


def test_average_skips_empty_in_denominator():
    cells = {(0, 0): 10.0, (2, 0): 20.0}  # A2 empty
    # Should be (10+20)/2 = 15, not (10+0+20)/3 = 10
    assert eval_formula("=AVERAGE(A1:A3)", cells) == 15.0


def test_average_of_empty_range_errors():
    assert eval_formula("=AVERAGE(A1:A3)") == ERR_DIV_ZERO


# --- MIN / MAX ---


def test_min_of_range():
    cells = {(0, 0): 5.0, (1, 0): -3.0, (2, 0): 10.0}
    assert eval_formula("=MIN(A1:A3)", cells) == -3.0


def test_max_of_range():
    cells = {(0, 0): 5.0, (1, 0): -3.0, (2, 0): 10.0}
    assert eval_formula("=MAX(A1:A3)", cells) == 10.0


def test_min_of_empty_range_returns_zero():
    # Excel returns 0 for MIN over an entirely empty/non-numeric range.
    assert eval_formula("=MIN(A1:A3)") == 0.0


# --- COUNT ---


def test_count_only_numerics():
    cells = {(0, 0): 1.0, (1, 0): "abc", (2, 0): 2.5, (3, 0): EMPTY}
    # A1 and A3 are numeric; A2 string; A4 empty (also not in dict).
    assert eval_formula("=COUNT(A1:A5)", cells) == 2.0


def test_count_skips_errors_silently():
    cells = {(0, 0): 1.0, (1, 0): ErrorValue("#REF!")}
    assert eval_formula("=COUNT(A1:A2)", cells) == 1.0


# --- CONCAT ---


def test_concat_strings():
    assert eval_formula('=CONCAT("Hello", " ", "World")') == "Hello World"


def test_concat_with_numbers():
    # Numbers render as integers when they're whole, else with their float repr.
    assert eval_formula("=CONCAT(1, 2, 3)") == "123"


def test_concat_with_empty_cell_renders_empty_string():
    assert eval_formula('=CONCAT("a", A1, "b")') == "ab"


def test_concat_propagates_error():
    cells = {(0, 0): ErrorValue("#REF!")}
    assert eval_formula('=CONCAT("x", A1)', cells) == ErrorValue("#REF!")


def test_concat_over_range():
    cells = {(0, 0): "a", (0, 1): "b", (0, 2): "c"}
    assert eval_formula("=CONCAT(A1:C1)", cells) == "abc"


# --- range expansion (predicted bug #3) ---


def test_range_in_sum_expands_to_cell_values():
    """If Range were passed through unflattened, fn_sum would fail.

    This guards predicted bug #3 directly.
    """
    cells = {(r, 0): float(r + 1) for r in range(5)}
    # SUM(A1:A5) = 1+2+3+4+5 = 15
    assert eval_formula("=SUM(A1:A5)", cells) == 15.0


def test_range_in_sum_two_dimensional():
    # A1:B2 expands to 4 cells row-major: (0,0), (0,1), (1,0), (1,1)
    cells = {(0, 0): 1.0, (0, 1): 2.0, (1, 0): 3.0, (1, 1): 4.0}
    assert eval_formula("=SUM(A1:B2)", cells) == 10.0


# --- unknown function ---


def test_unknown_function_returns_name_error():
    assert eval_formula("=FOOBAR(1,2)") == ERR_NAME


# --- error propagation through arithmetic ---


def test_error_in_left_operand_propagates():
    cells = {(0, 0): ErrorValue("#REF!")}
    assert eval_formula("=A1+5", cells) == ErrorValue("#REF!")


def test_error_in_right_operand_propagates():
    cells = {(0, 0): ErrorValue("#REF!")}
    assert eval_formula("=5+A1", cells) == ErrorValue("#REF!")


def test_error_left_wins_over_right_error():
    # When both operands are errors, the left one wins (we evaluate left first).
    cells = {(0, 0): ErrorValue("#REF!"), (1, 0): ErrorValue("#DIV/0!")}
    assert eval_formula("=A1+A2", cells) == ErrorValue("#REF!")


# --- evaluator never emits inf/nan (predicted bug #14, sanity) ---


def test_evaluator_results_are_json_safe():
    """Sweep across function results — none should be Python inf or nan."""
    for source, cells in [
        ("=5/0", {}),
        ("=A1*A1", {(0, 0): 1e308}),
        ("=SUM(A1:A3)", {(0, 0): 1.0, (1, 0): 2.0, (2, 0): 3.0}),
        ('=IF(1>0, "yes", "no")', {}),
    ]:
        result = eval_formula(source, cells)
        if isinstance(result, float):
            assert math.isfinite(result), f"non-finite result {result!r} from {source!r}"
