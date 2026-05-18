"""Dependency graph algorithms.

The engineering centerpiece of leonsheet v1.0 — every commit message
in this layer should remind the reader that this is THE code that makes
"working spreadsheet" a credible claim.

We model a spreadsheet as a directed graph:
  * Nodes are cells, identified by (row, col) tuples — see [predicted bug #17]
    in the kickoff: every subsystem speaks (row, col) ints internally.
  * An edge A -> B means "cell A's formula REFERENCES cell B"
    (i.e., A depends on B; B must be evaluated before A).

Two adjacency maps are maintained in parallel by the Sheet:
  * `depends_on[A]`     = set of cells A's formula references (forward)
  * `depended_on_by[B]` = set of cells whose formulas reference B (reverse)

The reverse map is the hot path: "X just changed — what needs recalc?"
is one lookup per visited cell.

This module is intentionally pure: every function takes adjacency maps
and returns sets/lists. No Sheet-state coupling, no I/O, no parsing.
That makes the cycle-detection and ordering logic unit-testable on
synthetic graphs (see tests/sheet/test_graph.py).

Two functions live here:
  1. transitive_dependents — reverse BFS from a set of seeds.
  2. topological_sort      — Kahn's algorithm on the subgraph induced by
                             a node set, with cycle detection falling out
                             as a natural byproduct (cycled = nodes that
                             never reached in-degree 0).

The composition (transitive_dependents -> topological_sort) is the
entirety of the recalc strategy. See Sheet._recalc_from for the wiring.
"""

from __future__ import annotations

import heapq
from typing import Iterable

CellId = tuple[int, int]


def transitive_dependents(
    seeds: Iterable[CellId],
    depended_on_by: dict[CellId, set[CellId]],
) -> set[CellId]:
    """Return every cell that transitively depends on any seed.

    Walks the *reverse* adjacency outward from `seeds`. Cells reachable
    via a chain of reverse edges are returned. The seeds themselves are
    NEVER included, even if they're reachable from themselves via a
    cycle — the caller can union them back in if they want the full
    "all affected cells" set.

    The traversal is a stack-based DFS (cheaper pop than FIFO BFS at
    Python list speed). The visit-each-once invariant comes from the
    `visited` set, not the traversal order, so the resulting *set* is
    identical for either DFS or BFS.

    Complexity: O(V + E) over the reverse-reachable subgraph.
    For the v1.0 acceptance benchmark — 50 transitive dependents — this
    is microseconds, leaving the 500ms budget entirely to topo + evaluation.
    """
    seed_set = set(seeds)
    visited: set[CellId] = set()
    stack: list[CellId] = []

    for seed in seed_set:
        for dependent in depended_on_by.get(seed, ()):
            if dependent not in visited and dependent not in seed_set:
                visited.add(dependent)
                stack.append(dependent)

    while stack:
        cell = stack.pop()
        for dependent in depended_on_by.get(cell, ()):
            if dependent not in visited and dependent not in seed_set:
                visited.add(dependent)
                stack.append(dependent)

    return visited


def topological_sort(
    nodes: set[CellId],
    depends_on: dict[CellId, set[CellId]],
    depended_on_by: dict[CellId, set[CellId]],
) -> tuple[list[CellId], set[CellId]]:
    """Kahn's algorithm restricted to the subgraph induced by `nodes`.

    Returns (ordered, cycled):
      * ordered  — list of cells in dependency-safe evaluation order.
                   Each cell appears AFTER every cell it depends on,
                   restricted to `nodes`. Dependencies that live outside
                   `nodes` are assumed already-evaluated (their stored
                   computed value is current) and impose no order.
      * cycled   — set of cells that are part of, or downstream of, a
                   cycle within `nodes`. These were not reached by the
                   sort; the caller marks them #CIRCULAR! and skips
                   evaluation.

    Algorithm (textbook Kahn's, with deterministic tie-breaking):

      1. Compute the in-degree of each node within the induced subgraph:
         how many of its dependencies are also in `nodes`?
      2. Seed `ready` with every node of in-degree 0 — a min-heap keyed
         by (row, col) for predictable ordering.
      3. Repeat until `ready` is empty:
         a. Pop the smallest cell from `ready`, append to `ordered`.
         b. For every cell that depends on this cell (within `nodes`),
            decrement its in-degree. If it reaches 0, push to `ready`.
      4. Whatever remains in `nodes` but didn't make it into `ordered`
         must be in a cycle (or downstream of one) — return them as
         `cycled`. This is the natural cycle-detection property of
         Kahn's: a node only finishes when all its prerequisites have,
         and a cycle means at least one node never gets its prerequisites.

    --- Predicted-bug guards baked into this function ---

    Predicted bug #5 (order stability): `ready` is a heap keyed on
    (row, col). When multiple cells become ready simultaneously (the
    typical case in a wide diamond DAG), the one with the smallest
    address pops first. The returned `ordered` list is therefore a
    deterministic function of the input graph: the same DAG always
    produces the same eval order across runs. This is what the
    acceptance smoke tests need to compare expected vs. actual.

    Predicted bug #8 (cycle detection inside cycle detection): both
    cycle shapes that the kickoff called out are caught by the same
    code path:
      * Trivial self-cycle (=A1 in A1): the cell appears in its own
        `depends_on`, giving it in-degree 1 with no provider. Never
        ready. Ends up in `cycled`.
      * Large N-cell cycle (A->B->...->Z->A): every cell has in-degree
        1 within the cycle. None reach 0. All `cycled`.

    Predicted bug #13 (self-referencing range): handled one layer up,
    in Sheet._extract_deps — ranges are expanded into individual cell
    refs before the graph sees them. By the time we get here, an
    A5=SUM(A1:A10) edit has already written A5 -> A5 as a real edge,
    and the self-cycle branch above catches it.
    """
    # In-degree of each node within the induced subgraph.
    in_degree: dict[CellId, int] = {}
    for node in nodes:
        in_degree[node] = sum(
            1 for dep in depends_on.get(node, ()) if dep in nodes
        )

    # Heap of cells with no unresolved subgraph-internal dependencies.
    # tuples compare lexicographically, so (row, col) gives us deterministic order.
    ready: list[CellId] = [n for n, d in in_degree.items() if d == 0]
    heapq.heapify(ready)

    ordered: list[CellId] = []
    while ready:
        cell = heapq.heappop(ready)
        ordered.append(cell)
        # Decrement in-degree of every cell that depends on `cell`.
        for dependent in depended_on_by.get(cell, ()):
            if dependent in in_degree:  # only cells in the subgraph
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    heapq.heappush(ready, dependent)

    cycled = nodes - set(ordered)
    return ordered, cycled
