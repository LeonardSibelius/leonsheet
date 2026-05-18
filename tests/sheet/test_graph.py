"""Smoke tests for the pure graph algorithms.

Synthetic graphs only — no Sheet, no parser, no evaluator. The graph
functions are tested in isolation so a failure here points at the
algorithm itself, not at how Sheet wires it in.

Coverage targets every predicted-bug shape the kickoff named for this layer:
  * #5  topo sort stability (deterministic order under ties)
  * #8  cycle detection: trivial self-loop AND large N-cell cycle
  * #12 stale-edge cleanup is a Sheet concern, but the topo sort must
        produce identical output for identical inputs (regression test)

Plus the standard suite:
  * empty graph, single node, linear chain, diamond, tall N-chain
  * mixed cycled + non-cycled subgraph (downstream of cycle is also cycled)
  * subgraph induced — external dependencies are ignored
"""

from __future__ import annotations

from app.sheet.graph import topological_sort, transitive_dependents


# Cell aliases for readability.
A = (0, 0); B = (0, 1); C = (0, 2); D = (0, 3); E = (0, 4)
A2 = (1, 0); B2 = (1, 1); C2 = (1, 2); D2 = (1, 3)


# --- transitive_dependents ---


def test_transitive_dependents_empty_graph():
    assert transitive_dependents({A}, {}) == set()


def test_transitive_dependents_single_consumer():
    # A is depended on by B only.
    rev = {A: {B}}
    assert transitive_dependents({A}, rev) == {B}


def test_transitive_dependents_chain():
    # A -> B -> C -> D (each one depended on by the next)
    rev = {A: {B}, B: {C}, C: {D}}
    assert transitive_dependents({A}, rev) == {B, C, D}


def test_transitive_dependents_diamond():
    # A -> B, A -> C, both B and C -> D
    rev = {A: {B, C}, B: {D}, C: {D}}
    assert transitive_dependents({A}, rev) == {B, C, D}


def test_transitive_dependents_seeds_not_included():
    """Even when a seed appears in its own reverse adjacency (cycle), it's not returned."""
    rev = {A: {A, B}}
    # A's dependents include A itself (self-cycle) and B. We return only B.
    assert transitive_dependents({A}, rev) == {B}


def test_transitive_dependents_multiple_seeds_union():
    rev = {A: {B}, C: {D}}
    assert transitive_dependents({A, C}, rev) == {B, D}


# --- topological_sort: ordering ---


def test_topo_empty():
    ordered, cycled = topological_sort(set(), {}, {})
    assert ordered == []
    assert cycled == set()


def test_topo_single_isolated_node():
    ordered, cycled = topological_sort({A}, {}, {})
    assert ordered == [A]
    assert cycled == set()


def test_topo_linear_chain():
    # B depends on A; C depends on B; D depends on C.
    # Eval order must be A, B, C, D.
    depends_on = {B: {A}, C: {B}, D: {C}}
    depended_on_by = {A: {B}, B: {C}, C: {D}}
    ordered, cycled = topological_sort({A, B, C, D}, depends_on, depended_on_by)
    assert ordered == [A, B, C, D]
    assert cycled == set()


def test_topo_diamond_with_deterministic_tiebreak():
    """Predicted bug #5: when B and C are both ready, the smaller (row, col) wins.

    Diamond: B depends on A; C depends on A; D depends on both B and C.
    A=(0,0), B=(0,1), C=(1,0), D=(1,1). After A, both B and C are ready.
    By (row, col) min-heap, B=(0,1) < C=(1,0), so order is A, B, C, D.
    """
    depends_on = {B: {A}, C: {A}, D: {B, C}}
    depended_on_by = {A: {B, C}, B: {D}, C: {D}}
    ordered, cycled = topological_sort({A, B, C, D}, depends_on, depended_on_by)
    assert ordered == [A, B, C, D]
    assert cycled == set()


def test_topo_subgraph_ignores_external_dependencies():
    """Dependencies outside `nodes` are treated as already-evaluated.

    Setup: B depends on A (which is outside `nodes`) and on C (inside `nodes`).
    Within the subgraph {B, C}, B's in-degree is 1 (from C), not 2.
    """
    depends_on = {B: {A, C}}
    depended_on_by = {A: {B}, C: {B}}
    ordered, cycled = topological_sort({B, C}, depends_on, depended_on_by)
    # C has no in-subgraph deps -> C first; B depends on C -> B second.
    assert ordered == [C, B]
    assert cycled == set()


