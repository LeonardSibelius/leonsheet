"""Smoke tests for the formula tokenizer.

Coverage targets the corners called out in the bug-prediction preflight:
  * greedy cell refs (bug #2): "AA10" is one token, not "A" + "A10".
  * multi-char comparisons (bug #11): "<=", ">=", "<>" win over "<", ">", "=".
  * unary minus / leading sign (bug #10): "-A1" tokenizes as MINUS then CELL_REF;
    the parser handles unary at the factor level.
"""

import pytest

from app.formula.tokenizer import Token, TokenKind as TK, TokenizeError, tokenize


def kinds(source: str) -> list[TK]:
    """Helper: return the sequence of token kinds (without the trailing EOF)."""
    return [tok.kind for tok in tokenize(source)[:-1]]


def values(source: str) -> list[str]:
    """Helper: return the sequence of token values (without the trailing EOF)."""
    return [tok.value for tok in tokenize(source)[:-1]]


# --- empty + whitespace ---


def test_empty_source_emits_only_eof():
    toks = tokenize("")
    assert len(toks) == 1
    assert toks[0].kind is TK.EOF


def test_whitespace_is_skipped():
    assert kinds("  \t\n 5 ") == [TK.NUMBER]


# --- numbers ---


def test_integer():
    toks = tokenize("42")
    assert toks[0] == Token(TK.NUMBER, "42", 0)


def test_decimal():
    toks = tokenize("3.14")
    assert toks[0] == Token(TK.NUMBER, "3.14", 0)


def test_leading_dot_decimal():
    toks = tokenize(".5")
    assert toks[0] == Token(TK.NUMBER, ".5", 0)


def test_trailing_dot_after_digits_consumed():
    # "5." is one token (NUMBER "5."), reflecting our greedy dot consumption.
    # Excel rejects this — we'll let the evaluator's float() reject it instead.
    toks = tokenize("5.")
    assert toks[0].kind is TK.NUMBER
    assert toks[0].value == "5."


# --- strings ---


def test_simple_string():
    toks = tokenize('"hello"')
    assert toks[0] == Token(TK.STRING, "hello", 0)


def test_string_with_comma_and_spaces():
    # The comma inside the quotes must NOT be tokenized as COMMA.
    toks = tokenize('"hello, world"')
    assert toks[0] == Token(TK.STRING, "hello, world", 0)
    assert toks[1].kind is TK.EOF


def test_unterminated_string_errors():
    with pytest.raises(TokenizeError, match="unterminated string"):
        tokenize('"oops')


# --- cell refs and idents ---


def test_simple_cell_ref():
    toks = tokenize("A1")
    assert toks[0] == Token(TK.CELL_REF, "A1", 0)


def test_greedy_cell_ref_AA10_is_one_token():
    """Predicted bug #2: tokenizer must consume AA as one letter run, then 10."""
    assert kinds("AA10") == [TK.CELL_REF]
    assert values("AA10") == ["AA10"]


def test_cell_ref_normalized_to_uppercase():
    toks = tokenize("aa10")
    assert toks[0] == Token(TK.CELL_REF, "AA10", 0)


def test_ident_without_digits():
    # SUM has no trailing digits => IDENT, not CELL_REF.
    toks = tokenize("SUM")
    assert toks[0] == Token(TK.IDENT, "SUM", 0)


def test_ident_case_normalized():
    toks = tokenize("sum")
    assert toks[0] == Token(TK.IDENT, "SUM", 0)


def test_two_adjacent_cell_refs_AA10_then_A1():
    """A1B2 → CELL_REF A1, CELL_REF B2. (Parser will reject; tokenizer is happy.)"""
    assert kinds("A1B2") == [TK.CELL_REF, TK.CELL_REF]
    assert values("A1B2") == ["A1", "B2"]


# --- operators and punctuation ---


def test_arith_operators():
    assert kinds("1+2-3*4/5") == [
        TK.NUMBER, TK.PLUS,
        TK.NUMBER, TK.MINUS,
        TK.NUMBER, TK.STAR,
        TK.NUMBER, TK.SLASH,
        TK.NUMBER,
    ]


def test_parens_colon_comma():
    assert kinds("SUM(A1:A10,B1)") == [
        TK.IDENT, TK.LPAREN,
        TK.CELL_REF, TK.COLON, TK.CELL_REF,
        TK.COMMA, TK.CELL_REF,
        TK.RPAREN,
    ]


# --- comparisons (predicted bug #11) ---


def test_two_char_leq_beats_lt():
    assert kinds("A1<=5") == [TK.CELL_REF, TK.LEQ, TK.NUMBER]
    assert values("A1<=5")[1] == "<="


def test_two_char_geq_beats_gt():
    assert kinds("A1>=5") == [TK.CELL_REF, TK.GEQ, TK.NUMBER]


def test_two_char_neq_beats_lt():
    assert kinds("A1<>5") == [TK.CELL_REF, TK.NEQ, TK.NUMBER]


def test_lone_lt_and_gt():
    assert kinds("A1<5") == [TK.CELL_REF, TK.LT, TK.NUMBER]
    assert kinds("A1>5") == [TK.CELL_REF, TK.GT, TK.NUMBER]


def test_equals_token():
    # "=" inside an expression is comparison (EQ). The leading formula-marker
    # "=" is stripped by the parser entry point, not the tokenizer.
    assert kinds("A1=5") == [TK.CELL_REF, TK.EQ, TK.NUMBER]


# --- unary minus (predicted bug #10) ---


def test_unary_minus_at_start_tokenizes_as_minus_then_cell_ref():
    # The parser is the one that interprets this as unary; tokenizer just emits MINUS, CELL_REF.
    assert kinds("-A1") == [TK.MINUS, TK.CELL_REF]


# --- full IF-style formula sanity ---


def test_full_if_formula():
    toks = tokenize('IF(A1>5,"big","small")')
    assert kinds('IF(A1>5,"big","small")') == [
        TK.IDENT, TK.LPAREN,
        TK.CELL_REF, TK.GT, TK.NUMBER, TK.COMMA,
        TK.STRING, TK.COMMA,
        TK.STRING,
        TK.RPAREN,
    ]
    # And the string contents preserved verbatim:
    str_toks = [t for t in toks if t.kind is TK.STRING]
    assert [t.value for t in str_toks] == ["big", "small"]


# --- bad input ---


def test_unknown_char_errors():
    # "$" is reserved for absolute references in v1.1; v1.0 rejects it.
    with pytest.raises(TokenizeError, match=r"unexpected character"):
        tokenize("$A$1")


def test_token_positions_are_recorded():
    toks = tokenize("A1 + 5")
    # A1 starts at 0, + starts at 3 (after the space), 5 starts at 5.
    assert (toks[0].pos, toks[1].pos, toks[2].pos) == (0, 3, 5)
