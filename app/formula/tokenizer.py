"""Tokenizer for leonsheet formulas.

The grammar (full spec in v1.0 backlog; reproduced here for the reader):

    formula     := "=" expression
    expression  := comparison
    comparison  := arith ( CMP arith )?
    arith       := term (("+" | "-") term)*
    term        := factor (("*" | "/") factor)*
    factor      := ("+" | "-")? base
    base        := number | string | cell_ref | function_call | "(" expression ")"
    cell_ref    := [A-Z]+ [0-9]+
    function_call := IDENT "(" arg ("," arg)* ")"
    arg         := range | expression
    range       := cell_ref ":" cell_ref
    CMP         := "=" | "<>" | "<" | ">" | "<=" | ">="

Notes on departures from the spec's grammar sketch:
  * unary +/- on factors — predicted bug #10 ("=-A1" must parse).
  * comparison operators as a precedence level below arithmetic — predicted
    bug #11 (IF promises =, <>, <, >, <=, >= but spec grammar had nowhere
    to put them). Optional comparison: arith with no CMP returns plain arith.

Token kinds the parser consumes:
  NUMBER  STRING  CELL_REF  IDENT
  LPAREN  RPAREN  COLON  COMMA
  PLUS  MINUS  STAR  SLASH
  EQ  NEQ  LT  GT  LEQ  GEQ
  EOF

The leading "=" that marks a formula is stripped by the formula entry
point (parser/cell layer), NOT by the tokenizer. Inside an expression,
"=" is the equality comparison operator (EQ). Keeping the tokenizer
context-free makes it easier to reason about.

Predicted bug #2 (greedy cell-ref): the tokenizer eats all consecutive
letters BEFORE deciding whether what follows is digits (=> CELL_REF) or
not (=> IDENT). So "AA10" tokenizes as one CELL_REF, not "A" + "A10".

Predicted bug #11 (multi-char comparisons): "<=", ">=", "<>" are
two-char lookahead BEFORE falling back to "<" or ">" alone. Order matters.
"""

from dataclasses import dataclass
from enum import Enum, auto


class TokenKind(Enum):
    NUMBER = auto()
    STRING = auto()
    CELL_REF = auto()
    IDENT = auto()
    LPAREN = auto()
    RPAREN = auto()
    COLON = auto()
    COMMA = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    EQ = auto()
    NEQ = auto()
    LT = auto()
    GT = auto()
    LEQ = auto()
    GEQ = auto()
    EOF = auto()


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    pos: int


class TokenizeError(ValueError):
    """Raised when a formula contains a character or sequence we cannot tokenize."""


_SINGLE_CHAR: dict[str, TokenKind] = {
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    ":": TokenKind.COLON,
    ",": TokenKind.COMMA,
    "+": TokenKind.PLUS,
    "-": TokenKind.MINUS,
    "*": TokenKind.STAR,
    "/": TokenKind.SLASH,
    "=": TokenKind.EQ,
}


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(source)

    while i < n:
        c = source[i]

        if c.isspace():
            i += 1
            continue

        if c in _SINGLE_CHAR:
            tokens.append(Token(_SINGLE_CHAR[c], c, i))
            i += 1
            continue

        # Two-char comparisons must be checked before falling back to "<" or ">".
        if c == "<":
            if i + 1 < n and source[i + 1] == "=":
                tokens.append(Token(TokenKind.LEQ, "<=", i)); i += 2; continue
            if i + 1 < n and source[i + 1] == ">":
                tokens.append(Token(TokenKind.NEQ, "<>", i)); i += 2; continue
            tokens.append(Token(TokenKind.LT, "<", i)); i += 1; continue

        if c == ">":
            if i + 1 < n and source[i + 1] == "=":
                tokens.append(Token(TokenKind.GEQ, ">=", i)); i += 2; continue
            tokens.append(Token(TokenKind.GT, ">", i)); i += 1; continue

        # Number: digits with optional single decimal point. No scientific notation in v1.0.
        # Accept leading-dot form ".5" but not lone ".".
        if c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit()):
            start = i
            seen_dot = c == "."
            i += 1
            while i < n and (source[i].isdigit() or (not seen_dot and source[i] == ".")):
                if source[i] == ".":
                    seen_dot = True
                i += 1
            tokens.append(Token(TokenKind.NUMBER, source[start:i], start))
            continue

        # String literal: double-quoted. No escapes in v1.0 (Excel-style "" doubling can come later).
        if c == '"':
            start = i
            i += 1
            buf: list[str] = []
            while i < n and source[i] != '"':
                buf.append(source[i])
                i += 1
            if i >= n:
                raise TokenizeError(f"unterminated string starting at pos {start}")
            i += 1  # consume closing quote
            tokens.append(Token(TokenKind.STRING, "".join(buf), start))
            continue

        # Letters: could be a CELL_REF (letters then digits) or an IDENT (letters only).
        # Greedy on letters first (predicted bug #2), then peek for digits.
        if c.isalpha():
            start = i
            while i < n and source[i].isalpha():
                i += 1
            letters_end = i
            if i < n and source[i].isdigit():
                while i < n and source[i].isdigit():
                    i += 1
                tokens.append(Token(TokenKind.CELL_REF, source[start:i].upper(), start))
            else:
                tokens.append(Token(TokenKind.IDENT, source[start:letters_end].upper(), start))
            continue

        raise TokenizeError(f"unexpected character {c!r} at pos {i}")

    tokens.append(Token(TokenKind.EOF, "", n))
    return tokens