def test_topo_stable_under_set_iteration_order():
    """The same logical graph must produce the same `ordered` list across runs.

    Set iteration is order-stable within a Python process but the spec
    requires reproducibility regardless. Build the graph two different
    ways and compare results.
    """
    depends_on_1 = {D: {A, B, C}}
    depended_on_by_1 = {A: {D}, B: {D}, C: {D}}
    depends_on_2 = {D: {C, B, A}}  # constructed differently
    depended_on_by_2 = {C: {D}, B: {D}, A: {D}}
    ordered_1, _ = topological_sort({A, B, C, D}, depends_on_1, depended_on_by_1)
    ordered_2, _ = topological_sort({A, B, C, D}, depends_on_2, depended_on_by_2)
    assert ordered_1 == ordered_2 == [A, B, C, D]


# --- topological_sort: cycle detection ---


def test_topo_self_loop():
    """Predicted bug #8a: trivial 1-cell cycle, =A1 in A1."""
    depends_on = {A: {A}}
    depended_on_by = {A: {A}}
    ordered, cycled = topological_sort({A}, depends_on, depended_on_by)
    assert ordered == []
    assert cycled == {A}


def test_topo_two_cell_cycle():
    """A depends on B, B depends on A."""
    depends_on = {A: {B}, B: {A}}
    depended_on_by = {A: {B}, B: {A}}
    ordered, cycled = topological_sort({A, B}, depends_on, depended_on_by)
    assert ordered == []
    assert cycled == {A, B}


def test_topo_100_cell_cycle():
    """Predicted bug #8b: large N-cell cycle.

    Build A, B, C, ..., 100-cell cycle where cell_i depends on cell_{i+1}
    and the last cell depends on the first.
    """
    cells = [(0, i) for i in range(100)]
    depends_on: dict = {}
    depended_on_by: dict = {}
    for i, cell in enumerate(cells):
        next_cell = cells[(i + 1) % 100]
        depends_on[cell] = {next_cell}
        depended_on_by.setdefault(next_cell, set()).add(cell)
    ordered, cycled = topological_sort(set(cells), depends_on, depended_on_by)
    assert ordered == []
    assert cycled == set(cells)


def test_topo_cycle_plus_downstream():
    """A cycle's downstream cells are also unevaluatable.

    Setup: A <-> B is a cycle. C depends on B. D depends on C.
    C and D can't be evaluated because B can't be — they're cycled too.
    """
    depends_on = {A: {B}, B: {A}, C: {B}, D: {C}}
    depended_on_by = {A: {B}, B: {A, C}, C: {D}}
    ordered, cycled = topological_sort(
        {A, B, C, D}, depends_on, depended_on_by
    )
    assert ordered == []
    assert cycled == {A, B, C, D}


def test_topo_mixed_cycle_and_safe_branch():
    """Cells unrelated to a cycle still get evaluated.

    Setup: A <-> B cycle (unevaluatable); separately, C depends on D (safe).
    """
    depends_on = {A: {B}, B: {A}, C: {D}}
    depended_on_by = {A: {B}, B: {A}, D: {C}}
    ordered, cycled = topological_sort(
        {A, B, C, D}, depends_on, depended_on_by
    )
    # D first (no deps), then C. A and B never reach in-degree 0.
    assert ordered == [D, C]
    assert cycled == {A, B}


def test_topo_in_degree_within_subgraph_only():
    """A dep on a cycled cell OUTSIDE the subgraph doesn't make a node cycled.

    This is the subtle case: if `nodes` excludes the cycle, but a cell
    in `nodes` depends on something in the cycle (outside `nodes`), the
    in-subgraph in-degree is 0 and the cell IS evaluated. (Whether the
    eval result is right is the evaluator's problem, not the sort's —
    the sort just orders work.)
    """
    depends_on = {A: {B}}  # A depends on B, but B is outside `nodes`
    depended_on_by = {B: {A}}
    ordered, cycled = topological_sort({A}, depends_on, depended_on_by)
    assert ordered == [A]
    assert cycled == set()
