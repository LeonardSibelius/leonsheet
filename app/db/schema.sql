-- leonsheet v1.0 schema
--
-- Three tables: meta, cells, dependencies. Composite (row, col) primary keys
-- (cell address IS the natural identity — no synthetic id to keep in sync).
--
-- Empty cells do NOT exist in the cells table; clearing a cell is a DELETE.
-- This keeps the table sparse: a fresh 26x100 grid has zero rows, not 2600.
--
-- Computed values are NOT persisted. On startup we read all cells, rebuild
-- the dependency graph from formulas, and recompute every formula cell once.
-- (Spec, v1.0 §5: "On startup: read all cells, rebuild the dependency graph,
-- recompute every formula cell once.")

PRAGMA journal_mode = WAL;        -- predicted bug #6: write contention
PRAGMA synchronous = NORMAL;      -- safe pairing with WAL on a single-host app
PRAGMA foreign_keys = ON;         -- future-proofs; v1.0 doesn't declare FKs

-- key/value store for sheet-level metadata: schema_version, last_modified, etc.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per non-empty cell. raw_value is exactly what the user typed:
-- formula cells start with '='; literal cells don't. No separate formula_text
-- column — that would be the same string twice and an invariant to maintain.
CREATE TABLE IF NOT EXISTS cells (
    row       INTEGER NOT NULL,
    col       INTEGER NOT NULL,
    raw_value TEXT    NOT NULL,
    PRIMARY KEY (row, col)
) WITHOUT ROWID;

-- Dependency edges: (from_row, from_col) depends on (to_row, to_col).
-- Read as: "the formula in (from) references the cell (to)."
-- Rebuilt from scratch for a cell whenever its formula changes
-- (DELETE WHERE from=…; INSERT new edges) inside the same transaction
-- that updates `cells` — predicted bug #12 (dependency staleness on formula
-- replacement) is structurally prevented.
CREATE TABLE IF NOT EXISTS dependencies (
    from_row INTEGER NOT NULL,
    from_col INTEGER NOT NULL,
    to_row   INTEGER NOT NULL,
    to_col   INTEGER NOT NULL,
    PRIMARY KEY (from_row, from_col, to_row, to_col)
) WITHOUT ROWID;

-- Reverse index: given a cell, find every formula that references it.
-- This is the hot path on every edit — "what needs recalc when X changes."
CREATE INDEX IF NOT EXISTS dependencies_to_idx
    ON dependencies (to_row, to_col);
