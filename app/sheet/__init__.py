"""Sheet layer: dependency graph + recalc orchestration.

This is the v1.0 engineering centerpiece. Two modules:
  * graph.py — pure functions on adjacency maps: transitive closure
    and Kahn's topological sort with built-in cycle detection.
  * sheet.py — the Sheet class that owns cell state and orchestrates
    parse -> dep-extraction -> graph update -> topo recalc on every edit.
"""
