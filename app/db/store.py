"""Persistence: load Sheet state from SQLite and write edits back.

The Sheet is the source of truth in memory; SQLite is the durable mirror.

What gets persisted:
  * raw_values   — the string the user typed (table: cells)
  * dependencies — the forward adjacency, one row per edge (table: dependencies)
  * last_modified timestamp (table: meta)

What does NOT get persisted (rebuilt on every load):
  * parsed AST       — re-parsed by Sheet.bulk_load
  * reverse adjacency — derived from forward edges
  * computed values  — recomputed by one full-graph topo recalc

Why the dependencies table at all if it's not consulted on load? It's
maintained on writes for v1.1 introspection ("what depends on X?") and
for future on-demand cell loading. v1.0 always loads everything.

Write strategy: every persist_cells call is one transaction. Single edits
get a one-cell transaction; bulk loads (CSV import) get one N-cell
transaction. Predicted bug #6 (write contention) is structurally addressed
by SQLite's WAL mode + serialized writes — see schema.sql.
"""

from __future__ import annotations

import time
from contextlib import closing
from pathlib import Path
from typing import Iterable

from app.db.connection import get_connection
from app.sheet.graph import CellId
from app.sheet.sheet import Sheet


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def load(self) -> Sheet:
        """Read all cells from the DB, build a Sheet, recompute every formula.

        Spec v1.0 §5: "On startup: read all cells, rebuild the dependency
        graph, recompute every formula cell once." This is one bulk recalc,
        not N individual set_cell calls — the latter would be O(N²) and
        would also evaluate formulas that reference cells not yet loaded.
        """
        sheet = Sheet()
        with closing(get_connection(self.db_path)) as conn:
            rows = conn.execute("SELECT row, col, raw_value FROM cells").fetchall()
        raw_values: dict[CellId, str] = {(r, c): raw for r, c, raw in rows}
        sheet.bulk_load(raw_values)
        return sheet

    def replace_all(self, sheet: Sheet) -> None:
        """Wipe the DB and write everything from `sheet`. Atomic.

        Used by CSV import, which replaces the entire grid contents in one shot.
        Drops both `cells` and `dependencies` then re-inserts from the sheet's
        in-memory state.
        """
        with closing(get_connection(self.db_path)) as conn:
            with conn:
                conn.execute("DELETE FROM cells")
                conn.execute("DELETE FROM dependencies")
                for (row, col), raw in sheet.raw_values.items():
                    conn.execute(
                        "INSERT INTO cells (row, col, raw_value) VALUES (?, ?, ?)",
                        (row, col, raw),
                    )
                    for (tr, tc) in sheet.depends_on.get((row, col), set()):
                        conn.execute(
                            "INSERT INTO dependencies "
                            "(from_row, from_col, to_row, to_col) VALUES (?, ?, ?, ?)",
                            (row, col, tr, tc),
                        )
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) "
                    "VALUES ('last_modified', ?)",
                    (str(int(time.time())),),
                )

    def persist_cells(self, sheet: Sheet, cells: Iterable[CellId]) -> None:
        """Write the listed cells' raw_value + forward dependencies. Atomic.

        Only the explicitly-listed cells are written, NOT their downstream
        dependents — those have new computed values but their raw_value and
        formula text didn't change, and we don't persist computed values.
        """
        cell_list = list(cells)
        if not cell_list:
            return

        with closing(get_connection(self.db_path)) as conn:
            # `with conn:` is sqlite3's transaction context manager —
            # commits on success, rolls back on exception.
            with conn:
                for (row, col) in cell_list:
                    # Always purge old dep edges from this cell — predicted bug #12
                    # at the persistence layer: stale rows in `dependencies` would
                    # accumulate without this DELETE.
                    conn.execute(
                        "DELETE FROM dependencies WHERE from_row=? AND from_col=?",
                        (row, col),
                    )
                    raw = sheet.raw_values.get((row, col))
                    if raw is None:
                        conn.execute(
                            "DELETE FROM cells WHERE row=? AND col=?", (row, col)
                        )
                    else:
                        conn.execute(
                            "INSERT OR REPLACE INTO cells (row, col, raw_value) "
                            "VALUES (?, ?, ?)",
                            (row, col, raw),
                        )
                        for (tr, tc) in sheet.depends_on.get((row, col), set()):
                            conn.execute(
                                "INSERT INTO dependencies "
                                "(from_row, from_col, to_row, to_col) "
                                "VALUES (?, ?, ?, ?)",
                                (row, col, tr, tc),
                            )
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) "
                    "VALUES ('last_modified', ?)",
                    (str(int(time.time())),),
                )
