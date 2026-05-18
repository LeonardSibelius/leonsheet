"""Recursive-descent parser for leonsheet formulas.

Grammar (recapped from the tokenizer module):

    formula     := "=" expression
    expression  := comparison
    comparison  := arith ( CMP arith )?
    arith       := term (("+" | "-") term)*
    term        := factor (("*" | "/") factor)*
    factor      := ("+" | "-")? base
    base        := number | string | cell_ref | function_call | "(" expression ")"
    function_call := IDENT "(" arg ("," arg)* ")"
    arg         := range | expression
    range       := cell_ref ":" cell_ref

Two non-spec design choices:
  * Comparison is its own precedence level *below* arithmetic. Predicted bug #11.
    `A1+1 < B1+1` parses as `(A1+1) < (B1+1)`, matching Excel.
  * Unary +/- is on the factor rule. Predicted bug #10 — `=-A1` and `=-5+3` must parse.

Range vs expression in function args: range only ever appears as
`CELL_REF ":" CELL_REF`. The arg parser does a one-token lookahead — if it
sees CELL_REF followed by COLON, it parses a range; otherwise an expression.
At the top level of a formula, `A1:A5` is NOT a legal expression — that
restriction falls out naturally because `parse_base` doesn't accept COLON.
"""

from __future__ import annotations

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
from .refs import parse_cell_ref
from .tokenizer import Token, TokenKind, tokenize


class ParseError(ValueError):
    """Raised when the token stream doesn't match the formula grammar."""


_CMP_OPS = {
    TokenKind.EQ: "=",
    TokenKind.NEQ: "<>",
    TokenKind.LT: "<",
    TokenKind.GT: ">",
    TokenKind.LEQ: "<=",
    TokenKind.GEQ: ">=",
}


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # --- token-stream helpers ---

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[self.pos + offset]

    def consume(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind: TokenKind) -> Token:
        tok = self.peek()
        if tok.kind is not kind:
            raise ParseError(
                f"expected {kind.name}, got {tok.kind.name} {tok.value!r} at pos {tok.pos}"
            )
        return self.consume()

    # --- entry point ---

    def parse(self) -> ASTNode:
        node = self.parse_comparison()
        self.expect(TokenKind.EOF)
        return node

    # --- precedence climbing (lowest to highest) ---

    def parse_comparison(self) -> ASTNode:
        left = self.parse_arith()
        if self.peek().kind in _CMP_OPS:
            op_tok = self.consume()
            right = self.parse_arith()
            return BinaryOp(_CMP_OPS[op_tok.kind], left, right)
        return left

    def parse_arith(self) -> ASTNode:
        node = self.parse_term()
        while self.peek().kind in (TokenKind.PLUS, TokenKind.MINUS):
            op_tok = self.consume()
            right = self.parse_term()
            node = BinaryOp(op_tok.value, node, right)
        return node

    def parse_term(self) -> ASTNode:
        node = self.parse_factor()
        while self.peek().kind in (TokenKind.STAR, TokenKind.SLASH):
            op_tok = self.consume()
            right = self.parse_factor()
            node = BinaryOp(op_tok.value, node, right)
        return node

    def parse_factor(self) -> ASTNode:
        if self.peek().kind in (TokenKind.PLUS, TokenKind.MINUS):
            op_tok = self.consume()
            operand = self.parse_factor()  # right-associative unary chain (- - A1)
            return UnaryOp(op_tok.value, operand)
        return self.parse_base()

    def parse_base(self) -> ASTNode:
        tok = self.peek()
        if tok.kind is TokenKind.NUMBER:
            self.consume()
            return Number(float(tok.value))
        if tok.kind is TokenKind.STRING:
            self.consume()
            return String(tok.value)
        if tok.kind is TokenKind.CELL_REF:
            self.consume()
            row, col = parse_cell_ref(tok.value)
            return CellRef(row, col)
        if tok.kind is TokenKind.IDENT:
            return self.parse_function_call()
        if tok.kind is TokenKind.LPAREN:
            self.consume()
            inner = self.parse_comparison()
            self.expect(TokenKind.RPAREN)
            return inner
        raise ParseError(
            f"unexpected token {tok.kind.name} {tok.value!r} at pos {tok.pos}"
        )

    def parse_function_call(self) -> FunctionCall:
        name_tok = self.expect(TokenKind.IDENT)
        self.expect(TokenKind.LPAREN)
        args: list[ASTNode] = []
        if self.peek().kind is not TokenKind.RPAREN:
            args.append(self.parse_arg())
            while self.peek().kind is TokenKind.COMMA:
                self.consume()
                args.append(self.parse_arg())
        self.expect(TokenKind.RPAREN)
        return FunctionCall(name_tok.value, tuple(args))

    def parse_arg(self) -> ASTNode:
        # Range only appears as CELL_REF ":" CELL_REF — one-token lookahead.
        if (
            self.peek().kind is TokenKind.CELL_REF
            and self.peek(1).kind is TokenKind.COLON
        ):
            start_tok = self.consume()
            self.expect(TokenKind.COLON)
            end_tok = self.expect(TokenKind.CELL_REF)
            start_row, start_col = parse_cell_ref(start_tok.value)
            end_row, end_col = parse_cell_ref(end_tok.value)
            return Range(start_row, start_col, end_row, end_col)
        return self.parse_comparison()


def parse(source: str) -> ASTNode:
    """Parse an expression (no leading '='). For user-entered formulas use parse_formula."""
    return Parser(tokenize(source)).parse()


def parse_formula(source: str) -> ASTNode:
    """Parse a user-entered formula. Strips the leading '=' and parses the rest."""
    src = source.strip()
    if not src.startswith("="):
        raise ParseError("formulas must start with '='")
    return parse(src[1:])
