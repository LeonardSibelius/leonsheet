"""In-memory Sheet: cell store + recalc orchestration.

Owns the per-cell state:
  * raw_values     — exactly what the user typed ("5", "=A1+B1", "hello", "")
  * formulas       — parsed AST for cells whose raw_value starts with "="
  * computed       — last evaluated Value (what the UI displays)
  * depends_on     — forward adjacency (who do I reference?)
  * depended_on_by — reverse adjacency (who references me?)

Orchestrates the layers below (parser, evaluator, dep graph). Knows
nothing about persistence or HTTP — those are separate concerns and
will land in their own modules at Checkpoint 4+.

Key invariants:
  * The forward and reverse adjacency maps are always in sync: for every
    edge A -> B in depends_on, A is in depended_on_by[B].
  * When a cell's formula changes, OLD forward edges are torn down BEFORE
    new ones are added (predicted bug #12: dependency staleness on
    formula replacement).
  * Cycle membership is detected per-edit by topological_sort, and cycled
    cells are marked ERR_CIRCULAR but their depends_on edges are RETAINED.
    Retaining the edges is what lets a cycle-breaking edit recover the
    cells: when the user fixes one side, recalc walks the (still-present)
    reverse edges and brings everything back to numeric values.
  * Range nodes are expanded to individual cell refs at dep-extraction
    time (predicted bug #13: self-referencing range). The graph only ever
    sees (row, col) edges, never opaque Range objects.

Predicted bug #5 (recalc order determinism) is handled by topological_sort
itself — see app/sheet/graph.py for the heap-on-(row,col) detail.
"""

from __future__ import annotations

from app.formula.ast_nodes import (
    ASTNode,
    BinaryOp,
    CellRef,
    FunctionCall,
    Range,
    UnaryOp,
)
from app.formula.evaluator import Evaluator
from app.formula.parser import ParseError, parse_formula
from app.formula.values import EMPTY, ERR_CIRCULAR, ERR_VALUE, ErrorValue, Value
from app.sheet.graph import CellId, topological_sort, transitive_dependents


def _parse_literal(raw: str) -> Value:
    """Coerce a non-formula raw_value to its typed Value.

    "" -> EMPTY (the singleton)
    "5"     -> 5.0
    "3.14"  -> 3.14
    "hello" -> "hello"
    """
    if raw == "":
        return EMPTY
    try:
        return float(raw)
    except ValueError:
        return raw


def _extract_deps(ast: ASTNode) -> set[CellId]:
    """Walk an AST collecting every cell it references.

    Range nodes are EXPANDED to individual (row, col) pairs here, NOT stored
    as opaque Range objects in the graph. This is predicted bug #13's fix:
    if A5 = =SUM(A1:A10), the dep extraction must produce {A1..A10} which
    includes A5 itself, so the cycle detector sees the self-edge.
    """
    deps: set[CellId] = set()

    def walk(node: ASTNode) -> None:
        if isinstance(node, CellRef):
            deps.add((node.row, node.col))
        elif isinstance(node, Range):
            for r in range(node.start_row, node.end_row + 1):
                for c in range(node.start_col, node.end_col + 1):
                    deps.add((r, c))
        elif isinstance(node, BinaryOp):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, UnaryOp):
            walk(node.operand)
        elif isinstance(node, FunctionCall):
            for arg in node.args:
                walk(arg)
        # Number, String have no dependencies.

    walk(ast)
    return deps


