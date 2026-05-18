"""Smoke tests for cell-reference / column-letter conversions.

Predicted bug #15: write these once, test the corners, never touch again.
Corners that catch off-by-one bugs: A, Z, AA, AZ, BA, ZZ. Plus a round-trip
sweep so we catch the "looks right at corners but wrong in the middle" case.
"""

import pytest

from app.formula.refs import (
    col_index_to_letters,
    col_letters_to_index,
    format_cell_ref,
    parse_cell_ref,
)


# --- letters -> 0-indexed column ---


@pytest.mark.parametrize(
    "letters,expected",
    [
        ("A", 0),
        ("B", 1),
        ("Z", 25),
        ("AA", 26),
        ("AB", 27),
        ("AZ", 51),
        ("BA", 52),
        ("ZZ", 701),
        ("AAA", 702),
    ],
)
def test_letters_to_index(letters: str, expected: int):
    assert col_letters_to_index(letters) == expected


def test_letters_to_index_case_insensitive():
    assert col_letters_to_index("aa") == 26


def test_letters_to_index_rejects_empty():
    with pytest.raises(ValueError):
        col_letters_to_index("")


def test_letters_to_index_rejects_non_letter():
    with pytest.raises(ValueError):
        col_letters_to_index("A1")


# --- 0-indexed column -> letters ---


@pytest.mark.parametrize(
    "index,expected",
    [
        (0, "A"),
        (1, "B"),
        (25, "Z"),
        (26, "AA"),
        (27, "AB"),
        (51, "AZ"),
        (52, "BA"),
        (701, "ZZ"),
        (702, "AAA"),
    ],
)
def test_index_to_letters(index: int, expected: str):
    assert col_index_to_letters(index) == expected


def test_index_to_letters_rejects_negative():
    with pytest.raises(ValueError):
        col_index_to_letters(-1)


# --- round trip ---


def test_round_trip_first_1000_columns():
    for i in range(1000):
        assert col_letters_to_index(col_index_to_letters(i)) == i


# --- full cell-ref parsing ---


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("A1", (0, 0)),
        ("A100", (99, 0)),
        ("Z1", (0, 25)),
        ("AA1", (0, 26)),
        ("AA10", (9, 26)),
        ("ZZ999", (998, 701)),
    ],
)
def test_parse_cell_ref(ref: str, expected: tuple[int, int]):
    assert parse_cell_ref(ref) == expected


def test_parse_cell_ref_case_insensitive():
    assert parse_cell_ref("aa10") == (9, 26)


@pytest.mark.parametrize("bad", ["", "A", "1", "1A", "A0", "$A$1", "A1B"])
def test_parse_cell_ref_rejects_malformed(bad: str):
    with pytest.raises(ValueError):
        parse_cell_ref(bad)


def test_format_cell_ref_round_trip():
    for ref in ["A1", "Z1", "AA1", "AZ10", "BA52", "ZZ999"]:
        row, col = parse_cell_ref(ref)
        assert format_cell_ref(row, col) == ref
