"""Smoke tests for the HTMX grid endpoints.

Walks through the v1.0 acceptance criteria via real HTTP requests through
TestClient. Each test boots a fresh in-tmp-path Sheet so tests don't share
state and the user's real ~/.leonsheet DB is never touched.

TestClient must be used as a context manager for lifespan startup to fire
(that's how the Store loads and app.state.sheet gets populated). The
`client` fixture handles that.

The 500ms round-trip benchmark (acceptance #10) lives here.
"""

from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(db_path=tmp_path / "test.db")) as c:
        yield c


def _post_cell(client, row, col, raw_value):
    return client.post(f"/cell/{row}/{col}", data={"raw_value": raw_value})


# --- acceptance #1: grid renders ---


def test_grid_renders_26x100(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "<th>A</th>" in body
    assert "<th>Z</th>" in body
    assert ">1</td>" in body
    assert ">100</td>" in body
    assert 'id="cell-0-0"' in body
    assert 'id="cell-99-25"' in body
    assert "<th>AA</th>" not in body


def test_grid_includes_formula_bar(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="formula-bar"' in resp.text
    assert 'id="active-cell-label"' in resp.text


# --- acceptance #2: A1=5, A2=3, A3==A1+A2 -> A3 shows 8 ---


def test_simple_formula_round_trip(client):
    _post_cell(client, 0, 0, "5")
    _post_cell(client, 1, 0, "3")
    resp = _post_cell(client, 2, 0, "=A1+A2")
    assert resp.status_code == 200
    assert 'id="cell-2-0"' in resp.text
    assert 'value="8"' in resp.text


# --- acceptance #3: edit A1, A3 auto-updates ---


def test_edit_source_triggers_oob_for_dependent(client):
    _post_cell(client, 0, 0, "5")
    _post_cell(client, 1, 0, "3")
    _post_cell(client, 2, 0, "=A1+A2")
    resp = _post_cell(client, 0, 0, "10")
    assert resp.status_code == 200
    assert 'id="cell-0-0"' in resp.text  # edited cell, non-OOB
    assert 'id="cell-2-0"' in resp.text  # A3 as OOB
    assert 'hx-swap-oob="true"' in resp.text
    assert 'value="13"' in resp.text


# --- acceptance #4: SUM(A1:A5) with blank A4 = 28 ---


def test_sum_range_blank_middle(client):
    _post_cell(client, 0, 0, "10")
    _post_cell(client, 1, 0, "3")
    _post_cell(client, 2, 0, "13")
    _post_cell(client, 4, 0, "2")
    resp = _post_cell(client, 0, 1, "=SUM(A1:A5)")
    assert resp.status_code == 200
    assert 'id="cell-0-1"' in resp.text
    assert 'value="28"' in resp.text


# --- acceptance #5: IF flips on source edit ---


def test_if_formula_flips(client):
    _post_cell(client, 0, 0, "10")
    resp = _post_cell(client, 0, 2, '=IF(A1>5,"big","small")')
    assert 'value="big"' in resp.text
    resp = _post_cell(client, 0, 0, "2")
    assert 'id="cell-0-2"' in resp.text
    assert 'value="small"' in resp.text


# --- acceptance #6: circular reference -> #CIRCULAR!, no crash ---


def test_circular_reference_renders_error(client):
    _post_cell(client, 0, 0, "=B1")
    resp = _post_cell(client, 0, 1, "=A1")
    assert resp.status_code == 200
    assert "#CIRCULAR!" in resp.text
    assert "cell-error" in resp.text


# --- acceptance #7: refresh — cells survive ---


def test_grid_reflects_persisted_state(tmp_path):
    db_path = tmp_path / "test.db"
    with TestClient(create_app(db_path=db_path)) as c1:
        _post_cell(c1, 0, 0, "5")
        _post_cell(c1, 1, 0, "3")
        _post_cell(c1, 2, 0, "=A1+A2")

    with TestClient(create_app(db_path=db_path)) as c2:
        resp = c2.get("/")
        assert resp.status_code == 200
        body = resp.text
        assert 'id="cell-2-0"' in body
        # Spot-check that the formula is preserved in data-raw.
        assert "=A1+A2" in body


# --- acceptance #10: 50-deep recalc under 500ms ---


def test_50_deep_recalc_under_500ms(client):
    _post_cell(client, 0, 0, "0")
    for r in range(1, 50):
        _post_cell(client, r, 0, f"=A{r}+1")
    start = time.perf_counter()
    resp = _post_cell(client, 0, 0, "100")
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    body = resp.text
    for r in range(50):
        assert f'id="cell-{r}-0"' in body, f"missing cell-{r}-0 in response"
    assert 'value="149"' in body
    assert elapsed < 0.5, f"50-deep round-trip took {elapsed:.3f}s — over 500ms budget"


# --- input validation ---


def test_out_of_range_row_rejected(client):
    resp = client.post("/cell/100/0", data={"raw_value": "5"})
    assert resp.status_code == 422


def test_out_of_range_col_rejected(client):
    resp = client.post("/cell/0/26", data={"raw_value": "5"})
    assert resp.status_code == 422


def test_negative_row_rejected(client):
    resp = client.post("/cell/-1/0", data={"raw_value": "5"})
    assert resp.status_code == 422


# --- display formatting (predicted bug #7) ---


def test_float_precision_rounded_for_display(client):
    _post_cell(client, 0, 0, "0.1")
    _post_cell(client, 1, 0, "0.2")
    resp = _post_cell(client, 2, 0, "=A1+A2")
    # Python: 0.1 + 0.2 = 0.30000000000000004; display rounds to "0.3".
    assert 'value="0.3"' in resp.text


def test_integer_valued_floats_display_as_integers(client):
    _post_cell(client, 0, 0, "10")
    resp = _post_cell(client, 0, 1, "=A1/2")
    assert 'value="5"' in resp.text


# --- clearing a cell ---


def test_clearing_cell_via_empty_string_persists(tmp_path):
    db_path = tmp_path / "test.db"
    with TestClient(create_app(db_path=db_path)) as c:
        _post_cell(c, 0, 0, "5")
        _post_cell(c, 0, 0, "")
    with TestClient(create_app(db_path=db_path)) as c2:
        resp = c2.get("/")
        assert resp.status_code == 200
        # After clearing, cell-0-0 should not carry value="5".
        # Inspect just the cell-0-0 substring.
        body = resp.text
        idx = body.index('id="cell-0-0"')
        cell_html = body[idx:idx + 400]
        assert 'value=""' in cell_html
        assert 'data-raw=""' in cell_html


# --- edited cell is in response (non-OOB) ---


def test_edited_cell_is_not_oob(client):
    resp = _post_cell(client, 0, 0, "42")
    body = resp.text
    start = body.index('id="cell-0-0"')
    # Find the end of this opening tag.
    end = body.index('>', start)
    cell_tag = body[start:end]
    assert 'hx-swap-oob' not in cell_tag