class Sheet:
    """A single sheet's in-memory state. Persistence lives elsewhere."""

    def __init__(self) -> None:
        self.raw_values: dict[CellId, str] = {}
        self.formulas: dict[CellId, ASTNode] = {}
        self.computed: dict[CellId, Value] = {}
        self.depends_on: dict[CellId, set[CellId]] = {}
        self.depended_on_by: dict[CellId, set[CellId]] = {}

    # --- public API ---

    def set_cell(self, row: int, col: int, raw_value: str) -> set[CellId]:
        """Update a cell's raw value, then recompute every cell that needs to change.

        Returns the set of all affected cells (including the edited cell
        itself) — the caller uses this for UI diff / DB writes.

        Raw value handling:
          * ""               -> cell cleared
          * "=…"             -> formula; parsed and dep-extracted
          * "5", "3.14"      -> numeric literal (coerced at evaluation)
          * anything else    -> text literal

        If the formula fails to parse, the cell's raw_value is stored
        anyway (so the user can fix the syntax) and computed becomes
        ERR_VALUE.
        """
        cell: CellId = (row, col)

        # 1. Tear down any old forward edges from this cell BEFORE we add new ones.
        #    Predicted bug #12: missing this step leaves phantom reverse-edges
        #    pointing into the cell from cells it used to reference, causing
        #    spurious recomputations forever.
        self._remove_edges_from(cell)

        # 2. Update the raw_value / formula state.
        parse_failed = False
        if raw_value == "":
            self.raw_values.pop(cell, None)
            self.formulas.pop(cell, None)
        else:
            self.raw_values[cell] = raw_value
            if raw_value.startswith("="):
                try:
                    ast = parse_formula(raw_value)
                    self.formulas[cell] = ast
                    self._add_edges(cell, _extract_deps(ast))
                except ParseError:
                    self.formulas.pop(cell, None)
                    parse_failed = True
            else:
                self.formulas.pop(cell, None)

        # 3. Recompute. _recalc_from walks the reverse-reachable subgraph,
        #    topo-sorts it, and evaluates in order. Cycled cells get marked.
        affected = self._recalc_from(cell)

        # 4. If the parse failed, override the computed value with #VALUE!.
        #    _recalc_from would otherwise evaluate the cell as a literal,
        #    returning the formula text as a string — confusing for the user.
        if parse_failed:
            self.computed[cell] = ERR_VALUE
            # Also propagate the error to dependents (which would have seen
            # the raw text "=A1+" as a string otherwise). Cheaper to just
            # re-evaluate them with the new ERR_VALUE in place.
            for dep_cell in affected - {cell}:
                self.computed[dep_cell] = self._evaluate_cell(dep_cell)

        return affected

    def get_value(self, row: int, col: int) -> Value:
        """Last computed value at (row, col), or EMPTY if the cell isn't set."""
        return self.computed.get((row, col), EMPTY)

    def get_raw(self, row: int, col: int) -> str:
        """The string the user last typed into this cell (or '' if not set)."""
        return self.raw_values.get((row, col), "")

    # --- internals ---

    def _remove_edges_from(self, cell: CellId) -> None:
        """Drop every forward edge from `cell` and the matching reverse edges.

        Maintains the forward/reverse symmetry invariant.
        """
        for dep in self.depends_on.pop(cell, ()):
            reverse_set = self.depended_on_by.get(dep)
            if reverse_set is not None:
                reverse_set.discard(cell)
                if not reverse_set:
                    # Don't leave empty sets lying around; future
                    # transitive-closure walks check `.get(dep, ())`.
                    del self.depended_on_by[dep]

    def _add_edges(self, cell: CellId, deps: set[CellId]) -> None:
        if not deps:
            return
        self.depends_on[cell] = set(deps)
        for dep in deps:
            self.depended_on_by.setdefault(dep, set()).add(cell)

    def _recalc_from(self, seed: CellId) -> set[CellId]:
        """Recompute the seed and every cell transitively depending on it.

        The contract from each helper:
          * transitive_dependents — gives us the reverse-reachable closure
            (cells whose value MIGHT change). Doesn't include the seed.
          * topological_sort      — partitions the affected subgraph into
            (ordered, cycled). `ordered` is dependency-safe; `cycled` is
            unevaluatable due to a cycle.

        We union the seed back in because it's always part of `affected`
        (it just got edited).
        """
        affected = transitive_dependents({seed}, self.depended_on_by) | {seed}

        ordered, cycled = topological_sort(
            affected, self.depends_on, self.depended_on_by
        )

        # Cycled cells: mark #CIRCULAR! and do NOT evaluate. Their depends_on
        # edges remain in place so a future cycle-breaking edit can recover them.
        for c in cycled:
            self.computed[c] = ERR_CIRCULAR

        # Non-cycled cells: evaluate in topo order. Each cell's deps either
        # live OUTSIDE `affected` (their stored computed value is current) or
        # come earlier in `ordered` (just computed this pass).
        for c in ordered:
            self.computed[c] = self._evaluate_cell(c)

        return affected

    def _evaluate_cell(self, cell: CellId) -> Value:
        """Evaluate one cell. Assumes all dependencies are already evaluated."""
        if cell in self.formulas:
            return Evaluator(self.get_value).evaluate(self.formulas[cell])
        return _parse_literal(self.raw_values.get(cell, ""))
