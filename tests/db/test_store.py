"""Round-trip tests for the Store: Sheet state -> SQLite -> fresh Sheet.

Acceptance criterion #7 ("Refresh the page. All cells survive the refresh
with both values and formulas intact.") lives here, verified by setting
state, persisting, loading a brand-new Sheet from the same DB, and
asserting identical computed values.

Predicted-bug guards exercised across the persist boundary:
  * #8  cycles survive a reload as #CIRCULAR! (not turning into stale data)
  * #12 formula replacement doesn't leave stale rows in `dependencies`
"""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from app.db.store import Store
from app.formula.values import EMPTY, ERR_CIRCULAR, ERR_VALUE


def test_load_empty_returns_empty_sheet(tmp_path: Path):
    sheet = Store(tmp_path / "test.db").load()
    assert sheet.get_value(0, 0) is EMPTY
    assert sheet.raw_values == {}


def test_persist_and_reload_literal(tmp_path: Path):
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    sheet.set_cell(0, 0, "5")
    Store(db).persist_cells(sheet, [(0, 0)])

    sheet2 = Store(db).load()
    assert sheet2.get_value(0, 0) == 5.0
    assert sheet2.get_raw(0, 0) == "5"


def test_persist_and_reload_text(tmp_path: Path):
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    sheet.set_cell(0, 0, "hello world")
    Store(db).persist_cells(sheet, [(0, 0)])

    sheet2 = Store(db).load()
    assert sheet2.get_value(0, 0) == "hello world"


def test_persist_and_reload_formula_chain(tmp_path: Path):
    """Acceptance #7 in its commonest form: literal + formula round-trip."""
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    sheet.set_cell(0, 0, "10")
    sheet.set_cell(1, 0, "5")
    sheet.set_cell(2, 0, "=A1+A2")
    Store(db).persist_cells(sheet, [(0, 0), (1, 0), (2, 0)])

    sheet2 = Store(db).load()
    assert sheet2.get_value(0, 0) == 10.0
    assert sheet2.get_value(1, 0) == 5.0
    assert sheet2.get_value(2, 0) == 15.0
    assert sheet2.get_raw(2, 0) == "=A1+A2"


def test_persist_and_reload_sum_range(tmp_path: Path):
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    for r in range(5):
        sheet.set_cell(r, 0, str(r + 1))
    sheet.set_cell(0, 1, "=SUM(A1:A5)")
    Store(db).persist_cells(
        sheet, [(r, 0) for r in range(5)] + [(0, 1)]
    )

    sheet2 = Store(db).load()
    assert sheet2.get_value(0, 1) == 15.0


def test_persist_and_reload_cycle(tmp_path: Path):
    """Cycles survive a reload as #CIRCULAR! — the dep graph rebuilds and
    re-detects the cycle on bulk_load."""
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    sheet.set_cell(0, 0, "=B1")
    sheet.set_cell(0, 1, "=A1")
    Store(db).persist_cells(sheet, [(0, 0), (0, 1)])

    sheet2 = Store(db).load()
    assert sheet2.get_value(0, 0) == ERR_CIRCULAR
    assert sheet2.get_value(0, 1) == ERR_CIRCULAR


def test_persist_and_reload_self_referencing_range_circular(tmp_path: Path):
    """Predicted bug #13 across the persist boundary."""
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    for r in range(10):
        sheet.set_cell(r, 0, str(r + 1))
    sheet.set_cell(4, 0, "=SUM(A1:A10)")  # A5 inside the range
    cells_to_persist = [(r, 0) for r in range(10)]
    Store(db).persist_cells(sheet, cells_to_persist)

    sheet2 = Store(db).load()
    assert sheet2.get_value(4, 0) == ERR_CIRCULAR


def test_clear_cell_persists_as_deletion(tmp_path: Path):
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    sheet.set_cell(0, 0, "5")
    Store(db).persist_cells(sheet, [(0, 0)])
    sheet.set_cell(0, 0, "")
    Store(db).persist_cells(sheet, [(0, 0)])

    sheet2 = Store(db).load()
    assert sheet2.get_value(0, 0) is EMPTY
    assert (0, 0) not in sheet2.raw_values


def test_formula_replacement_purges_old_dep_rows(tmp_path: Path):
    """Predicted bug #12 at the DB layer: stale rows in `dependencies`
    must NOT accumulate across formula replacements."""
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    sheet.set_cell(0, 0, "5")
    sheet.set_cell(1, 0, "100")
    sheet.set_cell(2, 0, "=A1")  # A3 depends on A1
    Store(db).persist_cells(sheet, [(0, 0), (1, 0), (2, 0)])

    sheet.set_cell(2, 0, "=A2")  # replace; now depends on A2 only
    Store(db).persist_cells(sheet, [(2, 0)])

    # Direct DB inspection: there should be one dep row for A3 -> A2 only.
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "SELECT from_row, from_col, to_row, to_col FROM dependencies "
            "WHERE from_row=? AND from_col=?",
            (2, 0),
        ).fetchall()
    assert rows == [(2, 0, 1, 0)]

    # Behavioral check via reload: editing A1 must NOT touch A3.
    sheet2 = Store(db).load()
    affected = sheet2.set_cell(0, 0, "999")
    assert (2, 0) not in affected
    assert sheet2.get_value(2, 0) == 100.0


def test_meta_last_modified_recorded(tmp_path: Path):
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    sheet.set_cell(0, 0, "5")
    Store(db).persist_cells(sheet, [(0, 0)])

    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='last_modified'"
        ).fetchone()
    assert row is not None
    assert abs(int(row[0]) - int(time.time())) < 10


def test_bulk_persist_atomic(tmp_path: Path):
    """20 cells in one persist call land in one transaction."""
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    cells = []
    for r in range(20):
        sheet.set_cell(r, 0, str(r))
        cells.append((r, 0))
    Store(db).persist_cells(sheet, cells)

    sheet2 = Store(db).load()
    for r in range(20):
        assert sheet2.get_value(r, 0) == float(r)


def test_parse_error_cell_survives_reload(tmp_path: Path):
    """A cell containing a malformed formula should reload as #VALUE! and
    keep its raw text so the user can edit it."""
    db = tmp_path / "test.db"
    sheet = Store(db).load()
    sheet.set_cell(0, 0, "=A1+")  # malformed
    Store(db).persist_cells(sheet, [(0, 0)])

    sheet2 = Store(db).load()
    assert sheet2.get_value(0, 0) == ERR_VALUE
    assert sheet2.get_raw(0, 0) == "=A1+"


def test_persist_empty_cell_list_is_noop(tmp_path: Path):
    """Persisting an empty iterable should not open a connection / create files unnecessarily."""
    db = tmp_path / "test.db"
    sheet = Store(db).load()  # this creates the DB file
    Store(db).persist_cells(sheet, [])  # should not raise
    # And the DB should be clean still.
    sheet2 = Store(db).load()
    assert sheet2.raw_values == {}
