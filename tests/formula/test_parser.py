"""Smoke tests for the recursive-descent parser.

Coverage:
  * literals: numbers, strings, cell refs
  * precedence: * binds tighter than +, parens override
  * unary +/- (predicted bug #10)
  * comparison operators (predicted bug #11), only one comparison per expression
  * function calls: 0, 1, N args; nested; mixed expression args
  * ranges only inside function arguments
  * formula entry point: strips leading '='; rejects raw expressions
  * parse errors: unbalanced parens, trailing junk, trailing comma
"""

import pytest

from app.formula.ast_nodes import (
    BinaryOp,
    CellRef,
    FunctionCall,
    Number,
    Range,
    String,
    UnaryOp,
)
from app.formula.parser import ParseError, parse, parse_formula


# --- literals ---


def test_number_integer():
    assert parse("42") == Number(42.0)


def test_number_decimal():
    assert parse("3.14") == Number(3.14)


def test_string():
    assert parse('"hi"') == String("hi")


def test_cell_ref():
    assert parse("A1") == CellRef(0, 0)


def test_cell_ref_aa10():
    # Predicted bug #2 (tokenizer) + #15 (refs) — verified here at the AST level.
    assert parse("AA10") == CellRef(9, 26)


# --- arithmetic precedence ---


def test_addition_left_associative():
    assert parse("1+2+3") == BinaryOp(
        "+", BinaryOp("+", Number(1.0), Number(2.0)), Number(3.0)
    )


def test_mul_binds_tighter_than_add():
    assert parse("1+2*3") == BinaryOp(
        "+", Number(1.0), BinaryOp("*", Number(2.0), Number(3.0))
    )


def test_parens_override_precedence():
    assert parse("(1+2)*3") == BinaryOp(
        "*", BinaryOp("+", Number(1.0), Number(2.0)), Number(3.0)
    )


# --- unary +/- (predicted bug #10) ---


def test_unary_minus_on_cell_ref():
    assert parse("-A1") == UnaryOp("-", CellRef(0, 0))


def test_unary_minus_in_expression():
    assert parse("1+-2") == BinaryOp("+", Number(1.0), UnaryOp("-", Number(2.0)))


def test_double_negation():
    assert parse("--A1") == UnaryOp("-", UnaryOp("-", CellRef(0, 0)))


def test_unary_plus():
    assert parse("+5") == UnaryOp("+", Number(5.0))


# --- comparisons (predicted bug #11) ---


@pytest.mark.parametrize("op", ["=", "<>", "<", ">", "<=", ">="])
def test_comparison_operator(op: str):
    assert parse(f"A1{op}5") == BinaryOp(op, CellRef(0, 0), Number(5.0))


def test_comparison_below_arithmetic():
    # A1+1 < B1+1 parses as (A1+1) < (B1+1)
    assert parse("A1+1<B1+1") == BinaryOp(
        "<",
        BinaryOp("+", CellRef(0, 0), Number(1.0)),
        BinaryOp("+", CellRef(0, 1), Number(1.0)),
    )


# --- function calls ---


def test_zero_arg_function():
    assert parse("NOW()") == FunctionCall("NOW", ())


def test_one_arg_function():
    assert parse("SUM(A1)") == FunctionCall("SUM", (CellRef(0, 0),))


def test_range_arg():
    assert parse("SUM(A1:A5)") == FunctionCall(
        "SUM", (Range(0, 0, 4, 0),)
    )


def test_multi_arg_function_strings_and_numbers():
    assert parse('CONCAT("a","b","c")') == FunctionCall(
        "CONCAT", (String("a"), String("b"), String("c"))
    )


def test_if_with_comparison_then_else():
    assert parse('IF(A1>5,"big","small")') == FunctionCall(
        "IF",
        (
            BinaryOp(">", CellRef(0, 0), Number(5.0)),
            String("big"),
            String("small"),
        ),
    )


def test_nested_function_call():
    assert parse("SUM(A1,MAX(B1:B5))") == FunctionCall(
        "SUM",
        (
            CellRef(0, 0),
            FunctionCall("MAX", (Range(0, 1, 4, 1),)),
        ),
    )


# --- range outside function args is rejected ---


def test_top_level_range_rejected():
    # A1:A5 at the expression level: parser consumes A1, then sees COLON which
    # isn't a valid arith operator. EOF check fails — ParseError.
    with pytest.raises(ParseError):
        parse("A1:A5")


# --- formula entry point ---


def test_parse_formula_strips_leading_equals():
    assert parse_formula("=1+2") == BinaryOp("+", Number(1.0), Number(2.0))


def test_parse_formula_strips_whitespace_around_equals():
    assert parse_formula("  =1+2  ") == BinaryOp("+", Number(1.0), Number(2.0))


def test_parse_formula_rejects_no_equals():
    with pytest.raises(ParseError, match="must start with '='"):
        parse_formula("1+2")


# --- error cases ---


def test_unbalanced_open_paren_errors():
    with pytest.raises(ParseError):
        parse("(1+2")


def test_unbalanced_close_paren_errors():
    with pytest.raises(ParseError):
        parse("1+2)")


def test_trailing_operator_errors():
    with pytest.raises(ParseError):
        parse("1+")


def test_trailing_comma_in_function_args_errors():
    with pytest.raises(ParseError):
        parse("SUM(1,)")


def test_bare_ident_without_parens_errors():
    # "A" is IDENT (no trailing digit). Not followed by LPAREN -> error.
    with pytest.raises(ParseError):
        parse("A")
