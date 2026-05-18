"""SQLite connection helper.

Opens a connection at a given path, creating the parent directory if
missing and applying the schema (idempotently). Sets per-connection
pragmas (foreign_keys, synchronous) that don't persist in the DB file.

`journal_mode = WAL` is set in schema.sql and persists at the DB file
level — running it once flips the file into WAL mode permanently.
Predicted bug #6 (write contention on rapid edits): WAL lets readers
proceed without blocking writers and serializes writes at the SQLite
level, so concurrent FastAPI handlers don't trample each other.

Connections are NOT thread-safe in Python sqlite3 by default. Open a
new connection per operation (cheap) rather than sharing one across
the FastAPI handler thread pool.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".leonsheet" / "sheet.db"

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection at db_path. Caller is responsible for closing it.

    The schema is applied via `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX
    IF NOT EXISTS` so this is safe to call on an existing DB.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    _apply_schema(conn)
    # Per-connection pragmas — don't persist in the DB file.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_PATH.read_text())
