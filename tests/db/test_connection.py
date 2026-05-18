"""Smoke tests for the SQLite connection helper.

Covers: schema applies, schema is idempotent, parent dir auto-created,
WAL mode flipped on, foreign_keys pragma honored.
"""

from contextlib import closing
from pathlib import Path

from app.db.connection import get_connection


def test_creates_parent_directory(tmp_path: Path):
    db_path = tmp_path / "nested" / "deep" / "test.db"
    with closing(get_connection(db_path)):
        pass
    assert db_path.exists()
    assert db_path.parent.is_dir()


def test_schema_creates_three_tables(tmp_path: Path):
    with closing(get_connection(tmp_path / "test.db")) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    # SQLite WAL mode creates a sqlite_sequence row in some cases; we just
    # check our three tables exist.
    assert {"cells", "dependencies", "meta"}.issubset(tables)


def test_schema_idempotent(tmp_path: Path):
    """Calling get_connection on an existing DB must not error."""
    db_path = tmp_path / "test.db"
    with closing(get_connection(db_path)):
        pass
    # Second call: schema's CREATE TABLE IF NOT EXISTS should silently no-op.
    with closing(get_connection(db_path)) as conn:
        # And the tables should still be queryable.
        rows = conn.execute("SELECT * FROM cells").fetchall()
        assert rows == []


def test_wal_mode_set(tmp_path: Path):
    with closing(get_connection(tmp_path / "test.db")) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_foreign_keys_pragma_on(tmp_path: Path):
    with closing(get_connection(tmp_path / "test.db")) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_dependencies_reverse_index_exists(tmp_path: Path):
    """The reverse-lookup index is the hot path on recalc — verify it's created."""
    with closing(get_connection(tmp_path / "test.db")) as conn:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert "dependencies_to_idx" in indexes
