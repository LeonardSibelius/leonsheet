"""Smoke tests for CSV import / export.

Acceptance criteria #8 (import 10-row CSV) and #9 (export, opens in Excel)
are covered here.

Predicted-bug guard: the spec's "no formula interpretation on import" rule
is verified by importing a row with "=A1+1" and asserting it survives as
LITERAL TEXT, not as an evaluated formula.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(db_path=tmp_path / "test.db")) as c:
        yield c


def _post_cell(client, row, col, raw_value):
    return client.post(f"/cell/{row}/{col}", data={"raw_value": raw_value})


# --- acceptance #8: import a 10-row CSV ---


def test_import_10_row_csv(client):
    csv_body = "\n".join(str(i + 1) for i in range(10))  # 10 rows, single column
    resp = client.post(
        "/import-csv",
        files={"file": ("data.csv", csv_body, "text/csv")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # Confirm via the grid page that A1..A10 hold the imported numbers.
    grid = client.get("/").text
    for r in range(10):
        # Each cell-{r}-0 should carry value="{r+1}".
        marker = f'id="cell-{r}-0"'
        idx = grid.index(marker)
        snippet = grid[idx:idx + 400]
        assert f'value="{r + 1}"' in snippet, (
            f"row {r} missing expected value in {snippet[:200]}"
        )


def test_import_multi_column_csv(client):
    csv_body = "1,2,3\n4,5,6\n7,8,9"
    resp = client.post(
        "/import-csv",
        files={"file": ("data.csv", csv_body, "text/csv")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    grid = client.get("/").text
    # Check (1,1)=5 (middle cell)
    idx = grid.index('id="cell-1-1"')
    snippet = grid[idx:idx + 400]
    assert 'value="5"' in snippet


def test_import_preserves_equals_string_as_literal(client):
    """Spec: =-prefixed CSV cells import as LITERAL TEXT, not formulas."""
    csv_body = "=A1+1\n=NOT_A_FORMULA()"
    resp = client.post(
        "/import-csv",
        files={"file": ("data.csv", csv_body, "text/csv")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    grid = client.get("/").text
    # cell-0-0 should display "=A1+1" as text, not evaluated. The apostrophe
    # escape ('=A1+1) is stored internally; format_display strips it.
    idx = grid.index('id="cell-0-0"')
    snippet = grid[idx:idx + 500]
    assert 'value="=A1+1"' in snippet
    # data-raw carries the apostrophe-escaped form (round-trip safe through reload).
    # Jinja HTML-escapes the apostrophe to &#39;.
    assert 'data-raw="&#39;=A1+1"' in snippet


def test_import_replaces_existing_cells(client):
    """Import wipes existing grid contents (matches Excel "Open CSV" behavior)."""
    _post_cell(client, 50, 25, "stale data far from origin")
    client.post(
        "/import-csv",
        files={"file": ("data.csv", "1,2,3", "text/csv")},
        follow_redirects=False,
    )
    grid = client.get("/").text
    # The far-away cell should now be empty.
    idx = grid.index('id="cell-50-25"')
    snippet = grid[idx:idx + 400]
    assert 'value=""' in snippet


def test_import_survives_reload(tmp_path):
    """Imported data must survive a fresh app instance."""
    db_path = tmp_path / "test.db"
    with TestClient(create_app(db_path=db_path)) as c:
        c.post(
            "/import-csv",
            files={"file": ("data.csv", "10,20,30\n40,50,60", "text/csv")},
            follow_redirects=False,
        )
    with TestClient(create_app(db_path=db_path)) as c2:
        grid = c2.get("/").text
        for (r, c, expected) in [(0, 0, "10"), (0, 2, "30"), (1, 1, "50")]:
            idx = grid.index(f'id="cell-{r}-{c}"')
            snippet = grid[idx:idx + 400]
            assert f'value="{expected}"' in snippet


def test_import_truncates_oversized_grid(client):
    """Rows or cols beyond the 26x100 grid are silently truncated."""
    # 105 rows; only first 100 should land.
    csv_body = "\n".join(str(i) for i in range(105))
    client.post(
        "/import-csv",
        files={"file": ("data.csv", csv_body, "text/csv")},
        follow_redirects=False,
    )
    grid = client.get("/").text
    # Last row in-grid: cell-99-0 should have value "99".
    idx = grid.index('id="cell-99-0"')
    snippet = grid[idx:idx + 400]
    assert 'value="99"' in snippet


# --- acceptance #9: export — formula cells export COMPUTED values ---


def test_export_empty_grid(client):
    resp = client.get("/export-csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "leonsheet.csv" in resp.headers["content-disposition"]
    assert resp.text == ""


def test_export_literal_values(client):
    _post_cell(client, 0, 0, "10")
    _post_cell(client, 0, 1, "20")
    _post_cell(client, 1, 0, "30")
    resp = client.get("/export-csv")
    # 2 rows x 2 cols
    assert resp.text.strip().splitlines() == ["10,20", "30,"]


def test_export_formula_cells_export_computed_value(client):
    _post_cell(client, 0, 0, "5")
    _post_cell(client, 0, 1, "=A1*2")  # B1 = 10
    resp = client.get("/export-csv")
    # B1 must export "10" (computed), NOT "=A1*2".
    assert resp.text.strip() == "5,10"


def test_export_errors_render_as_codes(client):
    _post_cell(client, 0, 0, "=B1")
    _post_cell(client, 0, 1, "=A1")  # cycle
    resp = client.get("/export-csv")
    # Both cells should appear as #CIRCULAR! in the CSV.
    assert "#CIRCULAR!" in resp.text


def test_export_strings_with_commas_get_quoted(client):
    _post_cell(client, 0, 0, "hello, world")
    resp = client.get("/export-csv")
    # csv.writer auto-quotes strings containing commas.
    assert '"hello, world"' in resp.text


# --- round trip: export then re-import ---


def test_round_trip_export_import_preserves_values(client):
    _post_cell(client, 0, 0, "10")
    _post_cell(client, 0, 1, "20")
    _post_cell(client, 0, 2, "=A1+B1")  # 30
    csv_text = client.get("/export-csv").text
    # Now re-import: the exported file should round-trip cleanly.
    client.post(
        "/import-csv",
        files={"file": ("rt.csv", csv_text, "text/csv")},
        follow_redirects=False,
    )
    grid = client.get("/").text
    # After import, the formula was exported as computed (30), then reimported
    # as a literal 30 (not as the formula). All three cells should show their
    # computed-then-stored numeric values.
    for (r, c, expected) in [(0, 0, "10"), (0, 1, "20"), (0, 2, "30")]:
        idx = grid.index(f'id="cell-{r}-{c}"')
        snippet = grid[idx:idx + 400]
        assert f'value="{expected}"' in snippet
